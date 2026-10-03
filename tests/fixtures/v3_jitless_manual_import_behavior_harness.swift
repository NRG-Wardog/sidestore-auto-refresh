import Foundation

final class ImportScenario {
    let mode: String
    let inputURL: URL
    init(mode: String, inputURL: URL) {
        self.mode = mode
        self.inputURL = inputURL
    }
}

struct BoolAlert {
    let scenario: ImportScenario
    func open() async -> Bool? { scenario.mode == "cancel-import" ? false : true }
}

struct FileAlert {
    let scenario: ImportScenario
    func open() async -> URL? {
        scenario.mode == "cancel-file" ? nil : scenario.inputURL
    }
}

struct PasswordAlert {
    let scenario: ImportScenario
    func open() async -> String? {
        scenario.mode == "cancel-password" ? nil :
            (scenario.mode == "validation-fail" ? "invalid-password" : "valid-password")
    }
}

final class RecordingAppGroupDefaults: @unchecked Sendable {
    var willWrite: ((String) -> Void)?
    func set(_ value: Any?, forKey key: String) {
        willWrite?(key)
        UserDefaults.standard.set(value, forKey: key)
    }
}

enum LCUtils {
    static let appGroupUserDefault = RecordingAppGroupDefaults()
    static func getCertTeamId(withKeyData data: Data, password: String) -> String? {
        !data.isEmpty && password == "valid-password" ? "TEAM-123" : nil
    }
}

enum LCSharedUtils {
    static func appGroupID() -> String { "group.example.livecontainer" }
}

extension String { var loc: String { self } }

final class ImportSettingsHarness {
    let scenario: ImportScenario
    var certificateDataFound = false
    var errorInfo = ""
    var errorShow = false
    var certificateImportAlert: BoolAlert { BoolAlert(scenario: scenario) }
    var certificateImportFileAlert: FileAlert { FileAlert(scenario: scenario) }
    var certificateImportPasswordAlert: PasswordAlert { PasswordAlert(scenario: scenario) }

    init(scenario: ImportScenario) { self.scenario = scenario }

$IMPORT_OWNERSHIP_HELPER$
    func beginPendingImport() -> String { V3CertificateImportOwnership.begin() }
    func isPendingImportActive(_ requestID: String) -> Bool {
        V3CertificateImportOwnership.isActive(requestID)
    }

$MANUAL_IMPORT_FUNCTION$
}

final class LeaseWriteRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private let settings: ImportSettingsHarness
    private let requestID: String
    private var activeAtCertificateWrite: Bool?

    init(settings: ImportSettingsHarness, requestID: String) {
        self.settings = settings
        self.requestID = requestID
    }

    func record(_ key: String) {
        guard key == "LCCertificateData" else { return }
        let active = settings.isPendingImportActive(requestID)
        lock.lock(); defer { lock.unlock() }
        activeAtCertificateWrite = active
    }

    func snapshot() -> Bool? {
        lock.lock(); defer { lock.unlock() }
        return activeAtCertificateWrite
    }
}

final class ImportEventRecorder: @unchecked Sendable {
    struct Snapshot {
        let count: Int
        let valuesAtPost: [String: Any]
    }
    private let lock = NSLock()
    private var count = 0
    private var valuesAtPost: [String: Any] = [:]

    func record(defaults: UserDefaults) {
        lock.lock(); defer { lock.unlock() }
        count += 1
        for key in ["LCCertificateData", "LCCertificatePassword", "LCCertificateUpdateDate", "LCAppGroupID"] {
            if let value = defaults.object(forKey: key) { valuesAtPost[key] = value }
        }
    }

    func snapshot() -> Snapshot {
        lock.lock(); defer { lock.unlock() }
        return Snapshot(count: count, valuesAtPost: valuesAtPost)
    }
}

@main struct ManualImportBehaviorHarness {
    static func main() async throws {
        let mode = CommandLine.arguments[1]
        let defaults = UserDefaults.standard
        for key in ["LCCertificateData", "LCCertificatePassword", "LCCertificateUpdateDate", "LCAppGroupID",
                    "V3PendingCertificateImportRequestID", "V3PendingCertificateImportExpiry"] {
            defaults.removeObject(forKey: key)
        }
        let inputURL = URL(fileURLWithPath: CommandLine.arguments[2])
        if mode != "file-error" { try Data("CERTIFICATE-BYTES".utf8).write(to: inputURL) }
        let scenario = ImportScenario(mode: mode,
            inputURL: mode == "file-error"
                ? inputURL.appendingPathComponent("missing.p12")
                : inputURL)
        let settings = ImportSettingsHarness(scenario: scenario)
        let pendingRequestID = settings.beginPendingImport()
        let leaseWrites = LeaseWriteRecorder(settings: settings, requestID: pendingRequestID)
        LCUtils.appGroupUserDefault.willWrite = { key in leaseWrites.record(key) }
        let recorder = ImportEventRecorder()
        let token = NotificationCenter.default.addObserver(
            forName: Notification.Name("V3CanonicalJITLessCertificateUpdated"),
            object: nil, queue: nil) { _ in recorder.record(defaults: defaults) }
        defer { NotificationCenter.default.removeObserver(token) }

        await settings.importCertificate()

        let expectedSuccess = mode == "success"
        let observed = recorder.snapshot()
        precondition(observed.count == (expectedSuccess ? 1 : 0), "event count for \(mode)")
        precondition(settings.certificateDataFound == expectedSuccess, "state for \(mode)")
        precondition(settings.isPendingImportActive(pendingRequestID) == !expectedSuccess,
            "pending request state for \(mode)")
        precondition(leaseWrites.snapshot() == (expectedSuccess ? false : nil),
            "pending request was not invalidated before certificate write for \(mode)")
        if expectedSuccess {
            precondition(defaults.data(forKey: "LCCertificateData") == Data("CERTIFICATE-BYTES".utf8))
            precondition(defaults.string(forKey: "LCCertificatePassword") == "valid-password")
            precondition(defaults.object(forKey: "LCCertificateUpdateDate") as? Date != nil)
            precondition(defaults.string(forKey: "LCAppGroupID") == "group.example.livecontainer")
            precondition(observed.valuesAtPost["LCCertificateData"] as? Data == Data("CERTIFICATE-BYTES".utf8))
            precondition(observed.valuesAtPost["LCCertificatePassword"] as? String == "valid-password")
            precondition(observed.valuesAtPost["LCCertificateUpdateDate"] as? Date != nil)
            precondition(observed.valuesAtPost["LCAppGroupID"] as? String == "group.example.livecontainer")
        } else {
            for key in ["LCCertificateData", "LCCertificatePassword", "LCCertificateUpdateDate", "LCAppGroupID"] {
                precondition(defaults.object(forKey: key) == nil, "unexpected write for \(mode): \(key)")
            }
        }
    }
}
