import Foundation

enum LCMultitaskDockRenderedMode: Equatable {
    case expandedDockView
    case collapsedDockView
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
