"""Execute crash/recreate and dispatch/terminal operation recovery interleavings."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWIFTC = shutil.which("swiftc")


class V3OperationRecoveryTests(unittest.TestCase):
    def test_durable_operation_lease_survives_process_recreation(self):
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; behavioral harness runs in macOS CI")
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_operation_recovery_harness.swift").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            main = Path(temporary) / "main.swift"
            executable = Path(temporary) / "operation-recovery"
            main.write_text(wire + "\n" + failure + "\n" + primitives + "\n" + harness, encoding="utf-8")
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(main), "-o", str(executable)],
                capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_OPERATION_RECOVERY_PASS", result.stdout)

    def test_production_journal_persistence_and_process_lock_with_temporary_root(self):
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; executable journal harness runs in macOS CI")
        handoff = (ROOT / "scripts/templates/v3_secret_handoff.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        lock_start = handoff.index("enum V3AppGroupProcessLock {")
        lock_end = handoff.index("\nenum V3SecretHandoffError", lock_start)
        error_start = lock_end + 1
        error_end = handoff.index("\nenum V3SecretHandoffRecord", error_start)
        journal_start = service.index("private enum V3OperationRecoveryJournal {")
        journal_end = service.index("\n// V3_NATIVE_CALLBACK_GATE_V1", journal_start)
        lock = handoff[lock_start:lock_end]
        error = handoff[error_start:error_end]
        journal = service[journal_start:journal_end]
        fixture = (ROOT / "tests/fixtures/v3_operation_recovery_journal_harness.swift").read_text(encoding="utf-8")
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        injected_imports = "import Foundation\n#if canImport(Darwin)\nimport Darwin\n#elseif canImport(Glibc)\nimport Glibc\n#endif\n"
        with tempfile.TemporaryDirectory() as temporary:
            main = Path(temporary) / "journal-main.swift"
            executable = Path(temporary) / "journal-harness"
            main.write_text(injected_imports + wire + "\n" + failure + "\n" + primitives +
                "\nenum V3IPAStaging { static let sideStoreAppGroupIdentifier = \"group.com.SideStore.SideStore\" }\n" +
                lock + "\n" + error + "\n" + journal + "\n" + fixture, encoding="utf-8")
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(main), "-o", str(executable)],
                capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_OPERATION_RECOVERY_JOURNAL_PASS", result.stdout)

    def test_host_and_service_use_journal_before_dispatch_and_preserve_ipa(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        self.assertIn("operation: \"opRecoveryPrepare\"", shell)
        self.assertIn('"opRecoveryPrepare", "opRecoveryReconcile"', wire)
        self.assertIn('case "opRecoveryPrepare":', wire)
        self.assertIn('case "opRecoveryReconcile", "refreshAdmissionReconcile":', wire)
        self.assertIn("V3AppGroupProcessLock.withLock", service)
        self.assertIn("V3OperationRecoveryJournal.reserve(sessionID: session", service)
        self.assertIn("V3OperationRecoveryJournal.beginDispatch(sessionID: session", service)
        self.assertIn("settleOperationRecoveryIfTerminal", service)
        self.assertIn("clearPreparedOperationRecoveryIfProven", service)
        self.assertIn("clearPreparedAfterConfirmedCancellation", service)
        self.assertIn("lease?.stagedIPAToken", service)
        self.assertIn("response[\"operationRecovery\"] = safeRecovery", service)
        self.assertIn("refreshAdmission.ownerLost", service)
        self.assertIn("response[\"refreshRecovery\"]", service)
        self.assertIn("reconcileDurableOperationAfterDeviceCheck", shell)
        self.assertIn("unresolvedOperationRecovery?.stagedIPAToken", shell)
        self.assertIn("reconcileLostRefreshAfterDeviceCheck", shell)
        self.assertIn("operation: \"opPoll\"", shell)
        self.assertIn("propertyListRepresentation", service)
        self.assertIn("decodePropertyList", service)
        for anchor in ("func perform(_ operation:", "private func runMutation(",
                       "func beginInstallPicker(", "func stageSharedIPA("):
            start = shell.index(anchor)
            self.assertIn("rejectForUnresolvedRecovery()", shell[start:start + 1800])
        self.assertIn("ownerLost", (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
