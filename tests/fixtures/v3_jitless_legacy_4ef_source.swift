struct V3SetupReadinessObservation: Equatable {
    let readiness: V3JITLessReadiness
    let sourceFactRevision: UInt64
    let activeCertificateAvailable: Bool?
}

enum V3SetupReadinessObservationPolicy {
    static func shouldFetchLocalReadiness(_ observation: V3SetupReadinessObservation?) -> Bool {
        observation == nil
    }

    static func shouldFetchLocalReadiness(_ observation: V3SetupReadinessObservation?,
                                          currentFactRevision: UInt64) -> Bool {
        guard let observation else { return true }
        return observation.sourceFactRevision != currentFactRevision
    }

    static func mayApplyFreshObservation(sourceFactRevision: UInt64,
                                         currentFactRevision: UInt64) -> Bool {
        sourceFactRevision == currentFactRevision
    }
}

    func beginSetupFactObservation() -> UInt64 {
        setupFactRevision &+= 1
        return setupFactRevision
    }

    func isSetupFactRevisionCurrent(_ revision: UInt64) -> Bool {
        V3SetupReadinessObservationPolicy.mayApplyFreshObservation(
            sourceFactRevision: revision, currentFactRevision: setupFactRevision)
    }

    func invalidateSetupFacts() {
        setupFactRevision &+= 1
        setupFactObservation = .pending
        setupFactLastAttemptAt = nil
        wifiAvailable = nil
        jitlessReadinessObservation = nil
        jitlessReadiness = nil
        jitlessActiveCertificateAvailable = nil
    }

    func recordJITLessReadiness(_ readiness: V3JITLessReadiness,
                                activeCertificateAvailable: Bool? = nil,
                                revision: UInt64? = nil) {
        if let revision, !isSetupFactRevisionCurrent(revision) { return }
        jitlessReadinessObservation = V3SetupReadinessObservation(
            readiness: readiness,
            sourceFactRevision: revision ?? setupFactRevision,
            activeCertificateAvailable: activeCertificateAvailable)
        jitlessReadiness = readiness
        jitlessActiveCertificateAvailable = activeCertificateAvailable
    }

private struct V3PKCS12CertificateFacts {
    let teamIdentifier: String
    let identitySHA256: String
}

private struct V3JITLessStatusResult {
    let readiness: V3JITLessReadiness
    let detail: String
    let hasImportedCopy: Bool
    let certificateFacts: V3PKCS12CertificateFacts?
}

private enum V3JITLessStatusReader {
    static func read(serviceCertificate: [String: Any]) async -> V3JITLessStatusResult {
        let osMajor = ProcessInfo.processInfo.operatingSystemVersion.majorVersion
        let active = serviceCertificate["active"] as? Bool ?? false
        let activeStatus = serviceCertificate["validation"] as? String ?? "unknown"
        let activeFingerprint = serviceCertificate["certificateIdentitySHA256"] as? String ?? ""
        let data = LCUtils.certificateData() as Data?
        let password = LCSharedUtils.certificatePassword()
        let facts = data.flatMap { bytes in password.flatMap { parse(bytes, password: $0) } }
        let identitiesMatch: Bool? = {
            guard active, !activeFingerprint.isEmpty, let facts else { return nil }
            return activeFingerprint == facts.identitySHA256
        }()
        var validationStatus: Int?
        var validationFailed = false
        if data != nil && password != nil {
            let validation = await validateLocalCopy()
            validationStatus = validation.status
            validationFailed = validation.failed
        }
        let state = V3JITLessReadinessPolicy.evaluate(
            osMajor: osMajor,
            hasCopy: data != nil && password != nil && facts != nil,
            activeCertificateExists: active,
            activeCertificateStatus: activeStatus,
            identitiesMatch: identitiesMatch,
            validationStatus: validationStatus,
            validationFailed: validationFailed)
        if osMajor >= 26 && !active {
            return V3JITLessStatusResult(readiness: state,
                detail: V3JITLessPresentation.present(.activeCertificateMissing).detail,
                hasImportedCopy: data != nil, certificateFacts: facts)
        }
        return V3JITLessStatusResult(readiness: state, detail: detail(for: state),
            hasImportedCopy: data != nil, certificateFacts: facts)
    }

    static func parse(_ data: Data, password: String) -> V3PKCS12CertificateFacts? {
        var importedItems: CFArray?
        let options = [kSecImportExportPassphrase as String: password] as CFDictionary
        guard SecPKCS12Import(data as CFData, options, &importedItems) == errSecSuccess,
              let item = (importedItems as? [[String: Any]])?.first,
              let identityValue = item[kSecImportItemIdentity as String] else { return nil }
        let identityObject = identityValue as AnyObject
        guard CFGetTypeID(identityObject as CFTypeRef) == SecIdentityGetTypeID() else { return nil }
        let identity = unsafeBitCast(identityObject, to: SecIdentity.self)
        var certificate: SecCertificate?
        guard SecIdentityCopyCertificate(identity, &certificate) == errSecSuccess,
              let certificate,
              let team = LCUtils.getCertTeamId(withKeyData: data, password: password) else { return nil }
        let der = SecCertificateCopyData(certificate) as Data
        let fingerprint = SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
        return V3PKCS12CertificateFacts(teamIdentifier: team, identitySHA256: fingerprint)
    }

    private static func validateLocalCopy() async -> (status: Int?, failed: Bool) {
        await withCheckedContinuation { (continuation: CheckedContinuation<(Int?, Bool), Never>) in
            LCUtils.validateCertificate { status, _, _, error in
                continuation.resume(returning: (Int(status), error != nil))
            }
        }
    }

    private static func detail(for state: V3JITLessReadiness) -> String {
        // V3_JITLESS_PRESENTATION_V1: one source of truth for the wording, so
        // Setup Assistant, Health and Settings cannot describe the same state
        // three different ways.
        V3JITLessPresentation.present(state).detail
    }
}

.onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3CanonicalJITLessCertificateUpdated"))) { _ in
            // V3_AWAITABLE_RELOAD_V1: a certificate import just changed
            // authoritative state. The snapshot is awaited before Setup is
            // reopened, so the assistant never recomputes JIT-Less from the
            // pre-import snapshot.
            Task {
                status.invalidateSetupFacts()
                let outcome = await status.reloadAndWait()
                guard V3SetupReloadRecomputePolicy.mayRecompute(
                    outcome: outcome.setupSnapshotOutcome) else {
                    status.invalidateSetupFacts()
                    status.notice = "Setup status was not refreshed. Reload Status before continuing."
                    return
                }
                if status.returnToSetupAfterJITLess {
                    status.returnToSetupAfterJITLess = false
                    status.setupPresented = true
                }
            }
        }
