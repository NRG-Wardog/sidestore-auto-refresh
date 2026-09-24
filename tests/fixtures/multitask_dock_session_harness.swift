@main
struct DockSessionHarness {
    static func main() {
        var state = LCMultitaskDockSessionState()

        // Preference OFF selects the actual expanded SwiftUI branch on the first frame.
        let first = state.begin(storedPreference: false)
        var collapsed = true // stale singleton value from a previous session
        guard let initial = state.applyBeforeFirstFrame(sessionID: first) else {
            preconditionFailure("fresh session did not provide an initial preference")
        }
        collapsed = initial
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
        let second = state.begin(storedPreference: true)
        precondition(second != first, "fresh session identity was reused")
        guard let nextInitial = state.applyBeforeFirstFrame(sessionID: second) else {
            preconditionFailure("reused manager did not apply the next session preference")
        }
        collapsed = nextInitial
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
        let third = state.begin(storedPreference: true)
        guard let thirdInitial = state.applyBeforeFirstFrame(sessionID: third) else {
            preconditionFailure("third session preference was not applied")
        }
        precondition(thirdInitial, "next fresh session did not start collapsed again")
        precondition(LCMultitaskDockSessionState.renderedMode(isCollapsed: thirdInitial) == .collapsedDockView,
                     "next fresh session did not present CollapsedDockView")
        state.end()
        let fourth = state.begin(storedPreference: false)
        guard let fourthInitial = state.applyBeforeFirstFrame(sessionID: fourth) else {
            preconditionFailure("preference OFF fresh session did not start")
        }
        precondition(!fourthInitial && LCMultitaskDockSessionState.renderedMode(isCollapsed: fourthInitial) == .expandedDockView,
                     "preference OFF fresh session did not restore expanded dock")
        print("DOCK_FIRST_PRESENTED_VIEW_BEHAVIOR_PASS")
    }
}
