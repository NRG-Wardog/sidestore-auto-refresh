#!/usr/bin/env python3
"""Persistent 'Start Dock Collapsed' preference for the Multitask Dock.

Separate from LCGuestReturnStartsCollapsed (guest Return control) and from
LCHideCollapsedDock (which only hides an already-collapsed dock). The
preference is applied on the show queue immediately before the first frame of
each fresh multitasking session; manual expand/collapse afterwards wins, and
layout/rotation never reapplies it.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

PIN = "12377cf3b91d51739a33f14a302e5f522b238593"
KEY = "LCMultitaskDockStartsCollapsed"
MARKER = "MULTITASK_DOCK_START_COLLAPSED_V1"
SESSION_MARKER = "MULTITASK_DOCK_SESSION_APPLY_V1"

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
    """Apply persisted state at the final showDock first-frame boundary."""
    if SESSION_MARKER in text:
        if text.count(SESSION_MARKER) != 4:
            die("previous dock session patch is partial or duplicated")
        return text
    if "collapseManuallyOverridden" in text:
        die("unexpected pre-existing session state")
    helper = SESSION_HELPER.read_text(encoding="utf-8")
    if "import Combine\n" not in text:
        die("dock imports changed")
    text = replace_once(text, "import Combine\n", "import Combine\n\n" + helper + "\n", "session state helper")
    text = replace_once(
        text,
        '    @Published var settingsChanged: Bool = false\n',
        '    @Published var settingsChanged: Bool = false\n'
        f'    // {SESSION_MARKER}: one session identity survives singleton reuse.\n'
        '    private var collapseStartState = LCMultitaskDockSessionState()\n'
        '    private var pendingCollapseSessionID: String?\n',
        "session state")
    if text.count("            self.isCollapsed.toggle()\n") != 1:
        die("toggle anchor is not unique")
    text = text.replace(
        "            self.isCollapsed.toggle()\n",
        f"            self.collapseStartState.userDidToggle()\n"
        '            NSLog("[LC_DOCK] MANUAL_TOGGLE session=%@ apps_count=%ld collapsed_before=%d", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0)\n'
        "            self.isCollapsed.toggle()\n",
        1)
    text = replace_once(
        text,
        '            if self.apps.count == 1 {\n                self.showDock()\n',
        f'            if self.apps.count == 1 {{\n'
        f'                // {SESSION_MARKER}: snapshot the preference for this fresh app session.\n'
        f'                let stored = LCUtils.appGroupUserDefault.bool(forKey: "{KEY}")\n'
        '                let sessionID = self.collapseStartState.begin(storedPreference: stored)\n'
        '                self.pendingCollapseSessionID = sessionID\n'
        '                NSLog("[LC_DOCK] SESSION_BEGIN id=%@ stored_preference=%d apps_count=%ld collapsed_before_show=%d", sessionID, stored ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0)\n'
        '                self.showDock()\n',
        "session start")

    show_anchor = '''        DispatchQueue.main.async {
            self.isVisible = true
'''
    show_replacement = f'''        DispatchQueue.main.async {{
            // {SESSION_MARKER}: apply the fresh-session value on the actual show queue,
            // after setupDockView and immediately before the first frame calculation.
            if let sessionID = self.pendingCollapseSessionID {{
                let stored = self.collapseStartState.preference(for: sessionID) ?? false
                NSLog("[LC_DOCK] SHOW session=%@ stored_preference=%d apps_count=%ld isCollapsed_inside_show=%d", sessionID, stored ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0)
                if let initial = self.collapseStartState.applyBeforeFirstFrame(sessionID: sessionID) {{
                    self.isCollapsed = initial
                }}
                self.pendingCollapseSessionID = nil
            }}
            self.isVisible = true
'''
    text = replace_once(text, show_anchor, show_replacement, "showDock first-frame preference")
    text = replace_once(
        text,
        "            self.updateDockFrame(animated: false)",
        '            NSLog("[LC_DOCK] BEFORE_FIRST_FRAME session=%@ apps_count=%ld isCollapsed=%d", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0)\n'
        "            self.updateDockFrame(animated: false)",
        "pre-frame diagnostic")

    setup_anchor = '            let dockView = AnyView(MultitaskDockSwiftView()\n'
    setup_replacement = ('            NSLog("[LC_DOCK] SETUP_VIEW apps_count=%ld isCollapsed=%d", self.apps.count, self.isCollapsed ? 1 : 0)\n'
                         + setup_anchor)
    text = replace_once(text, setup_anchor, setup_replacement, "hosting view diagnostic")
    # Session end: two shapes exist. patch_guest_return.py (which runs first)
    # replaces removeRunningApp wholesale, collapsing the empty branch to a
    # single line. Handle both; anything else is anchor drift.
    end_upstream = ('            if self.apps.isEmpty {\n'
                    '                self.hideDock()\n')
    end_guest = '        if self.apps.isEmpty { self.hideDock() }\n'
    if end_upstream in text:
        text = replace_once(
            text,
            end_upstream,
            f'            if self.apps.isEmpty {{\n'
            f'                // {SESSION_MARKER}: session over; the next session\n'
            '                // re-reads the persisted preference.\n'
            '                NSLog("[LC_DOCK] SESSION_END id=%@ apps_count=%ld isCollapsed=%d", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0)\n'
            '                self.collapseStartState.end()\n'
            '                self.pendingCollapseSessionID = nil\n'
            '                self.hideDock()\n',
            "session end")
    elif end_guest in text:
        text = replace_once(
            text,
            end_guest,
            f'        if self.apps.isEmpty {{\n'
            f'            // {SESSION_MARKER}: session over; the next session\n'
            '            // re-reads the persisted preference.\n'
            '            NSLog("[LC_DOCK] SESSION_END id=%@ apps_count=%ld isCollapsed=%d", self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0)\n'
            '            self.collapseStartState.end()\n'
            '            self.pendingCollapseSessionID = nil\n'
            '            self.hideDock()\n'
            '        }\n',
            "session end (guest-patched shape)")
    else:
        die("session end: expected one anchor, found 0")
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
