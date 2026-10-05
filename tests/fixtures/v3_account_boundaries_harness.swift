
struct ALTAccount {}
struct ALTAppleAPISession { let dsid = "private-dsid"; let authToken = "private-token" }
struct ALTTeam { let identifier = "private-team" }
struct ALTCertificate { let data: Data? = nil }
struct ALTDevice { let identifier = "private-device" }
struct CertificateDetails { let subject = ""; let issuer = "" }
func parseCertificate(derData: Data) -> CertificateDetails { CertificateDetails() }
enum ProvisioningErrorDecision { case retry, cancel }
struct ALTSigner { init(team: ALTTeam, certificate: ALTCertificate) {} }
struct SignInResult { var team = ALTTeam(); var certificate: ALTCertificate? = ALTCertificate(); var session = ALTAppleAPISession() }
enum OperationError: Error { case cancelled }
final class AuthManager {
    static let shared = AuthManager()
    var session: ALTAppleAPISession?
    var team: ALTTeam?
    var transitions = 0
    func v3BeginIdentityTransition() { transitions += 1 }
    func v3CompleteIdentityTransition() { transitions -= 1 }
}
final class Keychain {
    static let shared = Keychain()
    var injected: Error?
    var writes = 0
    func writeAuthenticationCredentials(appleID: String, password: String, dsid: String, authToken: String) throws {
        writes += 1
        if let injected { throw injected }
    }
}
final class CertificateManager {
    struct Active { let certificate: ALTCertificate }
    static let shared = CertificateManager()
    var activeCertificate: Active?
    var failuresRemaining = 0, activations = 0, errorCode = 1009
    func setActiveCertificate(_ certificate: ALTCertificate) throws {
        activations += 1
        if failuresRemaining > 0 { failuresRemaining -= 1; throw NSError(domain: "com.SideStore.Keychain", code: errorCode) }
        activeCertificate = Active(certificate: certificate)
    }
}
final class Handler {
    var prompts = 0, failures = 0, completed = 0, provisioningPrompts = 0
    func resolveProvisioningError(_ error: Error) async -> ProvisioningErrorDecision {
        provisioningPrompts += 1
        if let local = error as? V3AccountOperationError, local.requiresReconciliation { return .cancel }
        return .retry
    }
    func credentials() async throws -> (String, String) { prompts += 1; return ("private-email", "private-password") }
    func handleSignInResult(_ result: Result<(ALTAccount, ALTAppleAPISession), Error>) async {
        if case .failure = result { failures += 1 }
    }
    func complete() async { completed += 1 }
    func resolvePostAuth() async {}
}
final class Operation {
    let signInHandler = Handler()
    var v3DidCompleteProvisioning = false
    var isCancelled = false
    var requiresPostAuthFlow = false
    var skipCertificateProvisioning = false
    var skipDeviceRegistration = false
    var fetchedCertificates = 0, registeredDevices = 0
    var saveFails = true, validations = 0, signIns = 0
    func debugLog(_ text: String) {}
    func verboseLog(_ text: String) {}
    func signIn(appleID: String, password: String) async throws -> (ALTAccount, ALTAppleAPISession) {
        signIns += 1
        let session = ALTAppleAPISession()
        try commit(appleID: appleID, password: password, session: session)
        return (ALTAccount(), session)
    }
    func commit(appleID: String, password: String, session: ALTAppleAPISession) throws {
        // GENERATED_COMMIT
    }
    func saveTeamAndAccount(_ team: ALTTeam, makeActive: Bool = false) async throws {
        if saveFails { throw NSError(domain: NSCocoaErrorDomain, code: 134030,
            userInfo: [NSLocalizedDescriptionKey: "PRIVATE_RECORD HTTP 503 lc_stage=network"]) }
    }
    func validateCodeSign(signer: ALTSigner, session: ALTAppleAPISession) async throws -> Bool {
        validations += 1; return true
    }
    func fetchTeam(for account: ALTAccount, session: ALTAppleAPISession) async throws -> ALTTeam { ALTTeam() }
    func fetchCertificate(for team: ALTTeam, session: ALTAppleAPISession) async throws -> ALTCertificate {
        fetchedCertificates += 1; return ALTCertificate()
    }
    func registerCurrentDevice(for team: ALTTeam, session: ALTAppleAPISession) async throws -> ALTDevice {
        registeredDevices += 1; return ALTDevice()
    }
    // GENERATED_PROVISIONING
    // GENERATED_AUTH_LOOP
    // GENERATED_FINALIZE
}
@main struct AccountBoundaryHarness {
    static func main() async throws {
        for code in [1009, 1010, -34018] {
            let op = Operation()
            Keychain.shared.injected = NSError(domain: "com.SideStore.Keychain", code: code)
            AuthManager.shared.session = nil
            do { _ = try await op.authenticationLoop(); preconditionFailure("failed commit became auth success") }
            catch let local as V3AccountOperationError {
                precondition(local.credentialCommit && local.requiresReconciliation == (code == 1010))
                precondition(op.signIns == 1 && op.signInHandler.prompts == 1 && op.signInHandler.failures == 1)
                precondition(AuthManager.shared.session == nil && AuthManager.shared.transitions == 0)
            }
        }
        Keychain.shared.injected = nil
        let success = Operation()
        _ = try await success.authenticationLoop()
        precondition(AuthManager.shared.session != nil && AuthManager.shared.transitions == 0)
        let activate = Operation()
        do { try await activate.finalizeAuthentication(result: .success(SignInResult())); preconditionFailure("failed activation completed") }
        catch let local as V3AccountOperationError {
            precondition(local.step == .activateAccount && !local.requiresReconciliation)
            precondition(local.failure(operation: "signIn", id: UUID().uuidString).safeCause == .accountActivationFailed)
            precondition(activate.validations == 0 && activate.signInHandler.completed == 0)
        }
        activate.saveFails = false
        try await activate.finalizeAuthentication(result: .success(SignInResult()))
        precondition(activate.validations == 1 && activate.signInHandler.completed == 1)
        let provisioning = Operation()
        provisioning.saveFails = false
        CertificateManager.shared.activeCertificate = nil
        CertificateManager.shared.failuresRemaining = 1
        CertificateManager.shared.activations = 0
        _ = try await provisioning.provisioningLoop(account: ALTAccount(), session: ALTAppleAPISession(), reportProgress: { _ in })
        precondition(provisioning.fetchedCertificates == 1, "local activation retry fetched/created another certificate")
        precondition(CertificateManager.shared.activations == 2 && provisioning.registeredDevices == 1)
        precondition(provisioning.signInHandler.provisioningPrompts == 1)
        let uncertain = Operation()
        uncertain.saveFails = false
        CertificateManager.shared.activeCertificate = nil
        CertificateManager.shared.failuresRemaining = 1
        CertificateManager.shared.errorCode = 1010
        do {
            _ = try await uncertain.provisioningLoop(account: ALTAccount(), session: ALTAppleAPISession(), reportProgress: { _ in })
            preconditionFailure("uncertain certificate save was replayed")
        } catch let local as V3AccountOperationError {
            precondition(local.step == .activateCertificate && local.requiresReconciliation)
            precondition(uncertain.fetchedCertificates == 1 && uncertain.registeredDevices == 0)
        }
        print("Generated account boundaries PASS")
    }
}
