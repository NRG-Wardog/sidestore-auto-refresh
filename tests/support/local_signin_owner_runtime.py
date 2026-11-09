"""Current ordinary sign-in scope closure for synthetic Apple/Keychain harnesses.

The tested selection and commit methods are exact owner declarations. Optional
remote, diagnostic-sequence, and owned-native attempts are never selected by
these local-route scenarios. Their unsupported IO adapters fail loudly rather
than manufacturing a successful operation or silently falling back.
"""
from tests.owner_source import owner_source
from tests.support.product_sources import declaration_end


def declaration(source, anchor):
    start = source.index(anchor)
    return source[start:declaration_end(source, start)]


def members():
    operation = owner_source('SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift', 'SideStore')
    return FIELDS + '\n' + '\n'.join(declaration(operation, anchor) for anchor in (
        '    private func v3RemoteContextSnapshot()',
        '    private func v3OwnedODAContextSnapshot()',
        '    private func v3WithOwnedODA<T>',
        '    private func v3ResolveAnisetteRouteAfterCredentials()',
        '    private func v3OwnedODASourceUnchanged()',
    ))


def dependencies():
    sequence = owner_source('SideStore/Core/Anisette/V3DiagnosticProviderSequence.swift', 'SideStore')
    return '\n'.join(declaration(sequence, anchor) for anchor in (
        'enum V3DiagnosticProvider:', 'enum V3DiagnosticProviderRoute {',
        'enum V3OwnedODASourceEligibility:', 'actor V3DiagnosticAttemptClock {',
    )) + '\n' + IO


FIELDS = r'''
    let loginDiagnosticTrace = LoginDiagnosticTrace()
    let authenticationDiagnosticTrace = AuthenticationDiagnosticTrace()
    let v3RemoteAnisetteLock = NSLock()
    var v3RemoteAnisetteContext: FixtureAttemptContext? = nil
    var v3RemoteAnisetteAttempt: FixtureRemoteAttempt? = nil
    var v3OwnedODAAttempt: FixtureOwnedAttempt? = nil
    var v3OwnedODAIdentityStamp: String? = nil
    var v3OwnedODASource: String? = nil
    var v3DiagnosticIdentityStamp: String? = nil
    var v3DiagnosticCommittedIdentityStamp: String? = nil
    var v3DiagnosticAttemptClock: V3DiagnosticAttemptClock? = nil
    var v3DiagnosticProvider: V3DiagnosticProvider? = nil
    var v3AnisetteRouteResolved = false
    var v3RemoteAnisetteSelection: (@MainActor @Sendable () throws -> Bool)? = nil
    // Ordinary Handler has no diagnostic plan. Selecting one is unsupported in
    // this fixture and must never fabricate an authentication result.
    func diagnosticAuthentication(appleID: String, password: String,
        plan: V3DiagnosticProviderPlan, handler: V3HeadlessAuthHandler) async throws
        -> (account: ALTAccount, session: ALTAppleAPISession) {
        fatalError("unexpected diagnostic sequence in ordinary sign-in fixture")
    }
'''

IO = r'''
// External attempt API shapes: ordinary local-route tests never instantiate one.
final class FixtureAttemptContext: @unchecked Sendable {
    func checkActive() throws { fatalError("unexpected alternate-route context") }
    func invalidate() { fatalError("unexpected alternate-route context") }
    func stagedBlobForCommit() throws -> Data? { fatalError("unexpected owned blob acquisition") }
}
typealias ILoaderRemoteAuthenticationContext = FixtureAttemptContext
typealias OwnedODAAuthenticationContext = FixtureAttemptContext
enum ILoaderRemoteAuthentication { @TaskLocal static var current: FixtureAttemptContext? }
enum OwnedODAAuthentication { @TaskLocal static var current: FixtureAttemptContext? }
enum ILoaderRemoteError: Error { case consentRequired }
enum OwnedODAAuthenticationError: Error { case revoked }
struct V3DiagnosticSequenceStopped: Error {}
final class FixtureAuthorization {
    func adoptOwnedTransition(appleID: String, dsid: String, token: String) {
        fatalError("unexpected remote transition")
    }
}
struct FixtureRemoteHandoff {}
final class FixtureRemoteAttempt {
    func handoff() async throws -> FixtureRemoteHandoff { fatalError("unexpected remote identity handoff") }
    let context = FixtureAttemptContext()
    let authorization: FixtureAuthorization? = nil
    init() { fatalError("unexpected remote attempt creation") }
}
final class FixtureOwnedAttempt {
    let context = FixtureAttemptContext()
    func commitStagedBlob(_ blob: Data?, reservedStamp: String,
        isCancelled: () -> Bool, commitCredentials: () throws -> Void,
        installSession: () -> Void) throws { fatalError("unexpected owned-native commit") }
}
enum V3RemoteIdentityContinuity {
    static func manualAttempt() throws -> FixtureRemoteAttempt { fatalError("unexpected remote route selection") }
}
struct V3DiagnosticProviderPlan {}
class V3HeadlessAuthHandler {
    var diagnosticProviderPlan: V3DiagnosticProviderPlan? { get async { fatalError("unexpected diagnostic plan") } }
}
extension UserDefaults {
    var useOnDeviceAnisette: Bool { false }
    var menuAnisetteList: String { "" }
}
enum AnisetteServersManager { static let defaultSource = "https://example.invalid/unused" }
// Observation sinks preserve results and errors but are not diagnostic tests.
enum FixtureLoginStage { case credentialRead, credentialCommit, sessionCommit, accountLookup, appleAuthentication, uiCredentials, login, preconditions, credentialRoute, provisioning }
enum FixtureLoginReason { case notRequired, retry, cached, fresh }
enum FixtureLoginOutcome { case succeeded }
final class FixtureLoginSpan { func finish(_ outcome: FixtureLoginOutcome, reason: FixtureLoginReason) {} }
final class AuthenticationDiagnosticTrace: @unchecked Sendable { @TaskLocal static var current: AuthenticationDiagnosticTrace? }
final class LoginDiagnosticTrace: @unchecked Sendable {
    @TaskLocal static var current: LoginDiagnosticTrace?
    func begin(_ stage: FixtureLoginStage) -> FixtureLoginSpan { FixtureLoginSpan() }
    func skip(_ stage: FixtureLoginStage, reason: FixtureLoginReason) {}
}
func v3LoginStage<T>(_ stage: FixtureLoginStage, perform: () async throws -> T) async rethrows -> T { try await perform() }
func v3LoginStageSync<T>(_ stage: FixtureLoginStage, perform: () throws -> T) rethrows -> T { try perform() }
// These methods belong only to unselected diagnostic/native scopes. Any future
// scenario entering them needs real ownership support, not a successful fake.
extension AuthManager {
    func v3BeginIdentityTransition(ifCurrent stamp: String) -> Bool { fatalError("unexpected diagnostic transition") }
    func v3BeginOwnedODAIdentityTransition(ifCurrent stamp: String) -> String? { fatalError("unexpected owned transition") }
    func v3CompleteOwnedODAIdentityTransition(_ stamp: String) -> String? { fatalError("unexpected owned transition") }
    func v3CompleteIdentityTransitionReturningStamp() -> String { fatalError("unexpected diagnostic transition") }
    func v3InstallSessionIfCurrent(_ session: ALTAppleAPISession, capturedStamp: String) -> Bool { fatalError("unexpected diagnostic session commit") }
}
'''
