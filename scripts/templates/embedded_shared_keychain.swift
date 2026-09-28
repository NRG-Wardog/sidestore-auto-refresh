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
        guard let appGroup, !appGroup.isEmpty,
              Bundle.main.altstoreAppGroup == appGroup,
              FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroup) != nil else {
            throw NSError(domain: "com.SideStore.Keychain", code: -34018)
        }
        return try V3AppGroupProcessLock.withLock(operation)
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
        appleIDEmailAddress != nil && (appleIDPassword != nil || appleIDXcodeToken != nil)
    }
    var hasPasswordCredentials: Bool { appleIDPassword != nil }
    var hasTokenCredentials: Bool { appleIDXcodeToken != nil }
}

fileprivate enum LCEmbeddedSharedKeychain {
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
        let appGroup = Bundle.main.altstoreAppGroup
        let keychainGroup = try? V3SecretHandoff.sharedKeychainAccessGroup()
        // The normal identity hooks must already have run. Do not quietly
        // persist new credentials into an unshared, process-private namespace.
        if service == "com.kdt.livecontainer", let appGroup, !appGroup.isEmpty,
           let keychainGroup, !keychainGroup.isEmpty {
            installedGroup = keychainGroup
            installedAppGroup = appGroup
            debugLog("[LC_KEYCHAIN] SHARED_GROUP_SELECTED service=\(service) keychain_group=livecontainer.shared")
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
        try LCSharedKeychainMigration.prepare(group: group,
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
                try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.ready
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
            let snapshot = try withSharedTransaction {
                guard let values = try authenticationValuesLocked(client) else { return nil }
                func string(_ key: String) -> String? {
                    values[key].flatMap { String(data: $0, encoding: .utf8) }
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

    /// Reads the marker and all four auth values under one process-shared lock.
    /// Callers that make a decision from a credential pair must use the returned
    /// snapshot instead of combining separate KeychainItem getter results.
    private static func authenticationValuesLocked(_ client: KeychainAccess.Keychain) throws -> [String: Data]? {
        guard installedGroup != nil else { throw NSError(domain: "com.SideStore.Keychain", code: -34018) }
        var marker = try client.getData(LCSharedKeychainMigration.marker)
        if marker == LCSharedKeychainMigration.signedOut { return nil }
        if marker != LCSharedKeychainMigration.ready {
            guard let group = installedGroup else { return nil }
            _ = try prepareLocked(group: group, client: client)
            marker = try client.getData(LCSharedKeychainMigration.marker)
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
            var ready = try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.ready
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
    static func writeAuthenticationCredentials(appleID: String, password: String,
                                               dsid: String, authToken: String,
                                               client: KeychainAccess.Keychain) throws {
        guard installedGroup != nil else {
            note("authWrite", status: -34018)
            throw NSError(domain: "com.SideStore.Keychain", code: -34018)
        }
        let expected: [String: Data] = [
            "appleIDEmailAddress": Data(appleID.utf8),
            "appleIDPassword": Data(password.utf8),
            "appleIDAdsid": Data(dsid.utf8),
            "appleIDXcodeToken": Data(authToken.utf8)
        ]
        guard LCSharedKeychainMigration.complete(expected),
              expected.count == LCSharedKeychainMigration.authKeys.count else {
            note("authWrite", status: 1009)
            throw NSError(domain: "com.SideStore.Keychain", code: 1009,
                userInfo: [NSLocalizedDescriptionKey: "SideStore could not verify a complete Apple sign-in credential set."])
        }

        do {
            try withSharedTransaction {
                let keys = LCSharedKeychainMigration.authKeys
                let original = try LCSharedKeychainMigration.readAuthenticationValues {
                    try client.getData($0)
                }
                let originalMarker = try client.getData(LCSharedKeychainMigration.marker)
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

                do {
                    // Block all cooperating readers before the first item changes.
                    try client.set(LCSharedKeychainMigration.signedOut,
                                   key: LCSharedKeychainMigration.marker)
                    guard try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.signedOut else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }

                    for key in keys {
                        guard let value = expected[key] else {
                            throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                        }
                        try client.set(value, key: key)
                        guard try client.getData(key) == value else {
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
                        try client.set(LCSharedKeychainMigration.signedOut,
                                       key: LCSharedKeychainMigration.marker)
                        guard try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.signedOut else {
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
                        // If rollback cannot be proven, leave a tombstone whenever
                        // possible so a partial account is never advertised ready.
                        try? client.set(LCSharedKeychainMigration.signedOut,
                                        key: LCSharedKeychainMigration.marker)
                        note("authWrite", status: 1010)
                        throw NSError(domain: "com.SideStore.Keychain", code: 1010,
                            userInfo: [NSLocalizedDescriptionKey: "SideStore could not confirm whether the Apple sign-in credentials were saved. Reload Account & Signing before continuing."])
                    }
                    throw writeError
                }
            }
            note("authWrite", status: 0)
        } catch {
            note("authWrite", status: (error as NSError).code)
            throw error
        }
    }

    private static func writeOne(_ key: String, data: Data?,
                                 client: KeychainAccess.Keychain) throws {
        guard LCSharedKeychainMigration.authKeys.contains(key) else {
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
        if native.domain == keychainDomain && native.code == 1008 {
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
