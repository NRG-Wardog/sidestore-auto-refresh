// LC_EMBEDDED_SHARED_KEYCHAIN_V1. Only injected into the combined product.
// Keep credentials in Keychain, never in UserDefaults, app-group files, or XPC.
// Host and LiveProcess already share an App Group, but not necessarily their
// DEFAULT keychain access group. Explicitly select an entitled common Keychain
// group; the App Group container identifier is not a Keychain access group.
// The app and patched LiveProcess serialize migration/sign-out with an app-group
// flock. An already-running older SideStore binary does not participate in
// that lock or write the shared tombstone, so no client-side protocol can
// exclude a credential snapshot it already took before this patch was active.

// LC_SHARED_MIGRATION_POLICY_BEGIN
struct LCLegacyKeychainItem {
    let group: String
    let key: String
    let data: Data
}

enum LCSharedKeychainMigration {
    static let marker = "LCSharedKeychainReadyV1"
    static let ready = Data("1".utf8)
    static let signedOut = Data("signed-out-v1".utf8)
    static let pending = Data("pending-v1".utf8)
    static let authKeys = ["appleIDEmailAddress", "appleIDPassword", "appleIDAdsid", "appleIDXcodeToken"]
    static let knownKeys = Set(authKeys + ["signingCertificate", "signingCertificatePassword",
        "signingCertificatePrivateKey", "signingCertificateSerialNumber", "identifier", "adiPb"])

    static func supported(_ key: String) -> Bool {
        knownKeys.contains(key) || (key.hasPrefix("importedCert_") && key.count <= 256)
    }

    static func complete(_ values: [String: Data]) -> Bool {
        func present(_ key: String) -> Bool {
            guard let data = values[key], let value = String(data: data, encoding: .utf8) else { return false }
            return !value.isEmpty
        }
        return (present("appleIDEmailAddress") && present("appleIDPassword")) ||
            (present("appleIDAdsid") && present("appleIDXcodeToken"))
    }

    static func readAuthenticationValues(read: (String) throws -> Data?) throws -> [String: Data] {
        var values: [String: Data] = [:]
        for key in authKeys {
            if let value = try read(key) { values[key] = value }
        }
        return values
    }

    /// Returns true only after a verified migration, or an existing committed
    /// namespace. No mixing credentials from different legacy access groups.
    static func prepare(group: String, items: () throws -> [LCLegacyKeychainItem],
                        read: (String) throws -> Data?, write: (String, Data) throws -> Void,
                        afterSnapshot: () throws -> Void = {}) throws -> Bool {
        let initialMarker = try read(marker)
        if initialMarker == ready {
            let current = try readAuthenticationValues(read: read)
            guard complete(current) else {
                // A ready marker is meaningful only when a complete auth route
                // is present in this namespace. Repair old partial markers by
                // failing closed, never by treating one credential item as ready.
                try write(marker, signedOut)
                guard try read(marker) == signedOut else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                }
                return false
            }
            return true
        }
        if initialMarker == signedOut { return false }
        if initialMarker == pending { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
        guard initialMarker == nil else {
            throw NSError(domain: "com.SideStore.Keychain", code: 1009)
        }
        // Existing selected-namespace values are an uncommitted recovery
        // candidate, never a destination to fill from a different namespace.
        // Apple identity verification and a compare-and-commit are required.
        if !(try readAuthenticationValues(read: read)).isEmpty { return false }
        var candidates: [String: [String: Data]] = [:]
        for item in try items() where item.group != group && supported(item.key) {
            if let previous = candidates[item.group]?[item.key], previous != item.data {
                throw NSError(domain: "LiveContainerRefresh.Configuration", code: 1008)
            }
            candidates[item.group, default: [:]][item.key] = item.data
        }
        let completeSets = candidates.values.filter { complete($0) }
        guard let source = completeSets.first else { return false }
        guard completeSets.allSatisfy({ $0 == source }) else {
            throw NSError(domain: "LiveContainerRefresh.Configuration", code: 1008)
        }
        // The hook makes snapshot/sign-out ordering testable. In production,
        // the shared file lock below serializes cooperating processes.
        try afterSnapshot()
        let currentMarker = try read(marker)
        if currentMarker == signedOut { return false }
        if currentMarker == ready {
            let current = try readAuthenticationValues(read: read)
            if complete(current) { return true }
            try write(marker, signedOut)
            guard try read(marker) == signedOut else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1009)
            }
            return false
        }
        guard currentMarker == nil else { return false }
        let refreshedItems = try items().filter { $0.group != group && supported($0.key) }
        var refreshed: [String: [String: Data]] = [:]
        for item in refreshedItems {
            if let previous = refreshed[item.group]?[item.key], previous != item.data {
                throw NSError(domain: "LiveContainerRefresh.Configuration", code: 1008)
            }
            refreshed[item.group, default: [:]][item.key] = item.data
        }
        guard refreshed.values.contains(where: { $0 == source }) else { return false }
        // Check ALL conflicts before writing anything. Partial migrations can
        // retry, but may not overwrite credentials from a different sign-in.
        for key in source.keys.sorted() {
            if let existing = try read(key), existing != source[key] {
                throw NSError(domain: "LiveContainerRefresh.Configuration", code: 1008)
            }
        }
        for key in source.keys.sorted() {
            let value = source[key]!
            if try read(key) == nil { try write(key, value) }
            guard try read(key) == value else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1009)
            }
        }
        // Readers ignore an incomplete migration until this commit marker.
        try write(marker, ready)
        guard try read(marker) == ready else {
            throw NSError(domain: "com.SideStore.Keychain", code: 1009)
        }
        let committed = try readAuthenticationValues(read: read)
        guard complete(committed) else {
            try write(marker, signedOut)
            guard try read(marker) == signedOut else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1010)
            }
            throw NSError(domain: "com.SideStore.Keychain", code: 1009)
        }
        return true
    }
}
// LC_SHARED_MIGRATION_POLICY_END

