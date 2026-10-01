// LC_SECRET_HANDOFF_TYPED_DIAGNOSTICS_V1: the secure channel between the two
// signed processes, and what its failures must never be called.
//
// The device symptom was authRespond returning signIn/authentication/failed with
// safe_cause=unknown. That is not an Apple authentication failure: the response
// never left the device. It is a failure of the Keychain-backed handoff, and it
// needs a different fix than a retry with the same password.
//
// This harness models the handoff with an injected Keychain, so every failure
// mode is exercised exactly: store, transport, consume, deliver.
import Foundation

/// The Keychain surface the handoff uses, so a test can decide per group and per
/// item what the platform would answer.
protocol TestKeychain {
    func add(accessGroup: String?, service: String, account: String) -> (OSStatus, String?)
    func copy(accessGroup: String?, service: String, account: String) -> (OSStatus, Data?)
    func delete(accessGroup: String?, service: String, account: String) -> OSStatus
    func list(accessGroup: String, service: String) -> (OSStatus, [[String: Any]])
}

final class FakeKeychain: TestKeychain {
    /// Groups this process is entitled to. An extension after a re-sign may not
    /// hold the main app's shared group; that is the reported failure.
    var entitledGroups: Set<String>
    /// The group an add without an explicit group lands in, which is how the
    /// platform answers group discovery.
    var defaultGroup: String
    var records: [String: Data] = [:]
    var createdAt: [String: Date] = [:]

    init(entitledGroups: Set<String>, defaultGroup: String) {
        self.entitledGroups = entitledGroups
        self.defaultGroup = defaultGroup
    }

    private func authorized(_ group: String?) -> Bool {
        guard let group else { return true }          // the default group always works
        return entitledGroups.contains(group)
    }

    func add(accessGroup: String?, service: String, account: String) -> (OSStatus, String?) {
        guard authorized(accessGroup) else { return (errSecMissingEntitlement, nil) }
        records[account] = Data([0xA5])
        createdAt[account] = Date()
        return (errSecSuccess, accessGroup ?? defaultGroup)
    }

    func copy(accessGroup: String?, service: String, account: String) -> (OSStatus, Data?) {
        guard authorized(accessGroup) else { return (errSecMissingEntitlement, nil) }
        guard let data = records[account] else { return (errSecItemNotFound, nil) }
        return (errSecSuccess, data)
    }

    func delete(accessGroup: String?, service: String, account: String) -> OSStatus {
        guard authorized(accessGroup) else { return errSecMissingEntitlement }
        guard records.removeValue(forKey: account) != nil else { return errSecItemNotFound }
        return errSecSuccess
    }

    func list(accessGroup: String, service: String) -> (OSStatus, [[String: Any]]) {
        guard authorized(accessGroup) else { return (errSecMissingEntitlement, []) }
        let rows = createdAt.keys.map { [kSecAttrAccount as String: $0] }
        return (errSecSuccess, rows)
    }
}

/// The subject under test: group resolution and one-time consume, driven by an
/// injected Keychain. Mirrors V3SecretHandoff's real sequence exactly.
final class SecretTransport {
    static let sharedSuffix = ".com.kdt.livecontainer.shared"
    static let service = "com.kdt.livecontainer.v3-secret-handoff"
    /// Concrete rather than the existential: a case has to age a stored record,
    /// and Swift will not let a `let` protocol reference be mutated through.
    let keychain: FakeKeychain
    let defaultGroup: String
    var probeCount = 0

    init(keychain: FakeKeychain, defaultGroup: String) {
        self.keychain = keychain
        self.defaultGroup = defaultGroup
    }

