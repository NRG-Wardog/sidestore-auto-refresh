import Foundation

// Platform parsers/storage are controlled; production validator branches, actor
// cache, observation decoder/commit and setup completion are extracted verbatim.
public struct ALTX509Certificate: Sendable {
    public var serialNumber: String
    public var expiryDate: Date
    public var requesterEmail: String? = "owner@example.test"
    public var machineName: String? = "SideStore fixture"
    public init?(data: Data) {
        guard let serial = String(data: data, encoding: .utf8), !serial.isEmpty else { return nil }
        serialNumber = serial
        expiryDate = .distantFuture
    }
}
public struct ALTAccount: Sendable { public var appleID: String }
public enum ALTTeamType: Sendable { case free, individual }
public struct ALTTeam: Sendable {
    public var identifier: String
    public var type: ALTTeamType
    public var account: ALTAccount?
}
public struct ALTProvisioningProfile {
    public var expirationDate: Date
    public var teamIdentifier: String
    public var certificates: [ALTX509Certificate]
}
final class NativeFixture: @unchecked Sendable {
    static let shared = NativeFixture()
    var bundleURL = URL(fileURLWithPath: "/not-observed.app")
    var profile: ALTProvisioningProfile?
    var runningCertificate: ALTX509Certificate?
    var parseCount = 0
}
public final class CertificateManager: @unchecked Sendable {
    public static let shared = CertificateManager()
    public func getSigningCertificate(at url: URL) -> ALTX509Certificate? {
        precondition(url == NativeFixture.shared.bundleURL)
        precondition(!Thread.isMainThread, "Mach-O reads must not run on MainActor's main thread")
        NativeFixture.shared.parseCount += 1
        return NativeFixture.shared.runningCertificate
    }
}
public struct ALTApplication {
    public init?(fileURL: URL) { guard fileURL == NativeFixture.shared.bundleURL else { return nil } }
    public var provisioningProfile: ALTProvisioningProfile? { NativeFixture.shared.profile }
}
extension Bundle {
    enum Info { static var activeBundleURL: URL { NativeFixture.shared.bundleURL } }
}

// PRODUCTION_DECLARATIONS

@MainActor
final class ObservationHost {
    var identityStamp: String? = "process-A:0"
    var hostSigningContext: String?
    var installedHostSigning = V3HostSigningObservation()
    var revision: UInt64 = 1
    func isSetupFactRevisionCurrent(_ value: UInt64) -> Bool { revision == value }
    // PRODUCTION_OBSERVATION_COMMIT
}

