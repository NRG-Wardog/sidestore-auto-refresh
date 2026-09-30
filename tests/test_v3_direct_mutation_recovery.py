"""Execute the production direct-mutation journal across service recreation."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWIFTC = shutil.which("swiftc")


class V3DirectMutationRecoveryTests(unittest.TestCase):
    def test_executable_slice_contains_direct_recovery_declarations(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        journal_start = service.index("private enum V3DirectMutationRecoveryPhase:")
        journal_end = service.index("V3_NATIVE_CALLBACK_GATE_V1", journal_start)
        journal = service[journal_start:journal_end]

        for declaration in (
            "private enum V3DirectMutationRecoveryHash",
            "private struct V3DirectMutationRecoveryRecord",
            "private enum V3ServiceRecoveryFileRecord",
            "private enum V3OperationRecoveryJournal",
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, journal)

        fixture = (ROOT / "tests/fixtures/v3_direct_mutation_recovery_harness.swift").read_text(
            encoding="utf-8")
        self.assertIn("V3DirectMutationRecoveryRecord.isEligible(request: request)", fixture)

    def test_corrupt_journal_fixture_creates_parent_before_atomic_write(self):
        fixture = (ROOT / "tests/fixtures/v3_direct_mutation_recovery_harness.swift").read_text(
            encoding="utf-8")
        start = fixture.index("private static func testUnknownV2FailsClosed")
        end = fixture.index("\n    private static func testWireContract", start)
        corrupt_fixture = fixture[start:end]
        create_parent = corrupt_fixture.index("journalURL.deletingLastPathComponent()")
        write_record = corrupt_fixture.index("try data.write(to: journalURL, options: .atomic)")
        self.assertLess(create_parent, write_record)
        self.assertIn(".posixPermissions: 0o700", corrupt_fixture)

    def test_direct_not_dispatched_proof_uses_journal_aware_policy(self):
        fixture = (ROOT / "tests/fixtures/v3_direct_mutation_recovery_harness.swift").read_text(
            encoding="utf-8")
        start = fixture.index("private static func testWireContract")
        end = fixture.index("\n    private static func recordURL", start)
        wire_fixture = fixture[start:end]
        self.assertIn("!V3RequestReplayPolicy.mayClaimNotDispatched(", wire_fixture)
        self.assertIn("V3DirectMutationPreDispatchReplyPolicy.mayClaimInvalidRequestNotDispatched(",
                      wire_fixture)

    def test_receive_routes_direct_mutations_through_ordered_lifecycle(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        start = service.index("private func receive(_ data: Data")
        end = service.index("\n    private func invalidRequestReply", start)
        receive = service[start:end]

        reservation = receive.index("V3DirectMutationRecoveryLifecycle.reserve(")
        task_start = receive.index("tasks[id] = Task")
        lifecycle = receive.index("V3DirectMutationRecoveryLifecycle.dispatchAndSettle(", task_start)
        post_run_cancel_check = receive.index("try Task.checkCancellation()", lifecycle)
        catch_start = receive.index("} catch {", lifecycle)
        catch_end = receive.index("if operation == \"refreshAdmissionBegin\"", catch_start)
        failure_cleanup = receive[catch_start:catch_end]

        self.assertLess(reservation, task_start,
                        "the durable direct record is reserved before the async dispatch task")
        self.assertLess(task_start, lifecycle)
        self.assertLess(lifecycle, post_run_cancel_check,
                        "the lifecycle helper persists terminal state before cancellation is observed")
        self.assertIn("V3DirectMutationRecoveryLifecycle.clearPreparedAfterFailure", failure_cleanup)
        self.assertNotIn("V3OperationRecoveryJournal.beginDirectDispatch", receive)
        self.assertNotIn("V3OperationRecoveryJournal.settleDirect", receive)

    def test_production_journal_write_ahead_relaunch_v1_and_wire_contract(self):
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; executable recovery harness runs in macOS CI")

        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        handoff = (ROOT / "scripts/templates/v3_secret_handoff.swift").read_text(encoding="utf-8")
        shared = (ROOT / "scripts/templates/v3_shared_app_group.swift").read_text(encoding="utf-8")
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        fixture = (ROOT / "tests/fixtures/v3_direct_mutation_recovery_harness.swift").read_text(encoding="utf-8")

        lock_start = handoff.index("enum V3AppGroupProcessLock {")
        lock_end = handoff.index("\nenum V3SecretHandoffError", lock_start)
        error_start = lock_end + 1
        error_end = handoff.index("\n/// Serializes the full shared-Keychain", error_start)
        journal_start = service.index("private enum V3DirectMutationRecoveryPhase:")
        journal_end = service.index("\n// V3_NATIVE_CALLBACK_GATE_V1", journal_start)
        injected_imports = (
            "import Foundation\n"
            "#if canImport(Darwin)\nimport Darwin\n"
            "#elseif canImport(Glibc)\nimport Glibc\n#endif\n"
        )

        with tempfile.TemporaryDirectory() as temporary:
            main = Path(temporary) / "direct-recovery-main.swift"
            executable = Path(temporary) / "direct-recovery-harness"
            # The journal and the lock resolve the real shared App Group identity,
            # so the write-ahead record lands where the host looks for it.
            main.write_text(
                injected_imports + wire + "\n" + failure + "\n" + primitives + "\n" + shared + "\n" +
                "enum V3IPAStaging { static let sideStoreAppGroupIdentifier = \"group.com.SideStore.SideStore\" }\n" +
                handoff[lock_start:lock_end] + "\n" + handoff[error_start:error_end] + "\n" +
                service[journal_start:journal_end] + "\n" + fixture,
                encoding="utf-8",
            )
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(main), "-o", str(executable)],
                capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)

            root = Path(temporary) / "shared-app-group"
            seeded = subprocess.run([str(executable), "seed", str(root)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            self.assertIn("V3_DIRECT_MUTATION_SEEDED", seeded.stdout)

            relaunched = subprocess.run([str(executable), "verify", str(root)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(relaunched.returncode, 0, relaunched.stderr)
            self.assertIn("V3_DIRECT_MUTATION_RELAUNCH_PASS", relaunched.stdout)


if __name__ == "__main__":
    unittest.main()