/// `AltStore/AppDelegate.swift` and this Keychain file compile in the pinned
/// SideStore target (its synchronized source groups include both AltStore and
/// SideStore). Reuse the handoff's single process-shared flock implementation.
private enum LCSharedKeychainFileLock {
    static func withLock<T>(appGroup: String?, containerRoot: URL? = nil,
                            _ operation: () throws -> T) throws -> T {
        #if canImport(Darwin)
        if let containerRoot {
            return try V3AppGroupProcessLock.withLock(containerRoot: containerRoot, operation)
        }
        // The credential migration must take the same process-shared lock as the
        // secret handoff, so it resolves the same runtime group rather than the
        // packaged name or this process's own bundle declaration.
        guard let selected = appGroup.flatMap({ $0.isEmpty ? nil : $0 }) ??
                V3SharedAppGroup.environmentGroup(),
              let shared = V3SharedAppGroup.runtimeIdentity(selectedGroup: selected) else {
            throw NSError(domain: "com.SideStore.Keychain", code: -34018)
        }
        return try V3AppGroupProcessLock.withLock(containerRoot: shared.containerRoot, operation)
        #elseif canImport(Glibc)
        guard let containerRoot else { throw NSError(domain: "com.SideStore.Keychain", code: -34018) }
        return try V3AppGroupProcessLock.withLock(containerRoot: containerRoot, operation)
        #else
        throw NSError(domain: "com.SideStore.Keychain", code: -34018)
        #endif
    }
}

struct LCEmbeddedAuthenticationSnapshot: Equatable {
    let appleIDEmailAddress: String?
    let appleIDPassword: String?
    let appleIDAdsid: String?
    let appleIDXcodeToken: String?

    var isAuthenticated: Bool {
        hasPasswordCredentials || (appleIDEmailAddress != nil && hasTokenCredentials)
    }
    var hasPasswordCredentials: Bool { appleIDEmailAddress != nil && appleIDPassword != nil }
    var hasTokenCredentials: Bool { appleIDAdsid != nil && appleIDXcodeToken != nil }
}

/// A route that may be sent to Apple for verification. Its presence is not an
/// authenticated identity or authorization to enter the developer portal.
struct LCEmbeddedAuthenticationCandidate: Equatable {
    let credentials: LCEmbeddedAuthenticationSnapshot
    let marker: Data?
    let values: [String: Data]

    func matchesVerifiedIdentity(appleID: String, dsid: String) -> Bool {
        guard !appleID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !dsid.isEmpty else { return false }
        if let original = credentials.appleIDEmailAddress,
           original.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() !=
                appleID.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() { return false }
        if let original = credentials.appleIDAdsid, original != dsid { return false }
        return true
    }
}

struct LCEmbeddedSigningCertificateSnapshot {
    let p12Data: Data
    let password: String?
}

