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

final class MultitaskDockManager: NSObject {
    @Published var isVisible: Bool = false
    @Published @objc var isCollapsed: Bool = false
    @Published var isDockHidden: Bool = false
    @Published var settingsChanged: Bool = false

    override init() {
        super.init()
        keyWindow!.rootViewController!.view.subviews.first!.addSubview(self.windowHostingView)
        setupDockView()
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
            # Declaration default is untouched: absent/false preference stays expanded.
            self.assertIn("@Published @objc var isCollapsed: Bool = false", dock)
            # The persisted preference is read once at creation.
            self.assertIn('isCollapsed = LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed")', dock)

    def test_session_reapplies_preference_without_fighting_user(self):
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            patch.patch(live)
            dock = (live / "MultitaskSupport/MultitaskDockView.swift").read_text(encoding="utf-8")
            # One reader, called at creation and at each fresh session.
            self.assertIn("func applyStartCollapsedPreference()", dock)
            self.assertIn("applyStartCollapsedPreference()", dock[dock.index("override init()"):])
            self.assertIn("if !self.collapseManuallyOverridden", dock)
            # Manual toggle marks the session overridden; session end clears it.
            toggle = dock[dock.index("func toggleDockCollapse"):]
            toggle = toggle[:toggle.index("\n    }", toggle.index("isCollapsed.toggle")) + 6]
            self.assertIn("collapseManuallyOverridden = true", toggle)
            self.assertIn("collapseManuallyOverridden = false", dock)
            # The applied value is logged for field diagnosis (boolean only).
            self.assertIn('NSLog("[LC_DOCK] apply collapsed=%d"', dock)
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
            end = dock.index("    private func updateDockFrame", start)
            dock_path.write_text(dock[:start] + guest_remove + dock[end:], encoding="utf-8")
            patch.patch(live)
            patched = dock_path.read_text(encoding="utf-8")
            self.assertIn("collapseManuallyOverridden = false", patched)
            self.assertIn(patch.SESSION_MARKER, patched)
            before = snapshot(Path(directory))
            patch.patch(live)
            self.assertEqual(before, snapshot(Path(directory)))

    def test_no_continuous_reapplication(self):
        with tempfile.TemporaryDirectory() as directory:
            live = fixture(Path(directory))
            patch.patch(live)
            dock = (live / "MultitaskSupport/MultitaskDockView.swift").read_text(encoding="utf-8")
            # Declaration default is untouched: absent/false preference stays expanded.
            self.assertIn("@Published @objc var isCollapsed: Bool = false", dock)
            # Exactly one init-time read of the persisted preference...
            self.assertEqual(dock.count('isCollapsed = LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed")'), 1)
            # ...and the user's own toggle remains the only other write.
            self.assertEqual(dock.count("isCollapsed.toggle()"), 1)
            assignments = [line.strip() for line in dock.splitlines()
                           if "isCollapsed =" in line and "bool(forKey:" not in line]
            self.assertEqual(assignments, [])

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
            self.assertEqual(dock.count('isCollapsed = LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed")'), 1)
            self.assertEqual([line.strip() for line in dock.splitlines()
                              if "isCollapsed =" in line and "bool(forKey:" not in line], [])
            compiler = shutil.which("swiftc")
            if compiler:
                for name in ("MultitaskSupport/MultitaskDockView.swift",
                             "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift"):
                    result = subprocess.run([compiler, "-frontend", "-parse", str(live / name)],
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
