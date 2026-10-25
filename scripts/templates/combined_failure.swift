import Foundation
import CoreFoundation

public struct CombinedRefreshTargetPlan: Equatable {
    public let requestedIDs: [String]
    public let attemptedIDs: [String]
    public let skippedIDs: [String]
}

public enum CombinedRefreshTargetPolicy {
    public static func plan(requestedIDs: [String], runningIDs: Set<String>,
                            isCorrelatedManualRun: Bool) -> CombinedRefreshTargetPlan {
        let attempted = isCorrelatedManualRun
            ? requestedIDs
            : requestedIDs.filter { !runningIDs.contains($0) }
        let attemptedSet = Set(attempted)
        return CombinedRefreshTargetPlan(requestedIDs: requestedIDs,
            attemptedIDs: attempted,
            skippedIDs: requestedIDs.filter { !attemptedSet.contains($0) })
    }
}

// LC_REFRESH_METADATA_SANITIZED_V1: never forward arbitrary saved result dictionaries.
public enum CombinedVerification {
    static let uncertainMutationKey = "liveContainerAutoRefreshUncertainMutationRunID"
    static func clearUncertainty(_ defaults: UserDefaults, runID: String) {
        guard defaults.string(forKey: uncertainMutationKey) == runID else { return }
        defaults.removeObject(forKey: uncertainMutationKey)
    }
    // Complete terminal results establish completion, not verified refresh success.
    // Empty, duplicated or omitted app results leave mutation completion uncertain.
    // The Setup Assistant reuses this exact contract: a partial manifest (for
    // example two expected apps but only one result) never verifies.
    public static func hasCompleteTerminalResults(_ manifest: [String: Any], runID: String) -> Bool {
        guard UUID(uuidString: runID) != nil, manifest["run_id"] as? String == runID,
              manifest["version"] as? Int == 2, manifest["schema"] as? String == "LiveContainerRefreshManifestV2",
              let expected = manifest["expected_ids"] as? [String], !expected.isEmpty, expected.count <= 1024,
              expected.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 512 }), Set(expected).count == expected.count,
              (manifest["host_handoff"] == nil || strictBoolean(manifest["host_handoff"]) != nil),
              targetCoverageIsValid(manifest, expected: expected),
              let entries = manifest["results"] as? [[String: Any]], entries.count == expected.count else { return false }
        var received = Set<String>()
        for entry in entries {
            guard let identifier = entry["bundle_id"] as? String, expected.contains(identifier),
                  received.insert(identifier).inserted,
                  let success = entry["success"] as? NSNumber, CFGetTypeID(success) == CFBooleanGetTypeID() else { return false }
        }
        return received == Set(expected)
    }
    private static func strictBoolean(_ value: Any?) -> Bool? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        return number.boolValue
    }
    private static func targetCoverageIsValid(_ manifest: [String: Any], expected: [String]) -> Bool {
        guard manifest["requested_ids"] != nil || manifest["skipped_ids"] != nil else { return true }
        guard let requested = manifest["requested_ids"] as? [String],
              let skipped = manifest["skipped_ids"] as? [String],
              !requested.isEmpty, requested.count <= 1024, skipped.count <= 1024,
              requested.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 512 }),
              skipped.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 512 }),
              Set(requested).count == requested.count, Set(skipped).count == skipped.count else { return false }
        let expectedSet = Set(expected), skippedSet = Set(skipped)
        return expectedSet.isDisjoint(with: skippedSet) &&
            expectedSet.union(skippedSet) == Set(requested)
    }
    static func sanitized(_ payload: [String: Any], runID: String) -> [String: Any] {
        guard let manifest = payload["liveContainerAutoRefreshVerification"] as? [String: Any],
              manifest["run_id"] as? String == runID,
              let expected = manifest["expected_ids"] as? [String], expected.count <= 1024,
              expected.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 512 }),
              targetCoverageIsValid(manifest, expected: expected),
              let entries = manifest["results"] as? [[String: Any]],
              entries.count == expected.count,
              entries.allSatisfy({ entry in
                  guard let identifier = entry["bundle_id"] as? String,
                        expected.contains(identifier), strictBoolean(entry["success"]) != nil else { return false }
                  return true
              }),
              Set(entries.map { $0["bundle_id"] as? String ?? "" }) == Set(expected) else { return [:] }
        let manifestHostHandoff: Bool?
        if let rawHostHandoff = manifest["host_handoff"] {
            guard let value = strictBoolean(rawHostHandoff) else { return [:] }
            manifestHostHandoff = value
        } else {
            manifestHostHandoff = nil
        }
        let outerHostHandoffRunID: String?
        if let rawRunID = payload["liveContainerAutoRefreshHostHandoffRunID"] {
            guard let value = rawRunID as? String else { return [:] }
            outerHostHandoffRunID = value
        } else {
            outerHostHandoffRunID = nil
        }
        let outerHostHandoff: Bool?
        if let rawHostHandoff = payload["liveContainerAutoRefreshHostHandoff"] {
            guard let value = strictBoolean(rawHostHandoff) else { return [:] }
            outerHostHandoff = value
        } else {
            outerHostHandoff = nil
        }
        let hasCurrentHostHandoff = outerHostHandoffRunID == runID
        // The producer persists true plus this run ID before writing the
        // manifest's copied host_handoff flag. Never turn an incomplete or
        // contradictory current-run handoff into false at the XPC boundary.
        if hasCurrentHostHandoff {
            guard manifestHostHandoff == true, outerHostHandoff == true else { return [:] }
        } else {
            // A true manifest/outer marker without its matching run ID is
            // incomplete evidence. A normal refresh may omit the outer marker
            // or carry explicit false values.
            guard manifestHostHandoff != true, outerHostHandoff != true else { return [:] }
        }
        var result: [String: Any] = ["version": 2, "schema": "LiveContainerRefreshManifestV2", "run_id": runID, "expected_ids": expected]
        if let requested = manifest["requested_ids"] as? [String] { result["requested_ids"] = requested }
        if let skipped = manifest["skipped_ids"] as? [String] { result["skipped_ids"] = skipped }
        if let date = manifest["date"] as? Date { result["date"] = date }
        if let manifestHostHandoff { result["host_handoff"] = manifestHostHandoff }
        result["results"] = entries.map { entry -> [String: Any] in
            guard let identifier = entry["bundle_id"] as? String, expected.contains(identifier),
                  let success = strictBoolean(entry["success"]) else { return [:] }
            var item: [String: Any] = ["bundle_id": identifier, "success": success]
            for key in ["refreshed_date", "expiration_date"] { if let value = entry[key] as? Date { item[key] = value } }
            if !success {
                let native = NSError(domain: entry["error_domain"] as? String ?? "redacted", code: entry["error_code"] as? Int ?? 0,
                    userInfo: [NSLocalizedDescriptionKey: entry["error"] as? String ?? ""])
                let preserved = (entry["failure"] as? [String: Any]).flatMap { CombinedFailure.decode($0, expectedID: runID) }
                let failure = preserved ?? CombinedFailure.capture(native, operation: "refresh", stage: .refreshVerification, id: runID)
                let safeUnderlying = CombinedFailure.safeWireUnderlying(domain: failure.underlyingDomain,
                    code: failure.underlyingCode)
                item["error"] = failure.localizedDescription
                item["error_code"] = safeUnderlying.code; item["error_domain"] = safeUnderlying.domain
                item["failure"] = failure.wire
            }
            return item
        }
        var safe: [String: Any] = ["liveContainerAutoRefreshVerification": result]
        if hasCurrentHostHandoff {
            safe["liveContainerAutoRefreshHostHandoffRunID"] = runID
            safe["liveContainerAutoRefreshHostHandoff"] = true
            for key in ["liveContainerAutoRefreshHostHandoffStartedAt", "liveContainerAutoRefreshHostPreviousExpiration"] {
                if let value = payload[key] as? Date { safe[key] = value }
            }
        }
        return safe
    }
}


// LC_STRUCTURED_FAILURE_V1: fixed vocabulary, no arbitrary userInfo/descriptions on the wire.
public struct CombinedFailure: Error, LocalizedError {
    public struct LaunchContext: Equatable {
        public static let bridgeErrorDomain = "io.sidestore.LiveContainer.ExtensionLaunch"
        public static let bridgeNoIdentifierCode = 1
        public enum Step: String {
            case hostBundleUnavailable, missingPluginDirectory, liveProcessBundleMissing, liveProcessBundleUnreadable
            case bundleIdentifierMissing, executableMetadataMissing, executableFileMissing
            case extensionFactory, extensionFactoryNil, listenerCreation
            case requestCallbackNoIdentifier, requestCancellation, requestInterruption
            case requestCallbackError, processIdentifierUnavailable, xpcRemoteObjectError
            case xpcInvalidation, xpcPeerRejected, readinessProbe, startupTimeout, connectionStopped, unknown
        }
        public enum Kind: String {
            case extensionNotFound = "extension_not_found"
            case executableLoadFailure = "executable_load_failure"
            case signatureOrEntitlementRejection = "signature_or_entitlement_rejection"
            case dependencyLoadFailure = "dependency_load_failure"
            case bootstrapFailure = "bootstrap_failure"
            case xpcConnectionFailure = "xpc_connection_failure"
            case unknown
        }
        public struct Cause: Equatable {
            public let domain: String
            public let code: Int?
        }

        public let observerRole: String
        public let targetRole: String
        public let osVersion: String
        public let runtimeArchitecture: String
        public let sourceStep: Step
        public let kind: Kind
        public let requestIdentifierObserved: String
        public let pidObserved: String
        public let xpcAccepted: String
        public let applicationReadyObserved: String
        public let peerPIDRejected: String
        public private(set) var errorChain: [Cause]

        public init(error: Error? = nil, sourceStep: Step,
                    requestIdentifierObserved: Bool? = nil, pidObserved: Bool? = nil,
                    xpcAccepted: Bool? = nil, applicationReadyObserved: Bool? = nil,
                    peerPIDRejected: Bool? = nil) {
            observerRole = "host"
            targetRole = "LiveProcess"
            let version = ProcessInfo.processInfo.operatingSystemVersion
            osVersion = "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"
            #if arch(arm64e)
            runtimeArchitecture = "arm64e"
            #elseif arch(arm64)
            runtimeArchitecture = "arm64"
            #elseif arch(x86_64)
            runtimeArchitecture = "x86_64"
            #elseif arch(i386)
            runtimeArchitecture = "i386"
            #else
            runtimeArchitecture = "unknown"
            #endif
            self.sourceStep = sourceStep

            self.requestIdentifierObserved = Self.observation(requestIdentifierObserved)
            self.pidObserved = Self.observation(pidObserved)
            self.xpcAccepted = Self.observation(xpcAccepted)
            self.applicationReadyObserved = Self.observation(applicationReadyObserved)
            self.peerPIDRejected = Self.observation(peerPIDRejected)

            let causes = Self.safeErrorChain(error)
            errorChain = causes
            kind = Self.classify(sourceStep: sourceStep, errorChain: causes)
        }

        public var technicalDetails: String {
            let causes = errorChain.map { cause in
                cause.code.map { "\(cause.domain):\($0)" } ?? "\(cause.domain):unknown"
            }.joined(separator: ">")
            return " launch_observer_role=\(observerRole) launch_target_role=\(targetRole) launch_os=\(osVersion) launch_arch=\(runtimeArchitecture)" +
                " launch_source_step=\(sourceStep.rawValue) launch_failure_kind=\(kind.rawValue)" +
                " launch_request_id_observed=\(requestIdentifierObserved) launch_pid_observed=\(pidObserved)" +
                " launch_xpc_accepted=\(xpcAccepted) launch_application_ready=\(applicationReadyObserved)" +
                " launch_peer_pid_rejected=\(peerPIDRejected) launch_error_chain=\(causes.isEmpty ? "none" : causes)"
        }

        private static func observation(_ value: Bool?) -> String {
            guard let value else { return "unknown" }
            return value ? "yes" : "no"
        }

        public func retainingErrorChain(from prior: LaunchContext?) -> LaunchContext {
            guard let prior, !prior.errorChain.isEmpty else { return self }
            guard !errorChain.isEmpty else {
                var enriched = self
                enriched.errorChain = prior.errorChain
                return enriched
            }
            let limit = min(errorChain.count, prior.errorChain.count)
            let overlap = (0...limit).reversed().first { count in
                Array(errorChain.suffix(count)) == Array(prior.errorChain.prefix(count))
            } ?? 0
            let combined = Array((errorChain + Array(prior.errorChain.dropFirst(overlap))).prefix(5))
            guard combined != errorChain else { return self }
            var enriched = self
            enriched.errorChain = combined
            return enriched
        }

        private static func safeErrorChain(_ error: Error?) -> [Cause] {
            if let known = error as? CombinedFailure {
                if let context = known.launchContext { return context.errorChain }
                guard known.underlyingDomain != "none" || known.underlyingCode != 0 else { return [] }
                let safe = CombinedFailure.safeDiagnosticUnderlying(domain: known.underlyingDomain, code: known.underlyingCode)
                return [Cause(domain: safe.domain, code: safe.code == "unknown" ? nil : known.underlyingCode)]
            }
            guard var current = error as NSError? else { return [] }
            var result: [Cause] = []
            var seen = Set<ObjectIdentifier>()
            for _ in 0..<5 {
                let identity = ObjectIdentifier(current)
                guard seen.insert(identity).inserted else { break }
                let safe = CombinedFailure.safeDiagnosticUnderlying(domain: current.domain, code: current.code)
                result.append(Cause(domain: safe.domain, code: safe.code == "unknown" ? nil : current.code))
                guard let next = current.userInfo[NSUnderlyingErrorKey] as? NSError else { break }
                current = next
            }
            return result
        }

        private static func classify(sourceStep: Step, errorChain: [Cause]) -> Kind {
            switch sourceStep {
            case .missingPluginDirectory, .liveProcessBundleMissing:
                return .extensionNotFound
            case .executableFileMissing:
                return .executableLoadFailure
            case .listenerCreation, .xpcRemoteObjectError, .xpcInvalidation:
                return .xpcConnectionFailure
            case .requestCancellation:
                if errorChain.contains(where: { $0.domain == NSCocoaErrorDomain && $0.code == NSExecutableLoadError }) {
                    return .executableLoadFailure
                }
            default:
                break
            }
            return .unknown
        }

        public static func launchFailure(_ error: Error? = nil, stage: Stage, code: Code = .failed,
                                         id: String, sourceStep: Step,
                                         requestIdentifierObserved: Bool? = nil, pidObserved: Bool? = nil,
                                         xpcAccepted: Bool? = nil, applicationReadyObserved: Bool? = nil,
                                         peerPIDRejected: Bool? = nil, retryable: Bool? = nil) -> CombinedFailure {
            let context = LaunchContext(error: error, sourceStep: sourceStep,
                requestIdentifierObserved: requestIdentifierObserved, pidObserved: pidObserved,
                xpcAccepted: xpcAccepted, applicationReadyObserved: applicationReadyObserved,
                peerPIDRejected: peerPIDRejected)
            return CombinedFailure(operation: "connect", stage: stage, code: code, id: id,
                underlying: error, retryable: retryable, launchContext: context)
        }
    }

