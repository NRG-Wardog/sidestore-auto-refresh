// LC_EMBEDDED_SHARED_KEYCHAIN_V1. Only injected into the combined product.
// Keep credentials in Keychain, never in UserDefaults, app-group files, or XPC.
// Host and LiveProcess already share an App Group, but not necessarily their
// DEFAULT keychain access group. Explicitly select the installed App Group.

// LC_SHARED_MIGRATION_POLICY_BEGIN
struct LCLegacyKeychainItem {
    let group: String
    let key: String
    let data: Data
}

enum LCSharedKeychainMigration {
    static let marker = "LCSharedKeychainReadyV1"
    static let ready = Data("1".utf8)
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

    /// Returns true only after a verified migration, or an existing committed
    /// namespace. No mixing credentials from different legacy access groups.
    static func prepare(group: String, items: () throws -> [LCLegacyKeychainItem],
                        read: (String) throws -> Data?, write: (String, Data) throws -> Void) throws -> Bool {
        if try read(marker) == ready { return true }
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
        return true
    }
}
// LC_SHARED_MIGRATION_POLICY_END

fileprivate enum LCEmbeddedSharedKeychain {
    private static let lock = NSLock()
    private static var lastIssues: [String: Int] = [:]
    private static var installedGroup: String?
    private static var service = ""

    static func makeClient() -> KeychainAccess.Keychain {
        installedGroup = nil
        service = Bundle.Info.appbundleIdentifier
        let group = Bundle.main.altstoreAppGroup
        // The normal identity hooks must already have run. Do not quietly
        // persist new credentials into an unshared, process-private namespace.
        if service == "com.kdt.livecontainer", let group, !group.isEmpty {
            installedGroup = group
            debugLog("[LC_KEYCHAIN] SHARED_GROUP_SELECTED service=\(service) group=\(group)")
            return KeychainAccess.Keychain(service: service, accessGroup: group)
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
            let ready = try LCSharedKeychainMigration.prepare(group: group,
                items: { try legacyItems(service: service) },
                read: { try client.getData($0) }, write: { try client.set($1, key: $0) })
            note("migration", status: ready ? 0 : -25300)
            debugLog("[LC_KEYCHAIN] MIGRATION_READY value=\(ready) pid=\(ProcessInfo.processInfo.processIdentifier)")
        } catch { note("migration", status: (error as NSError).code) }
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
        do { return try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.ready }
        catch { note("migration", status: (error as NSError).code); return false }
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
            let ready = try client.getData(LCSharedKeychainMigration.marker) == LCSharedKeychainMigration.ready
            if !ready && LCSharedKeychainMigration.authKeys.contains(key) {
                note(key, status: -25300); return nil
            }
            var data = try client.getData(key)
            if data == nil && !ready && !LCSharedKeychainMigration.authKeys.contains(key) {
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
            // Also retained on sign-out: never resurrect an old login on restart.
            if LCSharedKeychainMigration.authKeys.contains(key),
               try client.getData(LCSharedKeychainMigration.marker) != LCSharedKeychainMigration.ready {
                try client.set(LCSharedKeychainMigration.ready, key: LCSharedKeychainMigration.marker)
            }
            if let data { try client.set(data, key: key) } else { try client.remove(key) }
            guard try client.getData(key) == data else {
                throw NSError(domain: "com.SideStore.Keychain", code: 1009)
            }
            note(key, status: 0)
        } catch { note(key, status: (error as NSError).code) }
    }

    static func clearAll(_ client: KeychainAccess.Keychain) {
        guard installedGroup != nil else { note("configuration", status: -34018); return }
        do {
            // Keep the migration tombstone. Remove only this service's items,
            // not the whole group and not other apps' credentials.
            for key in client.allKeys() where key != LCSharedKeychainMigration.marker {
                try client.remove(key)
            }
            try client.set(LCSharedKeychainMigration.ready, key: LCSharedKeychainMigration.marker)
            note("clear", status: 0)
        } catch { note("clear", status: (error as NSError).code) }
    }

    private static func note(_ key: String, status: Int) {
        // Item names and status ONLY. Never log values or NSError.userInfo,
        // which third-party wrappers may populate with a complete query.
        lock.lock()
        if status == 0 { lastIssues.removeValue(forKey: key) } else { lastIssues[key] = status }
        lock.unlock()
        let label = LCSharedKeychainMigration.knownKeys.contains(key) ? key : "storage"
        debugLog("[LC_KEYCHAIN] ACCESS item=\(label) status=\(status) pid=\(ProcessInfo.processInfo.processIdentifier)")
    }

    static func authenticationFailure() -> NSError {
        lock.lock(); let issues = lastIssues; lock.unlock()
        let statuses = Set(issues.values)
        if statuses.contains(-34018) {
            return NSError(domain: "LiveContainerRefresh.Configuration", code: 1006,
                userInfo: [NSLocalizedDescriptionKey: "This installation cannot access SideStore's shared Keychain group. Keep the LiveProcess extension and use the same signing team; do not sign out. Check LC_KEYCHAIN diagnostics."])
        }
        if statuses.contains(-25308) || statuses.contains(-25291) || statuses.contains(-25315) {
            return NSError(domain: "com.SideStore.Keychain", code: 1005,
                userInfo: [NSLocalizedDescriptionKey: "Saved sign-in details are temporarily inaccessible. Unlock the iPhone and retry; your account has not been signed out."])
        }
        if statuses.contains(1008) {
            return NSError(domain: "LiveContainerRefresh.Configuration", code: 1008,
                userInfo: [NSLocalizedDescriptionKey: "Conflicting saved SideStore logins were found. Automatic migration stopped without replacing them. Open embedded SideStore to choose the intended account."])
        }
        if let status = statuses.sorted().first(where: { $0 != -25300 }) {
            return NSError(domain: "com.SideStore.Keychain", code: 1009,
                userInfo: [NSLocalizedDescriptionKey: "SideStore Keychain access failed (status \(status)). Your account has not been signed out. Check LC_KEYCHAIN diagnostics."])
        }
        return NSError(domain: "com.SideStore.Authentication", code: 1004,
            userInfo: [NSLocalizedDescriptionKey: "Open embedded SideStore once in this LiveContainer installation so its existing sign-in can be migrated to the shared Keychain, then return and retry. If SideStore itself asks you to sign in, complete that there."])
    }
}
