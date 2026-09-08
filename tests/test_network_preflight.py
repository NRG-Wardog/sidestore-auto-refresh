"""Source contracts; OS path delivery and URL callbacks still need device tests."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
NETWORK = (ROOT / "scripts/templates/livecontainer_network_preflight.swift").read_text()
SCHEDULER = (ROOT / "scripts/templates/livecontainer_refresh_scheduler.swift").read_text()


class NetworkPreflightTests(unittest.TestCase):
    def test_wifi_is_one_shot_without_ssid_or_location(self):
        self.assertIn("NWPathMonitor(requiredInterfaceType: .wifi)", NETWORK)
        self.assertIn("path.status == .satisfied", NETWORK)
        self.assertIn("monitor.cancel()", NETWORK)
        for forbidden in ("CNCopyCurrentNetworkInfo", "CLLocationManager", "Timer.scheduledTimer", "Task.sleep"):
            self.assertNotIn(forbidden, NETWORK)

    def test_no_transport_until_preflight_passes(self):
        self.assertLess(SCHEDULER.index("try await LiveContainerNetworkPreflight.check"),
                        SCHEDULER.index("try await performRefresh(runID: runID)"))
        check = NETWORK[NETWORK.index("static func check("):]
        self.assertLess(check.index("guard await wifiAvailable()"), check.index("hasTunnelInterface()"))
        self.assertIn("WIFI_UNAVAILABLE", SCHEDULER)
        self.assertIn("VPN_UNAVAILABLE", SCHEDULER)

    def test_background_never_launches_vpn(self):
        self.assertIn('manual && source != "vpn_return" && task == nil', SCHEDULER)
        self.assertIn("UIApplication.shared.applicationState == .active", NETWORK)
        self.assertIn("VPN_UNAVAILABLE_BACKGROUND", NETWORK)

    def test_return_is_rechecked_not_assumed_ready(self):
        self.assertIn('URLQueryItem(name: "scheme", value: scheme)', NETWORK)
        self.assertIn("wifi_lost_during_activation", NETWORK)
        self.assertIn("no_utun_interface", NETWORK)
        self.assertIn("readiness=requires_coredevice_verification", NETWORK)
        self.assertIn("guard !completed else", NETWORK)
        self.assertIn("removeObserver", NETWORK)

    def test_durable_handoff_is_bounded_and_consumed_once(self):
        self.assertIn("age >= 0 && age < 120", NETWORK)
        self.assertIn("defaults.removeObject(forKey: pendingKey)", NETWORK)
        self.assertLess(SCHEDULER.index("guard activeRun == nil else { return }", SCHEDULER.index("static func recoverAfterLaunchOrResume")),
                        SCHEDULER.index("LiveContainerNetworkPreflight.consumePendingReturn()"))


if __name__ == "__main__":
    unittest.main()
