
struct V3ProvisioningResumeUnavailableError: Error {}
struct V3ProvisioningReauthenticationIdentityError: Error {}
enum OperationError: Error { case cancelled }
enum HarnessError: Error { case injected }
// The test substitutes provider diagnostics only; the provisioning loop and
// all recovery branching below are generated production methods.
struct CombinedFailure {
    enum SourceStep { case fetchTeams, saveAccount, fetchCertificate, activateCertificate, registerDevice }
}
struct DiagnosticFailure: Error {
    var requiresReconciliation: Bool { false }
    var portalSessionRejected: Bool { false }
}
func v3AccountOperationFailure(_ error: Error, step: CombinedFailure.SourceStep) -> DiagnosticFailure { DiagnosticFailure() }
struct ALTAnisetteData {}
struct ALTAppleAPISession { var dsid = "dsid"; var authToken = "token"; var anisetteData = ALTAnisetteData() }
struct ALTAccount { var appleID = "same@example.invalid"; var identifier = "account" }
struct ALTTeam { var identifier = "team"; var account: ALTAccount? = ALTAccount(); var name = "Team" }
struct ALTCertificate { var data: Data? = nil; var serialNumber = "certificate" }
struct CertificateDetails { var subject = "team"; var issuer = "team" }
func parseCertificate(derData: Data) -> CertificateDetails { CertificateDetails() }
struct SignInResult { let team: ALTTeam; let certificate: ALTCertificate?; let session: ALTAppleAPISession }
struct Credentials {
    var isAuthenticated = true
    var appleIDEmailAddress: String? = "same@example.invalid"
    var appleIDAdsid: String? = "dsid"
    var appleIDXcodeToken: String? = "token"
}
final class AuthManager {
    static let shared = AuthManager()
    var v3IdentityStamp = "stamp:1"
    var v3IdentityGeneration: UInt64 = 1
    var v3IdentityIsStable = true
    var authenticationSnapshot: Credentials? = Credentials()
    var session: ALTAppleAPISession? = ALTAppleAPISession()
    var team: ALTTeam? = ALTTeam()
    func v3CachedSessionMatchesCurrentRoute(_ session: ALTAppleAPISession?) -> Bool { session != nil }
    func v3ReplaceSession(_ session: ALTAppleAPISession?) { self.session = session }
}
final class CertificateManager {
    struct Active { var certificate = ALTCertificate() }
    static let shared = CertificateManager()
    var activeCertificate: Active? = Active()
    func setActiveCertificate(_ certificate: ALTCertificate) throws {
        try Harness.visit("activateCertificate")
        activeCertificate = Active(certificate: certificate)
    }
}
enum Harness {
    static var visits: [String] = []
    static var failure: String?
    static func visit(_ step: String) throws { visits.append(step); if failure == step { throw HarnessError.injected } }
}
enum Decision { case retry, cancel }
final class Handler {
    func handleSignInResult(_ result: Result<(ALTAccount, ALTAppleAPISession), Error>) async {}
    func resolveProvisioningError(_ error: Error) async -> Decision { .cancel }
}
class BaseOperation {
    func execute(parentProgress: Progress?) async throws -> SignInResult { fatalError() }
    func executePreconditionCheck(parentProgress: Progress?) async throws {}
}
final class SignInOperation: BaseOperation {
    var v3ForceProvisioningRetry = false
    var v3RequireFullProvisioning = true
    var v3ReauthenticateAppleID: String? = "same@example.invalid"
    var v3ReauthenticationIdentityStamp: String? = "stamp:1"
    var v3DidCompleteProvisioning = false
    let skipDeviceRegistration = false
    let skipCertificateProvisioning = false
    var isCancelled = false
    let signInHandler = Handler()
    func getAnisetteData() async throws -> ALTAnisetteData { try Harness.visit("cachedAnisette"); return ALTAnisetteData() }
    func silentSignIn() async throws -> (ALTAccount, ALTAppleAPISession)? {
        try Harness.visit("silentSignIn"); return (ALTAccount(), ALTAppleAPISession())
    }
    func authenticationLoop() async throws -> (ALTAccount, ALTAppleAPISession) {
        try Harness.visit("interactiveCredentials"); return (ALTAccount(), ALTAppleAPISession())
    }
    func fetchTeam(for account: ALTAccount, session: ALTAppleAPISession) async throws -> ALTTeam {
        try Harness.visit("fetchTeams"); return ALTTeam()
    }
    func saveTeamAndAccount(_ team: ALTTeam, makeActive: Bool = false) async throws {
        try Harness.visit(makeActive ? "activateAccount" : "saveAccount")
    }
    func fetchCertificate(for team: ALTTeam, session: ALTAppleAPISession) async throws -> ALTCertificate {
        try Harness.visit("fetchCertificate"); return ALTCertificate()
    }
    struct Device { let identifier = "redacted-test-device" }
    func registerCurrentDevice(for team: ALTTeam, session: ALTAppleAPISession) async throws -> Device {
        try Harness.visit("registerDevice"); return Device()
    }
    func finalizeAuthentication(result: Result<SignInResult, Error>) async throws {
        if case .success(let result) = result { try await saveTeamAndAccount(result.team, makeActive: true) }
    }
    func setProgress(_ value: Int64) {}
    func debugLog(_ message: String) {}
    func verboseLog(_ message: String) {}
    __PRODUCTION_OPERATION_METHODS__
    func validate(submitted: String, returned: String? = nil) throws {
        try v3ValidateReauthenticationIdentity(submittedAppleID: submitted, returnedAppleID: returned)
    }
}

@main
struct ReauthenticationOperationHarness {
    static func main() async throws {
        // Existing account/team/certificate/session cannot short-circuit the
        // explicitly requested reauthentication and actual provisioning loop.
        let full = SignInOperation()
        _ = try await full.execute(parentProgress: nil)
        precondition(full.v3DidCompleteProvisioning)
        precondition(Harness.visits == ["interactiveCredentials", "fetchTeams", "saveAccount",
            "fetchCertificate", "activateCertificate", "registerDevice", "activateAccount"],
            "reauthentication bypassed work or replayed silent/cached authentication")
        try full.validate(submitted: " SAME@example.invalid ", returned: "same@example.invalid")
        do { try full.validate(submitted: "other@example.invalid"); fatalError("different account accepted") }
        catch is V3ProvisioningReauthenticationIdentityError {}
        do { try full.validate(submitted: "same@example.invalid", returned: "other@example.invalid"); fatalError("different returned account accepted") }
        catch is V3ProvisioningReauthenticationIdentityError {}
        AuthManager.shared.v3IdentityStamp = "stamp:2"
        do { try full.validate(submitted: "same@example.invalid"); fatalError("identity transition accepted") }
        catch is V3ProvisioningReauthenticationIdentityError {}
        AuthManager.shared.v3IdentityStamp = "stamp:1"
        for failure in ["fetchTeams", "saveAccount", "fetchCertificate", "activateCertificate", "registerDevice", "activateAccount"] {
            Harness.visits = []; Harness.failure = failure
            let operation = SignInOperation()
            do { _ = try await operation.execute(parentProgress: nil); fatalError("failed stage returned success") }
            catch {}
            if failure != "activateAccount" { precondition(!operation.v3DidCompleteProvisioning) }
            precondition(!Harness.visits.contains("silentSignIn") && !Harness.visits.contains("cachedAnisette"))
            precondition(AuthManager.shared.team != nil && CertificateManager.shared.activeCertificate != nil,
                "failed recovery erased the previous account or certificate")
        }
        print("V3_REAUTHENTICATION_OPERATION_PASS")
    }
}