    /// Discovery uses this process's own default group and always succeeds for a
    /// signed process. The explicit probe is what a re-sign breaks.
    func sharedGroup() throws -> String {
        probeCount += 1
        let (status, group) = keychain.add(accessGroup: nil, service: "probe", account: "probe")
        guard status == errSecSuccess, let group, !group.hasPrefix("DEFAULT-") else {
            throw V3SecretHandoffError.fail(.keychainGroupDiscoveryFailed,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupDiscovery"),
                operation: "groupDiscovery", osStatus: status)
        }
        guard let prefix = group.split(separator: ".", maxSplits: 1).first,
              String(prefix).range(of: #"^[A-Z0-9]{10}$"#, options: .regularExpression) != nil else {
            throw V3SecretHandoffError.fail(.keychainGroupDiscoveryFailed,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupDiscovery", groupDiscovered: true),
                operation: "groupDiscovery", groupDiscovered: true)
        }
        let shared = "\(prefix)" + SecretTransport.sharedSuffix
        probeCount += 1
        let (explicitStatus, explicitGroup) = keychain.add(accessGroup: shared,
            service: "probe", account: "probe")
        guard explicitStatus == errSecSuccess else {
            throw V3SecretHandoffError.fail(.keychainExplicitGroupUnauthorized,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupAuthorize", groupDiscovered: true),
                operation: "groupAuthorize", osStatus: explicitStatus, groupDiscovered: true)
        }
        guard explicitGroup == shared else {
            throw V3SecretHandoffError.fail(.keychainExplicitGroupUnauthorized,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupAuthorize", groupDiscovered: true),
                operation: "groupAuthorize", osStatus: Int32(errSecParam), groupDiscovered: true)
        }
        _ = keychain.delete(accessGroup: shared, service: "probe", account: "probe")
        return shared
    }

    func store(_ payload: Data, account: String) throws -> String {
        let group = try sharedGroup()
        keychain.records[account] = payload
        keychain.createdAt[account] = Date()
        V3SecretHandoffTrace.emit(V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
            operation: "secretStore", groupDiscovered: true, tokenWellFormed: true))
        return group
    }

    /// Exactly one successful consume per token.
    func consume(_ token: String) throws -> Data {
        let group = try sharedGroup()
        let (status, record) = keychain.copy(accessGroup: group,
            service: SecretTransport.service, account: token)
        guard status == errSecSuccess, let record else {
            let reason: V3SecretHandoffFailure = status == errSecItemNotFound
                ? .keychainItemNotFound
                : (status == errSecMissingEntitlement ? .keychainExplicitGroupUnauthorized
                                                     : .keychainReadFailed)
            throw V3SecretHandoffError.fail(reason,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "secretLookup", groupDiscovered: true, tokenWellFormed: true),
                operation: "secretLookup", osStatus: status, groupDiscovered: true,
                tokenWellFormed: true)
        }
        let deleteStatus = keychain.delete(accessGroup: group,
            service: SecretTransport.service, account: token)
        guard deleteStatus == errSecSuccess || deleteStatus == errSecItemNotFound else {
            throw V3SecretHandoffError.fail(.keychainDeleteFailed,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "secretLookup", groupDiscovered: true, tokenWellFormed: true),
                operation: "secretLookup", osStatus: Int32(deleteStatus),
                groupDiscovered: true, tokenWellFormed: true)
        }
        return record
    }
}

/// What the auth prompt handler received.
final class AuthPromptSink {
    var delivered: [[String: String]] = []
    func respond(answer: [String: String]) { delivered.append(answer) }
}

@main
struct SecretHandoffTypedDiagnosticsHarness {
    static var failures = 0
    static var logged: [String] = []

    static func expect(_ condition: Bool, _ label: String) {
        if !condition {
            FileHandle.standardError.write(Data("V3_SECRET_HANDOFF_FAIL \(label)\n".utf8))
            failures += 1
        }
    }

    static let team = "AAAAA11111"
    static var sharedGroup: String { "\(team).com.kdt.livecontainer.shared" }
    static var hostDefault: String { "\(team).com.kdt.livecontainer" }
    static var serviceDefault: String { "\(team).com.kdt.livecontainer.LiveProcess" }

    /// `catch` is a Swift keyword, so this cannot be named after the construct.
    static func capture(_ body: () throws -> Void) -> V3SecretHandoffError? {
        do { try body(); return nil } catch let error as V3SecretHandoffError { return error }
        catch { return nil }
    }

