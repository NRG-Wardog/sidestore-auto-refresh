"""Maintained host refresh routing and executable scheduling policy."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from tests.owner_source import owner_source
from tests.support.product_sources import read_product_source

class LiveContainerRefreshTests(unittest.TestCase):
    def test_host_intent_routes_through_scheduler(self):
        support = owner_source("SideStoreSupport/SideStore.swift")
        delegate = owner_source("LiveContainerSwiftUI/App/AppDelegate.swift")
        helper = support[support.index("func performIntentRefresh("):support.index("// LC_REFRESH_BRIDGE_V3_BEGIN")]
        self.assertIn("V3ShortcutRefreshRequest.make()", helper)
        self.assertIn('Notification.Name("LiveContainerAutoRefreshRunNow")', helper)
        self.assertNotIn("RefreshHandler.shared.startRefresh(identifier: identifier", helper)
        self.assertNotIn("All apps have been refreshed.", helper)
        self.assertIn("guard let request = V3ShortcutRefreshRequest(userInfo: notification.userInfo) else", delegate)
        self.assertIn("RUN_NOW_REJECTED reason=invalid_request_identity", delegate)

    def test_policy_is_not_rewritten_to_assume_signer_allowlist(self):
        scheduler = read_product_source("livecontainer_refresh_scheduler.swift")
        support = owner_source("SideStoreSupport/SideStore.swift")
        self.assertIn('9SideStore20RefreshAllAppsIntentV', support)
        self.assertIn('identifier: "RefreshAllIntent"', support)
        self.assertIn('LiveContainerRefreshTaskIdentifiers.resolve', scheduler)
        self.assertNotIn('Bundle.main.bundleIdentifier ??', scheduler)

    def test_current_refresh_sources_parse(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")
        for name in ("livecontainer_refresh_policy.swift", "livecontainer_refresh_scheduler.swift", "livecontainer_refresh_alarm.swift", "livecontainer_refresh_settings.swift"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                file = Path(directory)/name
                file.write_text(read_product_source(name))
                result = subprocess.run([compiler, "-frontend", "-parse", str(file)], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_policy_executes_with_signed_and_unmodified_allowlists(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")
        source = read_product_source("livecontainer_refresh_policy.swift")
        source += r'''
let base = "com.kdt.livecontainer.sidestore.automatic-refresh"
func info(_ id: String, modes: [String] = ["processing", "fetch"]) -> [String: Any] {
    ["CFBundleIdentifier": "com.kdt.livecontainer.TESTTEAM", "BGTaskSchedulerPermittedIdentifiers": [id, id + ".watchdog"], "UIBackgroundModes": modes]
}
let original = try LiveContainerRefreshTaskIdentifiers.resolve(info: info(base))
precondition(original.processing == base) // ordinary iLoader, no allowlist rewrite
let rewritten = base.replacingOccurrences(of: "livecontainer.", with: "livecontainer.TESTTEAM.")
let signedIDs = try LiveContainerRefreshTaskIdentifiers.resolve(info: info(rewritten))
precondition(signedIDs.processing == rewritten)
do { _ = try LiveContainerRefreshTaskIdentifiers.resolve(info: info(base, modes: ["processing"])); fatalError("missing fetch accepted") } catch {}
do { _ = try LiveContainerRefreshTaskIdentifiers.resolve(info: [:]); fatalError("missing allowlist accepted") } catch {}
let now = Date(timeIntervalSince1970: 1000)
let deadline = now.addingTimeInterval(3600)
let eligible = now.addingTimeInterval(4000)
let retry = now.addingTimeInterval(5000)
precondition(LiveContainerRefreshPolicy.earliestUsefulDate(now: now, deadline: deadline, lead: 3600, eligible: eligible, retry: retry) == retry)
precondition(!LiveContainerRefreshPolicy.workIsDue(now: now, eligible: nil, retry: retry, pendingHandoff: false, retryExhausted: false, manual: false))
precondition(!LiveContainerRefreshPolicy.workIsDue(now: now, eligible: nil, retry: nil, pendingHandoff: true, retryExhausted: false, manual: true))
precondition(LiveContainerRefreshPolicy.workIsDue(now: now, eligible: eligible, retry: retry, pendingHandoff: false, retryExhausted: true, manual: true))
precondition(!LiveContainerRefreshPolicy.workIsDue(now: now, eligible: nil, retry: nil, pendingHandoff: false, retryExhausted: true, manual: false))
precondition(LiveContainerRefreshPolicy.retryDelay(failureCount: 1) == 300)
precondition(LiveContainerRefreshPolicy.retryDelay(failureCount: 2) == 1200)
precondition(LiveContainerRefreshPolicy.retryDelay(failureCount: 3) == 3600)
precondition(LiveContainerRefreshPolicy.retryDelay(failureCount: 4) == nil)
let gate = LiveContainerRefreshCompletionGate()
precondition(gate.claim()); precondition(!gate.claim()); precondition(!gate.claim())
let concurrentGate = LiveContainerRefreshCompletionGate()
let winnersLock = NSLock(); var winners = 0
DispatchQueue.concurrentPerform(iterations: 100) { _ in
    if concurrentGate.claim() { winnersLock.lock(); winners += 1; winnersLock.unlock() }
}
precondition(winners == 1)
let value = 42
print("INTERPOLATION_VALUE=\(value)")
print("POLICY_TESTS_PASSED")
'''
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "main.swift"
            executable = Path(directory) / "policy-test"
            file.write_text(source)
            build = subprocess.run([compiler, "-swift-version", "5", str(file), "-o", str(executable)], text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(executable)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("INTERPOLATION_VALUE=42", result.stdout)
            self.assertIn("POLICY_TESTS_PASSED", result.stdout)

    def test_notification_and_completion_contract(self):
        scheduler = read_product_source("livecontainer_refresh_scheduler.swift")
        alarm = read_product_source("livecontainer_refresh_alarm.swift")
        self.assertIn("await requestNotificationPermission()", scheduler)
        self.assertIn("requestNotificationPermissionFromUserAction", scheduler)
        self.assertIn("authorizationStatus == .denied", scheduler)
        self.assertIn("UIApplication.openSettingsURLString", scheduler)
        self.assertIn("gate.claim()", scheduler)
        self.assertIn("try Task.checkCancellation()", scheduler)
        self.assertIn("UNTimeIntervalNotificationTrigger", scheduler)
        self.assertIn("await LiveContainerAutoRefreshScheduler.requestRefreshNow()", alarm)
        self.assertIn("#if canImport(AlarmKit)", alarm)
        self.assertIn("@available(iOS 26.1, *)", alarm)
        self.assertNotIn("Timer.scheduledTimer", scheduler)
        self.assertNotIn("Task.sleep", scheduler)


if __name__ == "__main__": unittest.main()
