    static func readString(_ key: String, client: KeychainAccess.Keychain) -> String? {
        guard let data = read(key, client: client) else { return nil }
        guard let value = String(data: data, encoding: .utf8) else {
            note(key, status: 1009); return nil
        }
        return value
    }