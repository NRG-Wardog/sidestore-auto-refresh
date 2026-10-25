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