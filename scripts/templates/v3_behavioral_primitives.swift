import Foundation

enum V3SetupSnapshotOutcome: String, Equatable {
    case applied
    case snapshotFailed
    case notObserved
}

enum V3SetupReloadRecomputePolicy {
    static func mayRecompute(outcome: V3SetupSnapshotOutcome) -> Bool {
        outcome == .applied
    }
}

enum V3AuthReadStampPolicy {
    static func ownsTicket(captured: UInt64, current: UInt64) -> Bool {
        captured == current
    }

    static func mayReturn(capturedStamp: String, currentStamp: String, stable: Bool) -> Bool {
        stable && capturedStamp == currentStamp
    }

    static func mayCommit(capturedTicket: UInt64, currentTicket: UInt64,
                          capturedStamp: String, currentStamp: String?, stable: Bool,
                          resultStamps: [String?], authenticationActive: Bool = false) -> Bool {
        stable && !authenticationActive && capturedTicket == currentTicket &&
            currentStamp == capturedStamp && !resultStamps.isEmpty &&
            resultStamps.allSatisfy { $0 == capturedStamp }
    }
}

enum V3AuthSessionCoalescerKey {
    static func value(for identityStamp: String) -> String {
        "apple_auth_session:" + identityStamp
    }
}

struct V3AsyncRequestOwner: Equatable, Sendable {
    let generation: UInt64
    let bindingID: String?
}

struct V3AsyncRequestOwnerState: Sendable {
    private(set) var generation: UInt64 = 0

    mutating func begin(bindingID: String? = nil) -> V3AsyncRequestOwner {
        generation &+= 1
        return V3AsyncRequestOwner(generation: generation, bindingID: bindingID)
    }

    mutating func invalidate() {
        generation &+= 1
    }

    func owns(_ owner: V3AsyncRequestOwner, bindingID: String? = nil) -> Bool {
        owner.generation == generation && owner.bindingID == bindingID
    }
}

struct V3StatusWriteTicket: Equatable, Sendable {
    let revision: UInt64
    let serviceEpoch: UInt64
    let ownerID: String
    let serviceInstanceID: String
    let kind: V3StatusAuthorityLeaseKind
}

enum V3StatusAuthorityLeaseKind: String, Equatable, Sendable {
    case snapshot
    case mutation
}

struct V3StatusLeaseWaiterOrder: Sendable {
    private var ids: [String] = []

    mutating func enqueue(_ id: String) {
        ids.append(id)
    }

    mutating func remove(_ id: String) {
        ids.removeAll { $0 == id }
    }

    mutating func takeNext() -> String? {
        ids.isEmpty ? nil : ids.removeFirst()
    }

    var count: Int { ids.count }
}

enum V3StatusWriteOutcome: Equatable, Sendable {
    case committed
    case failed
    case notDispatched
    case outcomeUnknown
}

/// One bridge-owned revision and lease authority for status snapshots and writes.
/// Cancellation ACKs never complete a lease; only its original callback or
/// explicit service retirement does.
struct V3StatusWriteAuthority: Sendable {
    private(set) var revision: UInt64 = 0
    private(set) var serviceEpoch: UInt64 = 0
    private(set) var serviceInstanceID: String?
    private(set) var activeLease: V3StatusWriteTicket?
    // Unknown one-shot owners are deliberately not evicted or cleared by a
    // generic snapshot; operation-specific reconciliation must resolve them.
    private(set) var unresolvedOwnerIDs: Set<String> = []

    func canBegin(kind: V3StatusAuthorityLeaseKind) -> Bool {
        activeLease == nil && (kind == .snapshot || !hasUnresolvedMutation)
    }

    /// A mutation reserves a revision before it can suspend for a lease or XPC.
    mutating func reserveMutationRevision() -> UInt64 {
        revision &+= 1
        return revision
    }

    mutating func begin(ownerID: String, revision reservedRevision: UInt64,
                        serviceInstanceID: String,
                        kind: V3StatusAuthorityLeaseKind) -> V3StatusWriteTicket? {
        guard canBegin(kind: kind), reservedRevision <= revision else { return nil }
        let ticket = V3StatusWriteTicket(revision: reservedRevision,
            serviceEpoch: serviceEpoch, ownerID: ownerID,
            serviceInstanceID: serviceInstanceID, kind: kind)
        activeLease = ticket
        return ticket
    }

    @discardableResult
    mutating func complete(_ ticket: V3StatusWriteTicket,
                           outcome: V3StatusWriteOutcome) -> Bool {
        guard activeLease == ticket else { return false }
        activeLease = nil
        switch outcome {
        case .outcomeUnknown:
            if ticket.kind == .mutation { unresolvedOwnerIDs.insert(ticket.ownerID) }
        case .committed, .failed, .notDispatched:
            unresolvedOwnerIDs.remove(ticket.ownerID)
        }
        return true
    }

    /// Observing a replacement process advances the epoch. A matching long
    /// session control may transfer its lease to that process; unrelated work
    /// cannot take ownership from the original request.
    mutating func observeServiceInstance(_ instanceID: String,
                                         continuingOwnerID: String?) -> V3StatusWriteTicket? {
        if serviceInstanceID != instanceID {
            serviceEpoch &+= 1
            serviceInstanceID = instanceID
        }
        guard let activeLease, let continuingOwnerID,
              activeLease.ownerID == continuingOwnerID else {
            return nil
        }
        guard activeLease.serviceEpoch != serviceEpoch ||
              activeLease.serviceInstanceID != instanceID else { return activeLease }
        let rebound = V3StatusWriteTicket(revision: activeLease.revision,
            serviceEpoch: serviceEpoch, ownerID: activeLease.ownerID,
            serviceInstanceID: instanceID, kind: activeLease.kind)
        self.activeLease = rebound
        return rebound
    }

    /// Explicit process retirement is the only no-callback release path.
    mutating func retireService() -> V3StatusWriteTicket? {
        serviceEpoch &+= 1
        serviceInstanceID = nil
        guard let retired = activeLease else { return nil }
        activeLease = nil
        if retired.kind == .mutation { unresolvedOwnerIDs.insert(retired.ownerID) }
        return retired
    }

    func mayApply(_ ticket: V3StatusWriteTicket,
                  currentServiceEpoch: UInt64,
                  currentServiceInstanceID: String) -> Bool {
        activeLease == nil && ticket.revision == revision &&
            ticket.serviceEpoch == currentServiceEpoch &&
            ticket.serviceInstanceID == currentServiceInstanceID &&
            !unresolvedOwnerIDs.contains(ticket.ownerID)
    }

    @discardableResult
    mutating func resolveOwnerAfterReconciliation(_ ownerID: String) -> Bool {
        guard unresolvedOwnerIDs.remove(ownerID) != nil else { return false }
        revision &+= 1
        return true
    }

    var hasActiveWrite: Bool { activeLease?.kind == .mutation }
    var hasActiveLease: Bool { activeLease != nil }
    var hasUnresolvedMutation: Bool { !unresolvedOwnerIDs.isEmpty }
}

enum V3StatusReplyCommitPolicy {
    static func mayApply(_ ticket: V3StatusWriteTicket,
                         authority: V3StatusWriteAuthority,
                         currentServiceEpoch: UInt64,
                         currentServiceInstanceID: String,
                         busySnapshot: Bool = false) -> Bool {
        if ticket.kind == .snapshot && busySnapshot { return false }
        authority.mayApply(ticket, currentServiceEpoch: currentServiceEpoch,
            currentServiceInstanceID: currentServiceInstanceID)
    }
}

enum V3StatusRecoveryEvidencePolicy {
    static func mayApply(busySnapshot: Bool, activeMutation: Bool?,
                         hasDurableRecoveryEvidence: Bool) -> Bool {
        busySnapshot && activeMutation != true && hasDurableRecoveryEvidence
    }

    static func hasLegacyEvidence(_ reply: [String: Any]) -> Bool {
        let operation = reply["operationRecovery"] as? [String: Any]
        let operationEvidence = operation?["session"] as? String
        let refresh = reply["refreshRecovery"] as? [String: Any]
        let refreshRunID = refresh?["runID"] as? String
        let refreshEvidence = refreshRunID.map { UUID(uuidString: $0)?.uuidString == $0 } == true &&
            V3OperationReplyFieldPolicy.strictBoolean(refresh?["ownerLost"]) == true
        let unreadable = V3OperationReplyFieldPolicy.strictBoolean(reply["recoveryJournalUnreadable"]) == true
        return unreadable || refreshEvidence ||
            (operationEvidence.map { UUID(uuidString: $0) != nil } == true &&
             (operation?["kind"] as? String)?.isEmpty == false &&
             V3OperationRecoveryRecord.Phase(rawValue: operation?["phase"] as? String ?? "") != nil)
    }
}

enum V3StatusAuthorityOperationPolicy {
    static func directWriteOwnerID(operation: String, requestID: String) -> String? {
        // `backupResult` and `ipaCleanup` only transfer/retire staged files; they
        // do not change snapshot-owned SideStore state. Recovery journal edits
        // do change fields in the next authoritative snapshot and are fenced.
        let writes: Set<String> = [
            "signOut", "accountImport", "syncAppIDs", "clearCache", "refreshSources", "jit",
            "certSetActive", "certDelete", "certRevoke", "certCreate",
            "sourceAddConfirmed", "sourceRemoveConfirmed", "pairingImportData", "settingsSet",
            "sidesignSet", "sidesignReset", "sidesignImport", "anisetteReset", "anisetteSync",
            "opRecoveryPrepare", "recoveryDiscardUnreadable"
        ]
        return writes.contains(operation) ? "request:\(requestID)" : nil
    }

    static func longOwnerID(operation: String, sessionID: String?) -> String? {
        guard let sessionID, !sessionID.isEmpty else { return nil }
        switch operation {
        case "authBegin", "authRetryProvisioning": return "auth:\(sessionID)"
        case "opStart": return "operation:\(sessionID)"
        case "refreshAdmissionBegin": return "refresh:\(sessionID)"
        default: return nil
        }
    }

    static func controlOwnerID(operation: String, sessionID: String?) -> String? {
        guard let sessionID, !sessionID.isEmpty else { return nil }
        switch operation {
        case "authPoll", "authRespond", "authCancel": return "auth:\(sessionID)"
        case "opPoll", "opAnswer", "opCancel", "opRecoveryReconcile": return "operation:\(sessionID)"
        case "refreshAdmissionEnd", "refreshAdmissionReconcile": return "refresh:\(sessionID)"
        default: return nil
        }
    }

    static func terminalOutcome(operation: String, result: [String: Any]) -> V3StatusWriteOutcome? {
        let payload = result["result"] as? [String: Any] ?? result
        switch operation {
        case "authBegin", "authRetryProvisioning", "authPoll", "authRespond", "authCancel":
            guard let state = payload["state"] as? String,
                  ["completed", "failed", "cancelled", "timedOut", "promptExpired", "resultUnknown"].contains(state) else {
                return nil
            }
            return state == "resultUnknown" ? .outcomeUnknown : .committed
        case "opStart", "opPoll", "opAnswer", "opCancel":
            guard let state = payload["state"] as? String,
                  ["completed", "failed", "cancelled", "requiresSource", "waitingForAuthentication"].contains(state) else {
                return nil
            }
            let settled = V3OperationReplyFieldPolicy.strictBoolean(payload["backendSettled"]) == true ||
                V3OperationReplyFieldPolicy.strictBoolean(payload["stopConfirmed"]) == true
            return settled ? .committed : .outcomeUnknown
        case "refreshAdmissionBegin", "refreshAdmissionEnd", "refreshAdmissionReconcile":
            if operation != "refreshAdmissionBegin" &&
               (V3OperationReplyFieldPolicy.strictBoolean(payload["released"]) == true ||
                V3OperationReplyFieldPolicy.strictBoolean(payload["reconciled"]) == true) { return .committed }
            return nil
        case "opRecoveryReconcile":
            return V3OperationReplyFieldPolicy.strictBoolean(payload["reconciled"]) == true
                ? .committed : nil
        default:
            return .committed
        }
    }
}

import CoreFoundation

/// The lock protects only this small in-memory stamp state. Callers hold no
/// lock while network requests, authentication prompts, or provisioning run.
final class V3AuthIdentityStampState: @unchecked Sendable {
    struct Snapshot: Equatable, Sendable {
        let stamp: String
        let generation: UInt64
        let stable: Bool
    }

    private let lock = NSLock()
    private let processNonce = UUID().uuidString
    private var revision: UInt64 = 0
    private var transitionDepth = 0

    var snapshot: Snapshot {
        lock.lock()
        defer { lock.unlock() }
        return Snapshot(stamp: processNonce + ":\(revision)",
            generation: revision, stable: transitionDepth == 0)
    }

    func beginTransition() {
        lock.lock()
        revision &+= 1
        transitionDepth += 1
        lock.unlock()
    }

    func completeTransition() {
        lock.lock()
        revision &+= 1
        if transitionDepth > 0 { transitionDepth -= 1 }
        lock.unlock()
    }

    func advanceGeneration() {
        lock.lock()
        revision &+= 1
        lock.unlock()
    }

    /// Performs a short in-memory commit only while the captured identity is
    /// still current. The closure must not suspend or perform I/O.
    func runIfCurrent(_ capturedStamp: String, commit: () -> Void) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard transitionDepth == 0, processNonce + ":\(revision)" == capturedStamp else {
            return false
        }
        commit()
        return true
    }
}

// V3_CRASH_REASON_LOG_PRIVACY_V1: exception reasons and call stacks may contain
// credentials, URLs, user data, or local paths. Callers may only log this marker.
enum V3CrashLogPrivacy {
    static func safeCrashMarker(reason: String?) -> String {
        _ = reason
        return "[AppDelegate] UNCAUGHT_NSEXCEPTION_CRASH details=omitted"
    }
}

// Operation phases are fed by PipelineExecutor's actual PipelineStep callback.
// Unknown steps intentionally collapse to Working... rather than inferring a
// stage from progress percentages.
enum V3OperationPhase: String, Equatable, CaseIterable {
    case working
    case preparing
    case preparingIPA
    case downloadingIPA
    case verifying
    case preparingSigning
    case fetchingProvisioningProfile
    case signing
    case preparingInstallation
    case transferringToDevice
    case installing
    case refreshing
    case deleting
    case backingUp
    case restoring
    case updating
    case cleaningUp

    var label: String {
        switch self {
        case .working: return "Working..."
        case .preparing: return "Preparing..."
        case .preparingIPA: return "Preparing IPA..."
        case .downloadingIPA: return "Downloading IPA..."
        case .verifying: return "Verifying..."
        case .preparingSigning: return "Preparing signing..."
        case .fetchingProvisioningProfile: return "Fetching provisioning profile..."
        case .signing: return "Signing..."
        case .preparingInstallation: return "Preparing installation..."
        case .transferringToDevice: return "Transferring to device..."
        case .installing: return "Installing..."
        case .refreshing: return "Refreshing..."
        case .deleting: return "Removing app..."
        case .backingUp: return "Backing up..."
        case .restoring: return "Restoring..."
        case .updating: return "Updating app..."
        case .cleaningUp: return "Cleaning up..."
        }
    }

    static func forPipelineStep(_ step: String, downloadUsesNetwork: Bool = false) -> Self? {
        switch step {
        case "userCustomization", "preflightChecks", "cacheApp": return .preparing
        case "downloadApp": return downloadUsesNetwork ? .downloadingIPA : .preparingIPA
        case "verifyApp", "verifyCertificate": return .verifying
        case "updateAppCertificate": return .preparingSigning
        case "fetchProvisioningProfiles": return .fetchingProvisioningProfile
        case "embedSigningCert", "resignApp", "cacheSigningCert": return .signing
        case "stageApp", "stageBackupApp", "changeAppIcon", "removeAppExtensions",
             "prepareAppExtensionBundleIDs", "createIPA", "exportResignedIPA":
            return .preparingInstallation
        case "sendApp": return .transferringToDevice
        case "installApp": return .installing
        case "refreshApp": return .refreshing
        case "uninstallApp", "removeApp": return .deleting
        case "backupAppData": return .backingUp
        case "restoreAppData": return .restoring
        case "deactivateApp", "markAppInactive": return .updating
        case "removeBackupData", "cleanStagedApp": return .cleaningUp
        default: return nil
        }
    }
}

struct V3OperationPhaseTracker: Equatable {
    private(set) var phase: V3OperationPhase = .working

    mutating func recordPipelineStep(_ step: String, downloadUsesNetwork: Bool = false) {
        phase = V3OperationPhase.forPipelineStep(step, downloadUsesNetwork: downloadUsesNetwork) ?? .working
    }

    mutating func record(_ phase: V3OperationPhase) {
        self.phase = phase
    }
}

enum V3NormalizedProgress {
    static func clamp(_ value: Double) -> Double {
        guard value.isFinite else { return 0 }
        return min(max(value, 0), 1)
    }

    static func displayValue(_ value: Double, state: String) -> Double {
        state == "completed" ? 1 : clamp(value)
    }

    static func percent(_ value: Double, state: String) -> Int {
        Int((displayValue(value, state: state) * 100).rounded())
    }
}

enum V3SourceAddDecision: Equatable { case save, alreadyAdded }

enum V3SourceAddPersistencePolicy {
    static func decision(sourceIsPersisted: Bool) -> V3SourceAddDecision {
        sourceIsPersisted ? .alreadyAdded : .save
    }

    static func verifiedResult(identifier: String, alreadyAdded: Bool,
                                authoritativeCount: Int) -> [String: Any]? {
        guard !identifier.isEmpty, authoritativeCount == 1 else { return nil }
        return ["identifier": identifier,
                "added": !alreadyAdded,
                "alreadyAdded": alreadyAdded,
                "persistenceVerified": true]
    }

    static func confirmationMessage(_ result: [String: Any]) -> String? {
        guard result["persistenceVerified"] as? Bool == true,
              let identifier = result["identifier"] as? String, !identifier.isEmpty,
              let added = result["added"] as? Bool,
              let alreadyAdded = result["alreadyAdded"] as? Bool else { return nil }
        if added && !alreadyAdded { return "Source added." }
        if !added && alreadyAdded { return "Source already added." }
        return nil
    }

    static func validatedURL(_ value: String) -> URL? {
        guard let url = URL(string: value),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil, url.user == nil, url.password == nil else { return nil }
        return url
    }

    static func unverifiedPersistenceFailure(correlationID: String) -> CombinedFailure {
        CombinedFailure(operation: "source", stage: .source, code: .invalidResponse,
            id: correlationID, retryable: false,
            safeCause: .sourcePersistenceUnverified, sourceStep: .catalogRead)
    }
}

enum V3SourceAddFailurePolicy {
    static func normalized(_ failure: CombinedFailure) -> CombinedFailure {
        guard failure.operation == "source", failure.stage == .command,
              failure.safeCause == nil else { return failure }
        let stage: CombinedFailure.Stage = [.notReady, .unavailable].contains(failure.code)
            ? .serviceReadiness : .source
        let cause: CombinedFailure.SafeCause? = failure.code == .busy ? .sourceAddBusy : nil
        let underlying: NSError? = failure.underlyingDomain == "none" && failure.underlyingCode == 0
            ? nil : NSError(domain: failure.underlyingDomain, code: failure.underlyingCode)
        return CombinedFailure(operation: "source", stage: stage, code: failure.code,
            id: failure.correlationID, underlying: underlying,
            retryable: failure.retryable, safeCause: cause)
    }
}

enum V3SourceSubmissionPolicy {
    static func mayResubmit(retryable: Bool?, safeCause: String?,
                            failedInput: String?, currentInput: String) -> Bool {
        guard failedInput == currentInput else { return true }
        if retryable == false {
            return [CombinedFailure.SafeCause.sourceInvalidManifest.rawValue,
                    CombinedFailure.SafeCause.sourcePersistenceUnverified.rawValue].contains(safeCause ?? "")
        }
        return ![CombinedFailure.SafeCause.responseEncodingFailed.rawValue,
                 CombinedFailure.SafeCause.responseTooLarge.rawValue].contains(safeCause ?? "")
    }
}

enum V3JITLessReadiness: String, Equatable {
    case notRequired
    case setupRequired
    case certificateImported
    case needsCertificateRefresh
    case revoked
    case activeCertificateRevoked
    case activeCertificateExpired
    // V3_JITLESS_CERT_DISTINCTION_V1: SideStore's active certificate being
    // absent is a different problem from the LiveContainer copy being stale, and
    // neither means the other's certificate is broken.
    case activeCertificateMissing
    case certificateMismatch
    case ready
    case unknown

    var isReady: Bool { self == .ready || self == .notRequired }

    /// True only for a genuinely finished JIT-Less state. Used so a completed
    /// JIT-Less setup is never rendered as an outstanding setup task.
    var isSatisfied: Bool { isReady }
}

// This policy describes only the LiveContainer copy and safe public identity
// facts. Import/repair remains LiveContainer's canonical settings flow.
enum V3JITLessReadinessPolicy {
    static func evaluate(osMajor: Int, hasCopy: Bool, activeCertificateExists: Bool,
                         activeCertificateStatus: String = "unknown", identitiesMatch: Bool?,
                         validationStatus: Int?, validationFailed: Bool) -> V3JITLessReadiness {
        guard osMajor >= 26 else { return .notRequired }
        if activeCertificateExists && activeCertificateStatus == "revoked" { return .activeCertificateRevoked }
        if activeCertificateExists && activeCertificateStatus == "expired" { return .activeCertificateExpired }
        // Distinct from "the copy is missing": the active SideStore certificate
        // itself is absent, which is a SideStore-side prerequisite.
        guard activeCertificateExists else { return .activeCertificateMissing }
        guard hasCopy else { return .setupRequired }
        guard let validationStatus else { return .certificateImported }
        if validationStatus == 1 {
            if activeCertificateExists, identitiesMatch == true { return .activeCertificateRevoked }
            return .revoked
        }
        guard validationStatus == 0, !validationFailed else { return .unknown }
        guard let identitiesMatch else { return .unknown }
        // The copy is valid but SideStore has since moved to a different
        // certificate. Only the copy is stale; SideStore's certificate is fine.
        return identitiesMatch ? .ready : .certificateMismatch
    }
}

// V3_JITLESS_PRESENTATION_V1
// One place that decides how a JIT-Less state is presented, so the Setup
// Assistant, Health and Settings cannot each invent their own treatment. A ready
// state is a completed result, not an outstanding setup task.
struct V3JITLessPresentation: Equatable {
    let readiness: V3JITLessReadiness
    let severity: V3StatusSeverity
    let title: String
    let detail: String
    /// True when this state still requires the user to do something.
    let isOutstandingSetupTask: Bool

    var icon: String { severity.icon }

    static func present(_ readiness: V3JITLessReadiness) -> V3JITLessPresentation {
        switch readiness {
        case .notRequired:
            return V3JITLessPresentation(readiness: .notRequired, severity: .completed,
                                        title: "Not required",
                                        detail: "This iOS version does not require a JIT-Less certificate.",
                                        isOutstandingSetupTask: false)
        case .ready:
            return V3JITLessPresentation(readiness: .ready, severity: .completed,
                                        title: "Configured / Ready",
                                        detail: "The LiveContainer JIT-Less certificate matches the active SideStore certificate.",
                                        isOutstandingSetupTask: false)
        case .certificateMismatch:
            return V3JITLessPresentation(readiness: .certificateMismatch, severity: .warning,
                                        title: "JIT-Less certificate copy is out of date",
                                        detail: "SideStore is using a different or newer signing certificate than the JIT-Less certificate stored by LiveContainer. Refresh the JIT-Less certificate copy.",
                                        isOutstandingSetupTask: true)
        case .activeCertificateMissing:
            return V3JITLessPresentation(readiness: .activeCertificateMissing, severity: .failed,
                                        title: "No active SideStore certificate",
                                        detail: "SideStore has no active signing certificate. Open Certificates and create or select one before configuring JIT-Less.",
                                        isOutstandingSetupTask: true)
        case .activeCertificateRevoked:
            return V3JITLessPresentation(readiness: .activeCertificateRevoked, severity: .failed,
                                        title: "Active certificate revoked",
                                        detail: "SideStore's active signing certificate is reported as revoked. Open Certificates and select or create a current certificate.",
                                        isOutstandingSetupTask: true)
        case .activeCertificateExpired:
            return V3JITLessPresentation(readiness: .activeCertificateExpired, severity: .failed,
                                        title: "Active certificate expired",
                                        detail: "SideStore's active signing certificate has expired. Open Certificates and select or create a current certificate.",
                                        isOutstandingSetupTask: true)
        case .setupRequired:
            return V3JITLessPresentation(readiness: .setupRequired, severity: .warning,
                                        title: "JIT-Less certificate not configured",
                                        detail: "LiveContainer has no JIT-Less certificate copy yet. Import one to launch guest apps on this iOS version.",
                                        isOutstandingSetupTask: true)
        case .revoked:
            return V3JITLessPresentation(readiness: .revoked, severity: .failed,
                                        title: "JIT-Less certificate copy is revoked",
                                        detail: "The certificate stored by LiveContainer is reported as revoked. Import a current copy.",
                                        isOutstandingSetupTask: true)
        case .certificateImported:
            return V3JITLessPresentation(readiness: .certificateImported, severity: .warning,
                                        title: "Certificate imported, validation pending",
                                        detail: "The certificate is stored but could not be validated yet.",
                                        isOutstandingSetupTask: true)
        case .needsCertificateRefresh:
            return V3JITLessPresentation(readiness: .needsCertificateRefresh, severity: .warning,
                                        title: "JIT-Less certificate needs refreshing",
                                        detail: "Refresh the JIT-Less certificate copy from SideStore.",
                                        isOutstandingSetupTask: true)
        case .unknown:
            return V3JITLessPresentation(readiness: .unknown, severity: .unknown,
                                        title: "Validation unknown",
                                        detail: "The JIT-Less certificate state could not be verified.",
                                        isOutstandingSetupTask: true)
        }
    }
}

