"""Current admission/identity decisions for the ordinary local-route race fixture.

Remote transport and native ODA contexts below are external IO and fail loudly
if selected. No admission, lease, coalescing, record or credential-binding
algorithm is replaced by a fixture implementation.
"""
from tests.owner_source import owner_source
from tests.support.product_sources import declaration_end


def declaration(source, anchor):
    start = source.index(anchor)
    return source[start:declaration_end(source, start)]


def source():
    keychain = owner_source('AltStore/Core/Components/Keychain.swift', 'SideStore')
    provider = owner_source('SideStore/Core/Anisette/AnisetteProvider.swift', 'SideStore')
    records = keychain.split('// V3_REMOTE_IDENTITY_RECORD_CORE_BEGIN', 1)[1].split('// V3_REMOTE_IDENTITY_RECORD_CORE_END', 1)[0]
    remote = provider.split('// V3_REMOTE_IDENTITY_RUNTIME_BEGIN', 1)[1].split('// V3_REMOTE_IDENTITY_RUNTIME_END', 1)[0]
    remote += '\n' + declaration(provider, 'private actor ILoaderAttemptAnisetteSource {')
    return 'import CryptoKit\n' + records + remote + EXTERNAL_IO


EXTERNAL_IO = r'''
// No library/network acquisition is possible in these local-route races.
enum ILoaderRemoteError: Error { case consentRequired, invalidState, invalidProvider }
enum FixtureEndpoint: String { case macJkcoxson = "https://mac-ani.jkcoxson.com" }
struct FixtureProfile { let endpoint: FixtureEndpoint }
struct ILoaderRemoteState {
    let profile: FixtureProfile
    let identity = Data(repeating: 0, count: 16)
    let adiPB: String? = nil
    init(profile: FixtureProfile) { self.profile = profile }
    init(restoring profile: FixtureProfile, identity: Data, adiPB: String) throws {
        fatalError("unexpected remote identity restoration")
    }
}
final class ILoaderRemoteAnisetteClient: @unchecked Sendable {
    init(endpoint: FixtureEndpoint) {}
    func invalidate() {}
    func fetchProfile() async throws -> FixtureProfile { fatalError("unexpected remote network") }
    func fetch(state: ILoaderRemoteState, sim: String?) async throws -> (ALTAnisetteData, ILoaderRemoteState) {
        fatalError("unexpected remote network")
    }
}
final class ILoaderRemoteAuthenticationContext: @unchecked Sendable {
    init(endpoint: FixtureEndpoint, canceller: @escaping @Sendable () -> Void,
         isAuthorized: @escaping @Sendable () -> Bool,
         stateSnapshot: @escaping @Sendable () async throws -> ILoaderRemoteState,
         fetcher: @escaping @Sendable (String?) async throws -> ALTAnisetteData) {
        fatalError("unexpected remote route acquisition")
    }
    func checkActive() throws { fatalError("unexpected remote route") }
    func invalidate() { fatalError("unexpected remote route") }
    func identityState() async throws -> ILoaderRemoteState { fatalError("unexpected remote route") }
}
enum ILoaderRemoteAuthentication { @TaskLocal static var current: ILoaderRemoteAuthenticationContext? }
final class OwnedODAAuthenticationContext: @unchecked Sendable {
    func checkActive() throws { fatalError("unexpected owned ODA route") }
    func invalidate() { fatalError("unexpected owned ODA route") }
}
enum OwnedODAAuthentication { @TaskLocal static var current: OwnedODAAuthenticationContext? }
enum OwnedODAAuthenticationError: Error { case conflictingRoutes }
enum SideSign { enum AnisetteError: Error { case providerNotReady(String) } }
// The execute wrapper only retains an optional context; these fixtures do not
// construct an attempt. A future accidental construction is never successful.
final class V3OwnedODAAttempt {
    let context: OwnedODAAuthenticationContext
    init() { fatalError("unexpected owned ODA attempt creation") }
}
// Observation sinks do not choose a route or alter an error/result.
enum FixtureStage { case login, preconditions, credentialRoute, uiCredentials, appleAuthentication, credentialRead, sessionCommit, primaryProvider }
enum FixtureOutcome { case succeeded }
enum FixtureReason { case retry, notRequired, cached, fresh }
final class FixtureSpan { func finish(_ outcome: FixtureOutcome, reason: FixtureReason) {} }
final class LoginDiagnosticTrace: @unchecked Sendable {
    @TaskLocal static var current: LoginDiagnosticTrace?
    func begin(_ stage: FixtureStage) -> FixtureSpan { FixtureSpan() }
    func skip(_ stage: FixtureStage, reason: FixtureReason) {}
}
final class AuthenticationDiagnosticTrace: @unchecked Sendable {
    @TaskLocal static var current: AuthenticationDiagnosticTrace?
}
func v3LoginStage<T>(_ stage: FixtureStage, perform: () async throws -> T) async rethrows -> T { try await perform() }
func v3LoginStageSync<T>(_ stage: FixtureStage, perform: () throws -> T) rethrows -> T { try perform() }
enum V3DiagnosticProvider { case onDevice, remoteServer, remoteMac }
enum V3DiagnosticProviderRoute { @TaskLocal static var current: V3DiagnosticProvider? }
'''
