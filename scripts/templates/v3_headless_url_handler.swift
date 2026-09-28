import Foundation
// V3_HEADLESS_EXTERNAL_CALLBACKS_V3: only trusted in-process backup callbacks
// remain service-owned; pairing export is not exposed through an unauthenticated URL.
@MainActor
final class URLHandler {
    static let shared = URLHandler()
    private init() {}

    @discardableResult
    func handle(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == "sidestore",
              let host = url.host?.lowercased() else { return false }
        guard host == "appbackupresponse" else { return false }
        let result: Result<Void, Error>
        switch url.path.lowercased() {
        case "/success":
            result = .success(())
        case "/failure":
            let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let values = items.reduce(into: [String: String]()) { result, item in
                if let value = item.value, result[item.name] == nil { result[item.name] = value }
            }
            guard let domain = values["errorDomain"], !domain.isEmpty, domain.utf8.count <= 128,
                  let codeValue = values["errorCode"], let code = Int(codeValue) else { return false }
            // Provider text can contain private account data. Preserve the stable
            // domain/code while replacing its untrusted description with fixed copy.
            result = .failure(NSError(domain: domain, code: code,
                userInfo: [NSLocalizedDescriptionKey: "The backup or restore operation did not complete."]))
        default:
            return false
        }
        NotificationCenter.default.post(name: AppDelegate.appBackupDidFinish, object: nil,
            userInfo: [AppDelegate.appBackupResultKey: result])
        return true
    }
}
