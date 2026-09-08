"""Keep the opt-in probe isolated from production refresh and account writes."""
import importlib.util
from pathlib import Path
import unittest
from test_combined_transport import SourceFixture, patch as patch_transport, snapshot

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cellular_probe", ROOT / "experiments/cellular/patch.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class CellularSafetyTests(unittest.TestCase):
    def test_never_calls_write_or_refresh_pipeline(self):
        source = probe.GATEWAY + probe.MUX + probe.UI
        for forbidden in ("installation_proxy_install(", "installation_proxy_uninstall(",
                          "afc_file_write(", "AppManager", "RefreshAllAppsIntent", "setBool(",
                          "turnOffData", "UIApplication.shared.open", "isReady(withNetworkCheck: false"):
            self.assertNotIn(forbidden, source)
        self.assertIn("installation_proxy_get_apps(", source)

    def test_fresh_connection_cleanup_and_busy_rejection(self):
        self.assertLess(probe.GATEWAY.index("self.batchCount == 0"), probe.GATEWAY.index("try self.ensureCoreDeviceConnection()"))
        self.assertIn("self.adapter == nil, self.handshake == nil, self.coreDeviceProvider == nil", probe.GATEWAY)
        self.assertIn("installation_proxy_client_free(client)", probe.GATEWAY)
        self.assertIn("idevice_plist_array_free", probe.GATEWAY)
        self.assertIn("self.releaseTransport()", probe.GATEWAY)
        self.assertIn("self.setDeviceEndpointIp(previousPeer)", probe.GATEWAY)
        self.assertNotIn("configureCoreDeviceTransport(", probe.MUX)

    def test_unknown_network_fails_closed_and_foreground_only(self):
        self.assertIn("finish(nil)", probe.UI)
        self.assertIn("wifi == false, cellular == true", probe.UI)
        self.assertIn("applicationState == .active", probe.UI)
        self.assertIn("guard !cellularProbeRunning", probe.UI)
        self.assertIn("monitor.cancel()", probe.UI)
        self.assertNotIn("NSTimer", probe.UI)
        self.assertNotIn("while ", probe.UI)

    def test_opt_in_is_branch_scoped(self):
        workflow = (ROOT / ".github/workflows/livecontainer-build.yml").read_text()
        self.assertIn("default: false", workflow)
        self.assertIn("inputs.cellular_diagnostic && github.ref == 'refs/heads/investigate/cellular-coredevice'", workflow)


class CellularPinnedTests(SourceFixture):
    def test_patch_idempotence_and_normal_wifi_guard_preserved(self):
        patch_transport(self.side / "Dependencies/minimuxer")
        impl = self.side / "Dependencies/minimuxer/Sources/MinimuxerImpl.swift"
        original = impl.read_text()
        probe.patch(self.side)
        first = snapshot(self.side)
        probe.patch(self.side)
        self.assertEqual(first, snapshot(self.side))
        after = impl.read_text()
        start = "    func isReady(withNetworkCheck:"
        end = "    private func runIdeviceCheckingVPN"
        self.assertEqual(original[original.index(start):original.index(end)],
                         after[after.index(start):after.index(end)])


if __name__ == "__main__":
    unittest.main()
