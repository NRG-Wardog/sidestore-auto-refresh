"""Execute the production direct-mutation journal across service recreation."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWIFTC = shutil.which("swiftc")


class V3DirectMutationRecoveryTests(unittest.TestCase):
    def test_production_journal_write_ahead_relaunch_v1_and_wire_contract(self):
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; executable recovery harness runs in macOS CI")

        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        handoff = (ROOT / "scripts/templates/v3_secret_handoff.swift").read_text(encoding="utf-8")
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
            main.write_text(
                injected_imports + wire + "\n" + failure + "\n" + primitives + "\n" +
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