    public enum SafeCause: String, CaseIterable {
        case networkConnectionLost
        case networkTimedOut
        case networkUnavailable
        case anisetteServerUnavailable
        case anisetteServerRejected
        case anisetteRequestTimedOut
        case anisetteRateLimited
        case anisetteInvalidResponse
        case anisetteUnknownFailure
        case signingNetworkConnectionLost
        case signingNetworkTimedOut
        case signingNetworkUnavailable
        case developerPortalRejectedRequest
        case appIDLimitReached
        case developerPortalInvalidResponse
        case provisioningProfileUnavailable
        case certificateUnavailable
        case signingStorageUnverified
        case wifiUnavailable
        case localDevVPNUnavailable
        case unknownSigningCause
        case sourceNetworkFailure
        case sourceInvalidManifest
        case sourcePersistenceUnverified
        case sourceInvalidURL
        case sourceBlocked
        case sourceChangedID
        case sourceDuplicate
        case sourceUnsupported
        case sourceValidationFailed
        case sourceRemoveFailed
        case sourceRemoveBusy
        case sourceAddBusy
        case operationInProgress
        case responseCapacityUnavailable
        // V3_RUNTIME_SHARED_STORE_CAUSE_V1: the host and the embedded service must
        // read and write refresh state through one App Group. When that store
        // cannot be opened the run is refused rather than written somewhere the
        // other process cannot see, and retrying after a reinstall can succeed.
        case sharedStoreUnavailable
        // V3_SECRET_HANDOFF_FAILURE_TYPED_V1: the secure channel between the two
        // signed processes failed. The payload never reached Apple, so this is
        // not an authentication failure and must not be reported as one. It is
        // retryable only when the cause is transient; an unauthorized or missing
        // access group needs a different re-sign, not another attempt.
        case secretHandoffUnavailable
        case staleRefreshAttempt
        case knownSourcePolicyNetworkFailure
        case knownSourcePolicyInvalidResponse
        case catalogUnavailable
        case catalogSourceUnavailable
        // V3_RESPONSE_ENCODING_CLASSIFICATION_V1: the service built a reply it
        // could not serialize. Distinct from an oversized reply.
        case responseEncodingFailed
        // V3_RESPONSE_ENCODING_CLASSIFICATION_V1: the reply serialized cleanly
        // but exceeded the transport limit. This is a third defect, distinct
        // from both an encoding failure and a reply that could not be parsed,
        // and it must not be reported as any of them.
        case responseTooLarge
        case pairingRequired
        case invalidPairingFile
        case pairingFilePreparationFailed
        case authAttemptNotDispatched
        case authProvisioningRetryNotDispatched
        case authSessionUnavailable
        case authResponseCapacityUnavailable
        case credentialCommitFailed, credentialCommitOutcomeUnknown, accountActivationFailed, provisioningStorageFailed
        case keychainSignOutFailed
        case keychainSignOutOutcomeUnknown
        case operationPersistenceFailed
        case recoveryMalformedRecord, recoveryIncompatibleRecord
        case recoveryStorageUnavailable, recoveryLockUnavailable
        case recoveryReadFailure, recoveryDeleteFailure

        fileprivate var inferredRetryable: Bool? {
            switch self {
            case .networkConnectionLost, .networkTimedOut, .networkUnavailable,
                 .signingNetworkConnectionLost, .signingNetworkTimedOut, .signingNetworkUnavailable,
                 .wifiUnavailable, .localDevVPNUnavailable:
                return true
            case .anisetteServerUnavailable:
                return true
            case .anisetteRequestTimedOut, .anisetteRateLimited:
                return true
            case .anisetteServerRejected:
                return false
            case .anisetteInvalidResponse, .anisetteUnknownFailure:
                return nil
            case .appIDLimitReached, .provisioningProfileUnavailable, .certificateUnavailable, .signingStorageUnverified:
                return false
            case .developerPortalRejectedRequest, .developerPortalInvalidResponse:
                return nil
            case .unknownSigningCause:
                return nil
            case .sourceNetworkFailure:
                return true
            case .sourceInvalidManifest, .sourcePersistenceUnverified, .sourceInvalidURL,
                 .sourceBlocked, .sourceChangedID, .sourceDuplicate, .sourceUnsupported,
                 .sourceValidationFailed,
                 .sourceRemoveFailed, .catalogUnavailable:
                return false
            case .sourceRemoveBusy, .sourceAddBusy:
                return true
            case .operationInProgress, .knownSourcePolicyNetworkFailure:
                return true
            case .responseCapacityUnavailable:
                return true
            case .sharedStoreUnavailable:
                return true
            // Retrying the same answer cannot grant an access group or recreate
            // an absent item. Only a transient read or lock failure may repeat.
            case .secretHandoffUnavailable:
                return false
            case .staleRefreshAttempt:
                return false
            case .knownSourcePolicyInvalidResponse:
                return nil
            // The source is gone, so retrying the same request cannot succeed;
            // the recovery is to reload the source list, not to retry.
            case .catalogSourceUnavailable:
                return false
            // A reply that could not be serialized is not fixed by retrying the
            // same request; it needs a code fix or a smaller payload.
            case .responseEncodingFailed:
                return false
            // An oversized reply is not fixed by retrying the same request
            // either: the same data would serialize to the same size again.
            case .responseTooLarge:
                return false
            case .pairingRequired:
                return false
            case .invalidPairingFile:
                return false
            case .pairingFilePreparationFailed:
                return false
            case .authAttemptNotDispatched:
                return true
            case .authProvisioningRetryNotDispatched:
                return true
            case .authSessionUnavailable:
                return false
            case .authResponseCapacityUnavailable:
                return true
            case .credentialCommitFailed, .credentialCommitOutcomeUnknown, .accountActivationFailed, .provisioningStorageFailed:
                return false
            case .keychainSignOutFailed, .keychainSignOutOutcomeUnknown:
                return true
            case .operationPersistenceFailed:
                return false
            case .recoveryMalformedRecord, .recoveryIncompatibleRecord:
                return false
            case .recoveryStorageUnavailable, .recoveryLockUnavailable,
                 .recoveryReadFailure, .recoveryDeleteFailure:
                return true
            }
        }
    }

    public enum SourceStep: String, CaseIterable {
        case authenticate, anisetteFetch, appleAuthentication, accountLookup
        case credentialCommit, fetchTeams, saveAccount, fetchCertificate
        case activateCertificate, registerDevice, activateAccount, provisioningUnknown
        case provisioningProfileFetch, certificateValidation, localCodeSigning
        case appIDLookup, appIDRegistration, appIDCapabilitiesUpdate
        case appGroupLookup, appGroupRegistration, appGroupAssignment
        case provisioningProfileRetrieval, provisioningProfileCreation, provisioningProfileUpdate
        case sourceDownload, manifestParsing, sourceValidation, knownSourcePolicyFetch,
             knownSourcePolicyParsing, catalogRead
        var portalUserLabel: String? {
            switch self {
            case .appIDLookup: return "while looking up app identifiers"
            case .appIDRegistration: return "while registering an app identifier"
            case .appIDCapabilitiesUpdate: return "while updating the app's capabilities"
            case .appGroupLookup: return "while looking up app groups"
            case .appGroupRegistration: return "while registering an app group"
            case .appGroupAssignment: return "while assigning the app's groups"
            case .provisioningProfileRetrieval: return "while retrieving a provisioning profile"
            case .provisioningProfileCreation: return "while creating a provisioning profile"
            case .provisioningProfileUpdate: return "while updating a provisioning profile"
            default: return nil
            }
        }
    }

    public enum Stage: String, CaseIterable {
        case hostContainer, storagePreparation, bookmarkCreation, extensionDiscovery, extensionLaunch
        case xpcConnection, serviceReadiness, command, authentication, provisioning, signing, filePreparation, installation, persistence, refreshVerification
        case replyEncoding
        case endpointSelection, heartbeat, coreDevice, cdTunnel, rsdDiscovery, rsdService, lockdownConnection, uniqueDeviceID, pairing
        case network, source, catalog
    }
    public enum Code: String, CaseIterable {
        case unavailable, invalidConfiguration, permissionDenied, timedOut, cancelled, interrupted
        case notReady, busy, invalidResponse, unsupported, failed, missingResult, staleResult
        case invalidToken, missingFile, emptyFile, invalidPackage, fileAccess, stagingFailed
    }
    public let operation: String
    public let stage: Stage
    public let code: Code
    public let correlationID: String
    public let underlyingDomain: String
    public let underlyingCode: Int
    public let safeCause: SafeCause?
    public let sourceStep: SourceStep?
    public let signingContext: [String: String]
    public let retryable: Bool?
    // V3_CATALOG_OPERATION_CONTEXT_V1: host-only request context. It records
    // which request was waiting when a failure occurred before the service
    // received it, so a catalog read keeps its operation context even when the
    // failure is a connection problem. It is appended to the copied technical
    // line only and is never part of the wire envelope.
    public var requestContext: String?
    // Host-only extension startup details. This is intentionally omitted from
    // the service wire envelope and appended only to copied technical details.
    public let launchContext: LaunchContext?
    public init(operation: String, stage: Stage, code: Code = .failed, id: String,
                underlying: Error? = nil, retryable: Bool? = nil, safeCause: SafeCause? = nil,
                sourceStep: SourceStep? = nil, signingContext: [String: String] = [:],
                launchContext: LaunchContext? = nil) {
        let normalized = ["snapshot": "status", "refreshApp": "refresh", "refreshAdmissionBegin": "refresh", "refreshAdmissionEnd": "refresh", "installURL": "install", "installSharedIPA": "install",
                          "addSource": "source", "removeSource": "source", "refreshSources": "source", "syncAppIDs": "signIn",
                          "authBegin": "signIn", "authPoll": "signIn", "authRespond": "signIn", "authCancel": "signIn",
                          "authRetryProvisioning": "signIn", "authReconcileStorage": "signIn",
                          "opStart": "command", "opPoll": "command", "opAnswer": "command", "opCancel": "command",
                          "recoveryDiscardUnreadable": "recovery",
                          "sourcePreview": "source", "sourceAddConfirmed": "source", "sourceRemoveConfirmed": "source"][operation] ?? operation
        self.operation = Self.operations.contains(normalized) ? normalized : "command"
        self.stage = stage; self.code = code
        correlationID = UUID(uuidString: id) != nil ? id : UUID().uuidString
        let error = underlying as NSError?
        let safeUnderlying = Self.safeWireUnderlying(domain: error?.domain ?? "none", code: error?.code ?? 0)
        underlyingDomain = safeUnderlying.domain
        underlyingCode = safeUnderlying.code
        let inferredSourceAddBusy = operation == "sourceAddConfirmed" && code == .busy
            ? SafeCause.sourceAddBusy : nil
        self.safeCause = safeCause ?? inferredSourceAddBusy
        self.sourceStep = sourceStep
        self.signingContext = Self.validatedSigningContext(signingContext) ?? [:]
        self.retryable = retryable ?? self.safeCause?.inferredRetryable
        let launchStages: Set<Stage> = [.extensionDiscovery, .extensionLaunch, .xpcConnection, .serviceReadiness]
        self.launchContext = normalized == "connect" && launchStages.contains(stage) ? launchContext : nil
    }
    private static let operations: Set<String> = ["connect", "status", "command", "recovery", "refresh", "install", "update", "signIn", "signOut", "catalog", "source", "sign", "activate", "deactivate", "delete", "remove", "backup", "restore", "jit", "pairingImportData", "anisetteList", "anisetteReset", "anisetteSync"]
    private static let domains: Set<String> = ["none", "NSCocoaErrorDomain", "NSPOSIXErrorDomain", "NSURLErrorDomain", "NSOSStatusErrorDomain", "ALTServerErrorDomain", "ALTAppleAPIErrorDomain", "ALTErrorDomain", "MinimuxerError", "DeviceGatewayError", "IdeviceGatewayError", "InstallationProxyErrorDomain", "com.apple.installd", "com.apple.mobile.installation_proxy", "V3IPAFileErrorDomain", "Foundation", "CoreData", "CoreFoundation", "IOKit", "Security", "CFNetwork", "kCFErrorDomainCFNetwork", "HTTPStatus", "io.sidestore.SideStore.DecodingError", "io.sidestore.LiveContainer.ExtensionLaunch", "com.SideStore.Keychain", "LiveContainerRefresh.Configuration", "SideSign.ServerError", "SideSign.DeveloperPortalError"]
    private static let verificationDomains: Set<String> = ["ALTServerErrorDomain", "ALTErrorDomain", "IdeviceGatewayError", "DeviceGatewayError", "InstallationProxyErrorDomain", "com.apple.installd", "com.apple.mobile.installation_proxy"]

