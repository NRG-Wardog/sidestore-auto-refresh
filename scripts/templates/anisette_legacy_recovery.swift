// LC_ANISETTE_VERIFIED_LEGACY_RECOVERY_V1
// Restore a preserved identity only after an isolated, OTP-only native proof.
// Neither candidate enumeration nor a failed probe changes Keychain bytes.
private enum LCAnisetteIsolatedProbe {
    enum LocalFailure: Error { case temporaryStorage }

    static func run(libraries: URL, identifier: UUID, blob: Data,
                    headers: AnisetteRequestHeaders) async throws
        -> (result: ALTAnisetteData, oneTimePassword: String, machineID: String) {
        try Task.checkCancellation()
        var pattern = Array(FileManager.default.temporaryDirectory
            .appendingPathComponent("LCAnisetteRecovery.XXXXXX").path.utf8CString)
        let root: URL = try pattern.withUnsafeMutableBufferPointer { buffer in
            guard let pointer = buffer.baseAddress, let made = mkdtemp(pointer) else {
                throw LocalFailure.temporaryStorage
            }
            return URL(fileURLWithPath: String(cString: made), isDirectory: true)
        }
        // Capture the outcome so cleanup runs exactly once, including after
        // cancellation. Only the exclusively-created root is removed.
        let outcome: Result<(ALTAnisetteData, String, String), Error>
        do {
            let raw = try await IsolatedAnisetteOTPProvider.getExistingHeaders(
                libDir: libraries, provisioningDir: root, identifier: identifier,
                existingBlob: blob, headers: headers)
            let data = try AnisetteDataManager.validateAndCreateAnisetteData(from: raw)
            guard let otp = raw[AnisetteConstants.Headers.oneTimePassword],
                  let mid = raw[AnisetteConstants.Headers.machineID] else {
                throw LCAnisetteRecoveryError.invalidNativeProof
            }
            outcome = .success((data, otp, mid))
        } catch { outcome = .failure(error) }
        do { try FileManager.default.removeItem(at: root) }
        catch { throw LocalFailure.temporaryStorage }
        try Task.checkCancellation()
        let (data, otp, mid) = try outcome.get()
        return (data, otp, mid)
    }
}

extension OnDeviceAnisetteManager {
    private func recoverVerifiedLegacyIdentity(
        after original: Error, snapshot: LCEmbeddedAnisetteSnapshot,
        headers: AnisetteRequestHeaders
    ) async throws -> ALTAnisetteData {
        let blobState: V3AnisetteAttemptContext.BlobState = snapshot.adiBlob == nil ? .fresh : .existing
        func diagnosed(_ status: V3AnisetteAttemptContext.Recovery,
                       underlying: Error? = nil, probeError: Error? = nil) -> Error {
            var probeEvidence: V3AnisetteNativeEvidence?
            if let native = probeError as? AnisetteKit.AnisetteError,
               case .adiError(let code, let description) = native {
                probeEvidence = .capture(code: code, description: description)
            }
            return V3AnisetteAttemptError(underlying: underlying ?? original,
                context: V3AnisetteAttemptContext(blobState: blobState, recovery: status,
                    probeEvidence: probeEvidence))
        }
        try Task.checkCancellation()
        if original is CancellationError { throw original }
        guard let native = original as? AnisetteKit.AnisetteError,
              case .adiError(let code, let description) = native else { throw original }
        // Numeric equality alone does not identify the operation. Match the
        // pinned native OTP producer as well; setup/provisioning never recover.
        guard code == -45061,
              V3AnisetteNativeEvidence.capture(code: code, description: description).phase == .nativeOTP,
              let existingBlob = snapshot.adiBlob else { throw diagnosed(.notAttempted) }

        // Differential control: a clean VM may repair runtime/staging state
        // without changing the identity at all. Never infer a legacy mismatch
        // merely because a probe that also changes staging happens to succeed.
        let libraries = provider.libsDir
        do {
            let current = try await LCAnisetteIsolatedProbe.run(libraries: libraries,
                identifier: snapshot.identifier, blob: existingBlob, headers: headers)
            try LCAnisetteRecoveryPolicy.validateNativeOTP(oneTimePassword: current.oneTimePassword,
                machineID: current.machineID)
            try Task.checkCancellation()
            try Keychain.shared.validateAnisetteSnapshot(snapshot)
            try Task.checkCancellation()
            debugLog("[LC_ANISETTE_RECOVERY] outcome=isolated_current_pair")
            return current.result
        } catch {
            try Task.checkCancellation()
            if error is CancellationError { throw error }
            if let blocked = error as? LCAnisettePairError { throw diagnosed(.stateChanged, underlying: blocked) }
            if error is LCAnisetteIsolatedProbe.LocalFailure { throw diagnosed(.temporaryStorageUnavailable) }
            if error is LCAnisetteRecoveryError { throw diagnosed(.invalidNativeProof) }
            guard let control = error as? AnisetteKit.AnisetteError,
                  case .adiError(let controlCode, let controlDescription) = control,
                  controlCode == -45061,
                  V3AnisetteNativeEvidence.capture(code: controlCode, description: controlDescription).phase == .nativeOTP else {
                throw diagnosed(.currentProbeRejected, probeError: error)
            }
        }

        let candidate: LCAnisetteRecoveryCandidate
        do {
            guard let found = try Keychain.shared.anisetteRecoveryCandidate(for: snapshot) else {
                throw diagnosed(.noLegacyCandidate)
            }
            candidate = found
        } catch let failure as LCAnisetteRecoveryError {
            switch failure {
            case .ambiguousLegacyIdentity: throw diagnosed(.ambiguousLegacyIdentity)
            case .legacyBlobMismatch: throw diagnosed(.legacyBlobMismatch)
            case .invalidLegacyPair: throw diagnosed(.invalidLegacyPair)
            case .invalidNativeProof: throw diagnosed(.invalidNativeProof)
            }
        } catch let blocked as LCAnisettePairError {
            throw diagnosed(.stateChanged, underlying: blocked)
        } catch let reported as V3AnisetteAttemptError { throw reported }
        catch {
            try Task.checkCancellation()
            throw diagnosed(.legacyReadFailed)
        }

        let validated: (proof: LCAnisetteRecoveryProof, result: ALTAnisetteData)
        do {
            validated = try await candidate.validateNativeOTP { identifier, blob in
                try await LCAnisetteIsolatedProbe.run(libraries: libraries,
                    identifier: identifier, blob: blob, headers: headers)
            }
        } catch {
            try Task.checkCancellation()
            if error is CancellationError { throw error }
            if error is LCAnisetteIsolatedProbe.LocalFailure {
                throw diagnosed(.temporaryStorageUnavailable)
            }
            if error is LCAnisetteRecoveryError { throw diagnosed(.invalidNativeProof) }
            throw diagnosed(.probeRejected, probeError: error)
        }
        try Task.checkCancellation()
        do { _ = try Keychain.shared.commitAnisetteRecovery(validated.proof) }
        catch let blocked as LCAnisettePairError {
            throw diagnosed(.stateChanged, underlying: blocked)
        } catch {
            try Task.checkCancellation()
            throw diagnosed(.restoreFailed)
        }
        debugLog("[LC_ANISETTE_RECOVERY] outcome=verifiedLegacyIdentityRestored")
        return validated.result
    }
}
