#!/usr/bin/env python3
"""Persistent 'Start Dock Collapsed' preference for the Multitask Dock.

Separate from LCGuestReturnStartsCollapsed (guest Return control) and from
LCHideCollapsedDock (which only hides an already-collapsed dock). The
preference is applied once when a new multitask dock is created; manual
expand/collapse afterwards always wins and layout/rotation never resets it.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

PIN = "12377cf3b91d51739a33f14a302e5f522b238593"
KEY = "LCMultitaskDockStartsCollapsed"
MARKER = "MULTITASK_DOCK_START_COLLAPSED_V1"

DOCK_VIEW = "MultitaskSupport/MultitaskDockView.swift"
SETTINGS_VIEW = "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift"

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
    """Apply the persisted start-collapsed default once at dock creation."""
    if KEY in text:
        if text.count(MARKER) != 1 or text.count(KEY) != 1:
            die("previous dock init patch is partial or duplicated")
        return text
    old = "    override init() {\n        super.init()\n"
    new = ("    override init() {\n"
           "        super.init()\n"
           f"        // {MARKER}: apply the persisted start-collapsed\n"
           "        // preference once at creation. Never re-applied on layout, rotation or\n"
           "        // activation, so manual expand/collapse always wins afterwards.\n"
           f'        isCollapsed = LCUtils.appGroupUserDefault.bool(forKey: "{KEY}")\n')
    return replace_once(text, old, new, "dock manager init")


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
                  '                Text("Start Dock Collapsed applies once when a new multitask dock is created; expanding it afterwards always wins, and rotation or layout updates never reset it. Hide Collapsed Dock only controls whether the already-collapsed dock stays visible.")\n'
                  '            }\n')
    return replace_once(text, old_toggle, new_toggle, "settings dock section")


def patch(live: Path) -> None:
    # Validate every anchor before writing anything.
    dock_path = live / DOCK_VIEW
    settings_path = live / SETTINGS_VIEW
    dock_text = dock_path.read_text(encoding="utf-8")
    settings_text = settings_path.read_text(encoding="utf-8")
    dock_new = apply_dock_init(dock_text)
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