    /// Only observations from the request/operation context are allowed here.
    /// Never accept provider messages, tokens, account names or device IDs.
    public static let signingCapabilityNames: Set<String> = [
        "APG3427HIY", "IAD53UNK2F", "gameCenter", "inAppPurchase", "push",
        "associatedDomains", "dataProtection", "siri", "applePay", "vpn", "networkExtensions",
        "multipath", "hotspot", "nfc", "classKit", "autoFillCredentialProvider",
        "accessWiFiInformation", "wirelessAccessoryConfiguration", "increasedMemoryLimit",
        "extendedVirtualAddressing", "increasedDebuggingMemoryLimit"
    ]
    public static func validatedSigningContext(_ value: [String: String]) -> [String: String]? {
        var fields = value
        if !V3TemporaryAnisetteTrace.temporaryAnisetteTraceEnabled {
            fields.removeValue(forKey: V3TemporaryAnisetteTrace.contextKey)
        }
        guard fields.count <= 20 else { return nil }
        for (key, text) in fields {
            if key == V3TemporaryAnisetteTrace.contextKey {
                guard V3TemporaryAnisetteTrace(encoded: text) != nil else { return nil }
                continue
            }
            guard text.utf8.count <= 512 else { return nil }
            switch key {
            case "team_sha256", "requested_bundle_sha256", "requested_app_group_sha256", "capabilities_sha256", "signing_certificate_serial_sha256":
                guard text.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { return nil }
            case "provisioning_bundle_sha256":
                guard text.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { return nil }
            case "session_generation", "capability_count", "app_group_count", "extension_count":
                guard !text.isEmpty, text.utf8.count <= 20,
                      text.utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }), UInt64(text) != nil else { return nil }
            case "capability_names", "enabled_capability_names":
                let names = text.isEmpty ? [] : text.components(separatedBy: ",")
                guard names.count <= 32, Set(names).isSubset(of: signingCapabilityNames),
                      Set(names).count == names.count else { return nil }
            case "server_code":
                guard text == "unknown" || (text.utf8.count <= 20 && Int(text).map({ String($0) }) == text) else { return nil }
            case "native_code", "native_subcode", "probe_native_code", "probe_native_subcode":
                guard text == "unknown" || Int32(text).map({ String($0) }) == text else { return nil }
            case "anisette_blob_state":
                guard V3AnisetteAttemptContext.BlobState(rawValue: text) != nil else { return nil }
            case "anisette_recovery":
                guard V3AnisetteAttemptContext.Recovery(rawValue: text) != nil else { return nil }
            case "native_phase", "probe_native_phase":
                guard V3AnisetteNativeEvidence.Phase(rawValue: text) != nil else { return nil }
            case "http_status":
                guard text == "unavailable" || Int(text).map({ (100...599).contains($0) && String($0) == text }) == true else { return nil }
            case "provider_code":
                guard ["unavailable", "ENTITY_ERROR", "ENTITY_ERROR.INVALID", "ENTITY_ERROR.ATTRIBUTE.INVALID",
                    "ENTITY_ERROR.ATTRIBUTE.REQUIRED", "ENTITY_ERROR.ATTRIBUTE.UNKNOWN",
                    "ENTITY_ERROR.RELATIONSHIP.INVALID", "ENTITY_ERROR.RELATIONSHIP.INVALID_NOT_ALLOWED",
                    "ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE", "FORBIDDEN_ERROR", "NOT_FOUND",
                    "PARAMETER_ERROR.INVALID", "PARAMETER_ERROR.REQUIRED", "RATE_LIMIT_EXCEEDED",
                    "SERVICE_UNAVAILABLE", "UNEXPECTED_ERROR", "UNKNOWN_ERROR"].contains(text) else { return nil }
            case "account_binding", "team_binding":
                guard text == "verified" else { return nil }
            case "signing_certificate_present":
                guard text == "true" || text == "false" else { return nil }
            case "preferred_parent_id_match":
                guard text == "true" || text == "false" else { return nil }
            case "provisioning_bundle_role":
                guard text == "main" || text == "extension" else { return nil }
            case "profile_mode":
                guard text == "team" || text == "manual" else { return nil }
            case "device_registration":
                guard text == "unobserved" else { return nil }
            case "typed_error":
                guard ["sideSignServerReportedError", "sideSignBadResponse", "sideSignInvalidResponse", "sideSignMissingKey", "sideSignDeveloperPortalError", "keychainWrite", "keychainValidationFailed", "keychainOutcomeUnknown", "legacyMigrationConflict", "persistenceFailure", "persistenceOutcomeUnknown", "transportFailure", "anisetteFailure", "anisetteKitInvalidArgument", "anisetteKitLoaderFailed", "anisetteKitSymbolMissing", "anisetteKitReadFailure", "anisetteKitInvalidResponse", "anisetteKitADIError", "anisetteKitLibrariesNotFound", "anisetteKitHTTPError", "decodingTypeMismatch", "decodingValueNotFound", "decodingKeyNotFound", "decodingDataCorrupted", "archiveFileNotFound", "archiveCorrupt", "archiveReadFailed", "archiveWriteFailed", "archiveMissingApp", "unknownAccountFailure", "anisetteIdentityStateInvalid"].contains(text) else { return nil }
            default: return nil
            }
        }
        return fields
    }

    /// Copyable diagnostics may include a native code only when its domain is
    /// in the same fixed allowlist used by structured failures. Arbitrary NSError
    /// domains can contain endpoint or user supplied text, so both fields are
    /// suppressed together when provenance is not recognized.
    public static func safeDiagnosticUnderlying(domain: String, code: Int) -> (domain: String, code: String) {
        guard (Self.domains.contains(domain) && domain != "none") || (domain == "none" && code == 0) else {
            return ("redacted", "unknown")
        }
        return (domain, String(code))
    }

    /// Safe technical fields for the provisioning retry prompt. Preserve the
    /// area/correlation context, but never interpolate an untrusted NSError.
    public static func provisioningRetryTechnicalDetails(for error: Error,
                                                         correlationID: String) -> String {
        if let accountError = error as? V3AccountOperationError {
            let failure = accountError.failure(operation: "signIn", id: correlationID)
            return "area=provisioning " + failure.technicalDetails
        }
        let native = error as NSError
        let underlying = safeDiagnosticUnderlying(domain: native.domain, code: native.code)
        let safeID = UUID(uuidString: correlationID)?.uuidString ?? UUID().uuidString
        return "diagnostic_code=SS-PROV-C11 builder_commit=\(V3DiagnosticBuild.commit) domain=\(underlying.domain) code=\(underlying.code) area=provisioning correlation=\(safeID)"
    }

    /// The serialized wire keeps an integer field for compatibility. `none/0`
    /// means no underlying error; `redacted/0` means native details were hidden.
    /// The reserved `none` domain never authorizes a nonzero native code.
    fileprivate static func safeWireUnderlying(domain: String, code: Int) -> (domain: String, code: Int) {
        // `none` is reserved for absence of an underlying error; it does not
        // establish provenance for a caller-supplied numeric code.
        guard (Self.domains.contains(domain) && domain != "none") || (domain == "none" && code == 0) else {
            return ("redacted", 0)
        }
        return (domain, code)
    }

    private var timeoutAction: String {
        switch operation {
        case "connect": return "connect to SideStore"
        case "status": return "load status"
        case "refresh": return "refresh apps"
        case "install": return "install the app"
        case "update": return "update the app"
        case "delete": return "delete the app"
        case "signIn": return "sign in"
        case "signOut": return "sign out"
        case "source": return "load the source"
        case "catalog": return "load the catalog"
        default: return "complete the request"
        }
    }
    private var timeoutContext: String {
        switch stage {
        case .hostContainer, .storagePreparation, .bookmarkCreation:
            return "using the shared app container"
        case .extensionDiscovery, .extensionLaunch:
            return "starting the LiveProcess extension"
        case .xpcConnection:
            return "connecting to the SideStore service"
        case .serviceReadiness:
            return "waiting for SideStore to finish starting"
        case .authentication:
            return "checking the Apple account"
        case .provisioning:
            return "preparing provisioning data"
        case .signing:
            return "signing the app"
        case .filePreparation:
            return "preparing the selected IPA"
        case .installation:
            return "installing the app"
        case .persistence:
            return "saving the operation result"
        case .refreshVerification:
            return "verifying the refresh result"
        case .replyEncoding:
            return "preparing the service response"
        case .endpointSelection, .heartbeat, .coreDevice, .cdTunnel, .rsdDiscovery,
             .rsdService, .lockdownConnection, .uniqueDeviceID:
            return "connecting to the device"
        case .pairing:
            return "checking the pairing data"
        case .network:
            return "checking the network connection"
        case .source:
            return "loading the source"
        case .catalog:
            return "loading the source catalog"
        case .command:
            return "waiting for SideStore to finish the request"
        }
    }
    public var message: String {
        if signingContext["typed_error"] == "anisetteIdentityStateInvalid" { return LCAnisettePairError.safeMessage }
        if operation == "delete", code == .timedOut {
            return "SideStore could not confirm that the deleted app disappeared from its installed library."
        }
        // V3_CATALOG_FAILURE_VOCABULARY_V1: a catalog read must never surface as
        // the generic command-stage message. It can fail at a stage that is not
        // the catalog stage, so the operation selects this wording first.
        //
        // V3_RESPONSE_CLASSIFICATION_CARRIER_V1: the two reply-level causes are
        // the exception. They describe the reply itself rather than the catalog,
        // and the catalog vocabulary covered both of them with one sentence, so
        // a reply that could not be encoded and a reply that was too large read
        // identically to a user. The specific cause outranks it.
        if operation == "catalog", safeCause != .responseEncodingFailed,
           safeCause != .responseTooLarge, let catalog = catalogFailureMessage { return catalog }
        if code == .cancelled { return "The \(operation) request was cancelled. Its result may need reconciliation." }
        if code == .timedOut { return "SideStore could not \(timeoutAction) in time while \(timeoutContext)." }
        if let safeCause {            switch safeCause {
            case .networkConnectionLost: return "The network connection was lost during \(operation)."
            case .networkTimedOut: return "The network request timed out during \(operation)."
            case .networkUnavailable: return "A network connection was unavailable during \(operation)."
            case .anisetteServerUnavailable: return "The configured Anisette server is temporarily unavailable."
            case .anisetteServerRejected: return "The configured Anisette server returned an unsuccessful response."
            case .anisetteRequestTimedOut: return "The configured Anisette server timed out while synchronizing."
            case .anisetteRateLimited: return "The configured Anisette server is temporarily rate-limiting synchronization requests."
            case .anisetteInvalidResponse: return "The configured Anisette server returned data SideStore could not read."
            case .anisetteUnknownFailure: return "Anisette server synchronization failed for an unknown reason."
            case .signingNetworkConnectionLost: return "The connection to the provisioning service was interrupted during signing."
            case .signingNetworkTimedOut: return "The provisioning service did not respond during signing."
            case .signingNetworkUnavailable: return "The signing flow could not reach the provisioning service."
            case .developerPortalRejectedRequest:
                return "Apple's developer service reported an error \(sourceStep?.portalUserLabel ?? "while preparing the app's provisioning data")."
            case .appIDLimitReached: return "App ID limit reached. Apple could not register another App ID for the selected team."
            case .developerPortalInvalidResponse: return "The provisioning service returned an invalid response during signing."
            case .provisioningProfileUnavailable: return "A required provisioning profile is not available for this app."
            case .certificateUnavailable: return "The selected signing certificate is not available."
            case .signingStorageUnverified: return "SideStore cannot change Apple certificates while saved signing state is unverified."
            case .wifiUnavailable: return "Wi-Fi was unavailable before refresh started."
            case .localDevVPNUnavailable: return "LocalDevVPN was unavailable before refresh started."
            case .unknownSigningCause: return "SideStore could not sign the selected app. The exact underlying cause could not be safely identified."
            case .sourceNetworkFailure: return "The source could not be downloaded because its network request failed."
            case .sourceInvalidManifest: return "The source returned data SideStore could not read as a valid source."
            case .sourcePersistenceUnverified: return "SideStore could not confirm that the source was saved."
            case .sourceInvalidURL: return "The source URL is invalid."
            case .sourceBlocked: return "SideStore blocked this source for security reasons."
            case .sourceChangedID: return "SideStore stopped updating this source because its identifier changed."
            case .sourceDuplicate: return "A source with the same identifier is already saved."
            case .sourceUnsupported: return "This source format is not supported by this version of SideStore."
            case .sourceValidationFailed: return "SideStore rejected metadata in this source."
            case .sourceRemoveFailed: return "SideStore could not confirm that the source was removed from its saved list."
            case .sourceRemoveBusy: return "SideStore was busy with another request, so it did not start removing this source."
            case .sourceAddBusy: return "SideStore was busy with another request, so it did not confirm adding this source."
            case .operationInProgress: return "Another SideStore operation is still active."
            case .responseCapacityUnavailable: return "SideStore cannot safely accept another state-changing request yet."
            case .sharedStoreUnavailable: return "LiveContainer could not open the shared store that its refresh state and the embedded SideStore service both use."
    // V3_SECRET_HANDOFF_FAILURE_TYPED_V1: say plainly that the response never
    // left the device, so an Apple password is never implicated.
    case .secretHandoffUnavailable: return "Your response could not be delivered to the embedded service through the secure channel, so it was never sent to Apple. This is not an authentication failure."
            case .staleRefreshAttempt: return "This refresh request belonged to an expired scheduler run and was not started."
            case .knownSourcePolicyNetworkFailure: return "SideStore could not update its own known-source safety list."
            case .knownSourcePolicyInvalidResponse: return "SideStore could not read its own known-source safety list."
            case .catalogUnavailable: return "SideStore could not read this source's saved catalog data."
            case .catalogSourceUnavailable: return "This source is no longer in the SideStore source list."
            case .responseEncodingFailed: return "SideStore could not encode the response for this request."
            case .responseTooLarge: return "SideStore produced a response that is too large to transfer."
            case .pairingRequired: return "A pairing file is required before this device can be refreshed."
            case .invalidPairingFile: return "SideStore could not read or validate the pairing file."
            case .pairingFilePreparationFailed: return "LiveContainer could not read or prepare the selected pairing file."
            case .authAttemptNotDispatched: return "SideStore did not start this sign-in attempt, so Apple authentication was not submitted."
            case .authProvisioningRetryNotDispatched: return "SideStore did not start the provisioning retry; the saved authentication session was not changed by this request."
            case .authSessionUnavailable: return "SideStore no longer has the active sign-in session."
            case .authResponseCapacityUnavailable: return "SideStore could not start sign-in because it cannot safely reserve a response slot yet."
            case .credentialCommitFailed:
                return "Apple authentication succeeded, but the sign-in credentials could not be saved on this device."
            case .credentialCommitOutcomeUnknown:
                return "Apple authentication succeeded, but the local credential save could not be confirmed."
            case .accountActivationFailed:
                return "Authentication succeeded, but SideStore could not confirm that account and team activation was saved."
            case .provisioningStorageFailed:
                return "Authentication succeeded, but provisioning could not save the account or certificate on this device."
            case .keychainSignOutFailed: return "SideStore could not confirm removal of the saved Apple sign-in data. Sign Out stopped, and any partial changes were rolled back."
            case .keychainSignOutOutcomeUnknown: return "SideStore could not confirm the Sign Out outcome. Reload Account & Signing to reconcile which Apple account is active before continuing."
            case .operationPersistenceFailed: return "The device operation may have completed, but SideStore could not confirm that its updated app state was saved."
            case .recoveryMalformedRecord: return "SideStore found a malformed recovery record. Changes remain paused."
            case .recoveryIncompatibleRecord: return "SideStore found a recovery record from an incompatible schema. Changes remain paused."
            case .recoveryStorageUnavailable: return "SideStore cannot access its shared recovery storage. It has not identified a corrupt record."
            case .recoveryLockUnavailable: return "SideStore could not acquire its recovery storage lock."
            case .recoveryReadFailure: return "SideStore could not read the recovery file. Its contents have not been classified."
            case .recoveryDeleteFailure: return "SideStore could not delete and confirm removal of the recovery record."
            }
        }
        switch stage {
        case .hostContainer: return "SideStore could not start because the authoritative host container is unavailable."
        case .storagePreparation: return "SideStore could not start because its existing data storage could not be prepared."
        case .bookmarkCreation: return "SideStore could not start because its data bookmark could not be created."
        case .extensionDiscovery: return "The embedded LiveProcess extension is missing or unavailable."
        case .extensionLaunch: return "The embedded SideStore process could not be launched."
        case .xpcConnection: return "The connection to the embedded SideStore service was interrupted or unavailable."
        case .serviceReadiness: return "The SideStore process has not finished preparing its service."
        case .endpointSelection: return "No usable device transport endpoint was selected."
        case .heartbeat: return "The device transport heartbeat is inactive."
        case .coreDevice: return "Could not connect to the device through CoreDevice."
        case .cdTunnel: return "The CoreDevice tunnel could not be established."
        case .rsdDiscovery: return "Device service discovery through RSD failed."
        case .rsdService: return "The requested RSD device service could not be connected."
        case .lockdownConnection: return "The device transport opened, but the lockdownd connection failed."
        case .uniqueDeviceID: return "The device connection opened, but the UniqueDeviceID request failed."
        case .pairing: return "Pairing parsing, validation, or a concrete device trust check failed."
        case .source:
            switch sourceStep {
            case .sourceDownload: return "The source could not be downloaded."
            case .manifestParsing: return "The source returned data SideStore could not read as a valid source."
            case .sourceValidation: return "SideStore rejected the source during validation."
            case .catalogRead: return "SideStore could not confirm that the source was saved or read from its catalog."
            default: return "SideStore could not complete the source request."
            }
        case .catalog:
            // The wording is supplied by catalogFailureMessage, which keys on the
            // operation rather than on this stage.
            return "SideStore could not load this source's catalog."
        case .authentication: return "SideStore could not complete account authentication."
        case .provisioning: return "Apple sign-in succeeded, but device provisioning did not complete."
        case .signing:
            switch sourceStep {
            case .provisioningProfileFetch:
                return "SideStore could not prepare provisioning data for signing. The exact failed request could not be safely identified."
            case .certificateValidation:
                return "SideStore could not validate the signing certificate. The exact underlying cause could not be safely identified."
            case .localCodeSigning:
                return "SideStore could not sign the app locally. The exact underlying cause could not be safely identified."
            default: break
            }
            return underlyingDomain == "redacted" && underlyingCode != 0
                ? "SideStore could not sign the application. The exact underlying cause could not be safely identified."
                : "SideStore could not sign the application."
        case .filePreparation:
            switch code {
            case .invalidToken: return "The staged IPA reference is invalid. Select the file again."
            case .missingFile: return "The staged IPA is no longer available. Select it again."
            case .emptyFile: return "The selected IPA is empty and could not be installed."
            case .invalidPackage: return "The selected file is not a valid IPA app package."
            case .fileAccess: return "The selected IPA could not be read. Check file access and select it again."
            default: return "The selected IPA could not be prepared for installation. Select it again."
            }
        case .installation:
            // Apple-side application verification rejections carry fixed installd
            // codes. These describe profile/identity rejection, never an account
            // ban, and they do not imply a pairing or LocalDevVPN problem.
            if hasApplicationVerificationEvidence && underlyingCode == 0xE8008024 {
                return "iOS reports that the provisioning profile is banned during application verification. Recreating pairing or changing LocalDevVPN settings is unlikely to address this specific error."
            }
            if hasApplicationVerificationEvidence && underlyingCode == 0xE8008018 {
                return "iOS reports that the identity used to sign the executable is no longer valid. The app must be re-signed with a current signing identity."
            }
            return "SideStore could not complete the application installation."
        case .persistence:
            return "SideStore could not confirm that the operation result was saved. The device may already have changed."
        case .refreshVerification: return "Refresh completion could not be verified from the installation results."
        case .network: return "Network error during the \(operation) operation."
        case .replyEncoding: return "SideStore could not encode its service response."
        case .command:
            if underlyingDomain == "redacted" && underlyingCode != 0 {
                return "SideStore could not start or complete the requested \(operation) action. The exact underlying cause could not be safely identified."
            }
            return "SideStore could not start or complete the requested \(operation) action."
        }
    }
    // V3_CATALOG_FAILURE_VOCABULARY_V1: the exact sentence for each boundary a
    // catalog read can fail at, selected by the real stage and code rather than
    // by a generic fallback. The source manifest is never blamed here, because
    // nothing on this path proves the manifest failed to parse.
    private var catalogFailureMessage: String? {
        if safeCause == .catalogSourceUnavailable {
            return "This source is no longer in the SideStore source list."
        }
        if code == .unavailable || code == .notReady || stage == .serviceReadiness {
            return "The SideStore service is not ready to load this source yet."
        }
        if stage == .xpcConnection && code == .interrupted {
            return "The connection to the SideStore service was interrupted while loading the source."
        }
        if code == .busy {
            return "SideStore is still finishing another operation. Wait a moment, then reload the source."
        }
        if code == .invalidResponse {
            return "SideStore returned an unreadable response while loading the source catalog."
        }
        if code == .timedOut {
            return "The SideStore service did not answer while loading this source catalog."
        }
        if stage == .catalog { return "SideStore could not read this source's saved catalog." }
        return nil
    }

    private var catalogFailureRecovery: String? {
        if safeCause == .catalogSourceUnavailable {
            return "Return to Sources and reload the source list, then open the source again if it is still present."
        }
        if code == .unavailable || code == .notReady || stage == .serviceReadiness {
            return "Wait for SideStore to finish starting, then reload the source."
        }
        if code == .busy {
            return "Wait for the current SideStore operation to finish, then reload the source."
        }
        if stage == .xpcConnection || code == .timedOut {
            return "Wait for the SideStore service to become available, then reload the source."
        }
        return "Reload the source catalog. If it continues, copy the safe diagnostics."
    }

    public var recovery: String {
        if signingContext["typed_error"] == "anisetteIdentityStateInvalid" { return LCAnisettePairError.recovery }
        // V3_RESPONSE_CLASSIFICATION_CARRIER_V1: the two reply-level causes
        // outrank the catalog recovery for the same reason they outrank its
        // message. Repeating an unencodable request, or the same oversized one,
        // fails identically, so the catalog advice to reload would send the user
        // in a circle.
        if operation == "catalog", safeCause != .responseEncodingFailed,
           safeCause != .responseTooLarge, let catalog = catalogFailureRecovery { return catalog }
        if let safeCause {
            switch safeCause {
            case .networkConnectionLost, .networkTimedOut, .networkUnavailable:
                return "Check the network used by this request, then retry when the connection is stable. If a device operation still fails, run Connection Check."
            case .anisetteServerUnavailable:
                return "Try syncing again later or choose another configured Anisette server."
            case .anisetteServerRejected:
                return "Check the configured Anisette server address, then sync again after correcting it."
            case .anisetteRequestTimedOut:
                return "Retry once. If the configured Anisette server times out again, choose another server."
            case .anisetteRateLimited:
                return "Wait before retrying once. If the server is still rate-limiting requests, choose another configured Anisette server."
            case .anisetteInvalidResponse:
                return "Choose another configured Anisette server or report that its response could not be read."
            case .anisetteUnknownFailure:
                return "The exact Anisette synchronization cause could not be safely identified. Check the configured server and copy Diagnostics."
            case .signingNetworkConnectionLost, .signingNetworkTimedOut, .signingNetworkUnavailable:
                return "Your current connection may still be healthy. Retry once. If this happens again, open Connection Settings."
            case .developerPortalRejectedRequest, .developerPortalInvalidResponse:
                return "Copy Diagnostics, including the failed request step and server code. The correct recovery action is not yet known."
            case .appIDLimitReached:
                return "Apps with extensions may need multiple App IDs. Check App IDs for the selected team and retry when capacity is available. Repeating the install immediately or changing certificates will not free an App ID slot."
            case .provisioningProfileUnavailable:
                return "The requested provisioning profile was unavailable. Keep the diagnostics before trying the install again."
            case .certificateUnavailable:
                return "Open Certificates and inspect the selected signing certificate before retrying."
            case .wifiUnavailable:
                return "Restore Wi-Fi, then start a new refresh."
            case .localDevVPNUnavailable:
                return "Restore LocalDevVPN, then start a new refresh."
            case .unknownSigningCause:
                return "The exact underlying cause was not safely identified. Copy Diagnostics before trying this action again."
            case .sourceNetworkFailure:
                return "Check the network connection and retry the source request."
            case .sourceInvalidManifest:
                return "Check the source provider's manifest format, then preview it again."
            case .sourceBlocked:
                return "Do not add this source. Verify with the provider that it is safe before trying again."
            case .sourceChangedID:
                return "Contact the source provider before removing the saved source or adding it again."
            case .sourceDuplicate:
                return "Return to Sources and use the existing source. Remove it only after confirming which entry is correct."
            case .sourceUnsupported:
                return "Update SideStore or use a source format supported by this version."
            case .sourceValidationFailed:
                return "Ask the source provider to correct its metadata, then preview it again."
            case .sourcePersistenceUnverified:
                return "Return to Sources and reload the list. Confirm whether the source is present before submitting another add; copy Diagnostics if its status remains unclear."
            case .sourceInvalidURL:
                return "Enter a valid HTTP or HTTPS source URL, then preview it again."
            case .sourceRemoveFailed:
                return "Reload Sources and confirm whether the source is gone. If it remains, remove it again."
            case .sourceRemoveBusy:
                return "Wait for the current SideStore request to finish, reload Sources, then confirm removal again."
            case .sourceAddBusy:
                return "Wait for the current SideStore request to finish, reload Sources, then preview and confirm the add again."
            case .operationInProgress:
                return "Wait for the active SideStore request to finish, check the action's current state, then retry that action if needed."
            case .responseCapacityUnavailable:
                return "Wait for SideStore to release earlier request results, check the current state, then retry this action."
            case .sharedStoreUnavailable:
                return "Relaunch LiveContainer after reinstalling or re-signing it. Nothing was written to a private store, and the next launch can retry this run."
            // V3_SECRET_HANDOFF_FAILURE_TYPED_V1: the recovery is a re-sign that
            // grants every part of the app the same secure group, not another
            // attempt with the same password.
            case .secretHandoffUnavailable:
                return "Your response never left this device, so no Apple password was sent. Re-sign or reinstall LiveContainer so its embedded service shares the app's secure storage group, then submit the response again."
            case .staleRefreshAttempt:
                return "Return to Refresh and start a new refresh. This stale request did not reach SideStore or the device."
            case .knownSourcePolicyNetworkFailure:
                return "Check the network, then retry from Sources. This error came from SideStore's known-source safety list, not the URL you entered."
            case .knownSourcePolicyInvalidResponse:
                return "Try again later. If SideStore keeps receiving unreadable safety-list data, copy Diagnostics and report it."
            case .catalogUnavailable:
                return "Reload the catalog. If it continues, copy the safe diagnostics."
            case .catalogSourceUnavailable:
                return "Return to Sources and reload the source list, then open the source again."
            case .responseEncodingFailed:
                return "The same request cannot fix this reply-encoding failure. Copy Diagnostics and report that the service could not encode its response."
            case .responseTooLarge:
                return "The service reply exceeded the transfer limit. Copy Diagnostics and report this response-size issue; repeating the same request will fail again."
            case .pairingRequired:
                return "Add the pairing file, then retry the refresh."
            case .invalidPairingFile:
                return "Open Pairing File and replace the saved pairing file with a valid one, then retry."
            case .pairingFilePreparationFailed:
                return "Choose the pairing file again and make sure it is accessible to LiveContainer."
            case .authAttemptNotDispatched:
                if code == .busy {
                    return "Wait for the active SideStore operation to finish, then start sign-in again."
                }
                if stage == .serviceReadiness {
                    return "Wait for SideStore to finish starting, then start sign-in again."
                }
                return "Resolve the displayed prerequisite, then start sign-in again."
            case .authProvisioningRetryNotDispatched:
                if code == .busy {
                    return "Wait for the active SideStore operation to finish, then retry provisioning."
                }
                if stage == .serviceReadiness {
                    return "Wait for SideStore to finish starting, then retry provisioning."
                }
                return "Retry provisioning when the displayed prerequisite is ready."
            case .authSessionUnavailable:
                return "Open Account & Signing and start a new sign-in. SideStore will reconcile the current account before proceeding."
            case .authResponseCapacityUnavailable:
                return "Wait for SideStore to release earlier request results, reload account status, then try again. No Apple credentials were submitted."
            case .signingStorageUnverified:
                return "Open Account & Signing and choose Check Saved Signing State. Creating or revoking Apple certificates stays blocked until local storage is verified."
            case .credentialCommitFailed, .credentialCommitOutcomeUnknown:
                return "Reload Account & Signing to reconcile local storage before starting another sign-in. Keep existing account data and copy Diagnostics if this continues."
            case .accountActivationFailed, .provisioningStorageFailed:
                return "Reload Account & Signing before continuing. Keep the authenticated account and certificate; do not repeat Apple resource creation to repair local storage."
            case .keychainSignOutFailed:
                return "Unlock the iPhone and try Sign Out again. If it still fails, copy Diagnostics."
            case .keychainSignOutOutcomeUnknown:
                return "Reload Account & Signing to reconcile which Apple account is active before continuing. Do not assume Sign Out completed."
            case .operationPersistenceFailed:
                return "Reload installed app status and verify the device before starting another mutation. Do not repeat this operation until its state is known."
            case .recoveryMalformedRecord, .recoveryIncompatibleRecord:
                return "Confirm no SideStore operation remains active on the device before clearing this saved record."
            case .recoveryStorageUnavailable:
                return "Keep changes paused. Check that the combined app can access its shared App Group; copy Diagnostics for support. Clearing a record cannot repair unavailable storage."
            case .recoveryLockUnavailable:
                return "Keep changes paused and allow the active SideStore process to finish. Copy Diagnostics if the lock stays unavailable."
            case .recoveryReadFailure:
                return "Keep changes paused. Check device storage access and copy Diagnostics; do not clear an unclassified record."
            case .recoveryDeleteFailure:
                return "Keep changes paused and copy Diagnostics. The record was not confirmed removed."
            }
        }
        switch stage {
        case .command where operation == "delete" && code == .timedOut:
            return "Reload the installed app list and verify the deletion before trying another delete."
        case .hostContainer:
            return "Reopen LiveContainer and check that it can access its shared App Group container. Keep existing data intact and copy diagnostics if the host container is still unavailable."
        case .storagePreparation:
            return "Check available storage and access to LiveContainer's shared App Group container. Keep existing data intact and copy diagnostics if preparation still fails."
        case .bookmarkCreation:
            return "LiveContainer could not create access to its internal shared SideStore folder. Check that the App Group container is available; copy diagnostics if the folder still cannot be accessed."
        case .extensionDiscovery:
            return "The combined app could not find its embedded LiveProcess extension. Confirm that the installed app is the combined LiveContainer + SideStore package; do not reset SideStore or guest data. Copy diagnostics if it continues."
        case .serviceReadiness:
            return "Wait for SideStore to finish starting, then retry the request."
        case .authentication, .provisioning, .signing: return "Review Account and Signing, then explicitly retry. Never share credentials or private keys."
        case .source:
            if operation == "sourceAddConfirmed" {
                return "Return to Sources and reload the source list. Check whether it was added before retrying; copy Diagnostics if its status is still unclear."
            }
            return "Return to Sources and review the source result. Copy Diagnostics before retrying if its status is unclear."
        case .catalog:
            // The wording is supplied by catalogFailureRecovery, which keys on
            // the operation rather than on this stage.
            return "Reload the source catalog. If it continues, copy the safe diagnostics."
        case .replyEncoding:
            return "Copy Diagnostics and report the service reply-encoding failure. Repeating the same request will not fix it."
        case .filePreparation: return "Choose the IPA again. SideStore will copy it into private shared staging before starting installation."
        case .installation, .persistence, .refreshVerification: return "Reload authoritative app status and expiration before taking another action. Completion may be uncertain."
        case .endpointSelection, .heartbeat, .coreDevice, .cdTunnel, .rsdDiscovery, .rsdService, .lockdownConnection, .uniqueDeviceID, .network:
            return "Check LocalDevVPN and the device connection, then retry explicitly. This failure alone does not prove invalid pairing."
        default: return "Reload the current status to check the result. If the cause remains unclear, copy Diagnostics before deciding whether to try again."
        }
    }
    public var safeMessage: String { messageWithoutDiagnosticCode + "\n" + diagnosticLabel }
    private var messageWithoutDiagnosticCode: String {
        if operation == "refresh", safeCause == nil {
            return "Refresh failed during \(stage.rawValue), but no safe underlying cause was available."
        }
        if underlyingDomain == "redacted", underlyingCode != 0, safeCause == nil {
            return message + " The exact underlying cause could not be safely identified."
        }
        return message
    }
    public var technicalDetails: String {
        let displayedUnderlyingCode = underlyingDomain == "redacted" ? "unknown" : String(underlyingCode)
        let signingDetails = signingContext.filter { $0.key != V3TemporaryAnisetteTrace.contextKey }.sorted(by: { $0.key < $1.key }).map { " \($0.key)=\($0.value)" }.joined()
        return "schema=1 diagnostic_code=\(diagnosticCode) builder_commit=\(V3DiagnosticBuild.commit) operation=\(operation) stage=\(stage.rawValue) code=\(code.rawValue) correlation=\(correlationID) underlying_domain=\(underlyingDomain) underlying_code=\(displayedUnderlyingCode) retryable=\(retryable.map(String.init) ?? "unknown") source_step=\(sourceStep?.rawValue ?? "unknown") safe_cause=\(safeCause?.rawValue ?? "unknown")" + signingDetails + installVerdict + requestContextSuffix + (launchContext?.technicalDetails ?? "") + (temporaryAnisetteTrace?.technicalDetails ?? "")
    }
    public var temporaryAnisetteTrace: V3TemporaryAnisetteTrace? {
        signingContext[V3TemporaryAnisetteTrace.contextKey].flatMap(V3TemporaryAnisetteTrace.init(encoded:))
    }
    // Request context is appended only when it was observed.
    private var requestContextSuffix: String {
        guard let requestContext, !requestContext.isEmpty else { return "" }
        return " " + requestContext
    }
    public mutating func annotatingRequest(requestedOperation: String, requestID: String) {
        requestContext = "request_operation=\(requestedOperation) request_correlation=\(requestID)"
    }
    // V3_CATALOG_DIAGNOSTICS_V1: host-only catalog page context. Only the page
    // offset and the returned row count are recorded; never the source
    // identifier, app names, bundle identifiers, or response content.
    public mutating func annotatingCatalogPage(cursor: Int) {
        requestContext = "source_step=catalogRead page_cursor=\(cursor)"
    }
    // Bounded machine classification for Apple-side application verification
    // rejections (InstallationProxy/installd). Only the two fixed installd
    // codes produce a token; every other failure keeps the existing
    // diagnostics byte-identical. Never an account-ban claim.
    private var installVerdict: String {
        guard hasApplicationVerificationEvidence else { return "" }
        if underlyingCode == 0xE8008024 { return " installVerdict=profileBanned" }
        if underlyingCode == 0xE8008018 { return " installVerdict=signingIdentityRejected" }
        return ""
    }
    private var hasApplicationVerificationEvidence: Bool {
        ["install", "update"].contains(operation) && stage == .installation && Self.verificationDomains.contains(underlyingDomain)
    }
    public var errorDescription: String? { safeMessage + "\n" + recovery + "\n" + technicalDetails }
    /// Bind this semantic failure to the request/reply transaction carrying it.
    /// Session IDs and request IDs are distinct: an auth poll can discover a
    /// missing session while answering a different, current XPC request.
    public func correlating(to id: String) -> CombinedFailure {
        CombinedFailure(operation: operation, stage: stage, code: code, id: id,
            underlying: NSError(domain: underlyingDomain, code: underlyingCode),
            retryable: retryable, safeCause: safeCause, sourceStep: sourceStep, signingContext: signingContext,
            launchContext: launchContext)
    }
    public var wire: [String: Any] {
        let safeUnderlying = Self.safeWireUnderlying(domain: underlyingDomain, code: underlyingCode)
        var result: [String: Any] = ["version": 1, "operation": operation, "stage": stage.rawValue, "code": code.rawValue,
            "correlationID": correlationID, "underlyingDomain": safeUnderlying.domain,
            "underlyingCode": safeUnderlying.code]
        if let safeCause { result["safeCause"] = safeCause.rawValue }
        if let sourceStep { result["sourceStep"] = sourceStep.rawValue }
        if !signingContext.isEmpty { result["signingContext"] = signingContext }
        if let retryable { result["retryable"] = retryable }
        return result
    }
    public var encodedString: String {
        guard let data = try? PropertyListSerialization.data(fromPropertyList: wire, format: .binary, options: 0), data.count <= 4096 else { return "LCFAILURE1:invalid" }
        return "LCFAILURE1:" + data.base64EncodedString()
    }
    public static func fromEncodedString(_ text: String, expectedID: String) -> CombinedFailure? {
        guard text.hasPrefix("LCFAILURE1:"), text.utf8.count <= 6000,
              let data = Data(base64Encoded: String(text.dropFirst(11))), data.count <= 4096,
              let value = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else { return nil }
        return decode(value, expectedID: expectedID)
    }
    public static func decode(_ value: [String: Any], expectedID: String) -> CombinedFailure? {
        guard Set(value.keys).isSubset(of: ["version", "operation", "stage", "code", "correlationID", "underlyingDomain", "underlyingCode", "retryable", "safeCause", "sourceStep", "signingContext"]),
              Self.strictInteger(value["version"]) == 1,
              Self.uuidCorrelationMatches(value["correlationID"] as? String, expectedID: expectedID),
              let operation = value["operation"] as? String, operations.contains(operation),
              let stageName = value["stage"] as? String, let stage = Stage(rawValue: stageName),
              let codeName = value["code"] as? String, let code = Code(rawValue: codeName),
              let domain = value["underlyingDomain"] as? String, domains.contains(domain) || domain == "redacted",
              let number = Self.strictInteger(value["underlyingCode"]) else { return nil }
        let safeCause: SafeCause?
        if let rawCause = value["safeCause"] {
            guard let causeName = rawCause as? String, let cause = SafeCause(rawValue: causeName) else { return nil }
            safeCause = cause
        } else { safeCause = nil }
        let sourceStep: SourceStep?
        if let rawStep = value["sourceStep"] {
            guard let stepName = rawStep as? String, let step = SourceStep(rawValue: stepName) else { return nil }
            sourceStep = step
        } else { sourceStep = nil }
        let signingContext: [String: String]
        if let raw = value["signingContext"] {
            guard let fields = raw as? [String: String],
                  let validated = Self.validatedSigningContext(fields) else { return nil }
            signingContext = validated
        } else { signingContext = [:] }
        if let retry = value["retryable"] {
            guard let bool = retry as? NSNumber, CFGetTypeID(bool) == CFBooleanGetTypeID() else { return nil }
        }
        return CombinedFailure(operation: operation, stage: stage, code: code, id: expectedID,
            underlying: NSError(domain: domain, code: number), retryable: value["retryable"] as? Bool,
            safeCause: safeCause, sourceStep: sourceStep, signingContext: signingContext)
    }

    private static func strictInteger(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else {
            return nil
        }
        let type = String(cString: number.objCType)
        guard ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(type) else {
            return nil
        }
        if ["C", "S", "I", "L", "Q"].contains(type) {
            return Int(exactly: number.uint64Value)
        }
        return Int(exactly: number.int64Value)
    }

    public static func preserving(_ error: Error?, operation: String, stage: Stage, code: Code = .failed, id: String, retryable: Bool? = nil,
                                  launchContext: LaunchContext? = nil) -> CombinedFailure {
        if let known = error as? CombinedFailure {
            guard let launchContext else { return known }
            let retainedContext = launchContext.retainingErrorChain(from: known.launchContext)
            return CombinedFailure(operation: known.operation, stage: known.stage, code: known.code,
                id: known.correlationID, underlying: NSError(domain: known.underlyingDomain, code: known.underlyingCode),
                retryable: known.retryable, safeCause: known.safeCause, sourceStep: known.sourceStep,
                signingContext: known.signingContext, launchContext: retainedContext)
        }
        if let launchContext {
            return CombinedFailure(operation: operation, stage: stage, code: code, id: id,
                underlying: error, retryable: retryable, launchContext: launchContext)
        }
        return CombinedFailure(operation: operation, stage: stage, code: code, id: id, underlying: error, retryable: retryable)
    }
    private static func networkSafeCauseForURLCode(_ code: Int, signing: Bool) -> SafeCause? {
        switch code {
        case NSURLErrorNetworkConnectionLost:
            return signing ? .signingNetworkConnectionLost : .networkConnectionLost
        case NSURLErrorTimedOut:
            return signing ? .signingNetworkTimedOut : .networkTimedOut
        case NSURLErrorNotConnectedToInternet, NSURLErrorCannotConnectToHost,
             NSURLErrorCannotFindHost, NSURLErrorDNSLookupFailed:
            return signing ? .signingNetworkUnavailable : .networkUnavailable
        default:
            return nil
        }
    }

    /// Returns network evidence only for URL transport errors SideStore knows
    /// how to explain. URL-loading domains also carry local file I/O failures,
    /// so domain membership alone is not a network classification.
    public static func knownURLTransportCause(domain: String, code: Int,
                                               signing: Bool = false) -> SafeCause? {
        guard domain == NSURLErrorDomain || domain == "kCFErrorDomainCFNetwork" else { return nil }
        return networkSafeCauseForURLCode(code, signing: signing)
    }

    /// URL cancellation is terminal lifecycle evidence, not network failure.
    public static func isURLCancellation(domain: String, code: Int) -> Bool {
        (domain == NSURLErrorDomain || domain == "kCFErrorDomainCFNetwork") &&
            code == NSURLErrorCancelled
    }

    /// Correlation IDs are UUIDs. Compare their parsed identity so equivalent
    /// uppercase and lowercase UUID spellings stay bound to the same request.
    public static func uuidCorrelationMatches(_ receivedID: String?, expectedID: String) -> Bool {
        guard let receivedID,
              let received = UUID(uuidString: receivedID),
              let expected = UUID(uuidString: expectedID) else { return false }
        return received == expected
    }

    public static func capture(_ error: Error, operation: String, stage: Stage, id: String,
                               retryable: Bool? = nil) -> CombinedFailure {
        if let known = error as? CombinedFailure { return known }
        if let attempt = error as? V3AnisetteAttemptError {
            let known = capture(attempt.underlying, operation: operation, stage: stage,
                                id: id, retryable: retryable)
            var context = known.signingContext
            context.merge(attempt.context.diagnosticFields) { _, observed in observed }
            return CombinedFailure(operation: known.operation, stage: known.stage, code: known.code,
                id: id, underlying: NSError(domain: known.underlyingDomain, code: known.underlyingCode),
                retryable: known.retryable, safeCause: known.safeCause, sourceStep: known.sourceStep,
                signingContext: context, launchContext: known.launchContext)
        }
        if error is LCAnisettePairError {
            return V3AccountOperationError(step: .anisetteFetch, kind: .anisetteIdentityStateInvalid,
                underlying: error, serverCode: nil).failure(operation: operation, id: id)
        }
        // Generic non-auth consumers still see the original native evidence;
        // headless auth captures finite typed/phase detail before arriving here.
        if let phase = error as? V3AuthenticationPhaseError {
            return capture(phase.underlying, operation: operation, stage: stage,
                           id: id, retryable: retryable)
        }
        // Typed account boundaries bypass all provider-description parsing.
        if let accountError = error as? V3AccountOperationError {
            return accountError.failure(operation: operation, id: id)
        }
        if let refreshError = error as? CombinedRefreshVerificationError {
            let code: Code = refreshError == .missingResult ? .missingResult : .staleResult
            return CombinedFailure(operation: operation, stage: .refreshVerification, code: code, id: id, retryable: retryable)
        }
        if let fileFailure = error as? CombinedIPAFileError {
            return CombinedFailure(operation: operation, stage: .filePreparation,
                                  code: fileFailure.combinedCode, id: id, underlying: fileFailure, retryable: retryable)
        }
        var cause = error as NSError
        var resolved = stage
        var resolvedCode: Code = error is CancellationError ? .cancelled : .failed
        var resolvedRetryable: Bool? = error is CancellationError ? false : retryable
        var nativeCode: Int?
        var nativeDomain: String?
        var safeCause: SafeCause?
        var sourceStep: SourceStep?
        var signingContext: [String: String] = [:]
        var ppqLocked = false
        var explicitStageMarker = false
        // Only an allowlisted stage is inspected locally. No arbitrary userInfo is serialized.
        for _ in 0..<5 {
            let fingerprint = cause.localizedDescription.lowercased()
            let tokens = cause.localizedDescription.split(whereSeparator: { $0.isWhitespace })
            if let name = cause.userInfo["LCStructuredFailureStageV1"] as? String,
               let found = Stage(rawValue: name) {
                resolved = found
                explicitStageMarker = true
            } else if signingContext["typed_error"]?.hasPrefix("sideSign") != true,
                      let token = tokens.first(where: { $0.hasPrefix("lc_stage=") }),
                      let found = Stage(rawValue: String(token.dropFirst(9))) {
                resolved = found
                explicitStageMarker = true
            }
            if let name = cause.userInfo["LCStructuredFailureCauseV1"] as? String,
               let found = SafeCause(rawValue: name) {
                safeCause = found
            }
            if isURLCancellation(domain: cause.domain, code: cause.code) {
                resolvedCode = .cancelled
                resolvedRetryable = false
            }
            if let name = cause.userInfo["LCStructuredFailureSourceV1"] as? String,
               let found = SourceStep(rawValue: name) {
                sourceStep = found
            }
            if let fields = cause.userInfo["LCStructuredSigningContextV1"] as? [String: String],
               let safeFields = Self.validatedSigningContext(fields) {
                signingContext.merge(safeFields) { _, deeper in deeper }
            }
            // Domain-specific classification. Only map a numeric code to a
            // stage when the (domain, code) pair has an established meaning.
            // Otherwise preserve the caller stage and keep the underlying
            // domain/code for diagnostics. Unknown stays unknown.
            // Explicit stage markers from SideStore's pipeline take precedence
            // over a broader gateway domain.
            if !ppqLocked && !explicitStageMarker {
                switch cause.domain {
                case "com.SideStore.Authentication":
                    resolved = .authentication
                case NSURLErrorDomain, "kCFErrorDomainCFNetwork":
                    // Only transport-specific URL errors establish network
                    // failure. URLSession also uses this domain for local
                    // download-file and cancellation errors.
                    if let urlCause = knownURLTransportCause(
                        domain: cause.domain, code: cause.code, signing: resolved == .signing) {
                        if resolved != .signing { resolved = .network }
                        if safeCause == nil { safeCause = urlCause }
                    }
                case "MinimuxerError", "DeviceGatewayError", "IdeviceGatewayError":
                    resolved = .command
                default:
                    break
                }
            }
            // Apple-side installation rejection (InstallationProxy/installd
            // application verification). The hex installer codes are matched
            // case-insensitively alongside the verification marker; the stage
            // is installation and the numeric code is preserved with the
            // cause's own allowlisted domain (never a fabricated one).
            // 0xE8008024: provisioning profile banned. 0xE8008018: signing
            // identity no longer valid. Neither implies pairing, network,
            // CoreDevice, or account-ban conditions.
            let installContext = ["install", "installURL", "installSharedIPA", "update"].contains(operation)
                && (stage == .installation || stage == .command)
            let explicitContextAllowsVerification = !explicitStageMarker || resolved == .command || resolved == .installation
            let typedVerificationSource = verificationDomains.contains(cause.domain)
            let profileRejectionEvidence = fingerprint.contains("e8008024")
                && fingerprint.contains("applicationverificationfailed")
                && fingerprint.contains("provisioning profile")
                && (fingerprint.contains("banned") || fingerprint.contains("revoked")
                    || fingerprint.contains("invalid") || fingerprint.contains("failed to verify"))
            let signingIdentityEvidence = fingerprint.contains("e8008018")
                && fingerprint.contains("applicationverificationfailed")
                && fingerprint.contains("identity used to sign")
                && (fingerprint.contains("no longer valid") || fingerprint.contains("invalid")
                    || fingerprint.contains("expired") || fingerprint.contains("revoked"))
            if installContext && explicitContextAllowsVerification && typedVerificationSource {
                if profileRejectionEvidence {
                    resolved = .installation
                    nativeCode = 0xE8008024
                    if nativeDomain == nil, domains.contains(cause.domain) { nativeDomain = cause.domain }
                    ppqLocked = true
                } else if signingIdentityEvidence {
                    resolved = .installation
                    nativeCode = 0xE8008018
                    if nativeDomain == nil, domains.contains(cause.domain) { nativeDomain = cause.domain }
                    ppqLocked = true
                }
            }
            // Upstream gateway/Minimuxer typed errors carry a reason string. Inspect only
            // our fixed machine tokens locally; never forward the reason itself.
            // A preserved numeric code keeps the domain it was actually observed
            // in: gateway tokens stay in their gateway domain, HTTP statuses use
            // the fixed HTTPStatus domain, and POSIX errnos stay in
            // NSPOSIXErrorDomain. No unrelated code is ever relabelled as a
            // gateway error.
            for (index, token) in tokens.enumerated() {
                guard !ppqLocked else { continue }
                // A provider body is not evidence of an HTTP status or errno.
                // Typed SideSign code evidence was captured before NSError bridging.
                if signingContext["typed_error"]?.hasPrefix("sideSign") == true { continue }
                if token.hasPrefix("lc_native_code="), let code = Int(token.dropFirst(15)) {
                    nativeCode = code
                    if ["MinimuxerError", "DeviceGatewayError", "IdeviceGatewayError"].contains(cause.domain) {
                        nativeDomain = cause.domain
                    }
                }
                // HTTP status in "HTTP 503" form (tokens are whitespace-split).
                if (token == "HTTP" || token == "http"), index + 1 < tokens.count,
                   let code = Int(tokens[index + 1]) {
                    nativeCode = code
                    nativeDomain = "HTTPStatus"
                }
                // POSIX errno in "errno=20" / "errno:20" form.
                if token.hasPrefix("errno=") || token.hasPrefix("errno:") {
                    if let code = Int(token.dropFirst(6)) {
                        nativeCode = code
                        nativeDomain = "NSPOSIXErrorDomain"
                    }
                }
            }
            if let next = cause.userInfo[NSUnderlyingErrorKey] as? NSError { cause = next } else { break }
        }
        let underlying: NSError
        if let code = nativeCode {
            if let domain = nativeDomain {
                underlying = NSError(domain: domain, code: code)
            } else if domains.contains(cause.domain) {
                underlying = NSError(domain: cause.domain, code: code)
            } else {
                underlying = NSError(domain: "redacted", code: code)
            }
        } else {
            underlying = cause
        }
        if safeCause == nil && (resolved == .signing || resolved == .network) {
            safeCause = knownURLTransportCause(
                domain: cause.domain, code: cause.code, signing: resolved == .signing)
        }
        return CombinedFailure(operation: operation, stage: resolved,
            code: resolvedCode, id: id,
            underlying: underlying, retryable: resolvedRetryable, safeCause: safeCause,
            sourceStep: sourceStep, signingContext: signingContext)
    }
}