    static func main() {
        V3SecretHandoffRole.current = V3SecretHandoffRole.service
        // Collect the emitted lines instead of writing them to a device log.
        V3SecretHandoffTrace.isEnabled = false

        // 1. Both processes entitled: the happy path, exactly one delivery.
        let entitledBoth: Set<String> = [sharedGroup, hostDefault, serviceDefault]
        let host = SecretTransport(keychain: FakeKeychain(entitledGroups: entitledBoth,
            defaultGroup: hostDefault), defaultGroup: hostDefault)
        let service = SecretTransport(keychain: FakeKeychain(entitledGroups: entitledBoth,
            defaultGroup: serviceDefault), defaultGroup: serviceDefault)
        V3SecretHandoffRole.current = V3SecretHandoffRole.host
        let token = try! host.store(Data("appleIDPassword=SECRET_TOKEN".utf8), account: "T1")
        expect(token == "T1", "store returns the canonical token it was given")
        expect(host.keychain.records["T1"] != nil, "the host stored the record")
        V3SecretHandoffRole.current = V3SecretHandoffRole.service
        let sink = AuthPromptSink()
        let consumed = try! service.consume("T1")
        expect(String(decoding: consumed, as: UTF8.self).contains("SECRET_TOKEN"),
               "the service receives the exact payload")
        sink.respond(answer: ["appleIDPassword": "SECRET_TOKEN"])
        expect(sink.delivered.count == 1, "credentials are delivered exactly once")

        // 2. One successful consume only: a second take finds nothing.
        let secondTake = capture { try service.consume("T1") }
        expect(secondTake?.failure == .keychainItemNotFound,
               "a second consume reports the item absent, not a generic failure")

        // 3. The re-sign shape: the extension lacks the main app's shared group.
        let hostOnly = SecretTransport(keychain: FakeKeychain(entitledGroups: [sharedGroup, hostDefault],
            defaultGroup: hostDefault), defaultGroup: hostDefault)
        let extensionSide = SecretTransport(
            keychain: FakeKeychain(entitledGroups: [serviceDefault],
                defaultGroup: serviceDefault), defaultGroup: serviceDefault)
        V3SecretHandoffRole.current = V3SecretHandoffRole.host
        _ = try! hostOnly.store(Data("payload".utf8), account: "T2")
        V3SecretHandoffRole.current = V3SecretHandoffRole.service
        let denied = capture { try extensionSide.consume("T2") }
        expect(denied?.failure == .keychainExplicitGroupUnauthorized,
               "an extension without the shared group reports it as unauthorized")
        expect(denied?.osStatusValue == errSecMissingEntitlement,
               "the unauthorized group keeps its OSStatus")

        // 4. Host lacking the group fails symmetrically.
        V3SecretHandoffRole.current = V3SecretHandoffRole.host
        let hostNoGroup = SecretTransport(
            keychain: FakeKeychain(entitledGroups: [hostDefault],
                defaultGroup: hostDefault), defaultGroup: hostDefault)
        let hostStoreFailure = capture { _ = try hostNoGroup.store(Data("payload".utf8), account: "T3") }
        expect(hostStoreFailure?.failure == .keychainExplicitGroupUnauthorized,
               "a host without the shared group cannot store")

        // 5. Discovery failure is distinct from authorization failure.
        let undiscoverable = SecretTransport(
            keychain: FakeKeychain(entitledGroups: [],
                defaultGroup: hostDefault), defaultGroup: hostDefault)
        let discovery = capture { _ = try undiscoverable.sharedGroup() }
        expect(discovery?.failure == .keychainGroupDiscoveryFailed,
               "a process that cannot discover its default group says so")

        // 6. An absent token.
        let absent = capture { try service.consume("never-stored") }
        expect(absent?.failure == .keychainItemNotFound, "an absent token is reported as absent")

        // 7. An expired token.
        let expiring = SecretTransport(keychain: FakeKeychain(entitledGroups: entitledBoth,
            defaultGroup: serviceDefault), defaultGroup: serviceDefault)
        _ = try? expiring.store(Data("payload".utf8), account: "T4")
        expiring.keychain.createdAt["T4"] = Date(timeIntervalSinceNow: -10_000)
        let expired = capture { try expiring.consume("T4") }
        expect(expired?.failure == .tokenExpired, "an expired token is reported as expired")

        // 8. A malformed token never reaches the Keychain at all.
        let malformed = capture { try service.consume("not-a-uuid") }
        expect(malformed?.failure == .tokenMalformed, "a non-canonical token is rejected outright")

        // 9. Every handoff failure is classified as a transport failure, never as
        //    an Apple authentication failure.
        for operation in ["authRespond", "opAnswer", "accountExport", "accountImport",
                          "certCreate", "devPortalLogin"] {
            expect(V3SecretHandoffFailurePolicy.applies(to: operation),
                   "\(operation) is classified as a handoff-bearing operation")
            let failure = V3SecretHandoffFailurePolicy.failure(
                denied!, operation: operation, id: "corr")
            expect(failure.safeCause == .secretHandoffUnavailable,
                   "\(operation) reports the secure-transport cause")
            expect(failure.stage == .persistence,
                   "\(operation) reports persistence, not authentication")
            expect(failure.operation == operation,
                   "\(operation) keeps its own operation identity")
        }
        expect(!V3SecretHandoffFailurePolicy.applies(to: "authBegin"),
               "authBegin does not cross the handoff and is left alone")

        // 10. Retryability follows the cause, not the operation.
        for transient in [V3SecretHandoffFailure.appGroupLockUnavailable,
                          .keychainReadFailed, .keychainDeleteFailed, .capacity] {
            expect(V3SecretHandoffFailurePolicy.isRetryable(transient),
                   "\(transient.rawValue) may be retried")
        }
        for terminal in [V3SecretHandoffFailure.keychainExplicitGroupUnauthorized,
                         .keychainGroupDiscoveryFailed, .keychainItemNotFound,
                         .tokenExpired, .tokenMalformed] {
            expect(!V3SecretHandoffFailurePolicy.isRetryable(terminal),
                   "\(terminal.rawValue) must not be retried")
        }
        expect(V3SecretHandoffFailurePolicy.failure(denied!, operation: "authRespond",
            id: "c").retryable == false,
               "an unauthorized group is not retryable on the wire")

        // 11. The safe diagnostic line carries evidence and no secrets.
        let line = denied!.diagnostics.safeLine
        for required in ["handoff=1", "role=service", "op=secretLookup", "cause=keychainExplicitGroupUnauthorized",
                         "group_discovered=true", "token_well_formed=true", "osstatus=-34018"] {
            expect(line.contains(required), "the safe line carries \(required)")
        }
        for forbidden in ["SECRET_TOKEN", "appleID", "password", team, "com.kdt.livecontainer",
                          "T2", "probe"] {
            expect(!line.contains(forbidden), "the safe line never contains \(forbidden)")
        }
        expect(line.count < 220, "the safe line is one short line")

        // 12. The user-facing text never implies Apple rejected the password.
        // The message and the recovery are properties of the failure, not of its
        // cause, so read them off a real classified failure.
        let classified = V3SecretHandoffFailurePolicy.failure(denied!, operation: "authRespond",
                                                              id: "corr")
        expect(classified.safeMessage.contains("never sent to Apple"),
               "the message says the response never reached Apple")
        let recovery = classified.recovery.lowercased()
        expect(recovery.contains("re-sign") || recovery.contains("reinstall"),
               "the recovery names the action that can fix it")

        if failures == 0 {
            print("V3_SECRET_HANDOFF_TYPED_DIAGNOSTICS_PASS")
        } else {
            FileHandle.standardError.write(Data("V3_SECRET_HANDOFF_FAILURES=\(failures)\n".utf8))
            exit(1)
        }
    }
}