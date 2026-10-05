
let testDefaults = UserDefaults(suiteName: "v3-snapshot-test-" + UUID().uuidString)!
typealias LCEmbeddedAuthenticationSnapshot = CredentialSnapshot
final class UIDevice {
    static let current = UIDevice()
    var identifierForVendor: UUID? = UUID(uuidString: "00000000-0000-4000-8000-000000000001")
}
struct CredentialSnapshot {
    var isAuthenticated = true
    var appleIDEmailAddress: String? = "same@example.invalid"
    var appleIDAdsid: String? = "dsid"
    var appleIDXcodeToken: String? = "token"
}
final class AuthManager {
    static let shared = AuthManager()
    var v3IdentityGeneration: UInt64 = 1
    var v3IdentityStamp = "service:1"
    var v3IdentityIsStable = true
    var authenticationSnapshot: CredentialSnapshot? = CredentialSnapshot()
}
struct StoredAccount { var appleID = "same@example.invalid" }
struct StoredTeam { var identifier = "team"; var name = "Team"; var account: StoredAccount? = StoredAccount() }
struct X509 { var expiryDate = Date.distantFuture }
struct Certificate { var serialNumber = "serial"; var x509: X509? = X509() }
struct ActiveCertificate { var certificate = Certificate() }
final class CertificateManager {
    static let shared = CertificateManager()
    var activeCertificate: ActiveCertificate? = ActiveCertificate()
}
struct NSFetchRequest<T> { init(entityName: String) {} }
final class Context { func fetch<T>(_ request: NSFetchRequest<T>) throws -> [T] { [] } }
final class DatabaseManager {
    static let shared = DatabaseManager()
    let viewContext = Context()
    var account: StoredAccount? = StoredAccount()
    var team: StoredTeam? = StoredTeam()
    func activeAccount() -> StoredAccount? { account }
    func activeTeam() -> StoredTeam? { team }
}
struct ObjectID { func uriRepresentation() -> URL { URL(string: "x-coredata://test/InstalledApp/p1")! } }
struct InstalledApp {
    static func all(in context: Context) -> [InstalledApp] { [] }
    var objectID = ObjectID(); var bundleIdentifier = "app"; var name = "App"; var version = "1"
    var isActive = true; var expirationDate = Date(); var refreshedDate = Date(); var hasUpdate = false
    var certificateStatusRaw: String? = "valid"; var openAppURL = URL(string: "test://open")!
}
struct Source {
    static let altStoreIdentifier = "default"
    var identifier = "source"; var name = "Source"; var subtitle: String? = nil
    var sourceURL = URL(string: "https://example.invalid")!; var apps: [String] = []
}
enum StoreApp { static let altstoreAppID = "host" }
extension UserDefaults {
    var isBetaUpdatesEnabled: Bool { false }; var isIdleTimeoutDisableEnabled: Bool { false }
    var responseCachingDisabled: Bool { false }; var isVerboseOperationsLoggingEnabled: Bool { false }
}
struct V3OperationRecoveryRecord {
    enum Phase: String { case prepared }
    var sessionID = "operation"; var kind = "install"; var phase = Phase.prepared; var stagedIPAToken: String?
}
struct V3DirectMutationRecoveryRecord { var requestID = "request" }
struct V3RecoveryStorageFailure: Error {
    enum Kind { case readFailure }
    init(_ kind: Kind, underlying: Error) {}
    var snapshotValue: [String: Any] { ["kind": "readFailure"] }
}
enum V3OperationRecoveryJournal {
    enum State { case operation(V3OperationRecoveryRecord), directMutation(V3DirectMutationRecoveryRecord) }
    static var state: State?
    static var unreadable = false
    static func currentState() throws -> State? {
        if unreadable { throw NSError(domain: "local", code: 1) }; return state
    }
    static func markDirectUnknownIfOwnerLost(requestID: String, currentServiceInstanceID: String) throws -> V3DirectMutationRecoveryRecord? { nil }
    static func runtimeAppGroupDiagnostic() -> String { "test" }
}
final class SnapshotAuth {
    var provisioningCompletion = V3ProvisioningCompletionState(defaults: testDefaults)
    var activeSessionIDForSnapshot: String?
    var provisioningRecoveryRequiresReconciliation = false
    var resumeAvailable = false
    func canResumeProvisioning() -> Bool { resumeAvailable }
    func canReauthenticateProvisioning() -> Bool { !provisioningRecoveryRequiresReconciliation && activeSessionIDForSnapshot == nil }
}
final class Operations { var activeMutationID: String? }
final class V3HeadlessRuntime {
    static let shared = V3HeadlessRuntime(); let auth = SnapshotAuth(); let operations = Operations()
}
final class RefreshAdmission {
    var isActive = false; var ownerLost = false; var runID: String?
    func expire() -> Bool { false }; func owns(_ id: String) -> Bool { false }
    func restoreLost(runID: String) -> Bool { false }
}
enum V3BackendCommands { static func pairingFileStatus() -> String { "present" } }
final class SnapshotService {
    let refreshAdmission = RefreshAdmission()
    let recoveryServiceInstanceID = "service"
    var mutationID: String?
    func safeDirectRecovery(_ record: V3DirectMutationRecoveryRecord) -> [String: Any] { ["requestID": record.requestID] }
    __PRODUCTION_SNAPSHOT__
    func read() throws -> [String: Any] {
        let encoded = try PropertyListSerialization.data(fromPropertyList: snapshot(), format: .binary, options: 0)
        return try PropertyListSerialization.propertyList(from: encoded, format: nil) as! [String: Any]
    }
}

