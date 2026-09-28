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
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_operation_recovery_harness.swift").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            main = Path(temporary) / "main.swift"
            executable = Path(temporary) / "operation-recovery"
            main.write_text(failure + "\n" + primitives + "\n" + harness, encoding="utf-8")
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(main), "-o", str(executable)],
                capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_OPERATION_RECOVERY_PASS", result.stdout)

    def test_host_and_service_use_journal_before_dispatch_and_preserve_ipa(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertIn("operation: \"opRecoveryPrepare\"", shell)
        self.assertIn("V3AppGroupProcessLock.withLock", service)
        self.assertIn("V3OperationRecoveryJournal.reserve(sessionID: session", service)
        self.assertIn("V3OperationRecoveryJournal.beginDispatch(sessionID: session", service)
        self.assertIn("settleOperationRecoveryIfTerminal", service)
        self.assertIn("lease?.stagedIPAToken", service)
        self.assertIn("response[\"operationRecovery\"] = safeRecovery", service)
        self.assertIn("reconcileDurableOperationAfterDeviceCheck", shell)
        self.assertIn("unresolvedOperationRecovery?.stagedIPAToken", shell)
        self.assertIn("operation: \"opPoll\"", shell)


if __name__ == "__main__":
    unittest.main()
