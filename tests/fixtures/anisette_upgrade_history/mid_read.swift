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
            if data == nil && !ready && LCSharedKeychainMigration.supportsLegacyCertificateFallback(key) {
                // Preserve certificate-only/imported-certificate setups before
                // an Apple login is migrated. This fallback is READ ONLY and
                // cannot make an authentication preflight pass.
                // Anisette identifier/adiPb must use the selected namespace on
                // both sides of the auth commit. Borrowing them only before
                // ready would change the device identity between successful
                // viewDeveloper authentication and the following fetchTeams.
                // A coherent full migration above already preserves both keys.
                let legacy = KeychainAccess.Keychain(service: service)
                    .accessibility(.afterFirstUnlock).synchronizable(true)
                data = try legacy.getData(key)
            }
            if ready { note("migration", status: 0) }
            note(key, status: data == nil ? -25300 : 0)
            return data
        } catch { note(key, status: (error as NSError).code); return nil }
    }