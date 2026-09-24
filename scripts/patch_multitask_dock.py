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
TUCKED_KEY = "LCMultitaskDockStartsTuckedToEdge"
MARKER = "MULTITASK_DOCK_START_COLLAPSED_V3"
SESSION_MARKER = "MULTITASK_DOCK_SESSION_APPLY_V2"
RESHOW_MARKER = "MULTITASK_DOCK_RESHOW_AFTER_TRANSITION_V1"
RECOVERY_MARKER = "MULTITASK_DOCK_SESSION_RECOVERY_V1"
SETUP_PRESENT_MARKER = "MULTITASK_DOCK_SETUP_PRESENT_V1"
PREF_BEFORE_MOUNT_MARKER = "MULTITASK_DOCK_PREF_BEFORE_MOUNT_V1"
BODY_MARKER = "MULTITASK_DOCK_BODY_FIRST_RENDER_V1"
PRESENTATION_GATE_MARKER = "MULTITASK_DOCK_PRESENTATION_GATE_V1"
COLLAPSE_OBSERVER_MARKER = "MULTITASK_DOCK_COLLAPSE_OBSERVER_V1"
BODY_EVALUATION_MARKER = "MULTITASK_DOCK_BODY_EVALUATION_V1"

DOCK_VIEW = "MultitaskSupport/MultitaskDockView.swift"
SETTINGS_VIEW = "LiveContainerSwiftUI/Views/Settings/LCMultitaskSettingView.swift"
SESSION_HELPER = Path(__file__).with_name("templates") / "multitask_dock_session_state.swift"

PROP_LINE = f'    @AppStorage("{KEY}", store: LCUtils.appGroupUserDefault) var dockStartsCollapsed = false\n'
TUCKED_PROP_LINE = f'    @AppStorage("{TUCKED_KEY}", store: LCUtils.appGroupUserDefault) var dockStartsTuckedToEdge = false\n'
TOGGLE_LINE = '                Toggle(isOn: $dockStartsCollapsed) {\n'
TUCKED_TOGGLE_LINE = '                Toggle(isOn: $dockStartsTuckedToEdge) {\n'