// LC_ANISETTE_PAIR_FAILURE_V1: finite preservation failures only. Never attach
// the identifier, provisioning blob, provider response, or Keychain bytes.
public enum LCAnisettePairError: Error, LocalizedError, Equatable {
    case orphanedBlob, invalidIdentifier, invalidBlob, migrationPairConflict, stateChanged

    public static let safeMessage = "Sign-in is blocked because the saved Anisette identity state could not be verified."
    public static let recovery = "Keep the existing Anisette and account data unchanged. Copy Diagnostics for review before another sign-in."
    public var errorDescription: String? { Self.safeMessage }
}

// V3_AUTHENTICATION_PHASE_EVIDENCE_V1: preserve the original error for retry,
// cancellation and typed matching; never store provider strings or payloads.
struct V3AuthenticationPhaseError: Error {
    let step: CombinedFailure.SourceStep
    let underlying: Error
}
func v3AuthenticationPhase<T>(_ step: CombinedFailure.SourceStep,
                              perform: () async throws -> T) async throws -> T {
    do { return try await perform() }
    catch {
        let native = error as NSError
        if error is CancellationError || CombinedFailure.isURLCancellation(domain: native.domain, code: native.code) { throw error }
        if error is V3AuthenticationPhaseError { throw error }
        throw V3AuthenticationPhaseError(step: step, underlying: error)
    }
}