@main
struct HostSigningReadinessHarness {
    @MainActor
    static func main() async throws {
        let fixture = NativeFixture.shared
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let app = root.appendingPathComponent("Fixture.app")
        try FileManager.default.createDirectory(at: app, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let executable = app.appendingPathComponent("Fixture")
        let profileURL = app.appendingPathComponent("embedded.mobileprovision")
        try Data("binary A".utf8).write(to: executable)
        try Data("profile A".utf8).write(to: profileURL)
        let info: [String: Any] = ["CFBundleIdentifier": "com.example.fixture", "CFBundleExecutable": "Fixture",
                                   "CFBundlePackageType": "APPL", "CFBundleVersion": "1"]
        try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
            .write(to: app.appendingPathComponent("Info.plist"))
        fixture.bundleURL = app
        let certA = ALTX509Certificate(data: Data("certificate-A".utf8))!
        let certB = ALTX509Certificate(data: Data("certificate-B".utf8))!
        let free = ALTTeam(identifier: "team-A", type: .free, account: ALTAccount(appleID: "owner@example.test"))
        let paid = ALTTeam(identifier: "team-A", type: .individual, account: free.account)
        func context(_ char: Character, certificate: String = "certificate-A", team: ALTTeam? = nil) -> V3HostSigningContext {
            V3HostSigningContext(digest: String(repeating: String(char), count: 64),
                certificateDER: Data(certificate.utf8), team: team ?? free)
        }
        let a = context("a"), b = context("b", certificate: "certificate-B")
        fixture.runningCertificate = certA
        fixture.profile = ALTProvisioningProfile(expirationDate: .distantFuture,
            teamIdentifier: free.identifier, certificates: [certA])
        let reader = V3InstalledHostSigningReader()
        let now = Date()
        func expect(_ context: V3HostSigningContext, _ state: V3HostSigningState,
                    using observer: V3InstalledHostSigningReader? = nil) async {
            let value = await (observer ?? reader).observe(context, now: now)
            precondition(value.state == state)
        }
        let first = await reader.observe(a, now: now)
        precondition(first.currentState(context: a.digest, now: now) == .compatible)
        precondition(fixture.parseCount == 1, "The observed leaf must avoid a second validator parse")
        let repeated = await reader.observe(a, now: now.addingTimeInterval(10))
        precondition(repeated == first && fixture.parseCount == 1)
        _ = await reader.observe(a, now: now.addingTimeInterval(61))
        precondition(fixture.parseCount == 2, "The parser cache is bounded to 60 seconds")
        // The observation's age never outlives the pre-existing setup-fact cadence.
        precondition(first.currentState(context: a.digest, now: now.addingTimeInterval(300)) == .unknown)
        precondition(first.currentState(context: b.digest, now: now) == .unknown)
        precondition(first.currentState(context: a.digest, now: now.addingTimeInterval(-1)) == .unknown)
        let changedCertificate = await reader.observe(b, now: now)
        precondition(changedCertificate.state == .refreshRequired)
        let paidMismatch = await reader.observe(context("c", certificate: "certificate-B", team: paid), now: now)
        precondition(paidMismatch.state == .paidSignerUnverified)
        precondition(paidMismatch.state.detail.contains("not known to be required"))
        var otherTeam = free; otherTeam.identifier = "team-B"
        await expect(context("d", team: otherTeam), .refreshRequired)
        var otherAccountTeam = otherTeam
        otherAccountTeam.account = ALTAccount(appleID: "different@example.test")
        await expect(context("e", team: otherAccountTeam), .refreshRequired)
        fixture.profile?.expirationDate = .distantPast
        let expired = await reader.observe(context("f"), now: now)
        precondition(expired.currentState(context: context("f").digest, now: now) == .refreshRequired)
        fixture.profile?.expirationDate = .distantFuture
        fixture.runningCertificate = nil
        await expect(context("1"), .unknown)
        fixture.runningCertificate = certA
        fixture.profile = nil
        await expect(context("2"), .unknown)
        fixture.profile = ALTProvisioningProfile(expirationDate: now.addingTimeInterval(30),
            teamIdentifier: free.identifier, certificates: [certA])
        let soon = await reader.observe(context("3"), now: now)
        precondition(soon.validUntil == now.addingTimeInterval(30))
        fixture.profile?.expirationDate = .distantFuture
        fixture.runningCertificate?.expiryDate = now.addingTimeInterval(20)
        let leafSoon = await reader.observe(context("4"), now: now)
        precondition(leafSoon.validUntil == now.addingTimeInterval(20))
        fixture.runningCertificate = certA
        let beforeFileChange = await reader.observe(a, now: now)
        precondition(beforeFileChange.state == .compatible)
        let beforeCount = fixture.parseCount
        try Data("binary B with changed file identity".utf8).write(to: executable)
        _ = await reader.observe(a, now: now.addingTimeInterval(1))
        precondition(fixture.parseCount == beforeCount + 1, "Changing installed binary invalidates even a warm cache")
        try FileManager.default.removeItem(at: profileURL)
        await expect(a, .unknown)
        try Data("profile B".utf8).write(to: profileURL)
        await expect(a, .compatible)
        // A process restart discards only the transient observer cache.
        let restarted = V3InstalledHostSigningReader()
        await expect(a, .compatible, using: restarted)

        let host = ObservationHost()
        host.hostSigningContext = a.digest
        let reply: [String: Any] = ["identityStamp": "process-A:0", "hostSigning": first.wire]
        host.recordInstalledHostSigning(reply, revision: 1)
        precondition(host.installedHostSigning == first)
        host.hostSigningContext = b.digest
        host.recordInstalledHostSigning(reply, revision: 1)
        precondition(host.installedHostSigning.state == .unknown, "Old health cannot authorize new certificate")
        host.hostSigningContext = a.digest
        host.identityStamp = "process-B:0"
        host.recordInstalledHostSigning(reply, revision: 1)
        precondition(host.installedHostSigning.state == .unknown, "Old process result cannot commit after restart")
        // An auth event may observe the new identity before the full status
        // snapshot reaches the host. Reject then; accept after authoritative
        // snapshot correlation catches up, without a service restart or refresh.
        let next = V3HostSigningObservation(context: b.digest, state: .compatible,
            checkedAt: now, validUntil: now.addingTimeInterval(300))
        let nextReply: [String: Any] = ["identityStamp": "process-C:0", "hostSigning": next.wire]
        host.recordInstalledHostSigning(nextReply, revision: 1)
        precondition(host.installedHostSigning.state == .unknown)
        host.identityStamp = "process-C:0"
        host.hostSigningContext = b.digest
        host.recordInstalledHostSigning(nextReply, revision: 1)
        precondition(host.installedHostSigning == next)
        host.identityStamp = "process-A:0"
        host.hostSigningContext = a.digest
        host.installedHostSigning = V3HostSigningObservation()
        host.revision = 2
        host.recordInstalledHostSigning(reply, revision: 1)
        precondition(host.installedHostSigning.state == .unknown, "Mutation invalidates pending observation")
        host.recordInstalledHostSigning(reply, revision: 2)
        precondition(host.installedHostSigning == first)
        host.recordInstalledHostSigning([:], revision: 2)
        precondition(host.installedHostSigning.state == .unknown)
        host.identityStamp = nil
        host.recordInstalledHostSigning(["hostSigning": first.wire], revision: 2)
        precondition(host.installedHostSigning.state == .unknown, "Missing identity cannot match a missing stamp")
        var malformed = first.wire; malformed["state"] = "forged"
        precondition(V3HostSigningObservation.decode(malformed) == nil)
        malformed = first.wire; malformed["validUntil"] = now.addingTimeInterval(301)
        precondition(V3HostSigningObservation.decode(malformed) == nil)
        let plist = try PropertyListSerialization.data(fromPropertyList: first.wire, format: .binary, options: 0)
        let decodedPlist = try PropertyListSerialization.propertyList(from: plist, format: nil)
        precondition(V3HostSigningObservation.decode(decodedPlist) == first)

        var inputs = V3SetupCompletionInputs(accountComplete: true, pairingSatisfied: true,
            jitlessRequired: true, jitlessComplete: true, networkComplete: true, tunnelComplete: true,
            backgroundRefreshAvailable: true, scheduleEnabled: true, verifiedRefreshPresent: true,
            installedHostSigningCompatible: false)
        precondition(inputs.outstanding() == [.installedHostSigning],
            "Successful history and a matching imported JIT-Less copy cannot prove installed-host compatibility")
        inputs.installedHostSigningCompatible = true
        precondition(inputs.isComplete)
        inputs.installedHostSigningCompatible = false
        precondition(!inputs.isComplete, "The same Setup instance must become incomplete after signer change")
        precondition(inputs.verifiedRefreshPresent, "Historical refresh success is not rewritten or erased")
        _ = certB
        print("V3_HOST_SIGNING_READINESS_PASS")
    }
}
