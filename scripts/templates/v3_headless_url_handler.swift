import Foundation
// V3_HEADLESS_EXTERNAL_CALLBACKS_V3: external URLs are never trusted merely
// because they entered the service. Only a live one-shot backup owner may settle.
@MainActor
final class URLHandler {
    static let shared = URLHandler()
    private init() {}

    @discardableResult
    func handle(_ url: URL) -> Bool {
        guard let result = V3BackupCallbackResult(url: url,
            expectedTargetBundleID: Bundle.Info.activeBundleIdentifier) else { return false }
        return V3HeadlessRuntime.shared.operations.acceptBackupCallback(result)
    }
}