def die(message: str) -> None:
    raise SystemExit(f"patch_multitask_dock: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def apply_dock_init(text: str) -> str:
    """Initialize the manager's first root from the same shared preference."""
    if MARKER in text:
        if text.count(MARKER) != 1:
            die("previous dock init patch is partial or duplicated")
        return text
    if ("MULTITASK_DOCK_START_COLLAPSED_V1" in text or
            "MULTITASK_DOCK_START_COLLAPSED_V2" in text):
        die("legacy dock initialization marker found; reapply to the clean pinned source")
    old = "    override init() {\n        super.init()\n"
    new = ("    override init() {\n"
           "        super.init()\n"
           f"        // {MARKER}: initialize before setupDockView can create its first SwiftUI root.\n"
           f'        let stored = LCUtils.appGroupUserDefault.bool(forKey: "{KEY}")\n'
           "        self.isCollapsed = stored\n"
           f'        NSLog("[LC_DOCK] INIT manager=%@ suite=%@ stored_preference=%d apps_count=%ld isCollapsed=%d", '
           'String(describing: ObjectIdentifier(self)), (LCSharedUtils.appGroupID() ?? "unavailable"), stored ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0)\n')
    return replace_once(text, old, new, "dock manager init")


def apply_dock_session(text: str) -> str:
    """Select CollapsedDockView before a fresh session's first SwiftUI mount."""
    if SESSION_MARKER in text:
        if (text.count(SESSION_MARKER) != 3 or text.count(RESHOW_MARKER) != 1 or
                text.count(RECOVERY_MARKER) != 1 or text.count(SETUP_PRESENT_MARKER) != 1 or
                text.count(PREF_BEFORE_MOUNT_MARKER) != 1 or text.count(BODY_MARKER) != 1 or
                text.count(PRESENTATION_GATE_MARKER) < 3 or
                text.count(COLLAPSE_OBSERVER_MARKER) != 1 or
                text.count(BODY_EVALUATION_MARKER) != 2):
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
        f'    // {PRESENTATION_GATE_MARKER}: do not expose a reused root branch before session preference is committed.\n'
        '    @Published private(set) var v3DockPresentationState = LCMultitaskDockPresentationState()\n'
        '    private var collapseStartState = LCMultitaskDockSessionState()\n'
        '    var renderedDockMode: LCMultitaskDockRenderedMode {\n'
        '        LCMultitaskDockSessionState.renderedMode(isCollapsed: isCollapsed)\n'
        '    }\n'
        '    private var firstRenderedDockSessionID: String?\n'
        '    private var firstBodyEvaluationSessionID: String?\n'
        '    private var firstPresentedBodySessionID: String?\n'
        '    private var didLogPreSessionBodyEvaluation = false\n'
        '    func v3RecordFirstDockBodyEvaluation() -> EmptyView {\n'
        '        guard let sessionID = v3DockPresentationState.sessionID else {\n'
        '            if !didLogPreSessionBodyEvaluation {\n'
        '                didLogPreSessionBodyEvaluation = true\n'
        f'                // {BODY_EVALUATION_MARKER}: records the real host-root body evaluation before any multitask session exists.\n'
        '                NSLog("[LC_DOCK] BODY_EVALUATION_FIRST manager=%@ session=none stored_preference=%d suite=%@ apps_count=%ld isCollapsed=%d branch=suppressed_no_session ready=0", String(describing: ObjectIdentifier(self)), LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed") ? 1 : 0, (LCSharedUtils.appGroupID() ?? "unavailable"), apps.count, isCollapsed ? 1 : 0)\n'
        '            }\n'
        '            return EmptyView()\n'
        '        }\n'
        '        if firstBodyEvaluationSessionID != sessionID {\n'
        '            firstBodyEvaluationSessionID = sessionID\n'
        f'            // {BODY_EVALUATION_MARKER}: emitted synchronously during the concrete host-root body evaluation.\n'
        '            NSLog("[LC_DOCK] BODY_EVALUATION_FIRST manager=%@ session=%@ stored_preference=%d stored_tucked=%d suite=%@ apps_count=%ld isCollapsed=%d isDockHidden=%d branch=%@ ready=%d", String(describing: ObjectIdentifier(self)), sessionID, LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed") ? 1 : 0, LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsTuckedToEdge") ? 1 : 0, (LCSharedUtils.appGroupID() ?? "unavailable"), apps.count, isCollapsed ? 1 : 0, isDockHidden ? 1 : 0, v3DockPresentationState.isReady ? (isCollapsed ? "CollapsedDockView" : "ExpandedDockView") : "suppressed_waiting_for_session", v3DockPresentationState.isReady ? 1 : 0)\n'
        '        }\n'
        '        if v3DockPresentationState.isReady, firstPresentedBodySessionID != sessionID,\n'
        '           let mode = v3DockPresentationState.recordFirstBodyEvaluation(sessionID: sessionID, isCollapsed: isCollapsed) {\n'
        '            firstPresentedBodySessionID = sessionID\n'
        '            NSLog("[LC_DOCK] BODY_BRANCH_SELECTED_FIRST manager=%@ session=%@ apps_count=%ld isCollapsed=%d branch=%@", String(describing: ObjectIdentifier(self)), sessionID, apps.count, isCollapsed ? 1 : 0, mode == .collapsedDockView ? "CollapsedDockView" : "ExpandedDockView")\n'
        '        }\n'
        '        return EmptyView()\n'
        '    }\n'
        '    func v3RecordFirstRenderedDockView(_ mode: LCMultitaskDockRenderedMode) {\n'
        '        guard let sessionID = collapseStartState.sessionID, firstRenderedDockSessionID != sessionID else { return }\n'
        '        firstRenderedDockSessionID = sessionID\n'
        f'        // {BODY_MARKER}: emitted from the concrete SwiftUI branch on first appearance.\n'
        '        NSLog("[LC_DOCK] BODY_FIRST_RENDER manager=%@ session=%@ apps_count=%ld isCollapsed=%d branch=%@", String(describing: ObjectIdentifier(self)), sessionID, apps.count, isCollapsed ? 1 : 0, mode == .collapsedDockView ? "CollapsedDockView" : "ExpandedDockView")\n'
        '    }\n',
        "session state")
    text = replace_once(
        text,
        '        self.isCollapsed = stored\n',
        '        self.isCollapsed = stored\n'
        f'        // {COLLAPSE_OBSERVER_MARKER}: capture every later mutation, including reset/reuse paths.\n'
        '        self.v3CollapseObserver = self.$isCollapsed.dropFirst().sink { [weak self] value in\n'
        '            guard let self else { return }\n'
        '            NSLog("[LC_DOCK] IS_COLLAPSED_PUBLISHED manager=%@ session=%@ stored_preference=%d suite=%@ apps_count=%ld value=%d ready=%d", String(describing: ObjectIdentifier(self)), self.collapseStartState.sessionID ?? "none", LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed") ? 1 : 0, (LCSharedUtils.appGroupID() ?? "unavailable"), self.apps.count, value ? 1 : 0, self.v3DockPresentationState.isReady ? 1 : 0)\n'
        '        }\n',
        "collapse mutation observer")
    text = replace_once(
        text,
        '    @Published @objc var isCollapsed: Bool = false\n',
        '    @Published @objc var isCollapsed: Bool = false\n'
        '    private var v3CollapseObserver: AnyCancellable?\n',
        "collapse mutation observer storage")
    setup_call = ('        keyWindow!.rootViewController!.view.subviews.first!.addSubview(self.windowHostingView)\n'
                  '        setupDockView()\n')
    setup_call_replacement = ('        keyWindow!.rootViewController!.view.subviews.first!.addSubview(self.windowHostingView)\n'
        '        NSLog("[LC_DOCK] BEFORE_SETUP_DOCK_VIEW manager=%@ suite=%@ stored_preference=%d apps_count=%ld isCollapsed=%d session=%@", String(describing: ObjectIdentifier(self)), (LCSharedUtils.appGroupID() ?? "unavailable"), LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed") ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.collapseStartState.sessionID ?? "none")\n'
        '        setupDockView()\n')
    text = replace_once(text, setup_call, setup_call_replacement, "pre-setup session diagnostic")
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
        '                NSLog("[LC_DOCK] STALE_SESSION_RESET manager=%@ id=%@ apps_count=%ld isCollapsed=%d", String(describing: ObjectIdentifier(self)), self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0)\n'
        '                self.apps = [appModel]\n'
        '                self.v3DockPresentationState.end(sessionID: self.collapseStartState.sessionID)\n'
        '                self.collapseStartState.end()\n'
        '            }\n'
        f'            if !self.collapseStartState.isActiveSession {{\n'
        f'                // {SESSION_MARKER}: snapshot the preference before the first view is selected.\n'
        f'                let stored = LCUtils.appGroupUserDefault.bool(forKey: "{KEY}")\n'
        f'                let storedTucked = LCUtils.appGroupUserDefault.bool(forKey: "{TUCKED_KEY}")\n'
        '                let sessionID = self.collapseStartState.begin(storedPreference: stored, storedTuckedPreference: storedTucked)\n'
        '                self.v3DockPresentationState.begin(sessionID: sessionID)\n'
        '                NSLog("[LC_DOCK] SESSION_BEGIN manager=%@ id=%@ suite=%@ stored_preference=%d stored_tucked=%d apps_count=%ld isCollapsed_before_setup=%d isDockHidden_before_setup=%d", String(describing: ObjectIdentifier(self)), sessionID, (LCSharedUtils.appGroupID() ?? "unavailable"), stored ? 1 : 0, storedTucked ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.isDockHidden ? 1 : 0)\n'
        '                if let initial = self.collapseStartState.applyBeforeFirstFrame(sessionID: sessionID) { self.isCollapsed = initial }\n'
        '                if let initialHidden = self.collapseStartState.applyTuckedBeforeFirstFrame(sessionID: sessionID) { self.isDockHidden = initialHidden }\n'
        '                if let hostingController = self.hostingController {\n'
        '                    hostingController.rootView = AnyView(MultitaskDockSwiftView().environmentObject(self).id(sessionID))\n'
        '                }\n'
        f'                // {PRESENTATION_GATE_MARKER}: keep the reused host root blank until showDock commits the first branch.\n'
        '                NSLog("[LC_DOCK] SESSION_PREPARED manager=%@ session=%@ host=%@ stored_preference=%d stored_tucked=%d apps_count=%ld isCollapsed=%d isDockHidden=%d ready=%d", String(describing: ObjectIdentifier(self)), sessionID, self.hostingController.map { String(describing: ObjectIdentifier($0)) } ?? "not_created", stored ? 1 : 0, storedTucked ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.isDockHidden ? 1 : 0, self.v3DockPresentationState.isReady ? 1 : 0)\n'
        '                self.showDock()\n',
        "fresh session first presentation")

    show_anchor = '''        DispatchQueue.main.async {
            self.isVisible = true
'''
    show_replacement = '''        DispatchQueue.main.async {
            NSLog("[LC_DOCK] SHOW_BLOCK_ENTER manager=%@ session=%@ host=%@ suite=%@ stored_preference=%d apps_count=%ld isCollapsed=%d ready=%d", String(describing: ObjectIdentifier(self)), self.collapseStartState.sessionID ?? "none", String(describing: ObjectIdentifier(hostingController)), (LCSharedUtils.appGroupID() ?? "unavailable"), LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed") ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.v3DockPresentationState.isReady ? 1 : 0)
            self.isVisible = true
'''
    text = replace_once(text, show_anchor, show_replacement, "showDock first-frame diagnostic")
    show_call_anchor = '    @objc public func showDock() {\n'
    show_call_replacement = ('    @objc public func showDock() {\n'
        '        NSLog("[LC_DOCK] BEFORE_SHOW_BLOCK manager=%@ session=%@ stored_preference=%d apps_count=%ld isCollapsed=%d ready=%d", String(describing: ObjectIdentifier(self)), self.collapseStartState.sessionID ?? "none", LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed") ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.v3DockPresentationState.isReady ? 1 : 0)\n')
    text = replace_once(text, show_call_anchor, show_call_replacement, "showDock call sequence diagnostic")

    setup_func_anchor = '    private func setupDockView() {\n'
    setup_func_replacement = ('    private func setupDockView() {\n'
        '        NSLog("[LC_DOCK] SETUP_DOCK_VIEW manager=%@ suite=%@ stored_preference=%d apps_count=%ld isCollapsed=%d session=%@", String(describing: ObjectIdentifier(self)), (LCSharedUtils.appGroupID() ?? "unavailable"), LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed") ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.collapseStartState.sessionID ?? "none")\n')
    text = replace_once(text, setup_func_anchor, setup_func_replacement, "setupDockView entry diagnostic")
    setup_anchor = '            let dockView = AnyView(MultitaskDockSwiftView()\n'
    setup_replacement = ('            NSLog("[LC_DOCK] SETUP_ROOT_CREATE manager=%@ suite=%@ stored_preference=%d session=%@ apps_count=%ld isCollapsed=%d ready=%d", String(describing: ObjectIdentifier(self)), (LCSharedUtils.appGroupID() ?? "unavailable"), LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed") ? 1 : 0, self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0, self.v3DockPresentationState.isReady ? 1 : 0)\n'
                         + setup_anchor)
    text = replace_once(text, setup_anchor, setup_replacement, "hosting view diagnostic")
    host_ready = '            self.hostingController?.view.backgroundColor = .clear\n'
    host_ready_replacement = (host_ready +
        '            NSLog("[LC_DOCK] HOSTING_ROOT_CREATED manager=%@ host=%@ suite=%@ stored_preference=%d session=%@ apps_count=%ld isCollapsed=%d ready=%d", String(describing: ObjectIdentifier(self)), self.hostingController.map { String(describing: ObjectIdentifier($0)) } ?? "none", (LCSharedUtils.appGroupID() ?? "unavailable"), LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed") ? 1 : 0, self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0, self.v3DockPresentationState.isReady ? 1 : 0)\n'
        f'            // {SETUP_PRESENT_MARKER}: the app-add queue can beat host-controller creation.\n'
        '            if !self.apps.isEmpty { self.showDock() }\n')
    text = replace_once(text, host_ready, host_ready_replacement, "late hosting-controller presentation")
    actual_presentation = '''            NSLog("[LC_DOCK] SHOW_BLOCK_ENTER manager=%@ session=%@ host=%@ suite=%@ stored_preference=%d apps_count=%ld isCollapsed=%d ready=%d", String(describing: ObjectIdentifier(self)), self.collapseStartState.sessionID ?? "none", String(describing: ObjectIdentifier(hostingController)), (LCSharedUtils.appGroupID() ?? "unavailable"), LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed") ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.v3DockPresentationState.isReady ? 1 : 0)
            self.isVisible = true
'''
    actual_presentation_replacement = f'''            // {PREF_BEFORE_MOUNT_MARKER}: re-read/apply before the host's first visible mount.
            NSLog("[LC_DOCK] SHOW_BLOCK_ENTER manager=%@ session=%@ host=%@ suite=%@ stored_preference=%d stored_tucked=%d apps_count=%ld isCollapsed=%d isDockHidden=%d ready=%d", String(describing: ObjectIdentifier(self)), self.collapseStartState.sessionID ?? "none", String(describing: ObjectIdentifier(hostingController)), (LCSharedUtils.appGroupID() ?? "unavailable"), LCUtils.appGroupUserDefault.bool(forKey: "{KEY}") ? 1 : 0, LCUtils.appGroupUserDefault.bool(forKey: "{TUCKED_KEY}") ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.isDockHidden ? 1 : 0, self.v3DockPresentationState.isReady ? 1 : 0)
            if !self.collapseStartState.isActiveSession {{
                let stored = LCUtils.appGroupUserDefault.bool(forKey: "{KEY}")
                let storedTucked = LCUtils.appGroupUserDefault.bool(forKey: "{TUCKED_KEY}")
                let sessionID = self.collapseStartState.begin(storedPreference: stored, storedTuckedPreference: storedTucked)
                self.v3DockPresentationState.begin(sessionID: sessionID)
                NSLog("[LC_DOCK] SESSION_BEGIN_IN_SHOW manager=%@ session=%@ suite=%@ stored_preference=%d stored_tucked=%d apps_count=%ld isCollapsed=%d isDockHidden=%d", String(describing: ObjectIdentifier(self)), sessionID, (LCSharedUtils.appGroupID() ?? "unavailable"), stored ? 1 : 0, storedTucked ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.isDockHidden ? 1 : 0)
                if let initialHidden = self.collapseStartState.applyTuckedBeforeFirstFrame(sessionID: sessionID) {{ self.isDockHidden = initialHidden }}
            }}
            if let sessionID = self.collapseStartState.sessionID, !self.collapseStartState.wasPresented {{
                if let initial = self.collapseStartState.applyBeforeFirstFrame(sessionID: sessionID) {{ self.isCollapsed = initial }}
                if let initialHidden = self.collapseStartState.applyTuckedBeforeFirstFrame(sessionID: sessionID) {{ self.isDockHidden = initialHidden }}
                let firstMode = self.v3DockPresentationState.markReady(sessionID: sessionID, isCollapsed: self.isCollapsed, isDockHidden: self.isDockHidden)
                NSLog("[LC_DOCK] FIRST_PRESENTED_VIEW manager=%@ session=%@ first=%d stored_preference=%d stored_tucked=%d apps_count=%ld isCollapsed=%d isDockHidden=%d branch=%@", String(describing: ObjectIdentifier(self)), sessionID, firstMode == nil ? 0 : 1, LCUtils.appGroupUserDefault.bool(forKey: "{KEY}") ? 1 : 0, LCUtils.appGroupUserDefault.bool(forKey: "{TUCKED_KEY}") ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.isDockHidden ? 1 : 0, firstMode == .collapsedDockView ? "CollapsedDockView" : "ExpandedDockView")
                // {PRESENTATION_GATE_MARKER}: the blank branch stays selected until the preference has been committed.
                hostingController.rootView = AnyView(MultitaskDockSwiftView().environmentObject(self).id(sessionID))
                NSLog("[LC_DOCK] ROOT_REUSED_FOR_SESSION manager=%@ host=%@ session=%@ suite=%@ stored_preference=%d apps_count=%ld isCollapsed=%d ready=%d", String(describing: ObjectIdentifier(self)), String(describing: ObjectIdentifier(hostingController)), sessionID, (LCSharedUtils.appGroupID() ?? "unavailable"), LCUtils.appGroupUserDefault.bool(forKey: "{KEY}") ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.v3DockPresentationState.isReady ? 1 : 0)
                let firstPresentation = self.collapseStartState.markPresented(sessionID: sessionID)
                NSLog("[LC_DOCK] FIRST_FRAME_ARMED manager=%@ session=%@ first=%d ready=%d isCollapsed=%d", String(describing: ObjectIdentifier(self)), sessionID, firstPresentation ? 1 : 0, self.v3DockPresentationState.isReady ? 1 : 0, self.isCollapsed ? 1 : 0)
            }}
            NSLog("[LC_DOCK] SHOW_BLOCK_EXECUTED manager=%@ session=%@ host=%@ suite=%@ stored_preference=%d stored_tucked=%d apps_count=%ld isCollapsed=%d isDockHidden=%d branch=%@ ready=%d", String(describing: ObjectIdentifier(self)), self.collapseStartState.sessionID ?? "none", String(describing: ObjectIdentifier(hostingController)), (LCSharedUtils.appGroupID() ?? "unavailable"), LCUtils.appGroupUserDefault.bool(forKey: "{KEY}") ? 1 : 0, LCUtils.appGroupUserDefault.bool(forKey: "{TUCKED_KEY}") ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.isDockHidden ? 1 : 0, self.renderedDockMode == .collapsedDockView ? "CollapsedDockView" : "ExpandedDockView", self.v3DockPresentationState.isReady ? 1 : 0)
            self.isVisible = true
'''
    text = replace_once(text, actual_presentation, actual_presentation_replacement,
                        "actual first-presented-view marker")
    first_frame_anchor = "            self.updateDockFrame(animated: false)"
    first_frame_log = ('            NSLog("[LC_DOCK] BEFORE_FIRST_FRAME manager=%@ session=%@ suite=%@ stored_preference=%d stored_tucked=%d apps_count=%ld isCollapsed=%d isDockHidden=%d branch=%@ ready=%d", String(describing: ObjectIdentifier(self)), self.collapseStartState.sessionID ?? "none", (LCSharedUtils.appGroupID() ?? "unavailable"), LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsCollapsed") ? 1 : 0, LCUtils.appGroupUserDefault.bool(forKey: "LCMultitaskDockStartsTuckedToEdge") ? 1 : 0, self.apps.count, self.isCollapsed ? 1 : 0, self.isDockHidden ? 1 : 0, self.renderedDockMode == .collapsedDockView ? "CollapsedDockView" : "ExpandedDockView", self.v3DockPresentationState.isReady ? 1 : 0)\n'
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
            '                NSLog("[LC_DOCK] SESSION_END manager=%@ id=%@ apps_count=%ld isCollapsed=%d", String(describing: ObjectIdentifier(self)), self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0)\n'
            '                self.v3DockPresentationState.end(sessionID: self.collapseStartState.sessionID)\n'
            '                self.collapseStartState.end()\n'
            '                self.hideDock()\n',
            "session end")
    elif end_guest in text:
        text = replace_once(
            text, end_guest,
            f'        if self.apps.isEmpty {{\n'
            f'            // {SESSION_MARKER}: a later session re-reads the preference.\n'
            '            NSLog("[LC_DOCK] SESSION_END manager=%@ id=%@ apps_count=%ld isCollapsed=%d", String(describing: ObjectIdentifier(self)), self.collapseStartState.sessionID ?? "none", self.apps.count, self.isCollapsed ? 1 : 0)\n'
            '            self.v3DockPresentationState.end(sessionID: self.collapseStartState.sessionID)\n'
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
    # The existing direct state check is the acceptance branch. renderedDockMode
    # stays diagnostic and cannot replace MultitaskDockManager.isCollapsed.
    text = "".join(lines)
    text = replace_once(
        text,
        '                if dockManager.isCollapsed {\n',
        '                dockManager.v3RecordFirstDockBodyEvaluation()\n'
        f'                // {PRESENTATION_GATE_MARKER}: no expanded/collapsed branch is exposed before session readiness.\n'
        '                if dockManager.v3DockPresentationState.isReady {\n'
        '                    if dockManager.isCollapsed {\n',
        "gate the first SwiftUI branch")
    text = replace_once(
        text,
        '                    CollapsedDockView(isHidden: dockManager.isDockHidden)\n',
        '                    CollapsedDockView(isHidden: dockManager.isDockHidden)\n'
        '                        .onAppear { dockManager.v3RecordFirstRenderedDockView(.collapsedDockView) }\n',
        "collapsed body first-render marker")
    text = replace_once(
        text,
        '                        ForEach(dockManager.apps) { app in\n'
        '                            AppIconView(app: app)\n'
        '                        }\n'
        '                    }\n'
        '                }\n',
        '                        ForEach(dockManager.apps) { app in\n'
        '                            AppIconView(app: app)\n'
        '                        }\n'
        '                    }\n'
        '                    .onAppear { dockManager.v3RecordFirstRenderedDockView(.expandedDockView) }\n'
        '                }\n'
        '                } else {\n'
        '                    Color.clear\n'
        '                }\n',
        "expanded body first-render marker")
    return text


def apply_settings(text: str) -> str:
    """Add independent initial collapsed and tucked-to-edge dock preferences."""
    if KEY in text:
        if (text.count(PROP_LINE) != 1 or text.count(TOGGLE_LINE) != 1 or
                text.count(TUCKED_PROP_LINE) != 1 or text.count(TUCKED_TOGGLE_LINE) != 1):
            die("previous settings patch is partial or duplicated")
        return text
    old_props = '    @AppStorage("LCHideCollapsedDock", store: LCUtils.appGroupUserDefault) var hideCollapsedDock: Bool = false\n'
    new_props = old_props + PROP_LINE + TUCKED_PROP_LINE
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
                  '                Toggle(isOn: $dockStartsTuckedToEdge) {\n'
                  '                    Text("Start Dock Tucked To Edge")\n'
                  '                }\n'
                  '                Toggle(isOn: $hideCollapsedDock) {\n'
                  '                    Text("lc.settings.hideCollapsedDock".loc)\n'
                  '                }\n'
                  '            } footer: {\n'
                   '                Text("Start Dock Collapsed chooses the first expanded or collapsed dock view. Start Dock Tucked To Edge begins that fresh session in the existing hidden-to-side state; the edge control can bring it back. Both choices apply once per fresh session, so your later dock actions remain in effect through rotation. Hide Collapsed Dock controls visibility of an already-tucked collapsed dock.")\n'
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
