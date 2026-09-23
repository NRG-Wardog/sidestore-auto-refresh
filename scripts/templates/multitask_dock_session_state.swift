import Foundation

// The dock singleton outlives multitasking sessions. This model owns the
// one-time first-frame preference and the user override for each fresh session.
struct LCMultitaskDockSessionState {
    private(set) var sessionID: String?
    private var storedPreference = false
    private var initialPreferenceApplied = false
    private(set) var manuallyOverridden = false

    mutating func begin(storedPreference: Bool) -> String {
        let id = UUID().uuidString
        sessionID = id
        self.storedPreference = storedPreference
        initialPreferenceApplied = false
        manuallyOverridden = false
        return id
    }

    func preference(for id: String) -> Bool? {
        guard sessionID == id else { return nil }
        return storedPreference
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
    }
}