enum V3JITLessSetupAction: Equatable {
    case setUp
    case refreshCertificate
    case openCertificates
    case openSetup
    case none
}

enum V3JITLessSetupActionPolicy {
    static func action(for readiness: V3JITLessReadiness) -> V3JITLessSetupAction {
        switch readiness {
        case .setupRequired: return .setUp
        case .needsCertificateRefresh, .certificateMismatch, .revoked:
            return .refreshCertificate
        case .activeCertificateMissing, .activeCertificateRevoked, .activeCertificateExpired:
            return .openCertificates
        case .certificateImported, .unknown: return .openSetup
        case .ready, .notRequired: return .none
        }
    }
}

struct V3SignInJITLessGuidance: Equatable {
    let readiness: V3JITLessReadiness
    let presentation: V3JITLessPresentation
    let action: V3JITLessSetupAction
}

/// Composes the existing authoritative readiness, presentation, and action
/// policies for the post-sign-in page. It observes a fact; it never reads or
/// guesses certificate readiness itself.
enum V3SignInJITLessGuidancePolicy {
    static func resolve(osMajor: Int, readiness: V3JITLessReadiness?) -> V3SignInJITLessGuidance? {
        guard V3JITLessCompletionPolicy.isRequired(osMajor: osMajor) else { return nil }
        // A "not required" fact cannot be trusted on a supported iOS version.
        // Treat it as unverified instead of presenting setup as complete.
        let observed: V3JITLessReadiness
        if let readiness, readiness != .notRequired {
            observed = readiness
        } else {
            observed = .unknown
        }
        return V3SignInJITLessGuidance(readiness: observed,
            presentation: V3JITLessPresentation.present(observed),
            action: V3JITLessSetupActionPolicy.action(for: observed))
    }
}

enum V3JITLessHealthRecoveryPolicy {
    static func shouldOfferCanonicalSetup(for readiness: V3JITLessReadiness,
                                          activeCertificateAvailable: Bool) -> Bool {
        switch readiness {
        case .unknown: return true
        case .certificateImported: return activeCertificateAvailable
        default: return false
        }
    }
}

enum V3TwoFactorStep: String, Equatable {
    case chooseDeliveryMethod
    case choosePhoneNumber
    case deliveryRequested
    case enterVerificationCode
    case verifyingCode
    case completed
    case failed
    case cancelled

    var progressLabel: String? {
        switch self {
        case .choosePhoneNumber: return "Choose a phone number for this verification request..."
        case .deliveryRequested: return "Requesting verification..."
        case .verifyingCode: return "Verifying code..."
        default: return nil
        }
    }

    static func afterDeliveryChoice(_ method: String, phoneCount: Int) -> Self? {
        guard ["trustedDevice", "sms", "voice"].contains(method) else { return nil }
        return method == "sms" || method == "voice" ? (phoneCount > 1 ? .choosePhoneNumber : .deliveryRequested) : .deliveryRequested
    }

    static func afterDelivery(_ method: String) -> Self? {
        ["trustedDevice", "sms", "voice"].contains(method) ? .enterVerificationCode : nil
    }

    static func afterVerification(accepted: Bool) -> Self {
        accepted ? .completed : .enterVerificationCode
    }

    static var afterChangeMethod: Self { .chooseDeliveryMethod }
}

enum V3AuthTerminalPolicy {
    static func resolve(authenticationSucceeded: Bool, authoritativeAccountMatches: Bool,
                        provisioningFailed: Bool, cancelled: Bool) -> String {
        if authenticationSucceeded || authoritativeAccountMatches {
            return provisioningFailed || cancelled ? "authenticatedProvisioningIncomplete" : "completed"
        }
        return cancelled ? "cancelled" : "failed"
    }
}

struct V3AuthPostAuthenticationFailurePresentation: Equatable {
    let stage: CombinedFailure.Stage
    let message: String
}

enum V3AuthPostAuthenticationFailurePolicy {
    static func resolve(cancelled: Bool, savedSessionUnavailable: Bool)
        -> V3AuthPostAuthenticationFailurePresentation {
        let message: String
        if savedSessionUnavailable {
            message = "Signed in successfully, but SideStore could not reuse the saved Apple session to retry provisioning. Sign in again with this Apple ID before retrying setup."
        } else if cancelled {
            message = "Signed in successfully. Provisioning was cancelled before setup finished."
        } else {
            message = "Signed in successfully, but provisioning could not be completed."
        }
        return V3AuthPostAuthenticationFailurePresentation(stage: .provisioning, message: message)
    }
}

enum V3AuthAttemptAuthenticationPolicy {
    private static func normalized(_ value: String?) -> String? {
        guard let value else { return nil }
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return normalized.isEmpty ? nil : normalized
    }

    static func confirms(authenticationCallbackSeen: Bool, submittedAppleID: String?,
                         activeAppleID: String?, accountAppleIDAtStart: String?) -> Bool {
        if authenticationCallbackSeen { return true }
        guard let submitted = normalized(submittedAppleID),
              normalized(activeAppleID) == submitted else { return false }
        return normalized(accountAppleIDAtStart) != submitted
    }
}

enum V3AuthPromptFailurePolicy {
    static func applying(reply: [String: Any], current: [String: Any]?) -> [String: Any]? {
        (reply["previousFailure"] as? [String: Any]) ?? current
    }

    static func isVisible(_ failure: [String: Any]?, promptKind: String?) -> Bool {
        failure != nil && promptKind == "credentials"
    }

    static func clearingAfterSubmission(_ failure: [String: Any]?, promptKind: String?) -> [String: Any]? {
        promptKind == "credentials" ? nil : failure
    }

    static func clearingOnDismiss(_ failure: [String: Any]?) -> [String: Any]? { nil }
}

// A picker selection survives dismissal and any in-flight snapshot reload.
// The picker and operation occupy one host-owned cover, so SwiftUI never has to
// race two unrelated root presentations.
struct V3InstallPresentationRequest: Equatable {
    let attemptID: UUID
    let operationID: UUID
    let token: String
    let title: String
}

// Local IPA, URL, and catalog installs all converge on the same AppOperation
// builder after resolution has produced an AppProtocol value.
enum V3InstallInputRoute: String, Equatable { case localIPA, remoteURL, catalog }

enum V3InstallPipelineParity {
    static func makeOperation<ResolvedApp, Operation>(
        route: V3InstallInputRoute,
        _ resolvedApp: ResolvedApp,
        build: (ResolvedApp) -> Operation
    ) -> (route: V3InstallInputRoute, operation: Operation) {
        (route, build(resolvedApp))
    }
}

// Coordinates a direct root-owned UIKit picker. If the anchor is not in the
// window hierarchy yet, the attempt remains queued until UIKit reports that
// the anchor appeared; it is never converted into a nested SwiftUI sheet.
final class V3InstallPickerPresentationCoordinator {
    enum Phase: String, Equatable { case idle, queued, presenting, presented, dismissing, awaitingDismissal }
    enum Decision: Equatable {
        case present(UUID)
        case queued
        case dismissed(UUID)
        case rejected(UUID, String)
        case none
    }

    private(set) var phase: Phase = .idle
    private(set) var attemptID: UUID?

    func request(attemptID: UUID, presenterReady: Bool,
                 presenterBusy: Bool) -> Decision {
        guard phase == .idle else { return .rejected(attemptID, "presenter_busy") }
        self.attemptID = attemptID
        guard presenterReady else {
            phase = .queued
            return .queued
        }
        guard !presenterBusy else {
            phase = .queued
            return .rejected(attemptID, "presentation_active")
        }
        phase = .presenting
        return .present(attemptID)
    }

    func presenterBecameReady(isBusy: Bool) -> Decision {
        switch phase {
        case .queued:
            guard let attemptID else { return .none }
            guard !isBusy else {
                return .rejected(attemptID, "presentation_active")
            }
            phase = .presenting
            return .present(attemptID)
        case .awaitingDismissal:
            guard !isBusy, let attemptID else { return .none }
            reset()
            return .dismissed(attemptID)
        default:
            return .none
        }
    }

    @discardableResult
    func didPresent(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .presenting else { return false }
        phase = .presented
        return true
    }

    @discardableResult
    func beginDismissal(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .presenting || phase == .presented else { return false }
        phase = .dismissing
        return true
    }

    @discardableResult
    func didDismiss(attemptID id: UUID, presenterIsClear: Bool) -> Bool {
        guard attemptID == id, phase == .dismissing || phase == .presented else { return false }
        guard presenterIsClear else {
            phase = .awaitingDismissal
            return false
        }
        reset()
        return true
    }

    @discardableResult
    func fail(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase != .idle else { return false }
        reset()
        return true
    }

    private func reset() {
        phase = .idle
        attemptID = nil
    }
}

struct V3InstallAttemptState {
    enum Phase: String, Equatable {
        case idle, pickerPresented, staging, waitingForPickerDismissal, waitingForReload
        case readyToPresentOperation, operationPresented, operationStarted, terminal, cleaningUp
    }

    private(set) var phase: Phase = .idle
    private(set) var attemptID: UUID?
    private(set) var operationID: UUID?
    private(set) var token: String?
    private(set) var title: String?
    private(set) var backendSessionID: String?
    private(set) var terminalOutcome: String?
    private(set) var operationViewDidAppear = false

    var isIdle: Bool { phase == .idle }
    var hasActiveAttempt: Bool { !isIdle }

    mutating func beginPicker() -> UUID? {
        guard isIdle else { return nil }
        reset()
        let id = UUID()
        attemptID = id
        phase = .pickerPresented
        return id
    }

    mutating func beginDirectStaging() -> UUID? {
        guard isIdle else { return nil }
        reset()
        let id = UUID()
        attemptID = id
        phase = .staging
        return id
    }

    @discardableResult
    mutating func beginStaging(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .pickerPresented else { return false }
        phase = .staging
        return true
    }

    @discardableResult
    mutating func staged(attemptID id: UUID, token: String, title: String,
                         waitsForPickerDismissal: Bool, isLoading: Bool) -> Bool {
        guard attemptID == id, phase == .staging, UUID(uuidString: token) != nil,
              !title.isEmpty, title.utf8.count <= 160 else { return false }
        self.token = token
        self.title = title
        if waitsForPickerDismissal { phase = .waitingForPickerDismissal }
        else { phase = isLoading ? .waitingForReload : .readyToPresentOperation }
        return true
    }

    @discardableResult
    mutating func failStaging(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .staging else { return false }
        reset()
        return true
    }

    @discardableResult
    mutating func pickerDidDisappear(attemptID id: UUID, isLoading: Bool) -> Bool {
        guard attemptID == id, phase == .waitingForPickerDismissal else { return false }
        phase = isLoading ? .waitingForReload : .readyToPresentOperation
        return true
    }

    @discardableResult
    mutating func cancelPicker(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .pickerPresented || phase == .staging ||
                phase == .waitingForPickerDismissal else { return false }
        reset()
        return true
    }

    // A presentation can be discarded only before a backend session has been
    // issued, or after the caller has separately confirmed a terminal result.
    @discardableResult
    mutating func resetBeforeBackend(attemptID id: UUID) -> Bool {
        guard attemptID == id else { return false }
        switch phase {
        case .pickerPresented, .staging, .waitingForPickerDismissal,
             .waitingForReload, .readyToPresentOperation:
            reset()
            return true
        case .operationPresented where !operationViewDidAppear && backendSessionID == nil:
            reset()
            return true
        default:
            return false
        }
    }

    mutating func reloadFinished() {
        guard phase == .waitingForReload else { return }
        phase = .readyToPresentOperation
    }

    mutating func takeReadyOperation(isLoading: Bool,
                                     hasActiveOperationPresentation: Bool) -> V3InstallPresentationRequest? {
        guard phase == .readyToPresentOperation, !isLoading, !hasActiveOperationPresentation,
              let attemptID, let token, let title else { return nil }
        let operationID = UUID()
        self.operationID = operationID
        operationViewDidAppear = false
        phase = .operationPresented
        return V3InstallPresentationRequest(attemptID: attemptID, operationID: operationID,
                                            token: token, title: title)
    }

    @discardableResult
    mutating func markOperationViewDidAppear(attemptID id: UUID, operationID: UUID) -> Bool {
        guard attemptID == id, self.operationID == operationID,
              phase == .operationPresented || phase == .operationStarted else { return false }
        operationViewDidAppear = true
        return true
    }

    @discardableResult
    mutating func backendStarted(attemptID id: UUID, operationID: UUID, sessionID: String) -> Bool {
        guard attemptID == id, self.operationID == operationID,
              phase == .operationPresented, backendSessionID == sessionID,
              UUID(uuidString: sessionID) != nil else { return false }
        backendSessionID = sessionID
        phase = .operationStarted
        return true
    }

    @discardableResult
    mutating func backendStartRequested(attemptID id: UUID, operationID: UUID,
                                        sessionID: String) -> Bool {
        guard attemptID == id, self.operationID == operationID,
              phase == .operationPresented, UUID(uuidString: sessionID) != nil else { return false }
        backendSessionID = sessionID
        return true
    }

    @discardableResult
    mutating func recordTerminal(attemptID id: UUID, operationID: UUID, outcome: String) -> Bool {
        guard attemptID == id, self.operationID == operationID,
              phase == .operationPresented || phase == .operationStarted else { return false }
        terminalOutcome = outcome
        phase = .terminal
        return true
    }

    @discardableResult
    mutating func prepareRetry(attemptID id: UUID, operationID: UUID) -> Bool {
        guard attemptID == id, self.operationID == operationID, phase == .terminal else { return false }
        backendSessionID = nil
        terminalOutcome = nil
        operationViewDidAppear = true
        phase = .operationPresented
        return true
    }

    @discardableResult
    mutating func beginCleanup(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .terminal else { return false }
        phase = .cleaningUp
        return true
    }

    @discardableResult
    mutating func finishCleanup(attemptID id: UUID) -> Bool {
        guard attemptID == id, phase == .cleaningUp else { return false }
        reset()
        return true
    }

    private mutating func reset() {
        phase = .idle
        attemptID = nil
        operationID = nil
        token = nil
        title = nil
        backendSessionID = nil
        terminalOutcome = nil
        operationViewDidAppear = false
    }
}

// Deletion completion is based on SideStore's pipeline/native uninstall result
// plus its persisted app-library state. Progress and a host-side list update do
// not establish success on their own.
struct V3DeleteCompletionContract {
    enum BackendResult: Equatable { case pending, succeeded, failed }
    enum Terminal: Equatable { case completed, failed, outcomeUnknown }

    private(set) var terminal: Terminal?

    mutating func resolve(backend: BackendResult, nativeUninstallSucceeded: Bool,
                          appStillInAuthoritativeLibrary: Bool, deadlineExpired: Bool,
                          progress: Double) -> Terminal? {
        _ = progress // Progress is deliberately never a success signal.
        guard terminal == nil else { return terminal }
        if backend == .failed {
            terminal = .failed
        } else if !appStillInAuthoritativeLibrary &&
                    (backend == .succeeded ||
                     (backend == .pending && nativeUninstallSucceeded && deadlineExpired)) {
            terminal = .completed
        } else if backend == .pending && deadlineExpired {
            // This is provisional. Keep the contract open so the late callback
            // can still establish the authoritative result.
            return .outcomeUnknown
        } else if deadlineExpired {
            terminal = .failed
        }
        return terminal
    }
}

enum V3DeleteCancellationPolicy {
    static func callbackCancellationRemainsPending(isCancellation: Bool,
                                                   cancellationRequested: Bool) -> Bool {
        isCancellation && cancellationRequested
    }

    static func cancelRequestReturnsBeforeDriverSettlement(operation: String,
                                                            driverIsRunning: Bool) -> Bool {
        operation == "delete" && driverIsRunning
    }

    static func keepsHostPollMonitor(operation: String) -> Bool {
        operation == "delete"
    }
}

enum V3OperationCancellationResolutionPolicy {
    static func requiresReconciliation(backendSettled: Bool?, outcomeUnknown: Bool) -> Bool {
        outcomeUnknown || backendSettled != true
    }
}

enum V3OperationReplyFieldPolicy {
    static func strictBoolean(_ rawValue: Any?) -> Bool? {
        guard let rawValue, let value = rawValue as? NSNumber,
              CFGetTypeID(value) == CFBooleanGetTypeID() else { return nil }
        return value.boolValue
    }

    // Missing is accepted for older service replies. A present malformed value
    // must fail closed because it cannot prove a terminal result is settled.
    static func outcomeUnknown(_ rawValue: Any?) -> Bool {
        guard let rawValue else { return false }
        return strictBoolean(rawValue) ?? true
    }
}

final class V3DeleteNativeSuccessRegistry: @unchecked Sendable {
    static let shared = V3DeleteNativeSuccessRegistry()
    static let retentionInterval: TimeInterval = 10 * 60
    static let maximumEntries = 512
    private let lock = NSLock()
    private var sessions: [String: Date] = [:]

    func record(sessionID: String, now: Date = Date()) {
        guard UUID(uuidString: sessionID) != nil else { return }
        lock.lock()
        pruneLocked(now: now)
        sessions[sessionID] = now.addingTimeInterval(Self.retentionInterval)
        if sessions.count > Self.maximumEntries {
            let oldest = sessions.sorted { $0.value < $1.value }
            for (id, _) in oldest.prefix(sessions.count - Self.maximumEntries) {
                sessions.removeValue(forKey: id)
            }
        }
        lock.unlock()
    }

    func contains(sessionID: String, now: Date = Date()) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        pruneLocked(now: now)
        guard sessions[sessionID] != nil else { return false }
        sessions[sessionID] = now.addingTimeInterval(Self.retentionInterval)
        return true
    }

    func remove(sessionID: String) {
        lock.lock()
        sessions.removeValue(forKey: sessionID)
        lock.unlock()
    }

    private func pruneLocked(now: Date) {
        sessions = sessions.filter { $0.value > now }
    }
}

// Shared state primitives used by the UI/backend and executable regression
// harnesses. These types deliberately carry no paths, credentials, or logs.
struct V3OperationAttemptState {
    private(set) var generation = UUID()
    private(set) var sessionID: String?
    private(set) var isTerminal = false
    private(set) var transitionInFlight = false

    mutating func begin() -> UUID {
        generation = UUID()
        sessionID = generation.uuidString
        isTerminal = false
        return generation
    }

    mutating func attach(sessionID: String) -> UUID? {
        guard let id = UUID(uuidString: sessionID), id.uuidString == sessionID else { return nil }
        generation = id
        self.sessionID = sessionID
        isTerminal = false
        transitionInFlight = false
        return id
    }

    mutating func bind(sessionID: String, generation: UUID) -> Bool {
        guard self.generation == generation, !isTerminal,
              self.sessionID == sessionID else { return false }
        return true
    }

    @discardableResult
    mutating func acceptStartFailure(generation: UUID) -> Bool {
        guard self.generation == generation, !isTerminal else { return false }
        isTerminal = true
        return true
    }

    func matches(generation: UUID, sessionID: String) -> Bool {
        self.generation == generation && self.sessionID == sessionID && !isTerminal
    }

    func owns(generation: UUID, sessionID: String) -> Bool {
        self.generation == generation && self.sessionID == sessionID
    }

    @discardableResult
    mutating func accept(state: String, generation: UUID, sessionID: String) -> Bool {
        guard matches(generation: generation, sessionID: sessionID) else { return false }
        if !["working", "awaitingPrompt", "cancelling", "reconciling"].contains(state) { isTerminal = true }
        return true
    }

    func ownsProvisionalResolution(generation: UUID, sessionID: String,
                                   currentState: String?, currentBackendSettled: Bool?,
                                   currentOutcomeUnknown: Bool, nextState: String?,
                                   nextBackendSettled: Bool?, nextOutcomeUnknown: Bool,
                                   nextOperation: String? = nil,
                                   verifiedDeleteCompletion: Bool = false) -> Bool {
        owns(generation: generation, sessionID: sessionID) && isTerminal &&
            V3OperationProvisionalOutcomePolicy.canResolve(
                currentState: currentState, currentBackendSettled: currentBackendSettled,
                currentOutcomeUnknown: currentOutcomeUnknown, nextState: nextState,
                nextBackendSettled: nextBackendSettled, nextOutcomeUnknown: nextOutcomeUnknown,
                nextOperation: nextOperation, verifiedDeleteCompletion: verifiedDeleteCompletion)
    }

    mutating func supersede() -> String? {
        let previousSession = sessionID
        generation = UUID()
        sessionID = nil
        isTerminal = true
        return previousSession
    }

    mutating func beginTransition() -> Bool {
        guard !transitionInFlight else { return false }
        transitionInFlight = true
        return true
    }

    mutating func endTransition() {
        transitionInFlight = false
    }
}

enum V3OperationCoverDismissalPolicy {
    static func mustConfirmBackendStop(isRunning: Bool, hasSession: Bool,
                                       sessionIsTerminal: Bool,
                                       hasUncertainSession: Bool,
                                       transitionInFlight: Bool) -> Bool {
        hasUncertainSession ||
            (!sessionIsTerminal && (isRunning || hasSession || transitionInFlight))
    }
}

struct V3OperationMutationRegistry {
    enum StartResult: Equatable { case started, cancelledBeforeStart, busy }
    enum CancelResult: Equatable { case active, recordedBeforeStart }

    private(set) var activeID: String?
    private var cancelledBeforeStart: [String: Date] = [:]

    mutating func begin(_ id: String, now: Date = Date()) -> StartResult {
        prune(now: now)
        if cancelledBeforeStart.removeValue(forKey: id) != nil { return .cancelledBeforeStart }
        guard activeID == nil else { return .busy }
        activeID = id
        return .started
    }

    mutating func cancel(_ id: String, now: Date = Date()) -> CancelResult {
        if activeID == id { return .active }
        cancelledBeforeStart[id] = now.addingTimeInterval(600)
        prune(now: now)
        return .recordedBeforeStart
    }

    @discardableResult
    mutating func finish(_ id: String) -> Bool {
        guard activeID == id else { return false }
        activeID = nil
        return true
    }

    private mutating func prune(now: Date) {
        cancelledBeforeStart = cancelledBeforeStart.filter { $0.value > now }
        guard cancelledBeforeStart.count > 256 else { return }
        let oldest = cancelledBeforeStart.sorted { $0.value < $1.value }
        for (id, _) in oldest.prefix(cancelledBeforeStart.count - 256) {
            cancelledBeforeStart.removeValue(forKey: id)
        }
    }
}

// V3_OPERATION_RECOVERY_JOURNAL_V1
// This record is shared by LiveContainer and the embedded SideStore service.
// It intentionally contains identifiers and allow-listed markers only. It has
// no timestamp: process age and elapsed time never prove device settlement.
struct V3OperationRecoveryRecord: Equatable {
    enum Phase: String, Equatable { case prepared, dispatched }
    let sessionID: String
    let kind: String
    let phase: Phase
    let stagedIPAToken: String?

    static let allowedKinds: Set<String> = [
        "install", "installURL", "installSharedIPA", "update", "refreshApp",
        "activate", "deactivate", "remove", "delete", "backup", "restore", "refreshAll"
    ]

