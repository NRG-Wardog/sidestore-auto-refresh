@main struct VerifiedAnisetteRecoveryHarness {
    static func main() async throws {
        LCEmbeddedSharedKeychain.transactionOverride = { try $0() }
        let selected = Store.keychainGroup
        let legacy = Store.processGroup
        let a = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!
        let b = UUID(uuidString: "22222222-BBBB-2222-2222-BBBBBBBBBBBB")!
        let c = UUID(uuidString: "33333333-3333-3333-3333-333333333333")!
        let blob = Data("PRIVATE_BLOB_FOR_A".utf8)
        let rawBlob = Data(("\n" + blob.base64EncodedString() + "\n").utf8)
        let originalID = Data(b.uuidString.lowercased().utf8)
        let selectedValues = ["identifier": originalID, "adiPb": rawBlob,
            "appleIDPassword": Data("PRIVATE_PASSWORD".utf8),
            "signingCertificate": Data("PRIVATE_CERTIFICATE".utf8)]
        let journalKey = "LCAnisetteIdentityRecoveryV1"
        let otp = Data("PRIVATE_OTP".utf8).base64EncodedString()
        let mid = Data("PRIVATE_MID".utf8).base64EncodedString()
        let client = LCEmbeddedSharedKeychain.makeClient()

        func reset(_ sources: [String: [String: Data]]? = nil) {
            Store.data = [selected: selectedValues]
            for (group, values) in sources ?? [legacy: ["identifier": Data(a.uuidString.utf8)]] {
                Store.data[group] = values
            }
            Store.afterSet = nil; Store.failure = 0; Store.writes = 0
            Store.setCalls = 0; Store.removeCalls = 0; Store.setFailAt = nil; Store.removeFailAt = nil
            Store.failSetKey = nil; Store.failSetKeyCount = 0; Store.recoveryQueries = []; Store.logs = []; Store.malformedLegacyRow = false
        }
        func candidate() throws -> LCAnisetteRecoveryCandidate? {
            let snapshot = try LCEmbeddedSharedKeychain.resolveAnisetteSnapshot(client)
            return try LCEmbeddedSharedKeychain.anisetteRecoveryCandidate(for: snapshot, client: client)
        }
        func validProof(_ candidate: LCAnisetteRecoveryCandidate) async throws -> LCAnisetteRecoveryProof {
            let verified = try await candidate.validateNativeOTP { identifier, bytes in
                precondition(identifier == a && bytes == blob)
                return (result: "parsed-headers", oneTimePassword: otp, machineID: mid)
            }
            precondition(verified.result == "parsed-headers")
            return verified.proof
        }
        func expectStateChanged(_ body: () throws -> Void) {
            do { try body(); preconditionFailure("changed/uncertain state admitted") }
            catch { precondition(error as? LCAnisettePairError == .stateChanged) }
        }

        // Candidate discovery and native proof have no Keychain writes. UUID-only
        // legacy data is usable only as an untrusted isolated probe input.
        reset()
        let initial = Store.data
        let first = try candidate()!
        precondition(Store.writes == 0 && Store.data == initial)
        precondition(Set(Store.recoveryQueries) == ["identifier", "adiPb"])
        let proof = try await validProof(first)
        precondition(Store.writes == 0 && Store.data == initial)
        if !LCAnisetteRecoveryPolicy.automaticRecoveryEnabled {
            do {
                _ = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(proof, client: client)
                preconditionFailure("disabled recovery wrote an identifier")
            } catch {
                precondition(error as? LCAnisetteRecoveryError == .automaticRecoveryDisabled)
            }
            precondition(Store.data == initial && Store.writes == 0 && Store.removeCalls == 0)
            Store.data[selected]?[journalKey] = Data("preserve-unreviewed-journal".utf8)
            let held = Store.data
            expectStateChanged { _ = try LCEmbeddedSharedKeychain.resolveAnisetteSnapshot(client) }
            precondition(Store.data == held && Store.writes == 0 && Store.removeCalls == 0)
            // A valid interrupted journal must also hold the actual startup
            // migration/readiness boundaries, before the later pair resolver.
            let expectedID = Data(a.uuidString.utf8)
            let journal = try PropertyListSerialization.data(fromPropertyList: [
                "version": 1, "keys": ["identifier", "adiPb"],
                "original": ["identifier": originalID, "adiPb": rawBlob],
                "expected": ["identifier": expectedID, "adiPb": rawBlob],
                "expectedMarker": Data("native-otp-validated-v1".utf8)
            ], format: .binary, options: 0)
            for ready in [false, true] {
                reset()
                Store.data[selected] = ["identifier": expectedID, "adiPb": rawBlob, journalKey: journal]
                if ready { Store.data[selected]?[LCSharedKeychainMigration.marker] = LCSharedKeychainMigration.ready }
                Store.data[legacy] = ["identifier": expectedID, "adiPb": rawBlob,
                    "appleIDEmailAddress": Data("fixture@example.invalid".utf8),
                    "appleIDPassword": Data("PRIVATE_PASSWORD".utf8),
                    "appleIDAdsid": Data("synthetic-dsid".utf8),
                    "appleIDXcodeToken": Data("PRIVATE_TOKEN".utf8)]
                let beforeStartup = Store.data
                LCEmbeddedSharedKeychain.prepare(client)
                precondition(!LCEmbeddedSharedKeychain.isReady(client))
                expectStateChanged { _ = try LCEmbeddedSharedKeychain.readAuthenticationSnapshot(client) }
                precondition(Store.data == beforeStartup && Store.writes == 0 && Store.removeCalls == 0)
            }
            print("AUTOMATIC_ANISETTE_RECOVERY_DISABLED_PASS")
            return
        }
        let restored = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(proof, client: client)
        precondition(restored.identifier == a && restored.adiBlob == blob)
        precondition(Store.data[selected]?["identifier"] == Data(a.uuidString.utf8))
        precondition(Store.data[selected]?["adiPb"] == rawBlob)
        precondition(Store.data[legacy] == initial[legacy])
        for key in ["appleIDPassword", "signingCertificate"] { precondition(Store.data[selected]?[key] == selectedValues[key]) }
        precondition(Store.data[selected]?[journalKey] == nil)
        let committed = Store.data
        expectStateChanged { _ = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(proof, client: client) }
        precondition(Store.data == committed)

        // Decoded blob equality is required; original selected encoding is kept.
        reset([legacy: ["identifier": Data(a.uuidString.lowercased().utf8),
                        "adiPb": Data(blob.base64EncodedString().utf8)],
               "TEAM.second.legacy": ["identifier": Data(a.uuidString.utf8)]])
        let sameIDProof = try await validProof(candidate()!)
        _ = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(sameIDProof, client: client)
        precondition(Store.data[selected]?["adiPb"] == rawBlob)
        reset([:]); let absentCandidate = try candidate(); precondition(absentCandidate == nil && Store.writes == 0)
        reset([legacy: ["identifier": originalID]])
        let matchingCandidate = try candidate(); precondition(matchingCandidate == nil && Store.writes == 0)
        reset(); Store.data[selected]?.removeValue(forKey: "adiPb")
        let bloblessCandidate = try candidate(); precondition(bloblessCandidate == nil && Store.writes == 0 && Store.recoveryQueries.isEmpty)

        let invalidSources: [([String: [String: Data]], LCAnisetteRecoveryError)] = [
            ([legacy: ["identifier": Data("not-a-UUID PRIVATE_IDENTIFIER".utf8)]], .invalidLegacyPair),
            ([legacy: ["adiPb": rawBlob]], .invalidLegacyPair),
            ([legacy: ["identifier": Data(a.uuidString.utf8), "adiPb": Data("!".utf8)]], .invalidLegacyPair),
            ([legacy: ["identifier": Data(a.uuidString.utf8), "adiPb": Data(Data("OTHER_BLOB".utf8).base64EncodedString().utf8)]], .legacyBlobMismatch),
            ([legacy: ["identifier": Data(a.uuidString.utf8)], "TEAM.other": ["identifier": Data(c.uuidString.utf8)]], .ambiguousLegacyIdentity)
        ]
        for (sources, expected) in invalidSources {
            reset(sources)
            let before = Store.data
            do { _ = try candidate(); preconditionFailure("invalid legacy candidate admitted") }
            catch { precondition(error as? LCAnisetteRecoveryError == expected) }
            precondition(Store.data == before && Store.writes == 0)
        }
        reset(); Store.malformedLegacyRow = true
        do { _ = try candidate(); preconditionFailure("malformed Security row ignored") }
        catch { precondition(error as? LCAnisetteRecoveryError == .invalidLegacyPair) }
        precondition(Store.writes == 0)
        reset()
        do {
            _ = try LCAnisetteRecoveryPolicy.candidate(selected: LCAnisetteStoredPair(identifier: originalID, blob: rawBlob),
                selectedGroup: selected, items: [
                    LCLegacyKeychainItem(group: legacy, key: "identifier", data: Data(a.uuidString.utf8)),
                    LCLegacyKeychainItem(group: legacy, key: "identifier", data: Data(c.uuidString.utf8))])
            preconditionFailure("conflicting duplicate source admitted")
        } catch { precondition(error as? LCAnisetteRecoveryError == .ambiguousLegacyIdentity) }

        // Nonthrowing native callbacks with empty or malformed output still
        // cannot mint proof. No fixed OTP/MID byte length is assumed.
        for bad in ["", "!", "YQ", "YQ==\n", "YQ===", "YR==", "PRIVATE_OUTPUT"] {
            for invalidOTP in [true, false] {
                reset(); let found = try candidate()!; let before = Store.data
                do {
                    _ = try await found.validateNativeOTP { _, _ in
                        (result: 1, oneTimePassword: invalidOTP ? bad : otp, machineID: invalidOTP ? mid : bad)
                    }
                    preconditionFailure("invalid native output minted proof")
                } catch { precondition(error as? LCAnisetteRecoveryError == .invalidNativeProof) }
                precondition(Store.data == before && Store.writes == 0)
            }
        }
        reset(); let rejected = try candidate()!; let beforeReject = Store.data
        do {
            let _: (proof: LCAnisetteRecoveryProof, result: Int) = try await rejected.validateNativeOTP { _, _ in
                throw NSError(domain: "NativeProbeFixture", code: -45061)
            }
            preconditionFailure("failed native OTP minted proof")
        } catch { precondition((error as NSError).code == -45061) }
        precondition(Store.data == beforeReject && Store.writes == 0)

        // Raw selected or source changes after proof are never overwritten.
        for changedKey in ["identifier", "adiPb", "legacy"] {
            reset(); let p = try await validProof(candidate()!)
            if changedKey == "legacy" { Store.data[legacy]?["identifier"] = Data(c.uuidString.utf8) }
            else { Store.data[selected]?[changedKey] = Data("PRIVATE_CONCURRENT_CHANGE".utf8) }
            let changed = Store.data
            expectStateChanged { _ = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(p, client: client) }
            precondition(Store.data == changed && Store.writes == 0)
        }
        // Mutating the source as journal publication completes is also caught
        // by the second receipt check, before touching the identifier.
        reset(); let racedProof = try await validProof(candidate()!)
        Store.afterSet = { _, key, _ in
            if key == journalKey { Store.data[legacy]?["identifier"] = Data(c.uuidString.utf8) }
        }
        expectStateChanged { _ = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(racedProof, client: client) }
        precondition(Store.data[selected] == selectedValues)

        // Failed journal publication cannot make an uncertain journal look
        // safe. The normal reader later clears only the exact retained state.
        reset(); let journalProof = try await validProof(candidate()!)
        Store.afterSet = { _, key, _ in if key == journalKey { Store.failure = -25291 } }
        expectStateChanged { _ = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(journalProof, client: client) }
        precondition(Store.data[selected]?["identifier"] == originalID && Store.data[selected]?["adiPb"] == rawBlob)
        precondition(Store.data[selected]?[journalKey] != nil)
        Store.failure = 0; Store.afterSet = nil
        let priorAfterJournalFailure = try LCEmbeddedSharedKeychain.resolveAnisetteSnapshot(client)
        precondition(priorAfterJournalFailure.identifier == b && Store.data[selected] == selectedValues)

        // A failed identifier write is rolled back and byte-for-byte checked.
        reset(); let writeProof = try await validProof(candidate()!)
        Store.setFailAt = 2
        do { _ = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(writeProof, client: client); preconditionFailure("write failure swallowed") }
        catch { precondition((error as NSError).code == -25291) }
        precondition(Store.data[selected] == selectedValues)
        reset(); let afterWriteProof = try await validProof(candidate()!)
        var failAfterWrite = true
        Store.afterSet = { _, key, _ in
            if key == "identifier" && failAfterWrite { failAfterWrite = false; throw NSError(domain: "StorageFixture", code: -25291) }
        }
        do { _ = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(afterWriteProof, client: client); preconditionFailure("partial write failure swallowed") }
        catch { precondition((error as NSError).code == -25291) }
        precondition(Store.data[selected] == selectedValues)

        // Interrupted rollback remains held by the journal. A later reader may
        // accept only its exact prior/intended outcome, never a mixed pair.
        reset(); let uncertainProof = try await validProof(candidate()!)
        var interruptRollback = true
        Store.afterSet = { _, key, _ in
            if key == "identifier" && interruptRollback {
                interruptRollback = false
                Store.failSetKey = "identifier"; Store.failSetKeyCount = 1
                throw NSError(domain: "StorageFixture", code: -25291)
            }
        }
        expectStateChanged { _ = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(uncertainProof, client: client) }
        precondition(Store.data[selected]?[journalKey] != nil)
        Store.afterSet = nil
        let needsReconciliation = try LCEmbeddedSharedKeychain.storageRequiresReconciliation(client)
        precondition(needsReconciliation)
        let reconciled = try LCEmbeddedSharedKeychain.resolveAnisetteSnapshot(client)
        precondition(reconciled.identifier == a && reconciled.adiBlob == blob)
        precondition(Store.data[selected]?[journalKey] == nil)

        // Capture only in-memory snapshots at the exact durable boundaries to
        // model process termination before cleanup (without any device writes).
        reset(); let crashProof = try await validProof(candidate()!)
        var afterJournal: [String: [String: Data]]?
        var afterIdentifier: [String: [String: Data]]?
        Store.afterSet = { _, key, _ in
            if key == journalKey { afterJournal = Store.data }
            if key == "identifier" { afterIdentifier = Store.data }
        }
        _ = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(crashProof, client: client)
        Store.afterSet = nil
        for (snapshot, expectedID) in [(afterJournal!, b), (afterIdentifier!, a)] {
            Store.data = snapshot
            let loaded = try LCEmbeddedSharedKeychain.resolveAnisetteSnapshot(client)
            precondition(loaded.identifier == expectedID && loaded.adiBlob == blob)
            precondition(Store.data[selected]?[journalKey] == nil && Store.data[selected]?["adiPb"] == rawBlob)
        }
        Store.data = afterIdentifier!
        Store.data[selected]?["adiPb"] = Data(Data("MIXED_BLOB".utf8).base64EncodedString().utf8)
        let mixed = Store.data
        expectStateChanged { _ = try LCEmbeddedSharedKeychain.resolveAnisetteSnapshot(client) }
        precondition(Store.data == mixed)
        reset(); Store.data[selected]?[journalKey] = Data("PRIVATE_MALFORMED_JOURNAL".utf8)
        let malformed = Store.data
        expectStateChanged { _ = try LCEmbeddedSharedKeychain.resolveAnisetteSnapshot(client) }
        precondition(Store.data == malformed)

        // Cancellation at every native/commit boundary forbids publication or
        // performs verified rollback. Task cancellation does not cancel main.
        for location in ["beforeProbe", "afterProbe", "beforeCommit", "afterJournal", "afterIdentifier"] {
            reset(); let found = try candidate()!
            let task = Task {
                do {
                    if location == "beforeProbe" { withUnsafeCurrentTask { $0?.cancel() } }
                    let verified = try await found.validateNativeOTP { _, _ in
                        if location == "afterProbe" { withUnsafeCurrentTask { $0?.cancel() } }
                        return (result: 1, oneTimePassword: otp, machineID: mid)
                    }
                    if location == "beforeCommit" { withUnsafeCurrentTask { $0?.cancel() } }
                    Store.afterSet = { _, key, _ in
                        if (location == "afterJournal" && key == journalKey) ||
                           (location == "afterIdentifier" && key == "identifier") {
                            withUnsafeCurrentTask { $0?.cancel() }
                        }
                    }
                    _ = try LCEmbeddedSharedKeychain.commitAnisetteRecovery(verified.proof, client: client)
                    preconditionFailure("cancelled recovery committed")
                } catch { precondition(error is CancellationError) }
            }
            await task.value
            precondition(Store.data[selected] == selectedValues)
        }
        for printable in [String(describing: first), String(reflecting: first),
                          String(describing: proof), String(reflecting: proof)] + Store.logs {
            precondition(!printable.contains("PRIVATE_") && !printable.contains(a.uuidString))
            precondition(!printable.contains(b.uuidString) && !printable.contains(legacy))
        }
        print("VERIFIED_ANISETTE_RECOVERY_PASS")
    }
}