// DEBUG TEMPORARY: remove this finite, per-attempt diagnostic with the investigation.
// One release-visible switch also controls the native patcher. No TaskLocal/global
// mutable state and no raw provider text, paths, identifiers, blobs or headers.
public struct V3TemporaryAnisetteTrace: Equatable, Sendable {
    public static let temporaryAnisetteTraceEnabled = true
    public static let contextKey = "debug_temporary_anisette_trace"
    public static let maximumEvents = 64
    public static let maximumBytes = 2048
    public enum Step: String, CaseIterable, Sendable {
        case keychainRead, pairValidation, blobPresence, requestHeaders, primaryProvider
        case currentProbe, currentProof, currentSnapshot, legacyRead, legacyCandidate
        case legacyProbe, legacyProof, identityCommit, freshBlobCommit
    }
    public enum Outcome: String, CaseIterable, Sendable { case started, succeeded, failed, skipped }
    public enum Scope: String, CaseIterable, Sendable { case primary, current, legacy }
    private enum NativeEvent: String, CaseIterable, Sendable {
        case argumentsOk = "arguments.ok"
        case argumentsFailed = "arguments.failed"
        case rootOk = "root.ok"
        case rootFailed = "root.failed"
        case uuidDirCreated = "uuid_dir.created"
        case uuidDirExists = "uuid_dir.exists"
        case uuidDirFailed = "uuid_dir.failed"
        case fileOpenOk = "file.open.ok"
        case fileOpenFailed = "file.open.failed"
        case fileStreamOk = "file.stream.ok"
        case fileStreamFailed = "file.stream.failed"
        case fileWriteOk = "file.write.ok"
        case fileWriteFailed = "file.write.failed"
        case fileFlushOk = "file.flush.ok"
        case fileFlushFailed = "file.flush.failed"
        case fileFlushNotChecked = "file.flush.not_checked"
        case fileCloseOk = "file.close.ok"
        case fileCloseFailed = "file.close.failed"
        case fileReadOpenOk = "file.read_open.ok"
        case fileReadOpenFailed = "file.read_open.failed"
        case fileReadbackOk = "file.readback.ok"
        case fileReadbackFailed = "file.readback.failed"
        case fileReadbackNotChecked = "file.readback.not_checked"
        case fileReadCloseOk = "file.read_close.ok"
        case fileReadCloseFailed = "file.read_close.failed"
        case fileRenameOk = "file.rename.ok"
        case fileRenameFailed = "file.rename.failed"
        case vmInitOk = "vm.init.ok"
        case vmInitFailed = "vm.init.failed"
        case vmReused = "vm.reused"
        case setupBegin = "setup.begin"
        case setupOk = "setup.ok"
        case setupFailed = "setup.failed"
        case libraryLoadOk = "library.load.ok"
        case libraryLoadFailed = "library.load.failed"
        case libraryCached = "library.cached"
        case libraryInitOk = "library.init.ok"
        case libraryInitFailed = "library.init.failed"
        case provisioningPathOk = "provisioning_path.ok"
        case provisioningPathFailed = "provisioning_path.failed"
        case provisioningPathCached = "provisioning_path.cached"
        case androidIdOk = "android_id.ok"
        case androidIdFailed = "android_id.failed"
        case androidIdCached = "android_id.cached"
        case nativeSymbolOk = "native.symbol.ok"
        case nativeSymbolFailed = "native.symbol.failed"
        case nativeOtpOk = "native.otp.ok"
        case nativeOtpFailed = "native.otp.failed"
        case nativeOutputOk = "native.output.ok"
        case nativeOutputFailed = "native.output.failed"
        case nativeOutputNotChecked = "native.output.not_checked"
        case cleanupOk = "cleanup.ok"
        case cleanupFailed = "cleanup.failed"
        case cleanupNotNeeded = "cleanup.not_needed"
        case cleanupNotRequested = "cleanup.not_requested"
        case responseAllocationFailed = "response.allocation.failed"
        case traceTruncated = "trace.truncated"
    }
    private enum Event: Equatable, Sendable {
        case step(Step, Outcome)
        case native(Scope, NativeEvent)
        case truncated