    init?(sessionID: String, kind: String, phase: Phase, stagedIPAToken: String? = nil) {
        guard let id = UUID(uuidString: sessionID), id.uuidString == sessionID,
              Self.allowedKinds.contains(kind) else { return nil }
        if let stagedIPAToken {
            guard let token = UUID(uuidString: stagedIPAToken),
                  token.uuidString.lowercased() == stagedIPAToken else { return nil }
        }
        self.sessionID = sessionID
        self.kind = kind
        self.phase = phase
        self.stagedIPAToken = stagedIPAToken
    }

    var propertyListRepresentation: [String: Any] {
        var value: [String: Any] = ["version": 1, "session": sessionID,
            "kind": kind, "phase": phase.rawValue]
        if let stagedIPAToken { value["ipa"] = stagedIPAToken }
        return value
    }

    static func decodePropertyList(_ value: Any) -> V3OperationRecoveryRecord? {
        guard let plist = value as? [String: Any] else { return nil }
        let requiredKeys: Set<String> = ["version", "session", "kind", "phase"]
        let hasIPA = plist.keys.contains("ipa")
        guard let version = plist["version"] as? NSNumber,
              CFGetTypeID(version) != CFBooleanGetTypeID(), version.intValue == 1 else { return nil }
        guard Set(plist.keys) == (hasIPA ? requiredKeys.union(["ipa"]) : requiredKeys),
              let session = plist["session"] as? String,
              let kind = plist["kind"] as? String,
              let phaseRaw = plist["phase"] as? String,
              let phase = Phase(rawValue: phaseRaw) else { return nil }
        let token: String?
        if hasIPA {
            guard let value = plist["ipa"] as? String else { return nil }
            token = value
        } else {
            token = nil
        }
        return V3OperationRecoveryRecord(sessionID: session, kind: kind,
            phase: phase, stagedIPAToken: token)
    }
}

struct V3OperationRecoveryLease: Equatable {
    enum DispatchResult: Equatable { case reserved, alreadyOwned, blocked }
    private(set) var record: V3OperationRecoveryRecord?

    init(record: V3OperationRecoveryRecord? = nil) { self.record = record }

    mutating func reserve(sessionID: String, kind: String, stagedIPAToken: String? = nil) -> DispatchResult {
        guard let requested = V3OperationRecoveryRecord(sessionID: sessionID, kind: kind,
                phase: .prepared, stagedIPAToken: stagedIPAToken) else { return .blocked }
        guard let current = record else { record = requested; return .reserved }
        guard current.sessionID == requested.sessionID, current.kind == requested.kind,
              current.stagedIPAToken == requested.stagedIPAToken,
              current.phase == .prepared else { return .blocked }
        return .alreadyOwned
    }

    mutating func beginDispatch(sessionID: String, kind: String,
                                stagedIPAToken: String? = nil) -> Bool {
        guard let current = record, current.sessionID == sessionID,
              current.kind == kind, current.phase == .prepared,
              current.stagedIPAToken == stagedIPAToken,
              let dispatched = V3OperationRecoveryRecord(sessionID: sessionID, kind: kind,
                  phase: .dispatched, stagedIPAToken: stagedIPAToken) else { return false }
        record = dispatched
        return true
    }

    @discardableResult
    mutating func settle(sessionID: String, replySessionID: String?, state: String?,
                         backendSettled: Bool) -> Bool {
        guard let current = record, current.sessionID == sessionID,
              current.phase == .dispatched, replySessionID == sessionID, backendSettled,
              ["completed", "failed", "cancelled", "timedOut", "requiresSource", "waitingForAuthentication"].contains(state ?? "") else {
            return false
        }
        record = nil
        return true
    }

    @discardableResult
    mutating func reconcileAfterDeviceCheck(sessionID: String, userConfirmed: Bool) -> Bool {
        guard userConfirmed, record?.sessionID == sessionID else { return false }
        record = nil
        return true
    }

    @discardableResult
    mutating func clearPreparedAfterNotDispatched(sessionID: String, expectedRequestID: String,
                                                   replyRequestID: String?, operationNotDispatched: Bool) -> Bool {
        guard operationNotDispatched, replyRequestID == expectedRequestID,
              UUID(uuidString: expectedRequestID)?.uuidString == expectedRequestID,
              let current = record, current.sessionID == sessionID, current.phase == .prepared else { return false }
        record = nil
        return true
    }

    @discardableResult
    mutating func clearPreparedAfterConfirmedCancellation(sessionID: String, replySessionID: String?,
        state: String?, backendSettled: Bool, stopConfirmed: Bool, knownStarted: Bool) -> Bool {
        guard !knownStarted, let current = record,
              current.sessionID == sessionID, current.phase == .prepared,
              replySessionID == sessionID, state == "cancelled",
              backendSettled, stopConfirmed else { return false }
        record = nil
        return true
    }

    @discardableResult
    mutating func settleRefreshAdmission(runID: String, terminalState: String?,
                                         terminalConfirmed: Bool) -> Bool {
        guard terminalConfirmed, let current = record,
              current.sessionID == runID, current.kind == "refreshAll",
              (current.phase == .prepared ||
                (current.phase == .dispatched && terminalState != "notDispatched")),
              ["completed", "failed", "notDispatched"].contains(terminalState ?? "") else { return false }
        record = nil
        return true
    }

    @discardableResult
    mutating func clearPreparedRefreshAdmissionAfterRequestCancellation(runID: String) -> Bool {
        guard let current = record, current.sessionID == runID,
              current.kind == "refreshAll", current.phase == .prepared else { return false }
        record = nil
        return true
    }

    @discardableResult
    mutating func reconcileRefreshAdmissionAfterDeviceCheck(runID: String, userConfirmed: Bool) -> Bool {
        guard record?.kind == "refreshAll" else { return false }
        return reconcileAfterDeviceCheck(sessionID: runID, userConfirmed: userConfirmed)
    }

    var blocksMutation: Bool { record != nil }
    var protectedStagedIPAToken: String? { record?.stagedIPAToken }
}

// A staged IPA remains owned while any session task can still inspect it,
// preparation has not settled, or the global mutation registry still assigns
// the native mutation to that session. Age alone never releases a live lease.
enum V3StagedIPALeasePolicy {
    static func isLeased(hasOperationTask: Bool, preparationFinished: Bool,
                         ownsMutationRegistry: Bool) -> Bool {
        hasOperationTask || !preparationFinished || ownsMutationRegistry
    }
}

enum V3StagedIPACleanupFallbackPolicy {
    static func mayDeleteLocally(serviceReportsBusy: Bool,
                                 callerConfirmsNeverStartedOrSettled: Bool) -> Bool {
        callerConfirmsNeverStartedOrSettled && !serviceReportsBusy
    }
}

// Terminal responses are write-once. Callback and cancellation paths may race,
// so the first terminal result is authoritative and later results are ignored.
final class V3TerminalResponse: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String: Any]?

    @discardableResult
    func setIfEmpty(_ response: [String: Any]) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard storage == nil else { return false }
        storage = response
        return true
    }

    var value: [String: Any]? {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    var isEmpty: Bool { if case nil = value { return true }; return false }
}

enum V3AuthSessionResponsePolicy {
    static func mayRespond(terminalIsEmpty: Bool, cancellationRequested: Bool,
                           promptMatches: Bool) -> Bool {
        terminalIsEmpty && !cancellationRequested && promptMatches
    }

    static func mayApplyReply(currentSessionID: String?, replySessionID: String,
                              cancellationInProgress: Bool,
                              submittedPromptID: String? = nil,
                              currentPromptID: String? = nil,
                              currentRevision: Int? = nil,
                              replyRevision: Int? = nil) -> Bool {
        guard !cancellationInProgress, currentSessionID == replySessionID else { return false }
        if let currentRevision {
            guard let replyRevision, replyRevision >= currentRevision else { return false }
        }
        guard let submittedPromptID else { return true }
        return currentPromptID == submittedPromptID
    }

    static func mayAcceptStartedSession(expectedSessionID: String, replySessionID: String?,
                                        currentSessionID: String?, cancellationInProgress: Bool) -> Bool {
        !cancellationInProgress && replySessionID == expectedSessionID &&
            currentSessionID == expectedSessionID
    }

    static func mayLaunchCreatedSession(sessionID: String, activeSessionID: String?,
                                        cancellationRequested: Bool, terminalIsEmpty: Bool,
                                        requestCancelled: Bool = false) -> Bool {
        activeSessionID == sessionID && !cancellationRequested && !requestCancelled && terminalIsEmpty
    }
}

enum V3AuthPollResponsePolicy {
    static func mayApply(currentSessionID: String?, replySessionID: String,
                         cancellationInProgress: Bool, currentRevision: Int,
                         replyRevision: Int?, currentPromptID: String?,
                         replyPromptID: String?) -> Bool {
        guard !cancellationInProgress, currentSessionID == replySessionID,
              let replyRevision, replyRevision >= currentRevision else { return false }
        if replyRevision == currentRevision, let currentPromptID,
           replyPromptID != currentPromptID { return false }
        return true
    }
}

enum V3AuthPromptSubmissionPolicy {
    static func mayShowFailure(currentSessionID: String?, submittedSessionID: String,
                               currentPromptID: String?, submittedPromptID: String,
                               cancellationInProgress: Bool) -> Bool {
        !cancellationInProgress && currentSessionID == submittedSessionID &&
            currentPromptID == submittedPromptID
    }
}

enum V3AuthPromptResponsePolicy {
    static func maySubmit(state: String, currentPromptID: String?, submittedPromptID: String,
                          isSubmitting: Bool, cancellationInProgress: Bool) -> Bool {
        state == "awaitingPrompt" && currentPromptID == submittedPromptID &&
            !isSubmitting && !cancellationInProgress
    }

    static func shouldClearSubmissionFailure(oldPromptID: String?, newPromptID: String?,
                                             state: String) -> Bool {
        state == "awaitingPrompt" && oldPromptID != newPromptID
    }

    static func failureMessage(_ error: Error) -> String {
        if let failure = error as? CombinedFailure {
            return "\(failure.safeMessage) \(failure.recovery)"
        }
        return "The verification response could not be confirmed. The exact underlying cause could not be safely identified. Check the sign-in status before trying again."
    }

    static func diagnostics(_ error: Error) -> String {
        if let failure = error as? CombinedFailure { return failure.technicalDetails }
        return "schema=1 operation=authRespond stage=command code=failed correlation=unavailable underlying_domain=redacted underlying_code=redacted retryable=unknown"
    }

    static func blocksResubmission(_ error: Error) -> Bool {
        (error as? CombinedFailure)?.retryable == false
    }
}

enum V3TwoFactorRetryPolicy {
    static func shouldReuseCredentialsForCodeRetry(authFailureKind: String?) -> Bool {
        authFailureKind == "invalidCode"
    }

    static func recoveryMessage(authFailureKind: String?) -> String? {
        guard shouldReuseCredentialsForCodeRetry(authFailureKind: authFailureKind) else { return nil }
        return "The verification code was not accepted. Enter a new code and try again."
    }
}

struct V3AuthStartCancellationRegistry {
    private var cancelled: [String: Date] = [:]

    mutating func cancelBeforeStart(_ id: String, now: Date = Date()) -> Bool {
        guard let parsed = UUID(uuidString: id), parsed.uuidString == id else { return false }
        prune(now: now)
        cancelled[id] = now.addingTimeInterval(600)
        prune(now: now)
        return true
    }

    mutating func consume(_ id: String, now: Date = Date()) -> Bool {
        prune(now: now)
        return cancelled.removeValue(forKey: id) != nil
    }

    func contains(_ id: String, now: Date = Date()) -> Bool {
        guard let expiry = cancelled[id] else { return false }
        return expiry > now
    }

    mutating func prune(now: Date = Date()) {
        cancelled = cancelled.filter { $0.value > now }
        guard cancelled.count > 256 else { return }
        let oldest = cancelled.sorted { $0.value < $1.value }
        for (id, _) in oldest.prefix(cancelled.count - 256) { cancelled.removeValue(forKey: id) }
    }
}

enum V3DeleteReconciliationPolicy {
    static let callbackGrace: TimeInterval = 5
    static let libraryRecheckInterval: TimeInterval = 15
    static let maximumCallbackPollInterval: TimeInterval = 15

    static func shouldCheckLibrary(lastCheck: Date?, now: Date) -> Bool {
        guard let lastCheck else { return true }
        return now.timeIntervalSince(lastCheck) >= libraryRecheckInterval
    }

    static func shouldThrottleLibraryChecks(authoritativeAbsenceConfirmed: Bool,
                                            cancellationRequested: Bool) -> Bool {
        authoritativeAbsenceConfirmed || cancellationRequested
    }

    static func nextCallbackPollDelay(current: TimeInterval, backendPending: Bool,
                                      nativeUninstallSucceeded: Bool,
                                      appStillInLibrary: Bool,
                                      cancellationRequested: Bool = false) -> TimeInterval {
        if !backendPending {
            guard appStillInLibrary else { return 1.0 }
            let settledBase = current.isFinite && current > 0 ? current : 0.5
            return min(max(settledBase * 2, 1.0), maximumCallbackPollInterval)
        }
        guard cancellationRequested || (nativeUninstallSucceeded && !appStillInLibrary) else { return 1.0 }
        let base = current.isFinite && current > 0 ? current : 0.25
        return min(base * 2, maximumCallbackPollInterval)
    }

    static func shouldRequestCancellation(deadlineElapsed: Bool, backendPending: Bool,
                                         cancellationAlreadyRequested: Bool) -> Bool {
        deadlineElapsed && backendPending && !cancellationAlreadyRequested
    }

    static func callbackGraceElapsed(requestedAt: Date?, now: Date) -> Bool {
        guard let requestedAt else { return false }
        return now.timeIntervalSince(requestedAt) >= callbackGrace
    }

    static func shouldPublishOutcomeUnknown(backendPending: Bool, requestedAt: Date?,
                                           now: Date) -> Bool {
        backendPending && callbackGraceElapsed(requestedAt: requestedAt, now: now)
    }

    static func mayPublishVerifiedDeleteCompletion(backendPending: Bool,
                                                    nativeUninstallSucceeded: Bool,
                                                    appStillInLibrary: Bool,
                                                    reconciliationDeadlineElapsed: Bool) -> Bool {
        backendPending && nativeUninstallSucceeded && !appStillInLibrary &&
            reconciliationDeadlineElapsed
    }

    static func shouldReleaseMutationOwnership(backendSettled: Bool) -> Bool {
        backendSettled
    }
}

enum V3OperationSessionRetentionPolicy {
    static let terminalRetention: TimeInterval = 600

    static func shouldRefreshTerminalAt(terminalAccepted: Bool, backendSettled: Bool) -> Bool {
        terminalAccepted || backendSettled
    }

    static func isExpired(backendSettled: Bool, terminalAt: Date?, now: Date) -> Bool {
        guard backendSettled, let terminalAt else { return false }
        return now.timeIntervalSince(terminalAt) > terminalRetention
    }
}

enum V3OperationCompletionDisposition: Equatable {
    case notCompleted
    case completed
    case completedAwaitingBackendSettlement
    case outcomeUnknownAwaitingBackendSettlement
}

enum V3OperationCompletionPolicy {
    static func disposition(state: String, backendSettled: Bool?,
                            outcomeUnknown: Bool = false) -> V3OperationCompletionDisposition {
        if outcomeUnknown {
            return .outcomeUnknownAwaitingBackendSettlement
        }
        guard state == "completed" else { return .notCompleted }
        return backendSettled == true ? .completed : .completedAwaitingBackendSettlement
    }

    static func shouldContinuePolling(state: String, backendSettled: Bool?,
                                      outcomeUnknown: Bool = false) -> Bool {
        switch disposition(state: state, backendSettled: backendSettled,
                           outcomeUnknown: outcomeUnknown) {
        case .completedAwaitingBackendSettlement, .outcomeUnknownAwaitingBackendSettlement: return true
        case .notCompleted, .completed: return false
        }
    }

    static func shouldRetrySettlementPollFailure(state: String, backendSettled: Bool?,
                                                  outcomeUnknown: Bool,
                                                  cancellationRequested: Bool = false) -> Bool {
        shouldContinuePolling(state: state, backendSettled: backendSettled,
                              outcomeUnknown: outcomeUnknown) ||
            (state == "cancelling" && cancellationRequested)
    }

    static func requiresDeviceCheck(state: String, backendSettled: Bool?,
                                    deviceCheckConfirmed: Bool,
                                    outcomeUnknown: Bool = false) -> Bool {
        shouldContinuePolling(state: state, backendSettled: backendSettled,
                              outcomeUnknown: outcomeUnknown) && !deviceCheckConfirmed
    }

    static func mayDismiss(state: String, backendSettled: Bool?,
                           deviceCheckConfirmed: Bool = false,
                           outcomeUnknown: Bool = false) -> Bool {
        !requiresDeviceCheck(state: state, backendSettled: backendSettled,
                             deviceCheckConfirmed: deviceCheckConfirmed,
                             outcomeUnknown: outcomeUnknown)
    }

    static func pollInterval(state: String, backendSettled: Bool?,
                             outcomeUnknown: Bool = false) -> TimeInterval {
        shouldContinuePolling(state: state, backendSettled: backendSettled,
                              outcomeUnknown: outcomeUnknown) ? 5 : 1
    }

    static func nextSettlementPollRetryDelay(current: TimeInterval) -> TimeInterval {
        let base = current.isFinite && current > 0 ? current : 5
        if base < 5 { return 5 }
        return min(base * 2, 30)
    }
}

enum V3OperationProvisionalOutcomePolicy {
    static func canResolve(currentState: String?, currentBackendSettled: Bool?,
                           currentOutcomeUnknown: Bool, nextState: String?,
                           nextBackendSettled: Bool?, nextOutcomeUnknown: Bool,
                           nextOperation: String? = nil,
                           verifiedDeleteCompletion: Bool = false) -> Bool {
        let priorResultIsProvisional =
            ["reconciling", "failed", "cancelled"].contains(currentState ?? "") &&
            currentOutcomeUnknown && currentBackendSettled == false
        guard priorResultIsProvisional, !nextOutcomeUnknown else { return false }
        let settledTerminal = nextBackendSettled == true &&
            ["completed", "failed", "cancelled"].contains(nextState ?? "")
        let verifiedDeleteWhileCallbackPending = nextOperation == "delete" &&
            verifiedDeleteCompletion && nextState == "completed" && nextBackendSettled == false
        return settledTerminal || verifiedDeleteWhileCallbackPending
    }
}

// Cancellation is a request to stop. It is never itself a terminal result:
// the backend driver commits completed/failed/cancelled only after its native
// callback and required verification have settled.
final class V3OperationTerminalResponse: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String: Any]?
    private var cancellationRequested = false

    @discardableResult
    func requestCancellation() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard storage == nil else { return false }
        cancellationRequested = true
        return true
    }

    var isCancellationRequested: Bool {
        lock.lock()
        defer { lock.unlock() }
        return cancellationRequested
    }

    @discardableResult
    func setIfEmpty(_ response: [String: Any]) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard storage == nil else { return false }
        storage = response
        return true
    }

    // A reconciling record is provisional, not a terminal result. It can be
    // replaced once the backend callback settles, but ordinary terminal
    // responses remain write-once.
    @discardableResult
    func resolveProvisionalOutcome(_ response: [String: Any]) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard let current = storage,
              V3OperationProvisionalOutcomePolicy.canResolve(
                currentState: current["state"] as? String,
                currentBackendSettled: current["backendSettled"] as? Bool,
                currentOutcomeUnknown: current["outcomeUnknown"] as? Bool == true,
                nextState: response["state"] as? String,
                nextBackendSettled: response["backendSettled"] as? Bool,
                nextOutcomeUnknown: response["outcomeUnknown"] as? Bool == true,
                nextOperation: response["operation"] as? String,
                verifiedDeleteCompletion: V3OperationReplyFieldPolicy.strictBoolean(
                    response["verifiedDeleteCompletion"]) == true) else { return false }
        storage = response
        return true
    }

    @discardableResult
    func finishOrResolve(_ response: [String: Any], backendSettled: Bool) -> Bool {
        var resolved = response
        resolved["backendSettled"] = backendSettled
        return setIfEmpty(resolved) || resolveProvisionalOutcome(resolved)
    }

    var value: [String: Any]? {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    var isEmpty: Bool { value == nil }

    func reply(sessionID: String, backendSettled: Bool) -> [String: Any]? {
        guard var response = value else { return nil }
        response["session"] = sessionID
        response["backendSettled"] = backendSettled
        return response
    }
}

// Owns pre-driver work such as resolving or downloading a URL IPA. The session
// is not stopped until this gate finishes; cancellation is forwarded to the
// concrete preparation task and callers can await its settlement.
final class V3OperationPreparationGate: @unchecked Sendable {
    private let lock = NSLock()
    private var finished = false
    private var cancellationRequested = false
    private var cancellationAction: (() -> Void)?
    private var waiters: [CheckedContinuation<Void, Never>] = []

    var isFinished: Bool {
        lock.lock()
        defer { lock.unlock() }
        return finished
    }

    var isCancellationRequested: Bool {
        lock.lock()
        defer { lock.unlock() }
        return cancellationRequested
    }

    var pendingWaiterCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return waiters.count
    }

    func installCancellation(_ action: @escaping () -> Void) {
        lock.lock()
        if finished {
            lock.unlock()
            return
        }
        cancellationAction = action
        let shouldCancel = cancellationRequested
        lock.unlock()
        if shouldCancel { action() }
    }

    @discardableResult
    func requestCancellation() -> Bool {
        lock.lock()
        guard !finished else {
            lock.unlock()
            return false
        }
        let firstRequest = !cancellationRequested
        cancellationRequested = true
        let action = firstRequest ? cancellationAction : nil
        lock.unlock()
        action?()
        return true
    }

    func finish() {
        lock.lock()
        guard !finished else {
            lock.unlock()
            return
        }
        finished = true
        cancellationAction = nil
        let pending = waiters
        waiters.removeAll()
        lock.unlock()
        for continuation in pending { continuation.resume() }
    }

    func wait() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            lock.lock()
            if finished {
                lock.unlock()
                continuation.resume()
            } else {
                waiters.append(continuation)
                lock.unlock()
            }
        }
    }
}

struct V3SettingsWriteGeneration {
    private var values: [String: UInt64] = [:]

    mutating func begin(_ key: String) -> UInt64 {
        let next = (values[key] ?? 0) &+ 1
        values[key] = next
        return next
    }

    func isCurrent(_ generation: UInt64, for key: String) -> Bool {
        values[key] == generation
    }

    func current(for key: String) -> UInt64 {
        values[key] ?? 0
    }
}

enum V3RefreshResultVerifier {
    static func verified<Value>(expectedBundleID: String,
                                results: [String: Result<Value, Error>],
                                bundleIdentifier: (Value) -> String) throws -> Value {
        guard let result = results[expectedBundleID] else { throw CombinedRefreshVerificationError.missingResult }
        switch result {
        case .failure(let error): throw error
        case .success(let value):
            guard bundleIdentifier(value) == expectedBundleID else { throw CombinedRefreshVerificationError.staleResult }
            return value
        }
    }
}

// The install pipeline may call the handler even when there is nothing to
// remove. Keep this branch executable so a zero-item prompt cannot regress.
enum V3ExtensionRemovalPromptPolicy {
    static func decide<Element: Hashable, Decision>(
        excessExtensions: Set<Element>,
        whenEmpty: Decision,
        prompt: () async throws -> Decision
    ) async rethrows -> Decision {
        guard !excessExtensions.isEmpty else { return whenEmpty }
        return try await prompt()
    }
}

enum V3RefreshAllPhase: String {
    case idle, starting, refreshing, verifying, completed, failed
}

enum V3RefreshAllButtonPresentationPolicy {
    static func title(phase: V3RefreshAllPhase, activeRunID: String) -> String {
        switch phase {
        case .starting: return "Starting Refresh..."
        case .refreshing: return "Refreshing..."
        case .verifying: return "Verifying..."
        case .idle where !activeRunID.isEmpty: return "Refresh Already Running"
        default: return "Refresh All"
        }
    }

    static func explainsConcurrentRun(phase: V3RefreshAllPhase, activeRunID: String) -> Bool {
        phase == .idle && !activeRunID.isEmpty
    }
}

