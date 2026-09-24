#!/usr/bin/env python3
"""Persistent 'Start Dock Collapsed' preference for the Multitask Dock.

Separate from LCGuestReturnStartsCollapsed (guest Return control) and from
LCHideCollapsedDock (which only hides an already-collapsed dock). The
preference sets MultitaskDockManager.isCollapsed before the fresh session's
SwiftUI root is mounted, so its first body selects CollapsedDockView or the
expanded dock. Manual toggles win for that session; layout/rotation never
reapplies the preference. Queued host creation and hide completion are covered.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

PIN = "12377cf3b91d51739a33f14a302e5f522b238593"
KEY = "LCMultitaskDockStartsCollapsed"
MARKER = "MULTITASK_DOCK_START_COLLAPSED_V1"
SESSION_MARKER = "MULTITASK_DOCK_SESSION_APPLY_V2"
RESHOW_MARKER = "MULTITASK_DOCK_RESHOW_AFTER_TRANSITION_V1"
RECOVERY_MARKER = "MULTITASK_DOCK_SESSION_RECOVERY_V1"
SETUP_PRESENT_MARKER = "MULTITASK_DOCK_SETUP_PRESENT_V1"

DOCK_VIEW = "MultitaskSupport/MultitaskDockView.swift"
SETTINGS_VIEW = "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift"
SESSION_HELPER = Path(__file__).with_name("templates") / "multitask_dock_session_state.swift"

PROP_LINE = f'    @AppStorage("{KEY}", store: LCUtils.appGroupUserDefault) var dockStartsCollapsed = false\n'
TOGGLE_LINE = '                Toggle(isOn: $dockStartsCollapsed) {\n'


def die(message: str) -> None:
    raise SystemExit(f"patch_multitask_dock: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def apply_dock_init(text: str) -> str:
    """Trace singleton initialization without relying on an init-time write."""
    if MARKER in text:
        if text.count(MARKER) != 1:
            die("previous dock init patch is partial or duplicated")
        return text
    old = "    override init() {\n        super.init()\n"
    new = ("    override init() {\n"
           "        super.init()\n"
           f"        // {MARKER}: record the selected preference and initial singleton state.\n"
           f'        NSLog("[LC_DOCK] INIT stored_preference=%d apps_count=%ld isCollapsed=%d", '
           f'LCUtils.appGroupUserDefault.bool(forKey: "{KEY}") ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0)\n')
    return replace_once(text, old, new, "dock manager init")


def apply_dock_session(text: str) -> str:
    """Select CollapsedDockView before a fresh session's first SwiftUI mount."""
    if SESSION_MARKER in text:
        if (text.count(SESSION_MARKER) != 4 or text.count(RESHOW_MARKER) != 1 or
                text.count(RECOVERY_MARKER) != 1 or text.count(SETUP_PRESENT_MARKER) != 1):
            die("previous dock session patch is partial or duplicated")
        return text
    if "MULTITASK_DOCK_SESSION_APPLY_V1" in text or "collapseManuallyOverridden" in text:
        die("unexpected pre-existing session state")
    helper = SESSION_HELPER.read_text(encoding="utf-8")
    if "import Combine\n" not in text:
        die("dock imports changed")
    text = replace_once(text, "import Combine\n", "import Combine\n\n" + helper + "\n", "session state helper")
    text = replace_once(
        text,
        '    @Published var settingsChanged: Bool = false\n',
        '    @Published var settingsChanged: Bool = false\n'
        f'    // {SESSION_MARKER}: session identity survives singleton reuse.\n'
        '    private var collapseStartState = LCMultitaskDockSessionState()\n'
        '    var renderedDockMode: LCMultitaskDockRenderedMode {\n'
        '        LCMultitaskDockSessionState.renderedMode(isCollapsed: isCollapsed)\n'
        '    }\n',
        "session state")
    if text.count("            self.isCollapsed.toggle()\n") != 1:
        die("toggle anchor is not unique")
    text = text.replace(
        "            self.isCollapsed.toggle()\n",
        '            self.collapseStartState.userDidToggle()\n'
        '            NSLog("[LC_DOCK] MANUAL_TOGGLE session=%@ apps_count=%ld collapsed_before=%d", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0)\n'
        "            self.isCollapsed.toggle()\n",
        1)
    text = replace_once(
        text,
        '            if self.apps.count == 1 {\n                self.showDock()\n',
        f'            // {RECOVERY_MARKER}: recover if a prior session lost its final removal callback.\n'
        '            if self.collapseStartState.wasPresented && !self.isVisible {\n'
        '                NSLog("[LC_DOCK] STALE_SESSION_RESET id=%@ apps_count=%ld isCollapsed=%d", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0)\n'
        '                self.apps = [appModel]\n'
        '                self.collapseStartState.end()\n'
        '            }\n'
        f'            if !self.collapseStartState.isActiveSession {{\n'
        f'                // {SESSION_MARKER}: snapshot the preference before the first view is selected.\n'
        f'                let stored = LCUtils.appGroupUserDefault.bool(forKey: "{KEY}")\n'
        '                let sessionID = self.collapseStartState.begin(storedPreference: stored)\n'
        '                NSLog("[LC_DOCK] SESSION_BEGIN id=%@ stored_preference=%d apps_count=%ld collapsed_before_show=%d", sessionID, stored ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0)\n'
        '                if let initial = self.collapseStartState.applyBeforeFirstFrame(sessionID: sessionID) { self.isCollapsed = initial }\n'
        '                self.isDockHidden = false\n'
        '                if let hostingController = self.hostingController {\n'
        '                    hostingController.rootView = AnyView(MultitaskDockSwiftView().environmentObject(self))\n'
        '                }\n'
        f'                // {SESSION_MARKER}: this value controls the first-presented SwiftUI branch.\n'
        '                NSLog("[LC_DOCK] FIRST_VIEW_SELECTED session=%@ isCollapsed=%d rendered=%@", sessionID, self.isCollapsed ? 1 : 0, self.renderedDockMode == .collapsedDockView ? "CollapsedDockView" : "ExpandedDockView")\n'
        '                self.showDock()\n',
        "fresh session first presentation")

    show_anchor = '''        DispatchQueue.main.async {
            self.isVisible = true
'''
    show_replacement = '''        DispatchQueue.main.async {
            NSLog("[LC_DOCK] SHOW session=%@ apps_count=%ld isCollapsed=%d rendered=%@", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0, self.renderedDockMode == .collapsedDockView ? "CollapsedDockView" : "ExpandedDockView")
            self.isVisible = true
'''
    text = replace_once(text, show_anchor, show_replacement, "showDock first-frame diagnostic")
    setup_anchor = '            let dockView = AnyView(MultitaskDockSwiftView()\n'
    setup_replacement = ('            NSLog("[LC_DOCK] SETUP_VIEW apps_count=%ld isCollapsed=%d", self.apps.count, self.isCollapsed ? 1 : 0)\n'
                         + setup_anchor)
    text = replace_once(text, setup_anchor, setup_replacement, "hosting view diagnostic")
    host_ready = '            self.hostingController?.view.backgroundColor = .clear\n'
    host_ready_replacement = (host_ready +
        f'            // {SETUP_PRESENT_MARKER}: the app-add queue can beat host-controller creation.\n'
        '            if !self.apps.isEmpty { self.showDock() }\n')
    text = replace_once(text, host_ready, host_ready_replacement, "late hosting-controller presentation")
    actual_presentation = '''            NSLog("[LC_DOCK] SHOW session=%@ apps_count=%ld isCollapsed=%d rendered=%@", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0, self.renderedDockMode == .collapsedDockView ? "CollapsedDockView" : "ExpandedDockView")
            self.isVisible = true
'''
    actual_presentation_replacement = '''            if let sessionID = self.collapseStartState.sessionID {
                let firstPresentation = self.collapseStartState.markPresented(sessionID: sessionID)
                NSLog("[LC_DOCK] FIRST_PRESENTED_VIEW session=%@ first=%d isCollapsed=%d rendered=%@", sessionID, firstPresentation ? 1 : 0, self.isCollapsed ? 1 : 0, self.renderedDockMode == .collapsedDockView ? "CollapsedDockView" : "ExpandedDockView")
            }
            NSLog("[LC_DOCK] SHOW session=%@ apps_count=%ld isCollapsed=%d rendered=%@", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0, self.renderedDockMode == .collapsedDockView ? "CollapsedDockView" : "ExpandedDockView")
            self.isVisible = true
'''
    text = replace_once(text, actual_presentation, actual_presentation_replacement,
                        "actual first-presented-view marker")
    first_frame_anchor = "            self.updateDockFrame(animated: false)"
    first_frame_log = ('            NSLog("[LC_DOCK] BEFORE_FIRST_FRAME session=%@ apps_count=%ld isCollapsed=%d rendered=%@", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0, self.renderedDockMode == .collapsedDockView ? "CollapsedDockView" : "ExpandedDockView")\n'
                       + first_frame_anchor)
    text = replace_once(text, first_frame_anchor, first_frame_log, "pre-frame state diagnostic")

    # End the logical session when its final app is removed. The dock may still
    # be completing its hide animation when the next app session starts.
    end_upstream = ('            if self.apps.isEmpty {\n'
                    '                self.hideDock()\n')
    end_guest = '        if self.apps.isEmpty { self.hideDock() }\n'
    if end_upstream in text:
        text = replace_once(
            text, end_upstream,
            f'            if self.apps.isEmpty {{\n'
            f'                // {SESSION_MARKER}: a later session re-reads the preference.\n'
            '                NSLog("[LC_DOCK] SESSION_END id=%@ apps_count=%ld isCollapsed=%d", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0)\n'
            '                self.collapseStartState.end()\n'
            '                self.hideDock()\n',
            "session end")
    elif end_guest in text:
        text = replace_once(
            text, end_guest,
            f'        if self.apps.isEmpty {{\n'
            f'            // {SESSION_MARKER}: a later session re-reads the preference.\n'
            '            NSLog("[LC_DOCK] SESSION_END id=%@ apps_count=%ld isCollapsed=%d", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0)\n'
            '            self.collapseStartState.end()\n'
            '            self.hideDock()\n'
            '        }\n',
            "session end (guest-patched shape)")
    else:
        die("session end: expected one anchor, found 0")

    hide_completion = '''            } completion: { _ in
                self.isVisible = false
                hostingController.view.transform = .identity
            }
'''
    hide_replacement = '''            } completion: { _ in
                self.isVisible = false
                hostingController.view.transform = .identity
                // MULTITASK_DOCK_RESHOW_AFTER_TRANSITION_V1: preserve a new session arriving during hide.
                if !self.apps.isEmpty { self.showDock() }
            }
'''
    text = replace_once(text, hide_completion, hide_replacement, "hide completion session transition")
    lines = text.splitlines(keepends=True)
    branch_indices = [index for index, line in enumerate(lines)
                      if line.strip() == "if dockManager.isCollapsed {"]
    if len(branch_indices) != 1:
        die("CollapsedDockView first-presented branch: expected one unique anchor")
    branch_index = branch_indices[0]
    lines[branch_index] = lines[branch_index].replace(
        "if dockManager.isCollapsed", "if dockManager.renderedDockMode == .collapsedDockView", 1)
    text = "".join(lines)
    return text


