@main
struct DockSessionHarness {
    static func main() {
        var state = LCMultitaskDockSessionState()

        // Preference OFF must be applied before the first presented frame.
        let first = state.begin(storedPreference: false)
        var collapsed = true // stale singleton value from a previous session
        guard let initial = state.applyBeforeFirstFrame(sessionID: first) else {
            preconditionFailure("fresh session did not provide an initial preference")
        }
        collapsed = initial
        precondition(!collapsed, "preference OFF did not start expanded")
        precondition(state.applyBeforeFirstFrame(sessionID: first) == nil,
                     "layout/rotation reapplied the preference")

        // A manual toggle after presentation wins for this session.
        state.userDidToggle()
        collapsed.toggle()
        precondition(collapsed, "manual toggle did not expand the dock")
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
        precondition(collapsed, "preference ON did not start collapsed")
        precondition(state.applyBeforeFirstFrame(sessionID: first) == nil,
                     "late first-frame work from the old session was accepted")

        // A later fresh session reads the newly persisted OFF preference.
        state.end()
        let third = state.begin(storedPreference: false)
        guard let thirdInitial = state.applyBeforeFirstFrame(sessionID: third) else {
            preconditionFailure("third session preference was not applied")
        }
        precondition(!thirdInitial, "next fresh session did not apply the updated preference")
        print("DOCK_SESSION_BEHAVIOR_PASS")
    }
}
