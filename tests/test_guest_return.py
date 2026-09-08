"""Execute the actual injected registry and geometry; validate pinned patches in CI."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("guest_return", ROOT / "scripts/patch_guest_return.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SWIFT_STUBS = r'''
import Foundation
final class UISceneSession: NSObject {
    let persistentIdentifier: String
    init(_ id: String) { persistentIdentifier = id }
}
final class UIApplication {
    static let shared = UIApplication()
    var openSessions: Set<UISceneSession> = []
    var activated: [String] = []
    func requestSceneSessionActivation(_ session: UISceneSession?, userActivity: Any?, options: Any?, errorHandler: ((Error)->Void)?) {
        if let session { activated.append(session.persistentIdentifier) }
    }
}
final class SharedModel { var enableMultipleWindow = false }
final class DataManager {
    static let shared = DataManager()
    let model = SharedModel()
}
final class AppSceneViewController: NSObject {
    var pid: Int32
    var isAppRunning = true
    var cleanupCount = 0
    var exit: (() -> Void)?
    init(_ pid: Int32) { self.pid = pid }
    func appTerminationCleanUp() {
        cleanupCount += 1
        isAppRunning = false
        exit?()
    }
}
struct MultitaskAppInfo {
    var displayName: String
    var dataUUID: String
    var bundleId: String
    let windowID = UUID().uuidString
    var pid: Int32 = 0
    weak var controller: AppSceneViewController?
    var launchCallback: ((NSNumber, Error?) -> Void)?
}
class MultitaskWindowManager: NSObject {
    static var appDict: [String: MultitaskAppInfo] = [:]
    static var opened: [(String, String)] = []
    static func openWindow(id: String, value: String) { opened.append((id, value)) }
'''

SWIFT_TESTS = r'''
}
@main struct Tests {
    static func main() {
        typealias M = MultitaskWindowManager
        func launch(_ id: String, _ callback: @escaping (NSNumber, Error?)->Void) -> String {
            M.openAppWindow(displayName: id, dataUUID: id, bundleId: "app." + id, pidCallback: callback)
            return M.appDict.first(where: { $0.value.dataUUID == id })!.key
        }
        var results: [String: Int] = [:]
        let a = launch("A") { p, e in precondition(e == nil); results["A"] = p.intValue }
        let b = launch("B") { p, e in precondition(e == nil); results["B"] = p.intValue }
        precondition(a != b && a != "A" && b != "B")
        let ca = AppSceneViewController(101), cb = AppSceneViewController(202)
        M.bind(ca, windowID: a); M.bind(cb, windowID: b)
        // Reverse completion order must not swap callbacks or drop either result.
        M.initialized(cb, windowID: b, error: nil)
        M.initialized(ca, windowID: a, error: nil)
        precondition(results == ["A": 101, "B": 202])
        precondition(M.appDict[a]?.launchCallback == nil)
        precondition(M.openExistingAppWindow(dataUUID: "A"))
        precondition(M.opened.last!.1 == a)
        precondition(ca.cleanupCount == 0 && cb.cleanupCount == 0)
        var duplicateError = 0
        M.openAppWindow(displayName: "A", dataUUID: "A", bundleId: "app.A") { _, e in
            precondition(e != nil); duplicateError += 1
        }
        precondition(duplicateError == 1 && M.appDict.count == 2)
        // Minimized process death: cleanup before replacement, not a kill/relaunch of a live guest.
        ca.exit = { M.exited(ca, windowID: a) }
        ca.isAppRunning = false
        precondition(!M.openExistingAppWindow(dataUUID: "A"))
        precondition(ca.cleanupCount == 1 && M.appDict[a] == nil)
        var newCalls = 0
        let newA = launch("A") { _, e in precondition(e == nil); newCalls += 1 }
        precondition(newA != a)
        let newCA = AppSceneViewController(303)
        M.bind(newCA, windowID: newA)
        M.initialized(ca, windowID: a, error: nil) // Old completion must not touch new launch.
        M.exited(ca, windowID: a)
        precondition(M.appDict[newA] != nil && newCalls == 0)
        M.initialized(newCA, windowID: newA, error: nil)
        M.initialized(newCA, windowID: newA, error: nil)
        precondition(newCalls == 1)
        // Pending launch and zero PID are not declared dead.
        var pendingErrors = 0
        let pending = launch("pending") { _, e in if e != nil { pendingErrors += 1 } }
        precondition(M.openExistingAppWindow(dataUUID: "pending"))
        let pendingC = AppSceneViewController(0)
        M.bind(pendingC, windowID: pending)
        M.exited(pendingC, windowID: pending)
        M.exited(pendingC, windowID: pending)
        precondition(pendingErrors == 1 && M.appDict[pending] == nil)
        // Initializer failure must release this exact launch and propagate once.
        var failureCalls = 0
        let failed = launch("failed") { _, e in precondition(e != nil); failureCalls += 1 }
        let failedC = AppSceneViewController(0)
        M.bind(failedC, windowID: failed)
        failedC.exit = { M.exited(failedC, windowID: failed) }
        M.initialized(failedC, windowID: failed, error: NSError(domain: "test", code: 1))
        precondition(failureCalls == 1 && M.appDict[failed] == nil)
        // Activating host must target its real session; only create when missing.
        var creations = 0
        let main = UISceneSession("main")
        UIApplication.shared.openSessions = [main]
        M.mainSceneSession = main
        M.activateMainScene { creations += 1 }
        precondition(UIApplication.shared.activated == ["main"] && creations == 0)
        UIApplication.shared.openSessions = []
        M.activateMainScene { creations += 1 }
        precondition(creations == 1 && M.mainSceneSession == nil)
        precondition(cb.cleanupCount == 0 && newCA.cleanupCount == 0)
        print("REGISTRY_RUNTIME_TESTS_PASSED")
    }
}
'''

class GuestReturnTests(unittest.TestCase):
    def test_preservation_has_no_termination_or_new_session(self):
        self.assertIn("minimizeWindow", module.METHODS)
        self.assertIn("self.lcActivateHost()", module.METHODS)
        for forbidden in ("terminate]", "SIGKILL", "raise(", "launchToGuestApp", "NSExtension", "removeObjectForKey"):
            self.assertNotIn(forbidden, module.METHODS)

    def test_control_is_event_driven_and_touch_transparent(self):
        for required in ("return hit == self ? nil : hit", "UIGestureRecognizerStateEnded", "isfinite(x)",
                         "self.safeAreaInsets", "UIKeyboardWillChangeFrameNotification", "rect.size.width < 44"):
            self.assertIn(required, module.CONTROL)
        for forbidden in ("NSTimer", "dispatch_after", "sleep("):
            self.assertNotIn(forbidden, module.CONTROL)

    def test_direct_control_is_separate_and_honest(self):
        self.assertIn('Hide Return Button', module.CONTROL)
        self.assertIn('boolForKey:@"LCHideReturnControl"', module.CONTROL)
        self.assertIn('LCHideReturnControl', module.DIRECT_CONTROL)
        self.assertIn("Restarts LiveContainer and closes this guest", module.DIRECT_CONTROL)
        self.assertIn("DIRECT_PROCESS_RESTART_RETURN", module.DIRECT_RUNTIME)
        self.assertIn("launchToGuestAppWithClassicMode:0", module.DIRECT_RUNTIME)
        self.assertIn("UIWindowDidBecomeVisibleNotification", module.DIRECT_RUNTIME)
        self.assertIn("UISceneDidDisconnectNotification", module.DIRECT_RUNTIME)
        self.assertIn("window.hidden = NO", module.DIRECT_RUNTIME)
        self.assertNotIn("makeKeyAndVisible", module.DIRECT_RUNTIME)
        self.assertNotIn("launchToGuestApp", module.METHODS)
        for forbidden in ("NSTimer", "dispatch_after", "SIGKILL", "terminate", "sleep("):
            self.assertNotIn(forbidden, module.DIRECT_RUNTIME)

    def test_cleanup_finishes_before_exit_callback(self):
        self.assertLess(module.CLEANUP.index("unregisterMultitaskContainer"), module.CLEANUP.index("appSceneVCAppDidExit"))
        self.assertIn("NSThread.isMainThread", module.CLEANUP)
        self.assertNotIn("_kill", module.CLEANUP)
        self.assertNotIn("connectedScenes.first", module.DOCK_RESUME)
        self.assertIn("targetView.window", module.DOCK_RESUME)

    def test_registry_uses_generation_and_own_callback(self):
        self.assertNotIn("DataManager.shared.model.pidCallback", module.WINDOW_MANAGER)
        self.assertIn("entry.windowID", module.WINDOW_MANAGER)
        self.assertLess(module.WINDOW_MANAGER.index("entry.launchCallback = nil"), module.WINDOW_MANAGER.index("callback?("))
        self.assertNotIn("unregisterMultitaskContainer", module.WINDOW_MANAGER)
        self.assertNotIn("getpgid", module.WINDOW_MANAGER)

    def test_geometry_executes_for_resize_and_invalid_position(self):
        compiler = shutil.which("cc")
        if not compiler: self.skipTest("C compiler unavailable")
        source = "#include <math.h>\n#include <assert.h>\n" + module.GEOMETRY + r'''
int main(void) {
    for (int size = 44; size <= 2000; size += 7) {
        for (int p = -2; p <= 3; p++) {
            double center = LCReturnAxisCenter(9, size, p);
            assert(center - 22 >= 9 && center + 22 <= 9 + size);
        }
    }
    assert(LCReturnAxisCenter(0, 44, 1) == 22);
    assert(LCReturnAxisCenter(0, 10, 1) == 5);
    assert(isfinite(LCReturnAxisCenter(0, 300, NAN)));
    assert(LCReturnAxisCenter(NAN, 300, 0.2) == 0);
    assert(LCReturnAxisCenter(0, INFINITY, 0.2) == 0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            src, exe = Path(directory)/"geometry.c", Path(directory)/"geometry"
            src.write_text(source)
            subprocess.run([compiler, "-Wall", "-Wextra", "-Werror", str(src), "-lm", "-o", str(exe)], check=True, capture_output=True)
            subprocess.run([str(exe)], check=True, capture_output=True)

    def test_registry_executes_real_injected_code(self):
        compiler = shutil.which("swiftc")
        if not compiler: self.skipTest("Swift compiler unavailable")
        # Only remove Objective-C exposure for Linux. Registry bodies are the shipped code.
        source = SWIFT_STUBS + module.WINDOW_MANAGER.replace("@objc ", "") + SWIFT_TESTS
        with tempfile.TemporaryDirectory() as directory:
            src, exe = Path(directory)/"Registry.swift", Path(directory)/"registry"
            src.write_text(source)
            build = subprocess.run([compiler, "-parse-as-library", "-swift-version", "5", "-O", str(src), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("REGISTRY_RUNTIME_TESTS_PASSED", run.stdout)

    def test_pinned_patch_and_idempotence(self):
        source = Path(os.environ.get("LIVE_CONTAINER_TEST_SOURCE", str(ROOT / ".audit/upstream/LiveContainer")))
        if not source.is_dir(): self.skipTest("Pinned LiveContainer source unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in module.PATHS:
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name, dest)
            module.patch(root)
            self.assertIn('store: UserDefaults.lcUserDefaults()', (root / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift").read_text())
            first = {name: (root/name).read_bytes() for name in module.PATHS}
            module.patch(root)
            self.assertEqual(first, {name: (root/name).read_bytes() for name in module.PATHS})
            compiler = shutil.which("swiftc")
            if compiler:
                for name in module.PATHS:
                    if name.endswith(".swift"):
                        result = subprocess.run([compiler, "-frontend", "-parse", str(root/name)], text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, name + result.stderr)
            # No partial write when one pinned anchor drifts.
            broken = root / module.PATHS[0]
            broken.write_text(broken.read_text().replace("LCReturnControl", "UnexpectedControl"))
            with self.assertRaises(ValueError): module.patch(root)

if __name__ == "__main__": unittest.main()
