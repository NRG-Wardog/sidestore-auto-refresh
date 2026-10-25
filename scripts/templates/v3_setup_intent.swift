#if canImport(AppIntents)
import AppIntents
import Foundation

// V3_SETUP_INTENT_V1: opens the host into the Setup Assistant. It carries no
// credentials, device identifiers, pairing material, network values or
// signing material. The app remains fully usable without Shortcuts.
@available(iOS 16.0, *)
struct V3SetupAssistantIntent: AppIntent {
    static var title: LocalizedStringResource { "Set Up LiveContainer + SideStore" }
    static var description: IntentDescription? {
        IntentDescription("Opens the Setup Assistant.")
    }
    static var openAppWhenRun: Bool { true }

    @MainActor
    func perform() async throws -> some IntentResult {
        LCUtils.appGroupUserDefault.set(true, forKey: "V3PendingSetupAssistant")
        return .result()
    }
}
#endif