        var token: String {
            switch self {
            case .step(let step, let outcome): return "swift.\(step.rawValue).\(outcome.rawValue)"
            case .native(let scope, let event): return "native.\(scope.rawValue).\(event.rawValue)"
            case .truncated: return "trace.truncated"
            }
        }
        init?(token: String) {
            if token == "trace.truncated" { self = .truncated; return }
            let parts = token.components(separatedBy: ".")
            if parts.count == 3, parts[0] == "swift",
               let step = Step(rawValue: parts[1]), let outcome = Outcome(rawValue: parts[2]) {
                self = .step(step, outcome); return
            }
            if parts.count >= 3, parts[0] == "native", let scope = Scope(rawValue: parts[1]),
               let event = NativeEvent(rawValue: parts.dropFirst(2).joined(separator: ".")) {
                self = .native(scope, event); return
            }
            return nil
        }
    }
    private var events: [Event] = []
    public init() {}

    public mutating func record(step: Step, outcome: Outcome) {
        append(.step(step, outcome))
    }
    public mutating func appendNative(errorDescription: String, scope: Scope) {
        guard Self.temporaryAnisetteTraceEnabled,
              let suffix = Self.nativeSuffix(errorDescription) else { return }
        for event in suffix.events { append(.native(scope, event)) }
    }
    private mutating func append(_ event: Event) {
        guard Self.temporaryAnisetteTraceEnabled else { return }
        events.append(event)
        if events.count > Self.maximumEvents || Self.encode(events).utf8.count > Self.maximumBytes {
            if events.first != .truncated { events.insert(.truncated, at: 0) }
            // Drop the oldest observation, keeping the final failure and a
            // finite marker that makes the missing prefix explicit.
            while events.count > Self.maximumEvents || Self.encode(events).utf8.count > Self.maximumBytes {
                events.remove(at: 1)
            }
        }
    }
    private static func encode(_ events: [Event]) -> String {
        "v1;" + events.map(\.token).joined(separator: ";")
    }
    public var snapshot: String? {
        guard Self.temporaryAnisetteTraceEnabled, !events.isEmpty else { return nil }
        return Self.encode(events)
    }
    public init?(encoded: String) {
        guard Self.temporaryAnisetteTraceEnabled, encoded.utf8.count <= Self.maximumBytes,
              encoded.hasPrefix("v1;") else { return nil }
        let tokens = encoded.dropFirst(3).components(separatedBy: ";")
        guard !tokens.isEmpty, tokens.count <= Self.maximumEvents else { return nil }
        var decoded: [Event] = []
        for (index, token) in tokens.enumerated() {
            guard let event = Event(token: token),
                  event != .truncated || index == 0 else { return nil }
            decoded.append(event)
        }
        events = decoded
    }

    // Native suffixes are accepted only in their entirety. Arbitrary leading
    // prose is never retained by the trace or passed to diagnostics.
    private static func nativeSuffix(_ description: String) -> (base: String, events: [NativeEvent])? {
        let marker = " [DEBUG_TEMPORARY_NATIVE_TRACE:"
        guard description.utf8.count <= 4096, description.hasSuffix("]"),
              let range = description.range(of: marker),
              description[range.upperBound...].range(of: marker) == nil else { return nil }
        let body = description[range.upperBound...].dropLast()
        guard !body.isEmpty, body.utf8.count <= 1024 else { return nil }
        let tokens = body.components(separatedBy: ",")
        guard tokens.count <= 32 else { return nil }
        var decoded: [NativeEvent] = []
        for (index, token) in tokens.enumerated() {
            guard let event = NativeEvent(rawValue: token),
                  event != .traceTruncated || index == tokens.count - 1 else { return nil }
            decoded.append(event)
        }
        return (String(description[..<range.lowerBound]), decoded)
    }
    static func nativeDescriptionWithoutTrace(_ description: String) -> String {
        // Stripping valid metadata preserves the pre-existing native phase/code
        // classifier even if a trace-enabled service meets a disabled host.
        nativeSuffix(description)?.base ?? description
    }
    public var technicalDetails: String {
        guard let snapshot else { return "" }
        return "\nDEBUG TEMPORARY anisette_trace=\(snapshot)"
    }
    public var failedStep: String? {
        guard Self.temporaryAnisetteTraceEnabled else { return nil }
        for event in events.reversed() {
            switch event {
            case .step(let step, .failed):
                let scope: Scope?
                switch step {
                case .primaryProvider: scope = .primary
                case .currentProbe: scope = .current
                case .legacyProbe: scope = .legacy
                default: scope = nil
                }
                if let scope, let native = nativeFailedStep(scope: scope) { return native }
                return step.rawValue
            case .native(let scope, let native) where native.rawValue.hasSuffix(".failed"):
                return nativeFailedStep(scope: scope)
            default: continue
            }
        }
        return nil
    }
    private func nativeFailedStep(scope: Scope) -> String? {
        // Setup wrappers and cleanup can also fail after the causal native
        // step. Keep the first failure from this invocation visible; the full
        // ordered trace still includes every subsequent failure.
        for event in events {
            if case .native(let observedScope, let native) = event,
               observedScope == scope, native.rawValue.hasSuffix(".failed") {
                return "\(scope.rawValue).\(native.rawValue.dropLast(7))"
            }
        }
        return nil
    }

}

// Recovery evidence is finite and contains no identity, blob or provider text.
struct V3AnisetteAttemptContext {
    enum BlobState: String { case existing, fresh, unknown }
    enum Recovery: String {
        case notAttempted, noLegacyCandidate, legacyReadFailed, ambiguousLegacyIdentity
        case invalidLegacyPair, legacyBlobMismatch, probeRejected, invalidNativeProof
        case restoreFailed, stateChanged, temporaryStorageUnavailable, currentProbeRejected
        case automaticRecoveryDisabled
    }
    let blobState: BlobState
    let recovery: Recovery
    var probeEvidence: V3AnisetteNativeEvidence? = nil
    var trace: V3TemporaryAnisetteTrace? = nil
    var diagnosticFields: [String: String] {
        var fields = ["anisette_blob_state": blobState.rawValue, "anisette_recovery": recovery.rawValue]
        if let snapshot = trace?.snapshot { fields[V3TemporaryAnisetteTrace.contextKey] = snapshot }
        if let probeEvidence {
            fields["probe_native_code"] = String(probeEvidence.code)
            fields["probe_native_phase"] = probeEvidence.phase.rawValue
            fields["probe_native_subcode"] = probeEvidence.subcode.map(String.init) ?? "unknown"
        }
        return fields
    }
}
struct V3AnisetteAttemptError: Error {
    let underlying: Error
    let context: V3AnisetteAttemptContext
}

// V3_ANISETTE_NATIVE_EVIDENCE_V1: the associated ADI Int32 is not an Apple
// server result or Swift's NSError enum discriminator. Only exact producers in
// AnisetteKit 1f5a7e36553cc865b873f222b87a6486c0bcc7bf Native/anisette_core_{mac,uc}.cpp
// identify a native phase. Descriptions (including paths) never leave here.
struct V3AnisetteNativeEvidence {
    enum Phase: String {
        case unknown, nativeOTP, provisionStart, provisionEnd
        case setupLibraries, setupLoadLibrary, setupProvisioningPath, setupAndroidID
        case readProvisioningData, nativeStorage
    }
    let code: Int32
    let phase: Phase
    let subcode: Int32?

    static func capture(code: Int32, description: String) -> Self {
        let unknown = Self(code: code, phase: .unknown, subcode: nil)
        // Bound inspection before matching. Never scan arbitrary messages for
        // keywords, URLs, digits or error-like substrings.
        let description = V3TemporaryAnisetteTrace.nativeDescriptionWithoutTrace(description)
        guard description.utf8.count <= 256 else { return unknown }
        // Exact fixed producers in the reviewed native staging patch. Numeric
        // equality alone never assigns a storage phase to arbitrary errors.
        if code == -6 && (description == "Checked OTP staging failed" || description == "Isolated OTP staging failed") {
            return Self(code: code, phase: .nativeStorage, subcode: nil)
        }
        let storagePrefix = "Checked OTP staging failed (errno "
        if code == -6 {
            let value = String(description.dropFirst(storagePrefix.count).dropLast())
            if let observedErrno = Int32(value), observedErrno > 0, observedErrno <= 4095,
               description == "\(storagePrefix)\(observedErrno))" {
                // For nativeStorage only, subcode is the failed POSIX call's
                // immediately captured errno, not an ADI or Apple server code.
                return Self(code: code, phase: .nativeStorage, subcode: observedErrno)
            }
        }
        for (symbol, phase) in [("ADIOTPRequest", Phase.nativeOTP),
                                ("ADIProvisioningStart", .provisionStart),
                                ("ADIProvisioningEnd", .provisionEnd)] {
            if (code == -3 && description == "Symbol \(symbol) missing") ||
               (code != 0 && description == failureDescription(symbol, code: code)) {
                return Self(code: code, phase: phase, subcode: nil)
            }
        }
        if code == -4 && description == "Failed to read generated adi.pb" {
            return Self(code: code, phase: .readProvisioningData, subcode: nil)
        }
        // All setup failures return wrapper -2, even when a setup ADI call
        // reports another number. Keep that nested scalar separate as well.
        guard code == -2 else { return unknown }
        let setupSymbols: [(String, String, Phase)] = [
            ("ADILoadLibraryWithPath", "ADILoadLibraryWithPath (kq56gsgHG6)", .setupLoadLibrary),
            ("ADISetProvisioningPath", "ADISetProvisioningPath", .setupProvisioningPath),
            ("ADISetAndroidID", "ADISetAndroidID", .setupAndroidID)
        ]
        if let tail = description.components(separatedBy: ": ").last,
           let subcode = Int32(tail), subcode != 0, String(subcode) == tail {
            for (ucSymbol, macSymbol, phase) in setupSymbols {
                if description == "\(ucSymbol) failed: \(subcode)" ||
                   description == failureDescription(macSymbol, code: subcode) {
                    return Self(code: code, phase: phase, subcode: subcode)
                }
            }
        }
        let fixedSetup: [String: Phase] = [
            "Library directory path is null.": .setupLibraries,
            "Failed to load libraries into VM": .setupLibraries,
            "Required ADI setup symbol missing in VM": .setupLoadLibrary,
            "Symbol ADILoadLibraryWithPath (kq56gsgHG6) missing from libraries": .setupLoadLibrary,
            "Symbol ADISetProvisioningPath missing in VM": .setupProvisioningPath,
            "Symbol ADISetProvisioningPath (nf92ngaK92) missing": .setupProvisioningPath,
            "Symbol ADISetAndroidID missing in VM": .setupAndroidID,
            "Symbol ADISetAndroidID (Sph98paBcz) missing": .setupAndroidID
        ]
        return Self(code: code, phase: fixedSetup[description] ?? .unknown, subcode: nil)
    }

