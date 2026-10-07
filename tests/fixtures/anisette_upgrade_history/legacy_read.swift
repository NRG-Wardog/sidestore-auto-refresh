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