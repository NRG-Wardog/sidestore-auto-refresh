"""Regression coverage for the persistent Multitask Dock start-collapsed preference."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dock_patch", ROOT / "scripts/patch_multitask_dock.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)

DOCK_FIXTURE = '''import SwiftUI
import Combine

final class MultitaskDockManager: NSObject {
    @Published var isVisible: Bool = false
    @Published @objc var isCollapsed: Bool = false
    @Published var isDockHidden: Bool = false
    @Published var settingsChanged: Bool = false
    var hostingController: UIHostingController<AnyView>?
    var keyWindow: UIWindow! = nil

    override init() {
        super.init()
        keyWindow!.rootViewController!.view.subviews.first!.addSubview(self.windowHostingView)
        setupDockView()
    }

    private func setupDockView() {
        DispatchQueue.main.async {
            let dockView = AnyView(MultitaskDockSwiftView()
                .environmentObject(self))
            self.hostingController = UIHostingController(rootView: dockView)
        }
    }

    @objc public func toggleDockCollapse() {
        DispatchQueue.main.async {
            self.isCollapsed.toggle()
            self.updateDockFrame()
        }
    }

    @objc public func addRunningApp(_ appName: String, appUUID: String, view: UIView?) {
        DispatchQueue.main.async {
            self.apps.append(appModel)

            if self.apps.count == 1 {
                self.showDock()
            } else if self.isVisible {
                self.updateDockFrame()
            }
        }
    }

    @objc public func removeRunningApp(_ appUUID: String) {
        DispatchQueue.main.async {
            self.apps.removeAll { $0.appUUID == appUUID }

            if self.apps.isEmpty {
                self.hideDock()
            } else if self.isVisible {
                self.updateDockFrame()
            }
        }
    }

    @objc public func showDock() {
        guard !isVisible, let hostingController = hostingController else { return }
        let keyWindow = self.keyWindow!
        DispatchQueue.main.async {
            self.isVisible = true
            let screenBounds = keyWindow.bounds
            _ = screenBounds
            self.updateDockFrame(animated: false)
        }
    }

    private func updateDockFrame(animated: Bool = true) {
    }

    private func deviceOrientationDidChange() {
        if self.isVisible { updateDockFrame() }
    }
}
'''

SETTINGS_FIXTURE = '''import SwiftUI

struct LCMultitaskSettingView: View {
    @AppStorage("LCMultitaskMode", store: LCUtils.appGroupUserDefault) var multitaskMode: MultitaskMode = .virtualWindow
    @AppStorage("LCDockWidth", store: LCUtils.appGroupUserDefault) var dockWidth: Double = 80
    @AppStorage("LCHideCollapsedDock", store: LCUtils.appGroupUserDefault) var hideCollapsedDock: Bool = false

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Text("lc.settings.dockWidth".loc)
                    }
                    Slider(value: $dockWidth, in: 20...120) {
                        Text("lc.settings.dockWidth".loc)
                    }
                    .tint(.accentColor)
                }
                .padding(.vertical, 4)
                Toggle(isOn: $hideCollapsedDock) {
                    Text("lc.settings.hideCollapsedDock".loc)
                }
            }
        }
    }
}
'''


def fixture(root: Path) -> Path:
    live = root / "LiveContainer"
    dock = live / "MultitaskSupport/MultitaskDockView.swift"
    settings = live / "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift"
    dock.parent.mkdir(parents=True)
    settings.parent.mkdir(parents=True)
    dock.write_text(DOCK_FIXTURE, encoding="utf-8")
    settings.write_text(SETTINGS_FIXTURE, encoding="utf-8")
    return live


def snapshot(root: Path):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class DockPatchTests(unittest.TestCase):
    def test_session_preference_and_override_execute(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; session behavior executes in macOS CI")
        helper = (ROOT / "scripts/templates/multitask_dock_session_state.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/multitask_dock_session_harness.swift").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.swift"
            executable = Path(directory) / "dock-session-tests"
            source.write_text(helper + "\n" + harness, encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("DOCK_SESSION_BEHAVIOR_PASS", result.stdout)

    def test_patch_applies_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            patch.patch(live)
            first = snapshot(Path(directory))
            dock = (live / "MultitaskSupport/MultitaskDockView.swift").read_text(encoding="utf-8")
            settings = (live / "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift").read_text(encoding="utf-8")
            self.assertIn("LCMultitaskDockStartsCollapsed", dock)
            self.assertIn("LCMultitaskDockStartsCollapsed", settings)
            self.assertIn("Start Dock Collapsed", settings)
            patch.patch(live)
            self.assertEqual(first, snapshot(Path(directory)))

    def test_anchor_drift_fails_without_partial_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            path = live / "MultitaskSupport/MultitaskDockView.swift"
            path.write_text(path.read_text(encoding="utf-8").replace("override init() {", "override init () {"),
                            encoding="utf-8")
            before = snapshot(Path(directory))
            with self.assertRaises(SystemExit):
                patch.patch(live)
            self.assertEqual(before, snapshot(Path(directory)))

    def test_settings_anchor_drift_fails_without_partial_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            path = live / "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift"
            path.write_text(path.read_text(encoding="utf-8").replace("hideCollapsedDock", "hideDockCollapsed"),
                            encoding="utf-8")
            before = snapshot(Path(directory))
            with self.assertRaises(SystemExit):
                patch.patch(live)
            self.assertEqual(before, snapshot(Path(directory)))

    def test_default_expanded_pref_starts_collapsed(self):
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            patch.patch(live)
            dock = (live / "MultitaskSupport/MultitaskDockView.swift").read_text(encoding="utf-8")
            # No init-time write races setupDockView/showDock; the final show
            # queue applies the stored value immediately before the first frame.
            self.assertIn("@Published @objc var isCollapsed: Bool = false", dock)
            self.assertIn("collapseStartState.begin(storedPreference: stored)", dock)
            self.assertIn("applyBeforeFirstFrame(sessionID: sessionID)", dock)
            self.assertLess(dock.index("applyBeforeFirstFrame(sessionID: sessionID)"),
                            dock.index("self.updateDockFrame(animated: false)"))

    def test_session_reapplies_preference_without_fighting_user(self):
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            patch.patch(live)
            dock = (live / "MultitaskSupport/MultitaskDockView.swift").read_text(encoding="utf-8")
            # The final show queue reads session state immediately before the
            # first frame, after setupDockView's queued initialization.
            self.assertIn("pendingCollapseSessionID", dock)
            self.assertIn("collapseStartState.preference(for: sessionID)", dock)
            self.assertIn("applyBeforeFirstFrame(sessionID: sessionID)", dock)
            setup = dock[dock.index("private func setupDockView"):dock.index("private func updateDockFrame")]
            show = dock[dock.index("@objc public func showDock"):dock.index("private func updateDockFrame")]
            self.assertIn("SETUP_VIEW", setup)
            self.assertLess(show.index("SHOW session="), show.index("BEFORE_FIRST_FRAME"))
            # Manual toggle marks the session overridden; session teardown resets it.
            toggle = dock[dock.index("func toggleDockCollapse"):]
            toggle = toggle[:toggle.index("\n    }", toggle.index("isCollapsed.toggle")) + 6]
            self.assertIn("collapseStartState.userDidToggle()", toggle)
            self.assertIn("collapseStartState.end()", dock)
            for marker in ("stored_preference=", "SESSION_BEGIN id=", "apps_count=", "collapsed_before_show=",
                           "isCollapsed_inside_show=", "BEFORE_FIRST_FRAME"):
                self.assertIn(marker, dock)
            # Hide-collapsed-dock behavior is untouched.
            self.assertNotIn("LCHideCollapsedDock", dock)
            # Rotation/layout paths never touch collapse state.
            self.assertNotIn("collapseManuallyOverridden", dock[dock.index("deviceOrientationDidChange"):])

    def test_session_end_handles_guest_patched_shape(self):
        # patch_guest_return.py runs first in CI and replaces removeRunningApp
        # wholesale, collapsing the empty branch to one line. The dock patch
        # must handle that shape instead of dying on anchor drift.
        guest_remove = '''    @objc public func removeRunningApp(_ appUUID: String) {
        if !Thread.isMainThread {
            DispatchQueue.main.async { self.removeRunningApp(appUUID) }
            return
        }
        self.apps.removeAll { $0.appUUID == appUUID }
        if self.apps.isEmpty { self.hideDock() }
        else if self.isVisible { self.updateDockFrame() }
    }
'''
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            dock_path = live / "MultitaskSupport/MultitaskDockView.swift"
            dock = dock_path.read_text(encoding="utf-8")
            start = dock.index("    @objc public func removeRunningApp(")
            end = dock.index("    @objc public func showDock", start)
            dock_path.write_text(dock[:start] + guest_remove + dock[end:], encoding="utf-8")
            patch.patch(live)
            patched = dock_path.read_text(encoding="utf-8")
            self.assertIn("manuallyOverridden = false", patched)
            self.assertIn(patch.SESSION_MARKER, patched)
            before = snapshot(Path(directory))
            patch.patch(live)
            self.assertEqual(before, snapshot(Path(directory)))

    def test_no_continuous_reapplication(self):
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            patch.patch(live)
            dock = (live / "MultitaskSupport/MultitaskDockView.swift").read_text(encoding="utf-8")
            # Initial preference is applied only at the one-shot first-frame boundary.
            self.assertIn("@Published @objc var isCollapsed: Bool = false", dock)
            self.assertEqual(dock.count("self.isCollapsed = initial"), 1)
            self.assertEqual(dock.count("self.collapseStartState.userDidToggle()"), 1)
            self.assertEqual(dock.count("isCollapsed.toggle()"), 1)
            self.assertNotIn("applyStartCollapsedPreference", dock)

    def test_hide_collapsed_dock_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            patch.patch(live)
            settings = (live / "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift").read_text(encoding="utf-8")
            # Existing Hide label keeps its localization key, verbatim.
            self.assertIn('                Toggle(isOn: $hideCollapsedDock) {\n                    Text("lc.settings.hideCollapsedDock".loc)\n                }',
                          settings)

    def test_settings_row_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            patch.patch(live)
            settings = (live / "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift").read_text(encoding="utf-8")
            slider = settings.index("Slider(value: $dockWidth")
            start = settings.index("Start Dock Collapsed")
            hide = settings.index('Toggle(isOn: $hideCollapsedDock)')
            self.assertLess(slider, start)
            self.assertLess(start, hide)

    def test_separate_preference_from_guest_and_hide(self):
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            patch.patch(live)
            dock = (live / "MultitaskSupport/MultitaskDockView.swift").read_text(encoding="utf-8")
            settings = (live / "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift").read_text(encoding="utf-8")
            for text in (dock, settings):
                self.assertNotIn("LCGuestReturnStartsCollapsed", text)
                self.assertNotIn("LCGuestReturn", text)

    def test_template_parses_when_swift_is_available(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            patch.patch(live)
            for name in ("MultitaskSupport/MultitaskDockView.swift",
                         "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift"):
                result = subprocess.run([compiler, "-frontend", "-parse", str(live / name)],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)


class DockPinnedSourceTests(unittest.TestCase):
    def test_pinned_sources(self):
        source = os.getenv("LIVE_CONTAINER_TEST_SOURCE")
        if not source:
            self.skipTest("Set LIVE_CONTAINER_TEST_SOURCE to the pinned LiveContainer checkout")
        with tempfile.TemporaryDirectory() as directory:
            live = Path(directory) / "live"
            for name in ("MultitaskSupport/MultitaskDockView.swift",
                         "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift"):
                path = live / name
                path.parent.mkdir(parents=True)
                path.write_bytes(subprocess.check_output(
                    ["git", "-C", source, "show", patch.PIN + ":" + name]))
            patch.patch(live)
            dock = (live / "MultitaskSupport/MultitaskDockView.swift").read_text(encoding="utf-8")
            settings = (live / "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift").read_text(encoding="utf-8")
            self.assertIn('bool(forKey: "LCMultitaskDockStartsCollapsed")', dock)
            self.assertIn("Start Dock Collapsed", settings)
            self.assertEqual(dock.count("self.isCollapsed = initial"), 1)
            self.assertEqual(dock.count("self.collapseStartState.userDidToggle()"), 1)
            compiler = shutil.which("swiftc")
            if compiler:
                for name in ("MultitaskSupport/MultitaskDockView.swift",
                             "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift"):
                    result = subprocess.run([compiler, "-frontend", "-parse", str(live / name)],
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
