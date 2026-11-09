"""Execute the maintained dock session helper and inspect its real call sites.

No historical generator or upstream fixture is involved. Portable state-machine
execution and Swift syntax checks do not claim a device/SwiftUI rendering pass.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from tests.owner_source import owner_source

ROOT = Path(__file__).resolve().parents[2]
DOCK = "MultitaskSupport/MultitaskDockView.swift"
SETTINGS = "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift"


def session_helper(source):
    start = source.index("enum LCMultitaskDockRenderedMode")
    end = source.index("// MARK: - App Info Provider", start)
    return "import Foundation\n\n" + source[start:end].rstrip() + "\n"


def verify_hidden_preference_separation(source):
    ranges = (
        ("override init()", "private func setupDockView"),
        ("private func setupDockView", "private func updateDockFrame"),
        ("@objc public func showDock", "@objc public func hideDock"),
        ("@objc public func addRunningAppWithInfo", "@objc public func minimizeAllWindows"),
    )
    for start, end in ranges:
        first = source.index(start)
        preparation = source[first:source.index(end, first)]
        if "LCHideCollapsedDock" in preparation or re.search(r"self\.isDockHidden\s*=\s*false\b", preparation):
            raise ValueError("Session preparation overwrites the independent hidden-dock preference: " + start)


class DockOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dock = owner_source(DOCK)
        cls.settings = owner_source(SETTINGS)

    def test_preference_sets_is_collapsed_before_the_first_host_mount(self):
        dock, settings = self.dock, self.settings
        # Apply the setting before refreshing the SwiftUI root and mounting
        # it, so the first body evaluation chooses the expected view branch.
        self.assertIn("@Published @objc var isCollapsed: Bool = false", dock)
        self.assertIn("self.isCollapsed = stored", dock)
        self.assertIn("collapseStartState.begin(storedPreference: stored, storedTuckedPreference: storedTucked)", dock)
        self.assertIn("applyTuckedBeforeFirstFrame(sessionID: sessionID)", dock)
        self.assertIn("applyBeforeFirstFrame(sessionID: sessionID)", dock)
        self.assertIn("self.isCollapsed = initial", dock)
        self.assertIn("if dockManager.isCollapsed {", dock)
        self.assertIn("CollapsedDockView(isHidden: dockManager.isDockHidden)", dock)
        self.assertIn("hostingController.rootView = AnyView(MultitaskDockSwiftView().environmentObject(self).id(sessionID))", dock)
        self.assertIn("SESSION_PREPARED manager=", dock)
        self.assertIn("BODY_FIRST_RENDER", dock)
        self.assertIn("BODY_EVALUATION_FIRST manager=", dock)
        self.assertIn("BODY_BRANCH_SELECTED_FIRST manager=", dock)
        self.assertIn("v3DockPresentationState.isReady", dock)
        self.assertIn(".onAppear { dockManager.v3RecordFirstRenderedDockView(.collapsedDockView) }", dock)
        self.assertIn(".onAppear { dockManager.v3RecordFirstRenderedDockView(.expandedDockView) }", dock)
        self.assertIn("ROOT_REUSED_FOR_SESSION", dock)
        self.assertLess(dock.index("self.isCollapsed = initial"), dock.index("hostingController.rootView = AnyView"))
        first_show = dock.index("self.showDock()", dock.index("SESSION_PREPARED manager="))
        self.assertLess(dock.index("SESSION_PREPARED manager="), first_show)
        self.assertIn("v3DockPresentationState.begin(sessionID: sessionID)", dock)

    def test_session_reapplies_preference_without_fighting_user(self):
        dock, settings = self.dock, self.settings
        # A fresh session applies the setting before its root view can mount.
        self.assertIn("!self.collapseStartState.isActiveSession", dock)
        self.assertIn("applyBeforeFirstFrame(sessionID: sessionID)", dock)
        setup = dock[dock.index("private func setupDockView"):dock.index("private func updateDockFrame")]
        show = dock[dock.index("@objc public func showDock"):dock.index("@objc public func hideDock")]
        self.assertIn("SETUP_ROOT_CREATE", setup)
        self.assertIn("SHOW_BLOCK_EXECUTED", show)
        first_show = dock.index("self.showDock()", dock.index("SESSION_PREPARED manager="))
        self.assertLess(dock.index("SESSION_PREPARED manager="), first_show)
        self.assertIn("FIRST_PRESENTED_VIEW", dock)
        self.assertIn("MULTITASK_DOCK_SETUP_PRESENT_V1", dock)
        self.assertIn("MULTITASK_DOCK_SESSION_RECOVERY_V1", dock)
        self.assertLess(dock.index("self.isCollapsed = initial"), first_show)
        self.assertIn(".id(sessionID)", dock,
                      "a reused hosting controller must build a fresh SwiftUI body for each dock session")
        # Manual toggle marks the session overridden; session teardown resets it.
        toggle = dock[dock.index("func toggleDockCollapse"):]
        toggle = toggle[:toggle.index("\n    }", toggle.index("isCollapsed.toggle")) + 6]
        self.assertIn("collapseStartState.userDidToggle()", toggle)
        self.assertIn("collapseStartState.end()", dock)
        for marker in ("stored_preference=", "SESSION_BEGIN manager=", "apps_count=", "isCollapsed_before_setup=",
                       "FIRST_PRESENTED_VIEW", "BODY_EVALUATION_FIRST", "BEFORE_FIRST_FRAME",
                       "BODY_BRANCH_SELECTED_FIRST",
                       "CollapsedDockView", "renderedDockMode", "BEFORE_SETUP_DOCK_VIEW",
                       "BEFORE_SHOW_BLOCK", "SHOW_BLOCK_ENTER", "SHOW_BLOCK_EXECUTED", "IS_COLLAPSED_PUBLISHED",
                       "LCSharedUtils.appGroupID()"):
            self.assertIn(marker, dock)
        # Hidden-dock and Guest Return states are separate from this setting.
        # The complete owner has legitimate independent hidden-dock actions.
        # Guard every preference/session setup path, including app admission.
        verify_hidden_preference_separation(dock)
        self.assertIn('bool(forKey: "LCMultitaskDockStartsTuckedToEdge")', dock)
        self.assertIn("isDockHidden=", dock)
        self.assertIn("MULTITASK_DOCK_RESHOW_AFTER_TRANSITION_V1", dock)
        # Rotation/layout paths never touch collapse state.
        self.assertNotIn("collapseManuallyOverridden", dock[dock.index("deviceOrientationDidChange"):])

    def test_session_preparation_rejects_hidden_preference_regressions(self):
        start = self.dock.index("@objc public func addRunningAppWithInfo")
        insertion = self.dock.index("{", start) + 1
        admission = self.dock[start:self.dock.index("@objc public func minimizeAllWindows", start)]
        self.assertIn("SESSION_PREPARED", admission)
        self.assertIn("applyTuckedBeforeFirstFrame", admission)
        for regression in ('self.isDockHidden = false',
                           'let hidden = LCUtils.appGroupUserDefault.bool(forKey: "LCHideCollapsedDock")'):
            with self.subTest(regression=regression), self.assertRaisesRegex(ValueError, "addRunningAppWithInfo"):
                verify_hidden_preference_separation(self.dock[:insertion] + "\n" + regression + self.dock[insertion:])
        # An explicit user action outside session preparation remains legal.
        self.assertIn("self.isDockHidden = false", self.dock[self.dock.index("func showDockFromHidden"):])
        verify_hidden_preference_separation(self.dock)

    def test_guest_removal_ends_dock_session_before_hiding(self):
        remove = self.dock[self.dock.index("@objc public func removeRunningApp"):self.dock.index("@objc public func showDock")]
        self.assertIn("if self.apps.isEmpty", remove)
        self.assertIn("self.collapseStartState.end()", remove)
        self.assertLess(remove.index("self.collapseStartState.end()"), remove.index("self.hideDock()"))

    def test_no_continuous_reapplication(self):
        dock, settings = self.dock, self.settings
        # The persisted bool is applied once before first presentation.
        self.assertIn("@Published @objc var isCollapsed: Bool = false", dock)
        self.assertEqual(dock.count("self.isCollapsed = initial"), 2)
        self.assertEqual(dock.count("self.collapseStartState.userDidToggle()"), 1)
        self.assertEqual(dock.count("isCollapsed.toggle()"), 1)
        self.assertEqual(dock.count("applyBeforeFirstFrame(sessionID: sessionID)"), 2)
        self.assertNotIn("applyStartCollapsedPreference", dock)

    def test_hide_collapsed_dock_untouched(self):
        dock, settings = self.dock, self.settings
        # Existing Hide label keeps its localization key, verbatim.
        self.assertIn('                Toggle(isOn: $hideCollapsedDock) {\n                    Text("lc.settings.hideCollapsedDock".loc)\n                }',
                      settings)
        self.assertIn('Toggle(isOn: $dockStartsTuckedToEdge)', settings)

    def test_settings_row_placement(self):
        dock, settings = self.dock, self.settings
        slider = settings.index("Slider(value: $dockWidth")
        start = settings.index("Start Dock Collapsed")
        tucked = settings.index("Start Dock Tucked To Edge")
        hide = settings.index('Toggle(isOn: $hideCollapsedDock)')
        self.assertLess(slider, start)
        self.assertLess(start, tucked)
        self.assertLess(tucked, hide)

    def test_separate_preference_from_guest_and_hide(self):
        dock, settings = self.dock, self.settings
        for text in (dock, settings):
            self.assertNotIn("LCGuestReturnStartsCollapsed", text)
            self.assertNotIn("LCGuestReturn", text)


    def test_settings_persist_in_host_app_group_and_not_guest_preferences(self):
        for key in ("LCMultitaskDockStartsCollapsed", "LCMultitaskDockStartsTuckedToEdge", "LCHideCollapsedDock"):
            self.assertIn(f'@AppStorage("{key}", store: LCUtils.appGroupUserDefault)', self.settings)
        for key in ("LCMultitaskDockStartsCollapsed", "LCMultitaskDockStartsTuckedToEdge"):
            self.assertIn(f'LCUtils.appGroupUserDefault.bool(forKey: "{key}")', self.dock)
        self.assertIn("MULTITASK_DOCK_START_COLLAPSED_V3", self.dock)


class DockOwnerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("swiftc")
        if not cls.compiler:
            raise RuntimeError("Swift compiler required for maintained dock runtime tests")
        cls.dock = owner_source(DOCK)
        cls.settings = owner_source(SETTINGS)

    def test_first_presented_view_and_session_override_execute(self):
        helper = session_helper(self.dock)
        harness = (ROOT/"tests/fixtures/multitask_dock_session_harness.swift").read_text()
        with tempfile.TemporaryDirectory() as directory:
            source, executable = Path(directory)/"main.swift", Path(directory)/"dock-session"
            source.write_text(helper + "\n" + harness)
            build = subprocess.run([self.compiler, "-parse-as-library", str(source), "-o", str(executable)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("DOCK_FIRST_PRESENTED_VIEW_AND_TUCK_BEHAVIOR_PASS", result.stdout)

    def test_current_owner_swift_sources_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, contents in (("Dock.swift", self.dock), ("Settings.swift", self.settings)):
                source = Path(directory)/name
                source.write_text(contents)
                result = subprocess.run([self.compiler, "-frontend", "-parse", str(source)],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
