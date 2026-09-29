"""Exercise the production VPN handoff owner without importing iOS frameworks."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NETWORK_PATH = ROOT / "scripts/templates/livecontainer_network_preflight.swift"
SCHEDULER_PATH = ROOT / "scripts/templates/livecontainer_refresh_scheduler.swift"
HARNESS_PATH = ROOT / "tests/fixtures/livecontainer_vpn_handoff_harness.swift"
BEGIN = "// V3_VPN_HANDOFF_OWNER_BEGIN"
END = "// V3_VPN_HANDOFF_OWNER_END"


class LiveContainerVPNHandoffTests(unittest.TestCase):
    def production_helper(self):
        source = NETWORK_PATH.read_text(encoding="utf-8")
        self.assertEqual(source.count(BEGIN), 1)
        self.assertEqual(source.count(END), 1)
        return source.split(BEGIN, 1)[1].split(END, 1)[0]

    def test_production_waiter_and_marker_interleavings(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift execution runs in macOS CI")
        body = self.production_helper() + "\n" + HARNESS_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "vpn-handoff.swift"
            binary = Path(directory) / "vpn-handoff-test"
            source.write_text(body, encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(binary)],
                                      capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("LiveContainer VPN handoff ownership PASS", result.stdout)

    def test_scheduler_passes_the_active_run_as_preflight_owner(self):
        scheduler = SCHEDULER_PATH.read_text(encoding="utf-8")
        call = scheduler[scheduler.index("try await LiveContainerNetworkPreflight.check"):]
        check_call = call.split("try await performRefresh(runID: runID)", 1)[0]
        self.assertIn("runID: runID.uuidString", check_call)
        self.assertLess(call.index("LiveContainerNetworkPreflight.check"),
                        call.index("try await performRefresh(runID: runID)"))
        self.assertIn("guard activeRun == nil else { return }", scheduler[
            scheduler.index("static func recoverAfterLaunchOrResume"):])

    def test_marker_format_is_run_scoped_with_legacy_date_migration(self):
        network = NETWORK_PATH.read_text(encoding="utf-8")
        self.assertIn('["run_id": runID, "requested_at": requestedAt]', network)
        self.assertIn("LiveContainerVPNReturnMarkerPolicy.isOwned", network)
        self.assertIn("value as? Date", network)
        self.assertIn("withTaskCancellationHandler", network)
        self.assertIn("waiter.settle(.cancelled)", network)


if __name__ == "__main__":
    unittest.main()