    private static func failureDescription(_ symbol: String, code: Int32) -> String {
        // Exact finite labels from Native/anisette_base.cpp at the same pin.
        // Matching the label against its code rejects even plausible-looking
        // injected descriptions. Unknown numeric results remain observable.
        let labels: [Int32: String] = [
            -1: "Invalid argument passed", -2: "ELF Loader failed to map dependencies",
            -3: "Required ADI symbol missing", -4: "Failed to read generated file",
            -5: "Failed to parse response JSON",
            -45001: "Invalid ADI parameters (-45001)", -45002: "Invalid ADI decipher params (-45002)",
            -45003: "Invalid ADI trust key (-45003)", -45006: "PTM and TK mismatch (-45006)",
            -45018: "Invalid input header (-45018)", -45019: "Unknown ADI function (-45019)",
            -45020: "Invalid input body (-45020)", -45025: "Unknown ADI session (-45025)",
            -45026: "Empty ADI session (-45026)", -45031: "Invalid data header (-45031)",
            -45032: "Data too short (-45032)", -45033: "Invalid data body (-45033)",
            -45034: "Unknown call flags (-45034)", -45036: "ADI time error (-45036)",
            -45046: "Empty hardware IDs (-45046)", -45054: "ADI filesystem error (-45054)",
            -45061: "Device not provisioned (-45061)", -45062: "Cannot erase unprovisioned device (-45062)",
            -45063: "Pending ADI session (-45063)", -45066: "ADI session already done (-45066)",
            -45075: "Library loading failed (-45075)"
        ]
        return "\(symbol) failed (\(labels[code] ?? "Unknown ADI error")): \(code)"
    }
}

// V3_TYPED_ACCOUNT_DIAGNOSTICS_V1: only operation-owned stage and fixed
// classifications cross the wire. Original errors stay inside the process for
// typed guidance; descriptions, userInfo and provider payloads never serialize.
struct V3AccountOperationError: Error, LocalizedError {
    enum Kind: String {
        case keychainWrite, keychainValidationFailed, keychainOutcomeUnknown
        case legacyMigrationConflict, persistenceFailure, persistenceOutcomeUnknown, transportFailure
        case sideSignServerReportedError, sideSignBadResponse, sideSignInvalidResponse
        case sideSignMissingKey, sideSignDeveloperPortalError, anisetteFailure
        case anisetteKitInvalidArgument, anisetteKitLoaderFailed, anisetteKitSymbolMissing, anisetteKitReadFailure, anisetteKitInvalidResponse, anisetteKitADIError, anisetteKitLibrariesNotFound, anisetteKitHTTPError, decodingTypeMismatch, decodingValueNotFound, decodingKeyNotFound, decodingDataCorrupted
        case archiveFileNotFound, archiveCorrupt, archiveReadFailed, archiveWriteFailed, archiveMissingApp
        case unknownAccountFailure
        case anisetteIdentityStateInvalid
    }
    let step: CombinedFailure.SourceStep
    let kind: Kind
    let underlying: Error
    let serverCode: Int?
    var httpStatus: Int? = nil
    var nativeEvidence: V3AnisetteNativeEvidence? = nil
    var anisetteAttempt: V3AnisetteAttemptContext? = nil

    var errorDescription: String? { "An account operation failed; review the safe diagnostics." }
    var credentialCommit: Bool { step == .credentialCommit && kind != .anisetteIdentityStateInvalid }
    // Apple Developer Portal result 1100 rejects the portal session. Scope this
    // to the observed team-list boundary; an NSError bridge code is not proof.
    var portalSessionRejected: Bool {
        step == .fetchTeams && kind == .sideSignServerReportedError && serverCode == 1100
    }
    var requiresReconciliation: Bool {
        kind == .keychainOutcomeUnknown || kind == .persistenceOutcomeUnknown
    }
    var failureStage: CombinedFailure.Stage {
        if kind == .anisetteIdentityStateInvalid { return .authentication }
        switch step {
        case .credentialCommit, .saveAccount, .activateAccount, .activateCertificate: return .persistence
        case .authenticate, .anisetteFetch, .appleAuthentication, .accountLookup: return .authentication
        default: return .provisioning
        }
    }
    func failure(operation: String, id: String) -> CombinedFailure {
        let native = underlying as NSError
        let safeCause: CombinedFailure.SafeCause?
        if kind == .anisetteIdentityStateInvalid { safeCause = nil }
        else if credentialCommit {
            safeCause = kind == .keychainOutcomeUnknown ? .credentialCommitOutcomeUnknown : .credentialCommitFailed
        } else if step == .activateAccount { safeCause = .accountActivationFailed }
        else if step == .activateCertificate || step == .saveAccount { safeCause = .provisioningStorageFailed }
        else { safeCause = nil }
        // An associated Apple result is separate from Swift's enum bridge code.
        // HTTP status remains unavailable unless a typed producer observes it.
        var signingContext = ["typed_error": kind.rawValue,
            "server_code": serverCode.map(String.init) ?? "unknown", "http_status": httpStatus.map(String.init) ?? "unavailable"]
        if kind == .anisetteKitADIError, let nativeEvidence {
            signingContext["native_code"] = String(nativeEvidence.code)
            signingContext["native_phase"] = nativeEvidence.phase.rawValue
            signingContext["native_subcode"] = nativeEvidence.subcode.map(String.init) ?? "unknown"
        }
        if let anisetteAttempt {
            signingContext.merge(anisetteAttempt.diagnosticFields) { _, observed in observed }
        }
        return CombinedFailure(operation: operation, stage: failureStage, id: id,
            underlying: NSError(domain: native.domain, code: native.code),
            retryable: safeCause != nil || portalSessionRejected || kind == .anisetteIdentityStateInvalid ? false : nil, safeCause: safeCause,
            sourceStep: kind == .anisetteIdentityStateInvalid ? .anisetteFetch : step,
            signingContext: signingContext)
    }
}

// Journal only the account/team activation transaction, before the database is
// touched. No password, DSID, token, certificate or provider payload is stored.
// A crash is resolved from a fresh persistent-store read against the exact
// pre-transaction or intended active identity set, never account-row presence.
struct V3AccountDatabaseOutcomeUnknownError: Error, LocalizedError {
    var errorDescription: String? { "The local account activation outcome needs reconciliation." }
}

enum V3AccountDatabaseRecovery {
    static let key = "V3AccountDatabaseActivationPendingV1"
    static var requiresReconciliation: Bool { UserDefaults.standard.object(forKey: key) != nil }

    private static func valid(_ values: [String]) -> Bool {
        values.count <= 1024 && values == values.sorted() && Set(values).count == values.count &&
            values.allSatisfy { value in
                value.utf8.count <= 1024 &&
                    ((value.hasPrefix("account:") && value.count > 8) ||
                     (value.hasPrefix("team:") && value.count > 5))
            }
    }
    static func begin(previous: [String], intended: [String], defaults: UserDefaults = .standard) throws {
        guard valid(previous), valid(intended), defaults.object(forKey: key) == nil else {
            throw V3AccountDatabaseOutcomeUnknownError()
        }
        let record: [String: Any] = ["previous": previous, "intended": intended]
        defaults.set(record, forKey: key)
        guard defaults.synchronize(),
              let saved = defaults.dictionary(forKey: key),
              saved["previous"] as? [String] == previous,
              saved["intended"] as? [String] == intended else {
            throw V3AccountDatabaseOutcomeUnknownError()
        }
    }
    static func reconcile(observed: [String], defaults: UserDefaults = .standard) throws {
        guard defaults.object(forKey: key) != nil else { return }
        guard valid(observed), let record = defaults.dictionary(forKey: key),
              Set(record.keys) == Set(["previous", "intended"]),
              let previous = record["previous"] as? [String], valid(previous),
              let intended = record["intended"] as? [String], valid(intended),
              observed == previous || observed == intended else {
            throw V3AccountDatabaseOutcomeUnknownError()
        }
        defaults.removeObject(forKey: key)
        guard defaults.synchronize(), defaults.object(forKey: key) == nil else {
            // Restore the hold if durable removal cannot be established.
            defaults.set(record, forKey: key)
            _ = defaults.synchronize()
            throw V3AccountDatabaseOutcomeUnknownError()
        }
    }
}

// V3_POST_MUTATION_PERSISTENCE_CONTRACT_V1: the device operation may already
// have succeeded when this local durable save fails. Keep only fixed semantic
// markers; never retain or serialize the native Core Data error text/userInfo.
struct V3PostMutationPersistenceError: Error, CustomNSError, LocalizedError {
    static var errorDomain: String { "V3PostMutationPersistenceErrorDomain" }
    var errorCode: Int { 1 }
    var errorUserInfo: [String: Any] {
        [NSLocalizedDescriptionKey: "SideStore could not confirm that the operation result was saved. The device may already have changed.",
         "LCStructuredFailureStageV1": CombinedFailure.Stage.persistence.rawValue,
         "LCStructuredFailureCauseV1": CombinedFailure.SafeCause.operationPersistenceFailed.rawValue]
    }
    var errorDescription: String? {
        errorUserInfo[NSLocalizedDescriptionKey] as? String
    }
}

enum V3MutationPersistencePolicy {
    /// The Runner calls this only after its device-side pipeline returned success.
    /// A false `hasChanges` means the state was already durable; a failed save
    /// throws a fixed non-retryable outcome instead of allowing group success.
    static func persistResult(hasChanges: Bool, save: () throws -> Void) throws {
        guard hasChanges else { return }
        do {
            try save()
        } catch {
            throw V3PostMutationPersistenceError()
        }
    }
}

public enum CombinedRefreshVerificationError: Error, Equatable {
    case missingResult
    case staleResult
}

public struct CombinedIPAFileError: Error, LocalizedError, CustomNSError {
    public enum Problem: String, Equatable {
        case invalidToken, missingFile, emptyFile, invalidPackage, fileAccess, stagingFailed
    }
    public let problem: Problem
    public static let errorDomain = "V3IPAFileErrorDomain"
    public var errorCode: Int {
        switch problem {
        case .invalidToken: return 1
        case .missingFile: return 2
        case .emptyFile: return 3
        case .invalidPackage: return 4
        case .fileAccess: return 5
        case .stagingFailed: return 6
        }
    }
    public var errorUserInfo: [String: Any] { [NSLocalizedDescriptionKey: errorDescription ?? "IPA file preparation failed."] }
    public init(_ problem: Problem) { self.problem = problem }
    public var combinedCode: CombinedFailure.Code {
        switch problem {
        case .invalidToken: return .invalidToken
        case .missingFile: return .missingFile
        case .emptyFile: return .emptyFile
        case .invalidPackage: return .invalidPackage
        case .fileAccess: return .fileAccess
        case .stagingFailed: return .stagingFailed
        }
    }
    public var errorDescription: String? {
        switch problem {
        case .invalidToken: return "The staged IPA reference is invalid."
        case .missingFile: return "The staged IPA is no longer available."
        case .emptyFile: return "The selected IPA is empty."
        case .invalidPackage: return "The selected file is not a valid IPA app package."
        case .fileAccess: return "The selected IPA could not be read."
        case .stagingFailed: return "The selected IPA could not be staged."
        }
    }
}

private func v3StrictPlistInteger(_ value: Any?) -> Int? {
    guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else {
        return nil
    }
    let type = String(cString: number.objCType)
    guard ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(type) else {
        return nil
    }
    if ["C", "S", "I", "L", "Q"].contains(type) {
        return Int(exactly: number.uint64Value)
    }
    return Int(exactly: number.int64Value)
}

enum V3NotDispatchedReplyPolicy {
    static func confirms(_ data: Data, requestID: String, maximumBytes: Int) -> Bool {
        guard maximumBytes > 0, !data.isEmpty, data.count <= maximumBytes,
              let reply = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              v3StrictPlistInteger(reply["version"]) == 1,
              CombinedFailure.uuidCorrelationMatches(reply["id"] as? String, expectedID: requestID),
              reply["error"] as? String != nil,
              reply["result"] == nil,
              reply["ok"] == nil,
              let notDispatched = reply["operationNotDispatched"] as? NSNumber,
              CFGetTypeID(notDispatched) == CFBooleanGetTypeID(), notDispatched.boolValue,
              let failure = reply["failure"] as? [String: Any],
              CombinedFailure.decode(failure, expectedID: requestID) != nil else { return false }
        return true
    }
}

enum V3AnisetteSyncFailurePolicy {
    /// Converts only evidence exposed by the pinned Anisette sync path into a
    /// semantic failure. URL transport errors and AnisetteServersManager's
    /// explicit HTTP/invalid-response errors are distinct; every other error
    /// remains unknown rather than being called a network failure.
    static func failure(_ error: Error, id: String) -> CombinedFailure {
        if error is CancellationError {
            return CombinedFailure(operation: "anisetteSync", stage: .command,
                code: .cancelled, id: id, retryable: false)
        }
        let native = error as NSError
        if native.domain == NSURLErrorDomain && native.code == NSURLErrorCancelled {
            return CombinedFailure(operation: "anisetteSync", stage: .command,
                code: .cancelled, id: id, underlying: native, retryable: false)
        }
        if let urlError = error as? URLError {
            if urlError.code == .cancelled {
                return CombinedFailure(operation: "anisetteSync", stage: .command,
                    code: .cancelled, id: id, underlying: native, retryable: false)
            }
            if let cause = networkCause(urlError.code) {
                return CombinedFailure(operation: "anisetteSync", stage: .network,
                    code: .failed, id: id, underlying: error, retryable: true, safeCause: cause)
            }
        }
        if native.domain == NSURLErrorDomain,
           let cause = networkCause(URLError.Code(rawValue: native.code)) {
            return CombinedFailure(operation: "anisetteSync", stage: .network,
                code: .failed, id: id, underlying: native, retryable: true, safeCause: cause)
        }
        if native.domain == "AnisetteServersManager" {
            if native.code == -1 {
                return CombinedFailure(operation: "anisetteSync", stage: .command,
                    code: .invalidResponse, id: id, underlying: native,
                    safeCause: .anisetteInvalidResponse)
            }
            if native.code == 408 {
                return CombinedFailure(operation: "anisetteSync", stage: .command,
                    code: .failed, id: id, underlying: native, retryable: true,
                    safeCause: .anisetteRequestTimedOut)
            }
            if native.code == 429 {
                return CombinedFailure(operation: "anisetteSync", stage: .command,
                    code: .busy, id: id, underlying: native, retryable: true,
                    safeCause: .anisetteRateLimited)
            }
            if (500..<600).contains(native.code) {
                return CombinedFailure(operation: "anisetteSync", stage: .command,
                    code: .failed, id: id, underlying: native, retryable: true,
                    safeCause: .anisetteServerUnavailable)
            }
            if (100..<500).contains(native.code), !(200..<300).contains(native.code) {
                return CombinedFailure(operation: "anisetteSync", stage: .command,
                    code: .failed, id: id, underlying: native,
                    safeCause: .anisetteServerRejected)
            }
        }
        return CombinedFailure(operation: "anisetteSync", stage: .command,
            code: .failed, id: id, underlying: error, safeCause: .anisetteUnknownFailure)
    }

    private static func networkCause(_ code: URLError.Code) -> CombinedFailure.SafeCause? {
        switch code {
        case .networkConnectionLost: return .networkConnectionLost
        case .timedOut: return .networkTimedOut
        case .notConnectedToInternet, .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed:
            return .networkUnavailable
        default: return nil
        }
    }
}


