    static func write(_ key: String, data: Data?, client: KeychainAccess.Keychain) {
        guard installedGroup != nil else { note(key, status: -34018); return }
        do {
            try withSharedTransaction {
                try writeOne(key, data: data, client: client)
            }
            note(key, status: 0)
        } catch { note(key, status: (error as NSError).code) }
    }