enum V3RefreshAllTerminalEvidencePolicy {
    private static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID(),
              ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(String(cString: number.objCType)) else {
            return nil
        }
        return number.intValue
    }

    static func verifiedSummary(_ summary: [String: Any]?, record: [String: Any],
                                runID: String) -> Bool {
        guard let summary,
              integer(summary["version"]) == 2,
              summary["schema"] as? String == "LiveContainerRefreshManifestSummaryV2",
              summary["run_id"] as? String == runID,
              let verified = summary["verified"] as? NSNumber,
              CFGetTypeID(verified) == CFBooleanGetTypeID(), verified.boolValue,
              let expectedCount = integer(summary["expected_count"]),
              expectedCount > 0, expectedCount <= 1024,
              integer(summary["result_count"]) == expectedCount,
              integer(summary["failed_count"]) == 0,
              let skippedCount = integer(summary["skipped_count"]),
              skippedCount >= 0, skippedCount <= 1024,
              let requestedCount = integer(summary["requested_count"]),
              requestedCount <= 1024,
              expectedCount + skippedCount == requestedCount,
              record["run_id"] as? String == runID,
              record["state"] as? String == "completed",
              record["terminal_intent"] as? String == "verified",
              record["health"] as? String == "REFRESH_SUCCEEDED",
              record["manifest_run_id"] as? String == runID else { return false }
        return true
    }

    static func count(_ key: String, in summary: [String: Any]?) -> Int? {
        guard let summary else { return nil }
        return integer(summary[key])
    }
}

enum V3SetupRefreshTerminalOutcome: Equatable {
    case pending
    case failed
    case completedUnverified
    case verified
}

enum V3SetupRefreshTerminalEvidencePolicy {
    static func outcome(state: String, hasVerifiedManifest: Bool,
                        hasVerifiedSummary: Bool) -> V3SetupRefreshTerminalOutcome {
        switch state {
        case "failed": return .failed
        case "completed":
            return hasVerifiedManifest || hasVerifiedSummary ? .verified : .completedUnverified
        default: return .pending
        }
    }
}

// Request identity, rather than process-local notifications or global health,
// owns the Home refresh UI. A terminal record is absorbing for this attempt.
struct V3RefreshAllAttemptState {
    private(set) var requestID = ""
    private(set) var runID = ""
    private(set) var phase: V3RefreshAllPhase = .idle
    private(set) var terminalMessage = ""

    var isTerminal: Bool { phase == .completed || phase == .failed }

    mutating func begin(requestID: String) {
        self.requestID = requestID
        runID = ""
        phase = .starting
        terminalMessage = ""
    }

    @discardableResult
    mutating func observe(_ record: [String: Any], schedulerHealth: String? = nil,
                          activeRunID: String? = nil) -> Bool {
        guard !isTerminal,
              record["request_id"] as? String == requestID,
              let observedRunID = record["run_id"] as? String,
              UUID(uuidString: observedRunID) != nil else { return false }
        _ = schedulerHealth
        _ = activeRunID
        if runID.isEmpty { runID = observedRunID }
        guard runID == observedRunID else { return false }

        switch record["state"] as? String {
        case "running":
            if phase == .starting { phase = .refreshing }
        case "verifying":
            phase = .verifying
        case "completed":
            // Health and activeRun defaults may be observed out of order. The
            // correlated terminal record is authoritative, including when a
            // stale activeRun value is still visible to this view.
            let manifest = record["manifest"] as? [String: Any]
            let hasVerifiedManifest = Self.manifestIsVerified(manifest, runID: runID)
            let hasVerifiedSummary = V3RefreshAllTerminalEvidencePolicy.verifiedSummary(
                record["manifest_summary"] as? [String: Any], record: record, runID: runID)
            guard hasVerifiedManifest || hasVerifiedSummary else {
                phase = .failed
                terminalMessage = "Refresh reported completion without a matching verified manifest."
                return true
            }
            phase = .completed
            let skippedCount = (manifest?["skipped_ids"] as? [String])?.count ??
                V3RefreshAllTerminalEvidencePolicy.count("skipped_count",
                    in: record["manifest_summary"] as? [String: Any]) ?? 0
            terminalMessage = skippedCount == 0
                ? "Refresh completed. All requested app results were verified."
                : "Refresh completed. Results for this run were verified; \(skippedCount) running app(s) were skipped."
        case "failed":
            phase = .failed
            guard let failureWire = record["failure"] as? [String: Any],
                  let failure = CombinedFailure.decode(failureWire, expectedID: runID),
                  failure.operation == "refresh" else {
                terminalMessage = "Refresh failed, but no matching safe cause was available."
                return true
            }
            terminalMessage = failure.safeMessage
        default:
            return false
        }
        return true
    }

    mutating func markDidNotStart() {
        guard !isTerminal else { return }
        phase = .failed
        terminalMessage = "Refresh did not start."
    }

    mutating func markTimedOut() {
        guard !isTerminal else { return }
        phase = .failed
        terminalMessage = "Refresh did not reach a verified terminal result."
    }

    mutating func failBeforeStart(message: String) {
        guard !isTerminal else { return }
        phase = .failed
        terminalMessage = message
    }

    mutating func acknowledge() {
        requestID = ""
        runID = ""
        phase = .idle
        terminalMessage = ""
    }

    static func record(in ledger: [String: Any], requestID: String,
                       runID: String? = nil) -> [String: Any]? {
        let records = ledger.values.compactMap { $0 as? [String: Any] }
        return records.first { record in
            guard record["request_id"] as? String == requestID,
                  let recordRunID = record["run_id"] as? String,
                  UUID(uuidString: recordRunID) != nil else { return false }
            return runID == nil || recordRunID == runID
        }
    }

    private static func manifestIsVerified(_ manifest: [String: Any]?, runID: String) -> Bool {
        guard let manifest, CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID),
              let results = manifest["results"] as? [[String: Any]] else { return false }
        return results.allSatisfy { $0["success"] as? Bool == true }
    }
}

enum V3RefreshAllFailureDiagnostics {
    static func withoutRunRecord(requestID: String, runID: String?, message: String,
                                 health: String) -> String? {
        guard let request = UUID(uuidString: requestID), request.uuidString == requestID else { return nil }
        let safeRunID: String
        if let runID, let parsed = UUID(uuidString: runID), parsed.uuidString == runID {
            safeRunID = runID
        } else {
            safeRunID = "not_started"
        }
        let safeCorrelation = safeRunID == "not_started" ? requestID : safeRunID
        func safeLine(_ value: String) -> String {
            String(value.filter { $0.isASCII && $0 != "\n" && $0 != "\r" }.prefix(512))
        }
        return [
            "schema=1", "request_id=\(requestID)", "manual_refresh_request=\(requestID)",
            "run_id=\(safeRunID)", "state=failed", "operation=refresh", "stage=unknown",
            "code=unknown", "correlation=\(safeCorrelation)",
            "underlying_domain=redacted", "underlying_code=unknown", "retryable=unknown",
            "safe_cause=unknown", "source_step=unknown", "health=\(safeLine(health))",
            "safe_message=\(safeLine(message))"
        ].joined(separator: "\n")
    }

    static func text(requestID: String, runID: String,
                     record: [String: Any]) -> String? {
        guard UUID(uuidString: requestID) != nil, UUID(uuidString: runID) != nil,
              record["request_id"] as? String == requestID,
              record["run_id"] as? String == runID,
              record["state"] as? String == "failed" else { return nil }
        let failureWire = record["failure"] as? [String: Any]
        let failure = failureWire.flatMap { CombinedFailure.decode($0, expectedID: runID) }
        let manifest = record["manifest"] as? [String: Any]
            ?? record["manifest_summary"] as? [String: Any] ?? [:]
        func safeIDs(_ key: String) -> String {
            guard let values = manifest[key] as? [String] else { return "unknown" }
            let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
            return values.prefix(64).map { value in
                String(value.filter { character in
                    character.unicodeScalars.allSatisfy { allowed.contains($0) }
                }.prefix(160))
            }.joined(separator: ",")
        }
        func recordScalar(_ key: String) -> String {
            let value = (record[key] as? String ?? "unknown")
            return String(value.filter { $0.isASCII && $0 != "\n" && $0 != "\r" }.prefix(80))
        }
        if failure?.operation != "refresh" {
            return [
                "schema=1", "request_id=\(requestID)", "manual_refresh_request=\(requestID)", "run_id=\(runID)",
                "state=failed", "operation=refresh", "stage=unknown",
                "code=staleResult", "correlation=\(runID)",
                "underlying_domain=redacted", "underlying_code=unknown",
                "retryable=unknown", "safe_cause=unknown", "source_step=unknown",
                "source=\(recordScalar("source"))", "origin=\(recordScalar("origin"))",
                "network_preflight=\(recordScalar("network_preflight"))",
                "active_run_id=\(recordScalar("active_run_id"))", "health=\(recordScalar("health"))",
                "terminal_ledger_state=failed", "manifest_run_id=\(recordScalar("manifest_run_id"))",
                "target_app_ids=\(safeIDs("requested_ids"))",
                "requested_app_ids=\(safeIDs("requested_ids"))",
                "attempted_app_ids=\(safeIDs("expected_ids"))",
                "skipped_app_ids=\(safeIDs("skipped_ids"))",
                "safe_message=Refresh failed, but no matching safe cause was available."
            ].joined(separator: "\n")
        }
        guard let failure else { return nil }
        let retryable = failure.retryable.map { $0 ? "true" : "false" } ?? "unknown"
        let underlying = CombinedFailure.safeDiagnosticUnderlying(domain: failure.underlyingDomain,
                                                                    code: failure.underlyingCode)
        return [
            "schema=1",
            "request_id=\(requestID)",
            "manual_refresh_request=\(requestID)",
            "run_id=\(runID)",
            "state=failed",
            "source=\(recordScalar("source"))",
            "origin=\(recordScalar("origin"))",
            "network_preflight=\(recordScalar("network_preflight"))",
            "active_run_id=\(recordScalar("active_run_id"))",
            "health=\(recordScalar("health"))",
            "terminal_ledger_state=failed",
            "manifest_run_id=\(recordScalar("manifest_run_id"))",
            "target_app_ids=\(safeIDs("requested_ids"))",
            "requested_app_ids=\(safeIDs("requested_ids"))",
            "attempted_app_ids=\(safeIDs("expected_ids"))",
            "skipped_app_ids=\(safeIDs("skipped_ids"))",
            "operation=\(failure.operation)",
            "stage=\(failure.stage.rawValue)",
            "code=\(failure.code.rawValue)",
            "correlation=\(failure.correlationID)",
            "underlying_domain=\(underlying.domain)",
            "underlying_code=\(underlying.code)",
            "retryable=\(retryable)",
            "safe_cause=\(failure.safeCause?.rawValue ?? "unknown")",
            "source_step=\(failure.sourceStep?.rawValue ?? "unknown")",
            "safe_message=\(failure.safeMessage.replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "\r", with: " "))"
        ].joined(separator: "\n")
    }
}

// V3_STATUS_PRESENTATION_V1
// One reusable semantic status model. Success, warning and failure were drawn
// with almost the same treatment in the operation sheet, Sources, Setup
// Assistant, Health and install flows, so a red failure and a grey informational
// line were hard to tell apart. Every state carries an icon AND a text label so
// the meaning never depends on colour alone.
enum V3StatusSeverity: String, Equatable, CaseIterable {
    case working
    case completed
    case warning
    case failed
    case cancelled
    case unknown

    var icon: String {
        switch self {
        case .working: return "arrow.triangle.2.circlepath"
        case .completed: return "checkmark.circle.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .failed: return "xmark.circle.fill"
        case .cancelled: return "slash.circle"
        case .unknown: return "questionmark.circle"
        }
    }

    /// The colour used alongside the icon and the text.
    var severityName: String {
        switch self {
        case .working: return "working"
        case .completed: return "success"
        case .warning: return "warning"
        case .failed: return "failure"
        case .cancelled: return "cancelled"
        case .unknown: return "unknown"
        }
    }

    var isFailure: Bool { self == .failed }
    var isSuccess: Bool { self == .completed }
    /// Only a genuine success is presented as a tick.
    var showsCheckmark: Bool { self == .completed }
}

struct V3StatusPresentation: Equatable {
    let severity: V3StatusSeverity
    let title: String
    let detail: String

    var icon: String { severity.icon }
    var severityName: String { severity.severityName }
    var isFailure: Bool { severity.isFailure }
    var isSuccess: Bool { severity.isSuccess }

    init(severity: V3StatusSeverity, title: String, detail: String = "") {
        self.severity = severity
        self.title = title
        self.detail = detail
    }

    /// Maps a product state word onto the shared severity model.
    static func severity(forState state: String) -> V3StatusSeverity {
        switch state {
        case "complete", "completed", "verified", "ready", "success": return .completed
        case "failed", "error": return .failed
        case "warning", "actionRequired", "needsAttention": return .warning
        case "running", "checking", "working", "loading", "inProgress": return .working
        case "cancelled", "canceled": return .cancelled
        default: return .unknown
        }
    }

    /// V3_RELOAD_STATUS_VISIBILITY_V1: loading wins over connected. The previous
    /// ordering rendered a green "Active & Connected" while a reload was
    /// actively running, so the button appeared to do nothing.
    static func connectionState(connected: Bool, loading: Bool) -> V3StatusPresentation {
        if loading {
            return V3StatusPresentation(severity: .working, title: "Reloading Status...")
        }
        if connected {
            return V3StatusPresentation(severity: .completed, title: "Connected")
        }
        return V3StatusPresentation(severity: .failed, title: "Not Connected")
    }
}

// V3_USER_FACING_ISSUE_V1
// The global alert used to offer "Retry Connection" for essentially every
// failure, which trained users to read every problem as a networking problem.
// A source failure, a certificate failure, an auth failure and a pairing failure
// each get the action that can actually resolve them. Connection evidence opens
// Connection Settings; it does not claim that reloading status retried a mutation.
enum V3IssueAction: String, Equatable, CaseIterable {
    case retrySource
    case reloadSources
    case reloadStatus
    case openCertificates
    case openAccount
    case showPairingSetup
    case openConnectionCheck
    case chooseIPA
    case openSetup
    case openSources
    case dismiss

    var title: String {
        switch self {
        case .retrySource: return "Retry Source"
        case .reloadSources: return "Reload Sources"
        case .reloadStatus: return "Reload Status"
        case .openCertificates: return "Open Certificates"
        case .openAccount: return "Open Account & Signing"
        case .showPairingSetup: return "Show Pairing Setup"
        case .openConnectionCheck: return "Open Connection Settings"
        case .chooseIPA: return "Choose IPA Again"
        case .openSetup: return "Open Setup Assistant"
        case .openSources: return "Open Sources"
        case .dismiss: return "OK"
        }
    }

    /// The screen this action opens, or nil for an action that re-requests.
    var destination: String? {
        switch self {
        case .openCertificates: return "certificates"
        case .openAccount: return "signIn"
        case .showPairingSetup: return "pairing"
        case .openConnectionCheck: return "connection"
        case .chooseIPA: return "ipa"
        case .openSetup: return "setup"
        case .retrySource, .reloadSources, .openSources: return "sources"
        case .reloadStatus: return nil
        case .dismiss: return nil
        }
    }
}

// V3_SIGNOUT_AUTHORITATIVE_POSTCONDITION_V1: upstream signOut can return after
// swallowing a Core Data deactivation save failure. The host therefore reports
// success only when the returned authoritative snapshot explicitly confirms all
// three sign-out facts. Missing facts remain unknown; they are never coerced to
// false for the success decision.
enum V3SignOutOutcome: Equatable {
    case confirmed
    case accountStateRemains
    case authenticationRemains
    case snapshotIncomplete
}

enum V3SignOutOutcomePolicy {
    static func resolve(authenticated: Bool?, activeAccountPresent: Bool?,
                        activeTeamPresent: Bool?) -> V3SignOutOutcome {
        guard let authenticated, let activeAccount = activeAccountPresent,
              let activeTeam = activeTeamPresent else {
            return .snapshotIncomplete
        }
        if authenticated { return .authenticationRemains }
        if activeAccount || activeTeam { return .accountStateRemains }
        return .confirmed
    }

    static func successNotice(for outcome: V3SignOutOutcome) -> String? {
        outcome == .confirmed ? "Signed out successfully." : nil
    }

    static func whatHappened(for outcome: V3SignOutOutcome) -> String? {
        switch outcome {
        case .confirmed: return nil
        case .accountStateRemains:
            return "Sign-in credentials were cleared, but SideStore still reports an active account or team."
        case .authenticationRemains:
            return "SideStore still reports an active sign-in. Sign-out is not confirmed."
        case .snapshotIncomplete:
            return "SideStore did not return enough account state to confirm sign-out."
        }
    }

    static func whatToDo(for outcome: V3SignOutOutcome) -> String? {
        guard outcome != .confirmed else { return nil }
        switch outcome {
        case .confirmed: return nil
        case .accountStateRemains:
            return "Reload status. If the account or team remains active, try Sign Out again."
        case .authenticationRemains:
            return "Reload status to check the current account state, then try Sign Out again if it remains signed in."
        case .snapshotIncomplete:
            return "Reload status before continuing. If SideStore still reports a signed-in account, try Sign Out again."
        }
    }
}

enum V3AnisetteFailureGuidance {
    static func message(_ failure: CombinedFailure) -> String? {
        guard failure.operation.lowercased().hasPrefix("anisette") else { return nil }
        if failure.code == .cancelled {
            return "What happened: The Anisette Servers request was cancelled.\nWhat you can do: Reopen Anisette Servers to check the current state before trying again."
        }
        guard failure.stage == .network ||
              failure.safeCause == .networkConnectionLost ||
              failure.safeCause == .networkTimedOut ||
              failure.safeCause == .networkUnavailable else { return nil }
        return "What happened: SideStore could not reach the configured Anisette server.\nWhat you can do: Check its address and your network, then try again. This does not show that LocalDevVPN is unavailable."
    }
}

enum V3SideJITReachabilityFeedback {
    static let unreachable = "The SideJIT server could not be reached. Check its address and network, then try again."

    static func reachable(httpStatusCode: Int?) -> String {
        httpStatusCode.map { "Reachable (HTTP \($0))." } ?? "Reachable."
    }
}

struct V3UserFacingIssue: Equatable {
    let title: String
    let severity: V3StatusSeverity
    let whatHappened: String
    let whatToDo: String
    let technicalDetails: String
    let primaryAction: V3IssueAction
    let secondaryAction: V3IssueAction
    let recoveryDestination: String?
    let retryDisposition: V3RetryDisposition

    /// The single place that decides which action a failure deserves. Selection
    /// is driven by the typed operation, stage and safe cause, never by a
    /// numeric code or by the mere fact that a request failed.
    static func make(operation: String, stage: String, code: String,
                     safeCause: String?, sourceStep: String?, retryable: Bool?,
                     whatHappened: String, whatToDo: String, technicalDetails: String) -> V3UserFacingIssue {
        let anisetteNetworkFailure = operation.lowercased().hasPrefix("anisette") &&
            [CombinedFailure.SafeCause.networkConnectionLost.rawValue,
             CombinedFailure.SafeCause.networkTimedOut.rawValue,
             CombinedFailure.SafeCause.networkUnavailable.rawValue].contains(safeCause ?? "")
        let destination: String? = {
            if anisetteNetworkFailure { return nil }
            if safeCause == CombinedFailure.SafeCause.pairingRequired.rawValue ||
               safeCause == CombinedFailure.SafeCause.invalidPairingFile.rawValue { return "pairing" }
            if safeCause == CombinedFailure.SafeCause.authResponseCapacityUnavailable.rawValue { return "signIn" }
            if safeCause == CombinedFailure.SafeCause.keychainSignOutFailed.rawValue { return "signIn" }
            if safeCause == CombinedFailure.SafeCause.sourceRemoveFailed.rawValue ||
               safeCause == CombinedFailure.SafeCause.sourceRemoveBusy.rawValue { return "sources" }
            if [CombinedFailure.SafeCause.signingNetworkConnectionLost.rawValue,
                CombinedFailure.SafeCause.signingNetworkTimedOut.rawValue,
                CombinedFailure.SafeCause.signingNetworkUnavailable.rawValue].contains(safeCause ?? "") {
                return "connection"
            }
            if operation == "source" && stage == CombinedFailure.Stage.serviceReadiness.rawValue {
                return "sources"
            }
            if stage == CombinedFailure.Stage.authentication.rawValue { return "signIn" }
            if stage == CombinedFailure.Stage.filePreparation.rawValue { return "ipa" }
            if sourceStep == CombinedFailure.SourceStep.provisioningProfileFetch.rawValue
                || sourceStep == CombinedFailure.SourceStep.certificateValidation.rawValue
                || safeCause == CombinedFailure.SafeCause.certificateUnavailable.rawValue
                || safeCause == CombinedFailure.SafeCause.provisioningProfileUnavailable.rawValue
                || stage == CombinedFailure.Stage.signing.rawValue {
                return "certificates"
            }
            if operation == "source" || sourceStep == CombinedFailure.SourceStep.manifestParsing.rawValue
                || sourceStep == CombinedFailure.SourceStep.sourceDownload.rawValue {
                return "sources"
            }
            // Only these stages actually implicate connectivity or readiness.
            if stage == CombinedFailure.Stage.network.rawValue
                || stage == CombinedFailure.Stage.coreDevice.rawValue
                || stage == CombinedFailure.Stage.cdTunnel.rawValue
                || stage == CombinedFailure.Stage.rsdDiscovery.rawValue
                || stage == CombinedFailure.Stage.rsdService.rawValue
                || stage == CombinedFailure.Stage.lockdownConnection.rawValue
                || stage == CombinedFailure.Stage.uniqueDeviceID.rawValue
                || stage == CombinedFailure.Stage.heartbeat.rawValue
                || stage == CombinedFailure.Stage.endpointSelection.rawValue
                || safeCause == CombinedFailure.SafeCause.networkConnectionLost.rawValue
                || safeCause == CombinedFailure.SafeCause.networkTimedOut.rawValue
                || safeCause == CombinedFailure.SafeCause.networkUnavailable.rawValue
                || safeCause == CombinedFailure.SafeCause.wifiUnavailable.rawValue
                || safeCause == CombinedFailure.SafeCause.localDevVPNUnavailable.rawValue {
                return "connection"
            }
            if stage == CombinedFailure.Stage.provisioning.rawValue {
                return "setup"
            }
            return nil
        }()

        let primary: V3IssueAction = {
            switch destination {
            case "certificates": return .openCertificates
            case "signIn": return .openAccount
            case "pairing": return .showPairingSetup
            case "ipa": return .chooseIPA
            case "sources":
                if safeCause == CombinedFailure.SafeCause.sourceRemoveFailed.rawValue ||
                   safeCause == CombinedFailure.SafeCause.sourceRemoveBusy.rawValue { return .reloadSources }
                if safeCause == CombinedFailure.SafeCause.knownSourcePolicyNetworkFailure.rawValue ||
                    safeCause == CombinedFailure.SafeCause.knownSourcePolicyInvalidResponse.rawValue ||
                    safeCause == CombinedFailure.SafeCause.sourceInvalidManifest.rawValue ||
                    safeCause == CombinedFailure.SafeCause.sourceInvalidURL.rawValue ||
                    safeCause == CombinedFailure.SafeCause.sourceBlocked.rawValue ||
                    safeCause == CombinedFailure.SafeCause.sourceChangedID.rawValue ||
                    safeCause == CombinedFailure.SafeCause.sourceDuplicate.rawValue ||
                    safeCause == CombinedFailure.SafeCause.sourceUnsupported.rawValue ||
                    safeCause == CombinedFailure.SafeCause.sourceValidationFailed.rawValue {
                    return .openSources
                }
                if [CombinedFailure.SafeCause.responseEncodingFailed.rawValue,
                    CombinedFailure.SafeCause.responseTooLarge.rawValue,
                    CombinedFailure.SafeCause.responseCapacityUnavailable.rawValue,
                    CombinedFailure.SafeCause.operationInProgress.rawValue].contains(safeCause ?? "") {
                    return .dismiss
                }
                if stage == CombinedFailure.Stage.serviceReadiness.rawValue { return .openSources }
                return .retrySource
            case "setup": return .openSetup
            // Reloading a snapshot does not retry the failed mutation. Send the
            // user to the connection settings that can resolve this evidence.
            case "connection": return .openConnectionCheck
            default:
                // No evidence points anywhere specific. Never assume networking.
                return .dismiss
            }
        }()

        let disposition: V3RetryDisposition = {
            if safeCause == CombinedFailure.SafeCause.responseCapacityUnavailable.rawValue ||
               safeCause == CombinedFailure.SafeCause.authResponseCapacityUnavailable.rawValue {
                return .prerequisite
            }
            if retryable == false { return .blocked }
            if destination == "connection" && retryable == true { return .allowed }
            if retryable == true { return .allowed }
            return .unknown
        }()

        return V3UserFacingIssue(
            title: "SideStore",
            severity: .failed,
            whatHappened: whatHappened,
            whatToDo: anisetteNetworkFailure
                ? "Check the configured Anisette server and your network, then try again."
                : whatToDo,
            technicalDetails: technicalDetails,
            primaryAction: primary,
            secondaryAction: .dismiss,
            recoveryDestination: destination,
            retryDisposition: disposition)
    }