// V3_STABLE_DIAGNOSTIC_CODE_V1: identifiers classify finite evidence, never attempts.
// Values are append-only. See docs/ERROR_CODES_V1.json; never renumber existing cases.
extension CombinedFailure {
    public var diagnosticCode: String {
        var parts = ["SS", stage.diagnosticToken, code.diagnosticToken]
        if let sourceStep { parts.append(sourceStep.diagnosticToken) }
        if let safeCause { parts.append(safeCause.diagnosticToken) }
        if let typed = signingContext["typed_error"], let token = Self.typedDiagnosticToken(typed) { parts.append(token) }
        if let launchContext { parts.append(launchContext.sourceStep.diagnosticToken) }
        if hasApplicationVerificationEvidence && underlyingCode == 0xE8008024 { parts.append("V01") }
        if hasApplicationVerificationEvidence && underlyingCode == 0xE8008018 { parts.append("V02") }
        if sourceStep == .fetchTeams && signingContext["typed_error"] == "sideSignServerReportedError" && signingContext["server_code"] == "1100" { parts.append("P01") }
        return parts.joined(separator: "-")
    }
    public var diagnosticLabel: String { "Error ID: \(diagnosticCode)" }
    fileprivate static func typedDiagnosticToken(_ value: String) -> String? {
        switch value {
        case "sideSignServerReportedError": return "T01"
        case "sideSignBadResponse": return "T02"
        case "sideSignInvalidResponse": return "T03"
        case "sideSignMissingKey": return "T04"
        case "sideSignDeveloperPortalError": return "T05"
        case "keychainWrite": return "T06"
        case "keychainValidationFailed": return "T07"
        case "keychainOutcomeUnknown": return "T08"
        case "legacyMigrationConflict": return "T09"
        case "persistenceFailure": return "T10"
        case "persistenceOutcomeUnknown": return "T11"
        case "transportFailure": return "T12"
        case "anisetteFailure": return "T13"
        case "anisetteKitInvalidArgument": return "T14"
        case "anisetteKitLoaderFailed": return "T15"
        case "anisetteKitSymbolMissing": return "T16"
        case "anisetteKitReadFailure": return "T17"
        case "anisetteKitInvalidResponse": return "T18"
        case "anisetteKitADIError": return "T19"
        case "anisetteKitLibrariesNotFound": return "T20"
        case "anisetteKitHTTPError": return "T21"
        case "decodingTypeMismatch": return "T22"
        case "decodingValueNotFound": return "T23"
        case "decodingKeyNotFound": return "T24"
        case "decodingDataCorrupted": return "T25"
        case "archiveFileNotFound": return "T26"
        case "archiveCorrupt": return "T27"
        case "archiveReadFailed": return "T28"
        case "archiveWriteFailed": return "T29"
        case "archiveMissingApp": return "T30"
        case "unknownAccountFailure": return "T31"
        case "anisetteIdentityStateInvalid": return "T32"
        default: return nil
        }
    }
}
extension CombinedFailure.Stage {
    fileprivate var diagnosticToken: String {
        switch self {
        case .hostContainer: return "HOST"
        case .storagePreparation: return "STORE"
        case .bookmarkCreation: return "BOOK"
        case .extensionDiscovery: return "DISC"
        case .extensionLaunch: return "LAUNCH"
        case .xpcConnection: return "XPC"
        case .serviceReadiness: return "READY"
        case .command: return "CMD"
        case .authentication: return "AUTH"
        case .provisioning: return "PROV"
        case .signing: return "SIGN"
        case .filePreparation: return "IPA"
        case .installation: return "INSTALL"
        case .persistence: return "SAVE"
        case .refreshVerification: return "VERIFY"
        case .replyEncoding: return "REPLY"
        case .endpointSelection: return "ENDPOINT"
        case .heartbeat: return "HEART"
        case .coreDevice: return "CORE"
        case .cdTunnel: return "TUNNEL"
        case .rsdDiscovery: return "RSD"
        case .rsdService: return "SERVICE"
        case .lockdownConnection: return "LOCK"
        case .uniqueDeviceID: return "UDID"
        case .pairing: return "PAIR"
        case .network: return "NET"
        case .source: return "SOURCE"
        case .catalog: return "CAT"
        }
    }
}
extension CombinedFailure.Code {
    fileprivate var diagnosticToken: String {
        switch self {
        case .unavailable: return "C01"
        case .invalidConfiguration: return "C02"
        case .permissionDenied: return "C03"
        case .timedOut: return "C04"
        case .cancelled: return "C05"
        case .interrupted: return "C06"
        case .notReady: return "C07"
        case .busy: return "C08"
        case .invalidResponse: return "C09"
        case .unsupported: return "C10"
        case .failed: return "C11"
        case .missingResult: return "C12"
        case .staleResult: return "C13"
        case .invalidToken: return "C14"
        case .missingFile: return "C15"
        case .emptyFile: return "C16"
        case .invalidPackage: return "C17"
        case .fileAccess: return "C18"
        case .stagingFailed: return "C19"
        }
    }
}
extension CombinedFailure.SafeCause {
    fileprivate var diagnosticToken: String {
        switch self {
        case .networkConnectionLost: return "F01"
        case .networkTimedOut: return "F02"
        case .networkUnavailable: return "F03"
        case .anisetteServerUnavailable: return "F04"
        case .anisetteServerRejected: return "F05"
        case .anisetteRequestTimedOut: return "F06"
        case .anisetteRateLimited: return "F07"
        case .anisetteInvalidResponse: return "F08"
        case .anisetteUnknownFailure: return "F09"
        case .signingNetworkConnectionLost: return "F10"
        case .signingNetworkTimedOut: return "F11"
        case .signingNetworkUnavailable: return "F12"
        case .developerPortalRejectedRequest: return "F13"
        case .appIDLimitReached: return "F14"
        case .developerPortalInvalidResponse: return "F15"
        case .provisioningProfileUnavailable: return "F16"
        case .certificateUnavailable: return "F17"
        case .signingStorageUnverified: return "F18"
        case .wifiUnavailable: return "F19"
        case .localDevVPNUnavailable: return "F20"
        case .unknownSigningCause: return "F21"
        case .sourceNetworkFailure: return "F22"
        case .sourceInvalidManifest: return "F23"
        case .sourcePersistenceUnverified: return "F24"
        case .sourceInvalidURL: return "F25"
        case .sourceBlocked: return "F26"
        case .sourceChangedID: return "F27"
        case .sourceDuplicate: return "F28"
        case .sourceUnsupported: return "F29"
        case .sourceValidationFailed: return "F30"
        case .sourceRemoveFailed: return "F31"
        case .sourceRemoveBusy: return "F32"
        case .sourceAddBusy: return "F33"
        case .operationInProgress: return "F34"
        case .responseCapacityUnavailable: return "F35"
        case .sharedStoreUnavailable: return "F36"
        case .secretHandoffUnavailable: return "F37"
        case .staleRefreshAttempt: return "F38"
        case .knownSourcePolicyNetworkFailure: return "F39"
        case .knownSourcePolicyInvalidResponse: return "F40"
        case .catalogUnavailable: return "F41"
        case .catalogSourceUnavailable: return "F42"
        case .responseEncodingFailed: return "F43"
        case .responseTooLarge: return "F44"
        case .pairingRequired: return "F45"
        case .invalidPairingFile: return "F46"
        case .pairingFilePreparationFailed: return "F47"
        case .authAttemptNotDispatched: return "F48"
        case .authProvisioningRetryNotDispatched: return "F49"
        case .authSessionUnavailable: return "F50"
        case .authResponseCapacityUnavailable: return "F51"
        case .credentialCommitFailed: return "F52"
        case .credentialCommitOutcomeUnknown: return "F53"
        case .accountActivationFailed: return "F54"
        case .provisioningStorageFailed: return "F55"
        case .keychainSignOutFailed: return "F56"
        case .keychainSignOutOutcomeUnknown: return "F57"
        case .operationPersistenceFailed: return "F58"
        case .recoveryMalformedRecord: return "F59"
        case .recoveryIncompatibleRecord: return "F60"
        case .recoveryStorageUnavailable: return "F61"
        case .recoveryLockUnavailable: return "F62"
        case .recoveryReadFailure: return "F63"
        case .recoveryDeleteFailure: return "F64"
        }
    }
}
extension CombinedFailure.SourceStep {
    fileprivate var diagnosticToken: String {
        switch self {
        case .authenticate: return "S01"
        case .anisetteFetch: return "S02"
        case .appleAuthentication: return "S03"
        case .accountLookup: return "S04"
        case .credentialCommit: return "S05"
        case .fetchTeams: return "S06"
        case .saveAccount: return "S07"
        case .fetchCertificate: return "S08"
        case .activateCertificate: return "S09"
        case .registerDevice: return "S10"
        case .activateAccount: return "S11"
        case .provisioningUnknown: return "S12"
        case .provisioningProfileFetch: return "S13"
        case .certificateValidation: return "S14"
        case .localCodeSigning: return "S15"
        case .appIDLookup: return "S16"
        case .appIDRegistration: return "S17"
        case .appIDCapabilitiesUpdate: return "S18"
        case .appGroupLookup: return "S19"
        case .appGroupRegistration: return "S20"
        case .appGroupAssignment: return "S21"
        case .provisioningProfileRetrieval: return "S22"
        case .provisioningProfileCreation: return "S23"
        case .provisioningProfileUpdate: return "S24"
        case .sourceDownload: return "S25"
        case .manifestParsing: return "S26"
        case .sourceValidation: return "S27"
        case .knownSourcePolicyFetch: return "S28"
        case .knownSourcePolicyParsing: return "S29"
        case .catalogRead: return "S30"
        }
    }
}
extension CombinedFailure.LaunchContext.Step {
    fileprivate var diagnosticToken: String {
        switch self {
        case .hostBundleUnavailable: return "L01"
        case .missingPluginDirectory: return "L02"
        case .liveProcessBundleMissing: return "L03"
        case .liveProcessBundleUnreadable: return "L04"
        case .bundleIdentifierMissing: return "L05"
        case .executableMetadataMissing: return "L06"
        case .executableFileMissing: return "L07"
        case .extensionFactory: return "L08"
        case .extensionFactoryNil: return "L09"
        case .listenerCreation: return "L10"
        case .requestCallbackNoIdentifier: return "L11"
        case .requestCancellation: return "L12"
        case .requestInterruption: return "L13"
        case .requestCallbackError: return "L14"
        case .processIdentifierUnavailable: return "L15"
        case .xpcRemoteObjectError: return "L16"
        case .xpcInvalidation: return "L17"
        case .xpcPeerRejected: return "L18"
        case .readinessProbe: return "L19"
        case .startupTimeout: return "L20"
        case .connectionStopped: return "L21"
        case .unknown: return "L22"
        }
    }
}

// These diagnostic APIs are consumed by the separate LiveContainer app module
// through SideStoreSupport. Keep implementation-only helpers internal.
// Public build provenance only. Reject unexpected metadata rather than copying
// arbitrary Info.plist values into a diagnostic payload.
public enum V3DiagnosticBuild {
    public static var commit: String { validatedCommit(Bundle.main.object(forInfoDictionaryKey: "LCBuilderCommit")) }
    static func validatedCommit(_ value: Any?) -> String {
        guard let value = value as? String, value.utf8.count == 40,
              value.range(of: "^[0-9a-fA-F]{40}$", options: .regularExpression) != nil else { return "unknown" }
        return value.lowercased()
    }
}

// Local UI conditions can accompany a more specific underlying failure. Copy
// both classifications without copying message prose or guessing its cause.
public enum V3DiagnosticCopy {
    private static let localCodes: Set<String> = [
        "SS-PROV-D099",
        "SS-PROV-D100",
        "SS-PROV-D101",
        "SS-PROV-D102",
        "SS-PROV-D103",
        "SS-PROV-D104",
        "SS-PROV-D105",
        "SS-PROV-D106",
        "SS-PROV-D107",
        "SS-REFRESH-UNKNOWN", "SS-OPERATION-UNKNOWN", "SS-UI-UNKNOWN",
        "SS-AUTH-D024",
        "SS-AUTH-D032",
        "SS-AUTH-D033",
        "SS-AUTH-D034",
        "SS-AUTH-D035",
        "SS-AUTH-D036",
        "SS-AUTH-D037",
        "SS-AUTH-D038",
        "SS-AUTH-D039",
        "SS-AUTH-D040",
        "SS-AUTH-D041",
        "SS-AUTH-D060",
        "SS-AUTH-D069",
        "SS-AUTH-D070",
        "SS-AUTH-D071",
        "SS-AUTH-D072",
        "SS-AUTH-D073",
        "SS-AUTH-D074",
        "SS-AUTH-D075",
        "SS-AUTH-D076",
        "SS-AUTH-D077",
        "SS-AUTH-D078",
        "SS-AUTH-D079",
        "SS-AUTH-D080",
        "SS-AUTH-D081",
        "SS-AUTH-D082",
        "SS-AUTH-D083",
        "SS-AUTH-D089",
        "SS-AUTH-D092",
        "SS-AUTH-D093",
        "SS-AUTH-D094",
        "SS-AUTH-D095",
        "SS-CAT-D001",
        "SS-CAT-D017",
        "SS-CMD-D002",
        "SS-CMD-D004",
        "SS-CMD-D009",
        "SS-CMD-D010",
        "SS-CMD-D012",
        "SS-CMD-D013",
        "SS-CMD-D014",
        "SS-CMD-D015",
        "SS-CMD-D016",
        "SS-CMD-D019",
        "SS-CMD-D020",
        "SS-CMD-D021",
        "SS-CMD-D022",
        "SS-CMD-D023",
        "SS-CMD-D026",
        "SS-CMD-D027",
        "SS-CMD-D029",
        "SS-CMD-D045",
        "SS-CMD-D047",
        "SS-CMD-D050",
        "SS-CMD-D051",
        "SS-CMD-D055",
        "SS-CMD-D056",
        "SS-CMD-D064",
        "SS-CMD-D065",
        "SS-CMD-D066",
        "SS-CMD-D067",
        "SS-CMD-D068",
        "SS-CMD-D084",
        "SS-CMD-D085",
        "SS-CMD-D087",
        "SS-CMD-D088",
        "SS-IPA-D006",
        "SS-IPA-D007",
        "SS-IPA-D008",
        "SS-IPA-D011",
        "SS-NET-D061",
        "SS-NET-D096",
        "SS-PAIR-D043",
        "SS-READY-D005",
        "SS-SAVE-D018",
        "SS-SAVE-D030",
        "SS-SAVE-D031",
        "SS-SAVE-D042",
        "SS-SAVE-D059",
        "SS-SAVE-D086",
        "SS-SAVE-D090",
        "SS-SAVE-D091",
        "SS-SIGN-D097",
        "SS-SOURCE-D025",
        "SS-VERIFY-D003",
        "SS-VERIFY-D044",
        "SS-VERIFY-D046",
        "SS-VERIFY-D048",
        "SS-VERIFY-D049",
        "SS-VERIFY-D052",
        "SS-VERIFY-D053",
        "SS-VERIFY-D054",
        "SS-VERIFY-D057",
        "SS-VERIFY-D058",
        "SS-VERIFY-D062",
        "SS-VERIFY-D063",
        "SS-VERIFY-D098",
        "SS-XPC-D028",
    ]
    public static func details(visibleMessage: String, technical: String) -> String {
        let line = visibleMessage.components(separatedBy: "\n").last ?? ""
        let prefix = "Error ID: "
        let value = line.hasPrefix(prefix) ? String(line.dropFirst(prefix.count)) : ""
        let labels = localCodes.contains(value) ? "visible_error_id=\(value)" : ""
        let build = technical.contains("builder_commit=") ? "" : "builder_commit=\(V3DiagnosticBuild.commit)\n"
        return build + (labels.isEmpty ? "" : labels + "\n") + technical
    }
}

// Historical/plain messages have no recoverable typed cause. This fallback
// identifies only the known presentation flow and leaves original text intact.
public enum V3DiagnosticPresentation {
    public enum Context: String {
        case refresh = "SS-REFRESH-UNKNOWN"
        case operation = "SS-OPERATION-UNKNOWN"
        case global = "SS-UI-UNKNOWN"
    }
    public static func label(_ message: String, context: Context) -> String {
        guard !message.contains("\nError ID: SS-") else { return message }
        return message + "\nError ID: " + context.rawValue
    }
}
