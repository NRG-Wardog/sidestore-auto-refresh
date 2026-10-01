// LC_SECRET_HANDOFF_TYPED_DIAGNOSTICS_V1: the secure channel between the two
// signed processes, and what its failures must never be called.
//
// The device symptom was authRespond returning signIn/authentication/failed with
// safe_cause=unknown. That is not an Apple authentication failure: the response
// never left the device. It is a failure of the Keychain-backed handoff, and it
// needs a different fix than another attempt with the same password.
//
// One device Keychain is shared by both processes. Entitlements and the default
// group are per-process, because that is where they live: the item is in the
// same place and the access to it is not. That asymmetry is exactly what a
// re-sign produces when it grants the shared group to the main app only.
import Foundation
import Security

/// The device Keychain: one place both processes read and write.
final class DeviceKeychain {
    var records: [String: Data] = [:]
    var createdAt: [String: Date] = [:]
    static let lifetime: TimeInterval = 120

    /// Expired records are swept before a group is resolved, so an elapsed
    /// lifetime can never be read as a present item.
    func sweep(now: Date = Date()) {
        // Collect first: the two dictionaries are mutated below, so the iteration
        // must not be over the buffers being changed.
        let elapsed = createdAt.filter { now.timeIntervalSince($0.value) > DeviceKeychain.lifetime }
        for account in elapsed.keys {
            records.removeValue(forKey: account)
            createdAt.removeValue(forKey: account)
        }
    }
}

/// One signed process's view of it.
final class SecretTransport {
    static let sharedSuffix = ".com.kdt.livecontainer.shared"

    let device: DeviceKeychain
    /// Access groups this process may use.
    let entitledGroups: Set<String>
    /// The group an add with no explicit group lands in, which is how the platform
    /// answers group discovery.
    let defaultGroup: String

    init(device: DeviceKeychain, entitledGroups: Set<String>, defaultGroup: String) {
        self.device = device
        self.entitledGroups = entitledGroups
        self.defaultGroup = defaultGroup
    }

    /// An add with no explicit group lands in this process's default group, which
    /// it can only use if it holds that group. That is why a process with no
    /// Keychain entitlement cannot discover a default group at all.
    private func authorized(_ group: String?) -> Bool {
        entitledGroups.contains(group ?? defaultGroup)
    }

    private func add(_ account: String, to group: String?) -> (OSStatus, String?) {
        guard authorized(group) else { return (errSecMissingEntitlement, nil) }
        device.records[account] = Data([0xA5])
        device.createdAt[account] = Date()
        return (errSecSuccess, group ?? defaultGroup)
    }

    private func copy(_ account: String, from group: String) -> (OSStatus, Data?) {
        guard authorized(group) else { return (errSecMissingEntitlement, nil) }
        guard let data = device.records[account] else { return (errSecItemNotFound, nil) }
        return (errSecSuccess, data)
    }

    private func delete(_ account: String, from group: String) -> OSStatus {
        guard authorized(group) else { return errSecMissingEntitlement }
        guard device.records.removeValue(forKey: account) != nil else { return errSecItemNotFound }
        device.createdAt.removeValue(forKey: account)
        return errSecSuccess
    }

    /// Discovery uses this process's own default group and always succeeds for a
    /// signed process. The explicit probe is what a re-sign breaks.
    func sharedGroup() throws -> String {
        device.sweep()
        probes += 1
        let (status, group) = add(UUID().uuidString, to: nil)
        guard status == errSecSuccess, let group else {
            throw V3SecretHandoffError.fail(.keychainGroupDiscoveryFailed,
                as: nil, operation: "groupDiscovery", osStatus: status)
        }
        guard let prefix = group.split(separator: ".", maxSplits: 1).first,
              String(prefix).range(of: #"^[A-Z0-9]{10}$"#, options: .regularExpression) != nil else {
            throw V3SecretHandoffError.fail(.keychainGroupDiscoveryFailed,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupDiscovery", groupDiscovered: true),
                operation: "groupDiscovery", groupDiscovered: true)
        }
        let shared = String(prefix) + SecretTransport.sharedSuffix
        probes += 1
        let (explicitStatus, explicitGroup) = add(UUID().uuidString, to: shared)
        guard explicitStatus == errSecSuccess, explicitGroup == shared else {
            throw V3SecretHandoffError.fail(.keychainExplicitGroupUnauthorized,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupAuthorize", groupDiscovered: true),
                operation: "groupAuthorize",
                osStatus: explicitStatus == errSecSuccess ? Int32(errSecParam) : explicitStatus,
                groupDiscovered: true)
        }
        return shared
    }

    private(set) var probes = 0

