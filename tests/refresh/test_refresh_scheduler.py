"""Scheduler and VPN ownership regressions for the maintained refresh owners.

Compiled helpers use OS doubles; passing these tests is not device proof.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


from tests.support.product_sources import read_product_source
from tests.owner_source import owner_source

ROOT = Path(__file__).resolve().parents[2]


class LiveContainerRuntimeTests(unittest.TestCase):
    def test_scheduler_typechecks_and_executes_failure_paths(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")
        roles = ["combined_failure.swift", "v3_wire_contract.swift", "v3_behavioral_primitives.swift"]
        source = "\n".join(read_product_source(role) for role in roles)
        source += "\n" + (ROOT / "tests/fixtures/livecontainer_scheduler_stubs.swift").read_text()
        source += "\n" + "\n".join(read_product_source(role) for role in (
            "v3_shared_app_group.swift", "livecontainer_refresh_policy.swift",
            "livecontainer_vpn_recovery.swift", "livecontainer_refresh_scheduler.swift"))
        source += "\n" + (ROOT / "tests/fixtures/livecontainer_scheduler_harness.swift").read_text()
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "Test.swift"
            exe = Path(directory) / "scheduler-test"
            file.write_text(source)
            build = subprocess.run([compiler, "-swift-version", "5", "-parse-as-library", str(file), "-o", str(exe)], text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(exe)], text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("SCHEDULER_BEHAVIOR_TESTS_PASSED", result.stdout)
            self.assertIn("underlying_domain=redacted underlying_code=unknown", result.stdout)
            self.assertNotIn(r"\(runID", result.stdout)
            self.assertNotIn("SECRET_TOKEN", result.stdout)

    def test_installed_profile_identity_and_expiration(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")
        source = read_product_source('livecontainer_refresh_policy.swift') + r'''
let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: directory) }
let url = directory.appendingPathComponent("profile.fixture")
let expiry = Date(timeIntervalSince1970: 1900000000)
let plist: [String: Any] = ["UUID": "TEST-PROFILE-UUID", "ApplicationIdentifierPrefix": ["TESTTEAM"],
    "Entitlements": ["application-identifier": "TESTTEAM.com.kdt.livecontainer.TESTTEAM"], "ExpirationDate": expiry]
let xml = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
try (Data([0x30, 0x82, 0x01, 0x02]) + xml + Data([0x01, 0x02])).write(to: url)
let profile = try LiveContainerHostProfile.read(at: url, expectedBundleID: "com.kdt.livecontainer.TESTTEAM")
precondition(profile.expiration == expiry && profile.uuid == "TEST-PROFILE-UUID")
do { _ = try LiveContainerHostProfile.read(at: url, expectedBundleID: "another.app"); fatalError("wrong identity accepted") } catch {}
try Data("not a provisioning profile".utf8).write(to: url)
do { _ = try LiveContainerHostProfile.read(at: url, expectedBundleID: "com.kdt.livecontainer.TESTTEAM"); fatalError("invalid profile accepted") } catch {}
print("INSTALLED_PROFILE_TESTS_PASSED")
'''
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "main.swift"
            exe = Path(directory) / "profile-test"
            file.write_text(source)
            build = subprocess.run([compiler, "-swift-version", "5", str(file), "-o", str(exe)], text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(exe)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("INSTALLED_PROFILE_TESTS_PASSED", result.stdout)


NETWORK_PATH = 'livecontainer_network_preflight.swift'


SCHEDULER_PATH = 'livecontainer_refresh_scheduler.swift'


HARNESS_PATH = ROOT / "tests/fixtures/livecontainer_vpn_handoff_harness.swift"


BEGIN = "// V3_VPN_HANDOFF_OWNER_BEGIN"


END = "// V3_VPN_HANDOFF_OWNER_END"


class LiveContainerVPNHandoffTests(unittest.TestCase):
    def production_helper(self):
        source = owner_source("LiveContainerSwiftUI/App/AppDelegate.swift")
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
        scheduler = read_product_source(SCHEDULER_PATH)
        call = scheduler[scheduler.index("try await LiveContainerNetworkPreflight.check"): ]
        check_call = call.split("try await performRefreshWithVPNRecovery(runID: runID", 1)[0]
        self.assertIn("runID: runID.uuidString", check_call)
        self.assertLess(call.index("LiveContainerNetworkPreflight.check"),
                        call.index("try await performRefreshWithVPNRecovery(runID: runID"))
        self.assertIn("guard activeRun == nil else { return }", scheduler[
            scheduler.index("static func recoverAfterLaunchOrResume"):])

    def test_marker_format_is_run_scoped_with_legacy_date_migration(self):
        network = read_product_source(NETWORK_PATH)
        self.assertIn('["run_id": runID, "requested_at": requestedAt]', network)
        self.assertIn("LiveContainerVPNReturnMarkerPolicy.isOwned", network)
        self.assertIn("value as? Date", network)
        self.assertIn("withTaskCancellationHandler", network)
        self.assertIn("waiter.settle(.cancelled)", network)


def setUpModule():
    global NETWORK, SCHEDULER
    NETWORK = read_product_source('livecontainer_network_preflight.swift')
    SCHEDULER = read_product_source('livecontainer_refresh_scheduler.swift')



class NetworkPreflightTests(unittest.TestCase):
    def test_wifi_is_one_shot_without_ssid_or_location(self):
        self.assertIn("NWPathMonitor(requiredInterfaceType: .wifi)", NETWORK)
        self.assertIn("path.status == .satisfied", NETWORK)
        self.assertIn("monitor.cancel()", NETWORK)
        wifi = NETWORK[NETWORK.index("static func wifiAvailable()"):NETWORK.index("static func hasTunnelInterface()") ]
        self.assertNotIn("Task.sleep", wifi)
        for forbidden in ("CNCopyCurrentNetworkInfo", "CLLocationManager", "Timer.scheduledTimer"):
            self.assertNotIn(forbidden, NETWORK)

    def test_no_transport_until_preflight_passes(self):
        self.assertLess(SCHEDULER.index("try await LiveContainerNetworkPreflight.check"),
                        SCHEDULER.index("try await performRefreshWithVPNRecovery(runID: runID"))
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
        self.assertIn("try await settleAfterActivation(ownsRun: ownsRun)", NETWORK)
        self.assertIn("guard ownsRun() else { throw CancellationError() }", NETWORK)
        self.assertIn("case .wifiUnavailable:", NETWORK)
        self.assertIn("no_utun_interface", NETWORK)
        self.assertIn("readiness=requires_coredevice_verification", NETWORK)
        self.assertIn("guard !completed else", NETWORK)
        self.assertIn("removeObserver", NETWORK)

    def test_durable_handoff_is_bounded_and_consumed_once(self):
        self.assertIn("age >= 0 && age < 120", NETWORK)
        self.assertIn("defaults.removeObject(forKey: pendingKey)", NETWORK)
        self.assertLess(SCHEDULER.index("guard activeRun == nil else { return }", SCHEDULER.index("static func recoverAfterLaunchOrResume")),
                        SCHEDULER.index("LiveContainerNetworkPreflight.consumePendingReturn()"))


class VerificationMessagesTests(unittest.TestCase):
    def test_missing_mismatched_and_empty_results_are_distinct(self):
        source = read_product_source('livecontainer_refresh_scheduler.swift')
        self.assertNotIn("verification_manifest_missing_or_wrong_run", source)
        for code in ("manifest_missing", "run_mismatch", "results_empty"):
            self.assertIn("VERIFICATION_FAILED reason=" + code, source)
        self.assertIn("Refresh is unconfirmed", source)
        self.assertIn("different refresh attempt", source)
        self.assertIn("No successful refresh was confirmed", source)


if __name__ == "__main__":
    unittest.main()