    /// Builds an issue from a typed failure, preserving its privacy-safe text.
    static func make(_ failure: CombinedFailure) -> V3UserFacingIssue {
        make(operation: failure.operation, stage: failure.stage.rawValue, code: failure.code.rawValue,
             safeCause: failure.safeCause?.rawValue, sourceStep: failure.sourceStep?.rawValue,
             retryable: failure.retryable, whatHappened: failure.safeMessage,
             whatToDo: failure.recovery, technicalDetails: failure.technicalDetails)
    }

    /// One-line summary, kept short enough for a copyable alert body.
    var summary: String { whatHappened }
}

// V3_CATALOG_ROW_POLICY_V1
// The catalog view deduplicated by snapshotting the accumulated IDs before
// filtering a page, so an identifier repeated inside one page passed twice. The
// rule lives here so the real behaviour is executable rather than asserted as
// source text.
enum V3CatalogRowPolicy {
    static func identifier(of row: [String: Any]) -> String? {
        guard let value = row["identifier"] as? String, !value.isEmpty else { return nil }
        return value
    }

    static func isDisplayable(_ row: [String: Any]) -> Bool {
        identifier(of: row) != nil && row["name"] as? String != nil
    }

    /// Removes duplicates by identifier, preserving first-seen order, across
    /// every page seen so far. Rows without a usable identifier are rejected
    /// rather than silently kept, because they cannot be deduplicated or
    /// installed.
    static func dedupe(_ rows: [[String: Any]]) -> [[String: Any]] {
        var seen = Set<String>()
        var result: [[String: Any]] = []
        result.reserveCapacity(rows.count)
        for row in rows {
            guard let identifier = identifier(of: row) else { continue }
            if seen.insert(identifier).inserted { result.append(row) }
        }
        return result
    }

}

struct V3CatalogRowsAccumulator {
    private(set) var rows: [[String: Any]] = []
    private var identifiers = Set<String>()

    mutating func append(_ page: [[String: Any]]) {
        for row in page {
            guard V3CatalogRowPolicy.isDisplayable(row),
                  let identifier = V3CatalogRowPolicy.identifier(of: row),
                  identifiers.insert(identifier).inserted else { continue }
            rows.append(row)
        }
    }
}

// V3_RELOAD_GATE_V1
// The reload gate rules, made explicit and executable. The store previously
// inlined this, and callers could not await an authoritative snapshot, so a
// recalculate could read the previous snapshot.
// V3_LOAD_ACTIVITY_OWNERSHIP_V1
// One `loading` flag used to mean two different things: an authoritative status
// snapshot, and a mutation such as refreshSources, signOut, clearCache, syncAppIDs
// or a JIT operation. The reload gate read that flag as "a snapshot is in flight",
// so a caller awaiting an authoritative snapshot could join a mutation instead,
// and the mutation's completion released it with a not-observed outcome before
// any snapshot had been performed. The activity is now named, and the gate can
// tell the two apart.
enum V3LoadActivity: String, Equatable, CaseIterable {
    case idle
    /// An authoritative status snapshot is in flight. This is the only activity
    /// that may resolve a snapshot waiter.
    case snapshot
    /// A mutation is in flight. A snapshot must be requested after it, never
    /// substituted by it.
    case mutation
}

// V3_SNAPSHOT_GATE_V1
// The decision a snapshot request makes. It is a pure function so the ordering
// contract is executable behaviour rather than a comment about a flag.
enum V3SnapshotDecision: String, Equatable, CaseIterable {
    /// The caller owns the snapshot and must perform it now.
    case performSnapshot
    /// A snapshot is genuinely in flight. The caller parks and joins it.
    case joinSnapshot
    /// A mutation is in flight. The caller parks, and a snapshot is owed for
    /// after the mutation. The mutation's completion must not resolve it.
    case awaitMutationThenSnapshot
    /// A presented operation owns the state a snapshot would report. The caller
    /// parks, and a snapshot is owed for when the operation ends.
    case deferForPresentation
    /// A snapshot is owed, but its mutation/presentation blocker is still
    /// active. Keep the owed intent and parked waiters until that blocker ends.
    case stillBlocked
    /// Policy refuses an optional non-manual snapshot, so callers are told
    /// truthfully that nothing was observed. No continuation may remain parked.
    case doNotObserve
}

enum V3SnapshotGate {
    /// A presented operation owns the state, so its snapshot is deferred even
    /// when nothing else is running. This is checked first because a sheet can
    /// be up while a mutation is still settling, and both must be honoured.
    static func decide(activity: V3LoadActivity, presentationActive: Bool,
                       manual: Bool, requiresConnectionRetry: Bool) -> V3SnapshotDecision {
        if presentationActive { return .deferForPresentation }
        switch activity {
        case .snapshot: return .joinSnapshot
        case .mutation: return .awaitMutationThenSnapshot
        case .idle: break
        }
        if !manual && requiresConnectionRetry { return .doNotObserve }
        return .performSnapshot
    }

    /// The result of running an owed snapshot once the blocking activity has
    /// ended. Every case is total: no input leaves a parked continuation
    /// without a resumption, which is what made a non-manual deferred reload a
    /// latent permanent hang.
    static func drain(activity: V3LoadActivity, presentationActive: Bool,
                      owed: Bool, anyWaiterNeedsManual: Bool,
                      explicitManualOwed: Bool = false,
                      requiresConnectionRetry: Bool) -> V3SnapshotDecision {
        guard owed else { return .doNotObserve }
        guard activity == .idle, !presentationActive else { return .stillBlocked }
        return decide(activity: .idle, presentationActive: false,
                      manual: explicitManualOwed || anyWaiterNeedsManual || !requiresConnectionRetry,
                      requiresConnectionRetry: requiresConnectionRetry)
    }
}

// Fire-and-forget reloads have no waiter from which to recover their manual
// intent after a mutation or presentation defers them. Keep that intent with
// the owed snapshot so a manual request still clears a prior connection retry
// latch when the blocker ends.
struct V3SnapshotOwedIntent: Equatable {
    private(set) var isOwed = false
    private(set) var requiresManualSnapshot = false

    mutating func record(manual: Bool) {
        isOwed = true
        requiresManualSnapshot = requiresManualSnapshot || manual
    }

    mutating func clear() {
        isOwed = false
        requiresManualSnapshot = false
    }
}

// V3_SOURCE_EDITING_POLICY_V1
// Issue #40: the Add Source field had no focus state and no explicit dismissal,
// so Return was the only way out of the keyboard and read as a submit action.
// The cancel semantics are stated here so they are executable and testable:
// Cancel restores the URL that was present when editing began, and neither
// Cancel nor Done may preview, request, or persist anything.
enum V3SourceEditingOutcome: Equatable {
    case dismissed
    case restored(String)
}

struct V3SourceFormState: Equatable {
    var isPresented: Bool
    var url: String
    var originalURL: String
    var isFocused: Bool
    var hasPreview: Bool
}

enum V3SourceFormEffect: Equatable {
    case preview
    case validate
    case persist
}

struct V3SourceFormTransition: Equatable {
    var state: V3SourceFormState
    var effects: [V3SourceFormEffect]
}

struct V3SourcePreviewRequest: Equatable {
    let generation: UInt64
    let targetURL: String
}

struct V3SourcePreviewSession {
    private(set) var generation: UInt64 = 0
    private(set) var activeRequest: V3SourcePreviewRequest?

    mutating func begin(targetURL: String) -> V3SourcePreviewRequest? {
        guard !targetURL.isEmpty else { return nil }
        generation &+= 1
        let request = V3SourcePreviewRequest(generation: generation, targetURL: targetURL)
        activeRequest = request
        return request
    }

    mutating func invalidate() {
        generation &+= 1
        activeRequest = nil
    }

    func mayApply(_ request: V3SourcePreviewRequest, currentURL: String,
                  formPresented: Bool) -> Bool {
        formPresented && activeRequest == request && generation == request.generation &&
            currentURL == request.targetURL
    }

    static func responseRow(_ payload: [String: Any], for request: V3SourcePreviewRequest) -> [String: Any] {
        var row = payload
        row["url"] = request.targetURL
        return row
    }
}

struct V3SourceFormOpenRequestLedger {
    private(set) var lastClaimedRequestID: UUID?

    mutating func claim(_ requestID: UUID?) -> Bool {
        guard let requestID, requestID != lastClaimedRequestID else { return false }
        lastClaimedRequestID = requestID
        return true
    }
}

enum V3SourceEditingPolicy {
    static func canCancelForm(isAdding: Bool) -> Bool { !isAdding }

    /// Done: a pure UI dismissal. The typed value is kept.
    static func done(typed: String) -> V3SourceEditingOutcome { .dismissed }

    /// Cancel: restore the pre-edit value, so a URL is never silently discarded
    /// and a later focus always starts from a predictable value.
    static func cancel(typed: String, beforeEditing: String) -> V3SourceEditingOutcome {
        .restored(beforeEditing)
    }

    /// The value the field should hold after the outcome is applied.
    static func resolved(_ outcome: V3SourceEditingOutcome, typed: String) -> String {
        switch outcome {
        case .dismissed: return typed
        case .restored(let value): return value
        }
    }

    /// Closing Add Source is a UI-only transition. It restores the value that
    /// was present when the form opened and cannot request preview, validation,
    /// or persistence work.
    static func closeForm(_ current: V3SourceFormState) -> V3SourceFormTransition {
        V3SourceFormTransition(
            state: V3SourceFormState(
                isPresented: false,
                url: current.originalURL,
                originalURL: current.originalURL,
                isFocused: false,
                hasPreview: false),
            effects: [])
    }
}

// V3_SETUP_COMPLETION_POLICY_V1
// One authority for "is setup finished". Home and the Setup Assistant each used
// their own rule, so Home could stop showing "Finish Setup" while the assistant
// still considered setup incomplete. Two authorities for one product state is
// the defect; this type removes the possibility of disagreement by having exactly
// one decision, consumed by both, and by reporting which item is outstanding
// rather than a bare boolean.
enum V3SetupOutstandingItem: String, Equatable, CaseIterable {
    case account
    case provisioning
    case pairing
    case jitless
    case network
    case tunnel
    case backgroundRefresh
    case schedule
    case verifiedRefresh

    /// User-facing label, so the UI can name the outstanding step.
    var title: String {
        switch self {
        case .account: return "Sign in with your Apple ID"
        case .provisioning: return "Finish device provisioning"
        case .pairing: return "Add a pairing file"
        case .jitless: return "Configure the JIT-Less certificate"
        case .network: return "Connect to Wi-Fi"
        case .tunnel: return "Enable LocalDevVPN"
        case .backgroundRefresh: return "Allow Background App Refresh"
        case .schedule: return "Enable scheduled refresh"
        case .verifiedRefresh: return "Run one verified refresh"
        }
    }
}

struct V3SetupCompletionInputs: Equatable {
    var accountComplete = false
    var provisioningIncomplete = false
    var pairingSatisfied = false
    var jitlessRequired = false
    var jitlessComplete = false
    var networkComplete = false
    var tunnelComplete = false
    var backgroundRefreshAvailable = false
    var scheduleEnabled = false
    var verifiedRefreshPresent = false

    /// The only legal way to decide whether setup is finished.
    func outstanding() -> [V3SetupOutstandingItem] {
        var items: [V3SetupOutstandingItem] = []
        if !accountComplete { items.append(.account) }
        if provisioningIncomplete { items.append(.provisioning) }
        if !pairingSatisfied { items.append(.pairing) }
        // JIT-Less is only a prerequisite where the platform requires it.
        if jitlessRequired && !jitlessComplete { items.append(.jitless) }
        if !networkComplete { items.append(.network) }
        if !tunnelComplete { items.append(.tunnel) }
        if !backgroundRefreshAvailable { items.append(.backgroundRefresh) }
        if !scheduleEnabled { items.append(.schedule) }
        if !verifiedRefreshPresent { items.append(.verifiedRefresh) }
        return items
    }

    var isComplete: Bool { outstanding().isEmpty }
}

// V3_FAILURE_GUIDANCE_V1
// A failure that reached a view as an untyped error was displayed as
// error.localizedDescription. That publishes whatever text the service happened
// to attach, which for a bridged NSError includes its numeric domain and code
// and means nothing to a user, and it offered no guidance at all. Every
// user-visible failure message now comes from here.
//
// A typed CombinedFailure keeps its own product recovery copy. An untyped error
// cannot be attributed to a cause, so the guidance deliberately does not guess
// one: it says what is known, and it points at the diagnostics that can identify
// it. The unreadable text is kept out of the interface and offered through
// Copy Diagnostics instead.
enum V3FailureGuidance {
    static func message(_ error: Error) -> String {
        if let combined = error as? CombinedFailure {
            return combined.recovery
        }
        // The earlier wording asserted "and nothing was changed". Nothing
        // supports that: an untyped failure can arrive after the service applied
        // the request, and the same helper is used after settings writes, source
        // confirmation, pairing import and install staging. Claiming a known
        // side-effect from an unknown cause is the same class of error as
        // blaming the network, so the claim is removed and the outcome is stated
        // as unknown.
        return "That action did not complete, and whether it took effect is not known. Reload status to see the current state before trying again. If it keeps failing, copy diagnostics to identify the cause."
    }

    /// Privacy-safe diagnostic text, never shown as guidance.
    static func diagnostics(_ error: Error) -> String {
        if let combined = error as? CombinedFailure {
            return combined.technicalDetails
        }
        let nsError = error as NSError
        let underlying = CombinedFailure.safeDiagnosticUnderlying(domain: nsError.domain,
            code: nsError.code)
        return "operation=untyped stage=command code=failed underlying_domain=\(underlying.domain) underlying_code=\(underlying.code)"
    }
}

// V3_RESPONSE_CLASSIFICATION_CARRIER_V1
// The service's reply encoder and the host's reply classifier are separated by
// a property-list boundary, and the classification of a reply the service could
// not deliver has to survive that boundary. It previously did not: the service
// wrote the specific token under a legacy "error" key and a cause-less
// structured "failure", and the host prefers the structured envelope, so every
// encoding failure arrived as a generic invalidResponse.
//
// Both halves live here, as pure functions, so the pair can be executed together
// against real property-list bytes rather than asserted about in source text.
// The host still prefers the structured envelope; the classification simply
// travels inside it now, and the legacy token remains for an older host.
enum V3ResponseClassifier {
    /// The legacy string tokens a service may put in the "error" key.
    enum Token {
        static let encodingFailed = "responseEncodingFailed"
        static let tooLarge = "responseTooLarge"
    }

    /// The safe cause that carries a token's classification across the wire.
    static func safeCause(for token: String) -> CombinedFailure.SafeCause? {
        switch token {
        case Token.encodingFailed: return .responseEncodingFailed
        case Token.tooLarge: return .responseTooLarge
        default: return nil
        }
    }
}

// V3_RESPONSE_ENCODER_V1
// The service side of the classification pair. It is a separate enum rather than
// a private method so the harness can execute the real encoder, and it reads the
// shared responseLimit instead of repeating the literal.
struct V3EncodedServiceResponse {
    let data: Data
    let fallbackToken: String?
}

enum V3ResponseEncoder {
    /// Encodes a reply, or returns a correlated, typed fallback that says which
    /// of the two failure modes occurred.
    ///
    /// The limit is a parameter rather than a read of `V3WireContract` so this
    /// file stays independently compilable, exactly as the wire contract stays
    /// free of the error model. The caller passes the one shared constant, so
    /// the limit still has a single definition in production.
    static func encode(_ value: [String: Any], operation: String = "command",
                       limit: Int) -> Data {
        encodeDetailed(value, operation: operation, limit: limit).data
    }

    /// Returns a safe fallback marker with the data so the service can log
    /// classification without parsing every successful serialized reply.
    static func encodeDetailed(_ value: [String: Any], operation: String = "command",
                               limit: Int) -> V3EncodedServiceResponse {
        let correlationID = value["id"] as? String ?? ""
        do {
            let data = try PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
            guard data.count <= limit else {
                return V3EncodedServiceResponse(data: fallback(id: correlationID, operation: operation,
                                token: V3ResponseClassifier.Token.tooLarge,
                                code: .invalidResponse,
                                safeCause: V3ResponseClassifier.safeCause(for: V3ResponseClassifier.Token.tooLarge)),
                    fallbackToken: V3ResponseClassifier.Token.tooLarge)
            }
            return V3EncodedServiceResponse(data: data, fallbackToken: nil)
        } catch {
            return V3EncodedServiceResponse(data: fallback(id: correlationID, operation: operation,
                            token: V3ResponseClassifier.Token.encodingFailed,
                            code: .invalidResponse,
                            safeCause: V3ResponseClassifier.safeCause(for: V3ResponseClassifier.Token.encodingFailed)),
                fallbackToken: V3ResponseClassifier.Token.encodingFailed)
        }
    }

    /// Builds a small, correlated, typed fallback reply. Always serializable
    /// because every value is a concrete String, Bool or Int.
    ///
    /// The reply deliberately carries BOTH the legacy "error" token and the
    /// structured "failure" envelope, because that is the shape production
    /// emits. The structured envelope is authoritative on the host, so the
    /// classification that survives is the safeCause set here.
    static func fallback(id: String, operation: String, token: String,
                         code: CombinedFailure.Code,
                         safeCause: CombinedFailure.SafeCause? = nil) -> Data {
        let value: [String: Any] = [
            "version": 1,
            "id": id,
            "error": token,
            "failure": CombinedFailure(operation: operation, stage: .replyEncoding, code: code,
                                       id: id, safeCause: safeCause).wire
        ]
        return (try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)) ?? Data()
    }
}

// V3_SHARED_JITLESS_FACT_V1
// Home and the Setup Assistant each decided JIT-Less completion separately. Home
// had no access to the certificate facts, so on the platforms that require
// JIT-Less it reported the item as permanently outstanding while the assistant,
// which had the real readiness, showed it complete. One observed readiness is
// now published and both surfaces read it.
//
// A nil readiness means "not observed yet", which counts as outstanding. Guessing
// "fine" there is what produced the original disagreement.
enum V3JITLessCompletionPolicy {
    static func isComplete(_ readiness: V3JITLessReadiness?) -> Bool {
        guard let readiness else { return false }
        return readiness.isReady
    }

    /// True where an unobserved JIT-Less state is still an outstanding item.
    static func isRequired(osMajor: Int) -> Bool { osMajor >= 26 }
}

enum V3SetupFactObservationPolicy {
    static let maximumAge: TimeInterval = 5 * 60

    static func shouldObserve(connected: Bool, setupPresented: Bool,
                              operationPresented: Bool, loading: Bool,
                              returnToSetupPending: Bool, lastAttemptAt: Date?,
                              now: Date = Date()) -> Bool {
        guard connected, !setupPresented, !operationPresented, !loading,
              !returnToSetupPending else { return false }
        guard let lastAttemptAt else { return true }
        guard lastAttemptAt <= now else { return false }
        return now.timeIntervalSince(lastAttemptAt) >= maximumAge
    }
}

enum V3SetupFactRevisionPolicy {
    static func mayApply(captured: UInt64, current: UInt64) -> Bool {
        captured == current
    }
}

// V3_HOST_SNAPSHOT_WAITER_LIFETIME_V1
// Snapshot callers may cancel while the shared service request must continue
// for other callers. This registry gives each waiter one removable identity and
// drains only the waiters that are still pending when the snapshot finishes.
struct V3SnapshotWaiterRegistry {
    private struct Waiter: Equatable {
        let manual: Bool
        let requiredSnapshotGeneration: UInt64
    }
    private var waitersByID: [UUID: Waiter] = [:]

    var isEmpty: Bool { waitersByID.isEmpty }
    var anyManualWaiter: Bool { waitersByID.values.contains(where: \.manual) }

    mutating func insert(_ id: UUID, manual: Bool,
                         requiredSnapshotGeneration: UInt64 = 0) {
        waitersByID[id] = Waiter(manual: manual,
                                 requiredSnapshotGeneration: requiredSnapshotGeneration)
    }

    @discardableResult
    mutating func remove(_ id: UUID) -> Bool {
        waitersByID.removeValue(forKey: id) != nil
    }

    /// Take only callers whose freshness requirement is met by this snapshot.
    /// Waiters parked behind a blocker during an older in-flight snapshot remain
    /// registered until a later generation completes.
    mutating func take(throughSnapshotGeneration generation: UInt64) -> [UUID] {
        let ready = waitersByID.compactMap { id, waiter in
            waiter.requiredSnapshotGeneration <= generation ? id : nil
        }
        for id in ready { waitersByID.removeValue(forKey: id) }
        return ready
    }

    mutating func takeAll() -> [UUID] {
        let ids = Array(waitersByID.keys)
        waitersByID.removeAll(keepingCapacity: true)
        return ids
    }
}

/// Maps the gate decision to the minimum snapshot generation that can satisfy
/// the caller. A deferred request must not be released by a snapshot that began
/// before its mutation/presentation blocker ended.
enum V3SnapshotWaiterEpochPolicy {
    static func requiredGeneration(for decision: V3SnapshotDecision,
                                   currentGeneration: UInt64) -> UInt64 {
        switch decision {
        case .performSnapshot, .joinSnapshot, .doNotObserve:
            return currentGeneration
        case .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
            return currentGeneration &+ 1
        }
    }
}

enum V3SnapshotErrorPolicy {
    /// Cancellation is a local control-flow result, not evidence that the
    /// backend became disconnected.
    static func shouldMarkDisconnected(_ error: Error) -> Bool {
        !(error is CancellationError)
    }
}

// Health notifications can arrive while a request is in flight. Keep one
// pending rerun so a certificate update is observed after the current request.
struct V3HealthReloadQueue {
    private(set) var isChecking = false
    private(set) var rerunRequested = false

    mutating func request() -> Bool {
        guard !isChecking else {
            rerunRequested = true
            return false
        }
        isChecking = true
        return true
    }

    /// Returns true when exactly one queued request should run next.
    mutating func finishIteration() -> Bool {
        guard rerunRequested else {
            isChecking = false
            return false
        }
        rerunRequested = false
        return true
    }
}

enum V3RetryDisposition: Equatable {
    case allowed
    case unknown
    case prerequisite
    case blocked
}

enum V3CatalogRetryPresentation: Equatable {
    case retry
    case retryWithUnknownDisposition
    case reloadCatalog
    case noRetry
}

enum V3CatalogRetryPresentationPolicy {
    static func action(for disposition: V3RetryDisposition,
                       safeCause: String? = nil) -> V3CatalogRetryPresentation {
        if safeCause == CombinedFailure.SafeCause.catalogUnavailable.rawValue {
            return .reloadCatalog
        }
        switch disposition {
        case .allowed: return .retry
        case .unknown: return .retryWithUnknownDisposition
        case .prerequisite, .blocked: return .noRetry
        }
    }
}

// V3_REFRESH_PREREQUISITE_POLICY_V1
// One authoritative prerequisite contract for every refresh entry point. Home
// Refresh All, Setup Assistant Test Refresh, Refresh Manager Manual Refresh,
// and targeted per-app refresh all call this instead of re-deriving rules, so
// a prerequisite the host already knows about can never be reported later as
// "no safe underlying cause was available".
//
// Two invariants are encoded here rather than at each call site.
// 1. The service's pairing status string is interpreted in exactly one place.
// 2. Only an authoritative "Pairing file required" blocks. "Unknown" (before
//    the first snapshot, or after a failed snapshot) does not block, so a host
//    restart can never permanently disable a correctly configured device.
//    Nothing is blocked on Wi-Fi, LocalDevVPN, or an account here: those are
//    not proven required for a refresh, and the scheduler already owns the
//    transport preflight for them.
enum V3RefreshPrerequisiteState: String, Equatable {
    case unknown
    case satisfied
    case unsatisfied
}

enum V3RefreshPrerequisiteKind: String, Equatable {
    case pairing
}

