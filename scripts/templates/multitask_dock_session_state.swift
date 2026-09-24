import Foundation

enum LCMultitaskDockRenderedMode: Equatable {
    case expandedDockView
    case collapsedDockView
}

// A reused UIHostingController may evaluate its old root before a new virtual
// window session is assembled. Keep the dock branch unavailable until that
// session's persisted preference has selected its first visible mode.
struct LCMultitaskDockPresentationState {
    private(set) var sessionID: String?
    private(set) var firstPresentedMode: LCMultitaskDockRenderedMode?
    private(set) var firstBodyEvaluationMode: LCMultitaskDockRenderedMode?

    var isReady: Bool { sessionID != nil && firstPresentedMode != nil }

    mutating func begin(sessionID: String) {
        self.sessionID = sessionID
        firstPresentedMode = nil
        firstBodyEvaluationMode = nil
    }

    @discardableResult
    mutating func markReady(sessionID: String, isCollapsed: Bool) -> LCMultitaskDockRenderedMode? {
        guard self.sessionID == sessionID, firstPresentedMode == nil else { return nil }
        let mode = LCMultitaskDockSessionState.renderedMode(isCollapsed: isCollapsed)
        firstPresentedMode = mode
        return mode
    }

    @discardableResult
    mutating func recordFirstBodyEvaluation(sessionID: String, isCollapsed: Bool) -> LCMultitaskDockRenderedMode? {
        guard self.sessionID == sessionID, isReady, firstBodyEvaluationMode == nil else { return nil }
        let mode = LCMultitaskDockSessionState.renderedMode(isCollapsed: isCollapsed)
        firstBodyEvaluationMode = mode
        return mode
    }

    mutating func end(sessionID: String?) {
        guard sessionID != nil, self.sessionID == sessionID else { return }
        self.sessionID = nil
        firstPresentedMode = nil
        firstBodyEvaluationMode = nil
    }
}

// The dock singleton outlives multitasking sessions. This model owns the
// one-time first-frame preference and the user override for each fresh session.
struct LCMultitaskDockSessionState {
    private(set) var sessionID: String?
    private var storedPreference = false
    private var initialPreferenceApplied = false
    private(set) var manuallyOverridden = false
    private(set) var wasPresented = false

    var isActiveSession: Bool { sessionID != nil }

    static func renderedMode(isCollapsed: Bool) -> LCMultitaskDockRenderedMode {
        isCollapsed ? .collapsedDockView : .expandedDockView
    }

    mutating func begin(storedPreference: Bool) -> String {
        let id = UUID().uuidString
        sessionID = id
        self.storedPreference = storedPreference
        initialPreferenceApplied = false
        manuallyOverridden = false
        wasPresented = false
        return id
    }

    mutating func markPresented(sessionID id: String) -> Bool {
        guard sessionID == id, initialPreferenceApplied, !wasPresented else { return false }
        wasPresented = true
        return true
    }

    mutating func applyBeforeFirstFrame(sessionID id: String) -> Bool? {
        guard sessionID == id, !initialPreferenceApplied else { return nil }
        initialPreferenceApplied = true
        return manuallyOverridden ? nil : storedPreference
    }

    mutating func userDidToggle() {
        guard sessionID != nil else { return }
        manuallyOverridden = true
    }

    mutating func end() {
        sessionID = nil
        initialPreferenceApplied = false
        manuallyOverridden = false
        wasPresented = false
    }
}