@main
struct SnapshotHarness {
    static func main() throws {
        let service = SnapshotService(); let auth = V3HeadlessRuntime.shared.auth
        var snapshot = try service.read()
        precondition(snapshot["activeAccountPresent"] as? Bool == true)
        precondition(snapshot["provisioningIncomplete"] as? Bool == true)
        precondition(snapshot["provisioningState"] as? String == "unknown")
        precondition(snapshot["provisioningReauthenticationAvailable"] as? Bool == true)
        auth.provisioningCompletion.begin(attemptID: "A", owner: "same@example.invalid", identityStamp: "service:1")
        snapshot = try service.read()
        precondition(snapshot["provisioningState"] as? String == "incomplete")
        precondition(auth.provisioningCompletion.complete(attemptID: "A", owner: "same@example.invalid",
            identityStamp: "service:1", identityStable: true, fullProvisioningCompleted: true,
            activeAccountMatches: true, activeTeamMatches: true, activeCertificateMatches: true,
            binding: v3ProvisioningCompletionBinding(credentials: AuthManager.shared.authenticationSnapshot,
                teamID: "team", certificateSerial: "serial")))
        precondition(try! service.read()["provisioningIncomplete"] as? Bool == false)
        auth.provisioningCompletion = V3ProvisioningCompletionState(defaults: testDefaults)
        precondition(try! service.read()["provisioningIncomplete"] as? Bool == false,
            "same verified identity must stay ready after a normal service restart")
        auth.provisioningCompletion.begin(attemptID: "B", owner: "same@example.invalid", identityStamp: "service:1")
        precondition(try! service.read()["provisioningIncomplete"] as? Bool == true)
        DatabaseManager.shared.team = nil
        precondition(try! service.read()["activeTeamPresent"] as? Bool == false)
        DatabaseManager.shared.team = StoredTeam()
        CertificateManager.shared.activeCertificate = nil
        precondition(try! service.read()["activeCertificatePresent"] as? Bool == false)
        CertificateManager.shared.activeCertificate = ActiveCertificate()
        auth.activeSessionIDForSnapshot = UUID().uuidString
        precondition(try! service.read()["provisioningReauthenticationAvailable"] as? Bool == false)
        auth.activeSessionIDForSnapshot = nil
        service.mutationID = "mutation"
        precondition(try! service.read()["provisioningReauthenticationAvailable"] as? Bool == false)
        service.mutationID = nil
        V3OperationRecoveryJournal.state = .operation(V3OperationRecoveryRecord())
        precondition(try! service.read()["provisioningReauthenticationAvailable"] as? Bool == false)
        V3OperationRecoveryJournal.state = nil; V3OperationRecoveryJournal.unreadable = true
        precondition(try! service.read()["provisioningReauthenticationAvailable"] as? Bool == false)
        V3OperationRecoveryJournal.unreadable = false
        auth.provisioningCompletion = V3ProvisioningCompletionState(defaults: testDefaults)
        precondition(try! service.read()["provisioningState"] as? String == "unknown")
        AuthManager.shared.authenticationSnapshot?.appleIDEmailAddress = "different@example.invalid"
        snapshot = try service.read()
        precondition(snapshot["activeAccountPresent"] as? Bool == false)
        precondition(snapshot["activeTeamPresent"] as? Bool == false)
        precondition(snapshot["provisioningIncomplete"] as? Bool == true)
        print("V3_PROVISIONING_SNAPSHOT_PASS")
    }
}
