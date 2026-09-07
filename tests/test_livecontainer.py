"""Generated Swift regression tests, real policy execution, and patch idempotence."""
from pathlib import Path
import importlib.util
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("livecontainer_patch", ROOT / "scripts/patch_livecontainer_autorefresh.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


def fixture(root: Path) -> None:
    for directory in ("SideStoreSupport", "LiveContainerSwiftUI/App", "LiveContainerSwiftUI/Views/Settings", "LiveContainer.xcodeproj", "LiveContainer"):
        (root / directory).mkdir(parents=True)
    (root / "SideStoreSupport/SideStore.swift").write_text("import Foundation\n\nclass RefreshHandler: NSObject, RefreshServer {\n}\n")
    (root / "LiveContainerSwiftUI/App/AppDelegate.swift").write_text(
        "import UIKit\nimport SwiftUI\nimport Intents\n\n@objc class AppDelegate: UIResponder, UIApplicationDelegate {\n"
        "    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? ) -> Bool {\n"
        "        application.shortcutItems = nil\n        return true\n    }\n}\n\nclass SceneDelegate: NSObject {}\n")
    (root / "LiveContainer/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": "com.kdt.livecontainer", "MinimumOSVersion": "15.0",
        "BGTaskSchedulerPermittedIdentifiers": ["upstream.existing.task"], "UIBackgroundModes": ["audio"]}))
    (root / "LiveContainer.xcodeproj/project.pbxproj").write_text(
        "/* Begin PBXBuildFile section */\n"
        "17413FB22D9C0BAE00F3F928 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t\t);\n};\n"
        "17554B6A2DA165D8004C6D90 /* Frameworks */ = {\n\t\t\tisa = PBXFrameworksBuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n\t\t\t);\n};\n"
        "/* Begin PBXTargetDependency section */\n/* End PBXTargetDependency section */\n"
        "17413FB42D9C0BAE00F3F928 /* LiveContainerSwiftUI */ = {\n\t\t\tisa = PBXNativeTarget;\n\t\t\tdependencies = (\n\t\t\t);\n\t\t\tfileSystemSynchronizedGroups = (\n\t\t\t\t17413FB62D9C0BAE00F3F928 /* LiveContainerSwiftUI */\n\t\t\t);\n};\n"
        '\t\t\t\tOTHER_LDFLAGS = (\n\t\t\t\t\t"-e",\n\t\t\t\t\t_LiveContainerMainC,\n\t\t\t\t);\n'
        "IPHONEOS_DEPLOYMENT_TARGET = 15.0;\n")
    (root / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift").write_text(
        "struct LCSettingsView: View {\n    var body: some View {\n        NavigationView {\n            Form {\n            }\n        }\n    }\n}\n")


def apply(root: Path) -> None:
    for operation in (patch.patch_support, patch.patch_host_delegate, patch.patch_host_info,
                      patch.patch_project, patch.patch_alarm_provider, patch.patch_settings):
        operation(root)
    patch.verify(root)


class LiveContainerPatchTests(unittest.TestCase):
    def test_generated_host_fragments_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            apply(root)
            first = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            apply(root)
            self.assertEqual(first, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})
            info = plistlib.loads((root / "LiveContainer/Info.plist").read_bytes())
            self.assertEqual(info["MinimumOSVersion"], "15.0")
            self.assertEqual(set(info["UIBackgroundModes"]), {"audio", "processing", "fetch"})
            self.assertIn("upstream.existing.task", info["BGTaskSchedulerPermittedIdentifiers"])
            self.assertEqual(len(info["BGTaskSchedulerPermittedIdentifiers"]), 3)

    def test_templates_have_no_python_double_escaping(self):
        self.assertNotIn(r'\\(', patch.HOST_SCHEDULER)
        self.assertNotIn('?? \\"com.kdt.livecontainer', patch.HOST_SCHEDULER)
        self.assertIn('16SideStoreSupport20RefreshAllAppsIntentV', patch.BRIDGE)
        self.assertIn('identifier: "RefreshAllIntent"', patch.BRIDGE)
        self.assertNotIn('9SideStore20RefreshAllAppsIntentV', patch.BRIDGE)
        self.assertIn('LiveContainerRefreshTaskIdentifiers.resolve', patch.HOST_SCHEDULER)
        self.assertNotIn('Bundle.main.bundleIdentifier ??', patch.HOST_SCHEDULER)

    def test_plain_templates_parse_with_swift_compiler(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable; generated Swift NOT validated locally")
        for file in sorted((ROOT / "scripts/templates").glob("livecontainer_refresh_*.swift")):
            result = subprocess.run([compiler, "-frontend", "-parse", str(file)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, f"{file.name}: {result.stderr}")

    def test_failed_ci_interpolation_is_reproduced(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "Broken.swift"
            file.write_text(r'let taskIdentifier = "\(Bundle.main.bundleIdentifier ?? \"com.kdt.livecontainer\").sidestore.automatic-refresh"')
            result = subprocess.run([compiler, "-frontend", "-parse", str(file)], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("string", result.stderr)

    def test_policy_executes_with_signed_and_unmodified_allowlists(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")
        source = (ROOT / "scripts/templates/livecontainer_refresh_policy.swift").read_text()
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
        self.assertIn("await requestNotificationPermission()", patch.HOST_SCHEDULER)
        self.assertIn("gate.claim()", patch.HOST_SCHEDULER)
        self.assertIn("try Task.checkCancellation()", patch.HOST_SCHEDULER)
        self.assertIn("UNTimeIntervalNotificationTrigger", patch.HOST_SCHEDULER)
        self.assertIn("await LiveContainerAutoRefreshScheduler.requestRefreshNow()", patch.ALARM_PROVIDER)
        self.assertIn("#if canImport(AlarmKit)", patch.ALARM_PROVIDER)
        self.assertIn("@available(iOS 26.1, *)", patch.ALARM_PROVIDER)
        self.assertNotIn("Timer.scheduledTimer", patch.HOST_SCHEDULER)
        self.assertNotIn("Task.sleep", patch.HOST_SCHEDULER)

    def test_pinned_host_integration_when_available(self):
        source = os.environ.get("LIVE_CONTAINER_TEST_SOURCE")
        if not source:
            self.skipTest("LIVE_CONTAINER_TEST_SOURCE is not available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("SideStoreSupport/SideStore.swift", "LiveContainerSwiftUI/App/AppDelegate.swift", "LiveContainer/Info.plist",
                             "LiveContainer.xcodeproj/project.pbxproj", "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(source) / relative, target)
            apply(root)
            support = (root / "SideStoreSupport/SideStore.swift").read_text()
            start = support.index("guard let client = self.client")
            self.assertLess(support.index("self.c = c", start), support.index("client.refreshAllApps", start))
            apply(root)


if __name__ == "__main__":
    unittest.main()
