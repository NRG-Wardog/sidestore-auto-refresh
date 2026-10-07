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