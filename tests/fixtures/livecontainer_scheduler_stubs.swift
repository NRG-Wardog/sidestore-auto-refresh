// Test doubles for the OS APIs. These verify our Swift types and coordinator
// behavior, NOT iOS delivery, transport, signing, or physical-device execution.
import Foundation
class BGTask {
    var expirationHandler: (() -> Void)?
    var completions: [Bool] = []
    func setTaskCompleted(success: Bool) { completions.append(success) }
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
class FakeAppInfo { func bundlePath() -> String? { nil }; func bundleIdentifier() -> String { "test.guest" } }
struct FakeGuest { var appInfo = FakeAppInfo() }
class FakeModel { var apps: [FakeGuest] = []; var hiddenApps: [FakeGuest] = [] }
class DataManager { static let shared = DataManager(); var model = FakeModel() }
func checkCodeSignature(_ path: UnsafePointer<CChar>) -> Bool { true }
@MainActor
enum LiveContainerRefreshBridge {
    static var calls = 0
    static var fails = false
    static var incomplete = false
    static func refreshAllApps() async throws {
        calls += 1
        if fails { throw NSError(domain: "test.refresh", code: 42, userInfo: [NSLocalizedDescriptionKey: "transport failed"]) }
        let defaults = LiveContainerAutoRefreshScheduler.defaults
        defaults.set(["run_id": defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? "",
                      "expected_ids": incomplete ? ["spotify", "other"] : ["spotify"],
                      "results": [["bundle_id": "spotify", "success": true]]],
                     forKey: "liveContainerAutoRefreshVerification")
    }
}
@MainActor
enum LiveContainerAutoRefreshAlarmProvider {
    static func cancelIfAvailable() {}
    static func scheduleIfAvailable(deadline: Date) async {}
}
