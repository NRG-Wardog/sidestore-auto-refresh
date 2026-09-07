// Foundation-only policy. Kept outside Python strings so swiftc checks the real code.
import Foundation

struct LiveContainerRefreshTaskIdentifiers: Equatable {
    let processing: String
    let watchdog: String

    static func resolve(info: [String: Any]) throws -> Self {
        let suffix = ".sidestore.automatic-refresh"
        let permitted = info["BGTaskSchedulerPermittedIdentifiers"] as? [String] ?? []
        let candidates = permitted.filter { $0.hasSuffix(suffix) }
        guard candidates.count == 1, Set(permitted).count == permitted.count,
              permitted.contains(candidates[0] + ".watchdog") else {
            throw NSError(domain: "LiveContainerRefresh.Configuration", code: 3,
                userInfo: [NSLocalizedDescriptionKey: "This installation has missing or ambiguous background task identifiers. Reinstall a corrected build; manual refresh remains available."])
        }
        let modes = Set(info["UIBackgroundModes"] as? [String] ?? [])
        guard modes.contains("processing"), modes.contains("fetch") else {
            throw NSError(domain: "LiveContainerRefresh.Configuration", code: 3,
                userInfo: [NSLocalizedDescriptionKey: "This installation is missing the processing or fetch background mode. Manual refresh remains available."])
        }
        // The installed allowlist is authoritative. An external signer may leave
        // these IDs unchanged OR rewrite them. Do not invent a team-qualified ID.
        return Self(processing: candidates[0], watchdog: candidates[0] + ".watchdog")
    }
}

struct LiveContainerRefreshPolicy {
    static func earliestUsefulDate(now: Date, deadline: Date, lead: TimeInterval,
                                   eligible: Date?, retry: Date?) -> Date {
        [now, deadline.addingTimeInterval(-lead), eligible ?? now, retry ?? now].max()!
    }

    static func workIsDue(now: Date, eligible: Date?, retry: Date?, pendingHandoff: Bool,
                          retryExhausted: Bool, manual: Bool) -> Bool {
        if pendingHandoff { return false }
        if manual { return true }
        if retryExhausted { return false }
        if let retry, retry > now { return false }
        if let eligible, eligible > now { return false }
        return true
    }

    static func retryDelay(failureCount: Int) -> TimeInterval? {
        let delays: [TimeInterval] = [5 * 60, 20 * 60, 60 * 60]
        guard (1...delays.count).contains(failureCount) else { return nil }
        return delays[failureCount - 1]
    }

    static func isUserActionFailure(_ error: NSError) -> Bool {
        // Classify by concrete domain/code, not by guessing from localized text.
        error.domain == "LiveContainerRefresh.Configuration" ||
        error.domain == "com.SideStore.Authentication" ||
        error.domain == "LiveContainerRefresh.UnsupportedOS"
    }
}

/// Thread-safe terminal claim shared by completion and expiration callbacks.
/// A late async completion must not call setTaskCompleted for a second time.
final class LiveContainerRefreshCompletionGate: @unchecked Sendable {
    private let lock = NSLock()
    private var finished = false

    var isFinished: Bool {
        lock.lock()
        defer { lock.unlock() }
        return finished
    }

    @discardableResult
    func claim() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard !finished else { return false }
        finished = true
        return true
    }
}

/// Reads validity metadata from the installed, OS-signed host bundle, never
/// from SideStore's optimistic database result. This is metadata inspection,
/// NOT independent cryptographic validation of the CMS signer/certificate.
struct LiveContainerHostProfile {
    let identifier: String
    let uuid: String
    let expiration: Date

    static func read(at url: URL, expectedBundleID: String) throws -> Self {
        let data = try Data(contentsOf: url, options: .mappedIfSafe)
        guard data.count <= 2 * 1024 * 1024,
              let start = data.range(of: Data("<?xml".utf8)),
              let end = data.range(of: Data("</plist>".utf8), in: start.lowerBound..<data.endIndex),
              let plist = try PropertyListSerialization.propertyList(
                  from: data.subdata(in: start.lowerBound..<end.upperBound), options: [], format: nil) as? [String: Any],
              let entitlements = plist["Entitlements"] as? [String: Any],
              let identifier = entitlements["application-identifier"] as? String,
              let prefixes = plist["ApplicationIdentifierPrefix"] as? [String],
              prefixes.contains(where: { identifier == $0 + "." + expectedBundleID }),
              let uuid = plist["UUID"] as? String, !uuid.isEmpty,
              let expiration = plist["ExpirationDate"] as? Date else {
            throw NSError(domain: "LiveContainerRefresh.Verification", code: 1002,
                userInfo: [NSLocalizedDescriptionKey: "The installed host provisioning profile could not be read or its identity does not match. Refresh success has not been verified."])
        }
        return Self(identifier: identifier, uuid: uuid, expiration: expiration)
    }
}
