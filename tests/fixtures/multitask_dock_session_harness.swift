@main
struct DockSessionHarness {
    static func main() {
        var state = LCMultitaskDockSessionState()
        var presentation = LCMultitaskDockPresentationState()

        // A reused host may still have evaluated the previous session's root.
        // Until the new session commits its mode, no old branch is presented.
        let staleRootMode = LCMultitaskDockSessionState.renderedMode(isCollapsed: false)
        precondition(staleRootMode == .expandedDockView)

        // Both preferences OFF: fresh session presents the normal expanded,
        // visible dock on its first logical body evaluation.
        let first = state.begin(storedPreference: false, storedTuckedPreference: false)
        presentation.begin(sessionID: first)
        var collapsed = true
        var tucked = true
        collapsed = state.applyBeforeFirstFrame(sessionID: first)!
        tucked = state.applyTuckedBeforeFirstFrame(sessionID: first)!
        precondition(!presentation.isReady)
        precondition(presentation.markReady(sessionID: first, isCollapsed: collapsed,
                                             isDockHidden: tucked) == .expandedDockView)
        precondition(presentation.firstPresentedHiddenState == false)
        precondition(state.markPresented(sessionID: first))
        precondition(!collapsed && !tucked)
        precondition(state.applyBeforeFirstFrame(sessionID: first) == nil)
        precondition(state.applyTuckedBeforeFirstFrame(sessionID: first) == nil)

        // Start Dock Collapsed ON, Tucked To Edge OFF selects CollapsedDockView
        // without tucking it. A manual expansion then remains authoritative.
        state.end()
        presentation.end(sessionID: first)
        let second = state.begin(storedPreference: true, storedTuckedPreference: false)
        presentation.begin(sessionID: second)
        collapsed = state.applyBeforeFirstFrame(sessionID: second)!
        tucked = state.applyTuckedBeforeFirstFrame(sessionID: second)!
        precondition(presentation.markReady(sessionID: second, isCollapsed: collapsed,
                                             isDockHidden: tucked) == .collapsedDockView)
        precondition(presentation.firstPresentedHiddenState == false,
                     "collapsed-only preference must show the ordinary collapsed control")
        precondition(collapsed && !tucked)
        precondition(state.markPresented(sessionID: second))
        state.userDidToggle()
        collapsed = false
        precondition(!collapsed)
        precondition(state.applyBeforeFirstFrame(sessionID: second) == nil,
                     "rotation reapplied Start Dock Collapsed")
        precondition(state.applyTuckedBeforeFirstFrame(sessionID: second) == nil,
                     "layout reapplied Start Dock Tucked To Edge")

        // Both ON: the first branch is CollapsedDockView and its initial frame
        // uses the existing hidden-to-side state. This is not LCHideCollapsedDock.
        state.end()
        presentation.end(sessionID: second)
        let third = state.begin(storedPreference: true, storedTuckedPreference: true)
        presentation.begin(sessionID: third)
        collapsed = state.applyBeforeFirstFrame(sessionID: third)!
        tucked = state.applyTuckedBeforeFirstFrame(sessionID: third)!
        precondition(presentation.markReady(sessionID: third, isCollapsed: collapsed,
                                             isDockHidden: tucked) == .collapsedDockView)
        precondition(presentation.firstPresentedHiddenState == true,
                     "tucked preference did not select the hidden-to-side first state")
        precondition(collapsed && tucked)
        precondition(presentation.recordFirstBodyEvaluation(sessionID: third,
            isCollapsed: collapsed) == .collapsedDockView,
            "the first actual SwiftUI body branch did not select CollapsedDockView")
        precondition(state.markPresented(sessionID: third))

        // The edge control brings the dock back. Rotation/layout never re-tucks
        // it, and the next fresh session reads the persisted preference again.
        tucked = false
        precondition(state.applyTuckedBeforeFirstFrame(sessionID: third) == nil)
        precondition(!tucked, "rotation re-tucked a manually opened dock")
        state.end()
        presentation.end(sessionID: third)
        let fourth = state.begin(storedPreference: true, storedTuckedPreference: true)
        presentation.begin(sessionID: fourth)
        collapsed = state.applyBeforeFirstFrame(sessionID: fourth)!
        tucked = state.applyTuckedBeforeFirstFrame(sessionID: fourth)!
        precondition(presentation.markReady(sessionID: fourth, isCollapsed: collapsed,
                                             isDockHidden: tucked) == .collapsedDockView)
        precondition(collapsed && tucked,
                     "fresh session did not reapply both persisted preferences")

        // Collapsed OFF remains expanded even with no tuck; Start Dock Collapsed
        // retains its existing meaning and is independent of the new setting.
        state.end()
        presentation.end(sessionID: fourth)
        let fifth = state.begin(storedPreference: false, storedTuckedPreference: false)
        presentation.begin(sessionID: fifth)
        collapsed = state.applyBeforeFirstFrame(sessionID: fifth)!
        tucked = state.applyTuckedBeforeFirstFrame(sessionID: fifth)!
        precondition(presentation.markReady(sessionID: fifth, isCollapsed: collapsed,
                                             isDockHidden: tucked) == .expandedDockView)
        precondition(!collapsed && !tucked)
        print("DOCK_FIRST_PRESENTED_VIEW_AND_TUCK_BEHAVIOR_PASS")
    }
}