struct V3RefreshPrerequisite: Equatable {
    let state: V3RefreshPrerequisiteState
    let kind: V3RefreshPrerequisiteKind?
    let detail: String

    static let pairingRequiredDetail = "A valid pairing file is required before device refresh."

    private init(state: V3RefreshPrerequisiteState, kind: V3RefreshPrerequisiteKind?, detail: String) {
        self.state = state
        self.kind = kind
        self.detail = detail
    }

    static let unknown = V3RefreshPrerequisite(state: .unknown, kind: nil, detail: "")
    static let satisfied = V3RefreshPrerequisite(state: .satisfied, kind: nil, detail: "Pairing file available")
    static let pairingRequired = V3RefreshPrerequisite(state: .unsatisfied, kind: .pairing, detail: pairingRequiredDetail)

    /// The only interpretation of the authoritative pairing snapshot string.
    static func evaluate(pairingStatus: String?) -> V3RefreshPrerequisite {
        switch pairingStatus {
        case "Pairing file available": return .satisfied
        case "Pairing file required", "Pairing file invalid": return .pairingRequired
        default: return .unknown
        }
    }

    static func isConfirmed(pairingStatus: String?) -> Bool {
        evaluate(pairingStatus: pairingStatus).state == .satisfied
    }

    /// A cached pairing string is authoritative only while its snapshot is
    /// connected. After a failed snapshot, preserve the value for diagnostics
    /// but treat it as unknown for admission so stale state cannot block a run.
    static func evaluate(statusConnected: Bool, pairingStatus: String?) -> V3RefreshPrerequisite {
        guard statusConnected else { return .unknown }
        return evaluate(pairingStatus: pairingStatus)
    }

    var blocksRefresh: Bool { state == .unsatisfied }
    var blocksTargetedRefresh: Bool { blocksRefresh }
    var recoveryDestination: String? { kind == .pairing ? "pairing" : nil }
    var recoveryActionTitle: String? { kind == .pairing ? "Show Pairing Setup" : nil }
    var recommendedAction: String {
        kind == .pairing
            ? "Place or import a valid pairing file, then try again."
            : "Reload status, then try again."
    }

    /// The canonical structured failure for a blocked refresh. Minted only on
    /// demand so it can carry the caller's correlation ID.
    func failure(correlationID: String) -> CombinedFailure? {
        guard kind == .pairing else { return nil }
        return CombinedFailure(operation: "refresh", stage: .pairing, code: .notReady,
                               id: correlationID, retryable: false, safeCause: .pairingRequired)
    }
}

enum V3PairingPresentationPolicy {
    static func state(statusConnected: Bool, pairingStatus: String?) -> V3RefreshPrerequisiteState {
        V3RefreshPrerequisite.evaluate(statusConnected: statusConnected,
            pairingStatus: pairingStatus).state
    }

    /// A cached pairing value remains useful as history after a failed reload,
    /// but it must not look like a current authoritative result.
    static func displayText(statusConnected: Bool, pairingStatus: String) -> String {
        guard !statusConnected else { return pairingStatus }
        guard pairingStatus != "Unknown" else { return "Unknown" }
        return "Unknown (last known: \(pairingStatus))"
    }

    static func isConfirmed(statusConnected: Bool, pairingStatus: String?) -> Bool {
        state(statusConnected: statusConnected, pairingStatus: pairingStatus) == .satisfied
    }
}

enum V3IssueActionOutcomePolicy {
    /// Re-request actions dismiss the issue only when their request was
    /// accepted. A rejected retry replaces the alert with its current blocker.
    static func shouldDismiss(action: V3IssueAction, didStart: Bool) -> Bool {
        switch action {
        case .retrySource, .reloadSources: return didStart
        default: return true
        }
    }
}

struct V3OperationFailureDetails {
    let operation: String
    let stage: String
    let code: String
    let correlation: String
    let underlyingDomain: String
    let underlyingCode: Int
    let retryable: Bool?
    let safeCause: String?
    let sourceStep: String?
    let whatHappened: String
    let whatToDo: String
    let technical: String

    init(_ failure: CombinedFailure) {
        operation = failure.operation
        stage = failure.stage.rawValue
        code = failure.code.rawValue
        correlation = failure.correlationID
        underlyingDomain = failure.underlyingDomain
        underlyingCode = failure.underlyingCode
        retryable = failure.retryable
        safeCause = failure.safeCause?.rawValue
        sourceStep = failure.sourceStep?.rawValue
        whatHappened = failure.safeMessage
        whatToDo = failure.recovery
        technical = failure.technicalDetails
    }

    var retryDisposition: V3RetryDisposition {
        if safeCause == CombinedFailure.SafeCause.catalogSourceUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.responseEncodingFailed.rawValue ||
           safeCause == CombinedFailure.SafeCause.responseTooLarge.rawValue {
            return .blocked
        }
        if retryable == false { return .blocked }
        if stage == CombinedFailure.Stage.authentication.rawValue ||
           stage == CombinedFailure.Stage.filePreparation.rawValue ||
           safeCause == CombinedFailure.SafeCause.certificateUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.provisioningProfileUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.operationInProgress.rawValue ||
           safeCause == CombinedFailure.SafeCause.responseCapacityUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.authResponseCapacityUnavailable.rawValue {
            return .prerequisite
        }
        return retryable == true ? .allowed : .unknown
    }

    var recoveryDestination: String? {
        if operation == "source" ||
           [CombinedFailure.SafeCause.sourceNetworkFailure.rawValue,
            CombinedFailure.SafeCause.sourceInvalidManifest.rawValue,
            CombinedFailure.SafeCause.sourcePersistenceUnverified.rawValue,
            CombinedFailure.SafeCause.sourceInvalidURL.rawValue,
            CombinedFailure.SafeCause.sourceAddBusy.rawValue,
            CombinedFailure.SafeCause.catalogSourceUnavailable.rawValue].contains(safeCause ?? "") {
            return "sources"
        }
        if safeCause == CombinedFailure.SafeCause.pairingRequired.rawValue ||
           safeCause == CombinedFailure.SafeCause.invalidPairingFile.rawValue { return "pairing" }
        if safeCause == CombinedFailure.SafeCause.authResponseCapacityUnavailable.rawValue { return "signIn" }
        if stage == CombinedFailure.Stage.authentication.rawValue { return "signIn" }
        if stage == CombinedFailure.Stage.filePreparation.rawValue { return "ipa" }
        if safeCause == CombinedFailure.SafeCause.signingNetworkConnectionLost.rawValue ||
           safeCause == CombinedFailure.SafeCause.signingNetworkTimedOut.rawValue ||
           safeCause == CombinedFailure.SafeCause.signingNetworkUnavailable.rawValue {
            return "connection"
        }
        if stage == CombinedFailure.Stage.network.rawValue ||
           safeCause == CombinedFailure.SafeCause.networkConnectionLost.rawValue ||
           safeCause == CombinedFailure.SafeCause.networkTimedOut.rawValue ||
           safeCause == CombinedFailure.SafeCause.networkUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.signingNetworkConnectionLost.rawValue ||
           safeCause == CombinedFailure.SafeCause.signingNetworkTimedOut.rawValue ||
           safeCause == CombinedFailure.SafeCause.signingNetworkUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.wifiUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.localDevVPNUnavailable.rawValue {
            return "connection"
        }
        if sourceStep == CombinedFailure.SourceStep.provisioningProfileFetch.rawValue {
            return "certificates"
        }
        if sourceStep == CombinedFailure.SourceStep.certificateValidation.rawValue ||
           safeCause == CombinedFailure.SafeCause.certificateUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.provisioningProfileUnavailable.rawValue {
            return "certificates"
        }
        return nil
    }

    var recoveryActionTitle: String? {
        switch recoveryDestination {
        case "signIn": return "Open Account & Signing"
        case "ipa": return "Choose IPA Again"
        case "certificates": return "Open Certificates"
        case "connection": return "Open Connection Settings"
        case "pairing": return "Open Pairing File"
        case "sources": return "Open Sources"
        default: return nil
        }
    }

    var recommendedAction: String {
        if safeCause == CombinedFailure.SafeCause.responseEncodingFailed.rawValue {
            return "Copy Diagnostics and report that the service could not encode its response. Repeating the same request will not help."
        }
        if safeCause == CombinedFailure.SafeCause.responseTooLarge.rawValue {
            return "Copy Diagnostics and report that the service reply exceeded the transfer limit. Repeating the same request will fail again."
        }
        if safeCause == CombinedFailure.SafeCause.catalogUnavailable.rawValue {
            return "Reload this source's catalog. If it still cannot be read, copy Diagnostics and report the local catalog failure."
        }
        if safeCause == CombinedFailure.SafeCause.responseCapacityUnavailable.rawValue ||
           safeCause == CombinedFailure.SafeCause.authResponseCapacityUnavailable.rawValue {
            return "Wait for SideStore to release earlier request results, reload status, then try again."
        }
        switch safeCause ?? "" {
        case CombinedFailure.SafeCause.sourceNetworkFailure.rawValue:
            return "Open Sources. Check the network, then retry adding the source."
        case CombinedFailure.SafeCause.sourceInvalidManifest.rawValue,
             CombinedFailure.SafeCause.sourceInvalidURL.rawValue:
            return "Open Sources and correct the source URL or manifest before retrying."
        case CombinedFailure.SafeCause.sourceBlocked.rawValue:
            return "Do not add this source. Verify with the provider that it is safe before trying again."
        case CombinedFailure.SafeCause.sourceChangedID.rawValue:
            return "Contact the source provider before removing the saved source or adding it again."
        case CombinedFailure.SafeCause.sourceDuplicate.rawValue:
            return "Open Sources and use the existing source. Remove it only after confirming which entry is correct."
        case CombinedFailure.SafeCause.sourceUnsupported.rawValue:
            return "Update SideStore or use a source format supported by this version."
        case CombinedFailure.SafeCause.sourceValidationFailed.rawValue:
            return "Ask the source provider to correct its metadata, then preview it again."
        case CombinedFailure.SafeCause.sourcePersistenceUnverified.rawValue:
            return "Open Sources and reload the list to see whether the source was saved before retrying."
        case CombinedFailure.SafeCause.sourceAddBusy.rawValue,
             CombinedFailure.SafeCause.sourceRemoveBusy.rawValue:
            return "Wait for SideStore's active request to finish, then open Sources and check the result."
        case CombinedFailure.SafeCause.catalogSourceUnavailable.rawValue:
            return "Open Sources to confirm the source is still added, then reopen its catalog."
        default: break
        }
        if operation == "source" ||
           sourceStep == CombinedFailure.SourceStep.sourceDownload.rawValue ||
           sourceStep == CombinedFailure.SourceStep.manifestParsing.rawValue {
            return "Open Sources and review the source request. Copy Diagnostics if the result remains unclear."
        }
        switch safeCause ?? "" {
        case CombinedFailure.SafeCause.responseCapacityUnavailable.rawValue:
            return "Wait for SideStore to release earlier request results, check the current state, then retry this action."
        case CombinedFailure.SafeCause.pairingRequired.rawValue:
            return "Add the pairing file, then start the refresh again."
        case CombinedFailure.SafeCause.invalidPairingFile.rawValue:
            return "Open Pairing File and replace the saved pairing record, then retry."
        case CombinedFailure.SafeCause.operationInProgress.rawValue:
            return "Wait for the active SideStore operation to finish, then start this action again."
        case CombinedFailure.SafeCause.staleRefreshAttempt.rawValue:
            return "This stale refresh request was not started. Return to Refresh and start a new refresh."
        case CombinedFailure.SafeCause.signingNetworkConnectionLost.rawValue:
            return "Your current connection may still be healthy. Retry once. If this happens again, open Connection Settings."
        case CombinedFailure.SafeCause.signingNetworkTimedOut.rawValue:
            return "The provisioning service timed out for this request. Retry once. If it happens again, open Connection Settings."
        case CombinedFailure.SafeCause.signingNetworkUnavailable.rawValue:
            return "The provisioning service could not be reached for this request. Retry once. If it happens again, open Connection Settings."
        default: break
        }
        switch recoveryDestination {
        case "signIn": return "Open Account & Signing and complete the required account step."
        case "ipa": return "Choose the IPA again so SideStore can stage a fresh copy."
        case "certificates": return "Open Certificates and review the active certificate and provisioning profile."
        case "setup": return "Open Health Check / Connection and restore the required connection."
        default:
            if retryable == false {
                return "This operation is not marked safe to retry. Check the app and signing status before running it again."
            }
            if retryable == nil {
                if !whatToDo.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    return whatToDo
                }
                return "The service could not determine whether retry is safe. Check the app and signing status before deciding to retry."
            }
            return whatToDo
        }
    }
}

struct V3OperationPromptFailureDetails {
    let failure: V3OperationFailureDetails
    let blocksResubmission: Bool

    init(_ combinedFailure: CombinedFailure) {
        let details = V3OperationFailureDetails(combinedFailure)
        failure = details
        blocksResubmission = details.retryDisposition != .allowed
    }
}

// Keeps the failed pipeline stage across a Retry transition. A failure while
// creating the next backend session is explicitly separate from pipeline failure.
struct V3OperationRetryContext {
    private(set) var previousFailure: V3OperationFailureDetails?
    private(set) var currentFailure: V3OperationFailureDetails?
    private(set) var retryCouldNotStart = false

    mutating func recordPipelineFailure(_ failure: CombinedFailure) {
        currentFailure = V3OperationFailureDetails(failure)
        retryCouldNotStart = false
    }

    mutating func beginRetry() {
        previousFailure = currentFailure
        currentFailure = nil
        retryCouldNotStart = false
    }

    mutating func operationStarted() {
        previousFailure = nil
        currentFailure = nil
        retryCouldNotStart = false
    }

    mutating func recordStartFailure(_ failure: CombinedFailure) {
        currentFailure = V3OperationFailureDetails(failure)
        retryCouldNotStart = true
    }

    mutating func reset() {
        previousFailure = nil
        currentFailure = nil
        retryCouldNotStart = false
    }

    var whatHappened: String {
        guard let currentFailure else { return "The operation failed." }
        guard retryCouldNotStart else { return currentFailure.whatHappened }
        if currentFailure.safeCause == CombinedFailure.SafeCause.operationInProgress.rawValue {
            if let previousFailure {
                return "The retry could not start because another SideStore operation is still active. Previous attempt: \(previousFailure.whatHappened)"
            }
            return "The operation could not start because another SideStore operation is still active."
        }
        if let previousFailure {
            if ["timedOut", "interrupted"].contains(currentFailure.code) {
                return "The retry could not be confirmed as started. The previous operation may still be active. Previous attempt: \(previousFailure.whatHappened)"
            }
            return "The retry could not start, so the app operation did not run. Previous attempt: \(previousFailure.whatHappened)"
        }
        if ["timedOut", "interrupted"].contains(currentFailure.code) {
            return "The operation could not be confirmed as started. It may still be active."
        }
        return "The operation could not start, so the app pipeline did not run."
    }

    var whatToDo: String {
        guard let currentFailure else { return "Review the operation and try again only when it is safe." }
        guard retryCouldNotStart else { return currentFailure.recommendedAction }
        if currentFailure.safeCause == CombinedFailure.SafeCause.operationInProgress.rawValue {
            return "Wait for the active SideStore operation to finish, then start a fresh attempt."
        }
        if previousFailure != nil {
            return "The retry could not start. \(currentFailure.recommendedAction)"
        }
        return "The operation could not start. \(currentFailure.recommendedAction)"
    }

    var technicalDetails: String {
        let current = currentFailure?.technical ?? "No structured failure record was returned."
        guard retryCouldNotStart, let previousFailure else { return current }
        return "retry_start_failure:\n\(current)\nprevious_attempt_failure:\n\(previousFailure.technical)"
    }

    var retryDisposition: V3RetryDisposition {
        guard let currentFailure else { return .unknown }
        return currentFailure.retryDisposition
    }
}

enum V3OperationRetrySafetyPolicy {
    enum Disposition: Equatable { case retry, alreadyCompleted, outcomeUnknown }

    static func canRetry(backendSettled: Bool?, outcomeUnknown: Bool) -> Bool {
        !outcomeUnknown && backendSettled == true
    }

    static func disposition(state: String?, backendSettled: Bool?, outcomeUnknown: Bool) -> Disposition {
        guard canRetry(backendSettled: backendSettled, outcomeUnknown: outcomeUnknown) else {
            return .outcomeUnknown
        }
        if state == "completed" { return .alreadyCompleted }
        guard ["failed", "cancelled", "requiresSource", "waitingForAuthentication"].contains(state ?? "") else {
            return .outcomeUnknown
        }
        return .retry
    }
}

enum V3OperationRetryButtonPolicy {
    static func title(state: String, retryDisposition: V3RetryDisposition) -> String {
        if state == "cancelled" { return "Retry" }
        return retryDisposition == .unknown ? "Retry (retryability unknown)" : "Retry"
    }
}

struct V3OperationCancellationPresentation: Equatable {
    let message: String
    let whatToDo: String
}

enum V3OperationCancellationPresentationPolicy {
    static func resolve(userRequested: Bool) -> V3OperationCancellationPresentation {
        V3OperationCancellationPresentation(
            message: userRequested ? "The operation was cancelled." : "The operation was cancelled before it finished.",
            whatToDo: userRequested
                ? "The backend confirmed it stopped. Retry when you are ready to run this action again."
                : "The backend confirmed it stopped. Retry if you still need to complete this action.")
    }
}

enum V3OperationMissingSessionPolicy {
    static func unknownTerminal(sessionID: String, knownStarted: Bool) -> [String: Any]? {
        guard knownStarted else { return nil }
        return ["session": sessionID, "state": "failed", "backendSettled": false,
                "outcomeUnknown": true, "stopConfirmed": false,
                "message": "The operation session is no longer available, so its device result cannot be confirmed."]
    }
}

enum V3OperationTerminalAcceptancePolicy {
    static func isSettledTerminal(state: String?, backendSettled: Bool?, stopConfirmed: Bool?,
                                  outcomeUnknown: Bool = false) -> Bool {
        guard !outcomeUnknown else { return false }
        guard ["completed", "failed", "cancelled", "requiresSource", "waitingForAuthentication"]
                .contains(state ?? "") else { return false }
        return backendSettled == true || stopConfirmed == true
    }
}

enum V3OperationCancellationOutcomePolicy {
    static func isCorrelated(expectedSessionID: String, replySessionID: String?) -> Bool {
        replySessionID == expectedSessionID
    }

    static func terminalState(expectedSessionID: String, replySessionID: String?,
                              state: String?, backendSettled: Bool?, stopConfirmed: Bool?,
                              outcomeUnknown: Bool) -> String? {
        guard isCorrelated(expectedSessionID: expectedSessionID, replySessionID: replySessionID),
              V3OperationTerminalAcceptancePolicy.isSettledTerminal(state: state,
                  backendSettled: backendSettled, stopConfirmed: stopConfirmed,
                  outcomeUnknown: outcomeUnknown) else { return nil }
        return state
    }

    static func shouldClearSessionHandle(currentSessionID: String?, expectedSessionID: String,
                                         replySessionID: String?, state: String?,
                                         backendSettled: Bool?, stopConfirmed: Bool?,
                                         outcomeUnknown: Bool) -> Bool {
        currentSessionID == expectedSessionID &&
            terminalState(expectedSessionID: expectedSessionID, replySessionID: replySessionID,
                state: state, backendSettled: backendSettled, stopConfirmed: stopConfirmed,
                outcomeUnknown: outcomeUnknown) != nil
    }
}

enum V3OperationCancellationReplyPolicy {
    static func shouldApplyPollState(userRequestedCancellation: Bool, nextState: String) -> Bool {
        !(userRequestedCancellation && ["working", "awaitingPrompt"].contains(nextState))
    }
}

enum V3OperationStartDispatchPolicy {
    static func provesNotDispatched(resultWasReturned: Bool) -> Bool {
        !resultWasReturned
    }
}

enum V3RefreshTerminalRecoveryPolicy {
    enum Action: Equatable {
        case finalizeVerified
        case finalizeFailed
        case markInterrupted
    }

    static func action(state: String, terminalIntent: String?, manifestIsComplete: Bool,
                       hostHandoffPending: Bool) -> Action? {
        guard !["completed", "failed"].contains(state) else { return nil }
        if terminalIntent == "verified" && manifestIsComplete { return .finalizeVerified }
        if terminalIntent == "failed" { return .finalizeFailed }
        if hostHandoffPending { return nil }
        return ["running", "verifying", "failing"].contains(state) ? .markInterrupted : nil
    }
}

struct V3RefreshRunIdentitySelection: Equatable {
    let runID: String
    let schedulerOwned: Bool

    static func select(schedulerRunID: String?, expectedRunID: String?,
                       activeRunID: String?, newRunID: String) -> Self? {
        if let schedulerRunID {
            guard let parsed = UUID(uuidString: schedulerRunID), parsed.uuidString == schedulerRunID,
                  expectedRunID == schedulerRunID, activeRunID == schedulerRunID else { return nil }
            return Self(runID: schedulerRunID, schedulerOwned: true)
        }
        // A direct AppIntent cannot borrow an active scheduler's run identity.
        guard activeRunID == nil else { return nil }
        guard let generated = UUID(uuidString: newRunID), generated.uuidString == newRunID else { return nil }
        return Self(runID: newRunID, schedulerOwned: false)
    }
}

enum V3DirectRefreshPreflightPolicy {
    static func isBlocked(activeRunID: String?, hostHandoffPending: Bool,
                          uncertainMutationRunID: String?) -> Bool {
        activeRunID != nil || hostHandoffPending || uncertainMutationRunID != nil
    }
}

enum V3DirectRefreshRunClaimPolicy {
    static let defaultsKey = "liveContainerAutoRefreshDirectRunClaim"

    static func isActive(runID: String?, deadline: Date?, now: Date = Date()) -> Bool {
        guard let runID, let parsed = UUID(uuidString: runID), parsed.uuidString == runID,
              let deadline else { return false }
        return deadline > now
    }
}

enum V3RequestRetirementPolicy {
    private static let sessionControls: Set<String> = [
        "opStart", "opPoll", "opAnswer", "opCancel",
        "authPoll", "authRespond"
    ]

    static func shouldRetireServiceIfRequestStaysPending(_ operation: String) -> Bool {
        !sessionControls.contains(operation)
    }
}

enum V3CancellationRecoveryReplyPolicy {
    // Late auth/session-creation replies are not passed through the original
    // result classifier after settlement. Keep their service-retirement timer;
    // ordinary one-shot mutation callbacks retain their terminal recovery path.
    static func mayCancelRetirement(operation: String, requestStillPending: Bool) -> Bool {
        if requestStillPending { return true }
        // A late auth reply has not passed the request's result classifier, so
        // retain recovery until bounded service retirement clears host owners.
        if ["authBegin", "authRetryProvisioning", "authCancel", "refreshAdmissionBegin"].contains(operation) {
            return false
        }
        return true
    }
}

enum V3IdleReadRetirementPolicy {
    static func shouldRetireService(operation: String, hostMutationActive: Bool,
                                    refreshAttemptActive: Bool) -> Bool {
        guard !hostMutationActive, !refreshAttemptActive else { return false }
        // A timed-out authPoll is one lost observation of a live session, not
        // evidence that the in-memory SignInOperation should be discarded.
        return operation != "authPoll"
    }
}

struct V3AuthSessionOwnership {
    private(set) var deadlines: [String: Date] = [:]
    private static let terminalStates: Set<String> = [
        "completed", "authenticatedProvisioningIncomplete", "cancelled", "timedOut", "failed"
    ]

    mutating func register(sessionID: String, deadline: Date, now: Date = Date()) {
        prune(now: now)
        guard let parsed = UUID(uuidString: sessionID), parsed.uuidString == sessionID,
              deadline > now else { return }
        deadlines[sessionID] = deadline
        if deadlines.count > 256 {
            let oldest = deadlines.sorted { $0.value < $1.value }
            for (id, _) in oldest.prefix(deadlines.count - 256) { deadlines.removeValue(forKey: id) }
        }
    }

    mutating func observe(operation: String, sessionID: String, replySessionID: String?,
                          state: String?, now: Date = Date()) {
        prune(now: now)
        guard replySessionID == sessionID, let state, deadlines[sessionID] != nil else { return }
        if Self.terminalStates.contains(state) {
            deadlines.removeValue(forKey: sessionID)
        } else if ["authBegin", "authRetryProvisioning"].contains(operation),
                  ["working", "awaitingPrompt"].contains(state) {
            // A successful new begin returns only after the previous auth task
            // has unwound, so that response supersedes older host ownership.
            deadlines = deadlines.filter { $0.key == sessionID }
        }
    }