    /// Returns the token, which is what the caller transports. The group is an
    /// internal detail and is never part of the return value.
    @discardableResult
    func store(_ payload: Data, token: String) throws -> String {
        _ = try sharedGroup()
        device.records[token] = payload
        device.createdAt[token] = Date()
        V3SecretHandoffTrace.emit(V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
            operation: "secretStore", groupDiscovered: true, tokenWellFormed: true))
        return token
    }

    /// Exactly one successful consume per token.
    func consume(_ token: String) throws -> Data {
        guard V3TokenValidator.isValidToken(token) else {
            throw V3SecretHandoffError.fail(.tokenMalformed, as: nil, operation: "secretLookup")
        }
        let group = try sharedGroup()
        let (status, record) = copy(token, from: group)
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
        let deleteStatus = delete(token, from: group)
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

    static func expect(_ condition: Bool, _ label: String) {
        if !condition {
            FileHandle.standardError.write(Data("V3_SECRET_HANDOFF_FAIL \(label)\n".utf8))
            failures += 1
        }
    }

    static let team = "AAAAA11111"
    static var sharedGroup: String { "\(team)\(SecretTransport.sharedSuffix)" }
    static var hostDefault: String { "\(team).com.kdt.livecontainer" }
    static var serviceDefault: String { "\(team).com.kdt.livecontainer.LiveProcess" }

    static func capture(_ body: () throws -> Void) -> V3SecretHandoffError? {
        do { try body(); return nil } catch let error as V3SecretHandoffError { return error }
        catch { return nil }
    }

    static func main() {
        V3SecretHandoffRole.current = V3SecretHandoffRole.service
        V3SecretHandoffTrace.isEnabled = false
        let both: Set<String> = [sharedGroup, hostDefault, serviceDefault]

        // 1. Both processes entitled: the happy path, exactly one delivery.
        let device = DeviceKeychain()
        let host = SecretTransport(device: device, entitledGroups: both, defaultGroup: hostDefault)
        let service = SecretTransport(device: device, entitledGroups: both, defaultGroup: serviceDefault)
        V3SecretHandoffRole.current = V3SecretHandoffRole.host
        let token = try! host.store(Data("appleIDPassword=SECRET_TOKEN".utf8),
                                    token: "11111111-2222-3333-4444-555555555555")
        expect(device.records[token] != nil, "the host wrote the record to the device")
        expect(host.probes == 2, "resolution probes the default group and the explicit group")
        V3SecretHandoffRole.current = V3SecretHandoffRole.service
        let sink = AuthPromptSink()
        let consumed = try! service.consume(token)
        expect(String(decoding: consumed, as: UTF8.self).contains("SECRET_TOKEN"),
               "the service reads back the exact payload the host wrote")
        sink.respond(answer: ["appleIDPassword": "SECRET_TOKEN"])
        expect(sink.delivered.count == 1, "credentials are delivered exactly once")

        // 2. One successful consume only: a second take finds nothing.
        let secondTake = capture { _ = try service.consume(token) }
        expect(secondTake?.failure == .keychainItemNotFound,
               "a second consume reports the item absent, not a generic failure")

        // 3. The re-sign shape: the extension lacks the main app's shared group.
        let resignDevice = DeviceKeychain()
        let hostSide = SecretTransport(device: resignDevice,
            entitledGroups: [sharedGroup, hostDefault], defaultGroup: hostDefault)
        let extensionSide = SecretTransport(device: resignDevice,
            entitledGroups: [serviceDefault], defaultGroup: serviceDefault)
        V3SecretHandoffRole.current = V3SecretHandoffRole.host
        _ = try! hostSide.store(Data("payload".utf8), token: "66666666-7777-8888-9999-000000000000")
        V3SecretHandoffRole.current = V3SecretHandoffRole.service
        let denied = capture { _ = try extensionSide.consume("66666666-7777-8888-9999-000000000000") }
        expect(denied?.failure == .keychainExplicitGroupUnauthorized,
               "an extension without the shared group reports it as unauthorized")
        expect(denied?.osStatusValue == errSecMissingEntitlement,
               "the unauthorized group keeps its OSStatus")
        expect(resignDevice.records["66666666-7777-8888-9999-000000000000"] != nil,
               "the record survives, so the host can still clear it")

        // 4. The host lacking the group fails symmetrically, at its own store.
        let hostNoGroup = SecretTransport(device: resignDevice,
            entitledGroups: [hostDefault], defaultGroup: hostDefault)
        V3SecretHandoffRole.current = V3SecretHandoffRole.host
        let hostStoreFailure = capture {
            _ = try hostNoGroup.store(Data("payload".utf8), token: "T3-not-a-uuid")
        }
        expect(hostStoreFailure?.failure == .keychainExplicitGroupUnauthorized,
               "a host without the shared group cannot store")

        V3SecretHandoffRole.current = V3SecretHandoffRole.service

        // 5. Discovery failure is distinct from authorization failure.
        let undiscoverable = SecretTransport(device: DeviceKeychain(),
            entitledGroups: [], defaultGroup: hostDefault)
        let discovery = capture { _ = try undiscoverable.sharedGroup() }
        expect(discovery?.failure == .keychainGroupDiscoveryFailed,
               "a process with no Keychain entitlement cannot discover a default group")
        expect(discovery?.osStatusValue == errSecMissingEntitlement,
               "the discovery failure keeps its OSStatus")

        // 6. An absent token.
        let absent = capture { _ = try service.consume("99999999-0000-0000-0000-000000000000") }
        expect(absent?.failure == .keychainItemNotFound, "an absent token is reported as absent")

        // 7. An expired token is swept, so it cannot be consumed as present.
        let expiring = SecretTransport(device: DeviceKeychain(), entitledGroups: both,
            defaultGroup: serviceDefault)
        let stale = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
        _ = try? expiring.store(Data("payload".utf8), token: stale)
        expiring.device.createdAt[stale] = Date(timeIntervalSinceNow: -10_000)
        let expired = capture { _ = try expiring.consume(stale) }
        expect(expired?.failure == .keychainItemNotFound,
               "an expired record is swept before it can be consumed")

        // 8. A malformed token never reaches the Keychain at all.
        let malformed = capture { _ = try service.consume("not-a-uuid") }
        expect(malformed?.failure == .tokenMalformed, "a non-canonical token is rejected outright")

        // 9. Every handoff failure is classified as a transport failure, never as
        //    an Apple authentication failure.
        for operation in ["authRespond", "opAnswer", "accountExport", "accountImport",
                          "certCreate", "devPortalLogin"] {
            expect(V3SecretHandoffFailurePolicy.applies(to: operation),
                   "\(operation) is classified as a handoff-bearing operation")
            let failure = V3SecretHandoffFailurePolicy.failure(denied!, operation: operation,
                                                              id: "corr")
            expect(failure.safeCause == .secretHandoffUnavailable,
                   "\(operation) reports the secure-transport cause")
            expect(failure.stage == .persistence,
                   "\(operation) reports persistence, not authentication")
            // CombinedFailure normalizes to a canonical identity so the UI can
            // still group these under the operation the user started.
            let canonical = ["authRespond": "signIn", "opAnswer": "command",
                             "accountExport": "command", "accountImport": "command",
                             "certCreate": "command", "devPortalLogin": "command"]
            expect(failure.operation == canonical[operation],
                   "\(operation) normalizes to \(canonical[operation] ?? "?")")
            expect(failure.code == .unavailable,
                   "\(operation) is unavailable, not busy")
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
        expect((V3SecretHandoffFailurePolicy.failure(denied!, operation: "authRespond",
            id: "c").retryable ?? true) == false,
               "an unauthorized group is not retryable on the wire")

        // 11. The safe diagnostic line carries evidence and no secrets.
        let line = denied!.diagnostics.safeLine
        for required in ["handoff=1", "role=service", "op=consume",
                         "cause=keychainExplicitGroupUnauthorized",
                         "group_discovered=true", "token_well_formed=true",
                         "osstatus=-34018"] {
            expect(line.contains(required), "the safe line carries \(required)")
        }
        for forbidden in ["SECRET_TOKEN", "appleID", "password", team,
                          "com.kdt.livecontainer", "66666666"] {
            expect(!line.contains(forbidden), "the safe line never contains \(forbidden)")
        }
        expect(line.count < 220, "the safe line is one short line")

        // 12. A named step is preserved in what is emitted, so the device log says
        //     which half of the transaction failed.
        let named = V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.service,
            operation: "secretLookup", failure: .keychainExplicitGroupUnauthorized,
            osStatus: errSecMissingEntitlement, groupDiscovered: true, tokenWellFormed: true)
        expect(named.safeLine.contains("op=secretLookup"),
               "a named step keeps its name in the safe line")
        expect(!named.safeLine.contains(team),
               "the access group name is never logged, only the boolean")

        // 13. The user-facing text never implies Apple rejected the password.
        let classified = V3SecretHandoffFailurePolicy.failure(denied!, operation: "authRespond",
                                                              id: "corr")
        expect(classified.safeMessage.contains("never sent to Apple"),
               "the message says the response never reached Apple")
        expect(!classified.safeMessage.lowercased().contains("wrong password"),
               "the message never blames the password the user typed")
        expect(classified.recovery.lowercased().contains("re-sign"),
               "the recovery names the action that can fix it")

        if failures == 0 {
            print("V3_SECRET_HANDOFF_TYPED_DIAGNOSTICS_PASS")
        } else {
            FileHandle.standardError.write(Data("V3_SECRET_HANDOFF_FAILURES=\(failures)\n".utf8))
            exit(1)
        }
    }
}