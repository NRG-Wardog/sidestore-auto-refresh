// Production authentication routing is inserted below. Only platform storage,
// credential input, Apple IO, and post-auth provisioning are substituted.
struct ALTAccount { let appleID: String; let identifier: String }
struct ALTAppleAPISession { let dsid: String; let authToken: String }
struct SignInResult { let account: ALTAccount; let session: ALTAppleAPISession }
enum OperationError: Error {
    case notAuthenticated
    static var cancelled: CancellationError { CancellationError() }
}
struct V3ProvisioningResumeUnavailableError: Error {}
struct V3ProvisioningReauthenticationIdentityError: Error {}
enum HarnessError: Error { case rejected }
enum CombinedFailure {
    enum SourceStep { case anisetteFetch, appleAuthentication, accountLookup }
    static func isURLCancellation(domain: String, code: Int) -> Bool {
        domain == NSURLErrorDomain && code == NSURLErrorCancelled
    }
}
enum CommitStep { case credentialCommit }
struct V3AccountOperationError: Error {
    let underlying: Error
    var credentialCommit: Bool { true }
}
func v3AccountOperationFailure(_ error: Error, step: CommitStep) -> V3AccountOperationError {
    (error as? V3AccountOperationError) ?? V3AccountOperationError(underlying: error)
}
// The injected Apple error is deliberately unclassified. Provider classification
// has its own production tests; this fixture tests routing, never Apple's response.
enum FixtureFailureKind: String { case unknown, anisetteIdentityStateInvalid }
func v3ClassifyAuthError(_ error: Error) -> FixtureFailureKind? {
    if error is CancellationError { return nil }
    if let phase = error as? V3AuthenticationPhaseError, phase.underlying is LCAnisettePairError {
        return .anisetteIdentityStateInvalid
    }
    return .unknown
}
struct AnisetteConfigManager {
    static let shared = AnisetteConfigManager()
    func resolvedXcodeVersion() async -> String { "fixture" }
}
enum Harness {
    static var events: [String] = []
    static var before: [String: [String: Data]] = [:]
    static var expectPristineBeforeCredentials = true
    static var exercisePairGuard = false
}
final class Handler {
    var appleID = "saved@example.invalid"
    var calls = 0
    var cancelImmediately = false
    var cancelAfterFirst = false
    func credentials() async throws -> (String, String) {
        if Harness.expectPristineBeforeCredentials && calls == 0 {
            precondition(Harness.events.isEmpty, "manual sign-in ran saved authentication before credentials")
            precondition(Store.data == Harness.before && Store.writes == 0,
                "manual sign-in changed saved credentials/certificate before user input")
        }
        calls += 1
        Harness.events.append("credentials")
        if cancelImmediately || (cancelAfterFirst && calls > 1) { throw CancellationError() }
        return (appleID, "manual-password")
    }
    func handleSignInResult(_ result: Result<(ALTAccount, ALTAppleAPISession), Error>) async {
        if case .failure = result { Harness.events.append("attemptFailure") }
    }
    func accountRepair(url: URL, message: String) async {}
    func verificationCode(for request: String) async throws -> String { "fixture" }
}
final class AuthManager {
    static let shared = AuthManager()
    var session: ALTAppleAPISession?
    var v3IdentityStamp = "stamp:1"
    var v3IdentityIsStable = true
    var tokenRejects = false
    var passwordRejects = false
    var overrideDSID: String?
    var authenticationSnapshot: LCEmbeddedAuthenticationSnapshot? {
        try? Keychain.shared.authenticationSnapshot()
    }
    func v3BeginIdentityTransition() { v3IdentityIsStable = false; v3IdentityStamp += ":begin" }
    func v3CompleteIdentityTransition() { v3IdentityIsStable = true; v3IdentityStamp += ":end" }
    func v3ReplaceSession(_ value: ALTAppleAPISession?) { session = value }
    func authenticateWithToken(adsid: String, xcodeToken: String, anisetteData: String,
                               xcodeVersion: String) async throws -> (ALTAccount, ALTAppleAPISession) {
        Harness.events.append("savedToken")
        if tokenRejects { throw HarnessError.rejected }
        return (ALTAccount(appleID: "saved@example.invalid", identifier: adsid),
                ALTAppleAPISession(dsid: adsid, authToken: "verified-token"))
    }
    func signIn(appleID: String, password: String, anisetteData: String, xcodeVersion: String,
                accountRepairHandler: (URL, String) async -> Void,
                verificationHandler: (String) async throws -> String) async throws -> (ALTAccount, ALTAppleAPISession) {
        Harness.events.append("password:" + appleID)
        if passwordRejects { throw HarnessError.rejected }
        let dsid = overrideDSID ?? (appleID == "saved@example.invalid" ? "saved-dsid" : "manual-dsid")
        return (ALTAccount(appleID: appleID, identifier: dsid),
                ALTAppleAPISession(dsid: dsid, authToken: "verified-token"))
    }
}
final class SignInOperation {
    var v3RequireInteractiveCredentials = false
    var v3ForceProvisioningRetry = false
    var v3ReauthenticateAppleID: String?
    var v3ReauthenticationIdentityStamp: String?
    var isCancelled = false
    var requiresPostAuthFlow = false
    var appleIDEmailAddress: String?
    let signInHandler = Handler()
    func getAnisetteData() async throws -> String {
        Harness.events.append("anisette")
        if Harness.exercisePairGuard {
            return try await v3AuthenticationPhase(.anisetteFetch) {
                _ = try Keychain.shared.resolveAnisetteSnapshot()
                return "fixture"
            }
        }
        return "fixture"
    }
    func provisioningLoop(account: ALTAccount, session: ALTAppleAPISession,
                           reportProgress: @escaping @Sendable (Int64) -> Void) async throws -> SignInResult {
        Harness.events.append("provision")
        return SignInResult(account: account, session: session)
    }
    func verboseLog(_ message: String) {}
    func debugLog(_ message: String) {}
    __PRODUCTION_OPERATION_METHODS__
    func run() async throws -> SignInResult { try await startAuthentication(reportProgress: { _ in }) }
}
@main struct ExplicitSignInHarness {
    static func main() async throws {
        LCEmbeddedSharedKeychain.transactionOverride = { try $0() }
        Keychain.shared = Keychain(LCEmbeddedSharedKeychain.makeClient())
        let scenario = CommandLine.arguments[1]
        let group = Store.keychainGroup
        Store.data[group] = ["appleIDEmailAddress": Data("saved@example.invalid".utf8),
            "appleIDPassword": Data("saved-password".utf8), "appleIDAdsid": Data("saved-dsid".utf8),
            "appleIDXcodeToken": Data("saved-token".utf8), "unrelatedCertificate": Data("keep-certificate".utf8)]
        let operation = SignInOperation()
        let auth = AuthManager.shared
        let isDefault = scenario.hasPrefix("default_")
        let isReauthentication = scenario.contains("reauthentication")
        operation.v3RequireInteractiveCredentials = !isDefault && !isReauthentication
        Harness.expectPristineBeforeCredentials = !isDefault
        if scenario == "default_password" || scenario == "default_password_pair_blocked" {
            Store.data[group]?.removeValue(forKey: "appleIDAdsid")
            Store.data[group]?.removeValue(forKey: "appleIDXcodeToken")
        }
        if scenario == "default_expired_token_password" || scenario == "manual_saved_invalid" {
            auth.tokenRejects = true
        }
        if scenario == "manual_different_owner" { operation.signInHandler.appleID = "manual@example.invalid" }
        if scenario == "manual_cancel" { operation.signInHandler.cancelImmediately = true }
        if scenario == "manual_failed_then_cancel" {
            operation.signInHandler.appleID = "manual@example.invalid"
            operation.signInHandler.cancelAfterFirst = true
            auth.passwordRejects = true
        }
        if isReauthentication {
            try Keychain.shared.writeAuthenticationCredentials(appleID: "saved@example.invalid",
                password: "saved-password", dsid: "saved-dsid", authToken: "saved-token")
            operation.v3ReauthenticateAppleID = "saved@example.invalid"
            operation.v3ReauthenticationIdentityStamp = auth.v3IdentityStamp
            if scenario == "reauthentication_wrong_owner" { operation.signInHandler.appleID = "manual@example.invalid" }
            if scenario == "reauthentication_wrong_dsid" { auth.overrideDSID = "wrong-dsid" }
        }
        if scenario.hasSuffix("pair_blocked") {
            Harness.exercisePairGuard = true
            Store.data[group]?["adiPb"] = Data(Data("synthetic-orphan-blob".utf8).base64EncodedString().utf8)
            Store.data[group]?[LCSharedKeychainMigration.marker] = LCSharedKeychainMigration.ready
        }
        Harness.before = Store.data
        Store.writes = 0
        if scenario.hasSuffix("pair_blocked") {
            do { _ = try await operation.run(); preconditionFailure("unsafe Anisette state continued") }
            catch {
                guard let phase = error as? V3AuthenticationPhaseError else { fatalError("lost Anisette phase") }
                precondition(phase.underlying is LCAnisettePairError)
            }
            precondition(Store.data == Harness.before && Store.writes == 0)
            if isDefault {
                precondition(Harness.events == ["anisette"] && operation.signInHandler.calls == 0,
                    "blocked saved route replayed password or requested credentials")
            } else {
                precondition(Harness.events == ["credentials", "anisette", "attemptFailure"] && operation.signInHandler.calls == 1,
                    "blocked explicit route requested credentials again")
            }
        } else if scenario == "manual_cancel" || scenario == "manual_failed_then_cancel" {
            do { _ = try await operation.run(); preconditionFailure("cancelled input completed") }
            catch is CancellationError {}
            precondition(Store.data == Harness.before && Store.writes == 0)
            precondition(!Harness.events.contains("savedToken"))
            if scenario == "manual_cancel" { precondition(Harness.events == ["credentials"]) }
            else {
                precondition(Harness.events == ["credentials", "anisette", "password:manual@example.invalid",
                    "attemptFailure", "credentials"], "failed explicit credentials fell back to saved account")
            }
        } else if scenario.hasPrefix("reauthentication_wrong_") {
            do { _ = try await operation.run(); preconditionFailure("wrong reauthentication identity accepted") }
            catch is V3ProvisioningReauthenticationIdentityError {}
            precondition(Store.data == Harness.before && Store.writes == 0)
            precondition(!Harness.events.contains("savedToken"))
        } else {
            let result = try await operation.run()
            if isDefault {
                precondition(operation.signInHandler.calls == 0)
                let expected: [String]
                if scenario == "default_token" { expected = ["anisette", "savedToken", "provision"] }
                else if scenario == "default_password" { expected = ["anisette", "password:saved@example.invalid", "provision"] }
                else { expected = ["anisette", "savedToken", "anisette", "password:saved@example.invalid", "provision"] }
                precondition(Harness.events == expected, "default saved authentication behavior changed")
            } else {
                precondition(Harness.events == ["credentials", "anisette", "password:" + operation.signInHandler.appleID, "provision"])
                precondition(result.account.appleID == operation.signInHandler.appleID)
                if scenario == "manual_repeated" {
                    Harness.events = []; Harness.before = Store.data; Store.writes = 0
                    let repeated = SignInOperation(); repeated.v3RequireInteractiveCredentials = true
                    _ = try await repeated.run()
                    precondition(repeated.signInHandler.calls == 1 && Harness.events.first == "credentials")
                    precondition(!Harness.events.contains("savedToken"), "repeated manual sign-in reused cached credentials")
                }
            }
            precondition(Store.data[group]?["unrelatedCertificate"] == Data("keep-certificate".utf8))
        }
        print("V3_EXPLICIT_SIGNIN_PASS " + scenario)
    }
}
