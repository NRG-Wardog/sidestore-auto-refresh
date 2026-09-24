@main
struct DockSessionHarness {
    static func main() {
        var state = LCMultitaskDockSessionState()
        var presentation = LCMultitaskDockPresentationState()

        // iOS may evaluate a reused hosting root before addRunningApp's queued
        // session work executes. That cached expanded branch is the device-only
        // case the former state-only harness skipped.
        let reusedRootFirstBody = LCMultitaskDockSessionState.renderedMode(isCollapsed: false)
        precondition(reusedRootFirstBody == .expandedDockView)

        // Preference OFF selects the actual expanded SwiftUI branch on the first frame.
        let first = state.begin(storedPreference: false)
        presentation.begin(sessionID: first)
        var collapsed = true // stale singleton value from a previous session
        guard let initial = state.applyBeforeFirstFrame(sessionID: first) else {
            preconditionFailure("fresh session did not provide an initial preference")
        }
        collapsed = initial
        precondition(!presentation.isReady,
                     "a reused host exposed a dock branch before the fresh-session preference was committed")
        precondition(presentation.markReady(sessionID: first, isCollapsed: collapsed) == .expandedDockView)
        precondition(state.markPresented(sessionID: first), "first session presentation was not recorded")
        precondition(!state.markPresented(sessionID: first), "first presentation was not one-shot")
        precondition(!collapsed, "preference OFF did not start expanded")
        precondition(LCMultitaskDockSessionState.renderedMode(isCollapsed: collapsed) == .expandedDockView,
                     "preference OFF did not select the expanded view on first presentation")
        precondition(state.applyBeforeFirstFrame(sessionID: first) == nil,
                     "layout/rotation reapplied the preference")

        // A manual toggle after presentation wins for this session.
        state.userDidToggle()
        collapsed.toggle()
        precondition(collapsed, "manual collapse did not change the current session")
        precondition(state.applyBeforeFirstFrame(sessionID: first) == nil,
                     "manual override was overwritten")

        // The singleton is reused; removing the last app ends the session.
        state.end()
        presentation.end(sessionID: first)
        let second = state.begin(storedPreference: true)
        presentation.begin(sessionID: second)
        precondition(second != first, "fresh session identity was reused")
        guard let nextInitial = state.applyBeforeFirstFrame(sessionID: second) else {
            preconditionFailure("reused manager did not apply the next session preference")
        }
        collapsed = nextInitial
        // The old expanded root remains cached until the state is ready. It is
        // never selected for this fresh session's first visible body.
        precondition(!presentation.isReady)
        precondition(presentation.markReady(sessionID: second, isCollapsed: collapsed) == .collapsedDockView,
                     "first presented SwiftUI state did not select CollapsedDockView")
        precondition(presentation.recordFirstBodyEvaluation(sessionID: second, isCollapsed: collapsed) == .collapsedDockView,
                     "first SwiftUI body evaluation did not select CollapsedDockView")
        precondition(state.markPresented(sessionID: second), "collapsed session did not record its first presentation")
        precondition(collapsed, "preference ON did not start collapsed")
        precondition(LCMultitaskDockSessionState.renderedMode(isCollapsed: collapsed) == .collapsedDockView,
                     "preference ON did not select CollapsedDockView on first presentation")
        state.userDidToggle()
        collapsed.toggle()
        precondition(!collapsed, "manual expand did not keep the current session expanded")
        precondition(LCMultitaskDockSessionState.renderedMode(isCollapsed: collapsed) == .expandedDockView,
                     "manual expansion did not select the normal dock")
        precondition(state.applyBeforeFirstFrame(sessionID: second) == nil,
                     "rotation/layout collapsed the manually expanded dock")
        precondition(state.applyBeforeFirstFrame(sessionID: first) == nil,
                     "late first-frame work from the old session was accepted")

        // The next fresh session reads the preference again, even after a manual expand.
        state.end()
        presentation.end(sessionID: second)
        let third = state.begin(storedPreference: true)
        presentation.begin(sessionID: third)
        guard let thirdInitial = state.applyBeforeFirstFrame(sessionID: third) else {
            preconditionFailure("third session preference was not applied")
        }
        precondition(thirdInitial, "next fresh session did not start collapsed again")
        precondition(presentation.markReady(sessionID: third, isCollapsed: thirdInitial) == .collapsedDockView)
        precondition(LCMultitaskDockSessionState.renderedMode(isCollapsed: thirdInitial) == .collapsedDockView,
                     "next fresh session did not present CollapsedDockView")
        state.end()
        presentation.end(sessionID: third)
        let fourth = state.begin(storedPreference: false)
        presentation.begin(sessionID: fourth)
        guard let fourthInitial = state.applyBeforeFirstFrame(sessionID: fourth) else {
            preconditionFailure("preference OFF fresh session did not start")
        }
        precondition(!fourthInitial && LCMultitaskDockSessionState.renderedMode(isCollapsed: fourthInitial) == .expandedDockView,
                     "preference OFF fresh session did not restore expanded dock")
        precondition(presentation.markReady(sessionID: fourth, isCollapsed: fourthInitial) == .expandedDockView)
        print("DOCK_FIRST_PRESENTED_VIEW_BEHAVIOR_PASS")
    }
}