    mutating func prune(now: Date = Date()) {
        deadlines = deadlines.filter { $0.value > now }
    }

    mutating func clear(sessionID: String) {
        deadlines.removeValue(forKey: sessionID)
    }

    mutating func reconcile(sessionID: String, authenticationActive: Bool) {
        guard !authenticationActive else { return }
        clear(sessionID: sessionID)
    }

    mutating func clearAll() {
        deadlines.removeAll()
    }

    mutating func hasActiveSession(now: Date = Date()) -> Bool {
        prune(now: now)
        return !deadlines.isEmpty
    }

    func owns(_ sessionID: String, now: Date = Date()) -> Bool {
        deadlines[sessionID].map { $0 > now } == true
    }
}

enum V3ProvisioningResumeAvailabilityPolicy {
    static func canResume(authenticated: Bool, currentAppleID: String?, resumableAppleID: String?,
                          hasSession: Bool = true, hasTeamAccount: Bool = true,
                          teamAccountAppleID: String? = nil) -> Bool {
        guard authenticated, hasSession, hasTeamAccount,
              let currentAppleID, let resumableAppleID else { return false }
        guard let current = V3AuthIdentityBindingPolicy.normalizedOwner(currentAppleID),
              let resumable = V3AuthIdentityBindingPolicy.normalizedOwner(resumableAppleID),
              let teamOwner = V3AuthIdentityBindingPolicy.normalizedOwner(teamAccountAppleID) else { return false }
        return current == resumable && current == teamOwner
    }
}

// V3_AUTH_IDENTITY_BINDING_V1: the stored route remains an upstream fact;
// developer-portal readiness requires a coherent DSID/token session and the
// exact account owner associated with the team being sent to Apple.
enum V3AuthIdentityBindingPolicy {
    static func normalizedOwner(_ value: String?) -> String? {
        guard let value else { return nil }
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return normalized.isEmpty ? nil : normalized
    }

    static func hasTokenBackedRoute(credentialRoutePresent: Bool,
                                    dsid: String?, xcodeToken: String?) -> Bool {
        credentialRoutePresent && dsid?.isEmpty == false && xcodeToken?.isEmpty == false
    }

    static func hasUsableSession(credentialRoutePresent: Bool, dsid: String?,
                                 xcodeToken: String?, sessionDSID: String?,
                                 sessionXcodeToken: String?,
                                 generationBefore: UInt64, generationAfter: UInt64) -> Bool {
        guard hasTokenBackedRoute(credentialRoutePresent: credentialRoutePresent,
                dsid: dsid, xcodeToken: xcodeToken), generationBefore == generationAfter,
              let dsid, let xcodeToken,
              let sessionDSID, !sessionDSID.isEmpty,
              let sessionXcodeToken, !sessionXcodeToken.isEmpty else { return false }
        return dsid == sessionDSID && xcodeToken == sessionXcodeToken
    }

    static func sameCredentialRoute(appleIDBefore: String?, appleIDAfter: String?,
                                    dsidBefore: String?, dsidAfter: String?,
                                    tokenBefore: String?, tokenAfter: String?) -> Bool {
        normalizedOwner(appleIDBefore) == normalizedOwner(appleIDAfter) &&
            dsidBefore == dsidAfter && tokenBefore == tokenAfter
    }

    static func mayUseTeam(sessionOwner: String?, teamOwner: String?) -> Bool {
        guard let sessionOwner = normalizedOwner(sessionOwner),
              let teamOwner = normalizedOwner(teamOwner) else { return false }
        return sessionOwner == teamOwner
    }

    static func resolveColdTeamOwner(storedTeamOwners: [String],
                                     activeTeamIdentifier: String?, requestedTeamIdentifier: String,
                                     activeAccountOwner: String?, sessionOwner: String?) -> String? {
        let activeTeamMatches = activeTeamIdentifier == requestedTeamIdentifier
        if activeTeamMatches, mayUseTeam(sessionOwner: sessionOwner, teamOwner: activeAccountOwner) {
            return normalizedOwner(sessionOwner)
        }
        let owners = Set(storedTeamOwners.compactMap(normalizedOwner))
        return owners.count == 1 ? owners.first : nil
    }

    static func mayFetchTeams(sessionOwner: String?, requestedOwner: String?,
                              generationBefore: UInt64, generationAfter: UInt64,
                              cancelled: Bool = false) -> Bool {
        mayDispatchTeamRequest(sessionOwner: sessionOwner, teamOwner: requestedOwner,
            generationBefore: generationBefore, generationAfter: generationAfter,
            cancelled: cancelled)
    }

    static func mayDispatchTeamRequest(sessionOwner: String?, teamOwner: String?,
                                       generationBefore: UInt64, generationAfter: UInt64,
                                       cancelled: Bool = false) -> Bool {
        mayDispatch(generationBefore: generationBefore, generationAfter: generationAfter,
                    cancelled: cancelled) &&
            mayUseTeam(sessionOwner: sessionOwner, teamOwner: teamOwner)
    }

    static func mayDispatch(generationBefore: UInt64, generationAfter: UInt64,
                            cancelled: Bool = false) -> Bool {
        !cancelled && generationBefore == generationAfter
    }

    static func mayProjectIdentity(generationBefore: UInt64, generationAfter: UInt64) -> Bool {
        generationBefore == generationAfter
    }
}

enum V3ProvisioningResumeIdentityPolicy {
    static func select(authenticatedSessionAppleID: String?, submittedAppleID: String?,
                       activeAppleID: String?) -> String? {
        for candidate in [authenticatedSessionAppleID, submittedAppleID, activeAppleID] {
            guard let value = candidate?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
                  !value.isEmpty else { continue }
            return value
        }
        return nil
    }
}

enum V3ProvisioningResumeExecutionPolicy {
    static func mayUseCachedSignIn(forceProvisioningRetry: Bool) -> Bool {
        !forceProvisioningRetry
    }

    static func mayPromptForCredentials(forceProvisioningRetry: Bool) -> Bool {
        !forceProvisioningRetry
    }
}

enum V3ProvisioningRetryRecoveryPolicy {
    static func availabilityAfterFailure(snapshotConfirmed: Bool,
                                         snapshotAllowsRetry: Bool,
                                         previouslyConfirmedAvailable: Bool) -> Bool {
        snapshotConfirmed ? snapshotAllowsRetry : previouslyConfirmedAvailable
    }
}

enum V3AuthTimeoutReconciliationPolicy {
    static func shouldReconcileAfterTerminal(_ state: String) -> Bool {
        ["timedOut", "failed", "cancelled", "resultUnknown", "promptExpired"].contains(state)
    }
}

struct V3AuthReconciliationPresentation: Equatable {
    let state: String
    let message: String
}

enum V3AuthReconciliationPresentationPolicy {
    static func shouldPreserveActivePrompt(reportedState: String, hasPrompt: Bool,
                                           activeSessionMatches: Bool,
                                           cancellationInProgress: Bool) -> Bool {
        hasPrompt && activeSessionMatches && !cancellationInProgress &&
            ["working", "awaitingPrompt"].contains(reportedState)
    }

    static func resolve(reportedState: String, authenticated: Bool,
                        provisioningIncomplete: Bool,
                        previousFailureMessage: String? = nil,
                        authenticationActive: Bool = false) -> V3AuthReconciliationPresentation {
        guard authenticated else {
            switch reportedState {
            case "timedOut":
                return .init(state: "timedOut", message: "Sign-in timed out. SideStore reports that no account is currently signed in.")
            case "cancelled":
                return .init(state: "cancelled", message: "Sign-in was cancelled. SideStore reports that no account is currently signed in.")
            default:
                return .init(state: reportedState, message: "")
            }
        }
        if authenticationActive {
            return .init(state: "authenticatedProvisioningIncomplete",
                message: "Apple ID signed in successfully. SideStore is still finishing provisioning.")
        }
        switch reportedState {
        case "failed":
            var message = provisioningIncomplete
                ? "The sign-in attempt did not complete. SideStore reports authentication, but device provisioning is incomplete."
                : "The sign-in attempt did not complete. SideStore currently reports an account as signed in."
            if let previousFailureMessage { message += " " + previousFailureMessage }
            return .init(state: "failed", message: message)
        case "timedOut":
            return .init(state: "timedOut", message: provisioningIncomplete
                ? "The sign-in attempt timed out. SideStore reports authentication, but device provisioning is incomplete."
                : "The sign-in attempt timed out. SideStore currently reports an account as signed in.")
        case "cancelled":
            return .init(state: "cancelled", message: provisioningIncomplete
                ? "The sign-in attempt was cancelled. SideStore reports authentication, but device provisioning is incomplete."
                : "The sign-in attempt was cancelled. SideStore currently reports an account as signed in.")
        case "resultUnknown":
            return .init(state: "resultUnknown", message: provisioningIncomplete
                ? "The sign-in result remains unconfirmed. SideStore reports authentication, but device provisioning is incomplete."
                : "The sign-in result remains unconfirmed. SideStore currently reports an account as signed in.")
        case "promptExpired":
            return .init(state: "promptExpired", message: provisioningIncomplete
                ? "The verification session expired. SideStore reports authentication, but device provisioning is incomplete."
                : "The verification session expired. SideStore currently reports an account as signed in.")
        default:
            return provisioningIncomplete
                ? .init(state: "authenticatedProvisioningIncomplete", message: "Apple ID signed in successfully.")
                : .init(state: "completed", message: "")
        }
    }
}

enum V3AuthInactiveSessionResolutionPolicy {
    static func resolve(reportedState: String, authenticated: Bool,
                        authenticationActive: Bool,
                        anotherSessionActive: Bool = false) -> V3AuthReconciliationPresentation? {
        if anotherSessionActive && !authenticated &&
           ["working", "awaitingPrompt", "resultUnknown"].contains(reportedState) {
            return .init(state: "resultUnknown",
                message: "Another Apple sign-in session is active. This request could not be matched to it. Wait for it to finish, then reload status.")
        }
        guard !authenticated, !authenticationActive,
              ["working", "awaitingPrompt", "resultUnknown"].contains(reportedState) else { return nil }
        return .init(state: "failed",
            message: "SideStore confirmed that no account is currently signed in. You can start a new sign-in.")
    }
}

struct V3AuthOtherSessionPresentation: Equatable {
    let state: String
    let message: String
    let clearPrompt: Bool
}

enum V3AuthOtherSessionReconciliationPolicy {
    static func resolve(reportedState: String, authenticated: Bool,
                        anotherSessionActive: Bool) -> V3AuthOtherSessionPresentation? {
        guard anotherSessionActive,
              ["idle", "working", "awaitingPrompt", "resultUnknown",
               "authenticatedProvisioningIncomplete"].contains(reportedState) else { return nil }
        let accountState = authenticated ? "Apple ID is signed in, but " : ""
        return .init(state: "resultUnknown",
            message: accountState + "another sign-in session is active. This request could not be matched to it. Wait for it to finish, then reload status.",
            clearPrompt: true)
    }
}

struct V3AuthReconciliationTicket: Equatable {
    let generation: UInt64
    let sessionID: String?
    let state: String
    let revision: Int
}

struct V3AuthReconciliationGate {
    private(set) var generation: UInt64 = 0

    mutating func invalidate() {
        generation &+= 1
    }

    mutating func begin(sessionID: String?, state: String, revision: Int) -> V3AuthReconciliationTicket {
        generation &+= 1
        return V3AuthReconciliationTicket(generation: generation, sessionID: sessionID,
            state: state, revision: revision)
    }

    func mayApply(_ ticket: V3AuthReconciliationTicket, sessionID: String?,
                  state: String, revision: Int) -> Bool {
        ticket.generation == generation && ticket.sessionID == sessionID &&
            ticket.state == state && ticket.revision == revision
    }

    func ownsSingleReconciliation(after priorGeneration: UInt64) -> Bool {
        generation == (priorGeneration &+ 1)
    }
}

enum V3AuthReconciliationSessionPolicy {
    static func mayStart(expectedSessionID: String?, currentSessionID: String?) -> Bool {
        expectedSessionID == nil || expectedSessionID == currentSessionID
    }
}

enum V3AuthSessionCorrelationPolicy {
    static func isActive(sessionID: String?, authenticationActive: Bool,
                         activeSessionID: String?) -> Bool {
        guard authenticationActive, let sessionID, let activeSessionID else { return false }
        return sessionID == activeSessionID
    }

    static func hasOtherActiveSession(sessionID: String?, authenticationActive: Bool,
                                      activeSessionID: String?) -> Bool {
        guard authenticationActive, let activeSessionID else { return false }
        guard let sessionID else { return true }
        return sessionID != activeSessionID
    }
}

enum V3AuthSnapshotAuthorityPolicy {
    struct Facts: Equatable {
        let authenticated: Bool
        let credentialRoutePresent: Bool
        let provisioningIncomplete: Bool
        let provisioningRetryAvailable: Bool
        let authenticationActive: Bool
        let authenticationSessionID: String?
    }

    static func facts(_ snapshot: V3AuthServiceSnapshot) -> Facts {
        Facts(authenticated: snapshot.identityStable && snapshot.authenticated,
              credentialRoutePresent: snapshot.identityStable && snapshot.credentialRoutePresent,
              provisioningIncomplete: snapshot.identityStable && snapshot.provisioningIncomplete,
              provisioningRetryAvailable: snapshot.identityStable && snapshot.provisioningRetryAvailable,
              authenticationActive: snapshot.authenticationActive,
              authenticationSessionID: snapshot.authenticationSessionID)
    }

    static func isAuthenticated(_ snapshot: [String: Bool]) -> Bool {
        snapshot["authenticated"] == true
    }

    static func needsSignIn(authenticated: Bool) -> Bool { !authenticated }
}

struct V3AccountSessionPresentation: Equatable {
    let showSignIn: Bool
    let showSavedAppleID: Bool
    let showUnverifiedSavedState: Bool
    let showRetainedCertificateGuidance: Bool
    let showSignOut: Bool
}

enum V3AccountSessionPresentationPolicy {
    static func resolve(authenticated: Bool, activeAccountPresent: Bool,
                        activeTeamPresent: Bool, activeCertificatePresent: Bool) -> V3AccountSessionPresentation {
        let hasSavedAccountState = activeAccountPresent || activeTeamPresent
        return V3AccountSessionPresentation(
            showSignIn: !authenticated,
            showSavedAppleID: activeAccountPresent,
            showUnverifiedSavedState: !authenticated && hasSavedAccountState,
            showRetainedCertificateGuidance: !authenticated && activeCertificatePresent,
            // Sign Out clears the saved account/team session, but deliberately
            // retains signing certificates. A certificate alone cannot justify
            // presenting Sign Out as a way to remove stale local state.
            showSignOut: authenticated || hasSavedAccountState)
    }
}

enum V3DeveloperDataActionAvailabilityPolicy {
    static func isEnabled(authenticated: Bool, isLoading: Bool) -> Bool {
        authenticated && !isLoading
    }
}

struct V3AuthSessionUnavailablePresentation: Equatable {
    let state: String
    let message: String
    let provisioningMessage: String?
    let cancellationConfirmed: Bool
}

enum V3AuthSessionUnavailablePolicy {
    static func shouldRetireOwnership(sessionID: String, currentSessionID: String?,
                                      failure: CombinedFailure) -> Bool {
        currentSessionID == sessionID && failure.operation == "signIn" &&
            failure.stage == .authentication && failure.safeCause == .authSessionUnavailable
    }

    static func resolve(authenticated: Bool, provisioningIncomplete: Bool,
                        snapshotConfirmed: Bool, safeMessage: String,
                        recovery: String, anotherSessionActive: Bool = false) -> V3AuthSessionUnavailablePresentation {
        guard snapshotConfirmed else {
            return V3AuthSessionUnavailablePresentation(
                state: "resultUnknown",
                message: "SideStore no longer has the active sign-in session. The current account and provisioning state could not be confirmed. Reload status before continuing.",
                provisioningMessage: nil,
                cancellationConfirmed: false)
        }
        if anotherSessionActive {
            return V3AuthSessionUnavailablePresentation(
                state: "resultUnknown",
                message: "Another Apple sign-in session is active. This request could not be matched to it. Wait for it to finish, then reload status.",
                provisioningMessage: nil,
                cancellationConfirmed: true)
        }
        if authenticated && provisioningIncomplete {
            return V3AuthSessionUnavailablePresentation(
                state: "authenticatedProvisioningIncomplete",
                message: "Apple ID signed in successfully.",
                provisioningMessage: "The saved provisioning session is no longer available. Open Account & Signing to sign in again before retrying setup.",
                cancellationConfirmed: true)
        }
        if authenticated {
            return V3AuthSessionUnavailablePresentation(
                state: "completed",
                message: "SideStore confirmed that the account is signed in.",
                provisioningMessage: nil,
                cancellationConfirmed: true)
        }
        let message = snapshotConfirmed
            ? safeMessage + " " + recovery
            : "SideStore no longer has the active sign-in session and could not confirm the account state. Reload status before starting a new sign-in."
        return V3AuthSessionUnavailablePresentation(
            state: "failed", message: message, provisioningMessage: nil,
            cancellationConfirmed: true)
    }
}

enum V3AuthPollRecoveryPolicy {
    static func isTransientTransportFailure(_ failure: CombinedFailure) -> Bool {
        if failure.safeCause == .authSessionUnavailable { return false }
        let networkTransportCause = failure.safeCause.map {
            [.networkConnectionLost, .networkTimedOut, .networkUnavailable].contains($0)
        } ?? false
        if networkTransportCause {
            return failure.stage == .xpcConnection
        }
        return failure.code == .timedOut ||
            failure.code == .interrupted && failure.stage == .xpcConnection
    }

    static func shouldRetry(_ failure: CombinedFailure, now: Date = Date(),
                            sessionDeadline: Date) -> Bool {
        now < sessionDeadline && isTransientTransportFailure(failure)
    }

    static func shouldFinishTimedOut(_ failure: CombinedFailure, now: Date = Date(),
                                     sessionDeadline: Date) -> Bool {
        now >= sessionDeadline && isTransientTransportFailure(failure)
    }

    static func retryDelay(attempt: Int) -> TimeInterval {
        let backoff: [TimeInterval] = [1, 2, 5, 10]
        return backoff[min(max(0, attempt), backoff.count - 1)]
    }

    static func retryDelay(attempt: Int, remaining: TimeInterval) -> TimeInterval {
        guard remaining.isFinite, remaining > 0 else { return 0 }
        return min(retryDelay(attempt: attempt), remaining)
    }
}

enum V3AuthPollFailureRacePolicy {
    static func shouldIgnore(requestedSessionID: String, currentSessionID: String?,
                             requestedRevision: Int, currentRevision: Int,
                             requestedPromptResponseGeneration: UInt64,
                             currentPromptResponseGeneration: UInt64,
                             promptSubmissionInProgress: Bool) -> Bool {
        currentSessionID == requestedSessionID &&
            (requestedRevision != currentRevision ||
             requestedPromptResponseGeneration != currentPromptResponseGeneration ||
             promptSubmissionInProgress)
    }
}

enum V3AuthPollMonitorRecoveryPolicy {
    static func shouldResumeAfterAmbiguousStart(requestedSessionID: String,
                                                currentSessionID: String?,
                                                activeSessionID: String?,
                                                cancellationInProgress: Bool,
                                                taskCancelled: Bool) -> Bool {
        currentSessionID == requestedSessionID && activeSessionID == requestedSessionID &&
            !cancellationInProgress && !taskCancelled
    }

    static func shouldResume(requestedSessionID: String, currentSessionID: String?,
                             failedPromptRevision: Int,
                             currentPromptRevision: Int,
                             failedPromptResponseGeneration: UInt64,
                             currentPromptResponseGeneration: UInt64,
                             state: String, promptSubmissionInProgress: Bool,
                             activeSessionID: String? = nil,
                             pollFailureIsTransient: Bool = false,
                             cancellationInProgress: Bool, taskCancelled: Bool,
                             reconciliationWasSuperseded: Bool = false,
                             now: Date = Date(), sessionDeadline: Date) -> Bool {
        let authenticationActive = activeSessionID == requestedSessionID
        let anotherSessionActive = activeSessionID != nil && !authenticationActive
        guard currentSessionID == requestedSessionID,
              !anotherSessionActive,
              !cancellationInProgress, !taskCancelled,
              (["working", "awaitingPrompt"].contains(state) ||
                (authenticationActive && ["completed", "authenticatedProvisioningIncomplete"].contains(state))) else { return false }
        _ = now
        _ = sessionDeadline // PollLoop owns deadline terminalization on resume.
        return failedPromptRevision != currentPromptRevision ||
            failedPromptResponseGeneration != currentPromptResponseGeneration ||
            promptSubmissionInProgress || pollFailureIsTransient || reconciliationWasSuperseded ||
            authenticationActive
    }
}

public struct V3ShortcutRefreshRequest: Equatable {
    public let requestID: String
    public let origin: String

    public init?(userInfo: [AnyHashable: Any]?) {
        guard let userInfo,
              let value = userInfo["requestID"] as? String,
              let uuid = UUID(uuidString: value), uuid.uuidString == value,
              let origin = userInfo["origin"] as? String,
              V3RefreshRunCorrelation.allowedManualOrigins.contains(origin) else { return nil }
        self.requestID = uuid.uuidString
        self.origin = origin
    }

    public static func make() -> V3ShortcutRefreshRequest {
        V3ShortcutRefreshRequest(requestID: UUID().uuidString, origin: "manualUnknown")
    }

    private init(requestID: String, origin: String) {
        self.requestID = requestID
        self.origin = origin
    }

    public var userInfo: [AnyHashable: Any] {
        ["requestID": requestID, "origin": origin]
    }
}

public struct V3RefreshRunCorrelation: Equatable {
    public static let allowedManualOrigins: Set<String> = [
        "home", "refreshManager", "setupAssistant", "deadlineAlarm", "vpnReturn", "manualUnknown"
    ]

    public let runID: String
    public let requestID: String?
    public let origin: String

    public static func make(source: String, manual: Bool, requestID: String?,
                            manualOrigin: String?, runID: UUID) -> V3RefreshRunCorrelation {
        guard manual else {
            return V3RefreshRunCorrelation(runID: runID.uuidString, requestID: nil, origin: source)
        }
        let canonicalRequest: String
        if let requestID, let parsed = UUID(uuidString: requestID) {
            canonicalRequest = parsed.uuidString
        } else {
            canonicalRequest = UUID().uuidString
        }
        let canonicalOrigin: String
        if let manualOrigin, allowedManualOrigins.contains(manualOrigin) {
            canonicalOrigin = manualOrigin
        } else if source == "alarm_action" {
            canonicalOrigin = "deadlineAlarm"
        } else if source == "vpn_return" {
            canonicalOrigin = "vpnReturn"
        } else {
            canonicalOrigin = "manualUnknown"
        }
        return V3RefreshRunCorrelation(runID: runID.uuidString, requestID: canonicalRequest,
                                       origin: canonicalOrigin)
    }

    private init(runID: String, requestID: String?, origin: String) {
        self.runID = runID
        self.requestID = requestID
        self.origin = origin
    }
}

enum V3RefreshIntentStartPolicy {
    static func create<T>(_ factory: () throws -> T,
                          continuation: CheckedContinuation<Void, Error>,
                          classify: (Error) -> Error = { $0 }) -> T? {
        do {
            return try factory()
        } catch {
            continuation.resume(throwing: classify(error))
            return nil
        }
    }
}

enum V3ServiceReadinessRetryPolicy {
    static func retryable(operation: String, stage: CombinedFailure.Stage,
                          code: CombinedFailure.Code, typedNotReady: Bool) -> Bool? {
        guard typedNotReady, operation == "snapshot", stage == .serviceReadiness,
              code == .notReady else { return nil }
        return true
    }
}

enum V3PairingImportFailurePolicy {
    static func shouldOfferFileRetry(operation: String, stage: String, safeCause: String?) -> Bool {
        operation == "pairingImportData" &&
            ((stage == "pairing" && safeCause == "invalidPairingFile") ||
             (stage == "filePreparation" && safeCause == "pairingFilePreparationFailed"))
    }
}

enum V3SetupTestAttemptPolicy {
    static func mayApply(capturedAttemptID: String, currentAttemptID: String?,
                         taskCancelled: Bool) -> Bool {
        !taskCancelled && currentAttemptID == capturedAttemptID
    }
}

