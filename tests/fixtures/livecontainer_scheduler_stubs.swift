// Test doubles for the OS APIs. These verify our Swift types and coordinator
// behavior, NOT iOS delivery, transport, signing, or physical-device execution.
import Foundation
@MainActor enum LiveContainerNetworkPreflight {
    static var error: Error?
    static var checks = 0
    static func check(allowForegroundActivation: Bool) async throws {
        checks += 1
        if let error { throw error }
    }
    static func consumePendingReturn() -> Bool { false }
}
class BGTask {
    var expirationHandler: (() -> Void)?
    var completions: [Bool] = []
    func setTaskCompleted(success: Bool) {
        completions.append(success)
        if FakeGuestSignatureProbe.firstCompletionProbeCalls == nil {
            FakeGuestSignatureProbe.firstCompletionProbeCalls = FakeGuestSignatureProbe.calls
        }
    }
}
class BGProcessingTask: BGTask {}
class BGAppRefreshTask: BGTask {}
class BGTaskRequest { let identifier: String; var earliestBeginDate: Date?; init(identifier: String) { self.identifier = identifier } }
class BGProcessingTaskRequest: BGTaskRequest { var requiresNetworkConnectivity = false; var requiresExternalPower = false }
class BGAppRefreshTaskRequest: BGTaskRequest {}
class BGTaskScheduler {
    static let shared = BGTaskScheduler()
    var requests: [BGTaskRequest] = []
    var reject = false
    func register(forTaskWithIdentifier identifier: String, using queue: DispatchQueue?, launchHandler: @escaping (BGTask) -> Void) -> Bool { true }
    func submit(_ request: BGTaskRequest) throws {
        if reject { throw NSError(domain: "BGTaskSchedulerErrorDomain", code: 3) }
        requests.append(request)
    }
    func cancel(taskRequestWithIdentifier identifier: String) {}
}
enum UNAuthorizationStatus { case notDetermined, denied, authorized, provisional }
struct UNAuthorizationOptions: OptionSet {
    let rawValue: Int
    static let alert = Self(rawValue: 1); static let sound = Self(rawValue: 2)
}
struct UNNotificationSettings { var authorizationStatus: UNAuthorizationStatus = .authorized }
struct UNNotificationSound { static let `default` = Self() }
class UNMutableNotificationContent { var title = ""; var body = ""; var sound: UNNotificationSound? }
class UNTimeIntervalNotificationTrigger { init(timeInterval: TimeInterval, repeats: Bool) {} }
class UNNotificationRequest {
    let identifier: String; let content: UNMutableNotificationContent
    init(identifier: String, content: UNMutableNotificationContent, trigger: UNTimeIntervalNotificationTrigger?) { self.identifier = identifier; self.content = content }
}
class UNUserNotificationCenter {
    static let shared = UNUserNotificationCenter()
    static func current() -> UNUserNotificationCenter { shared }
    var requests: [UNNotificationRequest] = []
    func notificationSettings() async -> UNNotificationSettings { UNNotificationSettings() }
    func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool { true }
    func getNotificationSettings(_ completion: (UNNotificationSettings) -> Void) { completion(UNNotificationSettings()) }
    func add(_ request: UNNotificationRequest, withCompletionHandler completion: ((Error?) -> Void)? = nil) { requests.append(request); completion?(nil) }
    func removePendingNotificationRequests(withIdentifiers identifiers: [String]) {}
}
final class FakeAppInfo {
    let identifier: String
    let path: String?

    init(identifier: String = "test.guest", path: String? = nil) {
        self.identifier = identifier
        self.path = path
    }

    func bundlePath() -> String? { path }
    func bundleIdentifier() -> String { identifier }
}

struct FakeGuest { let appInfo: FakeAppInfo }
class FakeModel { var apps: [FakeGuest] = []; var hiddenApps: [FakeGuest] = [] }
class DataManager { static let shared = DataManager(); var model = FakeModel() }

enum FakeGuestSignatureProbe {
    static var calls = 0
    static var firstCompletionProbeCalls: Int?
    static var invalidPaths = Set<String>()
}

func checkCodeSignature(_ path: UnsafePointer<CChar>) -> Bool {
    let value = String(cString: path)
    FakeGuestSignatureProbe.calls += 1
    return !FakeGuestSignatureProbe.invalidPaths.contains(value)
}

enum FakeManifestMode { case valid, missing, mismatch, incomplete, failed }
@MainActor
enum LiveContainerRefreshBridge {
    static var calls = 0
    static var fails = false
    static var manifestMode: FakeManifestMode = .valid
    static var hostHandoff = false
    static func refreshAllApps() async throws {
        calls += 1
        if fails { throw NSError(domain: "test.refresh", code: 42, userInfo: [NSLocalizedDescriptionKey: "transport failed"]) }
        let defaults = LiveContainerAutoRefreshScheduler.defaults
        let runID = defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? ""
        if hostHandoff {
            defaults.set(true, forKey: "liveContainerAutoRefreshHostHandoff")
            defaults.set(runID, forKey: "liveContainerAutoRefreshHostHandoffRunID")
            defaults.set(Date(), forKey: "liveContainerAutoRefreshHostHandoffStartedAt")
        }
        switch manifestMode {
        case .missing:
            return
        case .valid:
            defaults.set(["run_id": runID, "expected_ids": ["spotify"],
                          "results": [["bundle_id": "spotify", "success": true]]],
                         forKey: "liveContainerAutoRefreshVerification")
        case .mismatch:
            defaults.set(["run_id": "stale-run", "expected_ids": ["spotify"],
                          "results": [["bundle_id": "spotify", "success": true]]],
                         forKey: "liveContainerAutoRefreshVerification")
        case .incomplete:
            defaults.set(["run_id": runID, "expected_ids": ["spotify", "other"],
                          "results": [["bundle_id": "spotify", "success": true]]],
                         forKey: "liveContainerAutoRefreshVerification")
        case .failed:
            defaults.set(["run_id": runID, "expected_ids": ["spotify"],
                          "results": [["bundle_id": "spotify", "success": false]]],
                         forKey: "liveContainerAutoRefreshVerification")
        }
    }
}
@MainActor
enum LiveContainerAutoRefreshAlarmProvider {
    static func cancelIfAvailable() {}
    static func scheduleIfAvailable(deadline: Date) async {}
}
