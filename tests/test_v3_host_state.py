"""Execute production host state helpers for cancellation and stale-result races."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWIFTC = shutil.which("swiftc")


class V3HostStateTests(unittest.TestCase):
    def test_waiter_cancellation_and_health_reload_races_execute(self):
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; host state harness runs in macOS CI")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        helpers = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_host_state_harness.swift").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "main.swift"
            executable = Path(temporary) / "host-state"
            source.write_text(failure + "\n" + helpers + "\n" + harness, encoding="utf-8")
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_HOST_STATE_PASS", result.stdout)

    def test_shell_uses_cancellation_safe_waiters_and_health_rerun_queue(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertIn("withTaskCancellationHandler", shell)
        self.assertIn("cancelSnapshotWaiter(waiterID)", shell)
        self.assertIn("snapshotWaiterRegistry.takeAll()", shell)
        self.assertIn("V3SnapshotErrorPolicy.shouldMarkDisconnected(error)", shell)
        health = shell[shell.index("struct V3HealthView"):shell.index("private func openJITLessSetup", shell.index("struct V3HealthView"))]
        self.assertIn("status.invalidateSetupFacts()", health)
        self.assertIn("status.beginSetupFactObservation()", health)
        self.assertIn("while reloadQueue.finishIteration()", health)
        self.assertIn("healthRevisionIsCurrent(factRevision)", health)


if __name__ == "__main__":
    unittest.main()