fileprivate enum LCEmbeddedSharedKeychain {
    private static let certificateMarker = "LCSharedCertificateCommitV1"
    private static let certificatePending = Data("pending-v1".utf8)
    private static let authenticationJournal = "LCSharedAuthenticationTransactionV1"
    private static let certificateJournal = "LCSharedCertificateTransactionV1"

    private static var installedGroup: String?
    private static var installedAppGroup: String?
    private static var service = ""
    // Injectable only for deterministic ordering tests. Production always uses
    // the shared-container flock above; NSLock protects diagnostics only.
    static var transactionOverride: (((() throws -> Void) throws -> Void))?

    private static func withSharedTransaction<T>(_ operation: () throws -> T) throws -> T {
        if let transactionOverride {
            var result: Result<T, Error>?
            try transactionOverride { result = Result { try operation() } }
            return try result!.get()
        }
        return try LCSharedKeychainFileLock.withLock(appGroup: installedAppGroup) { try operation() }
    }

    static func makeClient() -> KeychainAccess.Keychain {
        installedGroup = nil
        installedAppGroup = nil
        service = Bundle.Info.appbundleIdentifier
        // The migration lock must be the same process-shared lock the secret
        // handoff takes, so it resolves the one runtime group. This bundle's own
        // packaged declaration is only a fallback for a launch that published
        // nothing, and it is not proof that the process is entitled to it.
        let appGroup = V3SharedAppGroup.runtimeIdentity()?.identifier
        // Prefer the shared group, which a signer that also grants it to the
        // extension makes available to both processes. If a re-signer grants it
        // to the root bundle only, the extension falls back to its own default
        // group: it runs the embedded SideStore and no other process reads this
        // namespace, so it is the credential owner rather than a leak.
        let keychainGroup = (try? V3SecretHandoff.sharedKeychainAccessGroup())
            ?? (try? V3SecretHandoff.processDefaultKeychainAccessGroup())
        // The normal identity hooks must already have run.
        if service == "com.kdt.livecontainer", let appGroup, !appGroup.isEmpty,
           let keychainGroup, !keychainGroup.isEmpty {
            installedGroup = keychainGroup
            installedAppGroup = appGroup
            let scope = keychainGroup == V3SecretHandoff.sharedKeychainGroupName ? "shared" : "process"
            debugLog("[LC_KEYCHAIN] GROUP_SELECTED service=\(service) scope=\(scope)")
            return KeychainAccess.Keychain(service: service, accessGroup: keychainGroup)
                .accessibility(.afterFirstUnlock).synchronizable(true)
        }
        note("configuration", status: -34018)
        // A client is required by existing upstream certificate APIs; auth
        // reads/writes below fail closed until configuration is available.
        return KeychainAccess.Keychain(service: service)
            .accessibility(.afterFirstUnlock).synchronizable(true)
    }

    static func prepare(_ client: KeychainAccess.Keychain) {
        guard let group = installedGroup else { return }
        do {
            let ready = try withSharedTransaction { try prepareLocked(group: group, client: client) }
            note("migration", status: ready ? 0 : -25300)
            debugLog("[LC_KEYCHAIN] MIGRATION_READY value=\(ready) pid=\(ProcessInfo.processInfo.processIdentifier)")
        } catch { note("migration", status: (error as NSError).code) }
    }

    private static func prepareLocked(group: String, client: KeychainAccess.Keychain) throws -> Bool {
        guard try client.getData(authenticationJournal) == nil else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
        return try LCSharedKeychainMigration.prepare(group: group,
            items: { try legacyItems(service: service) },
            read: { try client.getData($0) }, write: { try client.set($1, key: $0) })
    }

    private static func legacyItems(service: String) throws -> [LCLegacyKeychainItem] {
        // Exact SideStore service only; securityd limits results to groups this
        // process is already entitled to. Never scan other apps' services.
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrSynchronizable as String: kSecAttrSynchronizableAny,
            kSecMatchLimit as String: kSecMatchLimitAll, kSecReturnAttributes as String: true,
            kSecReturnData as String: true, kSecUseAuthenticationUI as String: kSecUseAuthenticationUIFail]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return [] }
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
        guard let rows = result as? [[String: Any]] else {
            throw NSError(domain: "com.SideStore.Keychain", code: 1009)
        }
        return rows.compactMap { row in
            guard let group = row[kSecAttrAccessGroup as String] as? String,
                  let key = row[kSecAttrAccount as String] as? String,
                  let data = row[kSecValueData as String] as? Data else { return nil }
            return LCLegacyKeychainItem(group: group, key: key, data: data)
        }
    }

    static func isReady(_ client: KeychainAccess.Keychain) -> Bool {
        guard installedGroup != nil else { return false }
        do {
            // This marker also gates the legacy certificate-format migration;
            // keep that lifecycle independent of the auth snapshot's content.
            return try withSharedTransaction {
                try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.ready &&
                    client.getData(authenticationJournal) == nil
            }
        }
        catch { note("migration", status: (error as NSError).code); return false }
    }

    static func readAuthenticationSnapshot(_ client: KeychainAccess.Keychain) throws -> LCEmbeddedAuthenticationSnapshot? {
        guard installedGroup != nil else {
            let error = NSError(domain: "com.SideStore.Keychain", code: -34018)
            recordAuthenticationSnapshotIssue(error.code)
            throw error
        }
        do {
            let snapshot = try withSharedTransaction { () throws -> LCEmbeddedAuthenticationSnapshot? in
                guard let values = try authenticationValuesLocked(client) else { return nil }
                func string(_ key: String) -> String? {
                    guard let data = values[key], let value = String(data: data, encoding: .utf8), !value.isEmpty else { return nil }
                    return value
                }
                return LCEmbeddedAuthenticationSnapshot(
                    appleIDEmailAddress: string("appleIDEmailAddress"),
                    appleIDPassword: string("appleIDPassword"),
                    appleIDAdsid: string("appleIDAdsid"),
                    appleIDXcodeToken: string("appleIDXcodeToken"))
            }
            recordAuthenticationSnapshotIssue(snapshot == nil ? -25300 : 0)
            return snapshot
        } catch {
            recordAuthenticationSnapshotIssue((error as NSError).code)
            throw error
        }
    }

    static func readAuthenticationCandidate(_ client: KeychainAccess.Keychain) throws -> LCEmbeddedAuthenticationCandidate? {
        guard let group = installedGroup else { throw NSError(domain: "com.SideStore.Keychain", code: -34018) }
        return try withSharedTransaction {
            // This may migrate an empty destination, but never adopts existing
            // markerless values or promotes an explicit sign-out tombstone.
            _ = try authenticationValuesLocked(client)
            let marker = try client.getData(LCSharedKeychainMigration.marker)
            if marker == LCSharedKeychainMigration.signedOut { return nil }
            guard marker == nil || marker == LCSharedKeychainMigration.ready else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1009)
            }
            let values = try LCSharedKeychainMigration.readAuthenticationValues { try client.getData($0) }
            guard LCSharedKeychainMigration.complete(values) else { return nil }
            if marker == nil {
                try verifyRecoveryConflicts(group: group, values: values)
            }
            func string(_ key: String) -> String? {
                guard let data = values[key], let value = String(data: data, encoding: .utf8), !value.isEmpty else { return nil }
                return value
            }
            return LCEmbeddedAuthenticationCandidate(credentials: LCEmbeddedAuthenticationSnapshot(
                appleIDEmailAddress: string("appleIDEmailAddress"), appleIDPassword: string("appleIDPassword"),
                appleIDAdsid: string("appleIDAdsid"), appleIDXcodeToken: string("appleIDXcodeToken")),
                marker: marker, values: values)
        }
    }

    private static func verifyRecoveryConflicts(group: String, values: [String: Data]) throws {
        // Conservatively reject any conflicting auth value in another entitled
        // namespace. Never borrow an email/password from it to complete a route.
        for item in try legacyItems(service: service) where item.group != group &&
                LCSharedKeychainMigration.authKeys.contains(item.key) {
            guard values[item.key] == item.data else {
                throw NSError(domain: "LiveContainerRefresh.Configuration", code: 1008)
            }
        }
    }

    /// Reads the marker and all four auth values under one process-shared lock.
    /// Callers that make a decision from a credential pair must use the returned
    /// snapshot instead of combining separate KeychainItem getter results.
    private static func authenticationValuesLocked(_ client: KeychainAccess.Keychain) throws -> [String: Data]? {
        guard installedGroup != nil else { throw NSError(domain: "com.SideStore.Keychain", code: -34018) }
        var marker = try client.getData(LCSharedKeychainMigration.marker)
        if marker == LCSharedKeychainMigration.signedOut { return nil }
        guard try client.getData(authenticationJournal) == nil else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
        if marker == LCSharedKeychainMigration.pending { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
        if marker != LCSharedKeychainMigration.ready {
            guard let group = installedGroup else { return nil }
            _ = try prepareLocked(group: group, client: client)
            marker = try client.getData(LCSharedKeychainMigration.marker)
        }
        guard marker == nil || marker == LCSharedKeychainMigration.signedOut ||
              marker == LCSharedKeychainMigration.ready else {
            // An unknown marker is neither a committed credential route nor a
            // confirmed empty/signed-out namespace. Preserve it as unknown.
            throw NSError(domain: "com.SideStore.Keychain", code: 1009)
        }
        guard marker == LCSharedKeychainMigration.ready else { return nil }
        let values = try LCSharedKeychainMigration.readAuthenticationValues { try client.getData($0) }
        guard LCSharedKeychainMigration.complete(values) else {
            try client.set(LCSharedKeychainMigration.signedOut, key: LCSharedKeychainMigration.marker)
            guard try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.signedOut else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1009)
            }
            return nil
        }
        // A pre-patch process may not honor the flock. Recheck its epoch marker
        // after reading the values so a sign-out/change during the read fails closed.
        guard try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.ready else {
            return nil
        }
        return values
    }

    static func readString(_ key: String, client: KeychainAccess.Keychain) -> String? {
        guard let data = read(key, client: client) else { return nil }
        guard let value = String(data: data, encoding: .utf8) else {
            note(key, status: 1009); return nil
        }
        return value
    }

    static func read(_ key: String, client: KeychainAccess.Keychain) -> Data? {
        guard installedGroup != nil else { note(key, status: -34018); return nil }
        do {
            if LCSharedKeychainMigration.authKeys.contains(key) {
                let data = try withSharedTransaction {
                    try authenticationValuesLocked(client)?[key]
                }
                if data != nil { note("migration", status: 0) }
                note(key, status: data == nil ? -25300 : 0)
                return data
            }
            if key == "signingCertificate" || key == "signingCertificatePassword" {
                let certificate = try readSigningCertificateSnapshot(client)
                return key == "signingCertificate" ? certificate?.p12Data : certificate?.password.map { Data($0.utf8) }
            }
            let ready = try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.ready
            var data = try client.getData(key)
            if data == nil && !ready {
                // Preserve certificate-only/imported-certificate setups before
                // an Apple login is migrated. This fallback is READ ONLY and
                // cannot make an authentication preflight pass.
                let legacy = KeychainAccess.Keychain(service: service)
                    .accessibility(.afterFirstUnlock).synchronizable(true)
                data = try legacy.getData(key)
            }
            if ready { note("migration", status: 0) }
            note(key, status: data == nil ? -25300 : 0)
            return data
        } catch { note(key, status: (error as NSError).code); return nil }
    }

    static func write(_ key: String, data: Data?, client: KeychainAccess.Keychain) {
        guard installedGroup != nil else { note(key, status: -34018); return }
        do {
            try withSharedTransaction {
                try writeOne(key, data: data, client: client)
            }
            note(key, status: 0)
        } catch { note(key, status: (error as NSError).code) }
    }

    /// Commits the four values produced by one Apple authentication response as
    /// a single credential epoch. The migration marker remains non-ready until
    /// every value and at least one complete credential route have been read
    /// back from the shared Keychain.
    static func writeAuthenticationCredentials(appleID: String, password: String?,
                                               dsid: String, authToken: String,
                                               expectedCandidate: LCEmbeddedAuthenticationCandidate? = nil,
                                               client: KeychainAccess.Keychain) throws {
        guard installedGroup != nil else {
            note("authWrite", status: -34018)
            throw NSError(domain: "com.SideStore.Keychain", code: -34018)
        }
        var expected: [String: Data] = [
            "appleIDEmailAddress": Data(appleID.utf8),
            "appleIDAdsid": Data(dsid.utf8),
            "appleIDXcodeToken": Data(authToken.utf8)
        ]
        if let password, !password.isEmpty { expected["appleIDPassword"] = Data(password.utf8) }
        guard !appleID.isEmpty, !dsid.isEmpty, !authToken.isEmpty,
              LCSharedKeychainMigration.complete(expected) else {
            note("authWrite", status: 1009)
            throw NSError(domain: "com.SideStore.Keychain", code: 1009,
                userInfo: [NSLocalizedDescriptionKey: "SideStore could not verify a complete Apple sign-in credential set."])
        }

        do {
            try withSharedTransaction {
                guard try client.getData(authenticationJournal) == nil else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                }
                let keys = LCSharedKeychainMigration.authKeys
                let original = try LCSharedKeychainMigration.readAuthenticationValues {
                    try client.getData($0)
                }
                let originalMarker = try client.getData(LCSharedKeychainMigration.marker)
                if let candidate = expectedCandidate {
                    guard candidate.marker != LCSharedKeychainMigration.signedOut,
                          candidate.marker == originalMarker, candidate.values == original,
                          candidate.matchesVerifiedIdentity(appleID: appleID, dsid: dsid) else {
                        throw NSError(domain: "LiveContainerRefresh.Configuration", code: 1008)
                    }
                    if originalMarker == nil, let group = installedGroup {
                        try verifyRecoveryConflicts(group: group, values: original)
                    }
                }
                if originalMarker == LCSharedKeychainMigration.pending {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                }
                guard originalMarker == nil || originalMarker == LCSharedKeychainMigration.ready ||
                      originalMarker == LCSharedKeychainMigration.signedOut else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                }
                let rollbackMarker: Data? = {
                    if originalMarker == LCSharedKeychainMigration.ready &&
                       LCSharedKeychainMigration.complete(original) { return LCSharedKeychainMigration.ready }
                    if originalMarker == LCSharedKeychainMigration.signedOut { return LCSharedKeychainMigration.signedOut }
                    if originalMarker == LCSharedKeychainMigration.ready { return LCSharedKeychainMigration.signedOut }
                    return nil
                }()

                try saveTransactionJournal(authenticationJournal, keys: keys, original: original,
                    expected: expected, originalMarker: rollbackMarker,
                    expectedMarker: LCSharedKeychainMigration.ready, client: client)
                do {
                    // Block all cooperating readers before the first item changes.
                    try client.set(LCSharedKeychainMigration.pending,
                                   key: LCSharedKeychainMigration.marker)
                    guard try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.pending else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }

                    for key in keys {
                        if let value = expected[key] { try client.set(value, key: key) }
                        else if try client.getData(key) != nil { try client.remove(key) }
                        guard try client.getData(key) == expected[key] else {
                            throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                        }
                    }

                    let written = try LCSharedKeychainMigration.readAuthenticationValues {
                        try client.getData($0)
                    }
                    guard written == expected, LCSharedKeychainMigration.complete(written) else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }
                    try client.set(LCSharedKeychainMigration.ready, key: LCSharedKeychainMigration.marker)
                    guard try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.ready else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }
                    let committed = try LCSharedKeychainMigration.readAuthenticationValues {
                        try client.getData($0)
                    }
                    guard committed == expected, LCSharedKeychainMigration.complete(committed) else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }
                } catch {
                    let writeError = error
                    do {
                        // Keep reads fail-closed throughout rollback. Restore the
                        // previous item set, verify every key, then restore only
                        // a marker justified by that exact prior state.
                        try client.set(LCSharedKeychainMigration.pending,
                                       key: LCSharedKeychainMigration.marker)
                        guard try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.pending else {
                            throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                        }
                        for key in keys {
                            if let value = original[key] { try client.set(value, key: key) }
                            else if try client.getData(key) != nil { try client.remove(key) }
                            guard try client.getData(key) == original[key] else {
                                throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                            }
                        }
                        if let rollbackMarker {
                            try client.set(rollbackMarker, key: LCSharedKeychainMigration.marker)
                        } else if try client.getData(LCSharedKeychainMigration.marker) != nil {
                            try client.remove(LCSharedKeychainMigration.marker)
                        }
                        guard try client.getData(LCSharedKeychainMigration.marker) == rollbackMarker else {
                            throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                        }
                        let restored = try LCSharedKeychainMigration.readAuthenticationValues {
                            try client.getData($0)
                        }
                        guard restored == original,
                              rollbackMarker != LCSharedKeychainMigration.ready ||
                                LCSharedKeychainMigration.complete(restored) else {
                            throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                        }
                    } catch {
                        // If rollback cannot be proven, leave a pending marker whenever
                        // possible so a partial account is never advertised ready.
                        try? client.set(LCSharedKeychainMigration.pending,
                                        key: LCSharedKeychainMigration.marker)
                        note("authWrite", status: 1010)
                        throw NSError(domain: "com.SideStore.Keychain", code: 1010,
                            userInfo: [NSLocalizedDescriptionKey: "SideStore could not confirm whether the Apple sign-in credentials were saved. Reload Account & Signing before continuing."])
                    }
                    try clearTransactionJournal(authenticationJournal, client: client)
                    throw writeError
                }
                try clearTransactionJournal(authenticationJournal, client: client)
            }
            note("authWrite", status: 0)
        } catch {
            note("authWrite", status: (error as NSError).code)
            throw error
        }
    }

    /// Journal contents never leave Keychain. The verified Apple response or
    /// certificate bytes are recorded before any existing value is changed.
    private static func saveTransactionJournal(_ key: String, keys: [String],
                                               original: [String: Data], expected: [String: Data],
                                               originalMarker: Data?, expectedMarker: Data,
                                               client: KeychainAccess.Keychain) throws {
        var value: [String: Any] = ["version": 1, "keys": keys, "original": original,
                                  "expected": expected, "expectedMarker": expectedMarker]
        if let originalMarker { value["originalMarker"] = originalMarker }
        let data = try PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
        try client.set(data, key: key)
        guard try client.getData(key) == data else { throw NSError(domain: "com.SideStore.Keychain", code: 1009) }
    }

    private static func clearTransactionJournal(_ key: String, client: KeychainAccess.Keychain) throws {
        do {
            try client.remove(key)
            guard try client.getData(key) == nil else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
        } catch {
            // A retained journal is authoritative even if a ready marker was
            // published before rollback or journal cleanup failed.
            throw NSError(domain: "com.SideStore.Keychain", code: 1010)
        }
    }

    /// Reconcile only an exactly proven prior or intended transaction outcome.
    /// Mixed/partial values remain quarantined. This never retries Apple, fills
    /// missing credentials, or turns an explicit signed-out marker into ready.
    static func reconcileStorage(_ client: KeychainAccess.Keychain,
                                 certificateSerial: (Data, String?) throws -> String) throws {
        guard installedGroup != nil else { throw NSError(domain: "com.SideStore.Keychain", code: -34018) }
        try withSharedTransaction {
            for (markerKey, journalKey) in [(LCSharedKeychainMigration.marker, authenticationJournal),
                                            (certificateMarker, certificateJournal)] {
                let observedMarker = try client.getData(markerKey)
                let journalData = try client.getData(journalKey)
                if observedMarker == LCSharedKeychainMigration.signedOut {
                    // Explicit sign-out wins over every retained transaction.
                    // Remove only stale proof, never promote or rewrite values.
                    if journalData != nil { try clearTransactionJournal(journalKey, client: client) }
                    continue
                }
                if journalData == nil && observedMarker != LCSharedKeychainMigration.pending { continue }
                guard observedMarker == nil || observedMarker == LCSharedKeychainMigration.ready ||
                      observedMarker == LCSharedKeychainMigration.pending else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                }
                guard let data = journalData,
                      let journal = try PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any],
                      journal["version"] as? Int == 1,
                      let keys = journal["keys"] as? [String], Set(keys).count == keys.count,
                      let original = journal["original"] as? [String: Data],
                      let expected = journal["expected"] as? [String: Data],
                      let expectedMarker = journal["expectedMarker"] as? Data,
                      Set(original.keys).isSubset(of: Set(keys)), Set(expected.keys).isSubset(of: Set(keys)) else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                }
                let originalMarker = journal["originalMarker"] as? Data
                guard journal["originalMarker"] == nil || originalMarker != nil else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                }
                guard originalMarker == nil || originalMarker == LCSharedKeychainMigration.ready ||
                      originalMarker == LCSharedKeychainMigration.signedOut,
                      expectedMarker == LCSharedKeychainMigration.ready || expectedMarker == LCSharedKeychainMigration.signedOut else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                }
                let isAuthentication = markerKey == LCSharedKeychainMigration.marker
                if isAuthentication {
                    guard keys == LCSharedKeychainMigration.authKeys else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
                } else {
                    guard keys.count == 2 || keys.count == 3,
                          Array(keys.prefix(2)) == ["signingCertificate", "signingCertificatePassword"],
                          keys.count == 2 || (keys[2].hasPrefix("importedCert_") && keys[2].count <= 256) else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                    }
                }
                var current: [String: Data] = [:]
                for key in keys { current[key] = try client.getData(key) }
                let resolvedMarker: Data?
                if current == expected {
                    if isAuthentication && expectedMarker == LCSharedKeychainMigration.ready {
                        guard LCSharedKeychainMigration.complete(current),
                              let email = current["appleIDEmailAddress"], !email.isEmpty else {
                            throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                        }
                    }
                    if !isAuthentication && expectedMarker == LCSharedKeychainMigration.ready {
                        guard let p12Data = current["signingCertificate"], keys.count == 3,
                              current[keys[2]] == p12Data else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
                        let password = current["signingCertificatePassword"].flatMap { String(data: $0, encoding: .utf8) }
                        guard current["signingCertificatePassword"] == nil || password != nil,
                              try certificateSerial(p12Data, password) == String(keys[2].dropFirst("importedCert_".count)) else {
                            throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                        }
                    }
                    resolvedMarker = expectedMarker
                } else if current == original {
                    guard !isAuthentication || originalMarker != LCSharedKeychainMigration.ready ||
                          LCSharedKeychainMigration.complete(current) else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
                    resolvedMarker = originalMarker
                } else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                }
                // Verify again immediately before publishing the resolved state.
                for key in keys {
                    guard try client.getData(key) == current[key] else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
                }
                guard try client.getData(markerKey) == observedMarker else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                }
                if let resolvedMarker { try client.set(resolvedMarker, key: markerKey) }
                else { try client.remove(markerKey) }
                guard try client.getData(markerKey) == resolvedMarker else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
                try clearTransactionJournal(journalKey, client: client)
            }
        }
    }

    static func storageRequiresReconciliation(_ client: KeychainAccess.Keychain) throws -> Bool {
        guard installedGroup != nil else { throw NSError(domain: "com.SideStore.Keychain", code: -34018) }
        return try withSharedTransaction {
            if try client.getData(authenticationJournal) != nil || client.getData(certificateJournal) != nil { return true }
            for key in [LCSharedKeychainMigration.marker, certificateMarker] {
                let marker = try client.getData(key)
                if marker != nil && marker != LCSharedKeychainMigration.ready &&
                   marker != LCSharedKeychainMigration.signedOut { return true }
            }
            return false
        }
    }

    /// Read certificate and password from one namespace while holding the
    /// shared lock. A crash during a transaction is explicitly unknown; no
    /// fallback may resurrect a different certificate or combine passwords.
    static func readSigningCertificateSnapshot(_ client: KeychainAccess.Keychain) throws -> LCEmbeddedSigningCertificateSnapshot? {
        guard installedGroup != nil else { throw NSError(domain: "com.SideStore.Keychain", code: -34018) }
        return try withSharedTransaction {
            let marker = try client.getData(certificateMarker)
            if marker == LCSharedKeychainMigration.signedOut { return nil }
            guard try client.getData(certificateJournal) == nil else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
            guard marker == nil || marker == LCSharedKeychainMigration.ready else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1010)
            }
            var data = try client.getData("signingCertificate")
            var password = try client.getData("signingCertificatePassword")
            if marker == nil && data == nil && password == nil {
                let legacy = KeychainAccess.Keychain(service: service)
                    .accessibility(.afterFirstUnlock).synchronizable(true)
                data = try legacy.getData("signingCertificate")
                password = try legacy.getData("signingCertificatePassword")
            }
            guard let data else {
                if marker == LCSharedKeychainMigration.ready || password != nil {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                }
                return nil
            }
            let decoded = password.flatMap { String(data: $0, encoding: .utf8) }
            guard password == nil || decoded != nil else { throw NSError(domain: "com.SideStore.Keychain", code: 1009) }
            return LCEmbeddedSigningCertificateSnapshot(p12Data: data, password: decoded)
        }
    }

    /// A verified local transaction, independent of the Apple authentication
    /// marker. Validation decrypts/parses the read-back P12 before activation.
    /// No failure here creates or revokes any certificate on Apple's servers.
    static func writeSigningCertificate(p12Data: Data?, password: String?, serial: String?,
                                       client: KeychainAccess.Keychain,
                                       validate: (Data, String?) throws -> Void) throws {
        guard installedGroup != nil else { throw NSError(domain: "com.SideStore.Keychain", code: -34018) }
        var expected: [String: Data] = [:]
        var keys = ["signingCertificate", "signingCertificatePassword"]
        if let p12Data {
            guard !p12Data.isEmpty, let serial, !serial.isEmpty, serial.count <= 243 else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1009)
            }
            expected["signingCertificate"] = p12Data
            if let password { expected["signingCertificatePassword"] = Data(password.utf8) }
            let imported = "importedCert_" + serial
            keys.append(imported)
            expected[imported] = p12Data
        }
        try withSharedTransaction {
            guard try client.getData(certificateJournal) == nil else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
            let originalMarker = try client.getData(certificateMarker)
            guard originalMarker == nil || originalMarker == LCSharedKeychainMigration.ready ||
                  originalMarker == LCSharedKeychainMigration.signedOut else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1010)
            }
            var original: [String: Data] = [:]
            for key in keys { original[key] = try client.getData(key) }
            func verifyExpected() throws {
                for key in keys {
                    guard try client.getData(key) == expected[key] else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }
                }
                if let stored = try client.getData("signingCertificate") {
                    let passwordData = try client.getData("signingCertificatePassword")
                    let decoded = passwordData.flatMap { String(data: $0, encoding: .utf8) }
                    guard passwordData == nil || decoded != nil else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }
                    try validate(stored, decoded)
                }
            }
            try saveTransactionJournal(certificateJournal, keys: keys, original: original,
                expected: expected, originalMarker: originalMarker,
                expectedMarker: p12Data == nil ? LCSharedKeychainMigration.signedOut : LCSharedKeychainMigration.ready,
                client: client)
            do {
                try client.set(certificatePending, key: certificateMarker)
                guard try client.getData(certificateMarker) == certificatePending else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                }
                for key in keys {
                    if let value = expected[key] { try client.set(value, key: key) }
                    else if try client.getData(key) != nil { try client.remove(key) }
                }
                try verifyExpected()
                let committedMarker = p12Data == nil ? LCSharedKeychainMigration.signedOut : LCSharedKeychainMigration.ready
                try client.set(committedMarker, key: certificateMarker)
                guard try client.getData(certificateMarker) == committedMarker else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                }
                try verifyExpected()
            } catch {
                let writeError = error
                do {
                    try client.set(certificatePending, key: certificateMarker)
                    guard try client.getData(certificateMarker) == certificatePending else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                    }
                    for key in keys {
                        if let value = original[key] { try client.set(value, key: key) }
                        else if try client.getData(key) != nil { try client.remove(key) }
                        guard try client.getData(key) == original[key] else {
                            throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                        }
                    }
                    if let originalMarker { try client.set(originalMarker, key: certificateMarker) }
                    else { try client.remove(certificateMarker) }
                    guard try client.getData(certificateMarker) == originalMarker else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                    }
                } catch {
                    try? client.set(certificatePending, key: certificateMarker)
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010,
                        userInfo: [NSLocalizedDescriptionKey: "SideStore could not confirm certificate persistence. Reload Account & Signing before continuing."])
                }
                try clearTransactionJournal(certificateJournal, client: client)
                throw writeError
            }
            try clearTransactionJournal(certificateJournal, client: client)
        }
    }

    private static func writeOne(_ key: String, data: Data?,
                                 client: KeychainAccess.Keychain) throws {
        let isAuth = LCSharedKeychainMigration.authKeys.contains(key)
        if isAuth {
            guard try client.getData(authenticationJournal) == nil else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
        }
        if ["signingCertificate", "signingCertificatePassword"].contains(key) {
            guard try client.getData(certificateJournal) == nil else { throw NSError(domain: "com.SideStore.Keychain", code: 1010) }
        }
        guard isAuth else {
            if let data { try client.set(data, key: key) } else { try client.remove(key) }
            guard try client.getData(key) == data else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1009)
            }
            return
        }

        let markerKey = LCSharedKeychainMigration.marker
        let oldValue = try client.getData(key)
        let oldMarker = try client.getData(markerKey)
        guard oldMarker == nil || oldMarker == LCSharedKeychainMigration.ready ||
              oldMarker == LCSharedKeychainMigration.signedOut else {
            throw NSError(domain: "com.SideStore.Keychain", code: 1009)
        }
        let oldValues = try LCSharedKeychainMigration.readAuthenticationValues {
            try client.getData($0)
        }
        let rollbackMarker: Data? = {
            if oldMarker == LCSharedKeychainMigration.ready {
                return LCSharedKeychainMigration.complete(oldValues)
                    ? LCSharedKeychainMigration.ready : LCSharedKeychainMigration.signedOut
            }
            return oldMarker
        }()
        let nextValues: [String: Data] = {
            var values = oldValues
            if let data { values[key] = data } else { values.removeValue(forKey: key) }
            return values
        }()
        let nextMarker = LCSharedKeychainMigration.complete(nextValues)
            ? LCSharedKeychainMigration.ready : LCSharedKeychainMigration.signedOut

        do {
            // A single-key write must never declare ready based only on the key
            // being written. Incomplete routes remain hidden behind the marker.
            if oldMarker != LCSharedKeychainMigration.signedOut {
                try client.set(LCSharedKeychainMigration.signedOut, key: markerKey)
                guard try client.getData(markerKey) == LCSharedKeychainMigration.signedOut else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                }
            }
            if let data { try client.set(data, key: key) } else { try client.remove(key) }
            guard try client.getData(key) == data else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1009)
            }
            let verified = try LCSharedKeychainMigration.readAuthenticationValues {
                try client.getData($0)
            }
            guard verified == nextValues else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1009)
            }
            if nextMarker == LCSharedKeychainMigration.ready {
                try client.set(LCSharedKeychainMigration.ready, key: markerKey)
                guard try client.getData(markerKey) == LCSharedKeychainMigration.ready else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                }
            } else if oldMarker == nil || oldMarker == LCSharedKeychainMigration.ready {
                try client.set(LCSharedKeychainMigration.signedOut, key: markerKey)
                guard try client.getData(markerKey) == LCSharedKeychainMigration.signedOut else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                }
            }
        } catch {
            let writeError = error
            do {
                try client.set(LCSharedKeychainMigration.signedOut, key: markerKey)
                guard try client.getData(markerKey) == LCSharedKeychainMigration.signedOut else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                }
                if let oldValue { try client.set(oldValue, key: key) }
                else if try client.getData(key) != nil { try client.remove(key) }
                guard try client.getData(key) == oldValue else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                }
                if let rollbackMarker { try client.set(rollbackMarker, key: markerKey) }
                else { try client.remove(markerKey) }
                guard try client.getData(markerKey) == rollbackMarker else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                }
            } catch {
                try? client.set(LCSharedKeychainMigration.signedOut, key: markerKey)
                note("authWrite", status: 1010)
                throw NSError(domain: "com.SideStore.Keychain", code: 1010,
                    userInfo: [NSLocalizedDescriptionKey: "SideStore could not confirm whether the Apple sign-in credentials were saved. Reload Account & Signing before continuing."])
            }
            throw writeError
        }
    }

    static func removeChecked(_ key: String, client: KeychainAccess.Keychain) throws {
        guard installedGroup != nil else {
            note(key, status: -34018)
            throw NSError(domain: "com.SideStore.Keychain", code: -34018)
        }
        do {
            try withSharedTransaction {
                if LCSharedKeychainMigration.authKeys.contains(key) {
                    try client.set(LCSharedKeychainMigration.signedOut, key: LCSharedKeychainMigration.marker)
                    guard try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.signedOut else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }
                }
                if try client.getData(key) != nil {
                    try client.remove(key)
                    guard try client.getData(key) == nil else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }
                }
            }
            note(key, status: 0)
        } catch {
            note(key, status: (error as NSError).code)
            throw error
        }
    }

    static func clearSignInInfoChecked(_ client: KeychainAccess.Keychain) throws {
        guard installedGroup != nil else {
            note("configuration", status: -34018)
            throw NSError(domain: "com.SideStore.Keychain", code: -34018)
        }
        do {
            try withSharedTransaction {
                let keys = LCSharedKeychainMigration.authKeys
                let saved = try keys.map { key in (key: key, value: try client.getData(key)) }
                let priorMarker = try client.getData(LCSharedKeychainMigration.marker)
                do {
                    try client.set(LCSharedKeychainMigration.signedOut, key: LCSharedKeychainMigration.marker)
                    guard try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.signedOut else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }
                    for (key, value) in saved {
                        if value != nil { try client.remove(key) }
                        guard try client.getData(key) == nil else {
                            throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                        }
                    }
                    note("signout", status: 0)
                } catch {
                    let removalError = error
                    do {
                        for (key, value) in saved {
                            if let value { try client.set(value, key: key) }
                            else if try client.getData(key) != nil { try client.remove(key) }
                            guard try client.getData(key) == value else {
                                throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                            }
                        }
                        if let priorMarker { try client.set(priorMarker, key: LCSharedKeychainMigration.marker) }
                        else if try client.getData(LCSharedKeychainMigration.marker) != nil {
                            try client.remove(LCSharedKeychainMigration.marker)
                        }
                        guard try client.getData(LCSharedKeychainMigration.marker) == priorMarker else {
                            throw NSError(domain: "com.SideStore.Keychain", code: 1010)
                        }
                    } catch {
                        note("signout", status: 1010)
                        throw NSError(domain: "com.SideStore.Keychain", code: 1010,
                            userInfo: [NSLocalizedDescriptionKey: "The sign-out result is unknown; reload account state before continuing."])
                    }
                    note("signout", status: (removalError as NSError).code)
                    throw removalError
                }
            }
        } catch { throw error }
    }

    static func clearAll(_ client: KeychainAccess.Keychain) {
        guard installedGroup != nil else { note("configuration", status: -34018); return }
        do {
            try withSharedTransaction {
                try client.set(LCSharedKeychainMigration.signedOut, key: LCSharedKeychainMigration.marker)
                guard try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.signedOut else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                }
                // Remove only this service's items, not the whole access group.
                for key in client.allKeys() where key != LCSharedKeychainMigration.marker {
                    try client.remove(key)
                    guard try client.getData(key) == nil else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }
                }
            }
            note("clear", status: 0)
        } catch { note("clear", status: (error as NSError).code) }
    }

    private static func note(_ key: String, status: Int) {
        // Item names and status ONLY. Never log values or NSError.userInfo,
        // which third-party wrappers may populate with a complete query.
        let label = LCSharedKeychainMigration.knownKeys.contains(key) ? key : "storage"
        debugLog("[LC_KEYCHAIN] ACCESS item=\(label) status=\(status) pid=\(ProcessInfo.processInfo.processIdentifier)")
    }

    private static func recordAuthenticationSnapshotIssue(_ status: Int) {
        debugLog("[LC_KEYCHAIN] SNAPSHOT status=\(status) pid=\(ProcessInfo.processInfo.processIdentifier)")
    }

    static func authenticationFailure(for error: Error) -> NSError {
        let native = error as NSError
        let keychainDomain = "com.SideStore.Keychain"
        let keychainAccessErrorDomain = "com.kishikawakatsumi.KeychainAccess.error"
        let isKeychainAccessError = native.domain == NSOSStatusErrorDomain ||
            native.domain == keychainAccessErrorDomain
        if native.code == -34018 &&
           (native.domain == keychainDomain || native.domain == NSOSStatusErrorDomain ||
            native.domain == keychainAccessErrorDomain) {
            return NSError(domain: "LiveContainerRefresh.Configuration", code: 1006,
                userInfo: [NSLocalizedDescriptionKey: "This installation cannot access SideStore's shared Keychain group. Keep the LiveProcess extension and use the same signing team; do not sign out. Check LC_KEYCHAIN diagnostics."])
        }
        if isKeychainAccessError &&
           [-25308, -25291, -25315].contains(native.code) {
            return NSError(domain: "com.SideStore.Keychain", code: 1005,
                userInfo: [NSLocalizedDescriptionKey: "Saved sign-in details are temporarily inaccessible. Unlock the iPhone and retry; your account has not been signed out."])
        }
        if (native.domain == keychainDomain || native.domain == "LiveContainerRefresh.Configuration") && native.code == 1008 {
            return NSError(domain: "LiveContainerRefresh.Configuration", code: 1008,
                userInfo: [NSLocalizedDescriptionKey: "Conflicting saved SideStore logins were found. Automatic migration stopped without replacing them. Open embedded SideStore to choose the intended account."])
        }
        if native.domain == keychainDomain && native.code == 1010 {
            return NSError(domain: "com.SideStore.Keychain", code: 1010,
                userInfo: [NSLocalizedDescriptionKey: "SideStore could not confirm whether the Apple sign-in credentials were saved. Reload Account & Signing before continuing."])
        }
        let knownDomain = isKeychainAccessError || native.domain == keychainDomain
        let detail = knownDomain
            ? "SideStore Keychain access failed (status \(native.code)). Your account has not been signed out. Check LC_KEYCHAIN diagnostics."
            : "SideStore could not safely read the saved authentication state. Your account has not been signed out. Check LC_KEYCHAIN diagnostics."
        return NSError(domain: "com.SideStore.Keychain", code: 1009,
            userInfo: [NSLocalizedDescriptionKey: detail])
    }
}