def apply_settings(text: str) -> str:
    """Add the Start Dock Collapsed row between Dock Width and Hide Collapsed Dock."""
    if KEY in text:
        if text.count(PROP_LINE) != 1 or text.count(TOGGLE_LINE) != 1:
            die("previous settings patch is partial or duplicated")
        return text
    old_props = '    @AppStorage("LCHideCollapsedDock", store: LCUtils.appGroupUserDefault) var hideCollapsedDock: Bool = false\n'
    new_props = old_props + PROP_LINE
    text = replace_once(text, old_props, new_props, "settings properties")
    old_toggle = ('                .padding(.vertical, 4)\n'
                  '                Toggle(isOn: $hideCollapsedDock) {\n'
                  '                    Text("lc.settings.hideCollapsedDock".loc)\n'
                  '                }\n'
                  '            }\n')
    new_toggle = ('                .padding(.vertical, 4)\n'
                  '                Toggle(isOn: $dockStartsCollapsed) {\n'
                  '                    Text("Start Dock Collapsed")\n'
                  '                }\n'
                  '                Toggle(isOn: $hideCollapsedDock) {\n'
                  '                    Text("lc.settings.hideCollapsedDock".loc)\n'
                  '                }\n'
                  '            } footer: {\n'
                   '                Text("Start Dock Collapsed applies when a fresh multitasking session first presents the dock; expanding it afterwards always wins, and rotation or layout updates never reset it. Hide Collapsed Dock only controls whether the already-collapsed dock stays visible.")\n'
                  '            }\n')
    return replace_once(text, old_toggle, new_toggle, "settings dock section")


def patch(live: Path) -> None:
    # Validate every anchor before writing anything.
    dock_path = live / DOCK_VIEW
    settings_path = live / SETTINGS_VIEW
    dock_text = dock_path.read_text(encoding="utf-8")
    settings_text = settings_path.read_text(encoding="utf-8")
    dock_new = apply_dock_init(dock_text)
    dock_new = apply_dock_session(dock_new)
    settings_new = apply_settings(settings_text)
    dock_path.write_text(dock_new, encoding="utf-8")
    settings_path.write_text(settings_new, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        die("usage: patch_multitask_dock.py <livecontainer-root>")
    live = Path(sys.argv[1]).resolve()
    if subprocess.check_output(["git", "-C", str(live), "rev-parse", "HEAD"], text=True).strip() != PIN:
        die("input revision does not match the pinned LiveContainer revision")
    patch(live)
    print("multitask dock start-collapsed patch applied and verified")


if __name__ == "__main__":
    main()