enum V3SetupTestRequestDisposition: Equatable {
    case startNew
    case resumeExisting(String)
    case waitForActiveRun
}

enum V3SetupTestRequestPolicy {
    static let startGracePeriod: TimeInterval = 30

    static func select(pendingRequestID: String?, pendingAge: TimeInterval,
                       pendingState: String?, activeRunID: String?,
                       activeRunRequestID: String?) -> V3SetupTestRequestDisposition {
        // A terminal correlated request is read-only to consume. Resolve it
        // before considering a different run that began after it completed.
        if let pendingRequestID, ["completed", "failed"].contains(pendingState ?? "") {
            return .resumeExisting(pendingRequestID)
        }
        if let pendingRequestID, let activeRunID, !activeRunID.isEmpty,
           activeRunRequestID != pendingRequestID {
            return .waitForActiveRun
        }
        if let pendingRequestID {
            if pendingState != nil {
                return .resumeExisting(pendingRequestID)
            }
            if let activeRunID, !activeRunID.isEmpty {
                return activeRunRequestID == pendingRequestID
                    ? .resumeExisting(pendingRequestID) : .waitForActiveRun
            }
            return pendingAge < startGracePeriod
                ? .resumeExisting(pendingRequestID) : .startNew
        }
        return (activeRunID?.isEmpty == false) ? .waitForActiveRun : .startNew
    }
}

struct V3AuthPollFailure: Error {
    let underlying: Error
    let sessionID: String
    let promptResponseGeneration: UInt64
    let promptRevision: Int
}

enum V3AuthAttemptFailureCommitPolicy {
    static func mayCommit(requestedSessionID: String, currentSessionID: String?,
                          capturedPromptResponseGeneration: UInt64,
                          currentPromptResponseGeneration: UInt64,
                          reconciliationGenerationBefore: UInt64,
                          currentReconciliationGeneration: UInt64,
                          cancellationInProgress: Bool, taskCancelled: Bool) -> Bool {
        !cancellationInProgress && !taskCancelled &&
            currentSessionID == requestedSessionID &&
            currentPromptResponseGeneration == capturedPromptResponseGeneration &&
            currentReconciliationGeneration == (reconciliationGenerationBefore &+ 1)
    }

    static func shouldPreserveAuthoritativeAccountState(snapshotConfirmed: Bool,
                                                        authenticated: Bool,
                                                        state: String) -> Bool {
        snapshotConfirmed && authenticated &&
            ["completed", "authenticatedProvisioningIncomplete"].contains(state)
    }

    static func shouldCommitConfirmedSignedOutFailure(snapshotConfirmed: Bool,
                                                       authenticated: Bool,
                                                       hasSession: Bool,
                                                       cancellationConfirmed: Bool,
                                                       state: String) -> Bool {
        snapshotConfirmed && !authenticated && !hasSession && cancellationConfirmed && state == "failed"
    }
}

enum V3AuthCancellationRetryPolicy {
    static func canRetry(isCancelling: Bool, cancellationConfirmed: Bool,
                         hasSession: Bool) -> Bool {
        !isCancelling && !cancellationConfirmed && hasSession
    }
}

enum V3AuthUnknownResultRecoveryAction: Equatable {
    case cancelSession
    case reloadStatus
    case none
}

enum V3AuthUnknownResultRecoveryPolicy {
    static func action(isCancelling: Bool, cancellationConfirmed: Bool,
                       hasSession: Bool) -> V3AuthUnknownResultRecoveryAction {
        guard !isCancelling else { return .none }
        if !hasSession { return .reloadStatus }
        return cancellationConfirmed ? .none : .cancelSession
    }
}

enum V3AuthUnknownResultReconciliationPolicy {
    static func reportedState(originalState: String, hasSession: Bool,
                              authenticated: Bool) -> String {
        originalState == "resultUnknown" && !hasSession && !authenticated
            ? "working" : originalState
    }
}

enum V3AuthSessionAdmissionPolicy {
    static func mayStartNewSession(hasActiveSession: Bool) -> Bool {
        !hasActiveSession
    }
}

struct V3AuthProvisioningRecoveryPresentation: Equatable {
    let showCancellationInstruction: Bool
    let showRetryProvisioning: Bool
    let showFinishLater: Bool
    let blockedByActiveSession: Bool
}

enum V3AuthProvisioningRecoveryPolicy {
    static func resolve(state: String, hasSession: Bool, signedIn: Bool,
                        provisioningRetryAvailable: Bool, isCancelling: Bool,
                        cancellationConfirmed: Bool,
                        authenticationActive: Bool = false) -> V3AuthProvisioningRecoveryPresentation {
        let noSessionResumeIsSafe = state == "resultUnknown" && !hasSession && signedIn &&
            provisioningRetryAvailable && !authenticationActive
        let retryAllowed = !isCancelling && cancellationConfirmed && provisioningRetryAvailable &&
            !authenticationActive &&
            (state != "resultUnknown" || noSessionResumeIsSafe)
        return V3AuthProvisioningRecoveryPresentation(
            showCancellationInstruction: state == "resultUnknown" && hasSession,
            showRetryProvisioning: retryAllowed,
            showFinishLater: signedIn && (!hasSession || state != "resultUnknown"),
            blockedByActiveSession: authenticationActive)
    }
}

enum V3AuthCancellationFeedbackPolicy {
    static func statusLabel(isCancelling: Bool, normalLabel: String) -> String {
        isCancelling ? "Cancelling..." : normalLabel
    }

    static func message(isCancelling: Bool) -> String? {
        isCancelling ? "Cancellation requested. Waiting for SideStore to confirm the sign-in stopped." : nil
    }
}

enum V3AuthStatusTextPolicy {
    static func label(state: String, isSignedIn: Bool,
                      provisioningFinishedLater: Bool) -> String {
        switch state {
        case "completed": return "Signed in"
        case "authenticatedProvisioningIncomplete":
            return provisioningFinishedLater ? "Signed in" : "Signed in, provisioning needs attention"
        case "awaitingPrompt": return "Needs your input"
        case "failed": return "Failed"
        case "cancelled": return "Cancelled"
        case "timedOut": return "Timed out"
        case "promptExpired": return "Verification expired"
        case "resultUnknown": return "Result not confirmed"
        case "working": return isSignedIn ? "Finishing provisioning..." : "Working..."
        default: return "Not started"
        }
    }

    static func accountLabel(state: String, isSignedIn: Bool) -> String {
        if state == "resultUnknown" {
            return isSignedIn
                ? "Last confirmed account status: signed in"
                : "Current account status is unconfirmed"
        }
        guard isSignedIn else { return "" }
        if state == "completed" || state == "authenticatedProvisioningIncomplete" {
            return "Signed in successfully"
        }
        return "Account currently signed in"
    }
}

enum V3AuthFailureDiagnosticsPolicy {
    static func shouldShowTerminalDetails(state: String, hasPrompt: Bool,
                                          hasFailure: Bool) -> Bool {
        hasFailure && !hasPrompt && ["failed", "timedOut", "promptExpired", "resultUnknown"]
            .contains(state)
    }

    static func render(_ failure: [String: Any], underlyingCode: Int?,
                       retryableValue: Bool?) -> String {
        let kind = failure["kind"] as? String ?? ""
        let stage = failure["stage"] as? String ?? ""
        let code = failure["code"] as? String ?? ""
        let correlation = failure["correlationID"] as? String ?? ""
        let underlyingDomain = failure["underlyingDomain"] as? String ?? ""
        let codeText = underlyingCode.map(String.init) ?? "unknown"
        let retryableText = retryableValue.map { $0 ? "yes" : "no" } ?? "unknown"
        return "kind=\(kind) stage=\(stage) code=\(code) correlation=\(correlation) underlying=\(underlyingDomain)/\(codeText) retryable=\(retryableText)"
    }
}

enum V3SignInFailureRoutingPolicy {
    static func shouldOpenSignIn(stage: CombinedFailure.Stage,
                                 safeCause: CombinedFailure.SafeCause?) -> Bool {
        stage == .authentication && safeCause != .keychainSignOutFailed
    }
}

enum V3AuthTerminalFailureAction: Equatable {
    case beginNewSignIn(title: String)
    case repairAppleAccount
    case useAppSpecificPassword
    case blocked
}

enum V3AuthTerminalFailureActionPolicy {
    static func resolve(kind: String?, retryable: Bool?) -> V3AuthTerminalFailureAction {
        switch kind {
        case "accountRepairRequired": return .repairAppleAccount
        case "appSpecificPasswordRequired": return .useAppSpecificPassword
        default: break
        }
        if retryable == false { return .blocked }
        switch kind {
        case "rateLimited": return .beginNewSignIn(title: "Start New Sign-In")
        case "invalidCredentials": return .beginNewSignIn(title: "Check Password and Start New Sign-In")
        case "invalidCode": return .beginNewSignIn(title: "Start New Sign-In to Enter a New Code")
        case "serviceUnavailable", "anisette", "anisetteFailure", "network", "networkFailure":
            return .beginNewSignIn(title: "Start New Sign-In")
        default: break
        }
        if retryable == nil || kind == "unknown" { return .beginNewSignIn(title: "Start New Sign-In") }
        return .beginNewSignIn(title: "Try Sign-In Again")
    }

    static func guidance(kind: String?, retryable: Bool?) -> String? {
        switch resolve(kind: kind, retryable: retryable) {
        case .repairAppleAccount:
            return "Resolve the account issue shown by Apple, then begin a new sign-in."
        case .useAppSpecificPassword:
            return "Create an app-specific password for this authentication path, then enter it in the password prompt."
        case .blocked:
            return "This failure is not marked safe to retry. Resolve the displayed prerequisite and review Diagnostics."
        case .beginNewSignIn(_) where kind == "rateLimited":
            return "Apple is limiting sign-in attempts. Wait before starting a new sign-in."
        case .beginNewSignIn(_) where kind == "invalidCode":
            return "This sign-in attempt ended. Start a new sign-in; Apple will request a fresh verification code after credentials are accepted."
        case .beginNewSignIn(_) where kind == "serviceUnavailable":
            return "Apple's authentication service is temporarily unavailable. Wait for it to recover, then start a new sign-in."
        case .beginNewSignIn(_) where kind == "anisette" || kind == "anisetteFailure":
            return "SideStore could not obtain Anisette data. Check Anisette Servers in Settings, then start a new sign-in."
        case .beginNewSignIn(_) where kind == "network" || kind == "networkFailure":
            return "The connection to Apple's authentication service failed. Check your internet connection, then start a new sign-in."
        case .beginNewSignIn(_) where kind == "unknown" || (kind == nil && retryable == nil):
            return "The exact cause or retry safety could not be confirmed. Starting again creates a new attempt and may not resolve the previous failure."
        case .beginNewSignIn(_):
            return nil
        }
    }
}

enum V3AuthRepairURLPolicy {
    static let safeMessage = "Apple needs account attention before sign-in can continue."

    static func promptField(urlToken: String) -> [String: String] {
        ["key": "urlToken", "label": "Open Apple Account Repair",
         "secure": "false", "value": urlToken]
    }

    static func openableURL(_ rawValue: String) -> URL? {
        guard rawValue.count <= 2_048,
              let components = URLComponents(string: rawValue),
              components.scheme?.lowercased() == "https",
              let host = components.host?.lowercased(),
              host == "apple.com" || host.hasSuffix(".apple.com"),
              components.port == nil || components.port == 443,
              components.user == nil, components.password == nil,
              let url = components.url else { return nil }
        return url
    }
}

struct V3AuthAttemptFailureNotice: Equatable {
    private(set) var message = ""
    private(set) var technicalDetails = ""

    mutating func record(snapshotConfirmed: Bool, authenticated: Bool,
                         failureMessage: String, technicalDetails: String) {
        guard !failureMessage.isEmpty else { return }
        if authenticated {
            message = "The sign-in attempt could not be confirmed. SideStore currently reports an account as signed in. \(failureMessage)"
        } else if snapshotConfirmed {
            message = "The sign-in attempt could not be confirmed. SideStore confirms no account is currently signed in. \(failureMessage)"
        } else {
            message = "The sign-in attempt could not be confirmed. SideStore could not confirm whether sign-in completed. \(failureMessage)"
        }
        self.technicalDetails = technicalDetails
    }

    mutating func clear() {
        message = ""
        technicalDetails = ""
    }
}

enum V3AuthAttemptStartFailurePolicy {
    static func confirmedNotDispatched(_ failure: CombinedFailure,
                                       operation: String = "authBegin") -> CombinedFailure {
        let underlying: NSError? = failure.underlyingDomain == "none" && failure.underlyingCode == 0
            ? nil : NSError(domain: failure.underlyingDomain, code: failure.underlyingCode)
        let cause: CombinedFailure.SafeCause
        if failure.safeCause == .responseCapacityUnavailable {
            cause = .authResponseCapacityUnavailable
        } else if failure.safeCause == .operationInProgress {
            cause = .operationInProgress
        } else {
            cause = operation == "authRetryProvisioning"
                ? .authProvisioningRetryNotDispatched : .authAttemptNotDispatched
        }
        return CombinedFailure(operation: "signIn", stage: failure.stage, code: failure.code,
            id: failure.correlationID, underlying: underlying,
            retryable: true, safeCause: cause)
    }

    static func isConfirmedNotDispatched(_ failure: CombinedFailure) -> Bool {
        failure.safeCause == .authAttemptNotDispatched ||
            failure.safeCause == .authProvisioningRetryNotDispatched ||
            failure.safeCause == .authResponseCapacityUnavailable ||
            failure.safeCause == .operationInProgress
    }
}

enum V3AuthProvisioningRetryDispatchPolicy {
    static func isConfirmedNotDispatched(_ failure: CombinedFailure) -> Bool {
        failure.safeCause == .authProvisioningRetryNotDispatched ||
            failure.safeCause == .authResponseCapacityUnavailable ||
            failure.safeCause == .operationInProgress
    }

    static func whatHappened(_ failure: CombinedFailure) -> String {
        if failure.safeCause == .authResponseCapacityUnavailable {
            return "SideStore could not start the provisioning retry because it could not reserve a safe response slot."
        }
        if failure.safeCause == .operationInProgress {
            return "Another sign-in or provisioning attempt is already active."
        }
        return failure.safeMessage
    }
}

enum V3AuthSessionExpiryPolicy {
    static func response(authenticated: Bool, resumable: Bool = false) -> [String: Any] {
        if authenticated {
            return ["state": "authenticatedProvisioningIncomplete", "authenticated": true,
                    "resumable": resumable,
                    "message": "Apple ID sign-in succeeded, but provisioning did not finish before the session timed out."]
        }
        return ["state": "timedOut", "authenticated": false,
                "message": "Sign-in timed out. Start a new sign-in when you are ready."]
    }
}

enum V3ServiceMutationAdmissionPolicy {
    static func hasConflictingOperationMutation(operation: String, target: String,
                                                activeOperationID: String?) -> Bool {
        guard let activeOperationID else { return false }
        return !(target == activeOperationID &&
            ["opPoll", "opAnswer", "opCancel"].contains(operation))
    }

    static func admits(isMutation: Bool, anotherMutationActive: Bool,
                       authenticationActive: Bool, isAuthContinuation: Bool,
                       responseCapacityAvailable: Bool,
                       refreshActive: Bool = false,
                       isRefreshRelease: Bool = false) -> Bool {
        guard isMutation else { return true }
        guard !anotherMutationActive, responseCapacityAvailable else { return false }
        guard !refreshActive || isRefreshRelease else { return false }
        return !authenticationActive || isAuthContinuation
    }

    static func permitsAuthenticationControl(_ operation: String,
                                              ownsActiveSession: Bool,
                                              authenticationActive: Bool) -> Bool {
        if ["authBegin", "authRetryProvisioning"].contains(operation) {
            return V3AuthSessionAdmissionPolicy.mayStartNewSession(
                hasActiveSession: authenticationActive)
        }
        return ownsActiveSession && ["authRespond", "authCancel"].contains(operation)
    }

    static func ownsRefreshAdmissionControl(operation: String, target: String,
                                             activeRunID: String?, refreshAttemptActive: Bool,
                                             anotherHostMutationActive: Bool = false,
                                             userConfirmedReconciliation: Bool = false) -> Bool {
        if operation == "refreshAdmissionReconcile" {
            return userConfirmedReconciliation &&
                UUID(uuidString: target)?.uuidString == target
        }
        return ["refreshAdmissionBegin", "refreshAdmissionEnd"].contains(operation) &&
            refreshAttemptActive && !anotherHostMutationActive && !target.isEmpty && activeRunID == target
    }
}

enum V3ServiceMutationBusyCausePolicy {
    static func safeCause(operation: String, anotherMutationActive: Bool,
                          responseCapacityAvailable: Bool, refreshActive: Bool,
                          refreshRelease: Bool, authenticationActive: Bool,
                          isAuthContinuation: Bool) -> CombinedFailure.SafeCause {
        let ownershipConflict = anotherMutationActive || (refreshActive && !refreshRelease) ||
            (authenticationActive && !isAuthContinuation)
        if !ownershipConflict && !responseCapacityAvailable {
            return .responseCapacityUnavailable
        }
        if operation == "sourceRemoveConfirmed" { return .sourceRemoveBusy }
        return .operationInProgress
    }
}

struct V3RefreshAdmissionLease {
    static let nativeRefreshTimeout: TimeInterval = 600
    static let retirementGrace: TimeInterval = 60
    static let lifetime: TimeInterval = nativeRefreshTimeout + retirementGrace
    static let nativeRefreshTimeoutNanoseconds: UInt64 = 600_000_000_000

    private(set) var runID: String?
    private(set) var requestID: String?
    private(set) var expiresAt: Date?
    private(set) var ownerLost = false

    var isActive: Bool { runID != nil }

    mutating func expire(now: Date = Date()) -> Bool {
        guard let expiresAt, expiresAt <= now, !ownerLost else { return false }
        ownerLost = true
        self.expiresAt = nil
        return true
    }

    mutating func acquire(runID: String, requestID: String,
                          authenticationActive: Bool,
                          anotherMutationActive: Bool,
                          now: Date = Date()) -> Bool {
        _ = expire(now: now)
        guard let parsed = UUID(uuidString: runID), parsed.uuidString == runID,
              let parsedRequest = UUID(uuidString: requestID), parsedRequest.uuidString == requestID,
              self.runID == nil, !authenticationActive, !anotherMutationActive,
              Self.lifetime > 0 else { return false }
        self.runID = runID
        self.requestID = requestID
        expiresAt = now.addingTimeInterval(Self.lifetime)
        ownerLost = false
        return true
    }

    func owns(_ candidate: String) -> Bool { runID == candidate }

    mutating func restoreLost(runID: String) -> Bool {
        guard self.runID == nil,
              let parsed = UUID(uuidString: runID), parsed.uuidString == runID else { return false }
        self.runID = runID
        requestID = nil
        expiresAt = nil
        ownerLost = true
        return true
    }

    @discardableResult
    mutating func release(runID: String) -> Bool {
        guard self.runID == runID else { return false }
        self.runID = nil
        requestID = nil
        expiresAt = nil
        ownerLost = false
        return true
    }

    @discardableResult
    mutating func release(requestID: String) -> Bool {
        guard self.requestID == requestID else { return false }
        runID = nil
        self.requestID = nil
        expiresAt = nil
        ownerLost = false
        return true
    }

    @discardableResult
    mutating func reconcileAfterDeviceCheck(runID: String, userConfirmed: Bool) -> Bool {
        guard ownerLost, userConfirmed, owns(runID) else { return false }
        return release(runID: runID)
    }
}

enum V3KnownSourcePreflightPolicy {
    static let maximumAge: TimeInterval = 6 * 60 * 60

    static func shouldRefresh(hasCachedBlocklist: Bool, lastSuccessfulUpdate: Date?,
                              now: Date = Date(),
                              maximumAge: TimeInterval = V3KnownSourcePreflightPolicy.maximumAge) -> Bool {
        guard hasCachedBlocklist, let lastSuccessfulUpdate,
              lastSuccessfulUpdate <= now,
              maximumAge > 0 else { return true }
        return now.timeIntervalSince(lastSuccessfulUpdate) >= maximumAge
    }
}

enum V3OperationSessionCorrelationPolicy {
    static func requestSessionID(operation: String, target: String,
                                 payload: [String: Any]) -> String? {
        if operation == "opStart" { return payload["session"] as? String }
        if ["opPoll", "opAnswer", "opCancel"].contains(operation) {
            return target.isEmpty ? nil : target
        }
        return nil
    }

    static func matches(operation: String, target: String, requestedStartSession: String?,
                        resultSession: String?) -> Bool {
        guard ["opStart", "opPoll", "opAnswer", "opCancel"].contains(operation) else { return true }
        let expected = operation == "opStart" ? requestedStartSession : target
        guard let expected, !expected.isEmpty else { return false }
        return resultSession == expected
    }
}

struct V3ServiceRecoveryAdmissionDecision: Equatable {
    let recoveryControl: Bool
    let matchingPreparedStart: Bool
    let matchingPreparedReservation: Bool
    let blocksMutation: Bool
    let refreshRelease: Bool
}

enum V3ServiceRecoveryAdmissionPolicy {
    private static func strictBoolean(_ value: Any?) -> Bool? {
        guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        return number.boolValue
    }

    static func decide(operation: String, target: String, payload: [String: Any],
                       operationSessionID: String?, recovery: V3OperationRecoveryRecord?,
                       recoveryReadFailed: Bool, refreshOwnerLost: Bool) -> V3ServiceRecoveryAdmissionDecision {
        let refreshRecordMatches = recovery?.kind == "refreshAll" && recovery?.sessionID == target
        let refreshTerminalState = payload["state"] as? String
        let hasRefreshTerminal = ["completed", "failed", "notDispatched"].contains(refreshTerminalState ?? "")
        let userConfirmed = strictBoolean(payload["userConfirmed"]) == true
        let operationControl = recovery?.kind != "refreshAll" &&
            ((["opPoll", "opAnswer", "opCancel"].contains(operation) &&
              operationSessionID == recovery?.sessionID) ||
             (operation == "opRecoveryReconcile" && target == recovery?.sessionID && userConfirmed))
        let refreshControl = refreshRecordMatches &&
            ((operation == "refreshAdmissionEnd" && hasRefreshTerminal) ||
             (operation == "refreshAdmissionReconcile" && userConfirmed))
        let unreadableRecoveryControl = operation == "recoveryDiscardUnreadable" &&
            recoveryReadFailed && recovery == nil && userConfirmed
        let recoveryControl = operationControl || refreshControl || unreadableRecoveryControl
        let matchingPreparedStart = operation == "opStart" && recovery?.kind != "refreshAll" &&
            operationSessionID == recovery?.sessionID && payload["kind"] as? String == recovery?.kind &&
            recovery?.phase == .prepared
        let matchingPreparedReservation = operation == "opRecoveryPrepare" && recovery?.kind != "refreshAll" &&
            payload["session"] as? String == recovery?.sessionID && payload["kind"] as? String == recovery?.kind &&
            recovery?.phase == .prepared
        let blocksMutation = (recoveryReadFailed && !unreadableRecoveryControl) ||
            (recovery != nil && !recoveryControl && !matchingPreparedStart && !matchingPreparedReservation)
        let unreadableRefreshRelease = operation == "recoveryDiscardUnreadable" &&
            recoveryReadFailed && refreshOwnerLost && userConfirmed
        let refreshRelease = unreadableRefreshRelease || (refreshRecordMatches &&
            ((operation == "refreshAdmissionEnd" && hasRefreshTerminal) ||
             (operation == "refreshAdmissionReconcile" && refreshOwnerLost && userConfirmed)))
        return V3ServiceRecoveryAdmissionDecision(recoveryControl: recoveryControl,
            matchingPreparedStart: matchingPreparedStart,
            matchingPreparedReservation: matchingPreparedReservation,
            blocksMutation: blocksMutation, refreshRelease: refreshRelease)
    }
}

enum V3OperationCancelKnownStartedPolicy {
    static func resolve(sessionID: String, hostReportedKnownStarted: Bool,
                        recovery: V3OperationRecoveryRecord?) -> Bool {
        guard let recovery, recovery.sessionID == sessionID,
              recovery.kind != "refreshAll" else { return hostReportedKnownStarted }
        return recovery.phase == .dispatched
    }
}

enum V3SharedKeychainAccessGroupPolicy {
    static func sharedGroup(in entitledGroups: [String]) -> String? {
        entitledGroups.first(where: { $0.hasSuffix(".com.kdt.livecontainer.shared") })
    }
}
