import Foundation

// Only capability and bounded state cross the service boundary. Pending keys,
// owner IDs and certificate contents stay in the service's private storage.
enum V3PendingCertificateRecoveryState: String {
    case unavailable, none, signInRequired, ownerMismatch, ready, activationRequired, storageUnverified

    static func decode(protocolVersion: Int?, rawState: String?) -> Self {
        guard protocolVersion != nil || rawState != nil else { return .unavailable }
        guard protocolVersion == 1, let rawState,
              let state = Self(rawValue: rawState), state != .unavailable else { return .storageUnverified }
        return state
    }
    var requiresAttention: Bool { self != .none && self != .unavailable }
    func mayReconcile(authenticated: Bool, hasSession: Bool, isCancelling: Bool,
                      authenticationActive: Bool, checkingStorage: Bool) -> Bool {
        self == .ready && authenticated && !hasSession && !isCancelling &&
            !authenticationActive && !checkingStorage
    }
    func mayFinishActivation(authenticated: Bool, hasSession: Bool, isCancelling: Bool,
                             authenticationActive: Bool, checkingStorage: Bool) -> Bool {
        self == .activationRequired && authenticated && !hasSession && !isCancelling &&
            !authenticationActive && !checkingStorage
    }
    var guidance: String {
        switch self {
        case .activationRequired:
            return "This pending certificate is ready for activation. Sign in again with the original Apple ID to finish signing setup."
        case .signInRequired:
            return "Sign in with the Apple ID that started this certificate request, then reconcile the pending request."
        case .ownerMismatch:
            return "This pending request belongs to another Apple ID. Return to Account and Signing, sign out, then sign in with the original Apple ID."
        case .ready:
            return "Check the original certificate request before creating or revoking certificates. Reconciliation does not submit another request or revoke a certificate."
        case .storageUnverified:
            return "The pending certificate state could not be verified. Reload status before continuing. The saved request has not been discarded."
        case .none, .unavailable:
            return ""
        }
    }
}


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

struct V3HostDirectRecoveryRecord: Equatable {
    enum Phase: String { case prepared, dispatched, terminal, unknown }

    let requestID: String
    let operation: String
    let phase: Phase
    let resultState: String?
    let postcondition: V3DirectRecoveryPostcondition?

    init?(_ rawValue: Any?) {
        guard let raw = rawValue as? [String: Any],
              Set(raw.keys).isSubset(of: ["requestID", "operation", "phase", "resultState", "postcondition"]),
              let requestID = raw["requestID"] as? String,
              UUID(uuidString: requestID)?.uuidString == requestID,
              let operation = raw["operation"] as? String,
              Self.operations.contains(operation),
              let phase = (raw["phase"] as? String).flatMap(Phase.init(rawValue:)),
              !raw.keys.contains("resultState") || raw["resultState"] is String else { return nil }
        let resultState = raw["resultState"] as? String
        guard resultState.map({ ["completed", "createdAndStored",
              "remoteCreatedLocalStorageUnverified"].contains($0) }) ?? true,
              (phase == .terminal) == (resultState != nil) else { return nil }
        let postcondition: V3DirectRecoveryPostcondition?
        if raw.keys.contains("postcondition") {
            guard let rawPostcondition = raw["postcondition"] as? String,
                  let parsed = V3DirectRecoveryPostcondition(rawValue: rawPostcondition) else { return nil }
            postcondition = parsed
        } else {
            postcondition = nil
        }
        self.requestID = requestID
        self.operation = operation
        self.phase = phase
        self.resultState = resultState
        self.postcondition = postcondition
    }

    init?(snapshotValue: Any?) {
        guard let raw = snapshotValue as? [String: Any], !raw.keys.contains("postcondition"),
              let parsed = V3HostDirectRecoveryRecord(raw) else { return nil }
        self = parsed
    }

    init?(inspectionReply: [String: Any]) {
        guard let parsed = V3HostDirectRecoveryRecord(inspectionReply),
              parsed.postcondition != nil else { return nil }
        self = parsed
    }

    static let operations: Set<String> = [
        "certCreate", "certRevoke", "sourceAddConfirmed", "sourceRemoveConfirmed",
        "pairingImportData", "settingsSet", "accountImport"
    ]
}

enum V3DirectRecoveryPostcondition: String {
    case achieved, notAchieved, indeterminate, manualCheckRequired, notDispatched
}

enum V3DirectRecoveryHostPolicy {
    static func mayOfferUserConfirmation(_ record: V3HostDirectRecoveryRecord,
                                        postcondition: V3DirectRecoveryPostcondition?) -> Bool {
        record.phase != .dispatched && postcondition != nil
    }

    static func mayAcknowledgeSuccessfulResponse(operation: String,
                                                  result: [String: Any]) -> Bool {
        guard V3HostDirectRecoveryRecord.operations.contains(operation) else { return false }
        if operation == "certCreate" {
            return result["outcome"] as? String == "createdAndStored"
        }
        if operation == "accountImport" { return false }
        return true
    }

    static func mayAcknowledgeInspectedTerminal(_ record: V3HostDirectRecoveryRecord,
                                                 postcondition: V3DirectRecoveryPostcondition) -> Bool {
        guard record.phase == .terminal, postcondition == .achieved else { return false }
        if record.operation == "certCreate",
           record.resultState == "remoteCreatedLocalStorageUnverified" { return false }
        return true
    }
}

enum V3DirectRecoveryPresentationPolicy {
    static func operationName(_ operation: String) -> String {
        switch operation {
        case "certCreate": return "certificate creation"
        case "certRevoke": return "certificate revocation"
        case "sourceAddConfirmed": return "source add"
        case "sourceRemoveConfirmed": return "source removal"
        case "pairingImportData": return "pairing import"
        case "settingsSet": return "setting change"
        case "accountImport": return "account import"
        default: return "SideStore request"
        }
    }

    static func explanation(record: V3HostDirectRecoveryRecord,
                            postcondition: V3DirectRecoveryPostcondition?) -> String {
        if record.operation == "certCreate",
           record.resultState == "remoteCreatedLocalStorageUnverified" {
            return "The remote certificate may exist, but local storage was not verified. Check before trying again."
        }
        switch postcondition {
        case .achieved:
            return "The service verified the requested result. The matching terminal record can be acknowledged."
        case .notAchieved:
            return "The service did not observe the requested result. Check the account or device before clearing this hold."
        case .indeterminate:
            return "The service could not verify the result. Check the account or device before clearing this hold."
        case .manualCheckRequired:
            return "The result needs a manual account or device check before the recovery hold can be cleared."
        case .notDispatched:
            return "The service confirms this request was not dispatched. Clear the reservation only after reviewing it."
        case nil:
            return record.phase == .prepared
                ? "SideStore prepared the request but did not confirm dispatch. Inspect the result before retrying."
                : "The original reply is unavailable. Inspect the exact request before retrying."
        }
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

    func canBegin(kind: V3StatusAuthorityLeaseKind,
                  allowUnresolvedMutation: Bool = false) -> Bool {
        activeLease == nil && (kind == .snapshot || allowUnresolvedMutation || !hasUnresolvedMutation)
    }

    /// A mutation reserves a revision before it can suspend for a lease or XPC.
    mutating func reserveMutationRevision() -> UInt64 {
        revision &+= 1
        return revision
    }

    mutating func begin(ownerID: String, revision reservedRevision: UInt64,
                        serviceInstanceID: String,
                        kind: V3StatusAuthorityLeaseKind,
                        allowUnresolvedMutation: Bool = false) -> V3StatusWriteTicket? {
        guard canBegin(kind: kind, allowUnresolvedMutation: allowUnresolvedMutation),
              reservedRevision <= revision else { return nil }
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
        if retired.kind == .mutation && !retired.ownerID.hasPrefix("recovery-control:") {
            unresolvedOwnerIDs.insert(retired.ownerID)
        }
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

    /// Exact authoritative session evidence can also settle a matching active
    /// lease. This is separate from generic unknown-owner reconciliation so a
    /// caller cannot clear an unrelated active mutation.
    @discardableResult
    mutating func resolveOwnerAfterAuthoritativeReconciliation(_ ownerID: String) -> Bool {
        if activeLease?.ownerID == ownerID {
            activeLease = nil
            unresolvedOwnerIDs.remove(ownerID)
            revision &+= 1
            return true
        }
        return resolveOwnerAfterReconciliation(ownerID)
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
        return authority.mayApply(ticket, currentServiceEpoch: currentServiceEpoch,
            currentServiceInstanceID: currentServiceInstanceID)
    }
}

enum V3RecoveryOnlySnapshotPolicy {
    static func mayApplyFullStatus(busy: Bool, activeMutation: Bool,
                                   recoveryHold: Bool, hasTypedRecoveryEvidence: Bool) -> Bool {
        busy && !activeMutation && recoveryHold && hasTypedRecoveryEvidence
    }
}

enum V3RecoveryStoragePresentationPolicy {
    static func mayOfferClear(connected: Bool, unresolved: Bool, kind: String?,
                              serverClearEligible: Bool) -> Bool {
        connected && unresolved && serverClearEligible &&
            ["malformedRecord", "incompatibleRecord"].contains(kind ?? "")
    }

    static func confirmsCleared(snapshotApplied: Bool, unreadable: Bool,
                                operationRecovery: Bool, directRecovery: Bool,
                                refreshRecovery: Bool) -> Bool {
        snapshotApplied && !unreadable && !operationRecovery && !directRecovery && !refreshRecovery
    }
}

enum V3RecoveryClearHostAdmissionPolicy {
    static func permits(operation: String, target: String, userConfirmed: Bool,
                        recoveryHold: Bool, otherMutationActive: Bool) -> Bool {
        operation == "recoveryDiscardUnreadable" && target.isEmpty && userConfirmed &&
            recoveryHold && !otherMutationActive
    }
}

enum V3StatusRecoveryEvidencePolicy {
    static func mayApply(busySnapshot: Bool, activeMutation: Bool?,
                         hasDurableRecoveryEvidence: Bool) -> Bool {
        busySnapshot && activeMutation != true && hasDurableRecoveryEvidence
    }

    static func hasRecoveryEvidence(_ reply: [String: Any]) -> Bool {
        let recoveryHold = V3OperationReplyFieldPolicy.strictBoolean(reply["recoveryHold"]) == true
        let operation = reply["operationRecovery"] as? [String: Any]
        let operationEvidence = operation?["session"] as? String
        let refresh = reply["refreshRecovery"] as? [String: Any]
        let refreshRunID = refresh?["runID"] as? String
        let refreshEvidence = refreshRunID.map { UUID(uuidString: $0)?.uuidString == $0 } == true &&
            V3OperationReplyFieldPolicy.strictBoolean(refresh?["ownerLost"]) == true
        let unreadable = V3OperationReplyFieldPolicy.strictBoolean(reply["recoveryJournalUnreadable"]) == true
        let direct = V3HostDirectRecoveryRecord(snapshotValue: reply["directRecovery"])
        let directEvidence = direct != nil && recoveryHold
        let hasRecoveryField = reply.keys.contains("operationRecovery") ||
            reply.keys.contains("refreshRecovery") || reply.keys.contains("directRecovery")
        return recoveryHold || hasRecoveryField || directEvidence || unreadable || refreshEvidence ||
            (operationEvidence.map { UUID(uuidString: $0) != nil } == true &&
             (operation?["kind"] as? String)?.isEmpty == false &&
             V3OperationRecoveryRecord.Phase(rawValue: operation?["phase"] as? String ?? "") != nil)
    }
}

// Direct Apple certificate mutations must not outrun an interrupted local
// credential, certificate or account activation commit. Local readback repair
// is deliberately outside this policy and still uses the normal mutation gate.
enum V3CertificateStorageAdmission {
    static func failure(operation: String, id: String,
                        databaseRequiresReconciliation: Bool,
                        keychainRequiresReconciliation: () throws -> Bool) -> CombinedFailure? {
        guard ["certCreate", "certRevoke"].contains(operation) else { return nil }
        let unresolved = databaseRequiresReconciliation ||
            ((try? keychainRequiresReconciliation()) ?? true)
        guard unresolved else { return nil }
        return CombinedFailure(operation: operation, stage: .persistence, code: .notReady,
            id: id, retryable: false, safeCause: .signingStorageUnverified)
    }
}

enum V3StatusAuthorityOperationPolicy {
    static func directWriteOwnerID(operation: String, requestID: String) -> String? {
        // `backupResult` is control for its existing operation owner; `ipaCleanup`
        // only retires staged files. Neither creates a new status writer. Recovery journal edits
        // do change fields in the next authoritative snapshot and are fenced.
        let writes: Set<String> = [
            "signOut", "accountImport", "syncAppIDs", "clearCache", "refreshSources", "jit",
            "certSetActive", "certDelete", "certRevoke", "certCreate",
            "sourceAddConfirmed", "sourceRemoveConfirmed", "pairingImportData", "settingsSet",
            "sidesignSet", "sidesignReset", "sidesignImport", "anisetteReset", "anisetteSync",
            "opRecoveryPrepare", "recoveryDiscardUnreadable", "authReconcileStorage"
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
        case "opPoll", "opAnswer", "opCancel", "opRecoveryReconcile", "backupResult": return "operation:\(sessionID)"
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
        case "directRecoveryReconcile":
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

// Source identifiers are normalized database keys, not fetchable URLs. Never
// reconstruct a URL from one: normalization removes scheme/query and lowercases
// case-sensitive paths. Missing URLs from older backends require manual recovery.
enum V3SourceRecoveryPolicy {
    static func isSettledStartReply(_ reply: [String: Any], sessionID: String) -> Bool {
        reply["session"] as? String == sessionID &&
            reply["state"] as? String == "requiresSource" &&
            V3OperationReplyFieldPolicy.strictBoolean(reply["failedToStart"]) == true &&
            V3OperationReplyFieldPolicy.strictBoolean(reply["backendSettled"]) == true &&
            !V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])
    }

    static func target(sourceID: String, sourceURL: String?) -> String? {
        guard !sourceID.isEmpty, let sourceURL,
              V3SourceAddPersistencePolicy.validatedURL(sourceURL) != nil else { return nil }
        return sourceURL
    }

    static func matchesPreview(_ preview: [String: Any], sourceID: String) -> Bool {
        !sourceID.isEmpty && preview["identifier"] as? String == sourceID
    }

    static func verifiedAddition(_ result: [String: Any], sourceID: String) -> Bool {
        guard matchesPreview(result, sourceID: sourceID),
              V3SourceAddPersistencePolicy.confirmationMessage(result) != nil,
              let sources = result["sources"] as? [[String: Any]] else { return false }
        return sources.contains { $0["identifier"] as? String == sourceID }
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
        return "The verification response could not be confirmed. The exact underlying cause could not be safely identified. Check the sign-in status before trying again.\nError ID: SS-AUTH-C11"
    }

    static func diagnostics(_ error: Error) -> String {
        if let failure = error as? CombinedFailure { return failure.technicalDetails }
        return "schema=1 diagnostic_code=SS-AUTH-C11 builder_commit=\(V3DiagnosticBuild.commit) operation=authRespond stage=command code=failed correlation=unavailable underlying_domain=redacted underlying_code=redacted retryable=unknown"
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
    private var pending: [String: Set<UInt64>] = [:]

    mutating func begin(_ key: String) -> UInt64 {
        let next = (values[key] ?? 0) &+ 1
        values[key] = next
        pending[key, default: []].insert(next)
        return next
    }

    mutating func finish(_ generation: UInt64, for key: String) {
        pending[key]?.remove(generation)
        if pending[key]?.isEmpty == true { pending.removeValue(forKey: key) }
    }

    func isPending(_ generation: UInt64, for key: String) -> Bool {
        pending[key]?.contains(generation) == true
    }

    func hasPendingWrites(for key: String) -> Bool {
        pending[key]?.isEmpty == false
    }

    func isCurrent(_ generation: UInt64, for key: String) -> Bool {
        values[key] == generation
    }

    func current(for key: String) -> UInt64 {
        values[key] ?? 0
    }

    func isUnchanged(since captured: V3SettingsWriteGeneration) -> Bool {
        values == captured.values && pending.isEmpty && captured.pending.isEmpty
    }

    // A read can start before OR during a write and return after it settles.
    // Reject both cases per key, retaining unrelated authoritative read values.
    func mergingSnapshot<Value>(_ snapshot: [String: Value], into currentValues: [String: Value],
                                captured: V3SettingsWriteGeneration) -> [String: Value] {
        var result = currentValues
        for key in Set(currentValues.keys).union(snapshot.keys)
            where current(for: key) == captured.current(for: key) &&
                  pending[key] == nil && captured.pending[key] == nil {
            result[key] = snapshot[key]
        }
        return result
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

// Customization reviews every target extension, including fresh installs
// (where upstream reports no excess extensions). Only an empty target skips UI.
enum V3ExtensionRemovalPromptPolicy {
    static func decide<Element: Hashable, Decision>(
        targetExtensions: Set<Element>,
        whenEmpty: Decision,
        prompt: () async throws -> Decision
    ) async rethrows -> Decision {
        guard !targetExtensions.isEmpty else { return whenEmpty }
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

// A result dictionary is written before the scheduler verifies it, including
// failures. Home must consume settled scheduler evidence, never mere presence.
enum V3HomeRefreshVerificationPolicy {
    static func isVerified(manifest: [String: Any]?, ledger: [String: Any],
                           activeRunID: String?, hostHandoffPending: Bool,
                           uncertainMutationRunID: String?) -> Bool {
        guard activeRunID?.isEmpty != false, !hostHandoffPending,
              uncertainMutationRunID?.isEmpty != false,
              let manifest, let runID = manifest["run_id"] as? String,
              CombinedVerification.hasCompleteTerminalResults(manifest, runID: runID),
              let record = ledger[runID] as? [String: Any],
              let summary = record["manifest_summary"] as? [String: Any],
              V3RefreshAllTerminalEvidencePolicy.verifiedSummary(summary, record: record, runID: runID),
              let expected = manifest["expected_ids"] as? [String],
              let results = manifest["results"] as? [[String: Any]],
              summary["expected_count"] as? Int == expected.count,
              summary["result_count"] as? Int == results.count,
              summary["skipped_count"] as? Int == (manifest["skipped_ids"] as? [String] ?? []).count,
              summary["requested_count"] as? Int == (manifest["requested_ids"] as? [String] ?? []).count else {
            return false
        }
        // A completed result set can still contain failures. The canonical
        // coverage check above already requires actual plist boolean values.
        return results.allSatisfy { $0["success"] as? Bool == true }
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
        terminalMessage = "Refresh did not start." + "\nError ID: SS-CMD-D047"
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
            "schema=1", "diagnostic_code=SS-REFRESH-UNKNOWN", "builder_commit=\(V3DiagnosticBuild.commit)", "request_id=\(requestID)", "manual_refresh_request=\(requestID)",
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
                "schema=1", "diagnostic_code=SS-REFRESH-UNKNOWN", "builder_commit=\(V3DiagnosticBuild.commit)", "request_id=\(requestID)", "manual_refresh_request=\(requestID)", "run_id=\(runID)",
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
            "diagnostic_code=\(failure.diagnosticCode)",
            "builder_commit=\(V3DiagnosticBuild.commit)",
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
            return "Sign-in credentials were cleared, but SideStore still reports an active account or team." + "\nError ID: SS-AUTH-D094"
        case .authenticationRemains:
            return "SideStore still reports an active sign-in. Sign-out is not confirmed." + "\nError ID: SS-AUTH-D095"
        case .snapshotIncomplete:
            return "SideStore did not return enough account state to confirm sign-out." + "\nError ID: SS-AUTH-D060"
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
            return "What happened: The Anisette Servers request was cancelled.\nWhat you can do: Reopen Anisette Servers to check the current state before trying again.\n\(failure.diagnosticLabel)"
        }
        guard failure.stage == .network ||
              failure.safeCause == .networkConnectionLost ||
              failure.safeCause == .networkTimedOut ||
              failure.safeCause == .networkUnavailable else { return nil }
        return "What happened: SideStore could not reach the configured Anisette server.\nWhat you can do: Check its address and your network, then try again. This does not show that LocalDevVPN is unavailable.\n\(failure.diagnosticLabel)"
    }
}

enum V3SideJITReachabilityFeedback {
    static let unreachable = "The SideJIT server could not be reached. Check its address and network, then try again." + "\nError ID: SS-NET-D061"

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
        let anisetteServerUnavailable = operation.lowercased().hasPrefix("anisette") &&
            safeCause == CombinedFailure.SafeCause.anisetteServerUnavailable.rawValue
        let destination: String? = {
            // A remote Anisette server outage is service evidence even if an
            // upstream caller labels the boundary as `.network`. Do not let
            // the generic stage fallback send it to LocalDevVPN settings.
            if anisetteNetworkFailure || anisetteServerUnavailable { return nil }
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
            if sourceStep == CombinedFailure.SourceStep.certificateValidation.rawValue
                || safeCause == CombinedFailure.SafeCause.certificateUnavailable.rawValue {
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
// V3_INSTALLED_HOST_SIGNING_V1: local compatibility is a current observation,
// separate from successful refresh history and the imported JIT-Less copy.
// It does not establish portal revocation status or exact signing provenance.
enum V3HostSigningState: String, Equatable, Sendable {
    case unknown, compatible, refreshRequired, paidSignerUnverified

    var detail: String {
        switch self {
        case .unknown:
            return "Installed host signing could not be checked. Reload Status to check again." + "\nError ID: SS-VERIFY-D062"
        case .compatible:
            return "The installed host is locally compatible with the active signing setup. Revocation was not checked."
        case .refreshRequired:
            return "The installed host signing is expired or differs from the active setup. Run Test Refresh after completing account and certificate setup." + "\nError ID: SS-VERIFY-D063"
        case .paidSignerUnverified:
            return "The installed host uses a different paid-team signer. Its portal status was not checked; a re-sign is not known to be required. You can inspect Certificates or explicitly run Test Refresh."
        }
    }
}

struct V3HostSigningObservation: Equatable, Sendable {
    // Match the existing shared setup-fact observation interval.
    static let maximumAge: TimeInterval = 5 * 60
    var context = ""
    var state: V3HostSigningState = .unknown
    var checkedAt = Date.distantPast
    var validUntil = Date.distantPast

    func currentState(context: String?, now: Date = Date()) -> V3HostSigningState {
        guard let context, !context.isEmpty, self.context == context,
              now >= checkedAt, now < validUntil,
              now.timeIntervalSince(checkedAt) < Self.maximumAge else { return .unknown }
        return state
    }

    var wire: [String: Any] {
        ["context": context, "state": state.rawValue, "checkedAt": checkedAt, "validUntil": validUntil]
    }

    static func decode(_ value: Any?) -> V3HostSigningObservation? {
        guard let value = value as? [String: Any],
              let context = value["context"] as? String,
              context.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
              let rawState = value["state"] as? String, let state = V3HostSigningState(rawValue: rawState),
              let checkedAt = value["checkedAt"] as? Date,
              let validUntil = value["validUntil"] as? Date,
              checkedAt.timeIntervalSince1970.isFinite, validUntil.timeIntervalSince1970.isFinite,
              validUntil <= checkedAt.addingTimeInterval(maximumAge) else { return nil }
        return V3HostSigningObservation(context: context, state: state,
            checkedAt: checkedAt, validUntil: validUntil)
    }
}

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
    case installedHostSigning

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
        case .installedHostSigning: return "Check installed host signing"
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
    var installedHostSigningCompatible = false

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
        if !installedHostSigningCompatible { items.append(.installedHostSigning) }
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
            return combined.recovery + "\n" + combined.diagnosticLabel
        }
        // The earlier wording asserted "and nothing was changed". Nothing
        // supports that: an untyped failure can arrive after the service applied
        // the request, and the same helper is used after settings writes, source
        // confirmation, pairing import and install staging. Claiming a known
        // side-effect from an unknown cause is the same class of error as
        // blaming the network, so the claim is removed and the outcome is stated
        // as unknown.
        return "That action did not complete, and whether it took effect is not known. Reload status to see the current state before trying again. If it keeps failing, copy diagnostics to identify the cause.\nError ID: SS-CMD-C11"
    }

    /// Privacy-safe diagnostic text, never shown as guidance.
    static func diagnostics(_ error: Error) -> String {
        if let combined = error as? CombinedFailure {
            return combined.technicalDetails
        }
        let nsError = error as NSError
        let underlying = CombinedFailure.safeDiagnosticUnderlying(domain: nsError.domain,
            code: nsError.code)
        return "diagnostic_code=SS-CMD-C11 builder_commit=\(V3DiagnosticBuild.commit) operation=untyped stage=command code=failed underlying_domain=\(underlying.domain) underlying_code=\(underlying.code)"
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
        if sourceStep == CombinedFailure.SourceStep.certificateValidation.rawValue ||
           safeCause == CombinedFailure.SafeCause.certificateUnavailable.rawValue {
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
        if safeCause == CombinedFailure.SafeCause.appIDLimitReached.rawValue { return whatToDo }
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
        guard let currentFailure else { return "The operation failed." + "\nError ID: SS-CMD-D064" }
        guard retryCouldNotStart else { return currentFailure.whatHappened }
        if currentFailure.safeCause == CombinedFailure.SafeCause.operationInProgress.rawValue {
            if let previousFailure {
                return "The retry could not start because another SideStore operation is still active. Previous attempt: \(previousFailure.whatHappened)"
            }
            return "The operation could not start because another SideStore operation is still active." + "\nError ID: SS-CMD-D065"
        }
        if let previousFailure {
            if ["timedOut", "interrupted"].contains(currentFailure.code) {
                return "The retry could not be confirmed as started. The previous operation may still be active. Previous attempt: \(previousFailure.whatHappened)"
            }
            return "The retry could not start, so the app operation did not run. Previous attempt: \(previousFailure.whatHappened)"
        }
        if ["timedOut", "interrupted"].contains(currentFailure.code) {
            return "The operation could not be confirmed as started. It may still be active." + "\nError ID: SS-CMD-D066"
        }
        return "The operation could not start, so the app pipeline did not run." + "\nError ID: SS-CMD-D067"
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
                "message": "The operation session is no longer available, so its device result cannot be confirmed." + "\nError ID: SS-CMD-D068"]
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
        "opStart", "opPoll", "opAnswer", "opCancel", "backupResult",
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
    static func mayUseCachedSignIn(forceProvisioningRetry: Bool,
                                   requireFullProvisioning: Bool = false) -> Bool {
        !forceProvisioningRetry && !requireFullProvisioning
    }

    static func mayPromptForCredentials(forceProvisioningRetry: Bool) -> Bool {
        !forceProvisioningRetry
    }
}

// Completion is evidence from a full SignInOperation, never a database row.
// The journal is invalidated durably BEFORE a new attempt can mutate anything.
// A process restart may reuse verified completion only for the exact hashed
// credential route, team, certificate and device binding, never row presence.
struct V3ProvisioningCompletionState {
    private static let journalKey = "V3VerifiedProvisioningCompletionV1"
    private let defaults: UserDefaults
    private(set) var attemptID: String?
    private var owner: String?
    private var identityStamp: String?
    private var completed = false
    private var completedBinding: String?

    init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    private func persist(_ record: [String: Any]) -> Bool {
        defaults.set(record, forKey: Self.journalKey)
        return defaults.synchronize() &&
            defaults.dictionary(forKey: Self.journalKey).map { NSDictionary(dictionary: $0).isEqual(to: record) } == true
    }

    @discardableResult
    mutating func begin(attemptID: String, owner: String?, identityStamp: String) -> Bool {
        guard persist(["version": 1, "state": "incomplete", "attemptID": attemptID,
                       "identityStamp": identityStamp]) else { return false }
        self.attemptID = attemptID
        self.owner = V3AuthIdentityBindingPolicy.normalizedOwner(owner)
        self.identityStamp = identityStamp
        completed = false
        completedBinding = nil
        return true
    }

    mutating func authenticated(attemptID: String, owner: String?, identityStamp: String) {
        guard self.attemptID == attemptID else { return }
        self.owner = V3AuthIdentityBindingPolicy.normalizedOwner(owner)
        self.identityStamp = identityStamp
        completed = false
    }

    @discardableResult
    mutating func complete(attemptID: String, owner: String?, identityStamp: String,
                           identityStable: Bool, fullProvisioningCompleted: Bool,
                           activeAccountMatches: Bool, activeTeamMatches: Bool,
                           activeCertificateMatches: Bool, binding: String?) -> Bool {
        guard self.attemptID == attemptID,
              self.owner != nil, self.owner == V3AuthIdentityBindingPolicy.normalizedOwner(owner),
              self.identityStamp == identityStamp, identityStable,
              fullProvisioningCompleted, activeAccountMatches, activeTeamMatches,
              activeCertificateMatches, let binding, !binding.isEmpty,
              persist(["version": 1, "state": "complete", "attemptID": attemptID,
                       "identityStamp": identityStamp, "binding": binding]) else { return false }
        completed = true
        completedBinding = binding
        return true
    }

    func status(owner: String?, identityStamp: String, identityStable: Bool,
                activeAccountPresent: Bool, activeTeamPresent: Bool,
                activeCertificatePresent: Bool, binding: String?) -> String {
        guard identityStable, let owner = V3AuthIdentityBindingPolicy.normalizedOwner(owner) else { return "unknown" }
        let prerequisitesPresent = activeAccountPresent && activeTeamPresent && activeCertificatePresent
        if attemptID != nil {
            guard self.owner == owner, self.identityStamp == identityStamp else { return "unknown" }
            return completed && prerequisitesPresent && binding == completedBinding ? "complete" : "incomplete"
        }
        guard let record = defaults.dictionary(forKey: Self.journalKey),
              record["version"] as? Int == 1, let binding, !binding.isEmpty,
              record["state"] as? String == "complete", record["binding"] as? String == binding else {
            return "unknown"
        }
        return prerequisitesPresent ? "complete" : "incomplete"
    }
}

enum V3ProvisioningReauthenticationIdentityPolicy {
    static func mayAuthenticate(expectedOwner: String, submittedOwner: String?,
                                currentOwner: String?, capturedStamp: String,
                                currentStamp: String, identityStable: Bool) -> Bool {
        guard let expected = V3AuthIdentityBindingPolicy.normalizedOwner(expectedOwner) else { return false }
        return expected == V3AuthIdentityBindingPolicy.normalizedOwner(submittedOwner) &&
            expected == V3AuthIdentityBindingPolicy.normalizedOwner(currentOwner) &&
            V3AuthReadStampPolicy.mayReturn(capturedStamp: capturedStamp,
                currentStamp: currentStamp, stable: identityStable)
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
        ["timedOut", "failed", "cancelled", "resultUnknown", "promptExpired"].contains(state) ||
            state == "authenticatedProvisioningIncomplete"
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
                return .init(state: "timedOut", message: "Sign-in timed out. SideStore reports that no account is currently signed in." + "\nError ID: SS-AUTH-D069")
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
                ? "The sign-in attempt did not complete. SideStore reports authentication, but device provisioning is incomplete." + "\nError ID: SS-AUTH-D070"
                : "The sign-in attempt did not complete. SideStore currently reports an account as signed in." + "\nError ID: SS-AUTH-D071"
            if let previousFailureMessage { message += " " + previousFailureMessage }
            return .init(state: "failed", message: message)
        case "timedOut":
            return .init(state: "timedOut", message: provisioningIncomplete
                ? "The sign-in attempt timed out. SideStore reports authentication, but device provisioning is incomplete." + "\nError ID: SS-AUTH-D072"
                : "The sign-in attempt timed out. SideStore currently reports an account as signed in." + "\nError ID: SS-AUTH-D073")
        case "cancelled":
            return .init(state: "cancelled", message: provisioningIncomplete
                ? "The sign-in attempt was cancelled. SideStore reports authentication, but device provisioning is incomplete."
                : "The sign-in attempt was cancelled. SideStore currently reports an account as signed in.")
        case "resultUnknown":
            return .init(state: "resultUnknown", message: provisioningIncomplete
                ? "The sign-in result remains unconfirmed. SideStore reports authentication, but device provisioning is incomplete." + "\nError ID: SS-AUTH-D074"
                : "The sign-in result remains unconfirmed. SideStore currently reports an account as signed in." + "\nError ID: SS-AUTH-D075")
        case "promptExpired":
            return .init(state: "promptExpired", message: provisioningIncomplete
                ? "The verification session expired. SideStore reports authentication, but device provisioning is incomplete." + "\nError ID: SS-AUTH-D076"
                : "The verification session expired. SideStore currently reports an account as signed in." + "\nError ID: SS-AUTH-D077")
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
                message: "Another Apple sign-in session is active. This request could not be matched to it. Wait for it to finish, then reload status." + "\nError ID: SS-AUTH-D078")
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
                message: "SideStore no longer has the active sign-in session. The current account and provisioning state could not be confirmed. Reload status before continuing." + "\nError ID: SS-AUTH-D079",
                provisioningMessage: nil,
                cancellationConfirmed: false)
        }
        if anotherSessionActive {
            return V3AuthSessionUnavailablePresentation(
                state: "resultUnknown",
                message: "Another Apple sign-in session is active. This request could not be matched to it. Wait for it to finish, then reload status." + "\nError ID: SS-AUTH-D078",
                provisioningMessage: nil,
                cancellationConfirmed: true)
        }
        if authenticated && provisioningIncomplete {
            return V3AuthSessionUnavailablePresentation(
                state: "authenticatedProvisioningIncomplete",
                message: "Apple ID signed in successfully.",
                provisioningMessage: "The saved provisioning session is no longer available. Open Account & Signing to sign in again before retrying setup." + "\nError ID: SS-AUTH-D080",
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
            : "SideStore no longer has the active sign-in session and could not confirm the account state. Reload status before starting a new sign-in." + "\nError ID: SS-AUTH-D081"
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
    let showReauthenticateProvisioning: Bool
    let showFinishLater: Bool
    let blockedByActiveSession: Bool
}

enum V3AuthProvisioningRecoveryPolicy {
    static func resolve(state: String, hasSession: Bool, signedIn: Bool,
                        provisioningRetryAvailable: Bool, isCancelling: Bool,
                        cancellationConfirmed: Bool,
                        authenticationActive: Bool = false,
                        reauthenticationAvailable: Bool = false,
                        identityStateBlocked: Bool = false) -> V3AuthProvisioningRecoveryPresentation {
        let noSessionResumeIsSafe = state == "resultUnknown" && !hasSession && signedIn &&
            provisioningRetryAvailable && !authenticationActive
        let retryAllowed = !identityStateBlocked && !isCancelling && cancellationConfirmed && provisioningRetryAvailable &&
            !authenticationActive &&
            (state != "resultUnknown" || noSessionResumeIsSafe)
        return V3AuthProvisioningRecoveryPresentation(
            showCancellationInstruction: state == "resultUnknown" && hasSession,
            showRetryProvisioning: retryAllowed,
            showReauthenticateProvisioning: !identityStateBlocked && signedIn && !hasSession &&
                reauthenticationAvailable && !authenticationActive &&
                !isCancelling && cancellationConfirmed &&
                !["working", "awaitingPrompt"].contains(state),
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
            return "Signed in, provisioning needs attention"
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
    static func provisioning(reply: [String: Any], message: String, technical: String) -> (message: String, technical: String) {
        var evidence = (reply["failure"] as? [String: Any]) ?? ["stage": "provisioning", "code": "failed"]
        if let kind = reply["failureKind"] as? String { evidence["kind"] = kind }
        let canonical = "diagnostic_code=\(diagnosticCode(for: evidence)) builder_commit=\(V3DiagnosticBuild.commit)"
        // Preserve existing safe technical evidence, explicitly naming its
        // structured category separately from the presentation category.
        let underlying = technical.replacingOccurrences(of: "diagnostic_code=", with: "underlying_diagnostic_code=")
            .replacingOccurrences(of: "builder_commit=", with: "underlying_builder_commit=")
        return (display(message, failure: evidence), canonical + (underlying.isEmpty ? "" : "\n" + underlying))
    }

    static func display(_ message: String, failure: [String: Any]) -> String {
        // Replace our previous decoration only. Never inspect prose to infer a
        // cause; the canonical ID comes solely from the finite envelope fields.
        let prose = message.components(separatedBy: "\n").compactMap { line -> String? in
            let prefix = "Error ID: "
            guard line.hasPrefix(prefix + "SS-") else { return line }
            // Older call sites may append recovery prose after the ID token.
            // Remove only the decoration token, never the recovery instructions.
            let trailing = line.dropFirst(prefix.count).drop(while: { !$0.isWhitespace })
                .trimmingCharacters(in: .whitespaces)
            return trailing.isEmpty ? nil : trailing
        }.joined(separator: "\n")
        let fields = (failure["signingContext"] as? [String: String]).flatMap(CombinedFailure.validatedSigningContext) ?? [:]
        let trace = fields[V3TemporaryAnisetteTrace.contextKey].flatMap(V3TemporaryAnisetteTrace.init(encoded:))
        let failedStep = trace?.failedStep.map { "\nDEBUG TEMPORARY failed step: " + $0 } ?? ""
        let cleanProse = prose.components(separatedBy: "\n").filter {
            !$0.hasPrefix("DEBUG TEMPORARY failed step: ")
        }.joined(separator: "\n")
        return cleanProse + failedStep + "\nError ID: " + diagnosticCode(for: failure)
    }

    static func diagnosticCode(for failure: [String: Any]) -> String {
        let stage = (failure["stage"] as? String).flatMap(CombinedFailure.Stage.init(rawValue:)) ?? .authentication
        let code = (failure["code"] as? String).flatMap(CombinedFailure.Code.init(rawValue:)) ?? .failed
        let step = (failure["sourceStep"] as? String).flatMap(CombinedFailure.SourceStep.init(rawValue:))
        let cause = (failure["safeCause"] as? String).flatMap(CombinedFailure.SafeCause.init(rawValue:))
        let fields = (failure["signingContext"] as? [String: String]).flatMap(CombinedFailure.validatedSigningContext) ?? [:]
        let failureCode = CombinedFailure(operation: "signIn", stage: stage, code: code,
            id: "00000000-0000-0000-0000-000000000000", safeCause: cause, sourceStep: step,
            signingContext: fields).diagnosticCode
        let kind = failure["kind"] as? String ?? failure["code"] as? String ?? "unknown"
        let kindToken: String
        switch kind {
        case "unknown": kindToken = "A00"
        case "invalidCredentials": kindToken = "A01"
        case "appSpecificPasswordRequired": kindToken = "A02"
        case "invalidCode": kindToken = "A03"
        case "rateLimited": kindToken = "A04"
        case "serviceUnavailable": kindToken = "A05"
        case "anisetteFailure", "anisette": kindToken = "A06"
        case "networkFailure", "network": kindToken = "A07"
        case "accountRepairRequired": kindToken = "A08"
        case "credentialStorage": kindToken = "A09"
        case "credentialStorageUncertain": kindToken = "A10"
        case "accountIdentityMismatch": kindToken = "A11"
        case "anisetteIdentityStateInvalid": kindToken = "A12"
        default: kindToken = "A00"
        }
        return failureCode + "-" + kindToken
    }
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
        let step = (failure["sourceStep"] as? String).flatMap(CombinedFailure.SourceStep.init(rawValue:))?.rawValue ?? "unknown"
        let fields = (failure["signingContext"] as? [String: String]).flatMap(CombinedFailure.validatedSigningContext) ?? [:]
        let accountDetails = " source_step=\(step) typed_error=\(fields["typed_error"] ?? "unknown") server_code=\(fields["server_code"] ?? "unknown") http_status=\(fields["http_status"] ?? "unavailable")"
        let nativeDetails = fields["typed_error"] == "anisetteKitADIError"
            ? " native_code=\(fields["native_code"] ?? "unknown") native_phase=\(fields["native_phase"] ?? "unknown") native_subcode=\(fields["native_subcode"] ?? "unknown")" : ""
        let attemptDetails = fields["anisette_blob_state"].map {
            " anisette_blob_state=\($0) anisette_recovery=\(fields["anisette_recovery"] ?? "notAttempted")"
        } ?? ""
        let probeDetails = fields["probe_native_code"].map {
            " probe_native_code=\($0) probe_native_phase=\(fields["probe_native_phase"] ?? "unknown") probe_native_subcode=\(fields["probe_native_subcode"] ?? "unknown")"
        } ?? ""
        return "diagnostic_code=\(diagnosticCode(for: failure)) builder_commit=\(V3DiagnosticBuild.commit) kind=\(kind) stage=\(stage) code=\(code) correlation=\(correlation) underlying=\(underlyingDomain)/\(codeText) retryable=\(retryableText)" + accountDetails + nativeDetails + attemptDetails + probeDetails +
            (fields[V3TemporaryAnisetteTrace.contextKey].flatMap(V3TemporaryAnisetteTrace.init(encoded:))?.technicalDetails ?? "") +
            (fields[V3TemporaryADIConsumption.contextKey].flatMap(V3TemporaryADIConsumption.init(encoded:))?.technicalDetails ?? "")
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
        case "credentialStorage", "credentialStorageUncertain", "anisetteIdentityStateInvalid": return .blocked
        case "accountIdentityMismatch": return .beginNewSignIn(title: "Use Saved Apple ID")
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
        if kind == "anisetteIdentityStateInvalid" { return LCAnisettePairError.recovery }
        if kind == "accountIdentityMismatch" {
            return "Reload status, then sign in with the saved Apple ID to finish setup."
        }
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

    static func promptField(url: String) -> [String: String] {
        ["key": "url", "label": "Open Apple Account Repair",
         "secure": "false", "value": url]
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
            return "SideStore could not start the provisioning retry because it could not reserve a safe response slot." + "\nError ID: SS-AUTH-D082"
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
                    "message": "Apple ID sign-in succeeded, but provisioning did not finish before the session timed out." + "\nError ID: SS-AUTH-D083"]
        }
        return ["state": "timedOut", "authenticated": false,
                "message": "Sign-in timed out. Start a new sign-in when you are ready." + "\nError ID: SS-AUTH-D040"]
    }
}

// A backup callback is a one-shot capability for one external SideBackup step.
// The session owns the device mutation throughout the round trip; this control
// message must never create, release, or reconcile an operation owner.
struct V3BackupCallbackIdentity: Equatable, Sendable {
    let session: String
    let nonce: String
    let action: String

    init?(session: String, nonce: String, action: String) {
        guard UUID(uuidString: session)?.uuidString == session,
              UUID(uuidString: nonce)?.uuidString == nonce,
              ["backup", "restore"].contains(action) else { return nil }
        self.session = session; self.nonce = nonce; self.action = action
    }

    init?(_ raw: Any?) {
        guard let values = raw as? [String: String],
              Set(values.keys) == Set(["session", "nonce", "action"]),
              let session = values["session"], let nonce = values["nonce"],
              let action = values["action"] else { return nil }
        self.init(session: session, nonce: nonce, action: action)
    }

    var wire: [String: String] { ["session": session, "nonce": nonce, "action": action] }
    var queryItems: [URLQueryItem] {
        [URLQueryItem(name: "v3Session", value: session),
         URLQueryItem(name: "v3Nonce", value: nonce),
         URLQueryItem(name: "v3Action", value: action)]
    }

    static func supports(kind: String, action: String) -> Bool {
        switch (kind, action) {
        case ("backup", "backup"), ("deactivate", "backup"),
             ("restore", "restore"), ("activate", "restore"): return true
        default: return false
        }
    }
}

struct V3BackupCallbackResult: Equatable, Sendable {
    let identity: V3BackupCallbackIdentity
    let succeeded: Bool

    init?(session: String, payload: [String: Any]) {
        guard Set(payload.keys) == Set(["nonce", "action", "result"]),
              let nonce = payload["nonce"] as? String,
              let action = payload["action"] as? String,
              let result = payload["result"] as? String,
              ["success", "failure"].contains(result),
              let identity = V3BackupCallbackIdentity(session: session, nonce: nonce, action: action)
        else { return nil }
        self.identity = identity; self.succeeded = result == "success"
    }

    // URLs come from outside the process. Reject ambiguous and unbound input;
    // external descriptions, error domains and codes never enter XPC or logs.
    init?(url: URL, expectedTargetBundleID: String) {
        guard url.absoluteString.utf8.count <= 8192,
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme?.lowercased() == "sidestore",
              components.host?.lowercased() == "appbackupresponse",
              components.user == nil, components.password == nil, components.port == nil,
              components.fragment == nil,
              ["/success", "/failure"].contains(components.path.lowercased()) else { return nil }
        let items = components.queryItems ?? []
        let allowed: Set<String> = ["targetBundleID", "v3Session", "v3Nonce", "v3Action",
                                    "errorDomain", "errorCode", "errorDescription"]
        guard items.count <= allowed.count, Set(items.map(\.name)).count == items.count,
              items.allSatisfy({ allowed.contains($0.name) && $0.value != nil }) else { return nil }
        let values = Dictionary(uniqueKeysWithValues: items.compactMap { item in
            item.value.map { (item.name, $0) }
        })
        guard !expectedTargetBundleID.isEmpty, values["targetBundleID"] == expectedTargetBundleID,
              let session = values["v3Session"], let nonce = values["v3Nonce"],
              let action = values["v3Action"] else { return nil }
        self.init(session: session, payload: ["nonce": nonce, "action": action,
            "result": components.path.lowercased() == "/success" ? "success" : "failure"])
    }

    var payload: [String: Any] {
        ["nonce": identity.nonce, "action": identity.action,
         "result": succeeded ? "success" : "failure"]
    }
}

enum V3ServiceMutationAdmissionPolicy {
    static func hasConflictingOperationMutation(operation: String, target: String,
                                                activeOperationID: String?,
                                                backupCallbackControl: Bool = false) -> Bool {
        guard let activeOperationID else { return false }
        return !(target == activeOperationID &&
            (["opPoll", "opAnswer", "opCancel"].contains(operation) ||
             (operation == "backupResult" && backupCallbackControl)))
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
        if ["opPoll", "opAnswer", "opCancel", "backupResult"].contains(operation) {
            return target.isEmpty ? nil : target
        }
        return nil
    }

    static func matches(operation: String, target: String, requestedStartSession: String?,
                        resultSession: String?) -> Bool {
        guard ["opStart", "opPoll", "opAnswer", "opCancel", "backupResult"].contains(operation) else { return true }
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
                       recoveryReadFailed: Bool, recoveryDiscardable: Bool = false,
                       refreshOwnerLost: Bool, backupCallbackControl: Bool = false) -> V3ServiceRecoveryAdmissionDecision {
        let refreshRecordMatches = recovery?.kind == "refreshAll" && recovery?.sessionID == target
        let refreshTerminalState = payload["state"] as? String
        let hasRefreshTerminal = ["completed", "failed", "notDispatched"].contains(refreshTerminalState ?? "")
        let userConfirmed = strictBoolean(payload["userConfirmed"]) == true
        let operationControl = recovery?.kind != "refreshAll" &&
            (((["opPoll", "opAnswer", "opCancel"].contains(operation) ||
               (operation == "backupResult" && backupCallbackControl)) &&
              operationSessionID == recovery?.sessionID) ||
             (operation == "opRecoveryReconcile" && target == recovery?.sessionID && userConfirmed))
        let refreshControl = refreshRecordMatches &&
            ((operation == "refreshAdmissionEnd" && hasRefreshTerminal) ||
             (operation == "refreshAdmissionReconcile" && userConfirmed))
        let unreadableRecoveryControl = operation == "recoveryDiscardUnreadable" &&
            recovery == nil && userConfirmed && (!recoveryReadFailed || recoveryDiscardable)
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
            unreadableRecoveryControl && refreshOwnerLost
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

    static func sharedGroup(fromDefaultGroup group: String) -> String? {
        guard let prefix = group.split(separator: ".", maxSplits: 1).first,
              String(prefix).range(of: #"^[A-Z0-9]{10}$"#, options: .regularExpression) != nil else {
            return nil
        }
        return "\(prefix).com.kdt.livecontainer.shared"
    }
}

import Foundation
import Security

#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

/// Advisory process-shared lock for operations that must coordinate between
/// the LiveContainer app and its embedded service process. NSLock is process local.
enum V3AppGroupProcessLock {
    static func withLock<T>(containerRoot: URL? = nil,
                            selectedGroup: String? = nil,
                            onFailure: ((String, String, Int) -> Void)? = nil,
                            diagnostics: V3SecretHandoffDiagnostics? = nil,
                            _ operation: () throws -> T) throws -> T {
        #if canImport(Darwin)
        let container: URL
        if let containerRoot { container = containerRoot }
        else {
            // This helper is compiled into the host, the SideStoreSupport
            // framework and the embedded service. Only Foundation is visible in
            // all three, so the group is injected: the host passes
            // LiveContainer's own selection, and the service resolves the group
            // LiveProcess validated and published. Both land on the same
            // V3SharedAppGroup identity IPA staging and the recovery journal
            // use, so the two processes take the same lock file.
            guard let shared = V3SharedAppGroup.runtimeIdentity(selectedGroup: selectedGroup) else {
                onFailure?("appGroup", "none", 0)
                throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock")
            }
            container = shared.containerRoot
        }

        #elseif canImport(Glibc)
        guard let containerRoot else {
            onFailure?("appGroup", "none", 0)
            throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock")
        }
        let container = containerRoot
        #else
        throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock")
        #endif
        let directory = ["Library", "Application Support", "LiveContainer"].reduce(
            container.standardizedFileURL) { $0.appendingPathComponent($1, isDirectory: true) }.standardizedFileURL
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            let values = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values.isDirectory == true, values.isSymbolicLink != true,
                  directory.resolvingSymlinksInPath().standardizedFileURL == directory else {
                throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock")
            }
        } catch {
            let native = error as NSError
            let safe = [NSCocoaErrorDomain, NSPOSIXErrorDomain].contains(native.domain)
            onFailure?("directory", safe ? native.domain : "redacted", safe ? native.code : 0)
            throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock",
                       osStatus: safe ? Int32(native.code) : 0)
        }
        let path = directory.appendingPathComponent("keychain-transaction.lock").path
        let descriptor = open(path, O_CREAT | O_RDWR | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else {
            onFailure?("open", NSPOSIXErrorDomain, Int(errno))
            throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock",
                       osStatus: Int32(errno))
        }
        defer { _ = close(descriptor) }
        guard fchmod(descriptor, S_IRUSR | S_IWUSR) == 0 else {
            onFailure?("permissions", NSPOSIXErrorDomain, Int(errno))
            throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock",
                       osStatus: Int32(errno))
        }
        guard flock(descriptor, LOCK_EX) == 0 else {
            onFailure?("flock", NSPOSIXErrorDomain, Int(errno))
            throw V3SecretHandoffError.fail(.appGroupLockUnavailable, as: diagnostics, operation: "lock",
                       osStatus: Int32(errno))
        }
        defer { _ = flock(descriptor, LOCK_UN) }
        return try operation()
    }
}

/// Why a secure handoff did not complete, at the granularity that tells an
/// engineer where to look without revealing anything sensitive.
///
/// Every case carries only an OSStatus integer, booleans, and a role. No case
/// carries an Apple ID, a password, a token, Keychain item data, or an access
/// group string, because this value reaches a device log.
public enum V3SecretHandoffFailure: String, Sendable {
    /// The process-shared App Group lock could not be taken, so the two
    /// processes could not serialize this transaction.
    case appGroupLockUnavailable
    /// This process could not read back which Keychain access group it owns, so
    /// the shared group could not be derived from its own entitlement.
    case keychainGroupDiscoveryFailed
    /// The derived shared group was refused by the Keychain: this process is not
    /// entitled to it. This is the shape a re-sign produces when the group is
    /// granted to the main app but not to its extensions.
    case keychainExplicitGroupUnauthorized
    /// The item was absent when it should have been present.
    case keychainItemNotFound
    /// Reading the item failed for a reason other than absence.
    case keychainReadFailed
    /// The one-time take could not delete the item after reading it.
    case keychainDeleteFailed
    /// The record existed but its lifetime had elapsed.
    case tokenExpired
    /// The record could not be decoded, or the token was not canonical.
    case tokenMalformed
    /// The outstanding-item budget was exhausted.
    case capacity
    /// The transaction lock is held but the shared group could not be resolved.
    case sharedGroupUnavailable
}

/// Privacy-safe evidence for one handoff step. Safe to log.
public struct V3SecretHandoffDiagnostics: Sendable, Equatable {
    /// "host" or "service".
    public var role: String
    public var operation: String
    public var failure: V3SecretHandoffFailure?
    /// Raw OSStatus, or 0 when the failure was not an OS call. Integer only.
    public var osStatus: Int32
    /// Whether this process could discover its own default access group.
    public var groupDiscovered: Bool
    /// Whether the token was a canonical UUID. Never the token itself.
    public var tokenWellFormed: Bool

    public init(role: String, operation: String, failure: V3SecretHandoffFailure? = nil,
                osStatus: Int32 = 0, groupDiscovered: Bool = false,
                tokenWellFormed: Bool = false) {
        self.role = role
        self.operation = operation
        self.failure = failure
        self.osStatus = osStatus
        self.groupDiscovered = groupDiscovered
        self.tokenWellFormed = tokenWellFormed
    }

    /// A single line with no secret material. Group names are deliberately
    /// absent: they embed the team identifier and were previously reported
    /// only as a boolean elsewhere.
    public var safeLine: String {
        var parts = ["handoff=1", "role=\(role)", "op=\(operation)"]
        parts.append("group_discovered=\(groupDiscovered)")
        parts.append("token_well_formed=\(tokenWellFormed)")
        if let failure { parts.append("cause=\(failure.rawValue)") }
        else { parts.append("cause=none") }
        parts.append("osstatus=\(osStatus)")
        return parts.joined(separator: " ")
    }
}

/// Emits one privacy-safe line per handoff step. Replaces the previous
/// `onFailure` callback, which carried an untyped domain string.
public enum V3SecretHandoffTrace {
    /// Set to false only by tests that assert on the absence of output.
    public static var isEnabled = true

    public static func emit(_ diagnostics: V3SecretHandoffDiagnostics) {
        guard isEnabled else { return }
        NSLog("[V3_SECRET_HANDOFF] %@", diagnostics.safeLine)
    }
}

/// Decides whether a thrown handoff error is reported as a secure-transport
/// failure rather than as whatever the surrounding operation was doing.
///
/// An authRespond that fails here never reached Apple. Reporting it as
/// signIn/authentication/failed tells the user their password was rejected,
/// which is false and sends them to change a password that never failed.
public enum V3SecretHandoffFailurePolicy {
    /// Operations whose payload crosses the secure channel first.
    public static let handoffCarryingOperations: Set<String> = [
        "authRespond", "opAnswer", "accountExport", "accountImport",
        "certCreate", "devPortalLogin"]

    public static func applies(to operation: String) -> Bool {
        handoffCarryingOperations.contains(operation)
    }

    /// The stage a handoff failure belongs to. Persistence, not authentication:
    /// the response is intact and the channel is what is broken.
    public static func stage(for operation: String) -> CombinedFailure.Stage { .persistence }

    /// Distinguishes a transient channel problem from one that needs a re-sign.
    public static func code(for failure: V3SecretHandoffFailure) -> CombinedFailure.Code {
        switch failure {
        case .appGroupLockUnavailable, .keychainReadFailed, .keychainDeleteFailed, .capacity:
            return .busy
        case .keychainGroupDiscoveryFailed, .keychainExplicitGroupUnauthorized,
             .keychainItemNotFound, .tokenExpired, .tokenMalformed, .sharedGroupUnavailable:
            return .unavailable
        }
    }

    /// Only a transient cause may be retried. Retrying cannot grant an access
    /// group or recreate an item the service is entitled to read.
    public static func isRetryable(_ failure: V3SecretHandoffFailure) -> Bool {
        switch failure {
        case .appGroupLockUnavailable, .keychainReadFailed, .keychainDeleteFailed, .capacity:
            return true
        case .keychainGroupDiscoveryFailed, .keychainExplicitGroupUnauthorized,
             .keychainItemNotFound, .tokenExpired, .tokenMalformed, .sharedGroupUnavailable:
            return false
        }
    }

    public static func failure(_ error: V3SecretHandoffError, operation: String,
                               id: String) -> CombinedFailure {
        let reason = error.failure
        // The OSStatus is evidence and is safe: an integer from the Keychain.
        // The group name is never included, because it embeds the team id.
        let underlying = NSError(domain: "V3SecretHandoff", code: Int(error.osStatusValue))
        return CombinedFailure(operation: operation, stage: stage(for: operation),
            code: code(for: reason), id: id, underlying: underlying,
            retryable: isRetryable(reason), safeCause: .secretHandoffUnavailable)
    }
}

/// Which side of the handoff is running. Injected so the same binary reports
/// honestly in the host, the SideStoreSupport framework and the service.
public enum V3SecretHandoffRole {
    public static let host = "host"
    public static let service = "service"
    /// The role of the running process, detected rather than declared. The host
    /// bundle identifier is the only one that is not the embedded service, and
    /// it is read from the process's own identity rather than passed in, so a
    /// call site cannot mislabel it.
    public static var current: String = resolve()

    static func resolve(bundle: Bundle = .main) -> String {
        bundle.bundleIdentifier?.hasSuffix(".LiveProcess") == true ? service : host
    }
}

public enum V3SecretHandoffError: Error, LocalizedError {
    case unavailable(V3SecretHandoffFailure, osStatus: Int32 = 0, groupDiscovered: Bool = false,
                     tokenWellFormed: Bool = false)
    case invalidToken
    case expired
    case malformed
    case capacity

    /// The OSStatus behind this error, or 0 when there was no OS call.
    public var osStatusValue: Int32 {
        if case .unavailable(_, let osStatus, _, _) = self { return osStatus }
        return 0
    }

    public var failure: V3SecretHandoffFailure {
        switch self {
        case .unavailable(let reason, _, _, _): return reason
        case .invalidToken: return .tokenMalformed
        case .expired: return .tokenExpired
        case .malformed: return .tokenMalformed
        case .capacity: return .capacity
        }
    }

    /// The safe line for this error, so every call site reports identically.
    public var diagnostics: V3SecretHandoffDiagnostics {
        switch self {
        case .unavailable(let reason, let osStatus, let groupDiscovered, let tokenWellFormed):
            return V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "consume", failure: reason, osStatus: osStatus,
                groupDiscovered: groupDiscovered, tokenWellFormed: tokenWellFormed)
        case .invalidToken:
            return V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "consume", failure: .tokenMalformed, tokenWellFormed: false)
        case .expired:
            return V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "consume", failure: .tokenExpired, tokenWellFormed: true)
        case .malformed:
            return V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "consume", failure: .tokenMalformed, tokenWellFormed: true)
        case .capacity:
            return V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "store", failure: .capacity)
        }
    }

    public var errorDescription: String? {
        switch self {
        case .unavailable(let reason, _, _, _): return reason.userFacingMessage
        case .invalidToken: return "The secure response reference is invalid."
        case .expired: return "The secure response expired before SideStore received it."
        case .malformed: return "The secure response could not be read."
        case .capacity: return "Secure response storage is busy."
        }
    }
}

extension V3SecretHandoffFailure {
    /// What the user is told. This never implies Apple rejected anything: the
    /// response never reached Apple when the handoff failed.
    var userFacingMessage: String {
        switch self {
        case .appGroupLockUnavailable:
            return "The secure channel to the embedded service could not be locked. Try again."
        case .keychainGroupDiscoveryFailed:
            return "This build's secure storage group could not be identified. Reinstall or re-sign the app."
        case .keychainExplicitGroupUnauthorized:
            return "This build's secure storage group is not available to every part of the app, so the response could not be delivered. Re-sign the app so its extensions share the secure group."
        case .keychainItemNotFound:
            return "The secure response was already used or is no longer present. Enter it again."
        case .keychainReadFailed:
            return "The secure response could not be read from secure storage. Try again."
        case .keychainDeleteFailed:
            return "The secure response could not be cleared from secure storage. Try again."
        case .tokenExpired:
            return "The secure response expired before SideStore received it. Enter it again."
        case .tokenMalformed:
            return "The secure response could not be decoded. Enter it again."
        case .capacity:
            return "Secure response storage is busy. Try again."
        case .sharedGroupUnavailable:
            return "The shared App Group is unavailable, so the secure channel is unavailable."
        }
    }
}

/// Serializes the full shared-Keychain admission transaction across the host
/// and embedded service processes. The count/purge callback and SecItemAdd
/// callback must remain within this one lock scope.
enum V3SecretHandoffStoreAdmission {
    static func add<T>(containerRoot: URL? = nil, selectedGroup: String? = nil, maximumOutstandingItems: Int,
                       liveItemCount: () throws -> Int,
                       insert: () throws -> T) throws -> T {
        try V3AppGroupProcessLock.withLock(containerRoot: containerRoot, selectedGroup: selectedGroup) {
            guard try liveItemCount() < maximumOutstandingItems else {
                throw V3SecretHandoffError.capacity
            }
            return try insert()
        }
    }
}

extension V3SecretHandoffError {
    /// Builds a typed handoff failure and reports it once, so no call site has
    /// to remember to emit.
    static func fail(_ reason: V3SecretHandoffFailure,
                     as base: V3SecretHandoffDiagnostics?,
                     operation: String,
                     osStatus: Int32 = 0, groupDiscovered: Bool = false,
                     tokenWellFormed: Bool = false) -> V3SecretHandoffError {
        var resolved = base ?? V3SecretHandoffDiagnostics(
            role: V3SecretHandoffRole.current, operation: operation)
        resolved.operation = operation
        resolved.failure = reason
        resolved.osStatus = osStatus
        if groupDiscovered { resolved.groupDiscovered = true }
        if tokenWellFormed { resolved.tokenWellFormed = true }
        V3SecretHandoffTrace.emit(resolved)
        return .unavailable(reason, osStatus: osStatus,
                            groupDiscovered: resolved.groupDiscovered,
                            tokenWellFormed: resolved.tokenWellFormed)
    }
}

enum V3SecretHandoffRecord {
    static let lifetime: TimeInterval = 120
    static let maximumPayloadBytes = 64 * 1024
    private static let allowedKinds: Set<String> = ["string", "stringDictionary"]

    static func isStrictVersionOne(_ value: Any?) -> Bool {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return false }
        let type = String(cString: number.objCType)
        return ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(type) &&
            number.intValue == 1
    }

    static func encode(kind: String, payload: Data, createdAt: Date) -> Data? {
        guard allowedKinds.contains(kind), !payload.isEmpty, payload.count <= maximumPayloadBytes else { return nil }
        let expiresAt = createdAt.addingTimeInterval(lifetime)
        let value: [String: Any] = ["version": 1, "kind": kind, "createdAt": createdAt,
            "expiresAt": expiresAt, "payload": payload]
        return try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
    }

    static func decode(_ data: Data, expectedKind: String, now: Date) -> Data? {
        guard !data.isEmpty, data.count <= maximumPayloadBytes + 4096,
              let value = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              Set(value.keys) == Set(["version", "kind", "createdAt", "expiresAt", "payload"]),
              isStrictVersionOne(value["version"]),
              value["kind"] as? String == expectedKind, allowedKinds.contains(expectedKind),
              let createdAt = value["createdAt"] as? Date,
              let expiresAt = value["expiresAt"] as? Date,
              let payload = value["payload"] as? Data, !payload.isEmpty,
              payload.count <= maximumPayloadBytes,
              createdAt <= now, expiresAt > now,
              expiresAt.timeIntervalSince(createdAt) <= lifetime else { return nil }
        return payload
    }
}

enum V3SharedFileRecord {
    static let lifetime: TimeInterval = 60 * 60
    static let maximumPayloadBytes = 4_194_304
    static let maximumPendingFiles = 16
    static let maximumPendingStoredBytes = 16_777_216
    private static let directoryComponents = ["Library", "Application Support", "LiveContainer", "V3SharedFileStaging"]
    private static let allowedPurposes: Set<String> = ["pairing", "sidesign", "accountImport"]
    private static let transactionLock = NSLock()

    static func stagingDirectory(containerRoot: URL) -> URL {
        directoryComponents.reduce(containerRoot.standardizedFileURL) {
            $0.appendingPathComponent($1, isDirectory: true)
        }.standardizedFileURL
    }

    static func stage(_ payload: Data, purpose: String, containerRoot: URL,
                      now: Date = Date(), fileManager: FileManager = .default) -> String? {
        transactionLock.lock()
        defer { transactionLock.unlock() }
        guard allowedPurposes.contains(purpose), !payload.isEmpty,
              payload.count <= maximumPayloadBytes else { return nil }
        guard let directory = ensureDirectory(containerRoot: containerRoot, fileManager: fileManager) else { return nil }
        guard let record = encode(payload, purpose: purpose, createdAt: now) else { return nil }
        let current = sweep(directory: directory, now: now, fileManager: fileManager)
        guard current.count < maximumPendingFiles,
              current.storedBytes <= maximumPendingStoredBytes - record.count else { return nil }
        let token = UUID().uuidString
        guard let file = fileURL(token: token, directory: directory),
              !fileManager.fileExists(atPath: file.path) else { return nil }
        do {
            try record.write(to: file, options: .atomic)
            try fileManager.setAttributes([.posixPermissions: 0o600, .modificationDate: now],
                                          ofItemAtPath: file.path)
            guard readRecordFile(file, directory: directory) != nil else {
                removeFile(file, directory: directory, fileManager: fileManager)
                return nil
            }
        } catch {
            removeFile(file, directory: directory, fileManager: fileManager)
            return nil
        }
        return token
    }

    static func consume(_ token: String, purpose: String, containerRoot: URL,
                        now: Date = Date(), fileManager: FileManager = .default) -> Data? {
        transactionLock.lock()
        defer { transactionLock.unlock() }
        guard isCanonicalToken(token), allowedPurposes.contains(purpose),
              let directory = existingDirectory(containerRoot: containerRoot, fileManager: fileManager),
              let file = fileURL(token: token, directory: directory),
              let data = readRecordFile(file, directory: directory) else { return nil }
        guard let record = decodeRecord(data, now: now) else {
            removeFile(file, directory: directory, fileManager: fileManager)
            return nil
        }
        guard record.purpose == purpose else { return nil }
        removeFile(file, directory: directory, fileManager: fileManager)
        return record.payload
    }

    static func discard(_ token: String, containerRoot: URL,
                        fileManager: FileManager = .default) {
        transactionLock.lock()
        defer { transactionLock.unlock() }
        guard isCanonicalToken(token),
              let directory = existingDirectory(containerRoot: containerRoot, fileManager: fileManager),
              let file = fileURL(token: token, directory: directory) else { return }
        removeFile(file, directory: directory, fileManager: fileManager)
    }

    static func removeLegacyDefaultsRecords(_ defaults: UserDefaults) {
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix("V3SharedFile.") {
            defaults.removeObject(forKey: key)
        }
    }

    @discardableResult
    static func sweep(containerRoot: URL, now: Date = Date(),
                      fileManager: FileManager = .default) -> (count: Int, storedBytes: Int) {
        transactionLock.lock()
        defer { transactionLock.unlock() }
        guard let directory = existingDirectory(containerRoot: containerRoot, fileManager: fileManager) else {
            return (0, 0)
        }
        return sweep(directory: directory, now: now, fileManager: fileManager)
    }

    private static func sweep(directory: URL, now: Date,
                              fileManager: FileManager) -> (count: Int, storedBytes: Int) {
        var count = 0
        var storedBytes = 0
        guard let files = try? fileManager.contentsOfDirectory(at: directory,
                includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey,
                    .fileSizeKey, .contentModificationDateKey],
                options: [.skipsHiddenFiles]) else { return (0, 0) }
        for file in files {
            guard file.pathExtension == "bin",
                  isCanonicalToken(file.deletingPathExtension().lastPathComponent),
                  file.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL,
                  let values = try? file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey,
                    .fileSizeKey, .contentModificationDateKey]),
                  values.isRegularFile == true, values.isSymbolicLink != true,
                  file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { continue }
            let size = values.fileSize ?? 0
            let modified = values.contentModificationDate ?? .distantPast
            guard size > 0, size <= maximumPayloadBytes + 4096,
                  modified <= now, now.timeIntervalSince(modified) <= lifetime else {
                removeFile(file, directory: directory, fileManager: fileManager)
                continue
            }
            count += 1
            storedBytes += size
        }
        return (count, storedBytes)
    }

    private static func ensureDirectory(containerRoot: URL, fileManager: FileManager) -> URL? {
        let root = containerRoot.resolvingSymlinksInPath().standardizedFileURL
        let directory = stagingDirectory(containerRoot: root)
        do {
            try fileManager.createDirectory(at: directory, withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: 0o700])
            let values = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values.isDirectory == true, values.isSymbolicLink != true,
                  directory.resolvingSymlinksInPath().standardizedFileURL == directory else { return nil }
            try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
            return directory
        } catch { return nil }
    }

    private static func existingDirectory(containerRoot: URL, fileManager: FileManager) -> URL? {
        let root = containerRoot.resolvingSymlinksInPath().standardizedFileURL
        let directory = stagingDirectory(containerRoot: root)
        guard let values = try? directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey]),
              values.isDirectory == true, values.isSymbolicLink != true,
              directory.resolvingSymlinksInPath().standardizedFileURL == directory else { return nil }
        return directory
    }

    private static func fileURL(token: String, directory: URL) -> URL? {
        guard isCanonicalToken(token) else { return nil }
        let file = directory.appendingPathComponent(token + ".bin", isDirectory: false).standardizedFileURL
        guard file.deletingLastPathComponent() == directory.standardizedFileURL,
              file.lastPathComponent == token + ".bin" else { return nil }
        return file
    }

    private static func readRecordFile(_ file: URL, directory: URL) -> Data? {
        guard file.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL,
              let values = try? file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]),
              values.isRegularFile == true, values.isSymbolicLink != true,
              (values.fileSize ?? 0) > 0, (values.fileSize ?? 0) <= maximumPayloadBytes + 4096,
              file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { return nil }
        guard let data = try? Data(contentsOf: file),
              data.count <= maximumPayloadBytes + 4096,
              file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { return nil }
        return data
    }

    private static func removeFile(_ file: URL?, directory: URL, fileManager: FileManager) {
        guard let file, file.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL,
              let values = try? file.resourceValues(forKeys: [.isSymbolicLinkKey]),
              values.isSymbolicLink != true,
              file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { return }
        try? fileManager.removeItem(at: file)
    }

    private static func encode(_ payload: Data, purpose: String, createdAt: Date) -> Data? {
        guard allowedPurposes.contains(purpose), !payload.isEmpty,
              payload.count <= maximumPayloadBytes else { return nil }
        let value: [String: Any] = ["version": 1, "purpose": purpose, "createdAt": createdAt,
            "expiresAt": createdAt.addingTimeInterval(lifetime), "payload": payload]
        return try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
    }

    private static func decodeRecord(_ data: Data, now: Date) -> (purpose: String, payload: Data)? {
        guard !data.isEmpty, data.count <= maximumPayloadBytes + 4096,
              let value = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              Set(value.keys) == Set(["version", "purpose", "createdAt", "expiresAt", "payload"]),
              V3SecretHandoffRecord.isStrictVersionOne(value["version"]),
              let purpose = value["purpose"] as? String, allowedPurposes.contains(purpose),
              let createdAt = value["createdAt"] as? Date,
              let expiresAt = value["expiresAt"] as? Date,
              let payload = value["payload"] as? Data, !payload.isEmpty,
              payload.count <= maximumPayloadBytes,
              createdAt <= now, expiresAt > now,
              expiresAt.timeIntervalSince(createdAt) <= lifetime else { return nil }
        return (purpose, payload)
    }

    private static func isCanonicalToken(_ token: String) -> Bool {
        guard let uuid = UUID(uuidString: token) else { return false }
        return uuid.uuidString == token
    }
}

enum V3SharedFileInputError: Error, LocalizedError {
    case unavailable
    case empty
    case tooLarge

    var errorDescription: String? {
        switch self {
        case .unavailable: return "The selected file is unavailable. Choose an accessible file and try again."
        case .empty: return "The selected file is empty. Choose a valid export file and try again."
        case .tooLarge: return "The selected import file is larger than 4 MiB."
        }
    }
}

enum V3SharedFileInput {
    static let maximumBytes = V3SharedFileRecord.maximumPayloadBytes
    static let chunkBytes = 64 * 1024

    // Inspect the provider URL before reading, then enforce the same bound while
    // streaming so a replaced/growing file cannot allocate an unbounded Data.
    // Callers hold security-scoped access for the duration of this method.
    static func readBounded(_ url: URL) throws -> Data {
        guard url.isFileURL,
              let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]),
              values.isRegularFile == true, values.isSymbolicLink != true,
              let fileSize = values.fileSize, fileSize > 0 else { throw V3SharedFileInputError.unavailable }
        guard fileSize <= maximumBytes else { throw V3SharedFileInputError.tooLarge }
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var data = Data()
        data.reserveCapacity(fileSize)
        while data.count <= maximumBytes {
            let remaining = maximumBytes + 1 - data.count
            guard let chunk = try handle.read(upToCount: min(chunkBytes, remaining)), !chunk.isEmpty else { break }
            data.append(chunk)
            if data.count > maximumBytes { throw V3SharedFileInputError.tooLarge }
        }
        guard !data.isEmpty else { throw V3SharedFileInputError.empty }
        return data
    }

    static func readBoundedAsync(_ url: URL) async throws -> Data {
        try await Task.detached(priority: .utility) {
            try V3SharedFileInput.readBounded(url)
        }.value
    }
}

enum V3SecretHandoff {
    private static let service = "com.kdt.livecontainer.v3-secret-handoff"
    private static let maximumOutstandingItems = 32

    static func isValidToken(_ token: String?) -> Bool {
        guard let token, let uuid = UUID(uuidString: token) else { return false }
        return uuid.uuidString == token
    }

    static func storeString(_ value: String, selectedGroup: String? = nil) throws -> String {
        guard value.utf8.count <= 8192 else { throw V3SecretHandoffError.malformed }
        return try store(Data(value.utf8), kind: "string", selectedGroup: selectedGroup)
    }

    static func consumeString(_ token: String, selectedGroup: String? = nil) throws -> String {
        let data = try consume(token, kind: "string", selectedGroup: selectedGroup)
        guard let value = String(data: data, encoding: .utf8), value.utf8.count <= 8192 else {
            throw V3SecretHandoffError.malformed
        }
        return value
    }

    static func storeStringDictionary(_ value: [String: String], selectedGroup: String? = nil) throws -> String {
        guard value.count <= 128,
              value.allSatisfy({ !$0.key.isEmpty && $0.key.utf8.count <= 256 && $0.value.utf8.count <= 4096 }),
              let data = try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0),
              data.count <= V3SecretHandoffRecord.maximumPayloadBytes else {
            throw V3SecretHandoffError.malformed
        }
        return try store(data, kind: "stringDictionary", selectedGroup: selectedGroup)
    }

    static func consumeStringDictionary(_ token: String, selectedGroup: String? = nil) throws -> [String: String] {
        let data = try consume(token, kind: "stringDictionary", selectedGroup: selectedGroup)
        guard let value = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: String],
              value.count <= 128,
              value.allSatisfy({ !$0.key.isEmpty && $0.key.utf8.count <= 256 && $0.value.utf8.count <= 4096 }) else {
            throw V3SecretHandoffError.malformed
        }
        return value
    }

    static func discard(_ token: String, selectedGroup: String? = nil) {
        guard isValidToken(token), let group = try? sharedKeychainAccessGroup() else { return }
        _ = try? V3AppGroupProcessLock.withLock(selectedGroup: selectedGroup) {
            _ = SecItemDelete(itemQuery(token, group: group) as CFDictionary)
        }
    }

    static func cleanupExpiredItems(selectedGroup: String? = nil) {
        guard let group = try? sharedKeychainAccessGroup() else { return }
        _ = try? V3AppGroupProcessLock.withLock(selectedGroup: selectedGroup) {
            let rows = try listedItems(group: group)
            _ = try removeExpiredItems(group: group, rows: rows, now: Date())
        }
    }

    private static func store(_ payload: Data, kind: String, selectedGroup: String? = nil) throws -> String {
        guard payload.count <= V3SecretHandoffRecord.maximumPayloadBytes else {
            throw V3SecretHandoffError.malformed
        }
        let group = try sharedKeychainAccessGroup()
        return try V3SecretHandoffStoreAdmission.add(selectedGroup: selectedGroup,
            maximumOutstandingItems: maximumOutstandingItems,
            liveItemCount: {
                let rows = try listedItems(group: group)
                return try removeExpiredItems(group: group, rows: rows, now: Date())
            },
            insert: {
                // Start the lifetime only after this request owns the shared
                // transaction lock and has passed the capacity check.
                let now = Date()
                let token = UUID().uuidString
                guard let record = V3SecretHandoffRecord.encode(kind: kind, payload: payload, createdAt: now) else {
                    throw V3SecretHandoffError.malformed
                }
                var query = itemQuery(token, group: group)
                query[kSecValueData as String] = record
                query[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
                let status = SecItemAdd(query as CFDictionary, nil)
                guard status == errSecSuccess else {
                    let reason: V3SecretHandoffFailure = status == errSecMissingEntitlement
                        ? .keychainExplicitGroupUnauthorized : .keychainReadFailed
                    throw V3SecretHandoffError.fail(reason,
                        as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                            operation: "secretStore", groupDiscovered: true, tokenWellFormed: true),
                        operation: "secretStore", osStatus: status,
                        groupDiscovered: true, tokenWellFormed: true)
                }
                V3SecretHandoffTrace.emit(V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "secretStore", failure: nil, groupDiscovered: true, tokenWellFormed: true))
                return token
            })
    }

    private static func consume(_ token: String, kind: String, selectedGroup: String? = nil) throws -> Data {
        let wellFormed = isValidToken(token)
        guard wellFormed else {
            let error = V3SecretHandoffError.invalidToken
            V3SecretHandoffTrace.emit(error.diagnostics)
            throw error
        }
        // The process-shared advisory lock surrounds both copy and delete.
        // This makes competing patched processes serialize the one-time take;
        // NSLock alone cannot coordinate separate app/service processes.
        return try V3AppGroupProcessLock.withLock(selectedGroup: selectedGroup) {
            try consumeLocked(token, kind: kind)
        }
    }

    private static func consumeLocked(_ token: String, kind: String) throws -> Data {
        // Any failure below reports itself, so a caller that only logs the
        // returned error still gets the step that failed.
        let group = try sharedKeychainAccessGroup()
        var query = itemQuery(token, group: group)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let record = result as? Data else {
            let reason: V3SecretHandoffFailure = status == errSecItemNotFound
                ? .keychainItemNotFound : (status == errSecMissingEntitlement
                  ? .keychainExplicitGroupUnauthorized : .keychainReadFailed)
            throw V3SecretHandoffError.fail(reason,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "secretLookup", groupDiscovered: true, tokenWellFormed: true),
                operation: "secretLookup", osStatus: status, groupDiscovered: true, tokenWellFormed: true)
        }
        let deleteStatus = SecItemDelete(itemQuery(token, group: group) as CFDictionary)
        guard deleteStatus == errSecSuccess || deleteStatus == errSecItemNotFound else {
            throw V3SecretHandoffError.fail(.keychainDeleteFailed,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "secretLookup", groupDiscovered: true, tokenWellFormed: true),
                operation: "secretLookup", osStatus: Int32(deleteStatus),
                groupDiscovered: true, tokenWellFormed: true)
        }
        guard let payload = V3SecretHandoffRecord.decode(record, expectedKind: kind, now: Date()) else {
            let error = V3SecretHandoffError.expired
            V3SecretHandoffTrace.emit(V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "secretDecode", failure: .tokenExpired,
                groupDiscovered: true, tokenWellFormed: true))
            throw error
        }
        V3SecretHandoffTrace.emit(V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
            operation: "secretDecode", failure: nil, groupDiscovered: true, tokenWellFormed: true))
        return payload
    }

    static func sharedKeychainAccessGroup() throws -> String {
        // SecTask entitlement APIs are not exposed by the iOS SDK. Ask the
        // public Keychain API which default access group this signed process
        // owns, then verify the derived shared group with an explicit add.
        //
        // The two steps fail for different reasons and must stay distinguishable.
        // Discovery uses this process's own default group and therefore always
        // succeeds for a signed process. The explicit probe is the one that a
        // re-sign breaks when the shared group is granted to the main app but
        // not to its extensions, which is the shape of the reported failure.
        let defaultGroup: String
        do {
            defaultGroup = try probeAccessGroup()
        } catch let error as V3SecretHandoffError {
            throw V3SecretHandoffError.fail(.keychainGroupDiscoveryFailed, as: error.diagnostics,
                                            operation: "groupDiscovery", osStatus: error.osStatusValue)
        }
        guard let group = V3SharedKeychainAccessGroupPolicy.sharedGroup(fromDefaultGroup: defaultGroup) else {
            throw V3SecretHandoffError.fail(.keychainGroupDiscoveryFailed,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupDiscovery", groupDiscovered: true),
                operation: "groupDiscovery")
        }
        do {
            let verified = try probeAccessGroup(explicitGroup: group)
            guard verified == group else {
                throw V3SecretHandoffError.fail(.keychainExplicitGroupUnauthorized,
                    as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                        operation: "groupAuthorize", groupDiscovered: true),
                    operation: "groupAuthorize", osStatus: Int32(errSecParam))
            }
        } catch let error as V3SecretHandoffError {
            // errSecMissingEntitlement is the signature of a group this process
            // was never granted. Report it as such rather than as a generic
            // read failure, because the two need different fixes.
            let reason: V3SecretHandoffFailure =
                error.osStatusValue == errSecMissingEntitlement || error.osStatusValue == errSecNoAccessForItem
                ? .keychainExplicitGroupUnauthorized : .keychainGroupDiscoveryFailed
            throw V3SecretHandoffError.fail(reason, as: error.diagnostics,
                operation: "groupAuthorize", osStatus: error.osStatusValue, groupDiscovered: true)
        }
        return group
    }

    /// The one group name both processes would derive if they were entitled to
    /// the same group. Exposed so callers can report which scope they selected
    /// without ever logging the identifier itself.
    static var sharedKeychainGroupName: String {
        V3SharedKeychainAccessGroupPolicy.sharedGroup(fromDefaultGroup: "AAAAAAAAAA.x") ?? ""
    }

    /// This process's own default Keychain access group.
    ///
    /// A signer that re-signs this bundle grants the shared group to the root
    /// bundle only, so the service extension can never be entitled to it. The
    /// embedded SideStore runs in exactly one process, which makes its own
    /// default group a legitimate owner of its credentials rather than a
    /// fallback that leaks them.
    static func processDefaultKeychainAccessGroup() throws -> String {
        try probeAccessGroup()
    }

    private static func probeAccessGroup(explicitGroup: String? = nil) throws -> String {
        let service = "com.kdt.livecontainer.v3-access-group-probe"
        let account = UUID().uuidString
        var item: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: kCFBooleanFalse,
            // Group discovery also runs during background refresh after the
            // first unlock, matching the credential client's accessibility.
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData as String: Data([0]),
            kSecReturnAttributes as String: true]
        var deletion: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: kCFBooleanFalse]
        if let explicitGroup {
            item[kSecAttrAccessGroup as String] = explicitGroup
            deletion[kSecAttrAccessGroup as String] = explicitGroup
        }
        var result: CFTypeRef?
        let status = SecItemAdd(item as CFDictionary, &result)
        guard status == errSecSuccess else {
            let reason: V3SecretHandoffFailure = explicitGroup != nil && status == errSecMissingEntitlement
                ? .keychainExplicitGroupUnauthorized
                : (explicitGroup == nil ? .keychainGroupDiscoveryFailed : .keychainReadFailed)
            throw V3SecretHandoffError.fail(reason,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupProbe", groupDiscovered: explicitGroup == nil),
                operation: "groupProbe", osStatus: status, groupDiscovered: explicitGroup == nil)
        }
        let group = (result as? [String: Any])?[kSecAttrAccessGroup as String] as? String
        let deleteStatus = SecItemDelete(deletion as CFDictionary)
        guard deleteStatus == errSecSuccess, let group else {
            throw V3SecretHandoffError.fail(.keychainDeleteFailed,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "groupProbe", groupDiscovered: true),
                operation: "groupProbe", osStatus: Int32(deleteStatus), groupDiscovered: true)
        }
        return group
    }

    private static func itemQuery(_ token: String, group: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: token,
         kSecAttrAccessGroup as String: group,
         kSecAttrSynchronizable as String: kCFBooleanFalse]
    }

    private static func listedItems(group: String) throws -> [[String: Any]] {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccessGroup as String: group,
            kSecAttrSynchronizable as String: kCFBooleanFalse,
            kSecReturnAttributes as String: true, kSecMatchLimit as String: kSecMatchLimitAll]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return [] }
        guard status == errSecSuccess else {
            let reason: V3SecretHandoffFailure = status == errSecMissingEntitlement
                ? .keychainExplicitGroupUnauthorized : .keychainReadFailed
            throw V3SecretHandoffError.fail(reason,
                as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                    operation: "secretList", groupDiscovered: true),
                operation: "secretList", osStatus: status, groupDiscovered: true)
        }
        if let rows = result as? [[String: Any]] { return rows }
        if let row = result as? [String: Any] { return [row] }
        throw V3SecretHandoffError.fail(.keychainReadFailed,
            as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                operation: "secretList", groupDiscovered: true),
            operation: "secretList", groupDiscovered: true)
    }

    private static func removeExpiredItems(group: String, rows: [[String: Any]], now: Date) throws -> Int {
        var retained = 0
        for row in rows {
            guard let token = row[kSecAttrAccount as String] as? String, isValidToken(token) else { continue }
            guard let createdAt = row[kSecAttrCreationDate as String] as? Date,
                  createdAt <= now, now.timeIntervalSince(createdAt) <= V3SecretHandoffRecord.lifetime else {
                let status = SecItemDelete(itemQuery(token, group: group) as CFDictionary)
                guard status == errSecSuccess || status == errSecItemNotFound else {
                    throw V3SecretHandoffError.fail(.keychainDeleteFailed,
                        as: V3SecretHandoffDiagnostics(role: V3SecretHandoffRole.current,
                            operation: "secretSweep", groupDiscovered: true, tokenWellFormed: true),
                        operation: "secretSweep", osStatus: Int32(status),
                        groupDiscovered: true, tokenWellFormed: true)
                }
                continue
            }
            retained += 1
        }
        return retained
    }
}

import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

// IPA bytes live only in a private directory inside the shared App Group.
// XPC carries a canonical UUID token; the service derives every path itself.
enum V3IPAStaging {
    private static let directoryComponents = ["Library", "Application Support", "LiveContainer", "V3IPAStaging"]
    /// The packaged group name, retained for diagnostics and for the packaged
    /// fallback ranking. It is never used as a fixed runtime group: a re-signed
    /// build may only be entitled to a team-suffixed variant, or to the group
    /// LiveContainer itself selected.
    static let sideStoreAppGroupIdentifier = V3SharedAppGroup.packagedGroup
    static let orphanRetention: TimeInterval = 24 * 60 * 60

    /// Staging, the secret handoff lock, the recovery journal, the service
    /// Keychain lock and the cross-process refresh store all resolve the group
    /// through V3SharedAppGroup, so they cannot end up in different containers.
    /// This entry point takes every input explicitly and never reads process
    /// state, so the same call with the same facts always yields the same
    /// container in both processes.
    static func sharedIdentity(selectedGroup: String? = nil,
                               inheritedGroup: String? = nil,
                               bundleInfo: [String: Any],
                               resolveContainer: (String) -> URL?) -> V3SharedAppGroup.Identity? {
        V3SharedAppGroup.identity(selectedGroup: selectedGroup, inheritedGroup: inheritedGroup,
                                  usesEnvironment: false, bundleInfo: bundleInfo,
                                  resolveContainer: resolveContainer)
    }

    static func sideStoreContainerRoot(bundleInfo: [String: Any],
                                       selectedGroup: String? = nil,
                                       resolveContainer: (String) -> URL?) -> URL? {
        sharedIdentity(selectedGroup: selectedGroup, bundleInfo: bundleInfo,
                       resolveContainer: resolveContainer)?.containerRoot
    }

    static func sideStoreContainerRoot(bundle: Bundle = .main,
                                       fileManager: FileManager = .default,
                                       selectedGroup: String? = nil) -> URL? {
        V3SharedAppGroup.runtimeIdentity(selectedGroup: selectedGroup, bundle: bundle,
                                         fileManager: fileManager)?.containerRoot
    }

    private final class CopyStatus: @unchecked Sendable {
        private let lock = NSLock()
        private var failed = false
        func markFailed() { lock.withLock { failed = true } }
        var didFail: Bool { lock.withLock { failed } }
    }

    static func canonicalToken(_ token: String) throws -> String {
        guard token.utf8.count == 36,
              let value = UUID(uuidString: token),
              value.uuidString.lowercased() == token else {
            throw CombinedIPAFileError(.invalidToken)
        }
        return token
    }

    static func stagingDirectory(containerRoot: URL) -> URL {
        directoryComponents.reduce(containerRoot.standardizedFileURL) {
            $0.appendingPathComponent($1, isDirectory: true)
        }.standardizedFileURL
    }

    private static func ensureDirectory(containerRoot: URL, fileManager: FileManager) throws -> URL {
        let root = containerRoot.resolvingSymlinksInPath().standardizedFileURL
        let directory = stagingDirectory(containerRoot: root)
        do {
            try fileManager.createDirectory(at: directory, withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: 0o700])
            let values = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values.isDirectory == true, values.isSymbolicLink != true,
                  directory.resolvingSymlinksInPath().standardizedFileURL == directory else {
                throw CombinedIPAFileError(.fileAccess)
            }
            try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
            return directory
        } catch let error as CombinedIPAFileError {
            throw error
        } catch {
            throw CombinedIPAFileError(.stagingFailed)
        }
    }

    private static func url(token: String, directory: URL) throws -> URL {
        let canonical = try canonicalToken(token)
        let candidate = directory.appendingPathComponent(canonical + ".ipa", isDirectory: false).standardizedFileURL
        guard candidate.deletingLastPathComponent() == directory.standardizedFileURL,
              candidate.lastPathComponent == canonical + ".ipa" else {
            throw CombinedIPAFileError(.invalidToken)
        }
        return candidate
    }

    private static func requireRegularNonEmptyFile(_ file: URL, fileManager: FileManager) throws {
        do {
            let values = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
            guard values.isSymbolicLink != true, values.isRegularFile == true else {
                throw CombinedIPAFileError(.missingFile)
            }
            guard (values.fileSize ?? 0) > 0 else { throw CombinedIPAFileError(.emptyFile) }
        } catch let error as CombinedIPAFileError {
            throw error
        } catch {
            throw CombinedIPAFileError(.missingFile)
        }
    }

    private static func removePartial(_ file: URL?, directory: URL?, fileManager: FileManager) {
        guard let file, let directory,
              file.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL,
              let values = try? file.resourceValues(forKeys: [.isSymbolicLinkKey]),
              values.isSymbolicLink != true,
              file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { return }
        try? fileManager.removeItem(at: file)
    }

    private static func copyLeaseURL(token: String, directory: URL) -> URL {
        directory.appendingPathComponent(token + ".lease", isDirectory: false)
    }

    /// A per-token flock survives actor/process scheduling and is released by
    /// the OS after a crash. Never wait for an active copy during orphan cleanup.
    private static func acquireCopyLease(token: String, directory: URL, create: Bool) throws -> Int32? {
        _ = try canonicalToken(token)
        let lease = copyLeaseURL(token: token, directory: directory)
        let flags = O_RDWR | O_NOFOLLOW | O_CLOEXEC | (create ? O_CREAT | O_EXCL : 0)
        let descriptor = open(lease.path, flags, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else {
            if create { throw CombinedIPAFileError(.stagingFailed) }
            return nil
        }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            _ = close(descriptor)
            if create { throw CombinedIPAFileError(.stagingFailed) }
            return nil
        }
        var opened = stat()
        var named = stat()
        // A writer may have paused between open and flock while a cleaner
        // acquired/unlinked the lease. It must not copy through that stale FD.
        guard fstat(descriptor, &opened) == 0, lstat(lease.path, &named) == 0,
              opened.st_dev == named.st_dev, opened.st_ino == named.st_ino,
              opened.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG),
              !create || fchmod(descriptor, S_IRUSR | S_IWUSR) == 0 else {
            _ = flock(descriptor, LOCK_UN)
            _ = close(descriptor)
            if create { throw CombinedIPAFileError(.stagingFailed) }
            return nil
        }
        return descriptor
    }

    private static func releaseCopyLease(_ descriptor: Int32) {
        _ = flock(descriptor, LOCK_UN)
        _ = close(descriptor)
    }

    /// Inputs are value snapshots: no store, picker, or mutable UI ownership
    /// crosses into this detached worker. Security scope and coordination stay
    /// inside stage() until the synchronous copy has fully returned.
    static func stageOffMainActor(sourceURL: URL, bookmark: Data? = nil, containerRoot: URL) async throws -> String {
        let worker = Task.detached(priority: .userInitiated) {
            try Task.checkCancellation()
            let token = try stage(sourceURL: sourceURL, bookmark: bookmark, containerRoot: containerRoot)
            guard !Task.isCancelled else {
                // Cancellation cannot interrupt FileManager.copyItem safely.
                // This token has no host/service owner until we return it.
                try? cleanup(token: token, containerRoot: containerRoot)
                throw CancellationError()
            }
            return token
        }
        return try await withTaskCancellationHandler(operation: {
            try await worker.value
        }, onCancel: {
            worker.cancel()
        })
    }

    /// Only for a completed token which was never handed to an install attempt.
    /// Active or terminal backend tokens still use the service lease checks.
    static func cleanupUnclaimedOffMainActor(token: String, containerRoot: URL) async {
        await Task.detached(priority: .utility) {
            try? cleanup(token: token, containerRoot: containerRoot)
        }.value
    }

    static func stage(sourceURL: URL, bookmark: Data? = nil, containerRoot: URL,
                      fileManager: FileManager = .default) throws -> String {
        var source = sourceURL
        if let bookmark {
            var stale = false
            do {
                source = try URL(resolvingBookmarkData: bookmark, options: .withoutUI,
                                 relativeTo: nil, bookmarkDataIsStale: &stale)
            } catch {
                throw CombinedIPAFileError(.fileAccess)
            }
            _ = stale // A stale bookmark is usable only for this immediate copy.
        }
        guard source.isFileURL, source.pathExtension.lowercased() == "ipa" else {
            throw CombinedIPAFileError(.invalidPackage)
        }

        let scoped = source.startAccessingSecurityScopedResource()
        defer { if scoped { source.stopAccessingSecurityScopedResource() } }
        var partialDirectory: URL?
        var partialDestination: URL?
        do {
            let sourceValues = try source.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
            guard sourceValues.isSymbolicLink != true, sourceValues.isRegularFile == true else { throw CombinedIPAFileError(.fileAccess) }
            guard (sourceValues.fileSize ?? 0) > 0 else { throw CombinedIPAFileError(.emptyFile) }
            let directory = try ensureDirectory(containerRoot: containerRoot, fileManager: fileManager)
            partialDirectory = directory
            let token = UUID().uuidString.lowercased()
            let destination = try url(token: token, directory: directory)
            guard !fileManager.fileExists(atPath: destination.path) else {
                throw CombinedIPAFileError(.stagingFailed)
            }
            // An in-flight copy is not a published IPA token. In particular,
            // an old source mtime must not let orphan pruning delete a file
            // while the provider/FileManager is still writing it.
            let partial = directory.appendingPathComponent(token + ".partial", isDirectory: false)
            guard !fileManager.fileExists(atPath: partial.path) else {
                throw CombinedIPAFileError(.stagingFailed)
            }
            guard let lease = try acquireCopyLease(token: token, directory: directory, create: true) else {
                throw CombinedIPAFileError(.stagingFailed)
            }
            defer {
                // Keep the lease if cleanup still owes a partial file. A later
                // orphan pass can reclaim both after proving no process owns it.
                if !fileManager.fileExists(atPath: partial.path) {
                    try? fileManager.removeItem(at: copyLeaseURL(token: token, directory: directory))
                }
                releaseCopyLease(lease)
            }
            partialDestination = partial
            defer { removePartial(partialDestination, directory: partialDirectory, fileManager: fileManager) }
            let coordinator = NSFileCoordinator(filePresenter: nil)
            var coordinationError: NSError?
            let copyStatus = CopyStatus()
            coordinator.coordinate(readingItemAt: source, options: [], error: &coordinationError) { readableURL in
                do { try fileManager.copyItem(at: readableURL, to: partial) }
                catch { copyStatus.markFailed() }
            }
            guard coordinationError == nil, !copyStatus.didFail else { throw CombinedIPAFileError(.stagingFailed) }
            try fileManager.setAttributes([.posixPermissions: 0o600, .modificationDate: Date()],
                                          ofItemAtPath: partial.path)
            try requireRegularNonEmptyFile(partial, fileManager: fileManager)
            try fileManager.moveItem(at: partial, to: destination)
            partialDestination = nil
            return token
        } catch let error as CombinedIPAFileError {
            removePartial(partialDestination, directory: partialDirectory, fileManager: fileManager)
            throw error
        } catch {
            removePartial(partialDestination, directory: partialDirectory, fileManager: fileManager)
            throw CombinedIPAFileError(.stagingFailed)
        }
    }

    static func resolve(token: String, containerRoot: URL,
                        fileManager: FileManager = .default) throws -> URL {
        let directory = try ensureDirectory(containerRoot: containerRoot, fileManager: fileManager)
        let file = try url(token: token, directory: directory)
        try requireRegularNonEmptyFile(file, fileManager: fileManager)
        guard file.resolvingSymlinksInPath().standardizedFileURL == file else {
            throw CombinedIPAFileError(.missingFile)
        }
        return file
    }

    static func cleanup(token: String, containerRoot: URL,
                        fileManager: FileManager = .default) throws {
        let directory = try ensureDirectory(containerRoot: containerRoot, fileManager: fileManager)
        let file = try url(token: token, directory: directory)
        guard fileManager.fileExists(atPath: file.path) else { return }
        do {
            let values = try file.resourceValues(forKeys: [.isSymbolicLinkKey])
            guard values.isSymbolicLink != true,
                  file.resolvingSymlinksInPath().standardizedFileURL == file else {
                throw CombinedIPAFileError(.fileAccess)
            }
            try fileManager.removeItem(at: file)
        } catch let error as CombinedIPAFileError {
            throw error
        } catch {
            throw CombinedIPAFileError(.fileAccess)
        }
    }

    /// Recover canonical IPA files absent from the ownership snapshot, plus
    /// abandoned partial-copy records whose per-token lease can be acquired.
    /// Age alone never establishes that a copy or backend token is unowned.
    @discardableResult
    static func cleanupOrphans(containerRoot: URL, preservingTokens: Set<String>, now: Date = Date(),
                               fileManager: FileManager = .default) throws -> Int {
        let directory = try ensureDirectory(containerRoot: containerRoot, fileManager: fileManager)
        let files: [URL]
        do {
            files = try fileManager.contentsOfDirectory(at: directory,
                includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey, .contentModificationDateKey],
                options: [.skipsHiddenFiles])
        } catch {
            throw CombinedIPAFileError(.stagingFailed)
        }
        var removed = 0
        for file in files {
            guard file.deletingLastPathComponent().standardizedFileURL == directory.standardizedFileURL else { continue }
            if file.pathExtension == "lease" {
                let token = file.deletingPathExtension().lastPathComponent
                guard (try? canonicalToken(token)) == token, !preservingTokens.contains(token),
                      let lease = try acquireCopyLease(token: token, directory: directory, create: false) else { continue }
                defer { releaseCopyLease(lease) }
                // Re-read age after taking the lock, and only touch a sibling
                // regular partial belonging to this exact canonical token.
                guard let values = try? file.resourceValues(forKeys: [.contentModificationDateKey]),
                      let modified = values.contentModificationDate,
                      now.timeIntervalSince(modified) >= orphanRetention else { continue }
                let partial = directory.appendingPathComponent(token + ".partial", isDirectory: false)
                do {
                    if fileManager.fileExists(atPath: partial.path) {
                        let partialValues = try partial.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
                        guard partialValues.isRegularFile == true, partialValues.isSymbolicLink != true,
                              partial.resolvingSymlinksInPath().standardizedFileURL == partial else { continue }
                        try fileManager.removeItem(at: partial)
                    }
                    try fileManager.removeItem(at: file)
                    removed += 1
                } catch { continue }
                continue
            }
            guard file.pathExtension == "ipa" else { continue }
            let token = file.deletingPathExtension().lastPathComponent
            guard (try? canonicalToken(token)) == token,
                  !preservingTokens.contains(token),
                  !fileManager.fileExists(atPath: copyLeaseURL(token: token, directory: directory).path),
                  let values = try? file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .contentModificationDateKey]),
                  values.isRegularFile == true, values.isSymbolicLink != true,
                  let modified = values.contentModificationDate,
                  now.timeIntervalSince(modified) >= orphanRetention,
                  file.resolvingSymlinksInPath().standardizedFileURL == file.standardizedFileURL else { continue }
            do {
                try fileManager.removeItem(at: file)
                removed += 1
            } catch {
                // One undeletable orphan must not block staging or cleanup for
                // the remaining canonical files.
                continue
            }
        }
        return removed
    }

    static func inspect<T>(token: String, containerRoot: URL,
                           fileManager: FileManager = .default,
                           readMetadata: (URL) throws -> T) throws -> T {
        let file = try resolve(token: token, containerRoot: containerRoot, fileManager: fileManager)
        do { return try readMetadata(file) }
        catch let error as CombinedIPAFileError { throw error }
        catch { throw CombinedIPAFileError(.invalidPackage) }
    }
}

import SwiftUI
import Combine
import SideStoreSupport
import UniformTypeIdentifiers
import UIKit
import CoreFoundation
import CryptoKit
import Security

// V3_UNIFIED_SHELL_V1_BEGIN
enum V3AppIdentity: Hashable {
    case guest(path: String)
    case installed(uri: String)
    case source(identifier: String)
}

private final class V3AuthReadinessSequenceStorage: @unchecked Sendable {
    private let lock = NSLock()
    private var value: UInt64 = 0

    func next() -> UInt64 {
        lock.lock()
        defer { lock.unlock() }
        value &+= 1
        if value == 0 { value = 1 }
        return value
    }
}

// V3_AUTH_READINESS_REFRESH_EVENT_V1: auth tasks can outlive the sign-in view.
// Send terminal certificate-state changes to the app-owned root observer.
enum V3AuthReadinessRefreshEvent {
    private static let sequenceStorage = V3AuthReadinessSequenceStorage()
    static let notificationName = Notification.Name("V3AuthReadinessRefresh")
    static let sessionIDKey = "sessionID"
    static let attemptSequenceKey = "attemptSequence"

    static func nextAttemptSequence() -> UInt64 {
        sequenceStorage.next()
    }

    static func post(sessionID: String?, attemptSequence: UInt64?) {
        guard let sessionID, UUID(uuidString: sessionID)?.uuidString == sessionID,
              let attemptSequence, attemptSequence > 0 else { return }
        NotificationCenter.default.post(name: notificationName, object: nil,
            userInfo: [sessionIDKey: sessionID, attemptSequenceKey: attemptSequence])
    }

    static func sessionID(from notification: Notification) -> String? {
        guard let sessionID = notification.userInfo?[sessionIDKey] as? String,
              UUID(uuidString: sessionID)?.uuidString == sessionID else { return nil }
        return sessionID
    }

    static func attemptSequence(from notification: Notification) -> UInt64? {
        guard let number = notification.userInfo?[attemptSequenceKey] as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        // NSNumber.uint64Value wraps negatives and truncates floating-point
        // inputs. Accept only Objective-C integer encodings, then parse their
        // exact decimal representation so overflow and negative values fail.
        let type = String(cString: number.objCType)
        guard ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(type),
              let sequence = UInt64(number.stringValue) else { return nil }
        return sequence
    }
}

struct V3AuthReadinessRefreshEventLedger {
    private(set) var highestConsumedAttemptSequence: UInt64 = 0

    mutating func claim(attemptSequence: UInt64?) -> Bool {
        guard let attemptSequence, attemptSequence > highestConsumedAttemptSequence else { return false }
        highestConsumedAttemptSequence = attemptSequence
        return true
    }
}

// V3_SETUP_READINESS_SNAPSHOT_POLICY_V1: a cached readiness belongs to the
// exact setup-fact revision that observed it. Keep the active-certificate fact
// in the same value so consumers cannot combine facts from different reads.
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

// V3_MULTISELECT_PROMPT_ANSWER_POLICY_V1: option IDs representing actions must
// be routed as choices, while only member IDs may enter the selected-ID list.
enum V3MultiSelectPromptAnswerPolicy {
    static func isMemberOption(kind: String, optionID: String) -> Bool {
        if ["keep", "keepAll"].contains(optionID) { return false }
        if kind == "extensions" && ["keepAllMainProfile", "removeAll", "cancel"].contains(optionID) { return false }
        return true
    }

    static func actionAnswer(_ actionID: String, fields: [String: String]) -> [String: String] {
        var answer = fields
        answer["choice"] = actionID
        answer.removeValue(forKey: "ids")
        answer.removeValue(forKey: "serials")
        return answer
    }

    static func selectedMembersAnswer(kind: String, selectedIDs: Set<String>,
                                      fields: [String: String]) -> [String: String] {
        var answer = fields
        answer["choice"] = kind == "revocation" ? "revoke" : "selected"
        answer["ids"] = selectedIDs.sorted().joined(separator: ",")
        answer["serials"] = selectedIDs.sorted().joined(separator: ",")
        return answer
    }
}

enum V3AuthRetryReadinessReconciliationPolicy {
    static func shouldPublish(sessionID: String?, expectedSessionID: String?, ownsRetry: Bool,
                              authenticated: Bool, authenticationActive: Bool,
                              provisioningIncomplete: Bool, attemptSequence: UInt64?) -> Bool {
        guard let sessionID, UUID(uuidString: sessionID)?.uuidString == sessionID,
              sessionID == expectedSessionID, ownsRetry, authenticated,
              !authenticationActive, !provisioningIncomplete,
              let attemptSequence else { return false }
        return attemptSequence > 0
    }
}

extension LCAppModel {
    var v3Identity: V3AppIdentity { .guest(path: appInfo.relativeBundlePath ?? appInfo.bundlePath() ?? "") }
}

struct V3UnifiedShell: View {
    @AppStorage(V3AppLanguage.storageKey, store: V3Localization.hostDefaults) private var language = V3AppLanguage.english.rawValue
    var body: some View {
        V3ApplicationRoot(content: V3UnifiedTabs())
            .environment(\.locale, Locale(identifier: V3AppLanguage.resolve(language).rawValue))
            .environment(\.layoutDirection, V3AppLanguage.resolve(language).isRightToLeft ? .rightToLeft : .leftToRight)
    }
}

struct V3UnifiedTabs: View {
    @EnvironmentObject private var sharedModel: SharedModel
    @StateObject private var status = V3SideStoreStatusStore()
    @State private var showNotificationsPrompt = false
    @State private var showOperationDeviceCheck = false
    @State private var showRefreshDeviceCheck = false
    @State private var showUnreadableRecoveryDeviceCheck = false
    @State private var showDirectRecoveryDeviceCheck = false
    private let monitor = Timer.publish(every: 30, on: .main, in: .common).autoconnect()
    var body: some View {
        TabView(selection: $sharedModel.selectedTab) {
            V3HomeView().tabItem { Label("Home", systemImage: "house.fill") }.tag(LCTabIdentifier.home)
            LCAppListView().tabItem { Label("Apps", systemImage: "square.stack.3d.up.fill") }.tag(LCTabIdentifier.apps)
            V3SourcesView().tabItem { Label("Sources", systemImage: "books.vertical") }.tag(LCTabIdentifier.sources)
            LCSettingsView().tabItem { Label("Settings", systemImage: "gearshape.fill") }.tag(LCTabIdentifier.settings)
        }
        .environmentObject(status)
        .environment(\.v3StatusStore, status)
        .accessibilityIdentifier("V3_UNIFIED_SHELL_V1")
        .task {
            status.reload(manual: false)
            routePendingSetup()
            if !UserDefaults.standard.bool(forKey: "V3NotificationsPromptShown") {
                UserDefaults.standard.set(true, forKey: "V3NotificationsPromptShown")
                showNotificationsPrompt = true
            }
            if let pending = UserDefaults.standard.string(forKey: "V3PendingSideStoreURL"), let url = URL(string: pending) {
                UserDefaults.standard.removeObject(forKey: "V3PendingSideStoreURL")
                if url.isFileURL {
                    status.stageSharedIPA(url, bookmark: LCUtils.appGroupUserDefault.data(forKey: "LCLaunchExtensionFileBookmark"), title: "Install shared app")
                    LCUtils.appGroupUserDefault.removeObject(forKey: "LCLaunchExtensionFileBookmark")
                } else { dispatchURL(url) }
            }
            // Orphan pruning needs a SideStore ownership query and can wait for
            // a cold service start. Launch it only after first status and any
            // incoming install/setup route have been admitted.
            await status.cleanupOrphanedStagedIPAs()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            if !status.setupPresented { status.invalidateSetupFacts() }
            status.reload(manual: false)
            routePendingSetup()
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3StatusMutationReserved"))) { _ in
            status.statusAuthorityInvalidated()
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3StatusAuthorityChanged"))) { _ in
            status.statusAuthorityInvalidated()
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3CanonicalJITLessCertificateUpdated"))) { _ in
            // V3_AWAITABLE_RELOAD_V1: a certificate import just changed
            // authoritative state. The snapshot is awaited before Setup is
            // reopened, so the assistant never recomputes JIT-Less from the
            // pre-import snapshot.
            // Invalidate before yielding: an older callback may already be queued.
            status.invalidateSetupFacts()
            Task {
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
        .onReceive(NotificationCenter.default.publisher(for: V3AuthReadinessRefreshEvent.notificationName)) { notification in
            guard let sessionID = V3AuthReadinessRefreshEvent.sessionID(from: notification),
                  let attemptSequence = V3AuthReadinessRefreshEvent.attemptSequence(from: notification) else { return }
            status.refreshSetupFactsAfterAuthentication(sessionID: sessionID,
                attemptSequence: attemptSequence)
        }
        .onReceive(monitor) { _ in status.reload(manual: false) }
        .onOpenURL(perform: dispatchURL)
        // V3_USER_FACING_ISSUE_V1: a source failure routes the user to Sources.
        // Without this the flag was written but never read, so the action would
        // have appeared to do nothing.
        .onChange(of: status.sourcesPresented) { presented in
            guard presented else { return }
            status.sourcesPresented = false
            sharedModel.selectedTab = .sources
        }
        .safeAreaInset(edge: .bottom) {
            if status.installAttempt.phase == .staging {
                HStack(spacing: 12) {
                    ProgressView("Preparing IPA…")
                    Spacer()
                    Button("Cancel") { status.cancelIPAStaging() }
                        .accessibilityIdentifier("V3_IPA_STAGING_CANCEL")
                }
                .padding(12)
                .background(.regularMaterial)
                .accessibilityIdentifier("V3_IPA_STAGING_PROGRESS")
            }
        }
        .overlay(alignment: .topLeading) {
            V3InstallPickerPresenter(status: status)
                .frame(width: 1, height: 1)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            if let recovery = status.unresolvedOperationRecovery {
                VStack(alignment: .leading, spacing: 8) {
                    V3LocalizedText("A previous \(recovery.kind) may still be running")
                        .font(.subheadline.weight(.semibold))
                    V3LocalizedText(recovery.phase == .dispatched
                        ? "Check its status or confirm on the device that the operation has stopped."
                        : "The service prepared an operation but did not confirm dispatch. Check the device before clearing this hold.")
                        .font(.caption)
                    if recovery.phase == .dispatched {
                        Button("Resume Status Check") { status.resumeOperationRecovery() }
                            .font(.caption.weight(.semibold))
                    }
                    Button("I checked; device operation has stopped") {
                        showOperationDeviceCheck = true
                    }
                    .font(.caption.weight(.semibold))
                    .confirmationDialog("Reconcile the previous operation?", isPresented: $showOperationDeviceCheck,
                        titleVisibility: .visible) {
                        Button("I checked; clear the recovery hold", role: .destructive) {
                            status.reconcileDurableOperationAfterDeviceCheck()
                        }
                        Button("Keep waiting", role: .cancel) {}
                    } message: {
                        Text("Only continue after confirming the device is no longer installing, updating, refreshing, backing up, restoring, or deleting the app.")
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.regularMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.horizontal, 12)
                .padding(.top, 4)
            } else if let direct = status.unresolvedDirectRecovery {
                VStack(alignment: .leading, spacing: 8) {
                    V3LocalizedText("A previous \(V3DirectRecoveryPresentationPolicy.operationName(direct.operation)) has an unresolved result")
                        .font(.subheadline.weight(.semibold))
                    V3LocalizedText(V3DirectRecoveryPresentationPolicy.explanation(
                        record: direct, postcondition: status.directRecoveryPostcondition))
                        .font(.caption)
                    Button(LocalizedStringKey(status.directRecoveryInspecting ? "Inspecting..." : "Inspect result")) {
                        status.inspectDirectRecovery()
                    }
                    .disabled(status.directRecoveryInspecting)
                        .font(.caption.weight(.semibold))
                    if V3DirectRecoveryHostPolicy.mayOfferUserConfirmation(
                        direct, postcondition: status.directRecoveryPostcondition) {
                        Button("I checked the account or device", role: .destructive) {
                            showDirectRecoveryDeviceCheck = true
                        }
                        .font(.caption.weight(.semibold))
                        .confirmationDialog("Reconcile the previous request?",
                            isPresented: $showDirectRecoveryDeviceCheck, titleVisibility: .visible) {
                            Button("Confirm after checking", role: .destructive) {
                                status.reconcileDirectRecoveryAfterUserCheck()
                            }
                            Button("Keep recovery hold", role: .cancel) {}
                        } message: {
                            Text("Only clear this request after checking the account, source, certificate, pairing, or setting it may have changed.")
                        }
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.regularMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.horizontal, 12)
                .padding(.top, 4)
            } else if status.unresolvedRefreshRecoveryRunID != nil {
                VStack(alignment: .leading, spacing: 8) {
                    Text("A previous refresh may still be running")
                        .font(.subheadline.weight(.semibold))
                    Text("The service lost its refresh owner after the wait limit. Check the device before clearing the hold.")
                        .font(.caption)
                    Button("I checked; refresh has stopped") { showRefreshDeviceCheck = true }
                        .font(.caption.weight(.semibold))
                        .confirmationDialog("Reconcile the previous refresh?", isPresented: $showRefreshDeviceCheck,
                            titleVisibility: .visible) {
                            Button("I checked; clear the refresh hold", role: .destructive) {
                                status.reconcileLostRefreshAfterDeviceCheck()
                            }
                            Button("Keep waiting", role: .cancel) {}
                        } message: {
                            Text("Only continue after confirming SideStore is no longer refreshing apps on the device.")
                        }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.regularMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.horizontal, 12)
                .padding(.top, 4)
            } else if status.unresolvedRecoveryJournalUnreadable {
                VStack(alignment: .leading, spacing: 8) {
                    V3LocalizedText(status.recoveryStorageTitle)
                        .font(.subheadline.weight(.semibold))
                    V3LocalizedText(status.recoveryStorageGuidance)
                        .font(.caption)
                    if status.canDiscardUnreadableRecovery {
                        Button("I checked; no SideStore operation is running") {
                            showUnreadableRecoveryDeviceCheck = true
                        }
                        .font(.caption.weight(.semibold))
                        .confirmationDialog("Clear the unreadable recovery record?",
                            isPresented: $showUnreadableRecoveryDeviceCheck, titleVisibility: .visible) {
                            Button("Clear after device check", role: .destructive) {
                                status.discardUnreadableRecoveryAfterDeviceCheck()
                            }
                            Button("Keep waiting", role: .cancel) {}
                        } message: {
                            Text("Only continue after confirming the device is no longer installing, updating, refreshing, or deleting an app.")
                        }
                    }
                    Button("Copy Diagnostics") {
                        UIPasteboard.general.string = "diagnostic_code=\(status.recoveryStorageDiagnosticCode) builder_commit=\(V3DiagnosticBuild.commit)\n" + status.recoveryStorageDiagnostics
                    }
                    .font(.caption.weight(.semibold))
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.regularMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.horizontal, 12)
                .padding(.top, 4)
            }
        }
        .fullScreenCover(item: $status.presentation, onDismiss: {
            status.operationCoverDidDismiss()
            operationSheetDidDismiss()
        }) { request in
            V3OperationSheet(request: request).environmentObject(status)
        }
        .sheet(isPresented: $status.signInPresented, onDismiss: {
            status.reload()
            routePendingCanonicalJITLessSetup()
        }) {
            NavigationView { V3SignInView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .sheet(isPresented: $status.setupPresented, onDismiss: { routePendingCanonicalJITLessSetup() }) {
            NavigationView { V3SetupAssistantView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .sheet(isPresented: $status.connectionPresented) {
            NavigationView { V3ConnectionView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .sheet(isPresented: $status.certificatesPresented) {
            NavigationView { V3CertificatesView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .sheet(isPresented: $status.pairingPresented) {
            NavigationView { V3PairingView().environmentObject(status) }
                .navigationViewStyle(StackNavigationViewStyle())
        }
        .alert(LocalizedStringKey(status.issue?.title ?? "SideStore"),
              isPresented: Binding(get: { status.error != nil },
                                   set: { if !$0 { status.clearIssue() } })) {
            // V3_USER_FACING_ISSUE_V1: the primary action is the one the typed
            // evidence supports. Connection failures open Connection Settings,
            // and "Retry Source" re-requests sources rather than reloading status.
            //
            // A plain message with no structured issue has no action to offer.
            // It used to render a button labelled "OK" that then did nothing,
            // alongside a second "OK" that dismissed, so a refusal to work
            // looked like a choice.
                if let action = status.issue?.primaryAction, action != .dismiss {
                Button(LocalizedStringKey(action.title)) {
                    if status.performPrimaryIssueAction() {
                        status.clearIssue()
                    }
                }
            }
            if status.hasUncertainInstallCancellation {
                Button("Retry Cancellation") { status.retryInstallCancellation() }
            }
            Button("Copy Diagnostics") {
                UIPasteboard.general.string = V3DiagnosticCopy.details(visibleMessage: status.issue?.whatHappened ?? status.error ?? "",
                    technical: status.issue?.technicalDetails ?? status.error ?? "")
            }
            Button("OK", role: .cancel) { status.clearIssue() }
        } message: {
            VStack(alignment: .leading, spacing: 6) {
                V3LocalizedText(status.issue?.whatHappened ?? status.error ?? "")
                if let whatToDo = status.issue?.whatToDo, !whatToDo.isEmpty {
                    Text("What you can do").font(.caption.weight(.semibold))
                    V3LocalizedText(whatToDo).font(.caption)
                }
            }
        }
        .alert("SideStore", isPresented: Binding(get: { status.notice != nil }, set: { if !$0 { status.notice = nil } })) {
            Button("OK", role: .cancel) { status.notice = nil }
        } message: { V3LocalizedText(status.notice ?? "") }
        .alert("Stay Informed About Refreshes", isPresented: $showNotificationsPrompt) {
            Button("Allow Notifications") {
                Task { await LiveContainerAutoRefreshScheduler.requestNotificationPermissionFromUserAction() }
            }
            Button("Later", role: .cancel) {}
        } message: {
            Text("LiveContainer can notify you when a refresh starts, completes, or needs attention. Nothing runs differently if you skip this.")
        }
    }
    private func routePendingSetup() {
        guard LCUtils.appGroupUserDefault.bool(forKey: "V3PendingSetupAssistant") else { return }
        LCUtils.appGroupUserDefault.removeObject(forKey: "V3PendingSetupAssistant")
        NSLog("[V3_SETUP] OPEN source=shortcut")
        status.setupPresented = true
    }
    private func routePendingCanonicalJITLessSetup() {
        guard status.pendingCanonicalJITLessSetup else { return }
        status.pendingCanonicalJITLessSetup = false
        sharedModel.selectedTab = .settings
        sharedModel.deepLink = URL(string: "livecontainer://jitless-setup")
    }
    private func operationSheetDidDismiss() {
        guard let destination = status.operationRecoveryDestination else { return }
        status.operationRecoveryDestination = nil
        switch destination {
        case "signIn": status.signInPresented = true
        case "certificates": status.certificatesPresented = true
        case "ipa": status.beginInstallPicker()
        case "sources": sharedModel.selectedTab = .sources
        case "setup": status.setupPresented = true
        case "connection": status.connectionPresented = true
        default: break
        }
    }
    private func dispatchURL(_ url: URL) {
        if ["https", "http"].contains(url.scheme?.lowercased() ?? "") {
            status.perform("installURL", target: url.absoluteString, title: "Install shared app")
            return
        }
        if url.host?.lowercased() == "livecontainer-launch",
           let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
           query.contains(where: { $0.name == "bundle-name" && $0.value == "builtinSideStore" }) {
            if let encoded = query.first(where: { $0.name == "open-url" })?.value,
               let data = Data(base64Encoded: encoded), let value = String(data: data, encoding: .utf8),
               let selected = URL(string: value) {
                if selected.isFileURL {
                    let bookmark = LCUtils.appGroupUserDefault.data(forKey: "LCLaunchExtensionFileBookmark")
                    status.stageSharedIPA(selected, bookmark: bookmark, title: "Install shared app")
                    LCUtils.appGroupUserDefault.removeObject(forKey: "LCLaunchExtensionFileBookmark")
                } else { dispatchURL(selected) }
            } else { sharedModel.selectedTab = .settings }
            return
        }
        if url.scheme?.lowercased() == "sidestore", url.host?.lowercased() == "appbackupresponse" {
            Task {
                do { try await V3ServiceBridge.shared.submitBackupCallback(url) }
                catch { status.present(error) }
            }
            return
        }
        if url.scheme?.lowercased() == "sidestore", url.host?.lowercased() == "install" {
            if let target = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name.lowercased() == "url" })?.value {
                status.perform("installURL", target: target, title: "Install app")
            }
            return
        }
        if url.scheme?.lowercased() == "sidestore", url.host?.lowercased() == "enable-jit" {
            let bundle = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "bundle-id" })?.value
            Task {
                do {
                    status.accept(try await V3ServiceBridge.shared.request(operation: "snapshot"))
                    guard let app = status.installedApps.first(where: {
                        $0.bundleID == bundle || ($0.isHost && bundle == Bundle.main.bundleIdentifier)
                    }) else { status.error = "This app is not in SideStore's library." + "\nError ID: SS-CAT-D001"; return }
                    status.perform("jit", target: app.identifier, title: "Enable JIT for " + app.name)
                } catch { status.present(error) }
            }
            return
        }
        if url.host?.lowercased() == "source" {
            sharedModel.selectedTab = .sources
            let source = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "url" })?.value
            status.requestSourceForm(prefilledURL: source)
            return
        }
        if url.isFileURL || url.scheme?.lowercased() == "sidestore" { sharedModel.selectedTab = .apps }
        else {
            switch url.host?.lowercased() {
            case "livecontainer-launch", "install", "open-web-page", "open-url": sharedModel.selectedTab = .apps
            case "certificate": sharedModel.selectedTab = .settings
            case "setup":
                NSLog("[V3_SETUP] OPEN source=deep-link")
                status.setupPresented = true
            case "refresh":
                sharedModel.selectedTab = .home
                status.refreshPresented = true
            default: return
            }
        }
        sharedModel.deepLink = url
    }
}

@MainActor
final class V3InstallPickerAnchorController: UIViewController {
    var onDidAppear: (() -> Void)?

    override func loadView() {
        let anchorView = UIView(frame: .zero)
        anchorView.backgroundColor = .clear
        anchorView.isUserInteractionEnabled = false
        view = anchorView
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        onDidAppear?()
    }
}

// The initial tap presents UIDocumentPickerViewController directly from a
// root-attached UIKit controller. The operation cover is requested only after
// UIKit reports that this picker has actually left the presentation stack.
struct V3InstallPickerPresenter: UIViewControllerRepresentable {
    @ObservedObject var status: V3SideStoreStatusStore

    func makeCoordinator() -> Coordinator { Coordinator(status: status) }

    func makeUIViewController(context: Context) -> V3InstallPickerAnchorController {
        let controller = V3InstallPickerAnchorController()
        context.coordinator.attach(controller)
        return controller
    }

    func updateUIViewController(_ controller: V3InstallPickerAnchorController, context: Context) {
        context.coordinator.update(status: status)
    }

    static func dismantleUIViewController(_ controller: V3InstallPickerAnchorController,
                                           coordinator: Coordinator) {
        coordinator.detach()
    }

    @MainActor
    final class Coordinator: NSObject, UIDocumentPickerDelegate, UIAdaptivePresentationControllerDelegate {
        private weak var anchor: V3InstallPickerAnchorController?
        private weak var status: V3SideStoreStatusStore?
        private let presentation = V3InstallPickerPresentationCoordinator()
        private var picker: UIDocumentPickerViewController?
        private var selectionAccepted = false
        private var isDetaching = false

        init(status: V3SideStoreStatusStore) { self.status = status }

        func attach(_ controller: V3InstallPickerAnchorController) {
            anchor = controller
            controller.onDidAppear = { [weak self] in self?.anchorDidAppear() }
            if let status { update(status: status) }
        }

        func update(status: V3SideStoreStatusStore) {
            self.status = status
            guard let attemptID = status.installAttempt.attemptID,
                  status.installAttempt.phase == .pickerPresented,
                  presentation.phase == .idle else { return }
            let ready = anchor?.viewIfLoaded?.window != nil
            let decision = presentation.request(attemptID: attemptID,
                presenterReady: ready, presenterBusy: hasPresentedController())
            handle(decision)
        }

        func detach() {
            guard let attemptID = presentation.attemptID else { return }
            isDetaching = true
            if let picker, picker.presentingViewController != nil {
                dismissPicker(picker, attemptID: attemptID)
            } else {
                status?.cancelInstallPicker(attemptID: attemptID)
                _ = presentation.fail(attemptID: attemptID)
            }
            if presentation.phase == .idle {
                anchor?.onDidAppear = nil
                anchor = nil
                picker = nil
            }
        }

        private func anchorDidAppear() {
            if presentation.phase == .dismissing || presentation.phase == .awaitingDismissal,
               let attemptID = presentation.attemptID {
                completeDismissal(attemptID: attemptID)
                return
            }
            handle(presentation.presenterBecameReady(isBusy: hasPresentedController()))
        }

        private func handle(_ decision: V3InstallPickerPresentationCoordinator.Decision) {
            switch decision {
            case .present(let attemptID): presentPicker(attemptID: attemptID)
            case .rejected(let attemptID, let reason):
                _ = presentation.fail(attemptID: attemptID)
                status?.installPickerPresentationFailed(attemptID: attemptID, reason: reason)
            case .dismissed(let attemptID): finishDismissal(attemptID: attemptID)
            case .queued, .none: break
            }
        }

        private func presentPicker(attemptID: UUID) {
            guard let anchor, anchor.viewIfLoaded?.window != nil,
                  !hasPresentedController() else {
                _ = presentation.fail(attemptID: attemptID)
                status?.installPickerPresentationFailed(attemptID: attemptID,
                    reason: self.anchor == nil ? "presenter_unavailable" :
                        (self.anchor?.viewIfLoaded?.window == nil ? "presenter_not_in_window" : "presentation_active"))
                return
            }
            let documentPicker = UIDocumentPickerViewController(forOpeningContentTypes: [.data], asCopy: true)
            documentPicker.allowsMultipleSelection = false
            documentPicker.modalPresentationStyle = .formSheet
            documentPicker.delegate = self
            documentPicker.presentationController?.delegate = self
            picker = documentPicker
            selectionAccepted = false
            status?.installPickerPresentationRequested(attemptID: attemptID)
            anchor.present(documentPicker, animated: true) { [weak self, weak documentPicker] in
                guard let self, let documentPicker else { return }
                guard self.presentation.didPresent(attemptID: attemptID) else {
                    guard self.presentation.attemptID == attemptID else { return }
                    if self.presentation.phase == .dismissing || self.presentation.phase == .awaitingDismissal {
                        documentPicker.presentationController?.delegate = self
                        self.completeDismissal(attemptID: attemptID)
                        return
                    }
                    self.presentation.fail(attemptID: attemptID)
                    self.status?.installPickerPresentationFailed(
                        attemptID: attemptID, reason: "presentation_interrupted")
                    return
                }
                guard self.anchor?.presentedViewController === documentPicker else {
                    self.presentation.fail(attemptID: attemptID)
                    self.status?.installPickerPresentationFailed(
                        attemptID: attemptID, reason: "presentation_interrupted")
                    return
                }
                documentPicker.presentationController?.delegate = self
                self.status?.installPickerDidPresent(attemptID: attemptID)
            }
        }

        func documentPicker(_ controller: UIDocumentPickerViewController,
                            didPickDocumentsAt urls: [URL]) {
            guard let attemptID = presentation.attemptID,
                  presentation.phase == .presented,
                  let url = urls.first else { return }
            selectionAccepted = status?.stagePickerIPA(url, attemptID: attemptID) == true
            dismissPicker(controller, attemptID: attemptID)
        }

        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
            guard let attemptID = presentation.attemptID else { return }
            selectionAccepted = false
            dismissPicker(controller, attemptID: attemptID)
        }

        func presentationControllerDidDismiss(_ presentationController: UIPresentationController) {
            guard let attemptID = presentation.attemptID else { return }
            if presentation.phase == .presented || presentation.phase == .presenting {
                _ = presentation.beginDismissal(attemptID: attemptID)
            }
            completeDismissal(attemptID: attemptID)
        }

        private func dismissPicker(_ controller: UIDocumentPickerViewController, attemptID: UUID) {
            guard presentation.beginDismissal(attemptID: attemptID) else { return }
            if controller.isBeingDismissed {
                controller.transitionCoordinator?.animate(alongsideTransition: nil) { [weak self] _ in
                    self?.completeDismissal(attemptID: attemptID)
                }
                completeDismissal(attemptID: attemptID)
            } else if controller.presentingViewController == nil {
                completeDismissal(attemptID: attemptID)
            } else {
                controller.dismiss(animated: true) { [weak self] in
                    self?.completeDismissal(attemptID: attemptID)
                }
            }
        }

        private func completeDismissal(attemptID: UUID) {
            guard presentation.attemptID == attemptID else { return }
            let dismissed = presentation.didDismiss(attemptID: attemptID,
                presenterIsClear: !hasPresentedController())
            guard dismissed else {
                NSLog("[V3_INSTALL_UI] picker_dismiss_wait attempt=%@", attemptID.uuidString)
                return
            }
            finishDismissal(attemptID: attemptID)
        }

        private func finishDismissal(attemptID: UUID) {
            let selected = selectionAccepted
            picker = nil
            selectionAccepted = false
            NSLog("[V3_INSTALL_UI] picker_dismissed attempt=%@ selected=%d",
                  attemptID.uuidString, selected ? 1 : 0)
            if isDetaching {
                status?.cancelInstallPicker(attemptID: attemptID)
            } else if selected {
                status?.installPickerDidDisappear(attemptID: attemptID)
            } else {
                status?.cancelInstallPicker(attemptID: attemptID)
            }
        }

        private func hasPresentedController() -> Bool {
            var current: UIViewController? = anchor
            while let controller = current {
                if controller.presentedViewController != nil { return true }
                current = controller.parent
            }
            return false
        }
    }
}

struct V3RefreshAllButton: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    // V3_RUNTIME_SHARED_REFRESH_STORE_V1: the active run and health state are
    // written by the embedded service, so these bind to the one runtime App
    // Group store the host published, not a fixed suite name.
    @AppStorage("liveContainerAutoRefreshActiveRunID", store: V3SharedRefreshStore.defaults) private var activeRun = ""
    @AppStorage("liveContainerAutoRefreshHealthState", store: V3SharedRefreshStore.defaults) private var health = "UNKNOWN"
    @State private var attempt = V3RefreshAllAttemptState()
    @State private var message = ""
    @State private var diagnostics = ""
    @State private var terminalFailure: V3OperationFailureDetails?
    @State private var copied = false
    @State private var monitor: Task<Void, Never>?
    private let defaults = V3SharedRefreshStore.defaults

    private var phase: String { attempt.phase.rawValue }
    private var requestID: String { attempt.requestID }
    private var runID: String { attempt.runID }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(action: start) {
                HStack(spacing: 8) {
                    if ["starting", "refreshing", "verifying"].contains(phase) { ProgressView() }
                    V3LocalizedText(buttonTitle)
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .disabled(isBusy || isTerminal || !activeRun.isEmpty || status.presentation != nil || status.loading)
            .accessibilityValue(health.replacingOccurrences(of: "_", with: " ").lowercased())
            if V3RefreshAllButtonPresentationPolicy.explainsConcurrentRun(
                phase: attempt.phase, activeRunID: activeRun) {
                Text("Another refresh is already running. Refresh All will be available when it finishes.")
                    .font(.footnote)
                    .foregroundColor(.secondary)
            }
            if phase == "completed" || phase == "failed" {
                VStack(alignment: .leading, spacing: 6) {
                    Text("What happened").font(.caption.weight(.semibold))
                    V3LocalizedText(phase == "failed" ? V3DiagnosticPresentation.label(message, context: .refresh) : message)
                        .font(.footnote)
                        .foregroundColor(phase == "completed" ? .green : .red)
                        .textSelection(.enabled)
                    if phase == "failed", let terminalFailure {
                        Text("What you can do").font(.caption.weight(.semibold)).padding(.top, 4)
                        V3LocalizedText(terminalFailure.recommendedAction).font(.footnote)
                        HStack {
                            if let destination = terminalFailure.recoveryDestination,
                               let action = terminalFailure.recoveryActionTitle {
                                Button(LocalizedStringKey(action)) { openFailureRecovery(destination) }
                            }
                            if [.allowed, .unknown].contains(terminalFailure.retryDisposition) {
                                Button(LocalizedStringKey(terminalFailure.retryDisposition == .unknown
                                    ? "Retry (retryability unknown)" : "Retry")) {
                                        retryFailedAttempt()
                                    }
                                    .disabled(!activeRun.isEmpty || status.presentation != nil || status.loading)
                            }
                        }
                    } else if phase == "failed", message == "Refresh did not start." + "\nError ID: SS-CMD-D047" {
                        Button("Start Again") { acknowledge(); start() }
                            .disabled(!activeRun.isEmpty || status.presentation != nil || status.loading)
                    }
                }
                HStack {
                    Button(LocalizedStringKey(copied ? "Copied" : "Copy Diagnostics")) {
                        UIPasteboard.general.string = V3DiagnosticCopy.details(visibleMessage: message, technical: diagnostics)
                        copied = true
                    }
                    .font(.caption)
                    Button("Dismiss") { acknowledge() }
                        .font(.caption)
                    Spacer(minLength: 0)
                }
            }
        }
        .onChange(of: health) { _ in
            status.reload(manual: false)
            if phase == "refreshing" || phase == "verifying" { inspectSchedulerState() }
        }
        .onChange(of: activeRun) { _ in
            if phase == "starting" || phase == "refreshing" || phase == "verifying" {
                inspectSchedulerState()
            }
            if activeRun.isEmpty { status.reload(manual: false) }
        }
        .accessibilityHint("Starts one manual refresh and shows scheduler state through verified completion or failure.")
    }
    private var isBusy: Bool { ["starting", "refreshing", "verifying"].contains(phase) }
    private var isTerminal: Bool { ["completed", "failed"].contains(phase) }
    private var buttonTitle: String {
        V3RefreshAllButtonPresentationPolicy.title(phase: attempt.phase, activeRunID: activeRun)
    }

    private func start() {
        guard attempt.phase == .idle, !isBusy, !isTerminal, activeRun.isEmpty,
              status.presentation == nil, !status.loading else { return }
        let newRequestID = UUID().uuidString
        attempt.begin(requestID: newRequestID)
        message = "Starting Refresh..."
        diagnostics = "manual_refresh_request=\(newRequestID)\nstate=starting"
        // V3_REFRESH_PREREQUISITE_POLICY_V1: shared policy, evaluated before the
        // mutation request is posted. A known-missing pairing file blocks here
        // instead of surfacing later as an unexplained refresh failure.
        if let failure = V3RefreshPrerequisite.evaluate(statusConnected: status.connected,
                pairingStatus: status.pairing)
            .failure(correlationID: newRequestID) {
            attempt.failBeforeStart(message: failure.safeMessage)
            terminalFailure = V3OperationFailureDetails(failure)
            message = failure.safeMessage
            diagnostics = "schema=1\nrequest_id=\(newRequestID)\nrun_id=not_started\nstate=failed\n\(failure.technicalDetails)\nsafe_message=\(failure.safeMessage)"
            return
        }
        print("[V3_HOME_REFRESH] REQUEST request_id=\(newRequestID) origin=home health=\(health) active_run_id=\(activeRun.isEmpty ? "none" : activeRun)")
        NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil,
                                        userInfo: ["requestID": newRequestID, "origin": "home"])
        monitor = Task { @MainActor in await monitorRun(requestID: newRequestID) }
    }

    private func monitorRun(requestID expectedRequest: String) async {
        let startDeadline = Date().addingTimeInterval(20)
        while !Task.isCancelled && Date() < startDeadline {
            if let record = runRecord(requestID: expectedRequest) {
                _ = attempt.observe(record, schedulerHealth: health, activeRunID: activeRun)
                renderAttempt(record)
                if attempt.isTerminal { return }
                if !attempt.runID.isEmpty { break }
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
        guard !Task.isCancelled else { return }
        if attempt.runID.isEmpty {
            attempt.markDidNotStart()
            renderFailure(health: health)
            return
        }

        let finishDeadline = Date().addingTimeInterval(600)
        while !Task.isCancelled && Date() < finishDeadline {
            inspectSchedulerState()
            if attempt.isTerminal { return }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        if !Task.isCancelled && !attempt.isTerminal {
            attempt.markTimedOut()
            renderFailure(health: health)
        }
    }

    private func inspectSchedulerState() {
        guard !attempt.isTerminal, !runID.isEmpty,
              let record = runRecord(requestID: requestID, runID: runID) else { return }
        _ = attempt.observe(record, schedulerHealth: health, activeRunID: activeRun)
        renderAttempt(record)
    }

    private func runRecord(requestID: String, runID: String? = nil) -> [String: Any]? {
        guard let ledger = defaults.dictionary(forKey: "liveContainerAutoRefreshRunLedger") else { return nil }
        return V3RefreshAllAttemptState.record(in: ledger, requestID: requestID, runID: runID)
    }

    private func renderAttempt(_ record: [String: Any]) {
        switch attempt.phase {
        case .starting:
            message = "Starting Refresh..."
        case .refreshing:
            message = "Refreshing..."
        case .verifying:
            message = "Verifying..."
        case .completed:
            let manifest = record["manifest"] as? [String: Any]
                ?? record["manifest_summary"] as? [String: Any] ?? [:]
            let verifiedCount = (manifest["results"] as? [[String: Any]])?.count ??
                V3RefreshAllTerminalEvidencePolicy.count("result_count", in: manifest) ?? 0
            let skippedCount = (manifest["skipped_ids"] as? [String])?.count ??
                V3RefreshAllTerminalEvidencePolicy.count("skipped_count", in: manifest) ?? 0
            message = attempt.terminalMessage
            terminalFailure = nil
            diagnostics = "manual_refresh_request=\(requestID)\nrun_id=\(runID)\nstate=completed\nverified_app_count=\(verifiedCount)\nskipped_app_count=\(skippedCount)"
        case .failed:
            renderFailure(health: record["health"] as? String ?? health, record: record)
        case .idle:
            break
        }
    }

    private func renderFailure(health: String, record: [String: Any]? = nil) {
        guard !isTerminal || phase == "failed" else { return }
        message = attempt.terminalMessage.isEmpty ? "Refresh failed. Check Refresh History for details." : attempt.terminalMessage
        terminalFailure = record.flatMap { value in
            guard let wire = value["failure"] as? [String: Any],
                  let failure = CombinedFailure.decode(wire, expectedID: runID),
                  failure.operation == "refresh" else { return nil }
            return V3OperationFailureDetails(failure)
        }
        if let record, !runID.isEmpty,
           let currentRunDiagnostics = V3RefreshAllFailureDiagnostics.text(
               requestID: requestID, runID: runID, record: record) {
            diagnostics = currentRunDiagnostics
        } else {
            diagnostics = V3RefreshAllFailureDiagnostics.withoutRunRecord(
                requestID: requestID, runID: runID.isEmpty ? nil : runID,
                message: message, health: health) ?? "schema=1\noperation=refresh\nstage=unknown\ncode=unknown"
        }
    }

    private func acknowledge() {
        monitor?.cancel()
        monitor = nil
        attempt.acknowledge()
        message = ""
        diagnostics = ""
        terminalFailure = nil
        copied = false
    }

    private func retryFailedAttempt() {
        guard phase == "failed", let terminalFailure,
              [.allowed, .unknown].contains(terminalFailure.retryDisposition),
              activeRun.isEmpty, status.presentation == nil, !status.loading else { return }
        acknowledge()
        start()
    }

    private func openFailureRecovery(_ destination: String) {
        switch destination {
        case "signIn": status.signInPresented = true
        case "certificates": status.certificatesPresented = true
        case "ipa": status.beginInstallPicker()
        case "setup": status.setupPresented = true
        case "connection": status.connectionPresented = true
        case "pairing": status.pairingPresented = true
        default: break
        }
    }
}

struct V3InstallButton: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    var body: some View {
        Button("Install with SideStore", systemImage: "arrow.down.app") {
            status.beginInstallPicker()
        }
        .accessibilityLabel("Install / Sideload App with SideStore")
        .accessibilityHint("Choose an IPA to sign and install as an iOS app.")
    }
}

struct V3OperationRequest: Identifiable {
    let id: UUID
    let operation: String
    let target: String
    let title: String
    let installAttemptID: UUID?
    let recoverySessionID: String?

    init(id: UUID = UUID(), operation: String, target: String, title: String,
         installAttemptID: UUID? = nil, recoverySessionID: String? = nil) {
        self.id = id
        self.operation = operation
        self.target = target
        self.title = title
        self.installAttemptID = installAttemptID
        self.recoverySessionID = recoverySessionID
    }
}

struct V3PromptAnswer {
    var fields: [String: String] = [:]
    var choice = ""
    var selected: Set<String> = []
}

@MainActor
final class V3SideStoreStatusStore: ObservableObject {
    init() {
        Task.detached(priority: .utility) {
            // The host owns the App Group selection, so it injects it here rather
            // than relying on the launch-time publication having happened first.
            V3SecretHandoff.cleanupExpiredItems(selectedGroup: LCSharedUtils.appGroupID())
            V3SharedFileRecord.removeLegacyDefaultsRecords(LCUtils.appGroupUserDefault)
        }
        if let containerRoot = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) {
            Task.detached(priority: .utility) {
                _ = V3SharedFileRecord.sweep(containerRoot: containerRoot)
            }
        }
    }

    @Published private(set) var account = "Not available"
    @Published private(set) var signing = "Unknown"
    @Published private(set) var team = "Unknown"
    @Published private(set) var certificate = "Unknown"
    @Published private(set) var certificateExpiration: Date?
    @Published private(set) var pairing = "Unknown"
    // V3_AUTH_SESSION_SNAPSHOT_V1: the service reports the authenticated Apple
    // session separately from the active account row, because authentication
    // completes before provisioning activates that row.
    @Published private(set) var authenticated = false
    @Published private(set) var pendingCertificateRecoveryState = V3PendingCertificateRecoveryState.unavailable
    @Published private(set) var identityStamp: String?
    @Published private(set) var hostSigningContext: String?
    @Published private(set) var installedHostSigning = V3HostSigningObservation()

    var installedHostSigningState: V3HostSigningState {
        connected ? installedHostSigning.currentState(context: hostSigningContext) : .unknown
    }

    func recordInstalledHostSigning(_ reply: [String: Any], revision: UInt64) {
        guard isSetupFactRevisionCurrent(revision) else { return }
        guard let currentStamp = identityStamp, !currentStamp.isEmpty,
              reply["identityStamp"] as? String == currentStamp,
              let observation = V3HostSigningObservation.decode(reply["hostSigning"]),
              observation.context == hostSigningContext else {
            installedHostSigning = V3HostSigningObservation()
            return
        }
        installedHostSigning = observation
    }

    @Published private(set) var authenticationActive = false
    // V3_AUTH_LOCAL_STATE_SNAPSHOT_V1: persisted account/team/certificate
    // presence is separate from credential readability and display strings.
    @Published private(set) var activeAccountPresent = false
    @Published private(set) var activeTeamPresent = false
    @Published private(set) var activeCertificatePresent = false
    @Published private(set) var provisioningIncomplete = false
    // V3_SETUP_COMPLETION_POLICY_V1: the last authoritative Wi-Fi observation.
    // Probing Wi-Fi is async, so Home reads this cache instead of guessing.
    // nil means "not observed yet", which counts as outstanding. It is written
    // only through recordWifiAvailability so the cached fact has one owner.
    @Published private(set) var wifiAvailable: Bool?

    /// Records the authoritative Wi-Fi observation for the shared setup policy.
    private var setupFactRevision: UInt64 = 0

    func beginSetupFactObservation() -> UInt64 {
        setupFactRevision &+= 1
        return setupFactRevision
    }

    var currentSetupFactRevision: UInt64 { setupFactRevision }

    func isSetupFactRevisionCurrent(_ revision: UInt64) -> Bool {
        V3SetupReadinessObservationPolicy.mayApplyFreshObservation(
            sourceFactRevision: revision, currentFactRevision: setupFactRevision)
    }

    func recordWifiAvailability(_ available: Bool, revision: UInt64? = nil) {
        if let revision, !isSetupFactRevisionCurrent(revision) { return }
        wifiAvailable = available
    }

    func invalidateSetupFacts() {
        installedHostSigning = V3HostSigningObservation()
        setupFactRevision &+= 1
        setupFactObservation = .pending
        setupFactLastAttemptAt = nil
        wifiAvailable = nil
        jitlessReadinessObservation = nil
        jitlessReadiness = nil
        jitlessActiveCertificateAvailable = nil
    }

    func markSetupFactsObserved(revision: UInt64? = nil) {
        if let revision, !isSetupFactRevisionCurrent(revision) { return }
        setupFactObservation = .observed
        setupFactLastAttemptAt = Date()
    }

    // V3_SHARED_JITLESS_FACT_V1: the last authoritative JIT-Less readiness.
    // Health, the Setup Assistant and the store's own setup-fact observation all
    // publish it, and Home reads it, so no surface can claim a different
    // completion answer. nil means "not observed yet" and counts as
    // outstanding, so a fact nothing observes can never read as satisfied.
    @Published private(set) var jitlessReadinessObservation: V3SetupReadinessObservation?
    // Compatibility projections for existing UI consumers. The observation is
    // authoritative; all three values are updated together in this owner.
    @Published private(set) var jitlessReadiness: V3JITLessReadiness?
    @Published private(set) var jitlessActiveCertificateAvailable: Bool?

    /// Publishes an observed JIT-Less readiness for every setup surface to share.
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

    // V3_SETUP_FACT_OBSERVATION_V1
    // The two facts above were only ever observed by the Setup Assistant and by
    // Health. A user who opened neither left them nil, and nil is outstanding by
    // policy, so on a platform where JIT-Less is required the Home banner could
    // never clear no matter how correct the underlying state was. The store
    // observes them after an authoritative snapshot and refreshes them on a
    // bounded cadence or explicit lifecycle/certificate invalidation.
    //
    // The attempt is tri-state rather than a boolean so a failure cannot become
    // an unbounded retry loop against a service that is not answering, and so a
    // deliberate reload can still ask again.
    private enum SetupFactObservation: Equatable {
        /// Not attempted yet.
        case pending
        /// Both facts have been observed for the current cache interval.
        case observed
        /// Attempted and the service did not answer. Retried only after the cache interval.
        case deferred
    }
    private var setupFactObservation: SetupFactObservation = .pending
    private var setupFactLastAttemptAt: Date?
    private var authReadinessRefreshEventLedger = V3AuthReadinessRefreshEventLedger()
    private var setupFactObservationGeneration: UInt64 = 0
    private var setupFactObservationTask: Task<Void, Never>?
    private var authReadinessEventGeneration: UInt64 = 0
    private var authReadinessObservationTask: Task<Void, Never>?

    /// Observes the shared setup facts once, when they are the only thing
    /// standing between the user and a cleared setup banner.
    private func observeSetupFactsIfNeeded() {
        guard V3SetupFactObservationPolicy.shouldObserve(
            connected: connected, setupPresented: setupPresented,
            operationPresented: presentation != nil, loading: loadActivity != .idle,
            returnToSetupPending: returnToSetupAfterJITLess,
            lastAttemptAt: setupFactLastAttemptAt) else { return }
        // Legacy devices still observe installed host signing, through the
        // local-only endpoint branch. They never acquire an OCSP prerequisite.
        setupFactObservation = .deferred
        setupFactLastAttemptAt = Date()
        startSetupFactObservation()
    }

    /// The app-owned root view consumes auth events so sign-in presentation
    /// lifetime cannot drop a committed certificate change. A session event is
    /// claimed once before it invalidates any in-flight observation.
    func refreshSetupFactsAfterAuthentication(sessionID: String, attemptSequence: UInt64) {
        guard UUID(uuidString: sessionID)?.uuidString == sessionID,
              authReadinessRefreshEventLedger.claim(attemptSequence: attemptSequence) else { return }
        invalidateSetupFacts()
        setupFactObservation = .deferred
        setupFactLastAttemptAt = Date()
        authReadinessEventGeneration &+= 1
        startSetupFactObservation()
        authReadinessObservationTask = setupFactObservationTask
    }

    private func startSetupFactObservation() {
        setupFactObservationGeneration &+= 1
        setupFactObservationTask = Task { await observeSetupFacts() }
    }

    /// Quick Setup reuses the latest shared observation when it is already
    /// running or completed, instead of issuing a second health snapshot.
    func awaitAuthReadinessRefresh() async -> V3SetupReadinessObservation? {
        while let task = authReadinessObservationTask {
            let generation = authReadinessEventGeneration
            await task.value
            if generation == authReadinessEventGeneration {
                return jitlessReadinessObservation
            }
        }
        return nil
    }

    func awaitSharedSetupJITLessReadiness() async -> V3SetupReadinessObservation? {
        if let authReadiness = await awaitAuthReadinessRefresh() {
            return authReadiness
        }
        while let task = setupFactObservationTask {
            let generation = setupFactObservationGeneration
            await task.value
            if generation == setupFactObservationGeneration {
                return jitlessReadinessObservation
            }
        }
        return jitlessReadinessObservation
    }

    private func observeSetupFacts() async {
        let revision = beginSetupFactObservation()
        // Wi-Fi is a host-side fact, so it is probed here rather than asked of
        // the service. An unavailable answer is recorded as unavailable, not as
        // unknown, because the probe is the authority for it.
        let wifi = await LiveContainerNetworkPreflight.wifiAvailable()
        guard isSetupFactRevisionCurrent(revision) else { return }
        recordWifiAvailability(wifi, revision: revision)
        let jitlessRequired = V3JITLessCompletionPolicy.isRequired(
            osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion)
        do {
            let health = try await V3ServiceBridge.shared.request(operation: "healthSnapshot",
                target: jitlessRequired ? "" : "hostSigningOnly")
            guard isSetupFactRevisionCurrent(revision) else { return }
            recordInstalledHostSigning(health, revision: revision)
            if !jitlessRequired {
                recordJITLessReadiness(.notRequired, revision: revision)
                markSetupFactsObserved(revision: revision)
                return
            }
            let certificate = health["certificateState"] as? [String: Any] ?? [:]
            let readiness = await V3JITLessStatusReader.read(serviceCertificate: certificate)
            guard isSetupFactRevisionCurrent(revision) else { return }
            recordJITLessReadiness(readiness.readiness,
                activeCertificateAvailable: V3ServiceBridge.strictBool(certificate["active"]),
                revision: revision)
            markSetupFactsObserved(revision: revision)
        } catch {
            guard isSetupFactRevisionCurrent(revision) else { return }
            // Unobserved is published as unknown, so the item stays outstanding
            // rather than the banner claiming a certificate exists.
            recordInstalledHostSigning([:], revision: revision)
            recordJITLessReadiness(.unknown, revision: revision)
        }
    }
    @Published private(set) var updatedAt: Date?
    @Published private(set) var installedApps: [V3SideStoreApp] = []
    @Published private(set) var sources: [V3SideStoreSource] = []
    @Published private(set) var settings: [String: Bool] = [:]
    @Published var error: String?
    @Published private(set) var unresolvedOperationRecovery: V3OperationRecoveryRecord?
    @Published private(set) var unresolvedRefreshRecoveryRunID: String?
    @Published private(set) var unresolvedRecoveryJournalUnreadable = false
    @Published private(set) var recoveryStorageKind: String?
    @Published private(set) var recoveryStorageClearEligible = false
    @Published private(set) var recoveryStorageDiagnostics = "schema=1 operation=status stage=persistence recovery_storage_kind=unknown"

    var canDiscardUnreadableRecovery: Bool {
        V3RecoveryStoragePresentationPolicy.mayOfferClear(
            connected: connected, unresolved: unresolvedRecoveryJournalUnreadable,
            kind: recoveryStorageKind, serverClearEligible: recoveryStorageClearEligible)
    }

    var recoveryStorageDiagnosticCode: String {
        let causes: [String: CombinedFailure.SafeCause] = [
            "malformedRecord": .recoveryMalformedRecord, "incompatibleRecord": .recoveryIncompatibleRecord,
            "storageUnavailable": .recoveryStorageUnavailable, "lockUnavailable": .recoveryLockUnavailable,
            "readFailure": .recoveryReadFailure, "deleteFailure": .recoveryDeleteFailure]
        return CombinedFailure(operation: "status", stage: .persistence,
            id: "00000000-0000-0000-0000-000000000000", safeCause: recoveryStorageKind.flatMap { causes[$0] }).diagnosticCode
    }
    var recoveryStorageTitle: String {
        recoveryStorageUnlabeledTitle + "\nError ID: " + recoveryStorageDiagnosticCode
    }
    private var recoveryStorageUnlabeledTitle: String {
        switch recoveryStorageKind {
        case "malformedRecord": return "SideStore paused changes because its recovery record is malformed"
        case "incompatibleRecord": return "SideStore paused changes because its recovery record uses an incompatible format"
        case "storageUnavailable": return "SideStore cannot access shared recovery storage"
        case "lockUnavailable": return "SideStore cannot acquire its recovery storage lock"
        case "readFailure": return "SideStore could not read recovery storage"
        case "deleteFailure": return "SideStore could not remove its recovery record"
        default: return "SideStore recovery status is uncertain"
        }
    }

    var recoveryStorageGuidance: String {
        if canDiscardUnreadableRecovery {
            return "Check the device before clearing this saved record. An earlier operation may still be running."
        }
        return "Changes remain paused. This is not proof of a corrupt record. Check shared storage and service status; copy Diagnostics."
    }
    @Published private(set) var unresolvedDirectRecovery: V3HostDirectRecoveryRecord?
    @Published private(set) var directRecoveryPostcondition: V3DirectRecoveryPostcondition?
    @Published private(set) var directRecoveryInspecting = false
    // V3_USER_FACING_ISSUE_V1: the structured issue behind the global alert.
    // The string form is retained for compatibility and copyable summaries, but
    // actions are chosen from the typed issue, never from the string.
    @Published private(set) var issue: V3UserFacingIssue?
    @Published var notice: String?

    /// Presents a failure with the action its typed evidence supports.
    func present(_ error: Error) {
        if let combined = error as? CombinedFailure {
            let structured = V3UserFacingIssue.make(combined)
            issue = structured
            self.error = structured.summary
        } else {
            // V3_FAILURE_GUIDANCE_V1: an untyped failure still gets guidance, and
            // still never claims a connection problem without evidence. The raw
            // description is not shown to the user, because for a bridged NSError
            // it is a numeric domain and code; it is kept in the diagnostics the
            // user can copy instead.
            let structured = V3UserFacingIssue.make(
                operation: "command", stage: CombinedFailure.Stage.command.rawValue,
                code: CombinedFailure.Code.failed.rawValue, safeCause: nil, sourceStep: nil,
                retryable: nil,
                whatHappened: "That action did not complete.\nError ID: SS-CMD-C11",
                whatToDo: V3FailureGuidance.message(error),
                technicalDetails: V3FailureGuidance.diagnostics(error))
            issue = structured
            self.error = structured.summary
        }
    }

    func reconcileDurableOperationAfterDeviceCheck() {
        guard let record = unresolvedOperationRecovery else { return }
        Task {
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "opRecoveryReconcile",
                    target: record.sessionID, payload: ["userConfirmed": true])
                guard reply["session"] as? String == record.sessionID,
                      V3ServiceBridge.strictBool(reply["reconciled"]) == true else {
                    throw CombinedFailure(operation: "opRecoveryReconcile", stage: .command,
                        code: .staleResult, id: record.sessionID, retryable: false)
                }
                V3ServiceBridge.shared.retireReconciledOperationService(sessionID: record.sessionID)
                unresolvedOperationRecovery = nil
                notice = "The recovery hold was cleared after your device check."
                reload()
            } catch {
                self.error = "SideStore could not clear the recovery hold. Reconnect and try again." + "\nError ID: SS-CMD-D002"
            }
        }
    }

    func resumeOperationRecovery() {
        guard let record = unresolvedOperationRecovery, record.phase == .dispatched,
              presentation == nil, !installAttempt.hasActiveAttempt else { return }
        let title = "Recover \(record.kind)"
        presentation = V3OperationRequest(operation: record.kind,
            target: record.stagedIPAToken ?? "", title: title,
            recoverySessionID: record.sessionID)
    }

    func reconcileLostRefreshAfterDeviceCheck() {
        guard let runID = unresolvedRefreshRecoveryRunID else { return }
        Task {
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "refreshAdmissionReconcile",
                    target: runID, payload: ["userConfirmed": true])
                guard reply["runID"] as? String == runID,
                      V3ServiceBridge.strictBool(reply["released"]) == true,
                      V3ServiceBridge.strictBool(reply["reconciled"]) == true else {
                    throw CombinedFailure(operation: "refresh", stage: .command,
                        code: .staleResult, id: runID, retryable: false)
                }
                V3ServiceBridge.shared.retireReconciledRefreshService(runID: runID)
                unresolvedRefreshRecoveryRunID = nil
                notice = "The refresh hold was cleared after your device check."
                reload()
            } catch {
                self.error = "SideStore could not clear the refresh hold. Reconnect and try again." + "\nError ID: SS-VERIFY-D003"
            }
        }
    }

    func inspectDirectRecovery() {
        guard !directRecoveryInspecting, let record = unresolvedDirectRecovery else { return }
        directRecoveryInspecting = true
        Task {
            defer { directRecoveryInspecting = false }
            do {
                let reply = try await V3ServiceBridge.shared.request(
                    operation: "directRecoveryInspect", target: record.requestID)
                guard let inspected = V3HostDirectRecoveryRecord(inspectionReply: reply),
                      inspected.requestID == record.requestID,
                      inspected.operation == record.operation,
                      let postcondition = inspected.postcondition,
                      unresolvedDirectRecovery?.requestID == record.requestID else {
                    throw CombinedFailure(operation: "directRecoveryInspect", stage: .command,
                        code: .staleResult, id: record.requestID, retryable: false)
                }
                unresolvedDirectRecovery = inspected
                directRecoveryPostcondition = postcondition
                if V3DirectRecoveryHostPolicy.mayAcknowledgeInspectedTerminal(
                    inspected, postcondition: postcondition) {
                    _ = await reconcileDirectRecovery(inspected, userConfirmed: false)
                }
            } catch {
                present(error)
                reload()
            }
        }
    }

    func reconcileDirectRecoveryAfterUserCheck() {
        guard let record = unresolvedDirectRecovery,
              V3DirectRecoveryHostPolicy.mayOfferUserConfirmation(
                record, postcondition: directRecoveryPostcondition) else { return }
        Task { _ = await reconcileDirectRecovery(record, userConfirmed: true) }
    }

    private func reconcileDirectRecovery(_ record: V3HostDirectRecoveryRecord,
                                         userConfirmed: Bool) async -> Bool {
        let payload: [String: Any] = record.phase == .terminal
            ? ["ackTerminal": true] : ["userConfirmed": userConfirmed]
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "directRecoveryReconcile",
                target: record.requestID, payload: payload)
            guard reply["requestID"] as? String == record.requestID,
                  V3ServiceBridge.strictBool(reply["reconciled"]) == true else {
                throw CombinedFailure(operation: "directRecoveryReconcile", stage: .command,
                    code: .staleResult, id: record.requestID, retryable: false)
            }
            if unresolvedDirectRecovery?.requestID == record.requestID {
                unresolvedDirectRecovery = nil
                directRecoveryPostcondition = nil
            }
            notice = "The previous request was reconciled."
            reload()
            return true
        } catch {
            present(error)
            return false
        }
    }

    func discardUnreadableRecoveryAfterDeviceCheck() {
        guard canDiscardUnreadableRecovery else { return }
        Task {
            do {
                let reply = try await V3ServiceBridge.shared.request(
                    operation: "recoveryDiscardUnreadable", payload: ["userConfirmed": true])
                guard V3ServiceBridge.strictBool(reply["discardedUnreadable"]) == true else {
                    throw CombinedFailure(operation: "status", stage: .persistence,
                        code: .staleResult, id: UUID().uuidString, retryable: false)
                }
                // The service reread under its lock, then this independent
                // authoritative snapshot must confirm the host hold is gone.
                let outcome = await reloadAndWait()
                guard V3RecoveryStoragePresentationPolicy.confirmsCleared(
                    snapshotApplied: outcome == .applied,
                    unreadable: unresolvedRecoveryJournalUnreadable,
                    operationRecovery: unresolvedOperationRecovery != nil,
                    directRecovery: unresolvedDirectRecovery != nil,
                    refreshRecovery: unresolvedRefreshRecoveryRunID != nil) else {
                    throw CombinedFailure(operation: "status", stage: .persistence,
                        code: .staleResult, id: UUID().uuidString, retryable: true,
                        safeCause: .recoveryReadFailure)
                }
                notice = "The recovery record is absent and SideStore status was verified."
                await cleanupOrphanedStagedIPAs()
            } catch {
                present(error)
            }
        }
    }

    func clearIssue() {
        issue = nil
        error = nil
    }

    /// Opens the destination an issue's primary action points at.
    func openIssueRecovery() {
        guard let destination = issue?.recoveryDestination else { return }
        switch destination {
        case "signIn": signInPresented = true
        case "certificates": certificatesPresented = true
        case "ipa": beginInstallPicker()
        case "setup": setupPresented = true
        case "connection": connectionPresented = true
        case "pairing": pairingPresented = true
        case "sources": sourcesPresented = true
        default: break
        }
    }

    /// Runs the primary action the typed evidence selected.
    ///
    /// The two retry actions are deliberately different. A connection failure is
    /// re-observed by reloading status. A source failure must re-request the
    /// sources themselves, because reloading status does not re-fetch a manifest
    /// and would leave the user looking at the same empty or stale catalog while
    /// the button claims it retried.
    func performPrimaryIssueAction() -> Bool {
        guard let action = issue?.primaryAction else { return true }
        switch action {
        case .retrySource:
            return V3IssueActionOutcomePolicy.shouldDismiss(
                action: action, didStart: refreshSources())
        case .reloadSources:
            reload()
            return V3IssueActionOutcomePolicy.shouldDismiss(action: action, didStart: true)
        case .reloadStatus:
            reload()
            return V3IssueActionOutcomePolicy.shouldDismiss(action: action, didStart: true)
        default:
            openIssueRecovery()
            return V3IssueActionOutcomePolicy.shouldDismiss(action: action, didStart: true)
        }
    }

    private func presentUnconfirmedSignOut(_ outcome: V3SignOutOutcome) {
        guard let whatHappened = V3SignOutOutcomePolicy.whatHappened(for: outcome),
              let whatToDo = V3SignOutOutcomePolicy.whatToDo(for: outcome) else { return }
        let issue = V3UserFacingIssue(
            title: "Sign Out",
            severity: .failed,
            whatHappened: whatHappened,
            whatToDo: whatToDo,
            technicalDetails: "schema=1\noperation=signOut\nresult=notConfirmed\noutcome=\(outcome)",
            primaryAction: .reloadStatus,
            secondaryAction: .dismiss,
            recoveryDestination: nil,
            retryDisposition: .unknown)
        self.issue = issue
        error = issue.summary
        notice = nil
    }

    @Published var presentation: V3OperationRequest? {
        didSet {
            // A presented operation owns the state a snapshot would report, so a
            // deferred snapshot is owed until it ends. Draining here is what
            // guarantees a parked continuation is resumed rather than stranded.
            if presentation == nil { drainOwedSnapshot() }
        }
    }
    @Published var sourceURL = ""
    @Published private(set) var sourceFormOpenRequestID: UUID?
    @Published var refreshTarget: String?
    @Published var refreshPresented = false
    @Published var signInPresented = false
    @Published var setupPresented = false
    @Published var pendingCanonicalJITLessSetup = false
    @Published var returnToSetupAfterJITLess = false
    @Published var connectionPresented = false
    @Published var certificatesPresented = false
    @Published var pairingPresented = false
    // V3_USER_FACING_ISSUE_V1: a source failure routes back to Sources.
    @Published var sourcesPresented = false
    @Published var operationRecoveryDestination: String?
    // V3_LOAD_ACTIVITY_OWNERSHIP_V1: `loading` keeps its user-facing meaning of
    // "the service is busy", but it is now derived from a named activity so the
    // snapshot gate can tell a snapshot from a mutation. Five of the six
    // activities that used to set this flag were mutations.
    @Published private(set) var loading = false
    @Published private(set) var connected = false
    @Published private(set) var requiresConnectionRetry = false
    // The activity that currently owns the service. Only `.snapshot` may resolve
    // a snapshot waiter.
    private var loadActivity: V3LoadActivity = .idle
    // At most one snapshot is owed, because at most one can be pending. Starting
    // any snapshot discharges it, so an unrelated reload can never leave a stale
    // intent behind to cause a second fetch.
    private var snapshotOwedIntent = V3SnapshotOwedIntent()
    // Monotonic identity for each authoritative snapshot started by this store.
    // Awaiters parked behind a mutation or presentation require a later epoch,
    // even if an older in-flight snapshot finishes after the blocker.
    private var snapshotGeneration: UInt64 = 0
    private var externalMutationCount = 0
    private var directMutationOwners: Set<UUID> = []
    private var coordinatedMutationActive = false
    // Callers awaiting an authoritative snapshot. The registry tracks each
    // caller's minimum generation and manual requirement, because a shared
    // drain must not satisfy a deferred caller with an older in-flight fetch.
    private struct SnapshotWaiter {
        let continuation: CheckedContinuation<V3ReloadOutcome, Never>
    }
    private var snapshotWaiters: [UUID: SnapshotWaiter] = [:]
    private var snapshotWaiterRegistry = V3SnapshotWaiterRegistry()
    private var pendingPickerError: (attemptID: UUID, message: String)?
    private var ipaStagingTask: (attemptID: UUID, task: Task<Void, Never>)?
    private var dismissedIPAStagingAttemptID: UUID?
    @Published private(set) var installAttempt = V3InstallAttemptState()
    var installedAppCount: Int { installedApps.count }
    var hasUncertainInstallCancellation: Bool {
        installAttempt.backendSessionID != nil &&
            (installAttempt.phase == .operationStarted || installAttempt.phase == .operationPresented)
    }
    var isStale: Bool { !connected || (updatedAt.map { Date().timeIntervalSince($0) > 120 } ?? true) }
    var needsSignIn: Bool { V3AuthSnapshotAuthorityPolicy.needsSignIn(authenticated: authenticated) }
    func requestSourceForm(prefilledURL: String?) {
        if let prefilledURL { sourceURL = prefilledURL }
        sourceFormOpenRequestID = UUID()
    }
    // V3_AWAITABLE_RELOAD_V1 / V3_LOAD_ACTIVITY_OWNERSHIP_V1
    // reload() is fire-and-forget: it starts the snapshot and continues
    // immediately, so any code that reads status right after it sees the
    // PREVIOUS snapshot. reloadAndWait() completes only after an authoritative
    // snapshot has been applied, which is what callers that depend on ordering
    // must use. No delay or sleep is involved: the caller awaits the real
    // snapshot.
    func reload(manual: Bool = true) {
        switch beginSnapshot(manual: manual) {
        case .performSnapshot:
            Task { _ = await performSnapshot() }
        case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
            // The request is remembered and satisfied by the drain once the
            // blocking activity ends. No continuation is parked, because this
            // caller does not wait.
            break
        case .doNotObserve:
            break
        }
    }

    /// The result of awaiting an authoritative snapshot.
    ///
    /// A Bool could not distinguish "the snapshot ran and failed" from "no
    /// snapshot ran at all", so a caller that recomputes derived state could be
    /// told it had fresh state when it had been handed the previous snapshot
    /// unchanged.
    enum V3ReloadOutcome: Equatable {
        /// A snapshot was performed and accepted.
        case applied
        /// A snapshot was performed and failed.
        case snapshotFailed
        /// No snapshot was performed. A caller that recomputes derived state
        /// must treat this as "unknown", never as "up to date".
        case notObserved

        var setupSnapshotOutcome: V3SetupSnapshotOutcome {
            switch self {
            case .applied: return .applied
            case .snapshotFailed: return .snapshotFailed
            case .notObserved: return .notObserved
            }
        }
    }

    /// Performs one authoritative snapshot and returns only once the resulting
    /// state has been applied.
    ///
    /// If a snapshot is already in flight this joins that one. If a mutation is
    /// in flight it waits for a snapshot performed after that mutation, because
    /// a mutation's completion says nothing about authoritative status. If a
    /// presented operation owns the state it waits for the deferred snapshot. In
    /// every parked case only a snapshot completion resumes the caller.
    @discardableResult
    func reloadAndWait(manual: Bool = true) async -> V3ReloadOutcome {
        guard !Task.isCancelled else { return .notObserved }
        let decision = beginSnapshot(manual: manual, waiterWillBeInstalled: true)
        switch decision {
        case .performSnapshot, .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
            let requiredGeneration = V3SnapshotWaiterEpochPolicy.requiredGeneration(
                for: decision, currentGeneration: snapshotGeneration)
            // The service request belongs to the store, not to the first caller.
            // Cancellation removes only this caller's continuation and cannot
            // cancel the shared request for other waiters.
            let waiterID = UUID()
            return await withTaskCancellationHandler {
                await withCheckedContinuation { continuation in
                    if case .performSnapshot = decision {
                        Task { _ = await performSnapshot() }
                    }
                    guard !Task.isCancelled else {
                        continuation.resume(returning: .notObserved)
                        return
                    }
                    snapshotWaiters[waiterID] = SnapshotWaiter(continuation: continuation)
                    snapshotWaiterRegistry.insert(waiterID, manual: manual,
                        requiredSnapshotGeneration: requiredGeneration)
                }
            } onCancel: {
                Task { @MainActor [weak self] in
                    self?.cancelSnapshotWaiter(waiterID)
                }
            }
        case .doNotObserve:
            // Nothing ran and nothing is owed, so no continuation is parked.
            return .notObserved
        }
    }

    /// Resolves a canceled caller immediately while leaving a shared snapshot
    /// alive for its other waiters and the store's authoritative cache.
    private func cancelSnapshotWaiter(_ id: UUID) {
        guard snapshotWaiterRegistry.remove(id),
              let waiter = snapshotWaiters.removeValue(forKey: id) else { return }
        waiter.continuation.resume(returning: .notObserved)
    }

    /// The shared synchronous gate. It names the activity instead of inferring
    /// one from a shared busy flag, and it is the only place a snapshot is
    /// started or a waiter is parked.
    private func beginSnapshot(manual: Bool,
                               waiterWillBeInstalled: Bool = false) -> V3SnapshotDecision {
        let decision = V3SnapshotGate.decide(
            activity: loadActivity, presentationActive: presentation != nil,
            manual: manual, requiresConnectionRetry: requiresConnectionRetry)
        switch decision {
        case .performSnapshot:
            startSnapshot(manual: manual)
        case .joinSnapshot:
            break
        case .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
            // A snapshot is owed. It is owed once, not once per requester, so a
            // burst of requests cannot queue a burst of fetches.
            // Awaiting callers carry manual intent in the waiter registry so a
            // canceled last waiter cannot force a later retry. Fire-and-forget
            // reloads have no waiter and retain their manual intent here.
            snapshotOwedIntent.record(manual: manual && !waiterWillBeInstalled)
        case .doNotObserve:
            break
        }
        return decision
    }

    /// Claims the service for a snapshot. Starting any snapshot discharges the
    /// owed intent, which is what stops an unrelated reload from leaving a stale
    /// request behind to cause a second fetch.
    private func startSnapshot(manual: Bool) {
        snapshotOwedIntent.clear()
        snapshotGeneration &+= 1
        if manual {
            requiresConnectionRetry = false
            // A deliberate reload is also a deliberate request to try the
            // shared setup facts again, so a previous service failure is not
            // permanent.
            if setupFactObservation == .deferred {
                setupFactObservation = .pending
                setupFactLastAttemptAt = nil
            }
        }
        loadActivity = .snapshot
        loading = true
        if installAttempt.hasActiveAttempt {
            NSLog("[V3_INSTALL_STATE] attempt=%@ event=snapshot_started phase=%@",
                  installAttempt.attemptID?.uuidString ?? "none", installAttempt.phase.rawValue)
        }
    }

    /// Claims the service for a mutation. A mutation never resolves a snapshot
    /// waiter; it only makes an owed snapshot due.
    private func beginMutation() {
        coordinatedMutationActive = true
        loadActivity = .mutation
        loading = true
    }

    /// Begins a view-owned mutation outside runMutation before its first await.
    func beginDirectMutation() -> UUID {
        let owner = UUID()
        directMutationOwners.insert(owner)
        externalMutationCount += 1
        if loadActivity == .snapshot {
            snapshotOwedIntent.record(manual: true)
        } else if loadActivity == .idle {
            loadActivity = .mutation
            loading = true
        }
        return owner
    }

    func finishDirectMutation(ticket: UUID, reply: [String: Any]? = nil,
                              requestReload: Bool = false) {
        guard directMutationOwners.remove(ticket) != nil else { return }
        externalMutationCount = directMutationOwners.count
        if let reply { _ = accept(reply) }
        if requestReload { snapshotOwedIntent.record(manual: true) }
        if externalMutationCount == 0 && !coordinatedMutationActive && loadActivity == .mutation {
            loadActivity = .idle
            loading = false
            drainOwedSnapshot()
        }
    }

    /// A bridge write intent may invalidate a snapshot already in flight or
    /// originate outside a Store-owned mutation wrapper.
    func statusAuthorityInvalidated() {
        invalidateSetupFacts()
        if loadActivity == .idle {
            reload(manual: false)
        } else {
            snapshotOwedIntent.record(manual: true)
        }
    }

    private func performSnapshot() async -> V3ReloadOutcome {
        // Only one snapshot may own the service activity at a time, so this
        // generation remains the request's identity until its completion.
        let completedGeneration = snapshotGeneration
        var succeeded = false
        var cancelled = false
        var rejectedAsStale = false
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "snapshot")
            succeeded = accept(reply)
            rejectedAsStale = !succeeded
        } catch {
            if rejectedAsStale {
                rejectedAsStale = true
            } else if V3SnapshotErrorPolicy.shouldMarkDisconnected(error) {
                connected = false
                invalidateSetupFacts()
                requiresConnectionRetry = true
                present(error)
            } else {
                cancelled = true
            }
        }
        let outcome: V3ReloadOutcome = succeeded ? .applied :
            ((cancelled || rejectedAsStale) ? .notObserved : .snapshotFailed)
        // The only place a snapshot waiter is ever resumed. State is fully
        // applied first, so no caller can observe a partially updated snapshot.
        finishSnapshot(outcome: outcome, generation: completedGeneration)
        if installAttempt.hasActiveAttempt {
            NSLog("[V3_INSTALL_STATE] attempt=%@ event=snapshot_finished phase=%@",
                  installAttempt.attemptID?.uuidString ?? "none", installAttempt.phase.rawValue)
        }
        drainInstallPresentation(trigger: "snapshot_finished")
        // V3_SETUP_FACT_OBSERVATION_V1: only after an authoritative snapshot has
        // landed, so observation never runs against a disconnected store.
        observeSetupFactsIfNeeded()
        return outcome
    }

    /// V3_AWAITABLE_RELOAD_V1: the single place a snapshot activity ends.
    private func finishSnapshot(outcome: V3ReloadOutcome, generation: UInt64) {
        if externalMutationCount > 0 {
            loadActivity = .mutation
            loading = true
        } else {
            loadActivity = .idle
            loading = false
        }
        // The install presentation gate is snapshot-scoped: it advances only once
        // authoritative state has landed. A mutation advancing it would claim a
        // snapshot had happened.
        installAttempt.reloadFinished()
        for id in snapshotWaiterRegistry.take(throughSnapshotGeneration: generation) {
            guard let waiter = snapshotWaiters.removeValue(forKey: id) else { continue }
            waiter.continuation.resume(returning: outcome)
        }
        drainOwedSnapshot()
    }

    /// V3_LOAD_ACTIVITY_OWNERSHIP_V1: the single place a mutation activity ends.
    /// It resolves nothing. A caller awaiting authoritative status stays parked
    /// until a real snapshot completes, because a mutation's reply says nothing
    /// about the state a snapshot reports.
    private func finishMutation() {
        coordinatedMutationActive = false
        if externalMutationCount > 0 {
            loadActivity = .mutation
            loading = true
        } else {
            loadActivity = .idle
            loading = false
            drainOwedSnapshot()
        }
    }

    /// Runs the single owed snapshot once nothing blocks it.
    ///
    /// The decision is total. If policy refuses the snapshot, every parked
    /// continuation is resumed with `.notObserved` rather than left suspended,
    /// which is what let a non-manual deferred reload hang a task forever.
    private func drainOwedSnapshot() {
        let needsManual = snapshotWaiterRegistry.anyManualWaiter
        switch V3SnapshotGate.drain(activity: loadActivity, presentationActive: presentation != nil,
                                    owed: snapshotOwedIntent.isOwed, anyWaiterNeedsManual: needsManual,
                                    explicitManualOwed: snapshotOwedIntent.requiresManualSnapshot,
                                    requiresConnectionRetry: requiresConnectionRetry) {
        case .performSnapshot:
            startSnapshot(manual: snapshotOwedIntent.requiresManualSnapshot ||
                needsManual || !requiresConnectionRetry)
            Task { _ = await performSnapshot() }
        case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
            // Still blocked. The owed intent is kept for whoever ends it.
            break
        case .doNotObserve:
            guard snapshotOwedIntent.isOwed else { return }
            snapshotOwedIntent.clear()
            for id in snapshotWaiterRegistry.takeAll() {
                guard let waiter = snapshotWaiters.removeValue(forKey: id) else { continue }
                waiter.continuation.resume(returning: .notObserved)
            }
        }
    }
    @discardableResult
    func accept(_ snapshot: [String: Any]) -> Bool {
        guard V3ServiceBridge.shared.statusReplyMayApply(snapshot) else {
            _ = acceptRecoveryEvidence(snapshot)
            return false
        }
        guard let incomingIdentityStamp = snapshot["identityStamp"] as? String,
              !incomingIdentityStamp.isEmpty,
              V3ServiceBridge.strictBool(snapshot["identityStable"]) != nil else { return false }
        let incomingSigningContext = snapshot["hostSigningContext"] as? String
        if identityStamp != incomingIdentityStamp || hostSigningContext != incomingSigningContext {
            invalidateSetupFacts()
        }
        identityStamp = incomingIdentityStamp
        hostSigningContext = incomingSigningContext
        account = snapshot["account"] as? String ?? "Not signed in"
        team = snapshot["team"] as? String ?? "No active team"
        signing = snapshot["signing"] as? String ?? "Unknown"
        certificate = snapshot["certificate"] as? String ?? "Unknown"
        certificateExpiration = (snapshot["certificateExpiration"] as? Date).flatMap { $0 == .distantPast ? nil : $0 }
        pairing = snapshot["pairing"] as? String ?? "Unknown"
        authenticated = V3ServiceBridge.strictBool(snapshot["authenticated"]) ?? false
        pendingCertificateRecoveryState = V3PendingCertificateRecoveryState.decode(
            protocolVersion: V3ServiceBridge.strictInt(snapshot["pendingCertificateRecoveryProtocol"]),
            rawState: snapshot["pendingCertificateRecoveryState"] as? String)
        authenticationActive = V3ServiceBridge.strictBool(snapshot["authenticationActive"]) ?? false
        activeAccountPresent = V3ServiceBridge.strictBool(snapshot["activeAccountPresent"]) ?? false
        activeTeamPresent = V3ServiceBridge.strictBool(snapshot["activeTeamPresent"]) ?? false
        activeCertificatePresent = V3ServiceBridge.strictBool(snapshot["activeCertificatePresent"]) ?? false
        provisioningIncomplete = V3ServiceBridge.strictBool(snapshot["provisioningIncomplete"]) ?? false
        updatedAt = snapshot["updatedAt"] as? Date
        installedApps = (snapshot["installedApps"] as? [[String: Any]] ?? []).compactMap(V3SideStoreApp.init)
        sources = (snapshot["sources"] as? [[String: Any]] ?? []).compactMap(V3SideStoreSource.init)
        settings = snapshot["settings"] as? [String: Bool] ?? [:]
        connected = true
        applyFullRecoveryEvidence(snapshot)
        return true
    }

    private func acceptRecoveryEvidence(_ snapshot: [String: Any]) -> Bool {
        guard V3ServiceBridge.shared.statusReplyMayApplyRecoveryEvidence(snapshot) else { return false }
        V3ServiceBridge.shared.setHostRecoveryHold(true)
        observeRecoveryStorageFailure(snapshot)
        let recoveryHold = V3ServiceBridge.strictBool(snapshot["recoveryHold"]) == true
        var foundValidEvidence = false
        if snapshot.keys.contains("operationRecovery"),
           let recovery = snapshot["operationRecovery"] as? [String: Any],
           let session = recovery["session"] as? String,
           UUID(uuidString: session) != nil,
           let kind = recovery["kind"] as? String, !kind.isEmpty,
           let phaseText = recovery["phase"] as? String,
           let phase = V3OperationRecoveryRecord.Phase(rawValue: phaseText) {
            unresolvedOperationRecovery = V3OperationRecoveryRecord(sessionID: session, kind: kind,
                phase: phase, stagedIPAToken: recovery["stagedIPAToken"] as? String)
            foundValidEvidence = true
        } else if snapshot.keys.contains("operationRecovery") {
            unresolvedOperationRecovery = nil
            unresolvedRecoveryJournalUnreadable = true
        }
        if snapshot.keys.contains("refreshRecovery"),
           let refresh = snapshot["refreshRecovery"] as? [String: Any],
           let runID = refresh["runID"] as? String,
           let ownerLost = V3ServiceBridge.strictBool(refresh["ownerLost"]),
           UUID(uuidString: runID)?.uuidString == runID {
            if ownerLost { unresolvedRefreshRecoveryRunID = runID }
            foundValidEvidence = true
        } else if snapshot.keys.contains("refreshRecovery") {
            unresolvedRefreshRecoveryRunID = nil
            unresolvedRecoveryJournalUnreadable = true
        }
        if V3ServiceBridge.strictBool(snapshot["recoveryJournalUnreadable"]) == true {
            unresolvedRecoveryJournalUnreadable = true
        }
        if snapshot.keys.contains("directRecovery") {
            if recoveryHold,
               let direct = V3HostDirectRecoveryRecord(snapshotValue: snapshot["directRecovery"]) {
                unresolvedDirectRecovery = direct
                directRecoveryPostcondition = nil
                foundValidEvidence = true
            } else {
                unresolvedRecoveryJournalUnreadable = true
            }
        }
        if recoveryHold && !foundValidEvidence && !unresolvedRecoveryJournalUnreadable {
            unresolvedRecoveryJournalUnreadable = true
        }
        return true
    }

    private func observeRecoveryStorageFailure(_ snapshot: [String: Any]) {
        let known: Set<String> = ["malformedRecord", "incompatibleRecord", "storageUnavailable",
                                  "lockUnavailable", "readFailure", "deleteFailure"]
        let knownSteps: Set<String> = ["unknown", "appGroup", "directory", "open", "permissions",
                                       "flock", "metadata", "fileType", "readData", "parse", "schema"]
        guard let details = snapshot["recoveryStorageFailure"] as? [String: Any],
              let kind = details["kind"] as? String, known.contains(kind),
              let step = details["sourceStep"] as? String, knownSteps.contains(step),
              let present = V3ServiceBridge.strictBool(details["recordPresent"]),
              let eligible = V3ServiceBridge.strictBool(details["clearEligible"]),
              let retryable = V3ServiceBridge.strictBool(details["retryable"]),
              let domain = details["underlyingDomain"] as? String,
              let code = V3ServiceBridge.strictInt(details["underlyingCode"]) else {
            recoveryStorageKind = nil
            recoveryStorageClearEligible = false
            recoveryStorageDiagnostics = "schema=1 operation=status stage=persistence recovery_storage_kind=unknown"
            return
        }
        recoveryStorageKind = kind
        recoveryStorageClearEligible = present && eligible &&
            ["malformedRecord", "incompatibleRecord"].contains(kind)
        let native = CombinedFailure.safeDiagnosticUnderlying(domain: domain, code: code)
        var appGroupLine = ""
        if let group = snapshot["recoveryAppGroup"] as? [String: Any],
           let hash = group["groupHash"] as? String,
           (hash == "none" || (hash.count == 12 && hash.utf8.allSatisfy {
               (48...57).contains($0) || (97...102).contains($0)
           })),
           let source = group["selectionSource"] as? String,
           ["none", "inherited", "runtimeSelected"].contains(source),
           let entitled = group["signedEntitled"] as? String,
           ["yes", "no", "unknown"].contains(entitled),
           let resolves = V3ServiceBridge.strictBool(group["containerResolves"]) {
            appGroupLine = " group_hash=\(hash) group_selection_source=\(source) " +
                "signed_group_entitled=\(entitled) group_container_resolves=\(resolves)"
        }
        recoveryStorageDiagnostics = "schema=1 operation=status stage=persistence " +
            "recovery_storage_kind=\(kind) source_step=\(step) record_present=\(present) clear_eligible=\(recoveryStorageClearEligible) " +
            "underlying_domain=\(native.domain) underlying_code=\(native.code) retryable=\(retryable)" + appGroupLine
    }

    private func applyFullRecoveryEvidence(_ snapshot: [String: Any]) {
        observeRecoveryStorageFailure(snapshot)
        let recoveryHold = V3ServiceBridge.strictBool(snapshot["recoveryHold"]) == true
        var foundValidEvidence = false
        var unreadable = V3ServiceBridge.strictBool(snapshot["recoveryJournalUnreadable"]) == true
        if snapshot.keys.contains("operationRecovery"),
           let recovery = snapshot["operationRecovery"] as? [String: Any],
           let session = recovery["session"] as? String,
           UUID(uuidString: session) != nil,
           let kind = recovery["kind"] as? String, !kind.isEmpty,
           let phaseText = recovery["phase"] as? String,
           let phase = V3OperationRecoveryRecord.Phase(rawValue: phaseText) {
            unresolvedOperationRecovery = V3OperationRecoveryRecord(sessionID: session, kind: kind,
                phase: phase, stagedIPAToken: recovery["stagedIPAToken"] as? String)
            foundValidEvidence = true
        } else {
            unresolvedOperationRecovery = nil
            if snapshot.keys.contains("operationRecovery") { unreadable = true }
        }
        if snapshot.keys.contains("refreshRecovery"),
           let refresh = snapshot["refreshRecovery"] as? [String: Any],
           let runID = refresh["runID"] as? String,
           let ownerLost = V3ServiceBridge.strictBool(refresh["ownerLost"]),
           UUID(uuidString: runID)?.uuidString == runID {
            unresolvedRefreshRecoveryRunID = ownerLost ? runID : nil
            foundValidEvidence = true
        } else {
            unresolvedRefreshRecoveryRunID = nil
            if snapshot.keys.contains("refreshRecovery") { unreadable = true }
        }
        if snapshot.keys.contains("directRecovery") {
            if recoveryHold,
               let direct = V3HostDirectRecoveryRecord(snapshotValue: snapshot["directRecovery"]) {
                unresolvedDirectRecovery = direct
                foundValidEvidence = true
            } else {
                unreadable = true
            }
        }
        if recoveryHold && !foundValidEvidence && !unreadable { unreadable = true }
        unresolvedRecoveryJournalUnreadable = unreadable
        directRecoveryPostcondition = nil
        // Only a full, current-ticket snapshot with no durable hold can clear
        // the bridge admission fence. Locally retained unknown records keep it
        // closed even if an unrelated snapshot omits their journal entry.
        let locallyUnresolved = unresolvedOperationRecovery != nil ||
            unresolvedRefreshRecoveryRunID != nil || unresolvedRecoveryJournalUnreadable ||
            unresolvedDirectRecovery != nil
        V3ServiceBridge.shared.setHostRecoveryHold(recoveryHold || locallyUnresolved)
    }
    private func rejectForUnresolvedRecovery() -> Bool {
        guard unresolvedOperationRecovery != nil || unresolvedRefreshRecoveryRunID != nil ||
              unresolvedRecoveryJournalUnreadable || unresolvedDirectRecovery != nil else { return false }
        error = unresolvedRecoveryJournalUnreadable
            ? "The recovery record cannot be read. Use the recovery banner after checking the device before starting another operation." + "\nError ID: SS-SAVE-D086"
            : unresolvedDirectRecovery != nil
                ? "A previous request has an unresolved result. Inspect it in the recovery banner before repeating the action." + "\nError ID: SS-CMD-D087"
                : "A previous operation is unresolved. Use the recovery banner at the top of SideStore to resume its status check or reconcile after checking the device." + "\nError ID: SS-CMD-D088"
        return true
    }

    func perform(_ operation: String, target: String = "", title: String, value: Bool? = nil) {
        // A second operation while one is presented must explain itself
        // instead of silently doing nothing (which looks like the first tap
        // was ignored and invites blind retries).
        guard presentation == nil, !installAttempt.hasActiveAttempt else {
            self.error = "Another operation is already running. Finish or cancel it before starting a new one." + "\nError ID: SS-CMD-D004"
            return
        }
        guard !rejectForUnresolvedRecovery() else { return }
        guard loadActivity == .idle else {
            presentBusy()
            return
        }
        switch operation {
        case "signOut": signOut()
        case "syncAppIDs": syncAppIDs()
        case "clearCache": clearCache()
        case "refreshSources": refreshSources()
        case "jit": jit(target: target)
        case "install", "installURL", "installSharedIPA", "update", "refreshApp",
             "activate", "deactivate", "remove", "delete", "backup", "restore":
            let request = V3OperationRequest(operation: operation, target: target, title: title)
            presentation = request
        default: break
        }
    }
    private func needsSignIn(_ error: Error) -> Bool {
        guard let failure = error as? CombinedFailure else { return false }
        return V3SignInFailureRoutingPolicy.shouldOpenSignIn(
            stage: failure.stage, safeCause: failure.safeCause)
    }
    private func failed(_ error: Error) {
        if needsSignIn(error) { signInPresented = true }
        else { present(error) }
    }

    /// V3_LOAD_ACTIVITY_OWNERSHIP_V1: a busy store explains itself.
    ///
    /// refreshSources() was reachable from the global alert's "Retry Source"
    /// action, where a silent guard produced no work, no message, and a
    /// dismissed alert: the user was told a source retry had happened when
    /// nothing had been requested. Every entry point now reports the conflict.
    private func presentBusy() {
        // If a retry action was rejected, replace its original failure instead
        // of leaving an alert whose old text hides the new busy explanation.
        issue = nil
        self.error = "SideStore is still loading. Wait for the current request to finish, then try again." + "\nError ID: SS-READY-D005"
    }

    /// Runs one service mutation under an explicit mutation activity.
    ///
    /// A mutation owns the service but is not a snapshot, so it must never
    /// resolve a caller awaiting authoritative status. A snapshot is requested
    /// after it; a caller already parked is released by that snapshot, not by
    /// this one. The trailing reload is a plain request, so if the drain already
    /// started the owed snapshot this joins it instead of fetching twice.
    @discardableResult
    private func runMutation(_ operation: String, target: String = "", successNotice: String) -> Bool {
        guard !rejectForUnresolvedRecovery() else { return false }
        guard loadActivity == .idle else {
            presentBusy()
            return false
        }
        beginMutation()
        Task {
            do {
                let snapshot = try await V3ServiceBridge.shared.request(operation: operation, target: target)
                guard accept(snapshot) else {
                    finishMutation()
                    reload()
                    return
                }
                let signOutOutcome = operation == "signOut"
                    ? V3SignOutOutcomePolicy.resolve(
                        authenticated: V3ServiceBridge.strictBool(snapshot["authenticated"]),
                        activeAccountPresent: V3ServiceBridge.strictBool(snapshot["activeAccountPresent"]),
                        activeTeamPresent: V3ServiceBridge.strictBool(snapshot["activeTeamPresent"]))
                    : nil
                finishMutation()
                if let signOutOutcome {
                    if let verifiedNotice = V3SignOutOutcomePolicy.successNotice(for: signOutOutcome) {
                        notice = verifiedNotice
                    } else {
                        presentUnconfirmedSignOut(signOutOutcome)
                    }
                } else {
                    notice = successNotice
                }
                reload()
            } catch {
                finishMutation()
                failed(error)
                reload()
            }
        }
        return true
    }
    func signOut() { runMutation("signOut", successNotice: "Signed out successfully.") }
    func jit(target: String) { runMutation("jit", target: target, successNotice: "JIT enabled.") }
    func syncAppIDs() { runMutation("syncAppIDs", successNotice: "App IDs synced.") }
    func clearCache() { runMutation("clearCache", successNotice: "Download cache cleared.") }
    @discardableResult
    func refreshSources() -> Bool {
        runMutation("refreshSources", successNotice: "Sources updated.")
    }
    func stageSharedFile(_ data: Data, purpose: String) async -> String? {
        guard !data.isEmpty, data.count <= 4_194_304 else {
            self.error = "The selected file is empty or too large to hand to the SideStore service." + "\nError ID: SS-IPA-D006"
            return nil
        }
        guard let containerRoot = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) else {
            self.error = "The SideStore shared file container is unavailable. Check Connection and try again." + "\nError ID: SS-IPA-D007"
            return nil
        }
        let stagedToken = await Task.detached(priority: .utility) {
            V3SharedFileRecord.stage(data, purpose: purpose, containerRoot: containerRoot)
        }.value
        guard let token = stagedToken else {
            self.error = "Secure file staging is full or unavailable. Finish the pending import and try again." + "\nError ID: SS-IPA-D008"
            return nil
        }
        return token
    }
    func discardSharedFile(_ token: String) {
        guard let containerRoot = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) else { return }
        V3SharedFileRecord.discard(token, containerRoot: containerRoot)
    }
    func beginInstallPicker() {
        NSLog("[V3_INSTALL_UI] tap")
        guard !rejectForUnresolvedRecovery() else {
            NSLog("[V3_INSTALL_UI] tap_rejected reason=operation_recovery_required")
            return
        }
        guard presentation == nil else {
            NSLog("[V3_INSTALL_UI] tap_rejected reason=presentation_active")
            error = "Another operation is already running. Finish or cancel it before installing another app." + "\nError ID: SS-CMD-D009"
            return
        }
        guard !installAttempt.hasActiveAttempt else {
            NSLog("[V3_INSTALL_UI] tap_rejected reason=attempt_not_idle phase=%@",
                  installAttempt.phase.rawValue)
            error = hasUncertainInstallCancellation
                ? "SideStore has not confirmed that the previous install stopped. No new install was started; retry cancellation." + "\nError ID: SS-CMD-D085"
                : "An install attempt is still being resolved. Wait for it to finish, then try again." + "\nError ID: SS-CMD-D010"
            return
        }
        guard let attemptID = installAttempt.beginPicker() else {
            NSLog("[V3_INSTALL_UI] tap_rejected reason=attempt_not_idle phase=%@",
                  installAttempt.phase.rawValue)
            error = "An install attempt is still being resolved. Wait for it to finish, then try again." + "\nError ID: SS-CMD-D010"
            return
        }
        pendingPickerError = nil
        operationRecoveryDestination = nil
        NSLog("[V3_INSTALL_UI] begin_attempt result=started attempt=%@ loading=%d",
              attemptID.uuidString, loading ? 1 : 0)
    }

    func installPickerPresentationRequested(attemptID: UUID) {
        NSLog("[V3_INSTALL_UI] picker_present_requested attempt=%@", attemptID.uuidString)
    }

    func installPickerDidPresent(attemptID: UUID) {
        NSLog("[V3_INSTALL_UI] picker_did_present attempt=%@", attemptID.uuidString)
    }

    func installPickerPresentationFailed(attemptID: UUID, reason: String) {
        guard installAttempt.attemptID == attemptID else { return }
        NSLog("[V3_INSTALL_UI] tap_rejected reason=%@ attempt=%@", reason, attemptID.uuidString)
        NSLog("[V3_INSTALL_UI] picker_present_failed attempt=%@ reason=%@",
              attemptID.uuidString, reason)
        let token = resetInstallUI(attemptID: attemptID, outcome: "picker_presentation_failed")
        if let token { Task { _ = await cleanupStagedIPA(token, allowLocalFallback: true) } }
        error = "The IPA picker could not be opened. Tap Install / Sideload App to try again." + "\nError ID: SS-IPA-D011"
    }

    func cancelInstallPicker(attemptID: UUID) {
        if let pending = pendingPickerError, pending.attemptID == attemptID {
            pendingPickerError = nil
            NSLog("[V3_INSTALL_UI] terminal attempt=%@ outcome=staging_failed", attemptID.uuidString)
            error = pending.message
            return
        }
        guard installAttempt.attemptID == attemptID else { return }
        let token = resetInstallUI(attemptID: attemptID, outcome: "picker_cancelled")
        if let token { Task { _ = await cleanupStagedIPA(token, allowLocalFallback: true) } }
    }

    func cancelIPAStaging() {
        guard installAttempt.phase == .staging, let attemptID = installAttempt.attemptID else { return }
        // Invalidate ownership immediately. A coordinated copy may still be
        // running; its worker owns cleanup until it returns, never the UI.
        _ = resetInstallUI(attemptID: attemptID, outcome: "staging_cancelled")
    }

    @discardableResult
    func stagePickerIPA(_ url: URL, attemptID: UUID) -> Bool {
        NSLog("[V3_INSTALL_UI] picker_selected attempt=%@", attemptID.uuidString)
        guard installAttempt.beginStaging(attemptID: attemptID) else {
            NSLog("[V3_INSTALL_UI] picker_selection_rejected attempt=%@ reason=stale_attempt",
                  attemptID.uuidString)
            return false
        }
        NSLog("[V3_INSTALL_STATE] attempt=%@ event=staging_started", attemptID.uuidString)
        return stageIPA(url, attemptID: attemptID, bookmark: nil,
                        title: "Install / Sideload App with SideStore",
                        waitsForPickerDismissal: true)
    }

    @discardableResult
    func stageSharedIPA(_ url: URL, bookmark: Data? = nil, title: String) -> Bool {
        guard !rejectForUnresolvedRecovery() else { return false }
        guard presentation == nil, !installAttempt.hasActiveAttempt,
              let attemptID = installAttempt.beginDirectStaging() else {
            error = "Another operation is already running. Finish or cancel it before installing another app." + "\nError ID: SS-CMD-D009"
            return false
        }
        pendingPickerError = nil
        return stageIPA(url, attemptID: attemptID, bookmark: bookmark, title: title,
                        waitsForPickerDismissal: false)
    }

    func cleanupOrphanedStagedIPAs() async {
        guard let container = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) else { return }
        var protectedTokens = Set<String>()
        if let token = unresolvedOperationRecovery?.stagedIPAToken {
            protectedTokens.insert(token)
        }
        if let hostToken = installAttempt.token,
           let canonical = try? V3IPAStaging.canonicalToken(hostToken) {
            protectedTokens.insert(canonical)
        }
        do {
            // Age alone cannot prove a staged file is unused: native callbacks
            // may leave a backend mutation alive past its request deadline.
            // Ask the service for active token ownership and fail closed if the
            // service cannot give an authoritative answer.
            let reply = try await V3ServiceBridge.shared.request(operation: "ipaActiveTokens")
            guard let tokens = reply["tokens"] as? [String], tokens.count <= 512 else { return }
            for token in tokens {
                guard let canonical = try? V3IPAStaging.canonicalToken(token) else { return }
                protectedTokens.insert(canonical)
            }
            _ = try V3IPAStaging.cleanupOrphans(containerRoot: container,
                preservingTokens: protectedTokens)
        } catch {
            // Unavailable ownership means skip pruning. Do not expose paths or
            // filenames in user-copyable logs.
        }
    }

    // Returns admission, not a staged token. No coordinated file access or copy runs on
    // MainActor; the token is handed to the current attempt only after await.
    private func stageIPA(_ url: URL, attemptID: UUID, bookmark: Data?, title: String,
                          waitsForPickerDismissal: Bool) -> Bool {
        guard let container = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) else {
            failIPAStaging(CombinedIPAFileError(.fileAccess), attemptID: attemptID,
                           waitsForPickerDismissal: waitsForPickerDismissal)
            return false
        }
        dismissedIPAStagingAttemptID = nil
        let task = Task { [weak self] in
            do {
                let token = try await V3IPAStaging.stageOffMainActor(
                    sourceURL: url, bookmark: bookmark, containerRoot: container)
                guard let self, !Task.isCancelled,
                      self.installAttempt.attemptID == attemptID,
                      self.installAttempt.phase == .staging else {
                    // This token was never dispatched. Clean only this worker's
                    // result in its captured container, not a newer attempt's.
                    await V3IPAStaging.cleanupUnclaimedOffMainActor(token: token, containerRoot: container)
                    return
                }
                self.ipaStagingTask = nil
                let waitForPicker = waitsForPickerDismissal && self.dismissedIPAStagingAttemptID != attemptID
                guard self.installAttempt.staged(attemptID: attemptID, token: token, title: title,
                                                waitsForPickerDismissal: waitForPicker,
                                                isLoading: self.loading) else {
                    self.failIPAStaging(CombinedIPAFileError(.stagingFailed), attemptID: attemptID,
                                        waitsForPickerDismissal: waitsForPickerDismissal)
                    await V3IPAStaging.cleanupUnclaimedOffMainActor(token: token, containerRoot: container)
                    return
                }
                NSLog("[V3_INSTALL_UI] staged attempt=%@ phase=%@ loading=%d",
                      attemptID.uuidString, self.installAttempt.phase.rawValue, self.loading ? 1 : 0)
                if !waitForPicker { self.drainInstallPresentation(trigger: "input_staged") }
            } catch {
                guard let self, self.installAttempt.attemptID == attemptID,
                      self.installAttempt.phase == .staging else { return }
                self.ipaStagingTask = nil
                if Task.isCancelled {
                    _ = self.resetInstallUI(attemptID: attemptID, outcome: "staging_cancelled")
                    return
                }
                self.failIPAStaging((error as? CombinedIPAFileError) ?? CombinedIPAFileError(.stagingFailed),
                                    attemptID: attemptID, waitsForPickerDismissal: waitsForPickerDismissal)
            }
        }
        ipaStagingTask = (attemptID, task)
        return true
    }

    private func failIPAStaging(_ failure: CombinedIPAFileError, attemptID: UUID,
                               waitsForPickerDismissal: Bool) {
        guard installAttempt.attemptID == attemptID, installAttempt.phase == .staging else { return }
        let waitForPicker = waitsForPickerDismissal && dismissedIPAStagingAttemptID != attemptID
        _ = resetInstallUI(attemptID: attemptID, outcome: "staging_failed")
        if waitForPicker { pendingPickerError = (attemptID, V3FailureGuidance.message(failure)) }
        else { self.error = V3FailureGuidance.message(failure) }
    }

    private func drainInstallPresentation(trigger: String) {
        guard let request = installAttempt.takeReadyOperation(
            isLoading: loading, hasActiveOperationPresentation: presentation != nil
        ) else {
            if installAttempt.hasActiveAttempt {
                NSLog("[V3_INSTALL_UI] operation_present_wait trigger=%@ phase=%@ loading=%d presentation_active=%d",
                      trigger, installAttempt.phase.rawValue, loading ? 1 : 0,
                      presentation == nil ? 0 : 1)
            }
            return
        }
        NSLog("[V3_INSTALL_UI] host_cover_request attempt=%@ operation=%@ trigger=%@",
              request.attemptID.uuidString, request.operationID.uuidString, trigger)
        presentation = V3OperationRequest(id: request.operationID, operation: "installSharedIPA",
            target: request.token, title: request.title, installAttemptID: request.attemptID)
        NSLog("[V3_INSTALL_UI] operation_present_requested attempt=%@ operation=%@",
              request.attemptID.uuidString, request.operationID.uuidString)
    }

    func installPickerDidDisappear(attemptID: UUID) {
        if let pending = pendingPickerError, pending.attemptID == attemptID {
            pendingPickerError = nil
            error = pending.message
            return
        }
        guard installAttempt.attemptID == attemptID else { return }
        // Dismissal and copy completion may arrive in either order.
        if installAttempt.phase == .staging {
            dismissedIPAStagingAttemptID = attemptID
            return
        }
        guard installAttempt.pickerDidDisappear(attemptID: attemptID, isLoading: loading) else { return }
        NSLog("[V3_INSTALL_UI] picker_dismissed attempt=%@ loading=%d",
              attemptID.uuidString, loading ? 1 : 0)
        drainInstallPresentation(trigger: "picker_did_dismiss")
    }

    func installBackendStartRequested(attemptID: UUID?, operationID: UUID, sessionID: String) {
        guard let attemptID,
              installAttempt.backendStartRequested(attemptID: attemptID,
                  operationID: operationID, sessionID: sessionID) else { return }
        NSLog("[V3_INSTALL_STATE] attempt=%@ event=backend_start_requested session=%@",
              attemptID.uuidString, sessionID)
    }

    func installOperationDidPresent(attemptID: UUID?, operationID: UUID) {
        guard let attemptID,
              installAttempt.markOperationViewDidAppear(attemptID: attemptID, operationID: operationID) else { return }
        NSLog("[V3_INSTALL_UI] operation_did_present attempt=%@ operation=%@",
              attemptID.uuidString, operationID.uuidString)
    }

    func installBackendStarted(attemptID: UUID?, operationID: UUID, sessionID: String) {
        guard let attemptID,
              installAttempt.backendStarted(attemptID: attemptID, operationID: operationID, sessionID: sessionID) else { return }
        NSLog("[V3_INSTALL_STATE] attempt=%@ phase=operationStarted backend_session=%@",
              attemptID.uuidString, sessionID)
    }

    func installTerminal(attemptID: UUID?, operationID: UUID, outcome: String) {
        guard let attemptID,
              installAttempt.recordTerminal(attemptID: attemptID, operationID: operationID, outcome: outcome) else { return }
        NSLog("[V3_INSTALL_UI] terminal attempt=%@ operation=%@ outcome=%@",
              attemptID.uuidString, operationID.uuidString, outcome)
    }

    func prepareInstallRetry(attemptID: UUID?) {
        guard let attemptID, let operationID = presentation?.id,
              installAttempt.prepareRetry(attemptID: attemptID, operationID: operationID) else { return }
        NSLog("[V3_INSTALL_UI] retry_started attempt=%@ operation=%@",
              attemptID.uuidString, operationID.uuidString)
    }

    @discardableResult
    func resetInstallUI(attemptID: UUID, outcome: String,
                        preserveRecoveryDestination: Bool = false) -> String? {
        guard installAttempt.attemptID == attemptID else { return nil }
        let token = installAttempt.token
        switch installAttempt.phase {
        case .terminal:
            guard installAttempt.beginCleanup(attemptID: attemptID),
                  installAttempt.finishCleanup(attemptID: attemptID) else { return nil }
        case .cleaningUp:
            guard installAttempt.finishCleanup(attemptID: attemptID) else { return nil }
        default:
            guard installAttempt.resetBeforeBackend(attemptID: attemptID) else { return nil }
        }
        if ipaStagingTask?.attemptID == attemptID {
            ipaStagingTask?.task.cancel()
            ipaStagingTask = nil
        }
        if dismissedIPAStagingAttemptID == attemptID { dismissedIPAStagingAttemptID = nil }
        if presentation?.installAttemptID == attemptID { presentation = nil }
        if pendingPickerError?.attemptID == attemptID { pendingPickerError = nil }
        if !preserveRecoveryDestination { operationRecoveryDestination = nil }
        NSLog("[V3_INSTALL_UI] reset_to_idle attempt=%@ outcome=%@",
              attemptID.uuidString, outcome)
        return token
    }

    func operationCoverDidDismiss() {
        guard let attemptID = installAttempt.attemptID,
              installAttempt.phase == .operationPresented,
              !installAttempt.operationViewDidAppear,
              installAttempt.backendSessionID == nil else { return }
        let token = resetInstallUI(attemptID: attemptID, outcome: "operation_presentation_failed")
        error = "The install screen could not be opened. The attempt was cleared; tap Install / Sideload App again." + "\nError ID: SS-CMD-D012"
        if let token { Task { _ = await cleanupStagedIPA(token, allowLocalFallback: true) } }
    }

    func retryInstallCancellation() {
        guard let attemptID = installAttempt.attemptID,
              let operationID = installAttempt.operationID,
              let sessionID = installAttempt.backendSessionID else {
            error = "No install session is available to cancel. Keep this screen open and reload operation status." + "\nError ID: SS-CMD-D013"
            return
        }
        Task { @MainActor in
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "opCancel", target: sessionID)
                guard let terminalState = V3OperationCancellationOutcomePolicy.terminalState(
                    expectedSessionID: sessionID,
                    replySessionID: reply["session"] as? String,
                    state: reply["state"] as? String,
                    backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                    stopConfirmed: V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                    outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])) else {
                    self.error = "SideStore has not confirmed that the device operation stopped. The IPA and operation session were kept; retry cancellation or check device state before another install." + "\nError ID: SS-CMD-D014"
                    return
                }
                _ = installAttempt.recordTerminal(attemptID: attemptID,
                    operationID: operationID, outcome: terminalState)
                self.error = terminalState == "cancelled" ? nil :
                    (terminalState == "completed"
                        ? "The install completed before cancellation was confirmed."
                        : "The install ended with a failure before cancellation was confirmed." + "\nError ID: SS-CMD-D084")
                let token = resetInstallUI(attemptID: attemptID, outcome: terminalState)
                if let token { _ = await cleanupStagedIPA(token, allowLocalFallback: true) }
                reload()
            } catch {
                NSLog("[V3_INSTALL_UI] cancellation_unconfirmed attempt=%@ session=%@",
                      attemptID.uuidString, sessionID)
                self.error = "SideStore still cannot confirm that the install stopped. No new install was started. Reconnect, then retry cancellation." + "\nError ID: SS-CMD-D015"
            }
        }
    }

    func cleanupStagedIPA(_ token: String, allowLocalFallback: Bool = false) async -> Bool {
        do {
            _ = try await V3ServiceBridge.shared.request(operation: "ipaCleanup", target: token)
            return true
        } catch {
            let serviceReportsBusy = (error as? CombinedFailure).map {
                $0.code == .busy ||
                    $0.safeCause == .operationInProgress
            } ?? false
            guard V3StagedIPACleanupFallbackPolicy.mayDeleteLocally(
                serviceReportsBusy: serviceReportsBusy,
                callerConfirmsNeverStartedOrSettled: allowLocalFallback) else {
                NSLog("[V3_INSTALL_UI] staged_cleanup_deferred reason=%@",
                      serviceReportsBusy ? "service_busy" : "backend_state_unconfirmed")
                return false
            }
            // The host uses the same canonical UUID-only staging helper for a
            // local fallback only when no backend can still use the token. It
            // never accepts or constructs a caller path.
            do {
                guard let container = V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID()) else {
                    throw CombinedIPAFileError(.fileAccess)
                }
                try V3IPAStaging.cleanup(token: token, containerRoot: container)
                NSLog("[V3_INSTALL_UI] staged_cleanup_fallback result=success")
                return true
            } catch {
                NSLog("[V3_INSTALL_UI] staged_cleanup_failed reason=service_and_local_cleanup_unavailable")
                return false
            }
        }
    }
}

struct V3SideStoreApp: Identifiable, Hashable {
    let identifier: String, bundleID: String, name: String, version: String, certificateStatus: String
    let isActive: Bool, hasUpdate: Bool, isHost: Bool
    let expirationDate: Date?
    let openURL: URL?
    var id: V3AppIdentity { .installed(uri: identifier) }
    init?(_ row: [String: Any]) {
        guard let identifier = row["identifier"] as? String, let bundleID = row["bundleID"] as? String,
              let name = row["name"] as? String, let version = row["version"] as? String,
              let isActive = row["isActive"] as? Bool, let hasUpdate = row["hasUpdate"] as? Bool else { return nil }
        self.identifier = identifier; self.bundleID = bundleID; self.name = name; self.version = version
        self.isActive = isActive; self.hasUpdate = hasUpdate
        expirationDate = row["expirationDate"] as? Date
        certificateStatus = row["certificateStatus"] as? String ?? "unknown"
        openURL = (row["openURL"] as? String).flatMap(URL.init(string:))
        isHost = row["isHost"] as? Bool ?? false
    }
}

struct V3SideStoreSource: Identifiable, Hashable {
    let identifier: String, name: String, subtitle: String, url: String
    let appCount: Int
    let canRemove: Bool
    var id: V3AppIdentity { .source(identifier: identifier) }
    init?(_ row: [String: Any]) {
        guard let identifier = row["identifier"] as? String, let name = row["name"] as? String,
              let url = row["url"] as? String, let appCount = row["appCount"] as? Int else { return nil }
        self.identifier = identifier; self.name = name; self.url = url; self.appCount = appCount
        subtitle = row["subtitle"] as? String ?? ""; canRemove = row["canRemove"] as? Bool ?? false
    }
}

struct V3InstalledAppsSection: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @AppStorage(LCGridSize.storageKey, store: LCUtils.appGroupUserDefault) private var gridSize: LCGridSize = .medium
    @ScaledMetric(relativeTo: .caption) private var textScale: CGFloat = 1
    @AppStorage("LCShowAppLabels", store: LCUtils.appGroupUserDefault) private var labels = true
    var query = ""
    private var apps: [V3SideStoreApp] { status.installedApps.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) || $0.bundleID.localizedCaseInsensitiveContains(query) } }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Sideloaded Apps").font(.headline)
                Spacer()
                Text("\(status.installedAppCount)")
                    .font(.caption.weight(.bold))
                    .foregroundColor(.secondary)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(Color(UIColor.secondarySystemFill)))
            }
            V3RefreshAllButton()
            if status.isStale {
                Button {
                    status.reload()
                } label: {
                    Label("Reconnect to SideStore", systemImage: "arrow.clockwise")
                        .font(.caption)
                }
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: gridSize.minimumWidth * min(1.5, max(1, textScale))), spacing: 16, alignment: .top)], spacing: 16) {
                ForEach(apps) { app in
                    NavigationLink(destination: V3SideStoreAppDetail(identifier: app.identifier)) {
                        VStack(spacing: 6) {
                            V3InstalledAppIcon(identifier: app.identifier, version: app.version, size: gridSize.iconSize)
                            if labels {
                                Text(app.name).lineLimit(2).font(.caption).foregroundColor(.primary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if !app.isActive {
                                Text("Inactive").font(.caption2).foregroundColor(.secondary)
                            } else if let expiration = app.expirationDate {
                                Text(expiration, style: .relative).font(.caption2).foregroundColor(.secondary)
                            }
                        }.frame(maxWidth: .infinity, minHeight: gridSize.iconSize + 8)
                            .padding(.vertical, 4)
                    }.accessibilityLabel(app.name).contextMenu { V3AppActions(app: app) }
                }
            }
            if apps.isEmpty {
                HStack {
                    Spacer()
                    V3LocalizedText(status.loading ? "Loading apps..." : "No sideloaded apps")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                    Spacer()
                }
                .padding(.vertical, 8)
            }
            Text("LiveContainer Guests").font(.headline).padding(.top)
        }.padding(.horizontal)
    }
}

struct V3InstalledAppIcon: View {
    let identifier: String
    let version: String
    let size: CGFloat
    @State private var image: UIImage?
    var body: some View {
        Group {
            if let image { Image(uiImage: image).resizable().scaledToFit() }
            else { Image(systemName: "app.fill").resizable().scaledToFit().foregroundColor(.secondary) }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.23))
        .accessibilityHidden(true)
        .task(id: identifier + version) {
            image = nil
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "appIcon", target: identifier)
                try Task.checkCancellation()
                if let data = reply["icon"] as? Data { image = UIImage(data: data) }
            } catch {}
        }
    }
}

struct V3AppActions: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @EnvironmentObject private var sharedModel: SharedModel
    let app: V3SideStoreApp
    var body: some View {
        if app.isActive, let url = app.openURL, !app.isHost {
            Button("Open") { UIApplication.shared.open(url) { opened in
                if !opened { Task { @MainActor in status.error = "The app could not be opened. Check whether it is still installed." + "\nError ID: SS-CMD-D016" } }
            } }
        }
        Button("Refresh") {
            sharedModel.selectedTab = .home
            status.refreshTarget = app.isHost ? nil : app.identifier
            status.refreshPresented = true
        }
        if app.hasUpdate { Button("Update") { action("update", "Update " + app.name) } }
        if !app.isHost {
            Button(LocalizedStringKey(app.isActive ? "Deactivate" : "Activate")) { action(app.isActive ? "deactivate" : "activate", app.isActive ? "Deactivate app" : "Activate app") }
            Button("Back Up") { action("backup", "Back up app") }
            Button("Restore Backup") { action("restore", "Restore backup") }
            Button("Enable JIT") { action("jit", "Enable JIT") }
            Button("Remove from Library", role: .destructive) { action("remove", "Remove " + app.name + " from library and erase its backups") }
            if app.isActive { Button("Delete from Device", role: .destructive) { action("delete", "Delete " + app.name + " and erase its data and backups") } }
        }
    }
    private func action(_ operation: String, _ title: String) { status.perform(operation, target: app.identifier, title: title) }
}

struct V3SideStoreAppDetail: View {
    @Environment(\.locale) private var dateLocale
    @EnvironmentObject private var status: V3SideStoreStatusStore
    let identifier: String
    private var app: V3SideStoreApp? { status.installedApps.first { $0.identifier == identifier } }
    var body: some View {
        List {
            if let app {
                Section {
                    HStack(spacing: 16) {
                        Image(systemName: "app.fill")
                            .font(.system(size: 48))
                            .foregroundColor(.accentColor)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(app.name)
                                .font(.title3.weight(.bold))
                            Text(app.bundleID)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .textSelection(.enabled)
                            Text(V3Localization.format("Version %@", app.version))
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    .padding(.vertical, 4)
                }
                Section("Status") {
                    HStack {
                        Label("State", systemImage: "circle.fill")
                            .foregroundColor(app.isActive ? .green : .secondary)
                        Spacer()
                        V3LocalizedText(app.isActive ? "Active" : "Inactive")
                            .foregroundColor(.secondary)
                    }
                    HStack {
                        Label("Certificate", systemImage: "signature")
                        Spacer()
                        V3LocalizedText(app.certificateStatus.capitalized)
                            .foregroundColor(.secondary)
                    }
                    if let expiration = app.expirationDate {
                        HStack {
                            Label("Expires", systemImage: "calendar.badge.clock")
                            Spacer()
                            Text(V3Localization.date(expiration, date: .abbreviated, time: .shortened, language: V3AppLanguage.resolve(dateLocale.identifier)))
                                .foregroundColor(.secondary)
                        }
                    }
                }
                Section("Actions") { V3AppActions(app: app) }
            } else {
                Text("This app is no longer in the library.")
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(app?.name ?? V3Localization.string("App"))
    }
}

struct V3SourcesView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var preview: [String: Any]?
    @State private var previewBusy = false
    @State private var addBusy = false
    @State private var removeBusy = false
    @State private var notice = ""
    @State private var sourceFailure: V3SourceAddFailure?
    @State private var failedSourceInput: String?
    // V3_SOURCE_SEMANTIC_STATE_V1: a successful add is a success, and is never
    // rendered with the same neutral grey as an informational note.
    @State private var addSucceeded = false
    @State private var removeCandidate: V3SideStoreSource?
    // V3_SOURCE_KEYBOARD_DISMISS_V1 (issue #40): the field had no focus state and
    // no explicit dismiss action, so the only way out of the keyboard felt like
    // Return, which read as if Return were also the submit action.
    @FocusState private var sourceFieldFocused: Bool
    // @State so the pre-edit value survives; the view is a struct, so a plain
    // stored var could not be assigned from a non-mutating method.
    @State private var sourceURLBeforeEditing: String = ""
    @State private var sourceURLBeforeOpening: String = ""
    @State private var isAddSourcePresented = false
    @State private var sourceOpenRequestLedger = V3SourceFormOpenRequestLedger()
    @State private var sourcePreviewSession = V3SourcePreviewSession()
    @State private var sourcePreviewTask: Task<Void, Never>?
    private var savedGuestSources: [String] {
        (UserDefaults.standard.stringArray(forKey: "LCAltStoreSourceURLs") ?? [])
            .filter { saved in !status.sources.contains(where: { $0.url == saved }) }
    }
    var body: some View {
        NavigationView {
            List {
                if addSucceeded && !notice.isEmpty {
                    Section {
                        V3LocalizedLabel(notice, systemImage: V3StatusSeverity.completed.icon)
                            .font(.footnote)
                            .foregroundColor(.green)
                    }
                } else if !notice.isEmpty {
                    Section {
                        // V3_SOURCE_SEMANTIC_STATE_V1: an informational notice is
                        // neutral, never styled as if it were a result.
                        V3LocalizedLabel(notice, systemImage: "info.circle.fill")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    }
                }
                if let sourceFailure {
                    Section("What happened") {
                        // A source failure is a failure, and says so.
                        V3LocalizedLabel(sourceFailure.whatHappened, systemImage: V3StatusSeverity.failed.icon)
                            .font(.footnote)
                            .foregroundColor(.red)
                    }
                    Section("What you can do") {
                        V3LocalizedText(sourceFailure.whatToDo).font(.footnote)
                    }
                    Section {
                        DisclosureGroup("Technical details") {
                            Text(sourceFailure.technicalDetails).environment(\.layoutDirection, .leftToRight)
                                .font(.caption2)
                                .textSelection(.enabled)
                        }
                        Button("Copy Diagnostics") {
                            UIPasteboard.general.string = sourceFailure.technicalDetails
                        }
                        .font(.caption)
                    }
                }
                if isAddSourcePresented {
                    Section("Add Source") {
                        HStack {
                            Image(systemName: "link")
                                .foregroundColor(.secondary)
                            TextField("https://example.com/source.json", text: $status.sourceURL)
                                .keyboardType(.URL)
                                .autocapitalization(.none)
                                .disableAutocorrection(true)
                                .disabled(addBusy)
                                .focused($sourceFieldFocused)
                                // Return only dismisses the keyboard. It never
                                // previews and never adds a source.
                                .submitLabel(.done)
                                .onSubmit { dismissKeyboard() }
                                // The pre-edit value is captured when editing actually
                                // begins, which is focus. It used to be captured when a
                                // preview was requested, so a Cancel after typing but
                                // before previewing restored the wrong value, and a
                                // Cancel after previewing restored the value that was
                                // already on screen. Only the rising edge captures,
                                // because Cancel itself drops focus and must not
                                // overwrite the value it is about to restore.
                        .onChange(of: sourceFieldFocused) { focused in
                            if focused { sourceURLBeforeEditing = status.sourceURL }
                        }
                        .onChange(of: status.sourceURL) { newURL in
                            if let activeRequest = sourcePreviewSession.activeRequest,
                               activeRequest.targetURL != newURL {
                                invalidateSourcePreview()
                            }
                            if failedSourceInput != newURL {
                                sourceFailure = nil
                                failedSourceInput = nil
                            }
                            if let previewURL = preview?["url"] as? String, previewURL != newURL {
                                preview = nil
                            }
                        }
                        }
                        // Explicit keyboard dismissal, with an explicit Cancel that
                        // performs no preview, no network request and no persistence.
                        .toolbar {
                            ToolbarItemGroup(placement: .keyboard) {
                                Spacer()
                                Button("Cancel") { cancelSourceEditing() }
                                    .disabled(!V3SourceEditingPolicy.canCancelForm(isAdding: addBusy))
                                Button("Done") { dismissKeyboard() }
                            }
                        }
                        Button {
                            startPreviewSource()
                        } label: {
                            V3LocalizedLabel(previewBusy ? "Checking Source..." : "Preview and Add Source", systemImage: "plus.circle.fill")
                        }
                        .disabled(status.sourceURL.isEmpty || previewBusy || isSubmissionBlocked(for: status.sourceURL))
                        if let preview {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(preview["name"] as? String ?? "")
                                    .font(.headline)
                                V3LocalizedText(preview["title"] as? String ?? "")
                                    .font(.subheadline)
                                V3LocalizedText(preview["message"] as? String ?? "")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                            .padding(.vertical, 4)
                            Button {
                                Task { await confirmAdd(url: preview["url"] as? String ?? status.sourceURL) }
                            } label: {
                                V3LocalizedLabel((preview["alreadyAdded"] as? Bool ?? false) ? "Already Added" : (addBusy ? "Adding Source..." : "Confirm Add Source"), systemImage: "checkmark.circle.fill")
                            }
                            .disabled((preview["alreadyAdded"] as? Bool ?? false) || addBusy ||
                                isSubmissionBlocked(for: preview["url"] as? String ?? status.sourceURL))
                        }
                    }
                } else {
                    Section {
                        Button(action: openSourceForm) {
                            Label("Add Source", systemImage: "plus.circle.fill")
                        }
                        .accessibilityHint("Opens the source URL form. Nothing is added until you confirm.")
                    }
                }
                Section(V3Localization.text("Sources (\(status.sources.count))")) {
                    ForEach(status.sources) { source in
                        NavigationLink(destination: V3CatalogView(source: source)) {
                            HStack(spacing: 12) {
                                Image(systemName: "folder.fill")
                                    .font(.title3)
                                    .foregroundColor(.accentColor)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(source.name)
                                        .font(.headline)
                                    V3LocalizedText("\(source.appCount) apps")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                        .contextMenu {
                            if source.canRemove {
                                Button(role: .destructive) {
                                    removeCandidate = source
                                } label: {
                                    Label("Remove Source", systemImage: "trash")
                                }
                            }
                        }
                    }
                }
                if !savedGuestSources.isEmpty {
                    Section("Previously Saved Guest Sources") {
                        ForEach(savedGuestSources, id: \.self) { url in
                            Button {
                                status.sourceURL = url
                                openSourceForm()
                            } label: {
                                HStack {
                                    Image(systemName: "bookmark")
                                        .foregroundColor(.secondary)
                                    Text(url)
                                        .lineLimit(1)
                                        .truncationMode(.middle)
                                }
                            }
                        }
                        Text("Select a saved URL to preview and add it to the unified catalog. Existing saved URLs are preserved.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Sources")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Group {
                        if isAddSourcePresented {
                            Button("Cancel", action: cancelSourceForm)
                                .disabled(!V3SourceEditingPolicy.canCancelForm(isAdding: addBusy))
                                .accessibilityHint("Closes Add Source without starting an add request. An in-flight preview read may be cancelled.")
                        }
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        status.refreshSources()
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(status.loading)
                }
            }
            .confirmationDialog("Remove this source?", isPresented: Binding(get: { removeCandidate != nil }, set: { if !$0 { removeCandidate = nil } }), titleVisibility: .visible) {
                Button("Remove Source", role: .destructive) {
                    if let candidate = removeCandidate {
                        Task { await confirmRemove(id: candidate.identifier) }
                    }
                }
                Button("Cancel", role: .cancel) { removeCandidate = nil }
            } message: {
                Text("Apps already installed from this source stay installed, but they will no longer receive updates.")
            }
        }
        .navigationViewStyle(StackNavigationViewStyle())
        .onAppear {
            receiveSourceOpenRequest(status.sourceFormOpenRequestID)
        }
        .onChange(of: status.sourceFormOpenRequestID) { requestID in
            // Handles source routes delivered while this tab is already mounted.
            receiveSourceOpenRequest(requestID)
        }
    }
    private func openSourceForm() {
        invalidateSourcePreview()
        sourceURLBeforeOpening = status.sourceURL
        isAddSourcePresented = true
        preview = nil
        sourceFailure = nil
        failedSourceInput = nil
        notice = ""
        addSucceeded = false
    }
    private func receiveSourceOpenRequest(_ requestID: UUID?) {
        guard sourceOpenRequestLedger.claim(requestID) else { return }
        openSourceForm()
    }
    // V3_SOURCE_KEYBOARD_DISMISS_V1: dismissing the keyboard is a pure UI action.
    // It previews nothing, requests nothing and persists nothing.
    /// Done keeps the typed value and dismisses the keyboard. It is a pure UI
    /// dismissal: it never previews, requests, or persists anything.
    private func dismissKeyboard() {
        sourceFieldFocused = false
        _ = V3SourceEditingPolicy.done(typed: status.sourceURL)
    }

    /// Cancel restores the URL that was present when editing began and dismisses
    /// the keyboard.
    ///
    /// It performs no network request, no preview and no source mutation. It
    /// deliberately does not discard an already-rendered preview either: a
    /// preview the user just spent a request on used to vanish silently,
    /// together with its Confirm action, which is a source mutation the user
    /// never asked to lose.
    private func cancelSourceEditing() {
        status.sourceURL = V3SourceEditingPolicy.resolved(
            V3SourceEditingPolicy.cancel(typed: status.sourceURL, beforeEditing: sourceURLBeforeEditing),
            typed: status.sourceURL)
        sourceFieldFocused = false
    }

    /// The visible navigation Cancel works with the keyboard shown or hidden.
    /// The production transition starts no backend work. It closes the form,
    /// restores its opening value, discards the local preview, and invalidates
    /// any in-flight read-only preview request so its late reply cannot apply.
    private func cancelSourceForm() {
        invalidateSourcePreview()
        let transition = V3SourceEditingPolicy.closeForm(V3SourceFormState(
            isPresented: isAddSourcePresented,
            url: status.sourceURL,
            originalURL: sourceURLBeforeOpening,
            isFocused: sourceFieldFocused,
            hasPreview: preview != nil))
        status.sourceURL = transition.state.url
        sourceFieldFocused = transition.state.isFocused
        preview = nil
        isAddSourcePresented = transition.state.isPresented
    }

    private func invalidateSourcePreview() {
        sourcePreviewSession.invalidate()
        sourcePreviewTask?.cancel()
        sourcePreviewTask = nil
        previewBusy = false
    }

    private func startPreviewSource() {
        guard isAddSourcePresented else { return }
        invalidateSourcePreview()
        guard let request = sourcePreviewSession.begin(targetURL: status.sourceURL) else { return }
        previewBusy = true
        sourceFailure = nil
        failedSourceInput = nil
        notice = ""
        addSucceeded = false
        sourcePreviewTask = Task { await previewSource(request) }
    }

    private func previewSource(_ request: V3SourcePreviewRequest) async {
        defer {
            if sourcePreviewSession.activeRequest == request {
                previewBusy = false
                sourcePreviewTask = nil
            }
        }
        do {
            let payload = try await V3ServiceBridge.shared.request(operation: "sourcePreview", target: request.targetURL)
            guard sourcePreviewSession.mayApply(request, currentURL: status.sourceURL,
                                                formPresented: isAddSourcePresented) else { return }
            let row = V3SourcePreviewSession.responseRow(payload, for: request)
            preview = row
            // Previewing is an explicit action, so the keyboard has served its
            // purpose once the preview is on screen.
            dismissKeyboard()
        } catch {
            guard sourcePreviewSession.mayApply(request, currentURL: status.sourceURL,
                                                formPresented: isAddSourcePresented) else { return }
            sourceFailure = V3SourceAddFailure(error)
            failedSourceInput = request.targetURL
        }
    }
    private func confirmAdd(url: String) async {
        addBusy = true
        notice = ""
        sourceFailure = nil
        failedSourceInput = nil
        defer { addBusy = false }
        let mutationTicket = status.beginDirectMutation()
        var acceptedSnapshot: [String: Any]?
        var reloadAfterFailure = false
        defer {
            status.finishDirectMutation(ticket: mutationTicket, reply: acceptedSnapshot,
                requestReload: reloadAfterFailure)
        }
        do {
            let result = try await V3ServiceBridge.shared.request(operation: "sourceAddConfirmed", target: url)
            guard let message = V3SourceAddPersistencePolicy.confirmationMessage(result),
                  let identifier = result["identifier"] as? String,
                  let sources = result["sources"] as? [[String: Any]],
                  sources.contains(where: { $0["identifier"] as? String == identifier }) else {
                throw V3SourceAddPersistencePolicy.unverifiedPersistenceFailure(
                    correlationID: UUID().uuidString)
            }
            acceptedSnapshot = result
            _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                result, operation: "sourceAddConfirmed")
            preview = nil
            status.sourceURL = ""
            isAddSourcePresented = false
            failedSourceInput = nil
            notice = message
            addSucceeded = true
        } catch {
            reloadAfterFailure = true
            sourceFailure = V3SourceAddFailure(error)
            failedSourceInput = url
        }
    }

    private func isSubmissionBlocked(for input: String) -> Bool {
        guard let sourceFailure else { return false }
        return !V3SourceSubmissionPolicy.mayResubmit(retryable: sourceFailure.retryable,
            safeCause: sourceFailure.safeCause,
            failedInput: failedSourceInput, currentInput: input)
    }
    private func confirmRemove(id: String) async {
        removeCandidate = nil
        removeBusy = true
        notice = "Removing source..."
        addSucceeded = false
        defer { removeBusy = false }
        let mutationTicket = status.beginDirectMutation()
        do {
            let result = try await V3ServiceBridge.shared.request(operation: "sourceRemoveConfirmed", target: id)
            _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                result, operation: "sourceRemoveConfirmed")
            status.finishDirectMutation(ticket: mutationTicket, reply: result)
            notice = "Source removed."
        } catch {
            status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
            notice = ""
            status.present(error)
        }
    }
}

struct V3CatalogApp: Identifiable {
    let id: String, name: String, version: String, developer: String, description: String, installedID: String
    let canInstall: Bool
    let downloadURL: String
    let installedVersion: String?
    init?(_ row: [String: Any]) {
        guard V3CatalogRowPolicy.isDisplayable(row),
              let id = row["identifier"] as? String,
              let name = row["name"] as? String else { return nil }
        self.id = id; self.name = name; version = row["version"] as? String ?? ""
        developer = row["developer"] as? String ?? ""; description = row["description"] as? String ?? ""
        installedID = row["installedID"] as? String ?? ""; canInstall = row["canInstall"] as? Bool ?? false
        downloadURL = row["downloadURL"] as? String ?? ""
        installedVersion = row["installedVersion"] as? String
    }
}

struct V3CatalogView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @EnvironmentObject private var sharedModel: SharedModel
    @Environment(\.dismiss) private var dismiss
    let source: V3SideStoreSource
    @State private var apps: [V3CatalogApp] = []
    @State private var query = ""
    @State private var loading = true
    @State private var error: String?
    @State private var failure: V3OperationFailureDetails?
    @State private var loadInFlight = false
    var body: some View {
        List {
            if loading {
                HStack {
                    Spacer()
                    ProgressView("Loading catalog...")
                    Spacer()
                }
                .padding()
            }
            if let error {
                Section("What happened") {
                    V3LocalizedText(failure?.whatHappened ?? error).font(.footnote).foregroundColor(.red)
                    if let failure {
                        Text("What you can do").font(.caption.weight(.semibold)).padding(.top, 4)
                        V3LocalizedText(failure.whatToDo).font(.footnote)
                        DisclosureGroup("Technical details") {
                            Text(failure.technical).environment(\.layoutDirection, .leftToRight).font(.caption2).textSelection(.enabled)
                        }
                        Button("Copy Diagnostics") { UIPasteboard.general.string = failure.technical }
                    }
                    if failure?.safeCause == CombinedFailure.SafeCause.catalogSourceUnavailable.rawValue {
                        Button("Return to Sources") { dismiss() }
                    }
                    switch V3CatalogRetryPresentationPolicy.action(
                        for: failure?.retryDisposition ?? .unknown,
                        safeCause: failure?.safeCause) {
                    case .retry:
                        Button("Retry Catalog") { Task { await load() } }.disabled(loadInFlight)
                    case .retryWithUnknownDisposition:
                        Button("Try Catalog Again (retryability unknown)") { Task { await load() } }
                            .disabled(loadInFlight)
                    case .reloadCatalog:
                        Button("Reload Catalog") { Task { await load() } }.disabled(loadInFlight)
                    case .noRetry:
                        EmptyView()
                    }
                }
            }
            ForEach(apps.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) }) { app in
                NavigationLink {
                    List {
                        Section {
                            HStack(spacing: 16) {
                                Image(systemName: "app.fill")
                                    .font(.system(size: 48))
                                    .foregroundColor(.accentColor)
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(app.name)
                                        .font(.title3.weight(.bold))
                                    Text(app.developer)
                                        .font(.subheadline)
                                        .foregroundColor(.secondary)
                                    Text(V3Localization.format("Version %@", app.version))
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                            }
                            .padding(.vertical, 4)
                        }

                        if !app.description.isEmpty {
                            Section("Description") {
                                Text(app.description)
                                    .font(.body)
                            }
                        }

                        Section("Actions") {
                            if let installed = status.installedApps.first(where: { $0.identifier == app.installedID }) {
                                V3AppActions(app: installed)
                            } else {
                                Button {
                                    status.perform("install", target: app.id, title: "Install " + app.name)
                                } label: {
                                    Label("Install with SideStore", systemImage: "arrow.down.app.fill")
                                }
                                .disabled(!app.canInstall)
                            }
                            Button {
                                var link = URLComponents()
                                link.scheme = "livecontainer"
                                link.host = "install"
                                link.queryItems = [URLQueryItem(name: "url", value: app.downloadURL)]
                                sharedModel.deepLink = link.url
                                sharedModel.selectedTab = .apps
                            } label: {
                                Label("Install as LiveContainer Guest", systemImage: "square.stack.3d.up")
                            }
                            .disabled(!app.canInstall || app.downloadURL.isEmpty)
                        }
                    }
                    .listStyle(.insetGrouped)
                    .navigationTitle(app.name)
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "app.fill")
                            .font(.title2)
                            .foregroundColor(.accentColor)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(app.name)
                                .font(.headline)
                            Text(app.developer + (app.version.isEmpty ? "" : " · v" + app.version))
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        Spacer()
                        if status.installedApps.contains(where: { $0.identifier == app.installedID }) {
                            Text("Installed")
                                .font(.caption.weight(.semibold))
                                .foregroundColor(.secondary)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Capsule().fill(Color(UIColor.secondarySystemFill)))
                        } else if app.canInstall {
                            Text("GET")
                                .font(.caption.weight(.bold))
                                .foregroundColor(.accentColor)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 4)
                                .background(Capsule().fill(Color.accentColor.opacity(0.15)))
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(source.name)
        .searchable(text: $query, prompt: "Search apps in " + source.name)
        .task { await load() }
    }
    private func load() async {
        guard !loadInFlight else { return }
        loadInFlight = true
        defer { loadInFlight = false }
        loading = true
        apps.removeAll(keepingCapacity: true)
        error = nil
        failure = nil
        defer { loading = false }
        do {
            var cursor = 0
            // V3_CATALOG_ROW_POLICY_V1: retain the raw rows and a persistent
            // identifier set across pages, then map once. Re-deduplicating the
            // full accumulated array for every page made large catalogs quadratic.
            var accumulated = V3CatalogRowsAccumulator()
            repeat {
                try Task.checkCancellation()
                // V3_CATALOG_PAGE_VALIDATION_V1: a page is validated instead of
                // being coerced. A missing or mistyped app list is no longer
                // silently shown as an empty catalog, and a cursor that does not
                // advance is a typed invalid response rather than a raw error.
                let result = try await V3ServiceBridge.shared.request(operation: "catalog", target: source.identifier, cursor: cursor)
                guard let rawApps = result["apps"] as? [[String: Any]] else {
                    throw catalogResponseFailure(cursor: cursor)
                }
                guard rawApps.allSatisfy(V3CatalogRowPolicy.isDisplayable) else {
                    throw catalogResponseFailure(cursor: cursor)
                }
                guard let number = result["nextCursor"] as? NSNumber,
                      CFGetTypeID(number) != CFBooleanGetTypeID(),
                      let next = number as? Int else {
                    throw catalogResponseFailure(cursor: cursor)
                }
                // The real deduplication rule: duplicates are removed within a
                // page and across pages, first-seen order preserved.
                accumulated.append(rawApps)
                guard next == -1 || next > cursor else { throw catalogResponseFailure(cursor: cursor) }
                cursor = next
            } while cursor >= 0
            let mappedApps = accumulated.rows.compactMap(V3CatalogApp.init)
            guard mappedApps.count == accumulated.rows.count else { throw catalogResponseFailure(cursor: cursor) }
            apps = mappedApps
        } catch is CancellationError {
            // A cancelled load is lifecycle, not a catalog failure. Presenting it
            // as an error would blame the source for a navigation change.
            error = nil
            failure = nil
        } catch {
            if let combined = error as? CombinedFailure {
                failure = V3OperationFailureDetails(combined)
                self.error = combined.safeMessage
            } else {
                failure = nil
                self.error = "The source catalog could not be loaded." + "\nError ID: SS-CAT-D017"
            }
        }
    }

    // V3_CATALOG_DIAGNOSTICS_V1: an unreadable page is reported against the
    // catalog stage with the request's own correlation, and never contains the
    // source identifier, app rows, or any raw payload.
    private func catalogResponseFailure(cursor: Int) -> CombinedFailure {
        var failure = CombinedFailure(operation: "catalog", stage: .catalog, code: .invalidResponse,
                                     id: UUID().uuidString, safeCause: .catalogUnavailable,
                                     sourceStep: .catalogRead)
        failure.annotatingCatalogPage(cursor: cursor)
        return failure
    }
}

struct V3AccountSettings: View {
    let betaUpdateState: V3BoolSettingState
    let idleTimeoutState: V3BoolSettingState
    @Environment(\.locale) private var dateLocale

    @EnvironmentObject private var status: V3SideStoreStatusStore
    private var identityPresentation: V3AccountSessionPresentation {
        V3AccountSessionPresentationPolicy.resolve(authenticated: status.authenticated,
            activeAccountPresent: status.activeAccountPresent,
            activeTeamPresent: status.activeTeamPresent,
            activeCertificatePresent: status.activeCertificatePresent)
    }

    var body: some View {
        Section("Setup") {
            Button {
                NSLog("[V3_SETUP] OPEN source=settings")
                status.setupPresented = true
            } label: {
                Label("Setup Assistant", systemImage: "list.clipboard.fill")
            }
        }
        Section("Account and Signing") {
            if identityPresentation.showSignIn {
                V3SignInLink(title: "Sign In with Apple ID")
                if identityPresentation.showSavedAppleID {
                    HStack {
                        Label("Saved Apple ID", systemImage: "person.crop.circle.fill")
                        Spacer()
                        Group {
                            if status.activeAccountPresent { Text(status.account) }
                            else { Text("Not available") }
                        }
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                    }
                }
                if identityPresentation.showUnverifiedSavedState {
                    VStack(alignment: .leading, spacing: 6) {
                        Label("Saved account state is not verified",
                              systemImage: "exclamationmark.triangle.fill")
                            .foregroundColor(.orange)
                        Text("SideStore still has a saved account or team, but cannot confirm an active Apple sign-in. Sign in again or explicitly sign out of the saved account state.")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    }
                }
                if identityPresentation.showRetainedCertificateGuidance {
                    VStack(alignment: .leading, spacing: 6) {
                        Label("Saved certificate is retained separately",
                              systemImage: "doc.text.magnifyingglass")
                        Text("Sign Out does not remove signing certificates. Open Certificates to review or remove a saved certificate.")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                        Button("Open Certificates") { status.certificatesPresented = true }
                    }
                }
            } else {
                HStack {
                    Label("Apple ID", systemImage: "person.crop.circle.fill")
                    Spacer()
                    Group {
                            if status.activeAccountPresent { Text(status.account) }
                            else { Text("Not available") }
                        }
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
            }
            HStack {
                Label("Team", systemImage: "person.2.fill")
                Spacer()
                Group {
                            if status.activeTeamPresent { Text(status.team) }
                            else { Text("Not available") }
                        }
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            HStack {
                Label("Signing", systemImage: "signature")
                Spacer()
                V3LocalizedText(status.signing)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            if let date = status.certificateExpiration {
                HStack {
                    Label("Certificate", systemImage: "doc.plaintext")
                    Spacer()
                    Text(V3Localization.format("Expires %@", V3Localization.date(date, date: .abbreviated, time: .shortened, language: V3AppLanguage.resolve(dateLocale.identifier))))
                        .foregroundColor(.secondary)
                }
            }
            ForEach(status.installedApps.filter { $0.isHost }) { app in
                HStack {
                    Label("Host App", systemImage: "app.badge.fill")
                    Spacer()
                    Text(app.expirationDate.map { V3Localization.format("%@, expires %@", V3Localization.prose(app.certificateStatus.capitalized), V3Localization.date($0, date: .abbreviated, time: .omitted, language: V3AppLanguage.resolve(dateLocale.identifier))) } ?? V3Localization.prose(app.certificateStatus.capitalized))
                        .foregroundColor(.secondary)
                }
            }
            // V3_PROVISIONING_NEEDS_ATTENTION_V1: an authenticated session with
            // incomplete provisioning is signed in, so the recovery row is
            // presented as provisioning work, never as a sign-in problem. It is
            // placed before the signed-in-only block because
            // provisioningIncomplete can only be true for a signed-in account.
            if status.provisioningIncomplete {
                NavigationLink {
                    V3SignInView().environmentObject(status)
                } label: {
                    Label("Provisioning needs attention", systemImage: "exclamationmark.triangle.fill")
                        .foregroundColor(.orange)
                }
                .accessibilityHint("Apple ID is signed in. Retry provisioning or finish later.")
            }
            if status.pendingCertificateRecoveryState.requiresAttention {
                NavigationLink {
                    V3SignInView().environmentObject(status)
                } label: {
                    Label("Pending Certificate Recovery", systemImage: "exclamationmark.triangle.fill")
                        .foregroundColor(.orange)
                }
                .accessibilityIdentifier("account.pending-certificate-recovery")
            }
            // Account status is available only for a signed-in account. When
            // signed out, the section above already offers Sign In.
            if !status.needsSignIn {
                NavigationLink {
                    V3SignInView().environmentObject(status)
                } label: {
                    Label("Sign-In Status", systemImage: "person.badge.key.fill")
                }
                .accessibilityHint("Review the current Apple sign-in state")
            }
            Button {
                status.syncAppIDs()
            } label: {
                Label("Sync App IDs", systemImage: "arrow.triangle.2.circlepath")
            }
            .disabled(!V3DeveloperDataActionAvailabilityPolicy.isEnabled(
                authenticated: status.authenticated, isLoading: status.loading))
            .accessibilityHint("Sign in with Apple ID before syncing App IDs.")
            link("Certificates", icon: "doc.text") { V3CertificatesView().environmentObject(status) }
            link("Developer Services", icon: "wrench.and.screwdriver") { V3DeveloperServicesView().environmentObject(status) }
            if identityPresentation.showSignOut {
                Button(role: .destructive) {
                    status.signOut()
                } label: {
                    Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                }
            }
        }

        Section("Device") {
            HStack {
                Label("Pairing Status", systemImage: "link")
                Spacer()
                V3LocalizedText(V3PairingPresentationPolicy.displayText(statusConnected: status.connected,
                    pairingStatus: status.pairing))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            NavigationLink {
                V3PairingView().environmentObject(status)
            } label: {
                Label("Import Pairing File", systemImage: "doc.badge.plus")
            }
            link("Connection", icon: "network") { V3ConnectionView().environmentObject(status) }
        }

        Section("Apps and Data") {
            link("SideStore Backups", icon: "archivebox") { V3BackupsView().environmentObject(status) }
            link("Installation and Signing Options", icon: "slider.horizontal.3") { V3CustomizationsView().environmentObject(status) }
            Button {
                status.clearCache()
            } label: {
                Label("Clear Download Cache", systemImage: "trash")
            }
        }

        Section("Services") {
            link("Anisette Servers", icon: "server.rack") { V3AnisetteView().environmentObject(status) }
            link("SideSign Configuration", icon: "pencil.and.outline") { V3SideSignView().environmentObject(status) }
            link("SideJIT Server", icon: "bolt.fill") { V3SideJITView().environmentObject(status) }
            link("Update Channel", icon: "arrow.triangle.merge") { V3ReleaseTrackHostView().environmentObject(status) }
            setting("Beta updates", "isBetaUpdatesEnabled", icon: "sparkles", state: betaUpdateState)
            setting("Disable idle timeout", "isIdleTimeoutDisableEnabled", icon: "timer", state: idleTimeoutState)
        }

        Section("Diagnostics") {
            link("Health Check", icon: "heart.text.square") { V3HealthView().environmentObject(status) }
            link("Operation Logs", icon: "doc.text.magnifyingglass") { V3LogsView().environmentObject(status) }
            link("SideStore Diagnostics", icon: "waveform.path.ecg") { V3DiagnosticsView().environmentObject(status) }
            link("Experimental Features", icon: "flask") { V3ExperimentalView().environmentObject(status) }
        }

        Section("Guest Runtime") {
            NavigationLink {
                LCTweaksView()
            } label: {
                Label("Tweaks", systemImage: "slider.vertical.3")
            }
        }
    }
    private func link<Destination: View>(_ title: String, icon: String, @ViewBuilder destination: () -> Destination) -> some View {
        NavigationLink(destination: destination) {
            HStack {
                V3LocalizedLabel(title, systemImage: icon)
                Spacer()
            }
        }
    }
    private func setting(_ title: String, _ key: String, icon: String, state: V3BoolSettingState) -> some View {
        V3BoolSettingRow(title: title, key: key, icon: icon, state: state)
            .environmentObject(status)
    }
}

// Owned by LCSettingsView, not the direction-keyed Form subtree.
// In-flight loads/writes continue to address the same reconciliation state.
@MainActor final class V3BoolSettingState: ObservableObject {
    @Published var value = false
    @Published var loaded = false
    var loadingRequest = false
    var writeGenerations = V3SettingsWriteGeneration()
    var confirmedValue: Bool?
    var pendingWriteReconciliation = false
    private var loadGeneration: UInt64 = 0

    func load(key: String, status: V3SideStoreStatusStore) async {
        // A new Settings attachment supersedes a canceled load even if its
        // underlying request has not returned yet. Direction changes do not
        // restart this task because its owner is outside the keyed Form.
        loadGeneration &+= 1
        let generation = loadGeneration
        loadingRequest = true
        let capturedWrites = writeGenerations
        defer { if generation == loadGeneration { loadingRequest = false } }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "settingsGet")
            guard !Task.isCancelled, generation == loadGeneration,
                  writeGenerations.isUnchanged(since: capturedWrites) else { return }
            pendingWriteReconciliation = false
            if let bools = reply["bools"] as? [String: Bool], let current = bools[key] {
                value = current
                confirmedValue = current
            } else if let legacy = status.settings[key] {
                value = legacy
                confirmedValue = legacy
            }
            loaded = true
        } catch {
            if !Task.isCancelled, generation == loadGeneration,
               writeGenerations.isUnchanged(since: capturedWrites) { status.present(error) }
        }
    }
}

struct V3BoolSettingRow: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    let title: String
    let key: String
    let icon: String
    @ObservedObject var state: V3BoolSettingState
    var body: some View {
        Toggle(isOn: Binding(get: { state.value }, set: { state.value = $0; save($0) })) {
            V3LocalizedLabel(title, systemImage: icon)
        }
        .disabled(status.isStale || !state.loaded)
    }
    private func save(_ newValue: Bool) {
        let generation = state.writeGenerations.begin(key)
        Task {
            defer { finishWrite(generation) }
            let mutationTicket = status.beginDirectMutation()
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                    payload: ["key": key, "type": "bool", "bool": newValue])
                _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                    reply, operation: "settingsSet")
                status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
                if state.writeGenerations.isCurrent(generation, for: key) {
                    state.confirmedValue = newValue
                    status.reload()
                } else {
                    state.pendingWriteReconciliation = true
                }
            } catch {
                status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
                guard state.writeGenerations.isCurrent(generation, for: key) else {
                    state.pendingWriteReconciliation = true
                    return
                }
                let loaded = await reloadAuthoritative(generation: generation)
                guard state.writeGenerations.isCurrent(generation, for: key) else {
                    state.pendingWriteReconciliation = true
                    return
                }
                if !loaded {
                    state.value = state.confirmedValue ?? !newValue
                }
                status.present(error)
            }
        }
    }
    private func finishWrite(_ generation: UInt64) {
        state.writeGenerations.finish(generation, for: key)
        guard state.pendingWriteReconciliation,
              !state.writeGenerations.hasPendingWrites(for: key) else { return }
        state.pendingWriteReconciliation = false
        let settledGeneration = state.writeGenerations.current(for: key)
        Task {
            // Reconcile once all writes settle; response order need not match
            // backend commit order, even when the newest reply arrived first.
            let verified = await reloadAuthoritative(generation: settledGeneration)
            if !verified, state.writeGenerations.isCurrent(settledGeneration, for: key) {
                state.pendingWriteReconciliation = true
                status.notice = "The saved setting could not be verified. Reload settings to check its value." + "\nError ID: SS-SAVE-D018"
            }
        }
    }
    private func reloadAuthoritative(generation: UInt64) async -> Bool {
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "settingsGet")
            guard state.writeGenerations.isCurrent(generation, for: key),
                  let bools = reply["bools"] as? [String: Bool], let current = bools[key] else { return false }
            state.value = current
            state.confirmedValue = current
            status.reload()
            return true
        } catch {
            return false
        }
    }
}

private struct V3SourceAddFailure {
    let whatHappened: String
    let whatToDo: String
    let technicalDetails: String
    let retryable: Bool?
    let safeCause: String?

    init(_ error: Error) {
        let failure: CombinedFailure
        if let combined = error as? CombinedFailure {
            failure = V3SourceAddFailurePolicy.normalized(combined)
        } else {
            failure = CombinedFailure.capture(error,
                operation: "source", stage: .source, id: UUID().uuidString)
        }
        whatHappened = failure.safeMessage
        whatToDo = failure.recovery
        technicalDetails = failure.technicalDetails
        retryable = failure.retryable
        safeCause = failure.safeCause?.rawValue
    }
}

private struct V3StatusStoreKey: EnvironmentKey {
    static var defaultValue: V3SideStoreStatusStore? { nil }
}

extension EnvironmentValues {
    var v3StatusStore: V3SideStoreStatusStore? {
        get { self[V3StatusStoreKey.self] }
        set { self[V3StatusStoreKey.self] = newValue }
    }
}

struct V3TargetedRefreshSection: View {
    @Environment(\.locale) private var dateLocale

    // Custom key with a nil default: programmatic navigation links can
    // evaluate their destination outside the inherited environment on some
    // iOS versions. A missing store must hide this section, never trap.
    @Environment(\.v3StatusStore) private var status
    var body: some View {
        if let status,
           let target = status.refreshTarget,
           let app = status.installedApps.first(where: { $0.identifier == target }) {
            Section("Selected App") {
                HStack {
                    Label(app.name, systemImage: "app.fill")
                    Spacer()
                    if let date = app.expirationDate {
                        Text(V3Localization.format("Expires %@", V3Localization.date(date, date: .abbreviated, time: .shortened, language: V3AppLanguage.resolve(dateLocale.identifier))))
                            .foregroundColor(.secondary)
                    }
                }
                // V3_REFRESH_PREREQUISITE_POLICY_V1: targeted refresh uses the
                // same contract. A known-missing pairing file blocks the mutation
                // and offers the same recovery action instead of starting a run
                // that can only fail.
                if V3RefreshPrerequisite.evaluate(statusConnected: status.connected,
                    pairingStatus: status.pairing).blocksTargetedRefresh {
                    Text("A pairing file is required before this device can be refreshed.")
                        .font(.footnote)
                    Text("Place or import a valid pairing file, then try again.")
                        .font(.footnote).foregroundColor(.secondary)
                    Button("Show Pairing Setup") { status.pairingPresented = true }
                }
                Button {
                    status.perform("refreshApp", target: target, title: "Refresh " + app.name)
                } label: {
                    Label(V3Localization.format("Refresh %@", app.name), systemImage: "arrow.clockwise")
                }
                .disabled(V3RefreshPrerequisite.evaluate(statusConnected: status.connected,
                    pairingStatus: status.pairing).blocksTargetedRefresh)
                Button("Clear Selection") { status.refreshTarget = nil }
            }
        }
    }
}

struct V3OperationSheet: View {
    @Environment(\.locale) private var displayLocale
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @Environment(\.dismiss) private var dismiss
    let request: V3OperationRequest
    @State private var attempt = V3OperationAttemptState()
    @State private var uncertainSessionID: String?
    @State private var state = "working"
    @State private var progress = 0.0
    @State private var hasProgress = false
    @State private var terminalBackendSettled: Bool?
    @State private var deviceCheckConfirmedForCompletion = false
    @State private var operationPhase = V3OperationPhase.working
    @State private var prompt: [String: Any]?
    @State private var sourceOffer: [String: String]?
    @State private var promptResponseBlocked = false
    @State private var sourceAddFailure: V3OperationFailureDetails?
    @State private var sourceAddRetryBlocked = false
    @State private var message = ""
    @State private var task: Task<Void, Never>?
    @State private var startedGeneration: UUID?
    @State private var isDismissing = false
    @State private var promptSubmitting = false
    @State private var userRequestedCancellation = false
    @State private var failureContext = V3OperationRetryContext()
    @State private var whatToDo = ""
    @State private var technicalDetails = ""
    @State private var recoveryDestination: String?
    @State private var retryBlocked = false
    @State private var needsDeviceConfirmation = false
    @State private var confirmUncertainRetirement = false
    @State private var stagedIPACleaned = false
    @State private var copied = false
    private var retryAllowed: Bool {
        guard !retryBlocked else { return false }
        if state == "requiresSource" { return sourceOffer != nil }
        if state == "cancelled" { return true }
        guard state == "failed", !isTransitioning else { return false }
        return [.allowed, .unknown].contains(failureContext.retryDisposition)
    }
    private var retryButtonTitle: String {
        V3OperationRetryButtonPolicy.title(state: state,
            retryDisposition: failureContext.retryDisposition)
    }
    private var sourceAddButtonTitle: String {
        guard let sourceAddFailure else { return "Add Source and Retry" }
        if sourceAddRetryBlocked { return "Resolve Source Issue" }
        return sourceAddFailure.retryable == nil
            ? "Try Source Add Again (retryability unknown)" : "Try Source Add Again"
    }
    private func recoveryActionTitle(for destination: String) -> String? {
        switch destination {
        case "signIn": return "Open Account & Signing"
        case "certificates": return "Open Certificates"
        case "ipa": return "Choose IPA Again"
        case "connection": return "Open Connection Settings"
        case "sources": return "Open Sources"
        // This destination opens the Setup Assistant, so it must say so. It read
        // "Open Connection Settings" while routing to the assistant, which is the
        // same class of mislabel as offering a connection retry for a source
        // failure.
        case "setup": return "Open Setup Assistant"
        default: return nil
        }
    }
    private var isTransitioning: Bool { attempt.transitionInFlight }
    private var isRunning: Bool { ["working", "awaitingPrompt", "cancelling"].contains(state) }
    private var displayProgress: Double { V3NormalizedProgress.displayValue(progress, state: state) }
    private var progressPercent: Int { V3NormalizedProgress.percent(progress, state: state) }
    private var completionAwaitingSettlement: Bool {
        V3OperationCompletionPolicy.requiresDeviceCheck(state: state,
            backendSettled: terminalBackendSettled,
            deviceCheckConfirmed: deviceCheckConfirmedForCompletion,
            outcomeUnknown: needsDeviceConfirmation)
    }
    var body: some View {
        NavigationView {
            List {
                Section {
                    HStack {
                        Text("Status")
                        Spacer()
                        if state == "completed" {
                            Label("Completed", systemImage: "checkmark.circle.fill")
                                .foregroundColor(.green)
                        } else {
                            V3LocalizedText(statusText).foregroundColor(.secondary)
                        }
                    }
                    if isRunning || state == "completed" {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text("Progress")
                                Spacer()
                                Text(hasProgress || state == "completed" ? "\(progressPercent)%" : "—")
                                    .foregroundColor(.secondary)
                            }
                            ProgressView(value: hasProgress || state == "completed" ? displayProgress : nil)
                        }
                    }
                }
                if let prompt {
                    V3PromptSection(prompt: prompt, isSubmitting: $promptSubmitting,
                                    isSubmissionBlocked: promptResponseBlocked) { answer in
                        Task { await answerPrompt(id: prompt["id"] as? String ?? "", answer: answer) }
                    }
                }
                if let offer = sourceOffer {
                    Section("Missing Source") {
                        V3LocalizedText("\"\((offer["name"] ?? ""))\" is not added. Add it, then the operation retries automatically.")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                        Button {
                            guard !isTransitioning else { return }
                            Task { await addSourceAndRetry(id: offer["id"] ?? "") }
                        } label: {
                            V3LocalizedLabel(sourceAddButtonTitle, systemImage: "plus.circle.fill")
                        }
                        .disabled(isTransitioning || sourceAddRetryBlocked)
                    }
                }
                if !message.isEmpty {
                    Section("What happened") {
                        V3LocalizedText(message)
                            .font(.footnote)
                            .textSelection(.enabled)
                    }
                    if !whatToDo.isEmpty {
                        Section("What you can do") {
                            V3LocalizedText(whatToDo).font(.footnote)
                            if needsDeviceConfirmation, uncertainSessionID != nil {
                                Button("Reconcile After Checking Device") {
                                    confirmUncertainRetirement = true
                                }
                            }
                            if let destination = recoveryDestination,
                               let action = recoveryActionTitle(for: destination) {
                                Button(LocalizedStringKey(action)) { openRecoveryDestination(destination) }
                            }
                            if retryAllowed {
                                Button(LocalizedStringKey(isTransitioning ? "Waiting..." : retryButtonTitle)) { retry() }
                                    .buttonStyle(.borderedProminent)
                                    .disabled(isTransitioning)
                            }
                        }
                    } else if retryAllowed {
                        Section("What you can do") {
                            Button(LocalizedStringKey(isTransitioning ? "Waiting..." : retryButtonTitle)) { retry() }
                                .buttonStyle(.borderedProminent)
                                .disabled(isTransitioning)
                        }
                    }
                    if !technicalDetails.isEmpty {
                        Section {
                            DisclosureGroup("Technical details") {
                                Text(technicalDetails).environment(\.layoutDirection, .leftToRight)
                                    .font(.caption2)
                                    .textSelection(.enabled)
                            }
                            Button(LocalizedStringKey(copied ? "Copied" : "Copy Diagnostics")) {
                                UIPasteboard.general.string = V3DiagnosticCopy.details(visibleMessage: message, technical: technicalDetails)
                                copied = true
                                Task {
                                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                                    copied = false
                                }
                            }
                            .font(.caption)
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle(V3Localization.prose(request.title, language: .resolve(displayLocale.identifier)))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(LocalizedStringKey(isRunning ? (state == "cancelling" ? "Cancelling..." : "Cancel") :
                        (completionAwaitingSettlement ? "Reconcile" : "Done"))) {
                        if isRunning {
                            cancelAttempt()
                        } else if completionAwaitingSettlement {
                            confirmUncertainRetirement = true
                        } else {
                            acknowledgeAndDismiss()
                        }
                    }
                    .disabled(isTransitioning || state == "cancelling")
                }
            }
        }
        .navigationViewStyle(StackNavigationViewStyle())
        .interactiveDismissDisabled(true)
        .confirmationDialog("Check the device before restarting SideStore?", isPresented: $confirmUncertainRetirement,
                            titleVisibility: .visible) {
            Button("I checked; restart SideStore", role: .destructive) {
                confirmUncertainOperationAfterDeviceCheck()
            }
            Button("Keep Waiting", role: .cancel) {}
        } message: {
            Text("Use this only after confirming the device is no longer installing, updating, refreshing, backing up, restoring, or deleting the app.")
        }
        .task {
            if request.operation == "installSharedIPA" {
                status.installOperationDidPresent(attemptID: request.installAttemptID,
                    operationID: request.id)
            }
            start()
        }
        .onDisappear {
            if !isDismissing {
                isDismissing = true
                let oldTask = task
                let wasTerminal = attempt.isTerminal
                let terminalOutcome = wasTerminal &&
                    ["completed", "failed", "cancelled", "timedOut"].contains(state)
                    ? state : "cancelled"
                let mustConfirmCancel = V3OperationCoverDismissalPolicy.mustConfirmBackendStop(
                    isRunning: isRunning, hasSession: attempt.sessionID != nil,
                    sessionIsTerminal: wasTerminal,
                    hasUncertainSession: uncertainSessionID != nil,
                    transitionInFlight: attempt.transitionInFlight)
                let oldSession = uncertainSessionID ?? (mustConfirmCancel ? attempt.supersede() : nil)
                oldTask?.cancel()
                Task { @MainActor in
                    var cancellationConfirmed = !mustConfirmCancel
                    var confirmedOutcome = terminalOutcome
                    if let oldSession {
                        do {
                            let reply = try await V3ServiceBridge.shared.request(operation: "opCancel", target: oldSession)
                            let terminalState = reply["state"] as? String
                            cancellationConfirmed = V3OperationTerminalAcceptancePolicy.isSettledTerminal(
                                state: terminalState,
                                backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                                stopConfirmed: V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                                outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]))
                            if cancellationConfirmed { confirmedOutcome = terminalState ?? "cancelled" }
                        } catch { cancellationConfirmed = false }
                    }
                    await oldTask?.value
                    if request.operation == "installSharedIPA", cancellationConfirmed {
                        status.installTerminal(attemptID: request.installAttemptID,
                            operationID: request.id, outcome: confirmedOutcome)
                        let token = request.installAttemptID.flatMap {
                            status.resetInstallUI(attemptID: $0, outcome: "unexpected_cover_dismissal")
                        }
                        if let token { stagedIPACleaned = await status.cleanupStagedIPA(token,
                            allowLocalFallback: true) }
                    } else if request.operation == "installSharedIPA", mustConfirmCancel, !cancellationConfirmed {
                        status.error = "The operation was not confirmed as stopped, so its staged IPA was kept safely. Reconnect before cleanup." + "\nError ID: SS-CMD-D019"
                    }
                    status.reload()
                }
            }
        }
    }
    private var statusText: String {
        switch state {
        case "cancelling": return "Cancelling..."
        case "reconciling": return "Checking result..."
        case "working" where isTransitioning: return "Waiting for previous attempt..."
        case "completed": return "Completed"
        case "awaitingPrompt": return "Needs your input"
        case "promptExpired": return "Verification expired"
        case "timedOut": return "Sign-in timed out"
        case "failed": return "Failed"
        case "cancelled": return "Cancelled"
        case "requiresSource": return "Source required"
        default: return isTransitioning ? "Waiting for previous attempt..." : operationPhase.label
        }
    }
    private func start() {
        guard startedGeneration == nil, !attempt.transitionInFlight else { return }
        if let recoverySessionID = request.recoverySessionID {
            guard let generation = attempt.attach(sessionID: recoverySessionID) else {
                message = "The saved operation identity is invalid. Check the device before continuing." + "\nError ID: SS-CMD-D020"
                needsDeviceConfirmation = true
                uncertainSessionID = recoverySessionID
                return
            }
            startAttempt(generation: generation)
        } else {
            startAttempt(generation: attempt.begin())
        }
    }
    private func startAttempt(generation: UUID) {
        guard attempt.generation == generation, startedGeneration != generation else { return }
        startedGeneration = generation
        userRequestedCancellation = false
        progress = 0
        hasProgress = false
        operationPhase = .working
        task = Task { await run(generation: generation) }
    }
    private func run(generation: UUID) async {
        var backendSessionStarted = false
        do {
            let recovering = request.recoverySessionID != nil
            let reply: [String: Any]
            if recovering {
                reply = try await V3ServiceBridge.shared.request(operation: "opPoll",
                    target: generation.uuidString)
            } else {
                status.installBackendStartRequested(attemptID: request.installAttemptID,
                    operationID: request.id, sessionID: generation.uuidString)
                let prepared = try await V3ServiceBridge.shared.request(operation: "opRecoveryPrepare",
                    payload: ["kind": request.operation, "target": request.target,
                              "session": generation.uuidString])
                guard prepared["session"] as? String == generation.uuidString,
                      prepared["phase"] as? String == "prepared" else {
                    throw CombinedFailure(operation: request.operation, stage: .command,
                        code: .staleResult, id: generation.uuidString, retryable: false)
                }
                reply = try await V3ServiceBridge.shared.request(operation: "opStart",
                    payload: ["kind": request.operation, "target": request.target,
                              "session": generation.uuidString])
            }
            if !recovering, reply["failedToStart"] as? Bool == true {
                handleStartFailure(reply, generation: generation)
                return
            }
            guard let id = reply["session"] as? String else {
                if reply["state"] as? String == "failed" {
                    handleStartFailure(reply, generation: generation)
                } else {
                    let failure = CombinedFailure(operation: request.operation, stage: .command,
                        code: .invalidResponse, id: generation.uuidString, retryable: false)
                    failureContext.recordStartFailure(failure)
                    presentCurrentFailure()
                }
                return
            }
            guard id == generation.uuidString else {
                if !recovering { _ = try? await V3ServiceBridge.shared.request(operation: "opCancel", target: id) }
                let failure = CombinedFailure(operation: request.operation, stage: .command,
                    code: .staleResult, id: generation.uuidString, retryable: false)
                failureContext.recordStartFailure(failure)
                presentCurrentFailure()
                return
            }
            guard attempt.bind(sessionID: id, generation: generation) else {
                _ = try? await V3ServiceBridge.shared.request(operation: "opCancel", target: id)
                return
            }
            backendSessionStarted = true
            status.installBackendStarted(attemptID: request.installAttemptID,
                operationID: request.id, sessionID: id)
            failureContext.operationStarted()
            retryBlocked = false
            try await pollLoop(id: id, generation: generation)
        } catch {
            guard attempt.generation == generation, !attempt.isTerminal else { return }
            if error is CancellationError, attempt.transitionInFlight { return }
            let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
                operation: request.operation,
                stage: backendSessionStarted ? .xpcConnection : .command,
                id: generation.uuidString)
            if backendSessionStarted {
                failureContext.recordPipelineFailure(failure)
            } else {
                failureContext.recordStartFailure(failure)
            }
            presentCurrentFailure()
            if V3ServiceBridge.shared.hasUncertainOperationSession(generation.uuidString) ||
                request.recoverySessionID == generation.uuidString {
                needsDeviceConfirmation = true
                uncertainSessionID = generation.uuidString
                retryBlocked = true
                message = "SideStore could not confirm the current operation result." + "\nError ID: SS-CMD-D021"
                whatToDo = "Check the device before starting another mutation. If no operation is running, use Reconcile After Checking Device."
                technicalDetails += " backend_settled=no outcome=unknown"
            }
        }
    }

    private func handleStartFailure(_ reply: [String: Any], generation: UUID) {
        guard attempt.generation == generation, !attempt.isTerminal else { return }
        // Missing-source detection happens during driver preparation. Although
        // no installation started, this is a recoverable terminal session, not
        // a generic start error. Admit only its correlated, settled envelope.
        if V3SourceRecoveryPolicy.isSettledStartReply(reply, sessionID: generation.uuidString),
           attempt.bind(sessionID: generation.uuidString, generation: generation) {
            apply(reply, generation: generation, sessionID: generation.uuidString)
            return
        }
        let failure = (reply["failure"] as? [String: Any]).flatMap {
            CombinedFailure.decode($0, expectedID: generation.uuidString)
        } ?? CombinedFailure(operation: request.operation,
            stage: CombinedFailure.Stage(rawValue: reply["stage"] as? String ?? "") ?? .command,
            code: CombinedFailure.Code(rawValue: reply["code"] as? String ?? "") ?? .failed,
            id: generation.uuidString, retryable: reply["retryable"] as? Bool)
        failureContext.recordStartFailure(failure)
        presentCurrentFailure()
    }

    private func presentCurrentFailure() {
        _ = attempt.acceptStartFailure(generation: attempt.generation)
        status.installTerminal(attemptID: request.installAttemptID,
            operationID: request.id, outcome: "failed")
        state = "failed"
        message = failureContext.whatHappened
        whatToDo = failureContext.whatToDo
        technicalDetails = failureContext.technicalDetails
        recoveryDestination = failureContext.currentFailure?.recoveryDestination
        recordRefresh("failed", message)
    }
    private func confirmUncertainOperationAfterDeviceCheck() {
        guard let sessionID = uncertainSessionID else { return }
        Task {
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "opRecoveryReconcile",
                    target: sessionID, payload: ["userConfirmed": true])
                guard reply["session"] as? String == sessionID,
                      V3ServiceBridge.strictBool(reply["reconciled"]) == true else {
                    throw CombinedFailure(operation: request.operation, stage: .command,
                        code: .staleResult, id: sessionID, retryable: false)
                }
            } catch {
                message = "SideStore could not clear the recovery hold. Keep waiting and check the device again." + "\nError ID: SS-CMD-D022"
                whatToDo = "No new operation was started. Reconnect before trying reconciliation again."
                return
            }
            V3ServiceBridge.shared.retireReconciledOperationService(sessionID: sessionID)
            uncertainSessionID = nil
            needsDeviceConfirmation = false
            terminalBackendSettled = true
            retryBlocked = true
            deviceCheckConfirmedForCompletion = false
            finishReconciliationAfterRetirement()
            status.reload()
            message = "The previous operation result remains unknown." + "\nError ID: SS-CMD-D023"
            whatToDo = "Reload app status, then verify the installed app before starting another operation."
            technicalDetails += " service_retired_after_user_confirmation=yes outcome=unknown"
        }
    }
    private func finishReconciliationAfterRetirement() {
        guard state == "reconciling", let sessionID = attempt.sessionID else { return }
        _ = attempt.accept(state: "failed", generation: attempt.generation, sessionID: sessionID)
        state = "failed"
        status.installTerminal(attemptID: request.installAttemptID,
            operationID: request.id, outcome: "unknown")
    }
    private func pollLoop(id: String, generation: UUID) async throws {
        var settlementRetryDelay: TimeInterval?
        while !Task.isCancelled {
            let interval = settlementRetryDelay ?? V3OperationCompletionPolicy.pollInterval(
                state: state, backendSettled: terminalBackendSettled,
                outcomeUnknown: needsDeviceConfirmation)
            settlementRetryDelay = nil
            try await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            try Task.checkCancellation()
            guard attempt.owns(generation: generation, sessionID: id),
                  !attempt.isTerminal || completionAwaitingSettlement else { return }
            let reply: [String: Any]
            do {
                reply = try await V3ServiceBridge.shared.request(operation: "opPoll", target: id)
            } catch {
                guard attempt.owns(generation: generation, sessionID: id),
                      V3OperationCompletionPolicy.shouldRetrySettlementPollFailure(
                        state: state, backendSettled: terminalBackendSettled,
                        outcomeUnknown: needsDeviceConfirmation,
                        cancellationRequested: userRequestedCancellation),
                      !Task.isCancelled else { throw error }
                settlementRetryDelay = V3OperationCompletionPolicy.nextSettlementPollRetryDelay(
                    current: interval)
                NSLog("[V3_OPERATION_UI] settlement_poll_retry session=%@ delay=%.0f",
                      id, settlementRetryDelay ?? 0)
                continue
            }
            settlementRetryDelay = nil
            guard let current = reply["state"] as? String else {
                throw NSError(domain: "V3Operation", code: 2,
                              userInfo: [NSLocalizedDescriptionKey: "The service returned an unreadable operation state."])
            }
            guard reply["session"] as? String == id else { return }
            apply(reply, generation: generation, sessionID: id)
            if V3OperationCompletionPolicy.shouldContinuePolling(state: current,
                backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])) {
                continue
            }
            guard current == "working" || current == "awaitingPrompt" || current == "cancelling" ||
                    current == "reconciling" else { return }
        }
    }
    private func apply(_ reply: [String: Any], generation: UUID, sessionID: String) {
        guard let nextState = reply["state"] as? String else { return }
        guard V3OperationCancellationReplyPolicy.shouldApplyPollState(
            userRequestedCancellation: userRequestedCancellation, nextState: nextState) else { return }
        if nextState == "completed", state == "completed",
           attempt.owns(generation: generation, sessionID: sessionID) {
            let wasAwaitingSettlement = completionAwaitingSettlement
            applyCompletionSettlement(reply, sessionID: sessionID)
            if wasAwaitingSettlement && !completionAwaitingSettlement { status.reload() }
            return
        }
        let replyBackendSettled = V3ServiceBridge.strictBool(reply["backendSettled"])
        let replyOutcomeUnknown = V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])
        let resolvesProvisionalUnknown = attempt.ownsProvisionalResolution(
            generation: generation, sessionID: sessionID, currentState: state,
            currentBackendSettled: terminalBackendSettled,
            currentOutcomeUnknown: needsDeviceConfirmation, nextState: nextState,
            nextBackendSettled: replyBackendSettled, nextOutcomeUnknown: replyOutcomeUnknown,
            nextOperation: reply["operation"] as? String,
            verifiedDeleteCompletion: V3OperationReplyFieldPolicy.strictBoolean(
                reply["verifiedDeleteCompletion"]) == true)
        guard resolvesProvisionalUnknown ||
              attempt.accept(state: nextState, generation: generation, sessionID: sessionID) else { return }
        state = nextState
        if !["working", "awaitingPrompt", "cancelling", "reconciling"].contains(nextState) {
            status.installTerminal(attemptID: request.installAttemptID,
                operationID: request.id, outcome: nextState)
        }
        if let rawProgress = reply["progress"] as? Double {
            progress = V3NormalizedProgress.clamp(rawProgress)
            hasProgress = true
        }
        if let rawPhase = reply["phase"] as? String {
            operationPhase = V3OperationPhase(rawValue: rawPhase) ?? .working
        }
        let oldPromptID = prompt?["id"] as? String
        let nextPrompt = reply["prompt"] as? [String: Any]
        prompt = nextPrompt
        if oldPromptID != (nextPrompt?["id"] as? String) { promptSubmitting = false }
        if oldPromptID != (nextPrompt?["id"] as? String) { promptResponseBlocked = false }
        switch state {
        case "completed":
            progress = 1
            hasProgress = true
            // Terminal success stays visible until the user presses Done.
            // Auto-dismissing here made successful fast operations look like
            // nothing happened.
            applyCompletionSettlement(reply, sessionID: sessionID)
            recordRefresh("completed", "The operation completed. Reload the app list to confirm the result.")
            status.reload()
        case "cancelled":
            // Keep cancellation visible and distinguish the user's Cancel
            // action from a backend cancellation that arrived independently.
            terminalBackendSettled = V3ServiceBridge.strictBool(reply["backendSettled"])
            let outcomeUnknown = V3OperationCancellationResolutionPolicy.requiresReconciliation(
                backendSettled: terminalBackendSettled,
                outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]))
            needsDeviceConfirmation = outcomeUnknown
            uncertainSessionID = outcomeUnknown ? sessionID : nil
            retryBlocked = outcomeUnknown
            recoveryDestination = nil
            if outcomeUnknown {
                message = request.operation == "delete"
                    ? "SideStore has not confirmed that the app was removed or that deletion stopped." + "\nError ID: SS-CMD-D050"
                    : "SideStore has not confirmed that the device operation stopped." + "\nError ID: SS-CMD-D051"
                whatToDo = "Keep this screen open while SideStore reconciles the result. Do not start another operation yet."
                technicalDetails = "backend_settled=no outcome=unknown"
            } else {
                let cancellation = V3OperationCancellationPresentationPolicy.resolve(
                    userRequested: userRequestedCancellation)
                message = cancellation.message
                whatToDo = cancellation.whatToDo
                technicalDetails = ""
                recoveryDestination = nil
                deviceCheckConfirmedForCompletion = false
            }
        case "waitingForAuthentication":
            status.signInPresented = true
            message = "Sign in first, then run this action again." + "\nError ID: SS-AUTH-D024"
            whatToDo = "Open Account & Signing, complete sign-in, then start a new operation."
            recoveryDestination = "signIn"
            retryBlocked = true
        case "requiresSource":
            let sourceID = reply["sourceID"] as? String ?? ""
            if let url = V3SourceRecoveryPolicy.target(sourceID: sourceID,
                                                       sourceURL: reply["sourceURL"] as? String) {
                sourceOffer = ["id": sourceID, "url": url,
                               "name": reply["sourceName"] as? String ?? "Unknown source"]
            } else {
                // Older backends provide only the lossy identifier. Do not
                // invent an HTTPS URL or dispatch a broken source mutation.
                sourceOffer = nil
                retryBlocked = true
                message = "The app's source must be added before installation." + "\nError ID: SS-SOURCE-D025"
                whatToDo = "Open Sources and add the original source URL, then try installing again."
                recoveryDestination = "sources"
            }
            prompt = nil
        case "reconciling":
            let failure = (reply["failure"] as? [String: Any]).flatMap {
                CombinedFailure.decode($0, expectedID: sessionID)
            }
            if let failure { failureContext.recordPipelineFailure(failure) }
            terminalBackendSettled = V3ServiceBridge.strictBool(reply["backendSettled"])
            needsDeviceConfirmation = true
            uncertainSessionID = sessionID
            retryBlocked = true
            message = "SideStore is still checking whether the app was removed."
            whatToDo = "Keep this screen open while SideStore waits for the delete callback. Do not retry until the result is confirmed."
            technicalDetails = (failure?.technicalDetails ??
                "schema=1 operation=delete stage=command code=timedOut correlation=\(sessionID) underlying_domain=redacted underlying_code=redacted retryable=unknown") +
                " backend_settled=no outcome=unknown"
            recoveryDestination = nil
        case "failed":
            let failure = (reply["failure"] as? [String: Any]).flatMap {
                CombinedFailure.decode($0, expectedID: sessionID)
            } ?? CombinedFailure(operation: request.operation,
                stage: CombinedFailure.Stage(rawValue: reply["stage"] as? String ?? "") ?? .command,
                code: CombinedFailure.Code(rawValue: reply["code"] as? String ?? "") ?? .failed,
                id: sessionID, retryable: reply["retryable"] as? Bool)
            failureContext.recordPipelineFailure(failure)
            let backendSettled = V3ServiceBridge.strictBool(reply["backendSettled"])
            terminalBackendSettled = backendSettled
            let outcomeUnknown = V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]) ||
                backendSettled != true
            needsDeviceConfirmation = outcomeUnknown
            uncertainSessionID = outcomeUnknown ? sessionID : nil
            retryBlocked = !V3OperationRetrySafetyPolicy.canRetry(
                backendSettled: backendSettled,
                outcomeUnknown: outcomeUnknown)
            message = failureContext.whatHappened
            whatToDo = outcomeUnknown
                ? "The backend has not confirmed that the operation stopped. Do not retry yet; wait for status reconciliation and reload before starting another mutation."
                : failureContext.whatToDo
            technicalDetails = failureContext.technicalDetails +
                (outcomeUnknown ? " backend_settled=no outcome=unknown" : "")
            recoveryDestination = failureContext.currentFailure?.recoveryDestination
            if !outcomeUnknown { recordRefresh("failed", message) }
        default: break
        }
    }
    private func applyCompletionSettlement(_ reply: [String: Any], sessionID: String) {
        terminalBackendSettled = V3ServiceBridge.strictBool(reply["backendSettled"])
        let outcomeUnknown = V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])
        switch V3OperationCompletionPolicy.disposition(state: "completed",
            backendSettled: terminalBackendSettled, outcomeUnknown: outcomeUnknown) {
        case .completedAwaitingBackendSettlement, .outcomeUnknownAwaitingBackendSettlement:
            needsDeviceConfirmation = true
            uncertainSessionID = sessionID
            retryBlocked = true
            message = request.operation == "delete"
                ? "The app was removed from the device, but SideStore is still waiting for the delete callback to settle."
                : request.title + " completed, but SideStore has not confirmed that its backend session settled."
            whatToDo = "Keep this screen open while SideStore finishes cleanup. If it remains here, check the device, then use Reconcile After Checking Device before starting another mutation."
            technicalDetails = "backend_settled=no outcome=verified_completion_pending_callback"
            recoveryDestination = nil
        case .completed:
            needsDeviceConfirmation = false
            uncertainSessionID = nil
            deviceCheckConfirmedForCompletion = false
            retryBlocked = false
            message = request.title + " completed successfully."
            whatToDo = "Reload app status to confirm the installed app and signing state."
            technicalDetails = ""
            recoveryDestination = nil
            failureContext.reset()
        case .notCompleted:
            break
        }
    }
    private func answerPrompt(id: String, answer: [String: String]) async {
        // The operation store owns this transition, and it must claim it before
        // the request, not only once the reply says the response is pending. The
        // view used to set this flag, so between the tap and the reply a second
        // tap could dispatch a second opAnswer for one prompt.
        guard !promptSubmitting, !id.isEmpty, prompt?["id"] as? String == id,
              let session = attempt.sessionID else { return }
        promptSubmitting = true
        let generation = attempt.generation
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "opAnswer", target: session,
                payload: ["prompt": id, "answer": answer])
            guard attempt.matches(generation: generation, sessionID: session),
                  reply["session"] as? String == session else {
                promptSubmitting = false
                return
            }
            if V3ServiceBridge.strictBool(reply["responsePending"]) == true {
                // The service has the answer but no authoritative transition yet.
                // Hold the claim; a stale prompt or a superseded attempt must not
                // leave it held forever.
                guard prompt?["id"] as? String == id,
                      attempt.matches(generation: generation, sessionID: session) else {
                    promptSubmitting = false
                    return
                }
                message = "Your response is being processed..."
                return
            }
            promptSubmitting = false
            apply(reply, generation: generation, sessionID: session)
        } catch {
            guard attempt.matches(generation: generation, sessionID: session) else { return }
            promptSubmitting = false
            let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
                operation: request.operation, stage: .command, id: session)
            let promptFailure = V3OperationPromptFailureDetails(failure)
            message = promptFailure.failure.whatHappened
            whatToDo = promptFailure.failure.recommendedAction
            technicalDetails = promptFailure.failure.technical
            recoveryDestination = promptFailure.failure.recoveryDestination
            promptResponseBlocked = promptFailure.blocksResubmission
        }
    }
    private func addSourceAndRetry(id: String) async {
        guard sourceOffer?["id"] == id,
              let url = V3SourceRecoveryPolicy.target(sourceID: id, sourceURL: sourceOffer?["url"]),
              let session = attempt.sessionID, attempt.beginTransition() else { return }
        let generation = attempt.generation
        do {
            let preview = try await V3ServiceBridge.shared.request(operation: "sourcePreview", target: url)
            guard !isDismissing, attempt.owns(generation: generation, sessionID: session) else { return }
            guard V3SourceRecoveryPolicy.matchesPreview(preview, sourceID: id) else {
                throw V3SourceAddPersistencePolicy.unverifiedPersistenceFailure(correlationID: session)
            }
            let added = try await V3ServiceBridge.shared.request(operation: "sourceAddConfirmed", target: url)
            guard !isDismissing, attempt.owns(generation: generation, sessionID: session) else { return }
            guard V3SourceRecoveryPolicy.verifiedAddition(added, sourceID: id) else {
                throw V3SourceAddPersistencePolicy.unverifiedPersistenceFailure(correlationID: session)
            }
            _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                added, operation: "sourceAddConfirmed")
            guard !isDismissing, attempt.owns(generation: generation, sessionID: session) else { return }
            sourceAddFailure = nil
            sourceAddRetryBlocked = false
            retryBlocked = false
            status.reload()
            attempt.endTransition()
            retry()
        } catch {
            guard !isDismissing, attempt.owns(generation: generation, sessionID: session) else { return }
            attempt.endTransition()
            let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
                operation: "source", stage: .source,
                id: attempt.sessionID ?? UUID().uuidString)
            let details = V3OperationFailureDetails(failure)
            sourceAddFailure = details
            sourceAddRetryBlocked = details.retryDisposition == .blocked ||
                details.retryDisposition == .prerequisite
            retryBlocked = true
            message = details.whatHappened
            whatToDo = details.recommendedAction
            technicalDetails = details.technical
            recoveryDestination = details.recoveryDestination
        }
    }
    private func retry() {
        guard retryAllowed, attempt.beginTransition() else { return }
        let oldTask = task
        let oldSession = attempt.supersede()
        let transitionGeneration = attempt.generation
        uncertainSessionID = oldSession
        startedGeneration = nil
        promptSubmitting = false
        prompt = nil
        sourceOffer = nil
        progress = 0
        message = "Waiting for the previous attempt to stop..."
        whatToDo = "The new attempt will start after the service confirms that the prior session stopped."
        technicalDetails = failureContext.technicalDetails
        recoveryDestination = nil
        retryBlocked = false
        promptResponseBlocked = false
        sourceAddFailure = nil
        sourceAddRetryBlocked = false
        state = "working"
        Task { @MainActor in
            oldTask?.cancel()
            do {
                if let oldSession {
                    let reply = try await V3ServiceBridge.shared.request(operation: "opCancel", target: oldSession)
                    let disposition = V3OperationRetrySafetyPolicy.disposition(
                        state: reply["state"] as? String,
                        backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"])
                            ?? V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                        outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]))
                    if disposition == .alreadyCompleted {
                        await oldTask?.value
                        guard attempt.transitionInFlight, attempt.generation == transitionGeneration else { return }
                        attempt.endTransition()
                        uncertainSessionID = nil
                        needsDeviceConfirmation = false
                        retryBlocked = true
                        state = "completed"
                        progress = 1
                        hasProgress = true
                        message = request.title + " completed successfully."
                        whatToDo = "Reload app status to confirm the installed app and signing state."
                        technicalDetails = ""
                        failureContext.reset()
                        status.installTerminal(attemptID: request.installAttemptID,
                            operationID: request.id, outcome: "completed")
                        status.reload()
                        recordRefresh("completed", message)
                        V3ServiceBridge.shared.forgetSettledOperationSession(oldSession)
                        return
                    }
                    if disposition != .retry {
                        await oldTask?.value
                        guard attempt.transitionInFlight, attempt.generation == transitionGeneration else { return }
                        let failure = CombinedFailure(operation: request.operation, stage: .command,
                            code: .timedOut, id: transitionGeneration.uuidString)
                        failureContext.recordStartFailure(failure)
                        attempt.endTransition()
                        retryBlocked = true
                        needsDeviceConfirmation = true
                        uncertainSessionID = oldSession
                        state = "failed"
                        message = failureContext.whatHappened
                        whatToDo = "The previous operation has not settled. Check the device; do not start another mutation until the result is reconciled."
                        technicalDetails = failureContext.technicalDetails + " backend_settled=no outcome=unknown"
                        return
                    }
                    V3ServiceBridge.shared.forgetSettledOperationSession(oldSession)
                }
                await oldTask?.value
                guard attempt.transitionInFlight, attempt.generation == transitionGeneration else { return }
                uncertainSessionID = nil
                status.prepareInstallRetry(attemptID: request.installAttemptID)
                failureContext.beginRetry()
                message = ""
                let generation = attempt.begin()
                attempt.endTransition()
                startAttempt(generation: generation)
            } catch {
                await oldTask?.value
                guard attempt.generation == transitionGeneration else { return }
                attempt.endTransition()
                let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
                    operation: request.operation, stage: .xpcConnection,
                    id: transitionGeneration.uuidString)
                failureContext.recordStartFailure(failure)
                retryBlocked = true
                needsDeviceConfirmation = oldSession != nil
                if oldSession != nil { uncertainSessionID = oldSession }
                presentCurrentFailure()
            }
        }
    }
    private func cancelAttempt() {
        guard isRunning, state != "cancelling", attempt.beginTransition() else { return }
        let keepDeletePoller = V3DeleteCancellationPolicy.keepsHostPollMonitor(
            operation: request.operation)
        userRequestedCancellation = true
        state = "cancelling"
        message = ""
        let oldTask = task
        let transitionGeneration = attempt.generation
        let oldSession = attempt.sessionID ?? transitionGeneration.uuidString
        uncertainSessionID = oldSession
        startedGeneration = nil
        prompt = nil
        Task { @MainActor in
            if !keepDeletePoller { oldTask?.cancel() }
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "opCancel", target: oldSession)
                if !keepDeletePoller { await oldTask?.value }
                guard attempt.generation == transitionGeneration else { return }
                if attempt.sessionID == nil {
                    _ = attempt.bind(sessionID: oldSession, generation: transitionGeneration)
                }
                let settledCancellationAcknowledgement = V3OperationCancellationOutcomePolicy.terminalState(
                    expectedSessionID: oldSession,
                    replySessionID: reply["session"] as? String,
                    state: reply["state"] as? String,
                    backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                    stopConfirmed: V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                    outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]))
                let cancellationReplyIsCorrelated = V3OperationCancellationOutcomePolicy.isCorrelated(
                    expectedSessionID: oldSession, replySessionID: reply["session"] as? String)
                if (keepDeletePoller && settledCancellationAcknowledgement != nil) ||
                   (!keepDeletePoller && cancellationReplyIsCorrelated) {
                    apply(reply, generation: transitionGeneration, sessionID: oldSession)
                    if let settledCancellationAcknowledgement {
                        if V3OperationCancellationOutcomePolicy.shouldClearSessionHandle(
                            currentSessionID: uncertainSessionID, expectedSessionID: oldSession,
                            replySessionID: reply["session"] as? String,
                            state: reply["state"] as? String,
                            backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                            stopConfirmed: V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                            outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"])) {
                            // A delayed unsettled acknowledgment cannot discard
                            // the handle established by a newer delete poll.
                            state = settledCancellationAcknowledgement
                            terminalBackendSettled = true
                            needsDeviceConfirmation = false
                            uncertainSessionID = nil
                        }
                    }
                } else if !keepDeletePoller {
                    state = "failed"
                    needsDeviceConfirmation = true
                    uncertainSessionID = oldSession
                    retryBlocked = true
                    message = "SideStore could not confirm that this operation stopped." + "\nError ID: SS-CMD-D026"
                    whatToDo = "Reload operation status before starting another mutation."
                    technicalDetails = "operation_session_response_mismatch=yes backend_settled=no"
                }
                if keepDeletePoller && state == "cancelling" {
                    message = "Cancellation was requested. SideStore is waiting for the delete result."
                    whatToDo = "Keep this screen open. Do not start another operation until the delete result is confirmed."
                    retryBlocked = true
                } else if ["working", "cancelling"].contains(state) {
                    state = "reconciling"
                    terminalBackendSettled = false
                    needsDeviceConfirmation = true
                    uncertainSessionID = oldSession
                    retryBlocked = true
                    message = "SideStore has not confirmed that the operation stopped." + "\nError ID: SS-CMD-D027"
                    whatToDo = "Reload operation status before starting another mutation."
                    technicalDetails = "backend_settled=no outcome=unknown"
                }
            } catch {
                if !keepDeletePoller { await oldTask?.value }
                guard attempt.generation == transitionGeneration else { return }
                state = "failed"
                let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(error,
                    operation: request.operation, stage: .xpcConnection,
                    id: transitionGeneration.uuidString)
                failureContext.recordPipelineFailure(failure)
                retryBlocked = true
                needsDeviceConfirmation = true
                uncertainSessionID = oldSession
                message = "SideStore could not confirm that the operation stopped. It may still be running." + "\nError ID: SS-XPC-D028"
                whatToDo = "Reconnect and reload operation status before trying another mutation."
                technicalDetails = failure.technicalDetails
            }
            attempt.endTransition()
        }
    }
    private func acknowledgeAndDismiss() {
        guard V3OperationCompletionPolicy.mayDismiss(state: state,
            backendSettled: terminalBackendSettled,
            deviceCheckConfirmed: deviceCheckConfirmedForCompletion) else {
            confirmUncertainRetirement = true
            return
        }
        guard attempt.beginTransition() else { return }
        isDismissing = true
        let oldTask = task
        let oldSession = attempt.supersede()
        oldTask?.cancel()
        Task { @MainActor in
            await oldTask?.value
            let cancellationTarget = uncertainSessionID ?? oldSession
            var confirmedOutcome = "cancelled"
            if let cancellationTarget {
                do {
                    let reply = try await V3ServiceBridge.shared.request(operation: "opCancel", target: cancellationTarget)
                    let backendSettled = V3OperationTerminalAcceptancePolicy.isSettledTerminal(
                        state: reply["state"] as? String,
                        backendSettled: V3ServiceBridge.strictBool(reply["backendSettled"]),
                        stopConfirmed: V3ServiceBridge.strictBool(reply["stopConfirmed"]),
                        outcomeUnknown: V3OperationReplyFieldPolicy.outcomeUnknown(reply["outcomeUnknown"]))
                    guard backendSettled else {
                        isDismissing = false
                        attempt.endTransition()
                        needsDeviceConfirmation = true
                        uncertainSessionID = cancellationTarget
                        retryBlocked = true
                        message = "SideStore has not confirmed that the operation stopped." + "\nError ID: SS-CMD-D027"
                        whatToDo = "Check the device. If no operation is still running, use Reconcile After Checking Device."
                        return
                    }
                    uncertainSessionID = nil
                    if ["completed", "failed", "cancelled", "requiresSource", "waitingForAuthentication"]
                        .contains(reply["state"] as? String ?? "") {
                        V3ServiceBridge.shared.forgetSettledOperationSession(cancellationTarget)
                    }
                    if reply["state"] as? String == "completed" {
                        confirmedOutcome = "completed"
                        state = "completed"
                        progress = 1
                        hasProgress = true
                        message = request.title + " completed successfully."
                        whatToDo = "Reload app status to confirm the installed app and signing state."
                        recordRefresh("completed", message)
                    } else if reply["state"] as? String == "failed" {
                        confirmedOutcome = "failed"
                    }
                } catch {
                    if uncertainSessionID != nil {
                        isDismissing = false
                        attempt.endTransition()
                        retryBlocked = true
                        message = "SideStore could not confirm that the previous operation stopped. The staged IPA was kept safely." + "\nError ID: SS-CMD-D029"
                        whatToDo = "Reconnect before retrying or cleaning up the selected IPA."
                        return
                    }
                }
            }
            status.installTerminal(attemptID: request.installAttemptID,
                operationID: request.id, outcome: confirmedOutcome)
            if request.operation == "installSharedIPA" {
                let token = request.installAttemptID.flatMap {
                    status.resetInstallUI(attemptID: $0, outcome: "acknowledged",
                        preserveRecoveryDestination: status.operationRecoveryDestination != nil)
                }
                if let token, !stagedIPACleaned {
                    stagedIPACleaned = await status.cleanupStagedIPA(token, allowLocalFallback: true)
                }
            }
            status.reload()
            dismiss()
        }
    }
    private func openRecoveryDestination(_ destination: String) {
        status.operationRecoveryDestination = destination
        acknowledgeAndDismiss()
    }
    private func recordRefresh(_ result: String, _ detail: String) {
        guard request.operation == "refreshApp" else { return }
        NotificationCenter.default.post(name: Notification.Name("V3TargetedRefreshResult"), object: nil,
                                        userInfo: ["result": result, "detail": detail])
    }
}

struct V3PromptSection: View {
    @Environment(\.locale) private var displayLocale
    let prompt: [String: Any]
    @Binding var isSubmitting: Bool
    var isSubmissionBlocked = false
    var previousFailureMessage = ""
    var previousFailureDetails = ""
    var supplementalContent: AnyView? = nil
    var cancellationTitle = "Cancel Sign In"
    var cancellationDisabled = false
    var onCancel: (() -> Void)? = nil
    let onAnswer: ([String: String]) -> Void
    @State private var fields: [String: String] = [:]
    @State private var selected: Set<String> = []
    @State private var copiedDiagnostic: String?
    @State private var repairURL: URL?
    private var kind: String { prompt["kind"] as? String ?? "" }
    private var title: String { prompt["title"] as? String ?? "Input Needed" }
    private var message: String { prompt["message"] as? String ?? "" }
    private var fieldDefs: [[String: String]] {
        (prompt["fields"] as? [[String: Any]] ?? []).compactMap { row in
            guard let key = row["key"] as? String else { return nil }
            return ["key": key, "label": key == "appendTeamID" ? "Append Team ID" : (row["label"] as? String ?? key),
                    "secure": row["secure"] as? String ?? "false",
                    "value": row["value"] as? String ?? ""]
        }
    }
    private var options: [[String: String]] {
        (prompt["options"] as? [[String: Any]] ?? []).compactMap { row in
            guard let id = row["id"] as? String else { return nil }
            return ["id": id, "label": row["label"] as? String ?? id]
        }
    }
    private var isMulti: Bool { kind == "extensions" || kind == "revocation" }
    private var deliveryOptions: [[String: String]] {
        options.filter { ["trustedDevice", "sms", "voice"].contains($0["id"] ?? "") }
    }
    private var phoneOptions: [[String: String]] {
        options.filter { ($0["id"] ?? "").hasPrefix("phone:") }
    }
    private var twoFactorStep: V3TwoFactorStep {
        let raw = fieldDefs.first(where: { $0["key"] == "step" })?["value"] ?? "chooseDeliveryMethod"
        return V3TwoFactorStep(rawValue: raw) ?? .chooseDeliveryMethod
    }
    var body: some View {
        Section(V3Localization.prose(title, language: .resolve(displayLocale.identifier))) {
            if !previousFailureMessage.isEmpty {
                V3LocalizedText(previousFailureMessage)
                    .font(.footnote).foregroundColor(.orange)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("signin.prompt.previous-error")
                if !previousFailureDetails.isEmpty {
                    DisclosureGroup("Technical details") {
                        Text(previousFailureDetails)
                            .environment(\.layoutDirection, .leftToRight)
                            .font(.caption).foregroundColor(.secondary)
                            .textSelection(.enabled)
                            .accessibilityIdentifier("signin.prompt.previous-error-body")
                    }
                    .accessibilityIdentifier("signin.prompt.previous-error-details")
                    Button(LocalizedStringKey(copiedDiagnostic == previousFailureDetails ? "Copied" : "Copy Details")) {
                        copyDiagnostic(previousFailureDetails)
                    }
                        .font(.caption)
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("signin.prompt.copy-details")
                        .accessibilityHint("Copy diagnostic details for this sign-in attempt.")
                }
            }
            if !message.isEmpty {
                V3LocalizedText(message)
                    .font(.footnote)
                    .foregroundColor(.secondary)
            }
            if let supplementalContent { supplementalContent }
            if kind == "twoFactor" {
                switch twoFactorStep {
                case .chooseDeliveryMethod:
                    Text("Choose how Apple sends your verification code.")
                        .font(.subheadline.weight(.semibold))
                    ForEach(deliveryOptions, id: \.self) { option in twoFactorOption(option) }
                    twoFactorCancelButton()
                case .choosePhoneNumber:
                    Text("Choose the phone number for this request.")
                        .font(.subheadline.weight(.semibold))
                    ForEach(phoneOptions, id: \.self) { option in twoFactorOption(option) }
                    Button("Change Verification Method", systemImage: "arrow.uturn.backward") {
                        var answer = fields
                        answer["action"] = "changeMethod"
                        answer["choice"] = "changeMethod"
                        respond(answer)
                    }
                    .disabled(isSubmitting)
                    twoFactorCancelButton()
                case .enterVerificationCode:
                    TextField("Verification code", text: binding("code"))
                        .keyboardType(.numberPad)
                        .textFieldStyle(.roundedBorder)
                        .textContentType(.oneTimeCode)
                    Button("Verify Code") {
                        var answer = fields
                        answer["choice"] = "code"
                        answer["action"] = "code"
                        respond(answer)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled((fields["code"] ?? "").count != 6 || isSubmitting || isSubmissionBlocked)
                    ForEach(options.filter { $0["id"] == "resend" }, id: \.self) { option in
                        twoFactorOption(option)
                    }
                    Button("Change Verification Method", systemImage: "arrow.uturn.backward") {
                        var answer = fields
                        answer["action"] = "changeMethod"
                        answer["choice"] = "changeMethod"
                        respond(answer)
                    }
                    .disabled(isSubmitting)
                    twoFactorCancelButton()
                case .verifyingCode:
                    ProgressView("Verifying code...")
                case .deliveryRequested:
                    ProgressView("Requesting verification...")
                case .completed:
                    Label("Verification complete", systemImage: "checkmark.circle.fill")
                        .foregroundColor(.green)
                case .failed:
                    V3LocalizedText("Verification could not continue. You can change method or cancel sign-in." + "\nError ID: SS-AUTH-D089")
                        .font(.footnote)
                    twoFactorCancelButton()
                case .cancelled:
                    Text("Sign-in was cancelled.").font(.footnote)
                }
            } else {
            ForEach(fieldDefs, id: \.self) { field in
                // The "technical" field is diagnostics-only output: it renders
                // as selectable caption text below, never as an editable field.
                if field["key"] == "step" || field["key"] == "mode" || field["key"] == "activeID" || field["key"] == "phoneID" || field["key"] == "url" || field["key"] == "urlToken" || field["key"] == "serials" || field["key"] == "technical" {
                    if field["key"] == "urlToken" {
                        if let repairURL {
                            Link("Open Apple Account Repair", destination: repairURL)
                                .font(.caption)
                        } else {
                            Text("Apple account repair link is unavailable.")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    } else if let value = field["value"], !value.isEmpty, field["key"] == "url" {
                        if let repairURL = V3AuthRepairURLPolicy.openableURL(value) {
                            Link("Open Apple Account Repair", destination: repairURL)
                                .font(.caption)
                        }
                        Text(value)
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .textSelection(.enabled)
                    }
                } else if field["secure"] == "true" {
                    SecureField(V3Localization.prose(field["label"] ?? "", language: .resolve(displayLocale.identifier)), text: binding(field["key"] ?? ""))
                } else {
                    TextField(V3Localization.prose(field["label"] ?? "", language: .resolve(displayLocale.identifier)), text: binding(field["key"] ?? ""))
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                }
            }
            if fieldDefs.count > 0 && options.isEmpty {
                Button("Submit") { submit(choice: "") }
                    .buttonStyle(.borderedProminent)
                    .disabled(isSubmitting)
            }
            // Safe technical diagnostics travel separately from the
            // user-facing message and can be copied without the prompt text.
            if let technical = fieldDefs.first(where: { $0["key"] == "technical" }),
               let value = technical["value"], !value.isEmpty {
                DisclosureGroup("Technical details") {
                    Text(value)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                }
                .font(.caption)
                .foregroundColor(.secondary)
                Button(LocalizedStringKey(copiedDiagnostic == value ? "Copied" : "Copy Details")) {
                    copyDiagnostic(value)
                }
                .font(.caption)
            }
            if isMulti {
                ForEach(options.filter {
                    V3MultiSelectPromptAnswerPolicy.isMemberOption(
                        kind: kind, optionID: $0["id"] ?? "")
                }, id: \.self) { option in
                    Button {
                        toggle(option["id"] ?? "")
                    } label: {
                        HStack {
                            Image(systemName: selected.contains(option["id"] ?? "") ? "checkmark.circle.fill" : "circle")
                                .foregroundColor(.accentColor)
                            if kind == "extensions", let identifier = option["id"], identifier.hasPrefix("remove:") {
                                V3LocalizedText(.localized("Remove %@", String(identifier.dropFirst("remove:".count))))
                            } else { Text(option["label"] ?? "") }
                        }
                    }
                    .disabled(isSubmitting || isSubmissionBlocked)
                }
                if kind == "revocation" {
                    Button("Keep Existing") {
                        respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("keep", fields: fields))
                    }
                        .disabled(isSubmitting || isSubmissionBlocked)
                } else {
                    if options.contains(where: { $0["id"] == "keepAllMainProfile" }) {
                        Button("Keep All (Use Main Profile)") {
                            respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("keepAllMainProfile", fields: fields))
                        }
                        .disabled(isSubmitting || isSubmissionBlocked)
                    }
                    Button("Keep All (Register Each Extension)") {
                        respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("keepAll", fields: fields))
                    }
                    .disabled(isSubmitting || isSubmissionBlocked)
                    Button("Remove All", role: .destructive) {
                        respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("removeAll", fields: fields))
                    }
                    .disabled(isSubmitting || isSubmissionBlocked)
                    if onCancel == nil && options.contains(where: { $0["id"] == "cancel" }) {
                        Button("Cancel", role: .cancel) {
                            respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("cancel", fields: fields))
                        }
                        .disabled(isSubmitting)
                    }
                }
                Button(LocalizedStringKey(kind == "revocation" ? "Revoke Selected" : "Remove Selected"), role: .destructive) {
                    respond(V3MultiSelectPromptAnswerPolicy.selectedMembersAnswer(
                        kind: kind, selectedIDs: selected, fields: fields))
                }
                .disabled(selected.isEmpty || isSubmitting || isSubmissionBlocked)
            } else {
                ForEach(options.filter { onCancel == nil || $0["id"] != "cancel" }, id: \.self) { option in
                    Button(role: (option["id"] == "cancel" || option["id"] == "deny") ? .cancel : .none) {
                        var answer = fields
                        answer["choice"] = option["id"] ?? ""
                        answer["action"] = option["id"] ?? ""
                        respond(answer)
                    } label: {
                        if kind == "team" { Text(option["label"] ?? "") }
                        else { V3LocalizedText(option["label"] ?? "") }
                    }
                    .disabled(isSubmitting || (isSubmissionBlocked &&
                        !["cancel", "changeMethod"].contains(option["id"] ?? "")))
                }
            }
            }
            if let onCancel {
                Button(LocalizedStringKey(cancellationTitle), role: .cancel) { onCancel() }
                    .frame(minHeight: 44)
                    .disabled(cancellationDisabled)
                    .accessibilityIdentifier("signin.prompt.cancel")
            }
        }
        .onAppear {
            loadFields()
            Task { await loadRepairURL() }
        }
        .onChange(of: prompt["id"] as? String ?? "") { _ in
            copiedDiagnostic = nil
            loadFields()
            Task { await loadRepairURL() }
        }
        .task(id: copiedDiagnostic) {
            guard copiedDiagnostic != nil else { return }
            do { try await Task.sleep(nanoseconds: 2_000_000_000) }
            catch { return }
            guard !Task.isCancelled else { return }
            copiedDiagnostic = nil
        }
        .onDisappear {
            copiedDiagnostic = nil
            fields = [:]
            selected = []
            repairURL = nil
        }
    }
    private func copyDiagnostic(_ value: String) {
        UIPasteboard.general.string = value
        copiedDiagnostic = value
    }
    private func loadFields() {
        fields = [:]
        selected = []
        for field in fieldDefs { fields[field["key"] ?? ""] = field["value"] ?? "" }
    }
    private func twoFactorOption(_ option: [String: String]) -> some View {
        Button {
            var answer = fields
            let id = option["id"] ?? ""
            answer["choice"] = id
            answer["action"] = id
            respond(answer)
        } label: {
            HStack {
                if (option["id"] ?? "").hasPrefix("phone:") {
                    Image(systemName: "phone.fill").foregroundColor(.accentColor)
                }
                if (option["id"] ?? "").hasPrefix("phone:") {
                    Text(option["label"] ?? "")
                } else {
                    V3LocalizedText(option["label"] ?? "")
                }
                Spacer()
                Image(systemName: "chevron.right").font(.caption).foregroundColor(.secondary)
            }
        }
        .buttonStyle(.bordered)
        .disabled(isSubmitting || (isSubmissionBlocked &&
            !["cancel", "changeMethod"].contains(option["id"] ?? "")))
    }
    @ViewBuilder private func twoFactorCancelButton() -> some View {
        if onCancel == nil {
            Button("Cancel Sign In", role: .cancel) {
                respond(["action": "cancel", "choice": "cancel"])
            }
            .disabled(isSubmitting)
        }
    }
    private func binding(_ key: String) -> Binding<String> {
        Binding(get: { fields[key] ?? "" }, set: { fields[key] = $0 })
    }
    private func loadRepairURL() async {
        repairURL = nil
        guard kind == "accountRepair",
              let rawURL = fieldDefs.first(where: { $0["key"] == "url" })?["value"] else { return }
        repairURL = V3AuthRepairURLPolicy.openableURL(rawURL)
    }
    private func toggle(_ id: String) {
        if selected.contains(id) { selected.remove(id) } else { selected.insert(id) }
    }
    private func submit(choice: String) {
        var answer = fields
        answer["choice"] = choice
        respond(answer)
    }
    private func respond(_ answer: [String: String]) {
        // The view does not own this transition. Both call sites pass the parent
        // store's own submission flag as the binding, and the parent admits the
        // answer through that same flag: the auth store requires `!isSubmitting`
        // before it will dispatch, and the operation store sends opAnswer
        // unconditionally. Setting the flag here therefore ran admission against
        // state the tap itself had just created, so every answer was refused and
        // nothing was dispatched. This guard still stops a repeat tap within one
        // runloop, where the parent has not been able to update the binding yet;
        // the authoritative transition belongs to the parent alone.
        guard !isSubmitting else { return }
        onAnswer(answer)
    }
}

@MainActor
final class V3AuthStore: ObservableObject {
    @Published var state = "idle"
    @Published var prompt: [String: Any]?
    @Published var previousFailure: [String: Any]?
    @Published var attempts = 0
    @Published private(set) var revision = 0
    @Published var message = ""
    @Published private(set) var currentAttemptFailure = V3AuthAttemptFailureNotice()
    @Published private(set) var promptResponseDiagnostics = ""
    @Published private(set) var promptResponseBlocked = false
    @Published var deliveryProgressMessage = ""
    @Published var twoFactorTransientStep: V3TwoFactorStep?
    @Published var team = ""
    @Published var promptSubmitting = false
    @Published private(set) var isCancelling = false
    @Published private(set) var cancellationConfirmed = true
    @Published private(set) var cancellationWasAttempted = false
    // V3_PROVISIONING_RECOVERY_STATE_V1: a successful Apple sign-in and a failed
    // provisioning attempt are two separate facts and are stored separately, so
    // neither can be presented as the other. The typed provisioning guidance and
    // the safe technical line produced by the service are preserved verbatim
    // instead of being discarded with the terminal payload.
    @Published private(set) var provisioningMessage = ""
    @Published private(set) var provisioningTechnical = ""
    @Published private(set) var provisioningCode = ""
    @Published private(set) var provisioningStage = ""
    @Published private(set) var provisioningCorrelation = ""
    @Published private(set) var provisioningRetryAvailable = false
    @Published private(set) var provisioningReauthenticationAvailable = false
    @Published private(set) var provisioningRecoveryRequiresReconciliation = false
    @Published private(set) var pendingCertificateRecoveryState = V3PendingCertificateRecoveryState.unavailable
    @Published private(set) var provisioningIdentityStateBlocked = false
    @Published private(set) var checkingProvisioningStorage = false
    @Published private(set) var provisioningSessionUnavailable = false
    @Published private(set) var provisioningRetryBlockedByActiveSession = false
    @Published private(set) var provisioningFinishedLater = false
    @Published private(set) var provisioningIncomplete = false
    // Sticky once the service reports an authenticated terminal. It survives a
    // provisioning retry so the screen keeps saying the sign-in succeeded while
    // provisioning is running again.
    @Published private(set) var signedIn = false
    // Retry identity outlives a Sign In view because the app root observes its
    // terminal event after a superseded poll monitor takes ownership.
    private var provisioningRetryReadinessOwnership = V3ProvisioningRetryReadinessOwnership()
    private var pendingAuthenticationReadinessSessionID: String?
    private var pendingAuthenticationReadinessAttemptSequence: UInt64?
    private var provisioningRetryReadinessAttemptSequence: UInt64?
    private var reconciledProvisioningRetryReadinessSessionID: String?
    private var accountRecoveryProtocolAvailable = false
    private var reauthenticationSessionID: String?
    private var pendingCertificateActivationSessionID: String?
    private var session: String?
    private var authoritativeActiveAuthenticationSessionID: String?
    private var task: Task<Void, Never>?
    private var reconciliationGate = V3AuthReconciliationGate()
    private var promptResponseGeneration: UInt64 = 0

    // V3_AUTH_SUCCESS_IS_NOT_PROVISIONING_SUCCESS_V1: authentication succeeded
    // whenever the service reports an authenticated terminal, regardless of
    // whether provisioning then failed.
    var isSignedIn: Bool { signedIn }
    var hasSession: Bool { session != nil }
    private var terminalFailureKind: String? { previousFailure?["kind"] as? String }
    private var terminalFailureRetryable: Bool? {
        V3ServiceBridge.strictBool(previousFailure?["retryable"])
    }
    var terminalFailureAction: V3AuthTerminalFailureAction {
        V3AuthTerminalFailureActionPolicy.resolve(kind: terminalFailureKind,
            retryable: terminalFailureRetryable)
    }
    var terminalFailureGuidance: String? {
        V3AuthTerminalFailureActionPolicy.guidance(kind: terminalFailureKind,
            retryable: terminalFailureRetryable)
    }
    // Dismissal closes only this presentation; the account still needs attention.
    var hasProvisioningProblem: Bool { provisioningIncomplete }

    var provisioningRecoveryActions: V3AuthProvisioningRecoveryPresentation {
        V3AuthProvisioningRecoveryPolicy.resolve(state: state, hasSession: hasSession,
            signedIn: signedIn, provisioningRetryAvailable: provisioningRetryAvailable,
            isCancelling: isCancelling, cancellationConfirmed: cancellationConfirmed,
            authenticationActive: provisioningRetryBlockedByActiveSession,
            reauthenticationAvailable: provisioningReauthenticationAvailable,
            identityStateBlocked: provisioningIdentityStateBlocked)
    }

    var canReauthenticateProvisioning: Bool {
        hasProvisioningProblem && provisioningRecoveryActions.showReauthenticateProvisioning
    }

    var canCheckProvisioningStorage: Bool {
        accountRecoveryProtocolAvailable && provisioningRecoveryRequiresReconciliation && !checkingProvisioningStorage &&
            !hasSession && !isCancelling && !provisioningRetryBlockedByActiveSession
    }

    var canCheckPendingCertificateRecovery: Bool {
        accountRecoveryProtocolAvailable && pendingCertificateRecoveryState.mayReconcile(
            authenticated: signedIn, hasSession: hasSession, isCancelling: isCancelling,
            authenticationActive: provisioningRetryBlockedByActiveSession,
            checkingStorage: checkingProvisioningStorage)
    }

    var canFinishPendingCertificateActivation: Bool {
        accountRecoveryProtocolAvailable && cancellationConfirmed && !provisioningRecoveryRequiresReconciliation &&
            pendingCertificateRecoveryState.mayFinishActivation(authenticated: signedIn,
                hasSession: hasSession, isCancelling: isCancelling,
                authenticationActive: provisioningRetryBlockedByActiveSession,
                checkingStorage: checkingProvisioningStorage)
    }

    func finishPendingCertificateActivation() {
        guard canFinishPendingCertificateActivation else { return }
        startAuthentication(reauthenticatingProvisioning: true, finishingPendingCertificate: true)
    }

    func checkProvisioningStorage() {
        guard canCheckProvisioningStorage || canCheckPendingCertificateRecovery else { return }
        checkingProvisioningStorage = true
        Task { @MainActor in
            defer { checkingProvisioningStorage = false }
            do {
                _ = try await V3ServiceBridge.shared.request(operation: "authReconcileStorage")
                let confirmed = await reconcile(force: true)
                if confirmed && pendingCertificateRecoveryState == .activationRequired && !provisioningRecoveryRequiresReconciliation {
                    message = pendingCertificateRecoveryState.guidance
                } else if confirmed && pendingCertificateRecoveryState == .ready && !provisioningRecoveryRequiresReconciliation {
                    message = "The pending certificate request still needs attention. If Retry Provisioning is available below, use it to finish signing setup."
                } else {
                    message = !confirmed || provisioningRecoveryRequiresReconciliation || pendingCertificateRecoveryState.requiresAttention
                        ? "Saved signing state is still unverified. Review the account and certificate storage diagnostics before another attempt." + "\nError ID: SS-SAVE-D030"
                        : "Saved account state was verified. You can continue setup."
                }
            } catch {
                message = "SideStore could not verify saved account state. Reload status and review the storage diagnostics." + "\nError ID: SS-SAVE-D031"
                provisioningTechnical = (error as? CombinedFailure)?.technicalDetails ?? ""
            }
        }
    }

    func reauthenticateProvisioning() {
        guard canReauthenticateProvisioning else { return }
        startAuthentication(reauthenticatingProvisioning: true)
    }

    func begin() {
        guard canBegin else { return }
        startAuthentication(reauthenticatingProvisioning: signedIn && provisioningIncomplete)
    }

    private func startAuthentication(reauthenticatingProvisioning: Bool, finishingPendingCertificate: Bool = false) {
        task?.cancel()
        provisioningRetryReadinessOwnership.clear()
        provisioningRetryReadinessAttemptSequence = nil
        reconciledProvisioningRetryReadinessSessionID = nil
        let requestedSession = UUID().uuidString
        reauthenticationSessionID = reauthenticatingProvisioning ? requestedSession : nil
        pendingCertificateActivationSessionID = finishingPendingCertificate && reauthenticatingProvisioning ? requestedSession : nil
        reconciliationGate.invalidate()
        session = requestedSession
        pendingAuthenticationReadinessSessionID = requestedSession
        pendingAuthenticationReadinessAttemptSequence = V3AuthReadinessRefreshEvent.nextAttemptSequence()
        attempts = 0
        revision = 0
        state = "working"
        message = ""
        currentAttemptFailure.clear()
        promptResponseDiagnostics = ""
        promptResponseBlocked = false
        deliveryProgressMessage = ""
        twoFactorTransientStep = nil
        prompt = nil
        previousFailure = nil
        promptSubmitting = false
        cancellationConfirmed = true
        cancellationWasAttempted = false
        if !reauthenticatingProvisioning { signedIn = false }
        clearProvisioningOutcome()
        task = Task { await run(sessionID: requestedSession) }
    }
    var canBegin: Bool {
        guard !provisioningRecoveryRequiresReconciliation else { return false }
        if signedIn && provisioningIncomplete { return canReauthenticateProvisioning }
        return !isCancelling && cancellationConfirmed && !provisioningRetryBlockedByActiveSession &&
            !["working", "awaitingPrompt", "resultUnknown"].contains(state)
    }

    private func clearProvisioningOutcome() {
        provisioningMessage = ""
        provisioningTechnical = ""
        provisioningCode = ""
        provisioningStage = ""
        provisioningCorrelation = ""
        provisioningRetryAvailable = false
        provisioningReauthenticationAvailable = false
        provisioningIdentityStateBlocked = false
        provisioningSessionUnavailable = false
        provisioningRetryBlockedByActiveSession = false
        provisioningFinishedLater = false
        provisioningIncomplete = false
    }

    // V3_RETRY_PROVISIONING_REUSES_SESSION_V1: the Apple session is already
    // authenticated, so the retry is a distinct operation. It deliberately does
    // not reuse the interactive begin operation, which would ask for credentials
    // and 2FA again.
    func retryProvisioning() {
        guard canRetryProvisioning else { return }
        let previouslyAvailable = provisioningRetryAvailable
        task?.cancel()
        pendingAuthenticationReadinessSessionID = nil
        pendingAuthenticationReadinessAttemptSequence = nil
        let requestedSession = UUID().uuidString
        provisioningRetryReadinessAttemptSequence = V3AuthReadinessRefreshEvent.nextAttemptSequence()
        reconciledProvisioningRetryReadinessSessionID = nil
        reconciliationGate.invalidate()
        session = requestedSession
        attempts = 0
        revision = 0
        state = "working"
        message = ""
        currentAttemptFailure.clear()
        prompt = nil
        previousFailure = nil
        promptSubmitting = false
        promptResponseDiagnostics = ""
        promptResponseBlocked = false
        clearProvisioningOutcome()
        provisioningRetryReadinessOwnership.begin(sessionID: requestedSession)
        task = Task { await runProvisioningRetry(previouslyAvailable: previouslyAvailable) }
    }
    var canRetryProvisioning: Bool {
        V3AuthProvisioningRecoveryPolicy.resolve(state: state, hasSession: session != nil,
            signedIn: signedIn, provisioningRetryAvailable: provisioningRetryAvailable,
            isCancelling: isCancelling, cancellationConfirmed: cancellationConfirmed,
            authenticationActive: provisioningRetryBlockedByActiveSession,
            identityStateBlocked: provisioningIdentityStateBlocked)
            .showRetryProvisioning
    }

    private func releaseProvisioningRetryReadiness(sessionID: String) {
        guard provisioningRetryReadinessOwnership.owns(sessionID: sessionID) else { return }
        provisioningRetryReadinessOwnership.release(sessionID: sessionID)
        provisioningRetryReadinessAttemptSequence = nil
        reconciledProvisioningRetryReadinessSessionID = nil
    }

    private func runProvisioningRetry(previouslyAvailable: Bool) async {
        guard let requestedSession = session else { return }
        guard provisioningRetryReadinessOwnership.owns(sessionID: requestedSession) else { return }
        let sessionDeadline = Date().addingTimeInterval(V3ServiceBridge.authSessionLifetime)
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "authRetryProvisioning",
                target: requestedSession,
                payload: ["session": requestedSession, "sessionDeadline": sessionDeadline])
            guard V3AuthSessionResponsePolicy.mayAcceptStartedSession(
                expectedSessionID: requestedSession, replySessionID: reply["session"] as? String,
                currentSessionID: session, cancellationInProgress: isCancelling) else {
                // A local Cancel can race the begin reply after the backend
                // accepted this exact session. Keep its ownership for the
                // terminal authCancel reply; only retire mismatched/replaced
                // starts here.
                if session != requestedSession ||
                   (reply["session"] as? String) != requestedSession {
                    releaseProvisioningRetryReadiness(sessionID: requestedSession)
                }
                _ = try? await V3ServiceBridge.shared.request(operation: "authCancel", target: requestedSession)
                guard !Task.isCancelled, session == requestedSession else { return }
                await reconcile(force: true, expectedSession: requestedSession)
                return
            }
            guard let id = reply["session"] as? String,
                  reply["state"] as? String != "failed" else {
                releaseProvisioningRetryReadiness(sessionID: requestedSession)
                // The saved session is gone. Fall back to a full, honest sign-in
                // instead of silently claiming provisioning was retried.
                await reconcile(force: true, expectedSession: requestedSession)
                guard !Task.isCancelled, session == requestedSession else { return }
                if signedIn {
                    if state == "completed" { return }
                    state = "authenticatedProvisioningIncomplete"
                    message = "Apple ID signed in successfully."
                    provisioningMessage = "The saved provisioning session is no longer available. Open Account & Signing to reauthenticate before retrying setup." + "\nError ID: SS-PROV-D099"
                    provisioningRetryAvailable = false
                    provisioningSessionUnavailable = true
                } else {
                    state = "failed"
                    message = reply["message"] as? String
                        ?? "The saved Apple session is no longer valid. Sign in again with this Apple ID."
                    provisioningMessage = ""
                    provisioningSessionUnavailable = true
                }
                return
            }
            session = id
            try await pollLoop(id: id, sessionDeadline: sessionDeadline)
        } catch {
            if isCancelling || Task.isCancelled { return }
            let pollFailure = (error as? V3AuthPollFailure).flatMap {
                $0.sessionID == requestedSession ? $0 : nil
            }
            let failureResponseGeneration = pollFailure?.promptResponseGeneration ?? promptResponseGeneration
            if let notDispatched = error as? CombinedFailure,
               V3AuthProvisioningRetryDispatchPolicy.isConfirmedNotDispatched(notDispatched) {
                releaseProvisioningRetryReadiness(sessionID: requestedSession)
                let reconciliationGenerationBefore = reconciliationGate.generation
                let snapshotConfirmed = await reconcile(force: true, expectedSession: requestedSession)
                guard V3AuthAttemptFailureCommitPolicy.mayCommit(
                    requestedSessionID: requestedSession, currentSessionID: session,
                    capturedPromptResponseGeneration: failureResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    reconciliationGenerationBefore: reconciliationGenerationBefore,
                    currentReconciliationGeneration: reconciliationGate.generation,
                    cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled) else { return }
                provisioningTechnical = notDispatched.technicalDetails
                if signedIn {
                    if state == "completed" {
                        message = V3AuthProvisioningRetryDispatchPolicy.whatHappened(notDispatched) +
                            " " + notDispatched.recovery
                        return
                    }
                    state = "authenticatedProvisioningIncomplete"
                    provisioningIncomplete = true
                    message = "Apple ID signed in successfully."
                    provisioningMessage = V3AuthProvisioningRetryDispatchPolicy.whatHappened(notDispatched) +
                        " " + notDispatched.recovery
                    if snapshotConfirmed {
                        provisioningSessionUnavailable = !provisioningRetryAvailable &&
                            !provisioningRetryBlockedByActiveSession
                        if provisioningRetryBlockedByActiveSession {
                            provisioningMessage = "Another sign-in or provisioning attempt is still active. Wait for it to finish, then reload status before retrying provisioning." + "\nError ID: SS-PROV-D100"
                        }
                        if provisioningSessionUnavailable {
                            provisioningMessage = "Apple ID is signed in, but the saved provisioning session is unavailable. Sign in again with the same Apple ID to finish setup." + "\nError ID: SS-PROV-D101"
                        }
                    } else {
                        provisioningRetryAvailable = previouslyAvailable
                        provisioningRetryBlockedByActiveSession =
                            notDispatched.safeCause == .operationInProgress
                        if provisioningRetryBlockedByActiveSession {
                            provisioningRetryAvailable = false
                            provisioningMessage = "Another sign-in or provisioning attempt is already active. Reload status after it finishes before retrying." + "\nError ID: SS-PROV-D102"
                        }
                        provisioningSessionUnavailable = false
                    }
                } else {
                    state = "failed"
                    message = V3AuthProvisioningRetryDispatchPolicy.whatHappened(notDispatched) +
                        " " + notDispatched.recovery
                    provisioningMessage = ""
                    provisioningRetryAvailable = false
                }
                return
            }
            let reconciliationGenerationBefore = reconciliationGate.generation
            let sessionUnavailable = ((pollFailure?.underlying ?? error) as? CombinedFailure)?.safeCause == .authSessionUnavailable
            let snapshotConfirmed = await reconcile(force: true, expectedSession: requestedSession,
                retireInactiveAuthSession: !sessionUnavailable)
            if let sessionFailure = (pollFailure?.underlying ?? error) as? CombinedFailure,
               sessionFailure.safeCause == .authSessionUnavailable {
                releaseProvisioningRetryReadiness(sessionID: requestedSession)
                resolveUnavailableAuthSession(sessionFailure, expectedSessionID: requestedSession,
                    snapshotConfirmed: snapshotConfirmed)
                return
            }
            if let pollFailure,
               restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                    sessionDeadline: sessionDeadline,
                    failedPromptRevision: pollFailure.promptRevision,
                    failedPromptResponseGeneration: pollFailure.promptResponseGeneration,
                    pollFailureIsTransient: (pollFailure.underlying as? CombinedFailure)
                        .map(V3AuthPollRecoveryPolicy.isTransientTransportFailure) ?? false,
                    provisioningRetry: true,
                    reconciliationWasSuperseded: reconciliationGate.generation !=
                        (reconciliationGenerationBefore &+ 1)) {
                return
            }
            if pollFailure == nil,
               V3AuthPollMonitorRecoveryPolicy.shouldResumeAfterAmbiguousStart(
                    requestedSessionID: requestedSession, currentSessionID: session,
                    activeSessionID: authoritativeActiveAuthenticationSessionID,
                    cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled),
               restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                    sessionDeadline: sessionDeadline, failedPromptRevision: revision,
                    failedPromptResponseGeneration: promptResponseGeneration,
                    pollFailureIsTransient: false, provisioningRetry: true,
                    reconciliationWasSuperseded: false) {
                return
            }
            guard V3AuthAttemptFailureCommitPolicy.mayCommit(
                requestedSessionID: requestedSession, currentSessionID: session,
                capturedPromptResponseGeneration: failureResponseGeneration,
                currentPromptResponseGeneration: promptResponseGeneration,
                reconciliationGenerationBefore: reconciliationGenerationBefore,
                currentReconciliationGeneration: reconciliationGate.generation,
                cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled) else {
                if let pollFailure {
                    _ = restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                        sessionDeadline: sessionDeadline,
                        failedPromptRevision: pollFailure.promptRevision,
                        failedPromptResponseGeneration: pollFailure.promptResponseGeneration,
                        pollFailureIsTransient: (pollFailure.underlying as? CombinedFailure)
                            .map(V3AuthPollRecoveryPolicy.isTransientTransportFailure) ?? false,
                        provisioningRetry: true,
                        reconciliationWasSuperseded: reconciliationGate.generation !=
                            (reconciliationGenerationBefore &+ 1))
                }
                return
            }
            if let pollFailure, prompt != nil {
                let underlying = pollFailure.underlying
                state = "resultUnknown"
                prompt = nil
                promptSubmitting = false
                deliveryProgressMessage = ""
                twoFactorTransientStep = nil
                cancellationConfirmed = false
                message = "Apple ID is signed in, but SideStore could not confirm the current verification or provisioning response. Cancel the unconfirmed session before starting another attempt." + "\nError ID: SS-AUTH-D032"
                provisioningMessage = V3FailureGuidance.message(underlying)
                provisioningTechnical = (underlying as? CombinedFailure)?.technicalDetails ?? ""
                provisioningIncomplete = true
                provisioningRetryAvailable = false
                provisioningSessionUnavailable = false
                currentAttemptFailure.record(snapshotConfirmed: snapshotConfirmed,
                    authenticated: signedIn, failureMessage: V3FailureGuidance.message(underlying),
                    technicalDetails: provisioningTechnical)
                return
            }
            provisioningTechnical = ((pollFailure?.underlying ?? error) as? CombinedFailure)?.technicalDetails ?? ""
            if signedIn {
                if state == "completed" { return }
                if snapshotConfirmed && provisioningSessionUnavailable {
                    provisioningMessage = "Apple ID is signed in, but the saved provisioning session is unavailable. Open Account & Signing to sign in again before retrying setup." + "\nError ID: SS-PROV-D103"
                    provisioningRetryAvailable = false
                    return
                }
                state = "authenticatedProvisioningIncomplete"
                provisioningMessage = pollFailure.map {
                    "Apple ID is signed in, but SideStore could not confirm the provisioning result. " +
                        V3FailureGuidance.message($0.underlying)
                } ?? "Retry Provisioning could not be confirmed. Your last confirmed state is still signed in. Reload status, then try again." + "\nError ID: SS-PROV-D104"
                provisioningRetryAvailable = V3ProvisioningRetryRecoveryPolicy.availabilityAfterFailure(
                    snapshotConfirmed: snapshotConfirmed,
                    snapshotAllowsRetry: provisioningRetryAvailable,
                    previouslyConfirmedAvailable: previouslyAvailable)
                provisioningSessionUnavailable = snapshotConfirmed ? provisioningSessionUnavailable : false
            } else {
                state = "failed"
                message = "The provisioning retry could not be started, and SideStore could not confirm the account state. Check Account & Signing, then reload status." + "\nError ID: SS-AUTH-D033"
                provisioningMessage = ""
                provisioningRetryAvailable = false
            }
        }
    }

    // V3_FINISH_LATER_PRESERVES_ACCOUNT_V1: closing the provisioning flow must
    // not sign the account out. It only dismisses the local recovery
    // presentation; authoritative account state is reloaded afterwards.
    func finishProvisioningLater() {
        provisioningFinishedLater = true
    }

    @discardableResult
    func reconcile(force: Bool = false, expectedSession: String? = nil,
                   retireInactiveAuthSession: Bool = true) async -> Bool {
        guard force || !["working", "awaitingPrompt"].contains(state) else { return false }
        guard V3AuthReconciliationSessionPolicy.mayStart(
            expectedSessionID: expectedSession, currentSessionID: session) else { return false }
        let ticket = reconciliationGate.begin(sessionID: session, state: state, revision: revision)
        authoritativeActiveAuthenticationSessionID = nil
        provisioningReauthenticationAvailable = false
        if pendingCertificateRecoveryState.requiresAttention {
            pendingCertificateRecoveryState = .storageUnverified
        }
        let reportedTerminalState = state
        do {
            let snapshot = try await V3ServiceBridge.shared.request(operation: "snapshot")
            guard reconciliationGate.mayApply(ticket, sessionID: session,
                state: state, revision: revision) else { return false }
            guard let authSnapshot = V3ServiceBridge.authSnapshot(snapshot) else {
                message = "SideStore returned account status that could not be validated. Reload status and try again." + "\nError ID: SS-AUTH-D034"
                return false
            }
            let accountFacts = V3AuthSnapshotAuthorityPolicy.facts(authSnapshot)
            accountRecoveryProtocolAvailable = V3ServiceBridge.strictInt(snapshot["accountRecoveryProtocol"]) == 1
            pendingCertificateRecoveryState = V3PendingCertificateRecoveryState.decode(
                protocolVersion: V3ServiceBridge.strictInt(snapshot["pendingCertificateRecoveryProtocol"]),
                rawState: snapshot["pendingCertificateRecoveryState"] as? String)
            provisioningReauthenticationAvailable = accountRecoveryProtocolAvailable && authSnapshot.identityStable &&
                V3ServiceBridge.strictBool(snapshot["provisioningReauthenticationAvailable"]) == true
            provisioningRecoveryRequiresReconciliation =
                V3ServiceBridge.strictBool(snapshot["provisioningRecoveryRequiresReconciliation"]) == true
            if force { provisioningFinishedLater = false }
            authoritativeActiveAuthenticationSessionID = accountFacts.authenticationSessionID
            let ownerSessionID = expectedSession ?? session
            let authenticationActiveForCurrentSession = V3AuthSessionCorrelationPolicy.isActive(
                sessionID: ownerSessionID, authenticationActive: accountFacts.authenticationActive,
                activeSessionID: accountFacts.authenticationSessionID)
            let anotherSessionActive = V3AuthSessionCorrelationPolicy.hasOtherActiveSession(
                sessionID: ownerSessionID, authenticationActive: accountFacts.authenticationActive,
                activeSessionID: accountFacts.authenticationSessionID)
            if V3AuthReconciliationPresentationPolicy.shouldPreserveActivePrompt(
                reportedState: reportedTerminalState, hasPrompt: prompt != nil,
                activeSessionMatches: session != nil && (expectedSession == nil || expectedSession == session),
                cancellationInProgress: isCancelling) && authenticationActiveForCurrentSession {
                // The exact SideSign prompt/session is the current authority
                // while credentials, 2FA, or team selection are in flight. A
                // separate account snapshot can observe authenticated=true
                // before provisioning activates its account row; it must not
                // replace an answerable prompt with a terminal UI state.
                signedIn = accountFacts.authenticated
                provisioningIncomplete = accountFacts.provisioningIncomplete
                if let snapshotTeam = snapshot["team"] as? String { team = snapshotTeam }
                return true
            }
            let readinessOwnerSessionID = expectedSession ?? session
            let retrySessionID = readinessOwnerSessionID
            let retryAttemptSequence = provisioningRetryReadinessAttemptSequence
            if V3AuthRetryReadinessReconciliationPolicy.shouldPublish(
                sessionID: retrySessionID, expectedSessionID: readinessOwnerSessionID,
                ownsRetry: retrySessionID.map { provisioningRetryReadinessOwnership.owns(sessionID: $0) } ?? false,
                authenticated: accountFacts.authenticated,
                authenticationActive: accountFacts.authenticationActive,
                provisioningIncomplete: accountFacts.provisioningIncomplete,
                attemptSequence: retryAttemptSequence),
               let retrySessionID, let retryAttemptSequence {
                // A lost terminal poll can still be confirmed by the
                // authoritative account snapshot. Keep the retry attempt
                // sequence so a later terminal delivery is deduped as the
                // same operation by the root observer.
                V3AuthReadinessRefreshEvent.post(sessionID: retrySessionID,
                    attemptSequence: retryAttemptSequence)
                reconciledProvisioningRetryReadinessSessionID = retrySessionID
                provisioningRetryReadinessOwnership.release(sessionID: retrySessionID)
            } else if accountFacts.authenticated, !accountFacts.authenticationActive,
               let pendingReadinessSessionID = pendingAuthenticationReadinessSessionID,
               (expectedSession ?? session) == pendingReadinessSessionID,
               let pendingAttemptSequence = pendingAuthenticationReadinessAttemptSequence {
                // The correlated poll terminal may have been lost. Once the
                // authoritative snapshot confirms authentication and no auth
                // owner remains, the app root can still refresh JIT-Less facts.
                V3AuthReadinessRefreshEvent.post(sessionID: pendingReadinessSessionID,
                    attemptSequence: pendingAttemptSequence)
                pendingAuthenticationReadinessSessionID = nil
                pendingAuthenticationReadinessAttemptSequence = nil
            }
            // A persisted account row can outlive an authenticated Apple
            // session. Only SideStore's explicit session fact proves that
            // authentication is currently active.
            let authoritative = accountFacts.authenticated
            let incomplete = accountFacts.provisioningIncomplete
            let canRetryProvisioning = accountFacts.provisioningRetryAvailable
            let authenticationActive = accountFacts.authenticationActive
            if retireInactiveAuthSession, !authenticationActiveForCurrentSession,
               let ownerSessionID {
                V3ServiceBridge.shared.reconcileAuthSessionOwnership(
                    sessionID: ownerSessionID, authenticationActive: false)
                if session == ownerSessionID { session = nil }
                cancellationConfirmed = true
                cancellationWasAttempted = false
            }
            let reconciliationState = V3AuthUnknownResultReconciliationPolicy.reportedState(
                originalState: reportedTerminalState, hasSession: session != nil,
                authenticated: accountFacts.authenticated)
            let resolvesUnknownAttempt = reportedTerminalState == "resultUnknown" && session == nil &&
                !accountFacts.authenticated
            if authoritative {
                // V3_FINISH_LATER_RECONCILES_AS_SIGNED_IN_V1: authoritative state
                // wins. A finished-later provisioning attempt still reconciles as
                // signed in, never back to "sign in again".
                signedIn = true
                if reportedTerminalState == "resultUnknown", session == nil {
                    // The original attempt remains unconfirmed, but no host-owned
                    // SideSign session remains. A separately admitted provisioning
                    // retry is safe when the authoritative snapshot permits it.
                    cancellationConfirmed = true
                    cancellationWasAttempted = false
                }
                let previousFailureMessage = reportedTerminalState == "failed"
                    ? previousFailure.map { Self.failureMessage(from: $0) } : nil
                let presentation = V3AuthReconciliationPresentationPolicy.resolve(
                    reportedState: reconciliationState, authenticated: true,
                    provisioningIncomplete: incomplete,
                    previousFailureMessage: previousFailureMessage,
                    authenticationActive: authenticationActiveForCurrentSession)
                state = presentation.state
                message = presentation.message
                team = snapshot["team"] as? String ?? ""
                if incomplete || authenticationActiveForCurrentSession {
                    provisioningIncomplete = true
                    // The service still reports an authenticated session whose
                    // provisioning never activated an account. Preserve any
                    // terminal attempt state while showing provisioning recovery
                    // as a separate account-state fact.
                    if provisioningMessage.isEmpty {
                        provisioningMessage = snapshot["provisioningState"] as? String == "unknown"
                            ? "Device provisioning has not been verified in this SideStore session. Complete setup to verify it." + "\nError ID: SS-PROV-D105"
                            : "Device provisioning did not complete. Complete setup, or finish later and come back." + "\nError ID: SS-PROV-D106"
                    }
                    // The account snapshot alone does not prove the process-local
                    // authenticated session needed to resume provisioning survived.
                    provisioningRetryAvailable = authenticationActive ? false : canRetryProvisioning
                    provisioningRetryBlockedByActiveSession = authenticationActive
                    provisioningSessionUnavailable = !canRetryProvisioning && !authenticationActive
                    if provisioningIdentityStateBlocked {
                        // A generic account snapshot cannot verify or repair the
                        // Anisette pair. Keep the owned failure and its guidance.
                        provisioningRetryAvailable = false
                        provisioningSessionUnavailable = false
                    } else if canRetryProvisioning && reportedTerminalState == "resultUnknown" {
                        provisioningMessage = "Apple ID is signed in, but provisioning is incomplete. The previous sign-in attempt remains unconfirmed; you can retry provisioning in a new session." + "\nError ID: SS-PROV-D107"
                    } else if authenticationActiveForCurrentSession {
                        provisioningMessage = "Another sign-in or provisioning attempt is still active. Wait for it to finish, then reload status before retrying provisioning." + "\nError ID: SS-PROV-D100"
                    } else if !canRetryProvisioning {
                        provisioningMessage = "Apple ID is signed in, but the saved provisioning session is unavailable. Sign in again with the same Apple ID to finish setup." + "\nError ID: SS-PROV-D101"
                    }
                } else {
                    clearProvisioningOutcome()
                }
            } else {
                signedIn = false
                team = ""
                provisioningRetryBlockedByActiveSession = authenticationActive
                let inactiveSessionPresentation = V3AuthInactiveSessionResolutionPolicy.resolve(
                    reportedState: reportedTerminalState, authenticated: false,
                    authenticationActive: authenticationActiveForCurrentSession,
                    anotherSessionActive: anotherSessionActive)
                let signedOutPresentation = inactiveSessionPresentation ??
                    V3AuthReconciliationPresentationPolicy.resolve(
                        reportedState: reconciliationState, authenticated: false,
                        provisioningIncomplete: false)
                if let inactiveSessionPresentation {
                    state = inactiveSessionPresentation.state
                    message = inactiveSessionPresentation.message
                    prompt = nil
                    promptSubmitting = false
                    deliveryProgressMessage = ""
                    twoFactorTransientStep = nil
                } else if resolvesUnknownAttempt {
                    state = "failed"
                    cancellationConfirmed = true
                    cancellationWasAttempted = false
                    currentAttemptFailure.clear()
                    message = "SideStore confirmed that no account is signed in. You can start a new sign-in."
                } else if state == "idle" || state == "completed" || state == "authenticatedProvisioningIncomplete" {
                    state = "idle"
                    prompt = nil
                    clearProvisioningOutcome()
                }
                if !resolvesUnknownAttempt && !signedOutPresentation.message.isEmpty {
                    message = signedOutPresentation.message
                }
            }
            if anotherSessionActive {
                provisioningRetryBlockedByActiveSession = true
                if let presentation = V3AuthOtherSessionReconciliationPolicy.resolve(
                    reportedState: reportedTerminalState, authenticated: authoritative,
                    anotherSessionActive: true) {
                    state = presentation.state
                    message = presentation.message
                    if presentation.clearPrompt {
                        prompt = nil
                        promptSubmitting = false
                        deliveryProgressMessage = ""
                        twoFactorTransientStep = nil
                        cancellationConfirmed = true
                    }
                } else if state == "completed" {
                    message = "Apple ID is signed in. Another sign-in or provisioning session is active; wait for it to finish before starting another account action."
                }
            }
            return true
        } catch {
            guard reconciliationGate.mayApply(ticket, sessionID: session,
                state: state, revision: revision) else { return false }
            if state == "idle" { message = "Could not confirm the current SideStore account. Reload status and try again." + "\nError ID: SS-AUTH-D035" }
            return false
        }
    }

    private func resolveUnavailableAuthSession(_ failure: CombinedFailure,
                                               expectedSessionID: String,
                                               snapshotConfirmed: Bool) {
        guard V3AuthSessionUnavailablePolicy.shouldRetireOwnership(
            sessionID: expectedSessionID, currentSessionID: session, failure: failure) else { return }
        V3ServiceBridge.shared.confirmAuthSessionUnavailable(sessionID: expectedSessionID)
        let anotherSessionActive = snapshotConfirmed && authoritativeActiveAuthenticationSessionID != nil &&
            authoritativeActiveAuthenticationSessionID != expectedSessionID
        let presentation = V3AuthSessionUnavailablePolicy.resolve(
            authenticated: signedIn, provisioningIncomplete: provisioningIncomplete,
            snapshotConfirmed: snapshotConfirmed, safeMessage: failure.safeMessage,
            recovery: failure.recovery, anotherSessionActive: anotherSessionActive)
        session = nil
        cancellationConfirmed = presentation.cancellationConfirmed
        cancellationWasAttempted = false
        prompt = nil
        promptSubmitting = false
        deliveryProgressMessage = ""
        twoFactorTransientStep = nil
        state = presentation.state
        message = presentation.message
        if anotherSessionActive { provisioningRetryBlockedByActiveSession = true }
        if let provisioningMessage = presentation.provisioningMessage {
            provisioningIncomplete = true
            self.provisioningMessage = provisioningMessage
            provisioningTechnical = failure.technicalDetails
            provisioningStage = failure.stage.rawValue
            provisioningCode = failure.code.rawValue
            provisioningCorrelation = failure.correlationID
            provisioningRetryAvailable = false
            provisioningSessionUnavailable = true
            provisioningFinishedLater = false
        } else {
            clearProvisioningOutcome()
            if presentation.state == "resultUnknown" {
                currentAttemptFailure.record(snapshotConfirmed: false,
                    authenticated: false, failureMessage: failure.safeMessage,
                    technicalDetails: failure.technicalDetails)
            } else if signedIn {
                currentAttemptFailure.clear()
            } else {
                currentAttemptFailure.record(snapshotConfirmed: snapshotConfirmed,
                    authenticated: false, failureMessage: failure.safeMessage,
                    technicalDetails: failure.technicalDetails)
            }
        }
        task = nil
        NSLog("[V3_AUTH_UI] SESSION_UNAVAILABLE state=%@ signed_in=%d snapshot_confirmed=%d",
              presentation.state, signedIn ? 1 : 0, snapshotConfirmed ? 1 : 0)
    }

    private func run(sessionID requestedSession: String) async {
        let sessionDeadline = Date().addingTimeInterval(V3ServiceBridge.authSessionLifetime)
        do {
            state = "working"
            message = ""
            prompt = nil
            var payload: [String: Any] = ["session": requestedSession, "sessionDeadline": sessionDeadline]
            if reauthenticationSessionID == requestedSession {
                guard accountRecoveryProtocolAvailable else { return }
                payload["provisioningLogin"] = true
                if pendingCertificateActivationSessionID == requestedSession {
                    payload["finishPendingCertificateActivation"] = true
                }
            }
            let reply = try await V3ServiceBridge.shared.request(operation: "authBegin",
                target: requestedSession, payload: payload)
            guard V3AuthSessionResponsePolicy.mayAcceptStartedSession(
                expectedSessionID: requestedSession, replySessionID: reply["session"] as? String,
                currentSessionID: session, cancellationInProgress: isCancelling) else {
                _ = try? await V3ServiceBridge.shared.request(operation: "authCancel", target: requestedSession)
                guard !Task.isCancelled, session == requestedSession else { return }
                await reconcile(force: true, expectedSession: requestedSession)
                return
            }
            guard let id = reply["session"] as? String else {
                throw NSError(domain: "V3Auth", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "The service did not start sign-in."])
            }
            session = id
            try await pollLoop(id: id, sessionDeadline: sessionDeadline)
        } catch {
            if isCancelling || Task.isCancelled { return }
            let pollFailure = (error as? V3AuthPollFailure).flatMap {
                $0.sessionID == requestedSession ? $0 : nil
            }
            let failureResponseGeneration = pollFailure?.promptResponseGeneration ?? promptResponseGeneration
            if let notDispatched = error as? CombinedFailure,
               V3AuthAttemptStartFailurePolicy.isConfirmedNotDispatched(notDispatched) {
                if pendingAuthenticationReadinessSessionID == requestedSession {
                    pendingAuthenticationReadinessSessionID = nil
                }
                let reconciliationGenerationBefore = reconciliationGate.generation
                let snapshotConfirmed = await reconcile(force: true, expectedSession: requestedSession)
                if V3AuthAttemptFailureCommitPolicy.shouldPreserveAuthoritativeAccountState(
                    snapshotConfirmed: snapshotConfirmed, authenticated: signedIn, state: state) {
                    session = nil
                    cancellationConfirmed = true
                    cancellationWasAttempted = false
                    currentAttemptFailure.clear()
                    return
                }
                guard V3AuthAttemptFailureCommitPolicy.mayCommit(
                    requestedSessionID: requestedSession, currentSessionID: session,
                    capturedPromptResponseGeneration: failureResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    reconciliationGenerationBefore: reconciliationGenerationBefore,
                    currentReconciliationGeneration: reconciliationGate.generation,
                    cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled) else { return }
                state = "failed"
                cancellationConfirmed = true
                session = nil
                message = notDispatched.safeMessage + " " + notDispatched.recovery
                currentAttemptFailure.clear()
                return
            }
            let underlyingError = pollFailure?.underlying ?? error
            let failureMessage = V3FailureGuidance.message(underlyingError)
            let failureTechnical = (underlyingError as? CombinedFailure)?.technicalDetails ?? ""
            // A thrown start/poll request does not prove the authentication
            // attempt reached a terminal result. Reconcile account state for
            // display, but keep the attempt outcome unknown until its session
            // is cancelled or a correlated terminal reply arrives.
            let reconciliationGenerationBefore = reconciliationGate.generation
            let sessionUnavailable = ((pollFailure?.underlying ?? error) as? CombinedFailure)?.safeCause == .authSessionUnavailable
            let snapshotConfirmed = await reconcile(force: true, expectedSession: requestedSession,
                retireInactiveAuthSession: !sessionUnavailable)
            if let sessionFailure = (pollFailure?.underlying ?? error) as? CombinedFailure,
               sessionFailure.safeCause == .authSessionUnavailable {
                resolveUnavailableAuthSession(sessionFailure, expectedSessionID: requestedSession,
                    snapshotConfirmed: snapshotConfirmed)
                return
            }
            if let pollFailure,
               restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                    sessionDeadline: sessionDeadline,
                    failedPromptRevision: pollFailure.promptRevision,
                    failedPromptResponseGeneration: pollFailure.promptResponseGeneration,
                    pollFailureIsTransient: (pollFailure.underlying as? CombinedFailure)
                        .map(V3AuthPollRecoveryPolicy.isTransientTransportFailure) ?? false,
                    provisioningRetry: false,
                    reconciliationWasSuperseded: reconciliationGate.generation !=
                        (reconciliationGenerationBefore &+ 1)) {
                return
            }
            if pollFailure == nil,
               V3AuthPollMonitorRecoveryPolicy.shouldResumeAfterAmbiguousStart(
                    requestedSessionID: requestedSession, currentSessionID: session,
                    activeSessionID: authoritativeActiveAuthenticationSessionID,
                    cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled),
               restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                    sessionDeadline: sessionDeadline, failedPromptRevision: revision,
                    failedPromptResponseGeneration: promptResponseGeneration,
                    pollFailureIsTransient: false, provisioningRetry: false,
                    reconciliationWasSuperseded: false) {
                return
            }
            if V3AuthAttemptFailureCommitPolicy.shouldPreserveAuthoritativeAccountState(
                snapshotConfirmed: snapshotConfirmed, authenticated: signedIn, state: state) {
                return
            }
            if V3AuthAttemptFailureCommitPolicy.shouldCommitConfirmedSignedOutFailure(
                snapshotConfirmed: snapshotConfirmed, authenticated: signedIn,
                hasSession: session != nil, cancellationConfirmed: cancellationConfirmed,
                state: state) {
                currentAttemptFailure.record(snapshotConfirmed: true, authenticated: false,
                    failureMessage: failureMessage, technicalDetails: failureTechnical)
                return
            }
            guard V3AuthAttemptFailureCommitPolicy.mayCommit(
                requestedSessionID: requestedSession, currentSessionID: session,
                capturedPromptResponseGeneration: failureResponseGeneration,
                currentPromptResponseGeneration: promptResponseGeneration,
                reconciliationGenerationBefore: reconciliationGenerationBefore,
                currentReconciliationGeneration: reconciliationGate.generation,
                cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled) else {
                if let pollFailure {
                    _ = restartPollMonitorAfterSupersededFailure(sessionID: requestedSession,
                        sessionDeadline: sessionDeadline,
                        failedPromptRevision: pollFailure.promptRevision,
                        failedPromptResponseGeneration: pollFailure.promptResponseGeneration,
                        pollFailureIsTransient: (pollFailure.underlying as? CombinedFailure)
                            .map(V3AuthPollRecoveryPolicy.isTransientTransportFailure) ?? false,
                        provisioningRetry: false,
                        reconciliationWasSuperseded: reconciliationGate.generation !=
                            (reconciliationGenerationBefore &+ 1))
                }
                return
            }
            state = "resultUnknown"
            prompt = nil
            promptSubmitting = false
            deliveryProgressMessage = ""
            twoFactorTransientStep = nil
            cancellationConfirmed = false
            message = snapshotConfirmed
                ? "SideStore confirmed account status but could not confirm whether this sign-in attempt finished. Cancel the unconfirmed session before starting another attempt." + "\nError ID: SS-AUTH-D036"
                : "SideStore could not confirm the sign-in result. Cancel the unconfirmed session before starting another attempt." + "\nError ID: SS-AUTH-D037"
            currentAttemptFailure.record(snapshotConfirmed: snapshotConfirmed,
                authenticated: signedIn, failureMessage: failureMessage,
                technicalDetails: failureTechnical)
        }
    }

    private func pollLoop(id: String, sessionDeadline: Date) async throws {
        var pollFailureCount = 0
        while !Task.isCancelled {
            try await Task.sleep(nanoseconds: 1_000_000_000)
            try Task.checkCancellation()
            guard !isCancelling, session == id else { throw CancellationError() }
            if ["completed", "failed", "timedOut", "promptExpired", "cancelled"].contains(state) ||
               (state == "authenticatedProvisioningIncomplete" && !provisioningRetryBlockedByActiveSession) {
                return
            }
            guard Date() < sessionDeadline else {
                state = "timedOut"
                message = "Sign-in timed out. Checking the current SideStore account..."
                prompt = nil
                await reconcile(force: true, expectedSession: id)
                return
            }
            let pollRevision = revision
            let pollPromptResponseGeneration = promptResponseGeneration
            let reply: [String: Any]
            do {
                reply = try await V3ServiceBridge.shared.request(operation: "authPoll", target: id,
                    requestDeadline: sessionDeadline)
                if message == "Connection to SideStore was interrupted. Waiting for the current sign-in result..." {
                    message = ""
                }
                pollFailureCount = 0
            } catch let failure as CombinedFailure
                where V3AuthPollRecoveryPolicy.shouldRetry(failure, sessionDeadline: sessionDeadline) {
                if V3AuthPollFailureRacePolicy.shouldIgnore(
                    requestedSessionID: id, currentSessionID: session,
                    requestedRevision: pollRevision, currentRevision: revision,
                    requestedPromptResponseGeneration: pollPromptResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    promptSubmissionInProgress: promptSubmitting) {
                    continue
                }
                pollFailureCount += 1
                message = "Connection to SideStore was interrupted. Waiting for the current sign-in result..."
                let delay = V3AuthPollRecoveryPolicy.retryDelay(attempt: pollFailureCount - 1,
                    remaining: sessionDeadline.timeIntervalSinceNow)
                guard delay > 0 else {
                    state = "timedOut"
                    message = "Sign-in timed out. Checking the current SideStore account..."
                    prompt = nil
                    await reconcile(force: true, expectedSession: id)
                    return
                }
                try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                continue
            } catch let failure as CombinedFailure
                where V3AuthPollRecoveryPolicy.shouldFinishTimedOut(
                    failure, sessionDeadline: sessionDeadline) {
                if V3AuthPollFailureRacePolicy.shouldIgnore(
                    requestedSessionID: id, currentSessionID: session,
                    requestedRevision: pollRevision, currentRevision: revision,
                    requestedPromptResponseGeneration: pollPromptResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    promptSubmissionInProgress: promptSubmitting) {
                    continue
                }
                state = "timedOut"
                message = "Sign-in timed out. Checking the current SideStore account..."
                prompt = nil
                await reconcile(force: true, expectedSession: id)
                return
            } catch {
                if V3AuthPollFailureRacePolicy.shouldIgnore(
                    requestedSessionID: id, currentSessionID: session,
                    requestedRevision: pollRevision, currentRevision: revision,
                    requestedPromptResponseGeneration: pollPromptResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    promptSubmissionInProgress: promptSubmitting) {
                    continue
                }
                if error is CancellationError { throw error }
                throw V3AuthPollFailure(underlying: error, sessionID: id,
                    promptResponseGeneration: pollPromptResponseGeneration,
                    promptRevision: pollRevision)
            }
            guard V3AuthPollResponsePolicy.mayApply(
                currentSessionID: session, replySessionID: reply["session"] as? String ?? "",
                cancellationInProgress: isCancelling,
                currentRevision: revision,
                replyRevision: V3ServiceBridge.strictInt(reply["revision"]),
                currentPromptID: prompt?["id"] as? String,
                replyPromptID: (reply["prompt"] as? [String: Any])?["id"] as? String) else {
                continue
            }
            guard !Task.isCancelled, !isCancelling, session == id else { throw CancellationError() }
            apply(reply)
            guard let current = reply["state"] as? String else { return }
            if V3AuthTimeoutReconciliationPolicy.shouldReconcileAfterTerminal(current) {
                await reconcile(force: true, expectedSession: id)
                return
            }
            if Date() >= sessionDeadline && (current == "working" || current == "awaitingPrompt") {
                state = "timedOut"
                message = "Sign-in timed out. Checking the current SideStore account..."
                prompt = nil
                await reconcile(force: true, expectedSession: id)
                return
            }
            guard current == "working" || current == "awaitingPrompt" else { return }
        }
    }

    func reloadAuthoritativeAccountStatus() {
        guard !isCancelling else { return }
        Task { @MainActor in
            let confirmed = await reconcile(force: true)
            if !confirmed && state == "resultUnknown" {
                cancellationConfirmed = false
                message = "SideStore could not confirm the account state. Reload status again before starting a new sign-in." + "\nError ID: SS-AUTH-D038"
            }
        }
    }

    // A poll can fail while an answer is being submitted. The old monitor then
    // unwinds, and its catch awaits an account snapshot. If that answer wins
    // during reconciliation, the failure is stale and must hand ownership to a
    // replacement monitor for the same bounded session.
    private func restartPollMonitorAfterSupersededFailure(sessionID: String,
        sessionDeadline: Date, failedPromptRevision: Int,
        failedPromptResponseGeneration: UInt64,
        pollFailureIsTransient: Bool,
        provisioningRetry: Bool,
        reconciliationWasSuperseded: Bool) -> Bool {
        guard V3AuthPollMonitorRecoveryPolicy.shouldResume(
            requestedSessionID: sessionID, currentSessionID: session,
            failedPromptRevision: failedPromptRevision, currentPromptRevision: revision,
            failedPromptResponseGeneration: failedPromptResponseGeneration,
            currentPromptResponseGeneration: promptResponseGeneration, state: state,
            promptSubmissionInProgress: promptSubmitting,
            activeSessionID: authoritativeActiveAuthenticationSessionID,
            pollFailureIsTransient: pollFailureIsTransient, cancellationInProgress: isCancelling,
            taskCancelled: Task.isCancelled,
            reconciliationWasSuperseded: reconciliationWasSuperseded,
            sessionDeadline: sessionDeadline) else { return false }
        if provisioningRetry &&
           !provisioningRetryReadinessOwnership.handoffAfterSupersededPollFailure(sessionID: sessionID) {
            return false
        }
        task = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.continuePollingAfterSupersededFailure(sessionID: sessionID,
                sessionDeadline: sessionDeadline, provisioningRetry: provisioningRetry)
        }
        return true
    }

    private func continuePollingAfterSupersededFailure(sessionID: String,
        sessionDeadline: Date, provisioningRetry: Bool) async {
        var monitorFailureCount = 0
        while !Task.isCancelled, !isCancelling, session == sessionID,
              (!provisioningRetry || provisioningRetryReadinessOwnership.owns(sessionID: sessionID)) {
            do {
                try await pollLoop(id: sessionID, sessionDeadline: sessionDeadline)
                return
            } catch let failure as V3AuthPollFailure where failure.sessionID == sessionID {
                let reconciliationGenerationBefore = reconciliationGate.generation
                let sessionUnavailable = (failure.underlying as? CombinedFailure)?.safeCause == .authSessionUnavailable
                let snapshotConfirmed = await reconcile(force: true, expectedSession: sessionID,
                    retireInactiveAuthSession: !sessionUnavailable)
                if let combined = failure.underlying as? CombinedFailure,
                   combined.safeCause == .authSessionUnavailable {
                    resolveUnavailableAuthSession(combined, expectedSessionID: sessionID,
                        snapshotConfirmed: snapshotConfirmed)
                    return
                }
                if V3AuthPollMonitorRecoveryPolicy.shouldResume(
                    requestedSessionID: sessionID, currentSessionID: session,
                    failedPromptRevision: failure.promptRevision, currentPromptRevision: revision,
                    failedPromptResponseGeneration: failure.promptResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration, state: state,
                    promptSubmissionInProgress: promptSubmitting,
                    activeSessionID: authoritativeActiveAuthenticationSessionID,
                    pollFailureIsTransient: (failure.underlying as? CombinedFailure)
                        .map(V3AuthPollRecoveryPolicy.isTransientTransportFailure) ?? false,
                    cancellationInProgress: isCancelling,
                    taskCancelled: Task.isCancelled,
                    reconciliationWasSuperseded: reconciliationGate.generation !=
                        (reconciliationGenerationBefore &+ 1),
                    sessionDeadline: sessionDeadline) {
                    if let combined = failure.underlying as? CombinedFailure,
                       V3AuthPollRecoveryPolicy.isTransientTransportFailure(combined),
                       prompt != nil &&
                       failure.promptResponseGeneration == promptResponseGeneration &&
                       failure.promptRevision == revision && !promptSubmitting {
                        message = "Connection to SideStore was interrupted while checking this verification request. The current response is still available."
                    }
                    monitorFailureCount += 1
                    let delay = V3AuthPollRecoveryPolicy.retryDelay(attempt: monitorFailureCount - 1,
                        remaining: sessionDeadline.timeIntervalSinceNow)
                    if delay > 0 {
                        try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                    }
                    if Task.isCancelled { return }
                    continue
                }
                if V3AuthAttemptFailureCommitPolicy.shouldPreserveAuthoritativeAccountState(
                    snapshotConfirmed: snapshotConfirmed, authenticated: signedIn, state: state) {
                    return
                }
                let failedMessage = V3FailureGuidance.message(failure.underlying)
                let failedTechnical = (failure.underlying as? CombinedFailure)?.technicalDetails ?? ""
                if V3AuthAttemptFailureCommitPolicy.shouldCommitConfirmedSignedOutFailure(
                    snapshotConfirmed: snapshotConfirmed, authenticated: signedIn,
                    hasSession: session != nil, cancellationConfirmed: cancellationConfirmed,
                    state: state) {
                    currentAttemptFailure.record(snapshotConfirmed: true, authenticated: false,
                        failureMessage: failedMessage, technicalDetails: failedTechnical)
                    return
                }
                guard V3AuthAttemptFailureCommitPolicy.mayCommit(
                    requestedSessionID: sessionID, currentSessionID: session,
                    capturedPromptResponseGeneration: failure.promptResponseGeneration,
                    currentPromptResponseGeneration: promptResponseGeneration,
                    reconciliationGenerationBefore: reconciliationGenerationBefore,
                    currentReconciliationGeneration: reconciliationGate.generation,
                    cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled) else { return }
                let underlying = failure.underlying
                if provisioningRetry, signedIn, state != "completed", prompt != nil {
                    state = "resultUnknown"
                    prompt = nil
                    promptSubmitting = false
                    deliveryProgressMessage = ""
                    twoFactorTransientStep = nil
                    cancellationConfirmed = false
                    message = "Apple ID is signed in, but SideStore could not confirm the current verification or provisioning response. Cancel the unconfirmed session before starting another attempt." + "\nError ID: SS-AUTH-D032"
                    provisioningMessage = V3FailureGuidance.message(underlying)
                    provisioningTechnical = (underlying as? CombinedFailure)?.technicalDetails ?? ""
                    provisioningIncomplete = true
                    provisioningRetryAvailable = false
                    provisioningSessionUnavailable = false
                    currentAttemptFailure.record(snapshotConfirmed: snapshotConfirmed,
                        authenticated: signedIn, failureMessage: V3FailureGuidance.message(underlying),
                        technicalDetails: provisioningTechnical)
                    return
                }
                if provisioningRetry, signedIn, state != "completed" {
                    state = "authenticatedProvisioningIncomplete"
                    provisioningIncomplete = true
                    message = "Apple ID signed in successfully."
                    provisioningMessage = "SideStore could not confirm the provisioning result. " +
                        V3FailureGuidance.message(underlying)
                    provisioningTechnical = (underlying as? CombinedFailure)?.technicalDetails ?? ""
                    provisioningSessionUnavailable = false
                    return
                }
                state = "resultUnknown"
                prompt = nil
                promptSubmitting = false
                deliveryProgressMessage = ""
                twoFactorTransientStep = nil
                cancellationConfirmed = false
                message = snapshotConfirmed
                    ? "SideStore confirmed account status but could not confirm whether this sign-in attempt finished. Cancel the unconfirmed session before starting another attempt." + "\nError ID: SS-AUTH-D036"
                    : "SideStore could not confirm the sign-in result. Cancel the unconfirmed session before starting another attempt." + "\nError ID: SS-AUTH-D037"
                currentAttemptFailure.record(snapshotConfirmed: snapshotConfirmed,
                    authenticated: signedIn, failureMessage: V3FailureGuidance.message(underlying),
                    technicalDetails: (underlying as? CombinedFailure)?.technicalDetails ?? "")
                return
            } catch {
                return
            }
        }
    }

    private func apply(_ reply: [String: Any]) {
        let oldPromptID = prompt?["id"] as? String
        let replyState = reply["state"] as? String ?? state
        let retryReadinessSettlement = provisioningRetryReadinessOwnership.settle(
            currentSessionID: session, replySessionID: reply["session"] as? String,
            replyState: replyState,
            authenticated: V3ServiceBridge.strictBool(reply["authenticated"]) == true,
            cancellationInProgress: isCancelling, taskCancelled: Task.isCancelled)
        let terminalAuthenticationSucceeded =
            ["completed", "authenticatedProvisioningIncomplete"].contains(replyState) &&
            V3ServiceBridge.strictBool(reply["authenticated"]) == true
        let replySessionID = reply["session"] as? String
        let replyBelongsToCurrentSession = replySessionID != nil && replySessionID == session
        let isTerminal = ["completed", "authenticatedProvisioningIncomplete", "failed",
                          "timedOut", "promptExpired", "cancelled"].contains(replyState)
        var readinessAttemptSequence: UInt64?
        let readinessEventSessionID = replySessionID
        let retrySequenceForReply = provisioningRetryReadinessAttemptSequence
        if retryReadinessSettlement == .committed {
            readinessAttemptSequence = retrySequenceForReply
            reconciledProvisioningRetryReadinessSessionID = replySessionID
        } else if retryReadinessSettlement == .finishedWithoutCommit {
            provisioningRetryReadinessAttemptSequence = nil
            reconciledProvisioningRetryReadinessSessionID = nil
        } else if retryReadinessSettlement == .notRetry && terminalAuthenticationSucceeded {
            if reconciledProvisioningRetryReadinessSessionID == replySessionID {
                readinessAttemptSequence = provisioningRetryReadinessAttemptSequence
            } else if pendingAuthenticationReadinessSessionID == replySessionID {
                readinessAttemptSequence = pendingAuthenticationReadinessAttemptSequence
            }
        }
        if isTerminal {
            if pendingAuthenticationReadinessSessionID == replySessionID {
                pendingAuthenticationReadinessSessionID = nil
                pendingAuthenticationReadinessAttemptSequence = nil
            }
        }
        let belongsToCurrentOrReconciledRetry = replyBelongsToCurrentSession ||
            (reconciledProvisioningRetryReadinessSessionID == replySessionID &&
             provisioningRetryReadinessAttemptSequence != nil)
        if belongsToCurrentOrReconciledRetry, terminalAuthenticationSucceeded,
           (retryReadinessSettlement == .committed || retryReadinessSettlement == .notRetry) {
            V3AuthReadinessRefreshEvent.post(sessionID: readinessEventSessionID,
                attemptSequence: readinessAttemptSequence)
        }
        state = replyState
        if ["completed", "authenticatedProvisioningIncomplete", "failed", "timedOut", "promptExpired", "cancelled"].contains(state) {
            currentAttemptFailure.clear()
            provisioningRetryBlockedByActiveSession = false
        }
        attempts = V3ServiceBridge.strictInt(reply["attempts"]) ?? attempts
        revision = V3ServiceBridge.strictInt(reply["revision"]) ?? revision
        prompt = reply["prompt"] as? [String: Any]
        previousFailure = V3AuthPromptFailurePolicy.applying(reply: reply, current: previousFailure)
        if V3ServiceBridge.strictBool(reply["authenticated"]) == true { signedIn = true }
        if oldPromptID != (prompt?["id"] as? String) {
            promptSubmitting = false
            promptResponseDiagnostics = ""
            promptResponseBlocked = false
            if state == "awaitingPrompt" {
                if V3AuthPromptResponsePolicy.shouldClearSubmissionFailure(
                    oldPromptID: oldPromptID, newPromptID: prompt?["id"] as? String,
                    state: state) { message = "" }
                deliveryProgressMessage = ""
                twoFactorTransientStep = nil
            }
        }
        if state == "completed" {
            team = reply["team"] as? String ?? ""
            prompt = nil
            message = ""
            deliveryProgressMessage = ""
            clearProvisioningOutcome()
        } else if state == "authenticatedProvisioningIncomplete" {
            // V3_PROVISIONING_TERMINAL_NOT_A_SIGNIN_FAILURE_V1: the terminal
            // payload carries the classified provisioning problem, which is
            // retained verbatim. This state is never collapsed into "failed".
            provisioningIncomplete = true
            team = reply["team"] as? String ?? team
            provisioningIdentityStateBlocked = ((reply["failure"] as? [String: Any])?["signingContext"] as? [String: String])?["typed_error"] == "anisetteIdentityStateInvalid"
            if provisioningIdentityStateBlocked {
                message = "The saved Apple account is signed in. Provisioning is blocked."
            } else {
                message = "Apple ID signed in successfully."
            }
            prompt = nil
            deliveryProgressMessage = ""
            if let failureKind = reply["failureKind"] as? String {
                provisioningMessage = V3AuthStore.failureMessage(from: ((reply["failure"] as? [String: Any]) ?? ["stage": "provisioning"]).merging(["kind": failureKind]) { _, supplied in supplied })
            } else {
                provisioningMessage = reply["message"] as? String ?? "Provisioning could not be completed."
            }
            provisioningStage = reply["stage"] as? String ?? ""
            provisioningCode = reply["code"] as? String ?? ""
            provisioningTechnical = reply["technicalDetails"] as? String ?? ""
            provisioningCorrelation = (reply["failure"] as? [String: Any])?["correlationID"] as? String ?? ""
            provisioningRetryAvailable = V3ServiceBridge.strictBool(reply["resumable"]) ?? false
            provisioningSessionUnavailable = false
            if !provisioningRetryAvailable && !provisioningIdentityStateBlocked {
                provisioningMessage += " Checking the saved provisioning state before another attempt."
            }
            let diagnosticPresentation = V3AuthFailureDiagnosticsPolicy.provisioning(reply: reply,
                message: provisioningMessage, technical: provisioningTechnical)
            provisioningMessage = diagnosticPresentation.message
            provisioningTechnical = diagnosticPresentation.technical
            provisioningFinishedLater = false
        } else if state == "failed" {
            message = reply["message"] as? String ?? "The sign-in request failed for an unknown reason." + "\nError ID: SS-AUTH-D039"
            if let failure = reply["failure"] as? [String: Any] { previousFailure = failure }
            prompt = nil
            deliveryProgressMessage = ""
            clearProvisioningOutcome()
        } else if state == "timedOut" {
            message = reply["message"] as? String ?? "Sign-in timed out. Start a new sign-in when you are ready." + "\nError ID: SS-AUTH-D040"
            prompt = nil
            deliveryProgressMessage = ""
            twoFactorTransientStep = nil
        } else if state == "promptExpired" {
            message = reply["message"] as? String
                ?? "That verification session expired. Start a new sign-in to request another verification code." + "\nError ID: SS-AUTH-D041"
            task?.cancel()
            prompt = nil
            promptSubmitting = false
            deliveryProgressMessage = ""
            twoFactorTransientStep = nil
        } else if state == "cancelled" {
            message = "Sign-in was cancelled."
            prompt = nil
        }
        // A terminal reply may omit its structured failure. Label the observed
        // auth state without guessing the cause from its display message.
        if ["failed", "timedOut", "promptExpired"].contains(state) {
            let evidence = (reply["failure"] as? [String: Any]) ??
                ["stage": "authentication", "code": state == "failed" ? "failed" : "timedOut"]
            message = V3AuthFailureDiagnosticsPolicy.display(message, failure: evidence)
        }
    }

    static func failureMessage(from failure: [String: Any]) -> String {
        func messageWithoutDiagnosticCode() -> String {
        // The service classifies the real typed error into a display kind.
        // Only show password guidance for proven invalid credentials.
        switch failure["kind"] as? String {
        case "invalidCredentials": return "Apple did not accept the Apple ID or password. Check them and try again."
        case "appSpecificPasswordRequired": return "Apple requires an app-specific password for this authentication path."
        case "invalidCode": return "The verification code was not accepted. Enter a new code and try again."
        case "rateLimited": return "Too many authentication attempts. Apple is temporarily rate-limiting requests. Wait before trying again."
        case "serviceUnavailable": return "Apple's authentication service did not return a valid response. Try again later."
        case "anisetteIdentityStateInvalid": return LCAnisettePairError.safeMessage
        case "anisetteFailure", "anisette": return "Authentication could not obtain valid Anisette data."
        case "networkFailure", "network": return "Authentication could not reach the required service. Check the connection and try again."
        case "accountRepairRequired": return "Apple requires attention on this account before signing in."
        case "credentialStorage": return "Apple authentication succeeded, but the credentials could not be saved on this device. Reload Account & Signing and review Diagnostics before starting another sign-in."
        case "credentialStorageUncertain": return "Apple authentication succeeded, but the credential save result is uncertain. Reload Account & Signing to reconcile local storage before continuing."
        case "accountIdentityMismatch": return "Use the same Apple ID as the saved account. Reload status if the account changed."
        case "unknown": return "Sign-in failed before completion. Copy Details to help identify the cause."
        case nil: break
        default: break
        }
        let code = failure["code"] as? String ?? ""
        let stage = failure["stage"] as? String ?? ""
        switch code {
        case "invalidCredentials": return "Apple did not accept the Apple ID or password. Check them and try again."
        case "appSpecificPasswordRequired": return "Apple requires an app-specific password for this authentication path."
        case "rateLimited": return "Too many authentication attempts. Apple is temporarily rate-limiting requests. Wait before trying again."
        case "serviceUnavailable": return "Apple's authentication service is temporarily unavailable. Try again later."
        case "anisetteFailure": return "Authentication could not obtain valid Anisette data."
        case "networkFailure": return "Authentication could not reach the required service."
        case "accountRepairRequired": return "Account repair is required. Open the Apple Developer account to resolve."
        default:
            let messages = ["authentication": "Apple ID sign-in failed.",
                           "anisette": "Anisette authentication infrastructure failure.",
                           "network": "Network error during authentication.",
                           "accountRepair": "Account repair required."]
            return messages[stage] ?? "Apple ID sign-in failed."
        }

        }
        return V3AuthFailureDiagnosticsPolicy.display(messageWithoutDiagnosticCode(), failure: failure)
    }

    static func failureDetails(from failure: [String: Any]) -> String {
        V3AuthFailureDiagnosticsPolicy.render(failure,
            underlyingCode: V3ServiceBridge.strictInt(failure["underlyingCode"]),
            retryableValue: V3ServiceBridge.strictBool(failure["retryable"]))
    }

    func answer(promptID: String, answer: [String: String]) {
        guard !promptID.isEmpty, let session,
              V3AuthPromptResponsePolicy.maySubmit(state: state,
                currentPromptID: prompt?["id"] as? String, submittedPromptID: promptID,
                isSubmitting: promptSubmitting, cancellationInProgress: isCancelling),
              provisioningRetryReadinessOwnership.allowsPromptResponse(sessionID: session) else { return }
        if promptResponseBlocked && !["cancel", "changeMethod"].contains(answer["action"] ?? "") { return }
        promptResponseDiagnostics = ""
        promptResponseBlocked = false
        promptResponseGeneration &+= 1
        promptSubmitting = true
        previousFailure = V3AuthPromptFailurePolicy.clearingAfterSubmission(
            previousFailure, promptKind: prompt?["kind"] as? String)
        if prompt?["kind"] as? String == "twoFactor" {
            switch answer["action"] {
            case "trustedDevice":
                twoFactorTransientStep = .deliveryRequested
                deliveryProgressMessage = "Requesting approval from your trusted devices..."
            case "sms", "voice":
                let choices = prompt?["options"] as? [[String: Any]] ?? []
                let phoneCount = choices.filter { ($0["id"] as? String ?? "").hasPrefix("phone:") }.count
                if phoneCount > 1 {
                    twoFactorTransientStep = .choosePhoneNumber
                    deliveryProgressMessage = "Choose a phone number for this verification request..."
                } else if answer["action"] == "voice" {
                    twoFactorTransientStep = .deliveryRequested
                    deliveryProgressMessage = "Requesting a verification call..."
                } else {
                    twoFactorTransientStep = .deliveryRequested
                    deliveryProgressMessage = "Requesting a verification code by SMS..."
                }
            case "resend":
                twoFactorTransientStep = .deliveryRequested
                deliveryProgressMessage = answer["mode"] == "voice" ? "Requesting a verification call..." : "Requesting a verification code by SMS..."
            case "code":
                twoFactorTransientStep = .verifyingCode
                deliveryProgressMessage = "Verifying code..."
            case "changeMethod":
                twoFactorTransientStep = .chooseDeliveryMethod
                deliveryProgressMessage = "Opening verification methods..."
            default:
                if answer["action"]?.hasPrefix("phone:") == true {
                    let mode = answer["mode"] ?? "sms"
                    twoFactorTransientStep = .deliveryRequested
                    deliveryProgressMessage = mode == "voice" ? "Requesting a verification call..." : "Requesting a verification code by SMS..."
                }
            }
        }
        Task {
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "authRespond", target: session,
                    payload: ["prompt": promptID, "answer": answer])
                if V3ServiceBridge.strictBool(reply["responsePending"]) == true {
                    let replyRevision = V3ServiceBridge.strictInt(reply["revision"])
                    let replyPromptID = (reply["prompt"] as? [String: Any])?["id"] as? String
                    if replyPromptID != promptID {
                        guard V3AuthSessionResponsePolicy.mayApplyReply(
                            currentSessionID: self.session,
                            replySessionID: reply["session"] as? String ?? "",
                            cancellationInProgress: self.isCancelling,
                            currentRevision: self.revision,
                            replyRevision: replyRevision) else { return }
                        self.promptResponseGeneration &+= 1
                        apply(reply)
                        return
                    }
                    guard V3AuthSessionResponsePolicy.mayApplyReply(
                        currentSessionID: self.session,
                        replySessionID: reply["session"] as? String ?? "",
                        cancellationInProgress: self.isCancelling,
                        submittedPromptID: promptID,
                        currentPromptID: self.prompt?["id"] as? String,
                        currentRevision: self.revision,
                        replyRevision: replyRevision) else { return }
                    self.promptResponseGeneration &+= 1
                    self.revision = replyRevision ?? self.revision
                    self.promptSubmitting = true
                    self.deliveryProgressMessage = "Your response is being processed..."
                    return
                }
                if V3ServiceBridge.strictBool(reply["promptExpired"]) == true ||
                    reply["state"] as? String == "promptExpired" {
                    guard self.session == session, !self.isCancelling,
                          self.prompt?["id"] as? String == promptID else { return }
                    if let replyRevision = V3ServiceBridge.strictInt(reply["revision"]), replyRevision >= revision {
                        revision = replyRevision
                    }
                    state = "promptExpired"
                    task?.cancel()
                    promptResponseGeneration &+= 1
                    promptSubmitting = false
                    prompt = nil
                    deliveryProgressMessage = ""
                    twoFactorTransientStep = nil
                    message = "That verification session expired. Start a new sign-in to request another verification code." + "\nError ID: SS-AUTH-D041"
                    await reconcile(force: true, expectedSession: session)
                    return
                }
                guard V3AuthSessionResponsePolicy.mayApplyReply(
                    currentSessionID: self.session,
                    replySessionID: reply["session"] as? String ?? "",
                    cancellationInProgress: self.isCancelling,
                    submittedPromptID: promptID,
                    currentPromptID: self.prompt?["id"] as? String,
                    currentRevision: self.revision,
                    replyRevision: V3ServiceBridge.strictInt(reply["revision"])) else { return }
                promptResponseGeneration &+= 1
                apply(reply)
            } catch {
                guard V3AuthPromptSubmissionPolicy.mayShowFailure(
                    currentSessionID: self.session, submittedSessionID: session,
                    currentPromptID: self.prompt?["id"] as? String,
                    submittedPromptID: promptID, cancellationInProgress: self.isCancelling) else { return }
                promptResponseGeneration &+= 1
                promptSubmitting = false
                message = V3AuthPromptResponsePolicy.failureMessage(error)
                promptResponseDiagnostics = V3AuthPromptResponsePolicy.diagnostics(error)
                promptResponseBlocked = V3AuthPromptResponsePolicy.blocksResubmission(error)
            }
        }
    }

    func clearPreviousFailure() {
        previousFailure = V3AuthPromptFailurePolicy.clearingOnDismiss(previousFailure)
    }

    func cancel() {
        guard !(state == "resultUnknown" && session == nil) else {
            reloadAuthoritativeAccountStatus()
            return
        }
        let hasActiveAttempt = ["working", "awaitingPrompt", "promptExpired", "resultUnknown"].contains(state)
        let canRetryCancellation = V3AuthCancellationRetryPolicy.canRetry(
            isCancelling: isCancelling, cancellationConfirmed: cancellationConfirmed,
            hasSession: session != nil)
        guard !isCancelling, hasActiveAttempt || canRetryCancellation else { return }
        reconciliationGate.invalidate()
        cancellationWasAttempted = true
        isCancelling = true
        cancellationConfirmed = false
        if let cancellationMessage = V3AuthCancellationFeedbackPolicy.message(isCancelling: true) {
            message = cancellationMessage
        }
        let oldTask = task
        let oldSession = session
        task?.cancel()
        Task { @MainActor in
            do {
                var terminalReply: [String: Any]?
                if let oldSession {
                    terminalReply = try await V3ServiceBridge.shared.request(operation: "authCancel", target: oldSession)
                }
                await oldTask?.value
                if let terminalReply { apply(terminalReply) }
                await reconcile(force: true)
                cancellationConfirmed = true
                cancellationWasAttempted = false
                if !signedIn, terminalReply == nil {
                    state = "cancelled"
                    message = "Sign-in was cancelled before an authentication session was confirmed."
                }
            } catch {
                await oldTask?.value
                await reconcile(force: true)
                state = "resultUnknown"
                message = signedIn
                    ? "SideStore currently reports an account as signed in, but could not confirm that the sign-in request stopped. Retry Cancellation before starting another attempt." + "\nError ID: SS-AUTH-D092"
                    : "SideStore could not confirm that the sign-in request stopped. Retry Cancellation before starting another attempt." + "\nError ID: SS-AUTH-D093"
            }
            session = cancellationConfirmed ? nil : oldSession
            prompt = nil
            promptSubmitting = false
            task = nil
            isCancelling = false
        }
    }
}

struct V3SignInLink: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    let title: String
    var body: some View {
        NavigationLink {
            V3SignInView().environmentObject(status)
        } label: {
            V3LocalizedLabel(title, systemImage: "person.badge.key.fill")
        }
    }
}

struct V3SignInView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @EnvironmentObject private var sharedModel: SharedModel
    @StateObject private var auth = V3AuthStore()
    @State private var confirmPendingCertificateActivation = false
    var body: some View {
        List {
            if shouldShowAccountSection {
                Section("Apple ID") {
                    accountContent
                }
            }
            if let prompt = auth.prompt {
                V3PromptSection(prompt: prompt, isSubmitting: $auth.promptSubmitting,
                    isSubmissionBlocked: auth.promptResponseBlocked && (prompt["kind"] as? String == "twoFactor"),
                    previousFailureMessage: promptFailureMessage,
                    previousFailureDetails: promptFailureDetails,
                    supplementalContent: AnyView(accountContent),
                    cancellationTitle: auth.isCancelling ? "Cancelling..." :
                        (auth.cancellationWasAttempted ? "Retry Cancellation" : "Cancel Sign In"),
                    cancellationDisabled: auth.isCancelling,
                    onCancel: { auth.cancel() }) { answer in
                    auth.answer(promptID: prompt["id"] as? String ?? "", answer: answer)
                }

            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Sign In")
        .task { await auth.reconcile() }
        .onChange(of: auth.isSignedIn) { isSignedIn in
            guard isSignedIn else { return }
            // The app-owned root observer invalidates certificate-derived
            // readiness from correlated auth terminal events. This snapshot
            // updates the account presentation only.
            status.reload()
        }
        .onDisappear {
            auth.cancel()
            auth.clearPreviousFailure()
            // The root auth-event observer owns certificate-readiness
            // invalidation even after this presentation disappears.
            status.reload()
        }
    }
    @ViewBuilder private var accountContent: some View {
        if auth.prompt == nil || auth.isCancelling {
            HStack {
                Text("Status")
                Spacer()
                V3LocalizedText(statusText).foregroundColor(.secondary)
            }
        }
        if auth.isSignedIn {
            HStack {
                V3LocalizedLabel(V3AuthStatusTextPolicy.accountLabel(state: auth.state, isSignedIn: auth.isSignedIn),
                      systemImage: "checkmark.circle.fill")
                    .foregroundColor(.green)
                Spacer()
                if !auth.team.isEmpty { Text(auth.team).foregroundColor(.secondary) }
            }
            if let jitless = jitlessGuidance {
                VStack(alignment: .leading, spacing: 8) {
                    V3LocalizedLabel(jitless.presentation.title, systemImage: jitless.presentation.icon)
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(jitless.presentation.tint)
                    V3LocalizedText(jitless.presentation.detail)
                        .font(.footnote).foregroundColor(.secondary)
                    if jitless.presentation.isOutstandingSetupTask {
                        switch jitless.action {
                        case .setUp:
                            Button("Continue to JIT-Less Setup") { openJITLessSetup() }
                                .buttonStyle(.borderedProminent)
                        case .refreshCertificate:
                            Button("Refresh JIT-Less Certificate") { openJITLessSetup() }
                                .buttonStyle(.borderedProminent)
                        case .openCertificates:
                            NavigationLink {
                                V3CertificatesView().environmentObject(status)
                            } label: {
                                Label("Open Certificates", systemImage: "doc.text")
                            }
                        case .openSetup:
                            Button("Open JIT-Less Setup") { openJITLessSetup() }
                                .buttonStyle(.borderedProminent)
                        case .none:
                            EmptyView()
                        }
                    } else if jitless.readiness == .ready {
                        NavigationLink {
                            V3HealthView().environmentObject(status).environmentObject(sharedModel)
                        } label: {
                            Label("Review JIT-Less Status", systemImage: "stethoscope")
                        }
                        .font(.caption)
                    }
                }
                .padding(.vertical, 4)
            }
        }
        if !auth.message.isEmpty {
            V3LocalizedText(auth.message)
                .font(.footnote)
                .foregroundColor(auth.state == "resultUnknown" || auth.state == "timedOut" ||
                    auth.state == "cancelled" ? .orange : (auth.isSignedIn ? .green : .red))
                .textSelection(.enabled)
        }
        if V3AuthFailureDiagnosticsPolicy.shouldShowTerminalDetails(
            state: auth.state, hasPrompt: auth.prompt != nil,
            hasFailure: auth.previousFailure != nil),
           let failure = auth.previousFailure {
            VStack(alignment: .leading, spacing: 8) {
                Text("Sign-in diagnostics")
                    .font(.subheadline.weight(.semibold))
                DisclosureGroup("Technical details") {
                    Text(V3AuthStore.failureDetails(from: failure)).environment(\.layoutDirection, .leftToRight)
                        .font(.caption2)
                        .textSelection(.enabled)
                }
                Button("Copy Diagnostics", systemImage: "doc.on.doc") {
                    UIPasteboard.general.string = V3AuthStore.failureDetails(from: failure)
                }
                .font(.caption)
            }
            .padding(.vertical, 4)
        }
        if !auth.currentAttemptFailure.message.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text("Sign-in attempt could not be confirmed")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(.orange)
                V3LocalizedText(auth.currentAttemptFailure.message)
                    .font(.footnote)
                    .foregroundColor(.orange)
                    .textSelection(.enabled)
                if !auth.currentAttemptFailure.technicalDetails.isEmpty {
                    DisclosureGroup("Technical details") {
                        Text(auth.currentAttemptFailure.technicalDetails).environment(\.layoutDirection, .leftToRight)
                            .font(.caption2)
                            .textSelection(.enabled)
                    }
                    Button("Copy Diagnostics") {
                        UIPasteboard.general.string = V3DiagnosticCopy.details(visibleMessage: auth.currentAttemptFailure.message, technical: auth.currentAttemptFailure.technicalDetails)
                    }
                    .font(.caption)
                }
            }
        }
        // V3_PROVISIONING_NEEDS_ATTENTION_V1: the authenticated fact above
        // stays green while the provisioning problem is stated separately.
        if auth.hasProvisioningProblem {
            VStack(alignment: .leading, spacing: 6) {
                Text("Provisioning needs attention")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(.orange)
                Text("Provisioning could not be completed.")
                    .font(.footnote.weight(.medium))
                    .foregroundColor(.orange)
                V3LocalizedText(auth.provisioningMessage)
                    .font(.footnote)
                    .foregroundColor(.orange)
                    .textSelection(.enabled)
                if !auth.provisioningTechnical.isEmpty {
                    DisclosureGroup("Technical details") {
                        Text(auth.provisioningTechnical).environment(\.layoutDirection, .leftToRight)
                            .font(.caption2)
                            .textSelection(.enabled)
                    }
                    HStack {
                        Button("Copy Diagnostics") { UIPasteboard.general.string = V3DiagnosticCopy.details(visibleMessage: auth.provisioningMessage, technical: auth.provisioningTechnical) }
                            .font(.caption)
                        Spacer(minLength: 0)
                    }
                }
            }
        }
        if !auth.deliveryProgressMessage.isEmpty {
            V3LocalizedText(auth.deliveryProgressMessage)
                .font(.footnote.weight(.medium))
                .foregroundColor(.orange)
        } else if let progress = auth.twoFactorTransientStep?.progressLabel {
            V3LocalizedText(progress)
                .font(.footnote.weight(.medium))
                .foregroundColor(.orange)
        }
        if auth.state == "idle" {
            Button { auth.begin() } label: {
                Label("Begin Sign In", systemImage: "person.badge.key.fill")
            }
            .disabled(!auth.canBegin)
        } else if auth.state == "failed" || auth.state == "cancelled" ||
            auth.state == "timedOut" || auth.state == "promptExpired" {
            switch auth.terminalFailureAction {
            case .beginNewSignIn(let title):
                Button { auth.begin() } label: {
                    V3LocalizedLabel(title, systemImage: "person.badge.key.fill")
                }
                .disabled(!auth.canBegin)
                if let guidance = auth.terminalFailureGuidance {
                    V3LocalizedText(guidance).font(.caption).foregroundColor(.secondary)
                }
            case .repairAppleAccount:
                VStack(alignment: .leading, spacing: 8) {
                    V3LocalizedText(auth.terminalFailureGuidance ?? "Resolve the account issue shown by Apple before signing in again.")
                        .font(.footnote).foregroundColor(.orange)
                    Link("Open Apple Account", destination: URL(string: "https://account.apple.com")!)
                    Button("Begin Sign-In After Repair") { auth.begin() }
                        .disabled(!auth.canBegin)
                }
            case .useAppSpecificPassword:
                VStack(alignment: .leading, spacing: 8) {
                    V3LocalizedText(auth.terminalFailureGuidance ?? "Apple requires an app-specific password for this authentication path.")
                        .font(.footnote).foregroundColor(.orange)
                    Link("Open Apple Account", destination: URL(string: "https://account.apple.com")!)
                    Button("Use App-Specific Password") { auth.begin() }
                        .disabled(!auth.canBegin)
                }
            case .blocked:
                V3LocalizedText(auth.terminalFailureGuidance ?? "This failure is not marked safe to retry. Review Diagnostics before another attempt.")
                    .font(.footnote).foregroundColor(.orange)
            }
        }
        if auth.prompt == nil && auth.state != "resultUnknown" && V3AuthCancellationRetryPolicy.canRetry(isCancelling: auth.isCancelling,
            cancellationConfirmed: auth.cancellationConfirmed,
            hasSession: auth.hasSession) {
            Button(LocalizedStringKey(auth.cancellationWasAttempted ? "Retry Cancellation" : "Cancel Unconfirmed Sign-In"),
                   role: .cancel) { auth.cancel() }
        }
        if auth.state == "resultUnknown" {
            switch V3AuthUnknownResultRecoveryPolicy.action(
                isCancelling: auth.isCancelling,
                cancellationConfirmed: auth.cancellationConfirmed,
                hasSession: auth.hasSession) {
            case .cancelSession:
                Button(LocalizedStringKey(auth.cancellationWasAttempted ? "Retry Cancellation" : "Cancel Unconfirmed Sign-In"),
                       role: .cancel) { auth.cancel() }
                    .disabled(auth.isCancelling)
            case .reloadStatus:
                Button(LocalizedStringKey(auth.isCancelling ? "Checking..." : "Reload Status")) {
                    auth.reloadAuthoritativeAccountStatus()
                }
                .disabled(auth.isCancelling)
            case .none:
                EmptyView()
            }
        }
        if auth.prompt == nil && !V3AuthCancellationRetryPolicy.canRetry(isCancelling: auth.isCancelling,
            cancellationConfirmed: auth.cancellationConfirmed,
            hasSession: auth.hasSession) &&
            (auth.state == "working" || auth.state == "awaitingPrompt" ||
             auth.state == "promptExpired") {
            Button(LocalizedStringKey(auth.isCancelling ? "Cancelling..." : "Cancel Sign In"),
                role: .cancel) { auth.cancel() }
                .disabled(auth.isCancelling)
        }
        if auth.pendingCertificateRecoveryState.requiresAttention && auth.prompt == nil {
            VStack(alignment: .leading, spacing: 8) {
                Text("Pending Certificate Recovery")
                    .font(.subheadline.weight(.semibold)).foregroundColor(.orange)
                V3LocalizedText(auth.pendingCertificateRecoveryState.guidance)
                    .font(.footnote).fixedSize(horizontal: false, vertical: true)
                if auth.pendingCertificateRecoveryState == .ready {
                    Button(LocalizedStringKey(auth.checkingProvisioningStorage ? "Checking Saved State..." : "Reconcile Pending Certificate")) {
                        auth.checkProvisioningStorage()
                    }
                    .disabled(!auth.canCheckPendingCertificateRecovery)
                    .accessibilityIdentifier("signin.pending-certificate-reconcile")
                }
                if auth.pendingCertificateRecoveryState == .activationRequired {
                    Button("Sign In Again to Finish Setup") { confirmPendingCertificateActivation = true }
                        .disabled(!auth.canFinishPendingCertificateActivation)
                        .accessibilityIdentifier("signin.pending-certificate-activate")
                        .confirmationDialog("Activate Recovered Certificate?", isPresented: $confirmPendingCertificateActivation,
                            titleVisibility: .visible) {
                            Button("Use Recovered Certificate") { auth.finishPendingCertificateActivation() }
                            Button("Cancel", role: .cancel) {}
                        } message: {
                            Text("The recovered certificate will become active and replace the current active certificate. Existing certificates will be kept.")
                        }
                }
                Button("Reload Status") { auth.reloadAuthoritativeAccountStatus() }
                    .disabled(auth.isCancelling || auth.checkingProvisioningStorage || auth.hasSession)
            }
            .accessibilityIdentifier("signin.pending-certificate-recovery")
        }
        if auth.provisioningRecoveryRequiresReconciliation {
            Button(LocalizedStringKey(auth.checkingProvisioningStorage ? "Checking Saved State..." : "Check Saved Signing State")) {
                auth.checkProvisioningStorage()
            }
            .disabled(!auth.canCheckProvisioningStorage)
        }
        // V3_PROVISIONING_RECOVERY_ACTIONS_V1: the actions describe the
        // provisioning state, not a failed sign-in. "Retry" re-enters
        // provisioning with the saved session; "Finish Later" keeps the
        // authenticated account and closes this flow.
        if auth.hasProvisioningProblem {
            let recovery = auth.provisioningRecoveryActions
            if recovery.showCancellationInstruction {
                Text("Cancel the unconfirmed sign-in before retrying provisioning.")
                    .font(.caption).foregroundColor(.secondary)
            }
            if recovery.showRetryProvisioning || auth.state != "resultUnknown" {
                Button {
                    auth.retryProvisioning()
                } label: {
                    Label("Retry Provisioning", systemImage: "arrow.clockwise")
                }
                .disabled(!auth.canRetryProvisioning)
            }
            if recovery.showReauthenticateProvisioning && auth.pendingCertificateRecoveryState != .activationRequired {
                Button("Sign In Again to Finish Setup") { auth.reauthenticateProvisioning() }
                    .disabled(!auth.canReauthenticateProvisioning)
            }
            if auth.provisioningRecoveryRequiresReconciliation {
                V3LocalizedText("A local account or certificate save could not be verified. Setup is blocked until that saved state is repaired; another sign-in cannot safely retry it." + "\nError ID: SS-SAVE-D042")
                    .font(.caption).foregroundColor(.secondary)
            } else if auth.provisioningSessionUnavailable {
                Text("Sign in again with the same Apple ID to finish setup. Your account and certificate are kept.")
                    .font(.caption).foregroundColor(.secondary)
            }
            if recovery.blockedByActiveSession {
                Text("Another sign-in or provisioning attempt is still active. Wait for it to finish, then reload status.")
                    .font(.caption).foregroundColor(.secondary)
            }
            if recovery.showFinishLater {
                Button("Finish Later") { finishProvisioningLater() }
            }
        }

        if auth.prompt != nil && !auth.promptResponseDiagnostics.isEmpty {
            DisclosureGroup("Verification response details") {
                Text(auth.promptResponseDiagnostics).environment(\.layoutDirection, .leftToRight)
                    .font(.caption2).textSelection(.enabled)
            }
            Button("Copy Verification Details", systemImage: "doc.on.doc") {
                UIPasteboard.general.string = auth.promptResponseDiagnostics
            }
            .font(.caption)
        }
    }
    // The prompt panel owns input, prior-error guidance and cancellation for
    // every sign-in prompt, not just provisioning recovery. Existing account
    // facts, progress and recovery controls render inside that same panel.
    private var shouldShowAccountSection: Bool { auth.prompt == nil }
    private var visiblePromptFailure: [String: Any]? {
        V3AuthPromptFailurePolicy.isVisible(auth.previousFailure,
            promptKind: auth.prompt?["kind"] as? String) ? auth.previousFailure : nil
    }
    private var promptFailureMessage: String {
        visiblePromptFailure.map { V3AuthStore.failureMessage(from: $0) } ?? ""
    }
    private var promptFailureDetails: String {
        visiblePromptFailure.map { V3AuthStore.failureDetails(from: $0) } ?? ""
    }
    private var statusText: String {
        V3AuthCancellationFeedbackPolicy.statusLabel(isCancelling: auth.isCancelling,
            normalLabel: V3AuthStatusTextPolicy.label(state: auth.state, isSignedIn: auth.isSignedIn,
                provisioningFinishedLater: auth.provisioningFinishedLater))
    }
    private var jitlessGuidance: V3SignInJITLessGuidance? {
        V3SignInJITLessGuidancePolicy.resolve(
            osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion,
            readiness: status.jitlessReadiness)
    }

    // V3_FINISH_LATER_PRESERVES_ACCOUNT_V1: closing the flow reloads the
    // authoritative SideStore snapshot so the account is shown as signed in
    // again. It never signs out and never discards the saved session.
    private func finishProvisioningLater() {
        auth.finishProvisioningLater()
        status.reload()
        dismiss()
    }

    private func openJITLessSetup() {
        if status.setupPresented || status.signInPresented {
            status.pendingCanonicalJITLessSetup = true
            status.returnToSetupAfterJITLess = status.setupPresented
            status.setupPresented = false
            status.signInPresented = false
        } else {
            sharedModel.selectedTab = .settings
            sharedModel.deepLink = URL(string: "livecontainer://jitless-setup")
        }
    }
}

// V3_CERTIFICATE_CREATE_PRESENTATION_V1: keep the partial remote-success
// case explicit so users are not prompted to create a duplicate certificate.
enum V3CertificateCreatePresentation {
    static func message(for outcome: String?) -> String {
        if isVerified(outcome) { return "Certificate created and saved." }
        switch outcome {
        case "remoteCreatedLocalStorageUnverified":
            return "Apple created the certificate, but its local signing copy could not be verified. Open Certificates and reload before creating another certificate." + "\nError ID: SS-SAVE-D090"
        default:
            return "Certificate creation finished, but its local signing copy could not be confirmed. Open Certificates and reload before creating another certificate." + "\nError ID: SS-SAVE-D091"
        }
    }

    static func isVerified(_ outcome: String?) -> Bool {
        outcome == "createdAndStored"
    }
}

struct V3CertificateRow: Identifiable {
    let serial: String, name: String, machine: String, email: String
    let active: Bool
    let created: Date?
    let expiry: Date?
    var id: String { serial }
    init?(_ row: [String: Any]) {
        guard let serial = row["serial"] as? String, !serial.isEmpty else { return nil }
        self.serial = serial; name = row["name"] as? String ?? serial
        machine = row["machineName"] as? String ?? ""; email = row["requesterEmail"] as? String ?? ""
        active = row["active"] as? Bool ?? false
        created = row["created"] as? Date; expiry = row["expiry"] as? Date
    }
}

struct V3CertificatesView: View {
    @Environment(\.locale) private var dateLocale

    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var local: [V3CertificateRow] = []
    @State private var portal: [V3CertificateRow] = []
    @State private var loading = true
    @State private var portalLoaded = false
    @State private var message = ""
    @State private var notice = ""
    @State private var busy = ""
    @State private var loadingRequest = false
    @State private var portalTicket: UInt64 = 0
    @State private var confirm: (String, String)?
    var body: some View {
        List {
            if !message.isEmpty {
                Section {
                    V3LocalizedText(message).font(.footnote).foregroundColor(.red).textSelection(.enabled)
                }
            }
            if !notice.isEmpty {
                Section {
                    V3LocalizedText(notice).font(.footnote).foregroundColor(.secondary)
                }
            }
            if status.needsSignIn {
                Section {
                    V3SignInLink(title: "Sign In to Manage Certificates")
                }
            }
            Section(V3Localization.text("On This Device (\(local.count))")) {
                if loading { ProgressView("Loading certificates...") }
                ForEach(local) { cert in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(cert.name).font(.headline)
                            Spacer()
                            if cert.active {
                                Text("Active").font(.caption.weight(.bold)).foregroundColor(.green)
                            }
                        }
                        Text(cert.serial).font(.caption).foregroundColor(.secondary).textSelection(.enabled)
                        if let expiry = cert.expiry {
                            Text(V3Localization.format("Expires %@", V3Localization.date(expiry, date: .abbreviated, time: .omitted, language: V3AppLanguage.resolve(dateLocale.identifier))))
                                .font(.caption).foregroundColor(.secondary)
                        }
                        HStack {
                            if !cert.active {
                                Button(LocalizedStringKey(busy == cert.serial ? "Working..." : "Set Active")) { setActive(serial: cert.serial) }
                                    .font(.caption)
                                    .disabled(!busy.isEmpty)
                            }
                            Spacer()
                            Button("Delete", role: .destructive) { confirm = ("delete", cert.serial) }
                                .font(.caption)
                                .disabled(!busy.isEmpty)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
            Section("Developer Portal") {
                if !portalLoaded {
                    Button(LocalizedStringKey(busy == "portal" ? "Loading Portal Certificates..." : "Load Portal Certificates")) { Task { await loadPortal() } }
                        .disabled(!busy.isEmpty)
                } else {
                    ForEach(portal) { cert in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(cert.name).font(.headline)
                            Text(cert.serial).font(.caption).foregroundColor(.secondary).textSelection(.enabled)
                            if let expiry = cert.expiry {
                                Text(V3Localization.format("Expires %@", V3Localization.date(expiry, date: .abbreviated, time: .omitted, language: V3AppLanguage.resolve(dateLocale.identifier))))
                                    .font(.caption).foregroundColor(.secondary)
                            }
                            Button("Revoke", role: .destructive) { confirm = ("revoke", cert.serial) }
                                .font(.caption)
                                .disabled(!busy.isEmpty)
                        }
                        .padding(.vertical, 4)
                    }
                    Button("Request New Certificate") { confirm = ("create", "") }
                        .disabled(!busy.isEmpty)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Certificates")
        .task { await reload() }
        .onChange(of: status.identityStamp) { _ in
            portalTicket &+= 1
            portal = []
            portalLoaded = false
        }
        .onChange(of: status.authenticated) { authenticated in
            portalTicket &+= 1
            portal = []
            portalLoaded = false
            if !authenticated { busy = "" }
        }
        .onChange(of: status.authenticationActive) { active in
            portalTicket &+= 1
            if active {
                portal = []
                portalLoaded = false
                busy = ""
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3AuthIdentityTransition"))) { _ in
                portalTicket &+= 1
                portal = []
                portalLoaded = false
            }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3AuthIdentityTransitionFinished"))) { _ in
                if status.authenticated { Task { await reload() } }
            }
        .onDisappear {
            portalTicket &+= 1
        }
        .confirmationDialog("Are you sure?", isPresented: Binding(get: { confirm != nil }, set: { if !$0 { confirm = nil } }), titleVisibility: .visible) {
            Button("Confirm", role: .destructive) {
                if let action = confirm { Task { await runConfirmed(action: action.0, serial: action.1) } }
            }
            Button("Cancel", role: .cancel) { confirm = nil }
        } message: {
            Text("Revoking or deleting a certificate affects every app signed with it.")
        }
    }
    private func reload() async {
        guard !loadingRequest else { return }
        loadingRequest = true
        loading = true
        defer { loading = false; loadingRequest = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "certList")
            local = (reply["certificates"] as? [[String: Any]] ?? []).compactMap(V3CertificateRow.init)
            message = ""
        } catch { message = V3FailureGuidance.message(error) }
    }
    private func loadPortal() async {
        portalTicket &+= 1
        let ticket = portalTicket
        let stamp = status.identityStamp
        guard status.authenticated, let stamp else { return }
        busy = "portal"
        notice = ""
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "certPortalList")
            let currentIdentity = try await V3ServiceBridge.shared.request(operation: "snapshot")
            guard V3AuthReadStampPolicy.ownsTicket(captured: ticket, current: portalTicket) else { return }
            guard V3ServiceBridge.strictBool(currentIdentity["authenticated"]) == true,
                  V3ServiceBridge.strictBool(currentIdentity["identityStable"]) == true,
                  V3ServiceBridge.strictBool(currentIdentity["authenticationActive"]) != true,
                  currentIdentity["identityStamp"] as? String == stamp else {
                busy = ""
                return
            }
            guard V3AuthReadStampPolicy.mayCommit(capturedTicket: ticket,
                  currentTicket: portalTicket, capturedStamp: stamp,
                  currentStamp: status.identityStamp,
                  stable: V3ServiceBridge.strictBool(reply["identityStable"]) == true,
                  resultStamps: [reply["identityStamp"] as? String],
                  authenticationActive: status.authenticationActive) else {
                busy = ""
                return
            }
            portal = (reply["certificates"] as? [[String: Any]] ?? []).compactMap(V3CertificateRow.init)
            portalLoaded = true
            message = ""
            busy = ""
        } catch {
            guard ticket == portalTicket else { return }
            busy = ""
            message = V3FailureGuidance.message(error)
        }
    }
    private func setActive(serial: String) {
        busy = serial
        notice = ""
        Task {
            defer { busy = "" }
            let mutationTicket = status.beginDirectMutation()
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "certSetActive", target: serial)
                status.finishDirectMutation(ticket: mutationTicket, reply: reply)
                status.invalidateSetupFacts()
                status.reload()
                await reload()
                notice = "Active certificate updated."
            } catch {
                status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
                message = V3FailureGuidance.message(error)
            }
        }
    }
    private func runConfirmed(action: String, serial: String) async {
        confirm = nil
        busy = action + serial
        notice = ""
        defer { busy = "" }
        let mutationTicket = status.beginDirectMutation()
        do {
            var certificateCreateOutcome: String?
            var mutationSnapshot: [String: Any]?
            switch action {
            case "delete": mutationSnapshot = try await V3ServiceBridge.shared.request(operation: "certDelete", target: serial)
            case "revoke":
                mutationSnapshot = try await V3ServiceBridge.shared.request(operation: "certRevoke", target: serial)
                if let mutationSnapshot {
                    _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                        mutationSnapshot, operation: "certRevoke")
                }
            default:
                let reply = try await V3ServiceBridge.shared.request(operation: "certCreate")
                certificateCreateOutcome = reply["outcome"] as? String
                _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                    reply, operation: "certCreate")
            }
            status.finishDirectMutation(ticket: mutationTicket, reply: mutationSnapshot)
            // Certificate mutations can invalidate the cached JIT-Less
            // comparison. Ordinary snapshots do not carry that private fact,
            // so force a fresh authoritative observation before setup reuses it.
            status.invalidateSetupFacts()
            status.reload()
            await reload()
            portalLoaded = false
            message = ""
            switch action {
            case "delete": notice = "Certificate deleted."
            case "revoke": notice = "Certificate revoked."
            default:
                notice = V3CertificateCreatePresentation.message(for: certificateCreateOutcome)
            }
        } catch {
            status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
            message = V3FailureGuidance.message(error)
        }
    }
}

struct V3DeveloperServicesView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var teams: [[String: String]] = []
    @State private var devices: [[String: String]] = []
    @State private var appIDs: [[String: String]] = []
    @State private var groups: [[String: String]] = []
    @State private var profiles: [[String: Any]] = []
    @State private var message = ""
    @State private var loading = true
    @State private var reloadTicket: UInt64 = 0
    var body: some View {
        List {
            if !message.isEmpty {
                Section { V3LocalizedText(message).font(.footnote).foregroundColor(.red).textSelection(.enabled) }
            }
            if status.needsSignIn {
                Section {
                    V3SignInLink(title: "Sign In to Load Developer Data")
                }
            }
            Section("Actions") {
                Button { status.syncAppIDs() } label: { Label("Sync App IDs", systemImage: "arrow.triangle.2.circlepath") }
                    .disabled(!V3DeveloperDataActionAvailabilityPolicy.isEnabled(
                        authenticated: status.authenticated, isLoading: status.loading || loading))
                    .accessibilityHint("Sign in with Apple ID before syncing App IDs.")
                Button(LocalizedStringKey(loading ? "Loading Developer Data..." : "Reload Developer Data")) { Task { await reload() } }
                    .disabled(!V3DeveloperDataActionAvailabilityPolicy.isEnabled(
                        authenticated: status.authenticated, isLoading: loading || status.loading))
                    .accessibilityHint("Sign in with Apple ID before loading developer data.")
            }
            simpleSection("Teams", rows: teams.map { "\($0["name"] ?? "") (\($0["identifier"] ?? ""))" })
            simpleSection("Devices", rows: devices.map { "\($0["name"] ?? "") · \($0["identifier"] ?? "")" })
            simpleSection("App IDs", rows: appIDs.map { "\($0["name"] ?? "") · \($0["bundleID"] ?? "")" })
            simpleSection("App Groups", rows: groups.map { "\($0["name"] ?? "") · \($0["identifier"] ?? "")" })
            Section(V3Localization.text("Provisioning Profiles (\(profiles.count))")) {
                if loading { ProgressView() }
                ForEach(profiles.indices, id: \.self) { index in
                    let row = profiles[index]
                    let name = row["name"] as? String ?? row["profileName"] as? String ?? V3Localization.string("Profile")
                    let detail = row["bundleID"] as? String ?? row["bundleIdentifier"] as? String ?? ""
                    VStack(alignment: .leading, spacing: 2) {
                        Text(name).font(.headline)
                        if !detail.isEmpty {
                            Text(detail)
                                .font(.caption).foregroundColor(.secondary)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Developer Services")
        .task { await reload() }
        .onChange(of: status.identityStamp) { _ in
            invalidateScopedRows()
            if status.authenticated { Task { await reload() } }
        }
        .onChange(of: status.authenticated) { authenticated in
            invalidateScopedRows()
            if authenticated { Task { await reload() } }
        }
        .onChange(of: status.authenticationActive) { active in
            if active { invalidateScopedRows() }
            else if status.authenticated { Task { await reload() } }
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3AuthIdentityTransition"))) { _ in
                invalidateScopedRows()
            }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3AuthIdentityTransitionFinished"))) { _ in
                if status.authenticated { Task { await reload() } }
            }
        .onDisappear {
            reloadTicket &+= 1
        }
    }
    private func simpleSection(_ title: String, rows: [String]) -> some View {
        Section(V3Localization.format("%@ (%@)", V3Localization.text(title), String(rows.count))) {
            if loading { ProgressView() }
            if rows.isEmpty && !loading {
                Text("None").foregroundColor(.secondary)
            }
            ForEach(rows, id: \.self) { row in
                Text(row).font(.subheadline).textSelection(.enabled)
            }
        }
    }
    private func strings(_ reply: [String: Any], key: String) -> [[String: String]] {
        (reply[key] as? [[String: Any]] ?? []).map { row in
            Dictionary(uniqueKeysWithValues: row.compactMap { k, v in (v as? String).map { (k, $0) } })
        }
    }
    private func reload() async {
        reloadTicket &+= 1
        let ticket = reloadTicket
        let capturedStamp = status.identityStamp
        guard status.authenticated else {
            loading = false
            return
        }
        guard let capturedStamp else { loading = false; return }
        loading = true
        do {
            async let teamsReply = V3ServiceBridge.shared.request(operation: "devTeams")
            async let devicesReply = V3ServiceBridge.shared.request(operation: "devDevices")
            async let appIDsReply = V3ServiceBridge.shared.request(operation: "devAppIDs")
            async let groupsReply = V3ServiceBridge.shared.request(operation: "devGroups")
            async let profilesReply = V3ServiceBridge.shared.request(operation: "devProfiles")
            let (teamsResult, devicesResult, appIDsResult, groupsResult, profilesResult) =
                try await (teamsReply, devicesReply, appIDsReply, groupsReply, profilesReply)
            let currentIdentity = try await V3ServiceBridge.shared.request(operation: "snapshot")
            let replies = [teamsResult, devicesResult, appIDsResult, groupsResult, profilesResult]
            guard V3AuthReadStampPolicy.ownsTicket(captured: ticket, current: reloadTicket) else { return }
            guard status.authenticated,
                  V3ServiceBridge.strictBool(currentIdentity["authenticated"]) == true,
                  V3ServiceBridge.strictBool(currentIdentity["identityStable"]) == true,
                  V3ServiceBridge.strictBool(currentIdentity["authenticationActive"]) != true,
                  currentIdentity["identityStamp"] as? String == capturedStamp,
                  V3AuthReadStampPolicy.mayCommit(
                  capturedTicket: ticket, currentTicket: reloadTicket,
                  capturedStamp: capturedStamp, currentStamp: status.identityStamp,
                  stable: replies.allSatisfy({ V3ServiceBridge.strictBool($0["identityStable"]) == true }),
                  resultStamps: replies.map { $0["identityStamp"] as? String },
                  authenticationActive: status.authenticationActive) else {
                if V3AuthReadStampPolicy.ownsTicket(captured: ticket, current: reloadTicket) {
                    loading = false
                }
                return
            }
            teams = strings(teamsResult, key: "teams")
            devices = strings(devicesResult, key: "devices")
            appIDs = strings(appIDsResult, key: "appIDs")
            groups = strings(groupsResult, key: "groups")
            profiles = profilesResult["profiles"] as? [[String: Any]] ?? []
            message = ""
            loading = false
        } catch {
            guard ticket == reloadTicket else { return }
            message = V3FailureGuidance.message(error)
            loading = false
        }
    }
    private func invalidateScopedRows() {
        reloadTicket &+= 1
        teams = []
        devices = []
        appIDs = []
        groups = []
        profiles = []
        message = ""
        loading = false
    }
}

struct V3FilePicker: UIViewControllerRepresentable {
    let types: [String]
    let completion: (URL?) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(completion: completion) }
    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: types.map { UTType($0) ?? .data }, asCopy: true)
        picker.allowsMultipleSelection = false
        picker.delegate = context.coordinator
        return picker
    }
    func updateUIViewController(_ controller: UIDocumentPickerViewController, context: Context) {}
    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        private var completion: ((URL?) -> Void)?
        init(completion: @escaping (URL?) -> Void) { self.completion = completion }
        private func finish(_ url: URL?) {
            let callback = completion
            completion = nil
            callback?(url)
        }
        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) { finish(urls.first) }
        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { finish(nil) }
    }
}

struct V3ActivitySheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

struct V3PairingView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var pickerPresented = false
    @State private var message = ""
    @State private var pairingFailure: CombinedFailure?
    @State private var working = false
    var body: some View {
        List {
            Section("Status") {
                HStack {
                    Text("Pairing Status")
                    Spacer()
                    V3LocalizedText(V3PairingPresentationPolicy.displayText(statusConnected: status.connected,
                        pairingStatus: status.pairing)).foregroundColor(.secondary)
                }
                if !message.isEmpty {
                    V3LocalizedText(message).font(.footnote).foregroundColor(.red).textSelection(.enabled)
                }
            }
            if let failure = pairingFailure {
                Section("Pairing file could not be read or validated") {
                    V3LocalizedText(failure.safeMessage)
                        .font(.footnote)
                        .foregroundColor(.red)
                        .textSelection(.enabled)
                    Text("What you can do")
                        .font(.caption.weight(.semibold))
                    V3LocalizedText(failure.recovery)
                        .font(.footnote)
                    DisclosureGroup("Technical details") {
                        Text(failure.technicalDetails).environment(\.layoutDirection, .leftToRight)
                            .font(.caption2)
                            .textSelection(.enabled)
                    }
                    Button("Copy Diagnostics", systemImage: "doc.on.doc") {
                        UIPasteboard.general.string = failure.technicalDetails
                    }
                    if V3PairingImportFailurePolicy.shouldOfferFileRetry(operation: failure.operation,
                            stage: failure.stage.rawValue, safeCause: failure.safeCause?.rawValue) {
                        Button("Choose Pairing File Again") { pickerPresented = true }
                    }
                }
            }
            // V3_PAIRING_PLACEMENT_FIRST_V1: the pairing mechanism works. The
            // normal installation workflow places the pairing file with the tool
            // that installed LC+SS, so that is the recommended path. Manual import
            // remains available as a clearly secondary fallback.
            if pairingMissing {
                Section("Pairing File Required") {
                    Text("Recommended setup")
                        .font(.footnote.weight(.semibold))
                    Text("If you installed with iLoader:")
                        .font(.footnote).foregroundColor(.secondary)
                    ForEach(Array(Self.pairingPlacementSteps.enumerated()), id: \.offset) { index, step in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text("\(index + 1).").font(.caption).foregroundColor(.secondary)
                            V3LocalizedText(step).font(.footnote)
                        }
                    }
                    Text("Other installation tools use different menu names, but they all place the pairing file for the installed app.")
                        .font(.caption).foregroundColor(.secondary)
                    Button {
                        recheck()
                    } label: {
                        Label("Re-check Pairing", systemImage: "arrow.clockwise")
                    }
                    .disabled(working)
                }
            } else if pairingState == .unknown {
                Section("Pairing Status Unknown") {
                    V3LocalizedText("LiveContainer could not confirm the pairing-file state. Reload status before treating setup as complete." + "\nError ID: SS-PAIR-D043")
                        .font(.footnote).foregroundColor(.secondary)
                    Button {
                        recheck()
                    } label: {
                        Label("Re-check Pairing", systemImage: "arrow.clockwise")
                    }
                    .disabled(working)
                }
            } else {
                Section("Pairing File Ready") {
                    Text("A valid pairing file is available. Re-check if you replace the file or reset the device.")
                        .font(.footnote).foregroundColor(.secondary)
                    Button {
                        recheck()
                    } label: {
                        Label("Re-check Pairing", systemImage: "arrow.clockwise")
                    }
                    .disabled(working)
                }
            }
            // V3_PAIRING_IMPORT_IS_FALLBACK_V1: kept, presented as an alternative.
            Section {
                Text("Alternative: Import Pairing File Manually")
                    .font(.footnote.weight(.semibold))
                Button {
                    pickerPresented = true
                } label: {
                    V3LocalizedLabel(working ? "Importing..." : "Import Pairing File Manually", systemImage: "doc.badge.plus")
                }
                .disabled(working)
                Text("Use this only if the placement tool did not work. Pick a .mobiledevicepairing or .plist file. This screen owns the picker; the service only validates and stores the file.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Pairing File")
        .sheet(isPresented: $pickerPresented) {
            V3FilePicker(types: ["com.apple.property-list", "public.xml", "public.data"]) { url in
                pickerPresented = false
                if let url { Task { await importFile(url) } }
            }
        }
    }
    // V3_PAIRING_PLACEMENT_STEPS_V1: the documented iLoader placement flow.
    // It is presented as "If you installed with iLoader", because other
    // third-party installers do not necessarily use the same menu names.
    static let pairingPlacementSteps = [
        "Connect the iPhone to the computer if your installation tool requires it.",
        "Open the tool you used to install LC+SS.",
        "Open Management.",
        "Open Manage Pairing File.",
        "If LC+SS is not listed, use Rescan Installed Apps.",
        "Find the installed LC+SS app.",
        "Choose Place for this app.",
        "Wait for the tool to confirm success.",
        "Return to LC+SS."
    ]

    // V3_REFRESH_PREREQUISITE_POLICY_V1: one interpretation, shared with every
    // refresh entry point.
    private var pairingMissing: Bool {
        pairingState == .unsatisfied
    }

    private var pairingState: V3RefreshPrerequisiteState {
        V3PairingPresentationPolicy.state(statusConnected: status.connected, pairingStatus: status.pairing)
    }

    private func recheck() {
        pairingFailure = nil
        message = ""
        status.reload()
    }

    private func importFile(_ url: URL) async {
        working = true
        message = ""
        pairingFailure = nil
        defer { working = false }
        var mutationTicket: UUID?
        do {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data: Data
            do {
                data = try await V3SharedFileInput.readBoundedAsync(url)
            } catch {
                pairingFailure = CombinedFailure(operation: "pairingImportData", stage: .filePreparation,
                    code: .fileAccess, id: UUID().uuidString, underlying: error,
                    retryable: false, safeCause: .pairingFilePreparationFailed)
                return
            }
            guard let token = await status.stageSharedFile(data, purpose: "pairing") else {
                // stageSharedFile presents its own bounded capacity/preparation
                // failure. Do not relabel that prerequisite as a bad pairing file.
                return
            }
            defer { status.discardSharedFile(token) }
            mutationTicket = status.beginDirectMutation()
            let result = try await V3ServiceBridge.shared.request(operation: "pairingImportData", target: token)
            status.finishDirectMutation(ticket: mutationTicket!, reply: result)
            _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                result, operation: "pairingImportData")
            mutationTicket = nil
            message = ""
        } catch {
            // A failed import may have crossed the service mutation boundary.
            if let mutationTicket {
                status.finishDirectMutation(ticket: mutationTicket, requestReload: true)
            }
            if let failure = error as? CombinedFailure {
                if V3PairingImportFailurePolicy.shouldOfferFileRetry(operation: failure.operation,
                        stage: failure.stage.rawValue, safeCause: failure.safeCause?.rawValue) {
                    pairingFailure = failure
                } else {
                    status.present(failure)
                }
            } else {
                message = V3FailureGuidance.message(error)
            }
        }
    }
}

@MainActor
final class V3SettingsStore: ObservableObject {
    @Published var bools: [String: Bool] = [:]
    @Published var strings: [String: String] = [:]
    @Published var ints: [String: Int] = [:]
    @Published var loaded = false
    @Published var message = ""
    private var writeGenerations = V3SettingsWriteGeneration()
    private var loadingRequest = false
    private var pendingWriteReconciliation: Set<String> = []
    private var confirmedBools: [String: Bool] = [:]
    private var confirmedStrings: [String: String] = [:]
    private var confirmedInts: [String: Int] = [:]
    func load() async {
        guard !loadingRequest else { return }
        loadingRequest = true
        let capturedWrites = writeGenerations
        defer { loadingRequest = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "settingsGet")
            let readBools = reply["bools"] as? [String: Bool] ?? [:]
            let readStrings = reply["strings"] as? [String: String] ?? [:]
            let readInts = reply["ints"] as? [String: Int] ?? [:]
            bools = writeGenerations.mergingSnapshot(readBools, into: bools, captured: capturedWrites)
            strings = writeGenerations.mergingSnapshot(readStrings, into: strings, captured: capturedWrites)
            ints = writeGenerations.mergingSnapshot(readInts, into: ints, captured: capturedWrites)
            confirmedBools = writeGenerations.mergingSnapshot(readBools, into: confirmedBools, captured: capturedWrites)
            confirmedStrings = writeGenerations.mergingSnapshot(readStrings, into: confirmedStrings, captured: capturedWrites)
            confirmedInts = writeGenerations.mergingSnapshot(readInts, into: confirmedInts, captured: capturedWrites)
            loaded = true
            if writeGenerations.isUnchanged(since: capturedWrites) { message = "" }
        } catch {
            if writeGenerations.isUnchanged(since: capturedWrites) {
                message = V3FailureGuidance.message(error)
            }
        }
    }
    func setBool(_ key: String, _ value: Bool) {
        let generation = writeGenerations.begin(key)
        bools[key] = value
        Task {
            defer { finishWrite(generation, key: key, type: "bool") }
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                    payload: ["key": key, "type": "bool", "bool": value])
                _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                    reply, operation: "settingsSet")
                if writeGenerations.isCurrent(generation, for: key) {
                    confirmedBools[key] = value
                } else {
                    pendingWriteReconciliation.insert(key)
                }
            } catch {
                guard writeGenerations.isCurrent(generation, for: key) else {
                    pendingWriteReconciliation.insert(key)
                    return
                }
                let loaded = await reloadAuthoritative(key: key, type: "bool", generation: generation)
                guard writeGenerations.isCurrent(generation, for: key) else {
                    pendingWriteReconciliation.insert(key)
                    return
                }
                if !loaded, writeGenerations.isCurrent(generation, for: key) {
                    if let confirmed = confirmedBools[key] { bools[key] = confirmed }
                    else { bools.removeValue(forKey: key) }
                }
                message = V3FailureGuidance.message(error)
            }
        }
    }
    func setString(_ key: String, _ value: String) {
        Task { await setStringAndWait(key, value) }
    }
    func setStringAndWait(_ key: String, _ value: String) async {
        let generation = writeGenerations.begin(key)
        defer { finishWrite(generation, key: key, type: "string") }
        strings[key] = value
        message = ""
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                payload: ["key": key, "type": "string", "string": value])
            _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                reply, operation: "settingsSet")
            if writeGenerations.isCurrent(generation, for: key) {
                confirmedStrings[key] = value
            } else {
                pendingWriteReconciliation.insert(key)
            }
        } catch {
            guard writeGenerations.isCurrent(generation, for: key) else {
                pendingWriteReconciliation.insert(key)
                return
            }
            let loaded = await reloadAuthoritative(key: key, type: "string", generation: generation)
            guard writeGenerations.isCurrent(generation, for: key) else {
                pendingWriteReconciliation.insert(key)
                return
            }
            if !loaded, writeGenerations.isCurrent(generation, for: key) {
                if let confirmed = confirmedStrings[key] { strings[key] = confirmed }
                else { strings.removeValue(forKey: key) }
            }
            message = V3FailureGuidance.message(error)
        }
    }
    func setInt(_ key: String, _ value: Int) {
        let generation = writeGenerations.begin(key)
        ints[key] = value
        Task {
            defer { finishWrite(generation, key: key, type: "int") }
            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "settingsSet",
                    payload: ["key": key, "type": "int", "int": value])
                _ = await V3ServiceBridge.shared.acknowledgeDirectRecoveryAfterSuccess(
                    reply, operation: "settingsSet")
                if writeGenerations.isCurrent(generation, for: key) {
                    confirmedInts[key] = value
                } else {
                    pendingWriteReconciliation.insert(key)
                }
            } catch {
                guard writeGenerations.isCurrent(generation, for: key) else {
                    pendingWriteReconciliation.insert(key)
                    return
                }
                let loaded = await reloadAuthoritative(key: key, type: "int", generation: generation)
                guard writeGenerations.isCurrent(generation, for: key) else {
                    pendingWriteReconciliation.insert(key)
                    return
                }
                if !loaded, writeGenerations.isCurrent(generation, for: key) {
                    if let confirmed = confirmedInts[key] { ints[key] = confirmed }
                    else { ints.removeValue(forKey: key) }
                }
                message = V3FailureGuidance.message(error)
            }
        }
    }
    private func finishWrite(_ generation: UInt64, key: String, type: String) {
        writeGenerations.finish(generation, for: key)
        guard pendingWriteReconciliation.contains(key),
              !writeGenerations.hasPendingWrites(for: key) else { return }
        pendingWriteReconciliation.remove(key)
        let settledGeneration = writeGenerations.current(for: key)
        Task {
            // Reply order is not backend commit order. Once all writes settle,
            // read the saved value instead of trusting a delayed newer reply.
            let verified = await reloadAuthoritative(key: key, type: type, generation: settledGeneration)
            if !verified, writeGenerations.isCurrent(settledGeneration, for: key) {
                pendingWriteReconciliation.insert(key)
                message = "The saved setting could not be verified. Reload settings to check its value." + "\nError ID: SS-SAVE-D018"
            }
        }
    }

    private func reloadAuthoritative(key: String, type: String, generation: UInt64) async -> Bool {
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "settingsGet")
            guard writeGenerations.isCurrent(generation, for: key) else { return false }
            switch type {
            case "bool":
                let values = reply["bools"] as? [String: Bool] ?? [:]
                if let value = values[key] { bools[key] = value; confirmedBools[key] = value }
                else { bools.removeValue(forKey: key); confirmedBools.removeValue(forKey: key) }
            case "string":
                let values = reply["strings"] as? [String: String] ?? [:]
                if let value = values[key] { strings[key] = value; confirmedStrings[key] = value }
                else { strings.removeValue(forKey: key); confirmedStrings.removeValue(forKey: key) }
            default:
                let values = reply["ints"] as? [String: Int] ?? [:]
                if let value = values[key] { ints[key] = value; confirmedInts[key] = value }
                else { ints.removeValue(forKey: key); confirmedInts.removeValue(forKey: key) }
            }
            return true
        } catch {
            return false
        }
    }
}

struct V3ToggleRow: View {
    @ObservedObject var store: V3SettingsStore
    let title: String
    let key: String
    var body: some View {
        Toggle(LocalizedStringKey(title), isOn: Binding(get: { store.bools[key] ?? false },
                                    set: { store.setBool(key, $0) }))
            .disabled(!store.loaded)
    }
}

struct V3TextRow: View {
    @ObservedObject var store: V3SettingsStore
    let title: String
    let key: String
    @State private var text = ""
    @State private var seeded = false
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            V3LocalizedText(title).font(.subheadline)
            TextField("Not set", text: $text, onCommit: { store.setString(key, text) })
                .textFieldStyle(.roundedBorder)
                .autocapitalization(.none)
                .disableAutocorrection(true)
                .disabled(!store.loaded)
        }
        .padding(.vertical, 2)
        .onReceive(store.$strings) { strings in
            if !seeded, let current = strings[key] {
                text = current
                seeded = true
            }
        }
        .onChange(of: store.strings[key]) { current in
            if let current { text = current; seeded = true }
        }
    }
}

struct V3ConnectionView: View {
    @StateObject private var store = V3SettingsStore()
    @State private var port = ""
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { V3LocalizedText(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Connection") {
                V3ToggleRow(store: store, title: "Always Show VPN Configuration", key: "alwaysShowWireGuardConfig")
                V3ToggleRow(store: store, title: "Accept IPv6 Connections", key: "acceptIPv6ConnectionConfig")
                V3ToggleRow(store: store, title: "Use Local VPN", key: "useLocalVPN")
                VStack(alignment: .leading, spacing: 4) {
                    Text("Remote Pairing Port Override (0 = default)").font(.subheadline)
                    TextField("0", text: $port, onCommit: {
                        store.setInt("remotePairingPortOverride", Int(port) ?? 0)
                    })
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.numberPad)
                    .disabled(!store.loaded)
                }
                .padding(.vertical, 2)
                .onReceive(store.$ints) { ints in
                    if let value = ints["remotePairingPortOverride"] {
                        port = String(value)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Connection")
        .task { await store.load() }
    }
}

struct V3AnisetteServerRow: Identifiable {
    let id: String, name: String, address: String
    let hidden: Bool, active: Bool
    init?(_ row: [String: Any]) {
        guard let id = row["id"] as? String, !id.isEmpty else { return nil }
        self.id = id; name = row["name"] as? String ?? id; address = row["address"] as? String ?? ""
        hidden = row["hidden"] as? Bool ?? false; active = row["active"] as? Bool ?? false
    }
}

struct V3AnisetteView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @StateObject private var store = V3SettingsStore()
    @State private var servers: [V3AnisetteServerRow] = []
    @State private var message = ""
    @State private var notice = ""
    @State private var remoteBusy = false
    @State private var serverRequestOwners = V3AsyncRequestOwnerState()
    var body: some View {
        List {
            if !message.isEmpty {
                Section { V3LocalizedText(message).font(.footnote).foregroundColor(.red).textSelection(.enabled) }
            }
            if !notice.isEmpty {
                Section { V3LocalizedText(notice).font(.footnote).foregroundColor(.secondary) }
            }
            if !store.message.isEmpty {
                Section { V3LocalizedText(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section(V3Localization.text("Servers (\(servers.count))")) {
                ForEach(servers) { server in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(server.name).font(.headline)
                            Spacer()
                            if server.active {
                                Text("Active").font(.caption.weight(.bold)).foregroundColor(.green)
                            }
                        }
                            Text(server.address).font(.caption).foregroundColor(.secondary).textSelection(.enabled)
                        if !server.active && !server.hidden {
                            Button("Use This Server") {
                                guard !remoteBusy else { return }
                                remoteBusy = true
                                let owner = serverRequestOwners.begin(bindingID: "anisette-servers")
                                Task {
                                    defer { remoteBusy = false }
                                    await store.setStringAndWait("menuAnisetteURL", server.address)
                                    guard serverRequestOwners.owns(owner, bindingID: "anisette-servers") else { return }
                                    if !store.message.isEmpty {
                                        message = store.message
                                        return
                                    }
                                    await reload()
                                }
                            }
                            .font(.caption)
                            .disabled(remoteBusy)
                        }
                    }
                    .padding(.vertical, 2)
                }
                HStack {
                    Button(LocalizedStringKey(remoteBusy ? "Working..." : "Sync with Remote")) { Task { await remote("anisetteSync") } }
                        .disabled(remoteBusy)
                    Spacer()
                    Button("Reset to Defaults", role: .destructive) { Task { await remote("anisetteReset") } }
                        .disabled(remoteBusy)
                }
                .font(.caption)
            }
            Section("Options") {
                V3ToggleRow(store: store, title: "Offline Mode", key: "isAnisetteOfflineMode")
                V3ToggleRow(store: store, title: "Disable Rotation", key: "disableAnisetteRotation")
                V3ToggleRow(store: store, title: "On-Device Anisette", key: "useOnDeviceAnisette")
                V3TextRow(store: store, title: "Custom Server URL", key: "textInputAnisetteURL")
                V3TextRow(store: store, title: "Custom Anisette URL Override", key: "customAnisetteURL")
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Anisette Servers")
        .task {
            await store.load()
            await reload()
        }
        .onDisappear { serverRequestOwners.invalidate() }
    }
    private func reload() async {
        let owner = serverRequestOwners.begin(bindingID: "anisette-servers")
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "anisetteList")
            guard serverRequestOwners.owns(owner, bindingID: "anisette-servers") else { return }
            servers = (reply["servers"] as? [[String: Any]] ?? []).compactMap(V3AnisetteServerRow.init)
            message = ""
        } catch {
            guard serverRequestOwners.owns(owner, bindingID: "anisette-servers") else { return }
            message = V3FailureGuidance.message(error)
        }
    }
    private func remote(_ operation: String) async {
        guard !remoteBusy else { return }
        let owner = serverRequestOwners.begin(bindingID: "anisette-servers")
        remoteBusy = true
        notice = ""
        defer { remoteBusy = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: operation)
            guard serverRequestOwners.owns(owner, bindingID: "anisette-servers") else { return }
            servers = (reply["servers"] as? [[String: Any]] ?? []).compactMap(V3AnisetteServerRow.init)
            message = ""
            notice = operation == "anisetteReset" ? "Anisette servers reset." : "Anisette servers synced."
        } catch {
            guard serverRequestOwners.owns(owner, bindingID: "anisette-servers") else { return }
            if let failure = error as? CombinedFailure,
               let anisetteGuidance = V3AnisetteFailureGuidance.message(failure) {
                message = anisetteGuidance
            } else {
                message = V3FailureGuidance.message(error)
            }
        }
    }
}

struct V3SideSignView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var config = ""
    @State private var editorRevision: UInt64 = 0
    @State private var configRequestOwners = V3AsyncRequestOwnerState()
    @State private var editorVisible = false
    @State private var message = ""
    @State private var notice = ""
    @State private var busy = false
    @State private var exporting = false
    @State private var pickerPresented = false
    @State private var shareItems: [Any]?
    var body: some View {
        List {
            if !message.isEmpty {
                Section { V3LocalizedText(message).font(.footnote).foregroundColor(.red).textSelection(.enabled) }
            }
            if !notice.isEmpty {
                Section { V3LocalizedText(notice).font(.footnote).foregroundColor(.secondary) }
            }
            Section("Configuration JSON") {
                TextEditor(text: $config)
                    .font(.system(.caption, design: .monospaced))
                    .frame(minHeight: 220)
                HStack {
                    Button(LocalizedStringKey(busy ? "Saving..." : "Save")) { Task { await save() } }
                        .disabled(busy)
                    Spacer()
                    Button("Reset to Defaults", role: .destructive) { Task { await remote("sidesignReset") } }
                        .disabled(busy)
                }
                .font(.caption)
            }
            Section("Import / Export") {
                Button("Import from File") { pickerPresented = true }
                    .disabled(busy)
                Button(LocalizedStringKey(exporting ? "Exporting..." : "Export to File")) { Task { await exportConfig() } }
                    .disabled(busy || exporting)
                Text("The picker belongs to this screen; the service only parses and stores the file.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("SideSign Configuration")
        .task { await reload() }
        .onAppear { editorVisible = true }
        .onChange(of: config) { _ in invalidateConfigRequestOwner() }
        .onDisappear {
            editorVisible = false
            invalidateConfigRequestOwner()
        }
        .sheet(isPresented: $pickerPresented) {
            V3FilePicker(types: ["public.json"]) { url in
                pickerPresented = false
                if let url { Task { await importFile(url) } }
            }
        }
        .sheet(item: Binding(get: { shareItems.map { V3ShareBox(items: $0) } }, set: { _ in shareItems = nil })) { box in
            V3ActivitySheet(items: box.items)
        }
    }
    private func reload() async {
        let owner = configRequestOwners.begin(bindingID: "sidesign-config-editor")
        let capturedRevision = editorRevision
        let capturedConfig = config
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignGet")
            let loadedConfig = try consumeConfigToken(in: reply)
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            config = loadedConfig
            message = ""
        } catch {
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            message = V3FailureGuidance.message(error)
        }
    }
    private func save() async {
        let owner = configRequestOwners.begin(bindingID: "sidesign-config-editor")
        let capturedRevision = editorRevision
        let submittedConfig = config
        busy = true
        notice = ""
        defer { busy = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignSet",
                payload: ["config": submittedConfig])
            guard let savedConfig = reply["config"] as? String, savedConfig.utf8.count <= 8192 else {
                throw V3SecretHandoffError.malformed
            }
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == submittedConfig else {
                reportStaleMutationIfDraftChanged(from: submittedConfig,
                    capturedEditorRevision: capturedRevision)
                return
            }
            config = savedConfig
            message = ""
            notice = "Configuration saved."
        } catch {
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == submittedConfig else { return }
            message = V3FailureGuidance.message(error)
        }
    }
    private func remote(_ operation: String) async {
        let owner = configRequestOwners.begin(bindingID: "sidesign-config-editor")
        let capturedRevision = editorRevision
        let capturedConfig = config
        busy = true
        notice = ""
        defer { busy = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: operation)
            let resetConfig = try consumeConfigToken(in: reply)
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else {
                reportStaleMutationIfDraftChanged(from: capturedConfig,
                    capturedEditorRevision: capturedRevision)
                return
            }
            config = resetConfig
            message = ""
            notice = "Configuration reset."
        } catch {
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            message = V3FailureGuidance.message(error)
        }
    }
    private func importFile(_ url: URL) async {
        let owner = configRequestOwners.begin(bindingID: "sidesign-config-editor")
        let capturedRevision = editorRevision
        let capturedConfig = config
        busy = true
        notice = ""
        defer { busy = false }
        do {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try await V3SharedFileInput.readBoundedAsync(url)
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            guard let token = await status.stageSharedFile(data, purpose: "sidesign") else { return }
            defer { status.discardSharedFile(token) }
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignImport", target: token)
            let importedConfig = try consumeConfigToken(in: reply)
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else {
                reportStaleMutationIfDraftChanged(from: capturedConfig,
                    capturedEditorRevision: capturedRevision)
                return
            }
            config = importedConfig
            message = ""
            notice = "Configuration imported."
        } catch {
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            message = V3FailureGuidance.message(error)
        }
    }
    private func exportConfig() async {
        guard !busy, !exporting else { return }
        let owner = configRequestOwners.begin(bindingID: "sidesign-config-editor")
        let capturedRevision = editorRevision
        let capturedConfig = config
        exporting = true
        defer { exporting = false }
        message = ""
        notice = ""
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "sidesignExport")
            let text = try consumeConfigToken(in: reply)
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("sidesign-config.json")
            try text.write(to: url, atomically: true, encoding: .utf8)
            shareItems = [url]
            notice = "Configuration exported."
        } catch {
            guard mayApply(owner, capturedEditorRevision: capturedRevision),
                  config == capturedConfig else { return }
            message = V3FailureGuidance.message(error)
        }
    }

    private func invalidateConfigRequestOwner() {
        editorRevision &+= 1
        configRequestOwners.invalidate()
    }

    private func mayApply(_ owner: V3AsyncRequestOwner,
                          capturedEditorRevision: UInt64) -> Bool {
        configRequestOwners.owns(owner, bindingID: "sidesign-config-editor") &&
            capturedEditorRevision == editorRevision
    }

    private func reportStaleMutationIfDraftChanged(from capturedConfig: String,
                                                   capturedEditorRevision: UInt64) {
        guard editorVisible,
              config != capturedConfig || editorRevision != capturedEditorRevision else { return }
        notice = "SideStore accepted an earlier configuration change. Review your current draft before saving it."
    }

    private func consumeConfigToken(in reply: [String: Any]) throws -> String {
        guard let config = reply["config"] as? String, config.utf8.count <= 8192 else {
            throw V3SecretHandoffError.malformed
        }
        return config
    }
}

struct V3ShareBox: Identifiable {
    let id = UUID()
    let items: [Any]
}

struct V3CustomizationsView: View {
    @StateObject private var store = V3SettingsStore()
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { V3LocalizedText(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Signing") {
                V3ToggleRow(store: store, title: "Customize App ID", key: "customizeAppId")
                V3ToggleRow(store: store, title: "Customize App Extensions", key: "customizeAppExtensions")
                V3ToggleRow(store: store, title: "Auto-Fix App Group IDs", key: "autoFixAppGroupIDs")
                V3ToggleRow(store: store, title: "Prefer Resigned IPA", key: "preferResignedIPA")
                V3ToggleRow(store: store, title: "Export Resigned App", key: "isExportResignedAppEnabled")
                V3TextRow(store: store, title: "Minimuxer Gateway Backend", key: "minimuxerGatewayBackend")
            }
            Section("Verification") {
                V3ToggleRow(store: store, title: "App Verification Disabled", key: "appVerificationDisabled")
                V3ToggleRow(store: store, title: "Verify Bundle ID", key: "isBundleIDVerificationEnabled")
                V3ToggleRow(store: store, title: "Verify iOS Version", key: "isiOSVersionVerificationEnabled")
                V3ToggleRow(store: store, title: "Verify App Version", key: "isAppVersionVerificationEnabled")
                V3ToggleRow(store: store, title: "Verify Checksum", key: "isChecksumVerificationEnabled")
                V3ToggleRow(store: store, title: "Verify File Size", key: "isFileSizeVerificationEnabled")
                V3ToggleRow(store: store, title: "Disable Permission Checking", key: "permissionCheckingDisabled")
            }
            Section("Backups") {
                V3ToggleRow(store: store, title: "Skip Non-Copyable Backup Files", key: "skipNonCopyableBackupFiles")
            }
            Section("Network") {
                V3ToggleRow(store: store, title: "On-Device Anisette", key: "useOnDeviceAnisette")
                V3ToggleRow(store: store, title: "WireGuard EMP", key: "enableEMPforWireguard")
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Installation Options")
        .task { await store.load() }
    }
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

enum V3ProvisioningRetryReadinessPolicy {
    static func mayComplete(replyState: String?, authenticated: Bool,
                            cancellationInProgress: Bool, taskCancelled: Bool) -> Bool {
        guard replyState == "completed", authenticated, !taskCancelled else { return false }
        // A cancel request can lose the race after provisioning already
        // committed. Accept that exact completed terminal from the owner.
        let normalCommit = !cancellationInProgress
        let committedBeforeCancel = cancellationInProgress && replyState == "completed"
        return normalCommit || committedBeforeCancel
    }
}

struct V3ProvisioningRetryReadinessOwnership {
    private(set) var sessionID: String?

    mutating func begin(sessionID: String) {
        self.sessionID = sessionID
    }

    mutating func clear() {
        sessionID = nil
    }

    func owns(sessionID: String) -> Bool {
        self.sessionID == sessionID
    }

    func allowsPromptResponse(sessionID: String?) -> Bool {
        guard let owner = self.sessionID else { return true }
        guard let sessionID else { return false }
        return owner == sessionID
    }

    // A superseded poll monitor is a handoff of the same retry ownership, not
    // completion of that retry. The continuation must still be able to settle
    // the exact session that the original task started.
    func handoffAfterSupersededPollFailure(sessionID: String) -> Bool {
        owns(sessionID: sessionID)
    }

    mutating func release(sessionID: String) {
        guard owns(sessionID: sessionID) else { return }
        self.sessionID = nil
    }

    mutating func settle(currentSessionID: String?, replySessionID: String?,
                         replyState: String?, authenticated: Bool,
                         cancellationInProgress: Bool, taskCancelled: Bool)
        -> V3ProvisioningRetryReadinessSettlement {
        guard let owner = sessionID else { return .notRetry }
        guard currentSessionID == owner, replySessionID == owner,
              ["completed", "authenticatedProvisioningIncomplete", "failed", "timedOut",
               "promptExpired", "cancelled"].contains(replyState ?? "") else { return .unmatched }
        sessionID = nil
        return V3ProvisioningRetryReadinessPolicy.mayComplete(
            replyState: replyState, authenticated: authenticated,
            cancellationInProgress: cancellationInProgress,
            taskCancelled: taskCancelled) ? .committed : .finishedWithoutCommit
    }
}

enum V3ProvisioningRetryReadinessSettlement: Equatable {
    case notRetry
    case unmatched
    case finishedWithoutCommit
    case committed
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
            hasCopy: data != nil && password != nil,
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
        // Observe the same native parser that canonical LC import and Diagnose
        // use. Security's independent PKCS#12 acceptance is not a prerequisite.
        guard let facts = LCUtils.certificateFacts(withKeyData: data, password: password),
              let team = facts["teamIdentifier"], !team.isEmpty,
              let fingerprint = facts["identitySHA256"],
              fingerprint.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { return nil }
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

struct V3HealthView: View {
    @Environment(\.locale) private var dateLocale

    @EnvironmentObject private var status: V3SideStoreStatusStore
    @EnvironmentObject private var sharedModel: SharedModel
    @State private var rows: [(String, String)] = []
    @State private var opaqueHealthRows: Set<String> = []
    @State private var certRows: [(String, String)] = []
    @State private var certDates: [String: Date] = [:]
    @State private var message = ""
    @State private var reloadQueue = V3HealthReloadQueue()

    private var jitlessReadiness: V3JITLessReadiness {
        status.jitlessReadiness ?? .unknown
    }
    private var jitlessDetail: String {
        guard let readiness = status.jitlessReadiness else { return "Checking" }
        return V3JITLessPresentation.present(readiness).detail
    }
    private var activeCertificateAvailable: Bool {
        status.jitlessActiveCertificateAvailable ?? false
    }

    var body: some View {
        List {
            if status.needsSignIn {
                Section { V3SignInLink(title: "Sign In to Check Account Health") }
            }
            if !message.isEmpty {
                Section { V3LocalizedText(message).font(.footnote).foregroundColor(.red).textSelection(.enabled) }
            }
            Section("Health") {
                ForEach(rows, id: \.0) { row in
                    HStack {
                        V3LocalizedText(row.0)
                        Spacer()
                        Text(opaqueHealthRows.contains(row.0) || row.0 == "Anisette Servers" ? row.1 : V3Localization.prose(row.1)).foregroundColor(.secondary).multilineTextAlignment(.trailing)
                    }
                    .font(.subheadline)
                }
            }
            Section {
                Button(LocalizedStringKey(reloadQueue.isChecking ? "Checking..." : "Re-check")) { Task { await reload() } }
                    .disabled(reloadQueue.isChecking)
            }
            Section("Certificates") {
                ForEach(certRows, id: \.0) { row in
                    HStack {
                        V3LocalizedText(row.0)
                        Spacer()
                        Text(certDates[row.0].map {
                            V3Localization.date($0, date: .abbreviated,
                                time: row.0 == "Active Expiry" ? .omitted : .shortened,
                                language: V3AppLanguage.resolve(dateLocale.identifier))
                        } ?? (["Active Serial", "Active Team", "Copy Team"].contains(row.0) ? row.1 : V3Localization.prose(row.1))).foregroundColor(.secondary).multilineTextAlignment(.trailing)
                    }
                    .font(.subheadline)
                }
                Text("SideStore uses its active certificate for signing, refresh, and installation. LiveContainer keeps a separate JIT-Less certificate copy. JIT-Less certificate status does not by itself mean SideStore refresh used that copy.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Section("JIT-Less Mode") {
                HStack {
                    Text("Status")
                    Spacer()
                    V3LocalizedText(jitlessDetail).foregroundColor(.secondary).multilineTextAlignment(.trailing)
                }
                if V3JITLessCompletionPolicy.isRequired(osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion) {
                    // V3_JITLESS_PRESENTATION_V1: Health renders the same shared
                    // presentation as Setup Assistant, and adds the certificate
                    // action that actually resolves each distinct state.
                    let jitless = V3JITLessPresentation.present(jitlessReadiness)
                    V3LocalizedLabel(jitless.title, systemImage: jitless.icon)
                        .font(.footnote)
                        .foregroundColor(jitless.tint)
                    if !jitless.isOutstandingSetupTask {
                        Button("Open JIT-Less Diagnose") { openJITLessDiagnose() }
                            .font(.caption)
                    } else {
                        switch jitlessReadiness {
                        case .setupRequired, .needsCertificateRefresh, .certificateMismatch, .revoked:
                            Button(LocalizedStringKey(jitlessReadiness == .setupRequired
                                   ? "Set Up JIT-Less" : "Refresh JIT-Less Certificate")) {
                                openJITLessSetup()
                            }
                        case .certificateImported:
                            // The copy exists but validation is not conclusive, so
                            // the canonical setup flow is still the useful action.
                            if activeCertificateAvailable {
                                Button("Open JIT-Less Setup") { openJITLessSetup() }
                            }
                            Button("Open Certificates") { status.certificatesPresented = true }
                        case .unknown:
                            if V3JITLessHealthRecoveryPolicy.shouldOfferCanonicalSetup(
                                for: .unknown, activeCertificateAvailable: activeCertificateAvailable) {
                                Button("Open JIT-Less Setup") { openJITLessSetup() }
                            }
                            Button(LocalizedStringKey(reloadQueue.isChecking ? "Checking..." : "Re-check")) { Task { await reload() } }
                                .disabled(reloadQueue.isChecking)
                        case .activeCertificateMissing, .activeCertificateRevoked,
                             .activeCertificateExpired:
                            // Refreshing the copy cannot repair SideStore's own
                            // certificate, so only Certificates is offered.
                            Text("Refreshing the JIT-Less copy cannot repair SideStore's active certificate.")
                                .font(.caption).foregroundColor(.secondary)
                            Button("Open Certificates") { status.certificatesPresented = true }
                        case .ready, .notRequired:
                            EmptyView()
                        }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Health Check")
        .task { await reload() }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("V3CanonicalJITLessCertificateUpdated"))) { _ in
            // Invalidate immediately, even if this view already has a health
            // request in flight. Its result is stale as soon as the certificate
            // changes, and reload() queues one follow-up request.
            status.invalidateSetupFacts()
            Task { await reload() }
        }
    }

    private func reload() async {
        guard reloadQueue.request() else { return }
        repeat {
            let factRevision = status.beginSetupFactObservation()
            await reloadOnce(factRevision: factRevision)
        } while reloadQueue.finishIteration()
    }

    private func reloadOnce(factRevision: UInt64) async {
        rows.removeAll(keepingCapacity: true)
        opaqueHealthRows.removeAll(keepingCapacity: true)
        certRows.removeAll(keepingCapacity: true)
        certDates.removeAll(keepingCapacity: true)
        message = ""
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "healthSnapshot")
            guard healthRevisionIsCurrent(factRevision) else { return }
            status.recordInstalledHostSigning(reply, revision: factRevision)
            var result: [(String, String)] = []
            // Presence is separate from names: a real name may equal any localized key.
            let accountPresent = V3ServiceBridge.strictBool(reply["activeAccountPresent"])
            let teamPresent = V3ServiceBridge.strictBool(reply["activeTeamPresent"])
            result.append(("Account", accountPresent == true ? (reply["account"] as? String ?? "")
                : accountPresent == false ? "Not signed in" : "Not available"))
            result.append(("Team", teamPresent == true ? (reply["team"] as? String ?? "")
                : teamPresent == false ? "No active team" : "Not available"))
            if accountPresent == true { opaqueHealthRows.insert("Account") }
            if teamPresent == true { opaqueHealthRows.insert("Team") }
            result.append(("Certificate", reply["certificate"] as? String ?? ""))
            result.append(("Installed Host Signing", status.installedHostSigningState.detail))
            result.append(("Pairing", reply["pairing"] as? String ?? ""))
            if let anisette = reply["anisette"] as? [String: Any] {
                result.append(("Anisette Servers", "\(anisette["servers"] as? Int ?? 0)"))
            }
            if let sidesign = reply["sidesign"] as? [String: Any] {
                result.append(("SideSign Configured", (sidesign["configured"] as? Bool ?? false) ? "Yes" : "No"))
            }
            rows = result
            let certificateState = reply["certificateState"] as? [String: Any] ?? [:]
            let activeCertificateAvailable = V3ServiceBridge.strictBool(certificateState["active"])
            let readiness = await V3JITLessStatusReader.read(serviceCertificate: certificateState)
            guard healthRevisionIsCurrent(factRevision) else { return }
            if let expiry = certificateState["expiry"] as? Date { certDates["Active Expiry"] = expiry }
            if let imported = LCUtils.appGroupUserDefault.object(forKey: "LCCertificateUpdateDate") as? Date {
                certDates["Copy Imported"] = imported
            }
            certRows = certComparison(service: certificateState,
                hasImportedCopy: readiness.hasImportedCopy, localFacts: readiness.certificateFacts)
            // V3_SHARED_JITLESS_FACT_V1: Health is an observer of the same fact,
            // so visiting Health can also complete Home's outstanding item.
            status.recordJITLessReadiness(readiness.readiness,
                activeCertificateAvailable: activeCertificateAvailable, revision: factRevision)
            message = ""
        } catch {
            guard healthRevisionIsCurrent(factRevision) else { return }
            message = V3FailureGuidance.message(error)
            status.recordInstalledHostSigning([:], revision: factRevision)
            status.recordJITLessReadiness(.unknown,
                activeCertificateAvailable: nil, revision: factRevision)
        }
    }

    private func healthRevisionIsCurrent(_ revision: UInt64) -> Bool {
        guard !status.isSetupFactRevisionCurrent(revision) else { return true }
        // A host lifecycle or another authoritative observer invalidated this
        // request while it was running. Recheck after it finishes so Health
        // does not stay on a stale result or an empty "Checking" state.
        _ = reloadQueue.request()
        return false
    }

    private func openJITLessSetup() {
        sharedModel.selectedTab = .settings
        sharedModel.deepLink = URL(string: "livecontainer://jitless-setup")
    }

    private func openJITLessDiagnose() {
        sharedModel.selectedTab = .settings
        sharedModel.deepLink = URL(string: "livecontainer://jitless-diagnose")
    }

    private func certComparison(service: [String: Any], hasImportedCopy: Bool,
                                localFacts: V3PKCS12CertificateFacts?) -> [(String, String)] {
        let active = service["active"] as? Bool ?? false
        let serialSuffix = service["serialSuffix"] as? String ?? ""
        let team = service["team"] as? String ?? ""
        let expiry = service["expiry"] as? Date
        var result: [(String, String)] = [("SideStore Active", active ? "Yes" : "No")]
        if active {
            if !serialSuffix.isEmpty { result.append(("Active Serial", "?\(serialSuffix)")) }
            if !team.isEmpty { result.append(("Active Team", "?\(String(team.suffix(4)))")) }
            if expiry != nil { result.append(("Active Expiry", "")) }
        }
        result.append(("JIT-Less Copy", hasImportedCopy ? "Imported" : "Not imported"))
        let localTeam = localFacts?.teamIdentifier ?? ""
        let localFingerprint = localFacts?.identitySHA256 ?? ""
        if !localTeam.isEmpty {
            result.append(("Copy Team", "?\(String(localTeam.suffix(4)))"))
        }
        if certDates["Copy Imported"] != nil {
            result.append(("Copy Imported", ""))
        }
        let teamVerdict: String
        if !active { teamVerdict = "unknown: no active SideStore certificate" }
        else if localTeam.isEmpty || team.isEmpty { teamVerdict = "unknown: team could not be compared" }
        else { teamVerdict = localTeam == team ? "yes: same team" : "no: different teams" }
        result.append(("Team Match", teamVerdict))
        let activeFingerprint = service["certificateIdentitySHA256"] as? String ?? ""
        let identityVerdict: String
        if !active { identityVerdict = "unknown: no active SideStore certificate" }
        else if activeFingerprint.isEmpty || localFingerprint.isEmpty {
            identityVerdict = "unknown: certificate identity could not be compared"
        } else {
            identityVerdict = activeFingerprint == localFingerprint ? "yes: same certificate" : "no: different certificates"
        }
        result.append(("Certificate Identity Match", identityVerdict))
        if active { result.append(("SideStore Certificate Validation", service["validation"] as? String ?? "unknown")) }
        return result
    }
}

struct V3BackupsView: View {
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @State private var exportPassword = ""
    @State private var includeApple = false
    @State private var importPassword = ""
    @State private var pickerPresented = false
    @State private var shareItems: [Any]?
    @State private var message = ""
    @State private var importedEmail = ""
    @State private var exportBusy = false
    @State private var importBusy = false
    var body: some View {
        List {
            if !message.isEmpty {
                Section { V3LocalizedText(message).font(.footnote).foregroundColor(.red).textSelection(.enabled) }
            }
            Section("App Backups") {
                ForEach(status.installedApps.filter { !$0.isHost }) { app in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(app.name).font(.headline)
                        HStack {
                            Button("Back Up") {
                                status.perform("backup", target: app.identifier, title: "Back up " + app.name)
                            }
                            .font(.caption)
                            Spacer()
                            Button("Restore") {
                                status.perform("restore", target: app.identifier, title: "Restore " + app.name)
                            }
                            .font(.caption)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
            Section("Export Account") {
                SecureField("File Password", text: $exportPassword)
                    .textFieldStyle(.roundedBorder)
                Toggle("Include Apple Password", isOn: $includeApple)
                Button(LocalizedStringKey(exportBusy ? "Exporting..." : "Export Account File")) { Task { await exportAccount() } }
                    .disabled(exportPassword.isEmpty || exportBusy || importBusy)
            }
            Section("Import Account") {
                Button(LocalizedStringKey(importBusy ? "Importing..." : "Select Backup File")) { pickerPresented = true }
                    .disabled(exportBusy || importBusy)
                SecureField("File Password", text: $importPassword)
                    .textFieldStyle(.roundedBorder)
                if !importedEmail.isEmpty {
                    V3LocalizedText("Imported account for \(importedEmail). Sign in with its Apple password to finish.")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                    Button("Continue to Sign In") { status.signInPresented = true }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Backups")
        .sheet(isPresented: $pickerPresented) {
            V3FilePicker(types: ["public.data"]) { url in
                pickerPresented = false
                if let url { Task { await importAccount(url) } }
            }
        }
        .sheet(item: Binding(get: { shareItems.map { V3ShareBox(items: $0) } }, set: { _ in shareItems = nil })) { box in
            V3ActivitySheet(items: box.items)
        }
    }
    private func exportAccount() async {
        exportBusy = true
        defer { exportBusy = false }
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "accountExport",
                payload: ["answer": ["password": exportPassword], "includeApple": includeApple])
            guard let encoded = reply["backup"] as? String,
                  let data = Data(base64Encoded: encoded) else {
                throw NSError(domain: "V3Backups", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "The service returned an unreadable backup."])
            }
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("sidestore-account.sidestorebackup")
            try data.write(to: url, options: .atomic)
            shareItems = [url]
            message = ""
        } catch { message = V3FailureGuidance.message(error) }
    }
    private func importAccount(_ url: URL) async {
        importBusy = true
        defer { importBusy = false }
        do {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try await V3SharedFileInput.readBoundedAsync(url)
            guard let token = await status.stageSharedFile(data, purpose: "accountImport") else { return }
            defer { status.discardSharedFile(token) }
            let reply = try await V3ServiceBridge.shared.request(operation: "accountImport", target: token,
                payload: ["answer": ["password": importPassword]])
            importedEmail = reply["email"] as? String ?? ""
            message = ""
            status.reload()
        } catch { message = V3FailureGuidance.message(error) }
    }
}

struct V3SideJITView: View {
    @StateObject private var store = V3SettingsStore()
    @State private var ping = ""
    @State private var testing = false
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { V3LocalizedText(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Server") {
                V3ToggleRow(store: store, title: "SideJIT Server Enabled", key: "isSideJITServerEnabled")
                V3TextRow(store: store, title: "Server Address", key: "textInputSideJITServerurl")
                Button(LocalizedStringKey(testing ? "Checking..." : "Test Reachability")) { test() }
                    .disabled(testing)
                if !ping.isEmpty {
                    V3LocalizedText(ping).font(.caption).foregroundColor(.secondary)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("SideJIT Server")
        .task { await store.load() }
    }
    private func test() {
        guard !testing else { return }
        guard let address = store.strings["textInputSideJITServerurl"], !address.isEmpty,
              let url = URL(string: address.hasPrefix("http") ? address : "http://" + address) else {
            ping = "Enter a server address first."
            return
        }
        testing = true
        ping = "Checking..."
        Task {
            defer { testing = false }
            do {
                var request = URLRequest(url: url, timeoutInterval: 10)
                request.httpMethod = "GET"
                let (_, response) = try await URLSession.shared.data(for: request)
                ping = V3SideJITReachabilityFeedback.reachable(
                    httpStatusCode: (response as? HTTPURLResponse)?.statusCode)
            } catch {
                ping = V3SideJITReachabilityFeedback.unreachable
            }
        }
    }
}

struct V3ReleaseTrackHostView: View {
    @StateObject private var store = V3SettingsStore()
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { V3LocalizedText(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Update Channel") {
                V3TextRow(store: store, title: "Beta Track", key: "betaUdpatesTrack")
                Text("Leave empty for the default channel.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Update Channel")
        .task { await store.load() }
    }
}

struct V3DiagnosticsView: View {
    @StateObject private var store = V3SettingsStore()
    @State private var confirmReset = false
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { V3LocalizedText(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Logging") {
                V3ToggleRow(store: store, title: "Verbose Operations", key: "isVerboseOperationsLoggingEnabled")
                V3ToggleRow(store: store, title: "Verbose SideStore", key: "isSideStoreVerboseLoggingEnabled")
                V3ToggleRow(store: store, title: "Verbose Signing", key: "isAltSignVerboseLoggingEnabled")
                V3ToggleRow(store: store, title: "Verbose Transport", key: "isMinimuxerVerboseLoggingEnabled")
                V3ToggleRow(store: store, title: "Widget Logging", key: "widgetVerboseLogging")
                V3ToggleRow(store: store, title: "Rotate Logs on Startup", key: "isRotateLogsOnStartupEnabled")
                V3ToggleRow(store: store, title: "Disable Response Caching", key: "responseCachingDisabled")
            }
            Section("Advanced") {
                V3ToggleRow(store: store, title: "Cellular Refresh", key: "isCellularRefreshEnabled")
                V3ToggleRow(store: store, title: "Debug Mode", key: "isDebugModeEnabled")
                Button("Recreate Database on Next Start", role: .destructive) { confirmReset = true }
                    .confirmationDialog("Recreate the database on next start?", isPresented: $confirmReset, titleVisibility: .visible) {
                        Button("Confirm", role: .destructive) { store.setBool("recreateDatabaseOnNextStart", true) }
                        Button("Cancel", role: .cancel) {}
                    }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Diagnostics")
        .task { await store.load() }
    }
}

struct V3LogsView: View {
    @State private var tail = ""
    @State private var message = ""
    @State private var copied = false
    @State private var reloading = false
    var body: some View {
        List {
            if !message.isEmpty {
                Section { V3LocalizedText(message).font(.footnote).foregroundColor(.red) }
            }
            Section {
                Button(LocalizedStringKey(reloading ? "Loading Logs..." : "Reload Logs")) { Task { await reload() } }
                    .disabled(reloading)
                Button(LocalizedStringKey(copied ? "Copied" : "Copy Logs")) {
                    UIPasteboard.general.string = tail
                    copied = true
                    Task {
                        try? await Task.sleep(nanoseconds: 2_000_000_000)
                        copied = false
                    }
                }
                .disabled(reloading || tail.isEmpty)
            }
            Section("Operation Logs") {
                Text(String(tail.suffix(120_000))).environment(\.layoutDirection, .leftToRight)
                    .font(.system(.caption2, design: .monospaced))
                    .textSelection(.enabled)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Operation Logs")
        .task { await reload() }
    }
    private func reload() async {
        guard !reloading else { return }
        reloading = true
        defer { reloading = false }
        tail = ""
        message = ""
        do {
            let reply = try await V3ServiceBridge.shared.request(operation: "logTail")
            tail = reply["tail"] as? String ?? ""
            message = ""
        } catch { message = V3FailureGuidance.message(error) }
    }
}

struct V3ExperimentalView: View {
    @StateObject private var store = V3SettingsStore()
    var body: some View {
        List {
            if !store.message.isEmpty {
                Section { V3LocalizedText(store.message).font(.footnote).foregroundColor(.red) }
            }
            Section("Experimental") {
                V3ToggleRow(store: store, title: "Cellular Refresh", key: "isCellularRefreshEnabled")
                Text("Experimental options can change or disappear. Current signing state is never reset by toggling them.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Experimental Features")
        .task { await store.load() }
    }
}

struct V3RefreshDetailView: View {
    var body: some View {
        LCEmbeddedSideStoreRefreshView()
            .navigationTitle("Refresh")
            .navigationBarTitleDisplayMode(.inline)
    }
}

struct V3SetupStepState: Equatable {
    var state = "checking"
    var detail = ""
    var detailIsOpaque = false
}

@MainActor
final class V3SetupStore: ObservableObject {
    @Published var device = V3SetupStepState()
    @Published var pairing = V3SetupStepState()
    @Published var account = V3SetupStepState()
    @Published var jitless = V3SetupStepState()
    @Published var jitlessHasActiveCertificate = false
    @Published var network = V3SetupStepState()
    @Published var tunnel = V3SetupStepState()
    @Published var background = V3SetupStepState()
    @Published var schedule = V3SetupStepState()
    @Published private(set) var scheduleDeadline: Date?
    @Published private(set) var scheduleFrequency = "interval"

    func localizedScheduleState(language: V3AppLanguage) -> V3SetupStepState {
        guard schedule.state == "complete", let deadline = scheduleDeadline else { return schedule }
        let detail = "Scheduled refresh enabled (\(scheduleFrequency)), next expected "
            + V3Localization.date(deadline, date: .abbreviated, time: .shortened, language: language)
        return V3SetupStepState(state: schedule.state, detail: detail)
    }
    @Published var verification = V3SetupStepState()
    // V3_REFRESH_PREREQUISITE_POLICY_V1: the "What you can do" line for a
    // blocked or failed Test Refresh, kept separate from the row detail so the
    // structured failure fields stay machine-readable.
    @Published var verificationGuidance = ""
    @Published var failureOperation = ""
    @Published var failureStage = ""
    @Published var failureCode = ""
    @Published var failureCorrelation = ""
    @Published var failureRetryable = ""
    @Published var testLedgerState = ""
    @Published var testSummarySchema = ""
    @Published var testRunning = false
    @Published var lastVerified: Date?
    @Published var diagnostics = ""
    private var testTask: Task<Void, Never>?
    private var testRequestID: String?
    private var testRunID: String?
    private var testAttemptID: String?

    // The setup assistant reconciles the same run ledger and verification
    // manifest the embedded service writes, so it reads the runtime App Group
    // store. An unavailable store is an honest "not observed", never a private
    // store that would report an empty ledger as authoritative.
    private var groupDefaults: UserDefaults? {
        V3SharedAppGroup.sharedUserDefaults()
    }
    private static let pendingTestRequestIDKey = "V3SetupPendingRefreshRequestID"
    private static let pendingTestRequestDateKey = "V3SetupPendingRefreshRequestDate"
    private var testRequestStartedAt: Date?

    private func testRequestDisposition() -> V3SetupTestRequestDisposition {
        let defaults = groupDefaults
        let ledger = defaults?.dictionary(forKey: "liveContainerAutoRefreshRunLedger") ?? [:]
        let pendingID = defaults?.string(forKey: Self.pendingTestRequestIDKey) ?? testRequestID
        let pendingRecord = pendingID.flatMap {
            V3RefreshAllAttemptState.record(in: ledger, requestID: $0)
        }
        let activeRunID = defaults?.string(forKey: "liveContainerAutoRefreshActiveRunID")
        let activeRecord = activeRunID.flatMap { ledger[$0] as? [String: Any] }
        let storedDate = defaults?.object(forKey: Self.pendingTestRequestDateKey) as? Date
        let startedAt = storedDate ?? testRequestStartedAt
        let age = startedAt.map { max(0, Date().timeIntervalSince($0)) } ?? .infinity
        return V3SetupTestRequestPolicy.select(
            pendingRequestID: pendingID, pendingAge: age,
            pendingState: pendingRecord?["state"] as? String,
            activeRunID: activeRunID,
            activeRunRequestID: activeRecord?["request_id"] as? String)
    }

    // Setup Complete requires every required item: pairing, signed-in account
    // with team, acceptable network and tunnel, available Background App
    // Refresh, an enabled schedule, and a test verified in this assistant
    // session. Developer Mode stays advisory and never gates.
    // V3_SETUP_COMPLETION_POLICY_V1: the decision comes from the one shared
    // policy that Home also uses, so the two screens cannot disagree.
    /// V3_SHARED_JITLESS_FACT_V1: the store is passed in because the fact the
    /// decision needs is published on the status store, and this type is not a
    /// View. Reading the published fact rather than a local copy is what stops
    /// the assistant and Home from holding two answers.
    func completionInputs(status: V3SideStoreStatusStore) -> V3SetupCompletionInputs {
        V3SetupCompletionInputs(
            accountComplete: account.state == "complete",
            provisioningIncomplete: statusProvisioningIncomplete,
            pairingSatisfied: pairing.state == "complete",
            // V3_SHARED_JITLESS_FACT_V1: both surfaces ask the same shared
            // policy the same question, of the same published fact. The
            // assistant previously answered from its own step-state string,
            // which is a second authority: Health could publish a ready
            // readiness the assistant had not yet observed, and the two would
            // disagree about whether the item was outstanding.
            jitlessRequired: V3JITLessCompletionPolicy.isRequired(
                osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion),
            jitlessComplete: V3JITLessCompletionPolicy.isComplete(status.jitlessReadiness),
            networkComplete: network.state == "complete",
            tunnelComplete: tunnel.state == "complete",
            backgroundRefreshAvailable: background.state == "complete",
            scheduleEnabled: schedule.state == "complete",
            verifiedRefreshPresent: verification.state == "complete",
            installedHostSigningCompatible: status.installedHostSigningState == .compatible)
    }
    func outstandingSetup(status: V3SideStoreStatusStore) -> [V3SetupOutstandingItem] {
        completionInputs(status: status).outstanding()
    }
    func isComplete(status: V3SideStoreStatusStore) -> Bool {
        completionInputs(status: status).isComplete
    }

    /// Where the JIT-Less row leads when the state still needs work. A ready
    /// state has no required destination; its diagnostic action is separate.
    /// The status store is passed in because the store is not a View and has no
    /// environment of its own.
    func jitlessDestination(status: V3SideStoreStatusStore) -> AnyView? {
        let readiness = status.jitlessReadiness ?? .unknown
        if readiness.isSatisfied { return nil }
        if [.activeCertificateRevoked, .activeCertificateExpired].contains(readiness) || !jitlessHasActiveCertificate {
            return AnyView(V3CertificatesView().environmentObject(status))
        }
        return nil
    }

    // Set from the authoritative snapshot so the shared policy sees the same
    // provisioning fact Home sees, rather than inferring it from step states.
    @Published private(set) var statusProvisioningIncomplete = false

    private func publishJITLessReadiness(_ readiness: V3JITLessReadiness,
                                        activeCertificateAvailable: Bool?,
                                        status: V3SideStoreStatusStore,
                                        factRevision: UInt64) {
        jitlessHasActiveCertificate = activeCertificateAvailable == true
        status.recordJITLessReadiness(readiness,
            activeCertificateAvailable: activeCertificateAvailable, revision: factRevision)
        let presentation = V3JITLessPresentation.present(readiness)
        switch presentation.severity {
        case .completed:
            jitless = V3SetupStepState(state: "complete", detail: presentation.title)
        case .failed:
            jitless = V3SetupStepState(state: "failed", detail: presentation.title)
        case .unknown:
            jitless = V3SetupStepState(state: "warning", detail: presentation.title)
        default:
            jitless = V3SetupStepState(state: "actionRequired", detail: presentation.title)
        }
    }

    @discardableResult
    func reloadAndRecalculate(status: V3SideStoreStatusStore) async -> Bool {
        let outcome = await status.reloadAndWait()
        guard V3SetupReloadRecomputePolicy.mayRecompute(outcome: outcome.setupSnapshotOutcome) else {
            preserveUnknownStatusFacts(status: status)
            return false
        }
        await recalculate(status: status)
        return true
    }

    private func preserveUnknownStatusFacts(status: V3SideStoreStatusStore) {
        status.invalidateSetupFacts()
        statusProvisioningIncomplete = true
        account = V3SetupStepState(state: "checking", detail: "Sign-in status was not refreshed. Retry to check.")
        pairing = V3SetupStepState(state: "checking", detail: "Pairing status was not refreshed. Retry to check.")
        jitless = V3SetupStepState(state: "checking", detail: "Certificate status was not refreshed. Retry to check.")
    }

    func recalculate(status: V3SideStoreStatusStore) async {
        // If the root auth observer already started a fresh readiness read,
        // join it before taking a new fact revision or opening another health
        // snapshot. This is the Setup Assistant's return-from-sign-in path.
        let sharedReadiness = await status.awaitSharedSetupJITLessReadiness()
        // Validate the observation against the status owner's current revision
        // after the await. Reuse keeps its source revision; a missing or stale
        // snapshot starts a fresh revision before the authoritative health read.
        let currentFactRevision = status.currentSetupFactRevision
        let observationToReuse: V3SetupReadinessObservation?
        if !V3SetupReadinessObservationPolicy.shouldFetchLocalReadiness(sharedReadiness),
           let sharedReadiness,
           !V3SetupReadinessObservationPolicy.shouldFetchLocalReadiness(
                sharedReadiness, currentFactRevision: currentFactRevision),
           status.jitlessReadiness == sharedReadiness.readiness,
           status.jitlessActiveCertificateAvailable == sharedReadiness.activeCertificateAvailable {
            observationToReuse = sharedReadiness
        } else {
            observationToReuse = nil
        }
        let factRevision: UInt64
        if let observationToReuse {
            factRevision = observationToReuse.sourceFactRevision
        } else {
            factRevision = status.beginSetupFactObservation()
        }
        NSLog("[V3_SETUP] STATUS recalculating")
        // Recorded from the authoritative snapshot so the shared completion
        // policy sees the same provisioning fact Home sees.
        statusProvisioningIncomplete = status.provisioningIncomplete
        device = V3SetupStepState(state: "complete", detail: "App running")
        // V3_REFRESH_PREREQUISITE_POLICY_V1: shared with Home Refresh All, Test
        // Refresh, and targeted refresh. A known-missing pairing file is
        // "missing"; an unknown status stays unknown rather than being reported
        // as missing.
        switch V3PairingPresentationPolicy.state(statusConnected: status.connected,
            pairingStatus: status.pairing) {
        case .satisfied:
            pairing = V3SetupStepState(state: "complete", detail: "Pairing file available")
        case .unsatisfied:
            pairing = V3SetupStepState(state: "actionRequired", detail: V3RefreshPrerequisite.pairingRequiredDetail)
        case .unknown:
            pairing = V3SetupStepState(state: "checking", detail: "Checking pairing file status")
        }
        // V3_AUTH_SESSION_SNAPSHOT_V1: an authenticated session with incomplete
        // provisioning is signed in, so the row must not read "Not signed in".
        if status.needsSignIn {
            account = V3SetupStepState(state: "actionRequired", detail: "Not signed in")
        } else if status.provisioningIncomplete {
            account = V3SetupStepState(state: "warning", detail: "Signed in, provisioning needs attention")
        } else if status.team == "No active team" {
            account = V3SetupStepState(state: "warning", detail: "Signed in without an active team")
        } else {
            account = V3SetupStepState(state: "complete", detail: status.account, detailIsOpaque: true)
        }
        if status.installedHostSigningState == .unknown {
            do {
                let local = try await V3ServiceBridge.shared.request(operation: "healthSnapshot",
                    target: "hostSigningOnly")
                guard status.isSetupFactRevisionCurrent(factRevision) else { return }
                status.recordInstalledHostSigning(local, revision: factRevision)
            } catch {
                guard status.isSetupFactRevisionCurrent(factRevision) else { return }
                status.recordInstalledHostSigning([:], revision: factRevision)
            }
        }
        // V3_SHARED_JITLESS_FACT_V1: the requirement itself also comes from the
        // shared policy, so the branch below and the completion input can never
        // disagree about whether JIT-Less applies on this iOS version.
        if !V3JITLessCompletionPolicy.isRequired(
            osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion) {
            status.recordJITLessReadiness(.notRequired, revision: factRevision)
            jitless = V3SetupStepState(state: "complete", detail: "Not required on this iOS version")
        } else if let observationToReuse {
            guard status.isSetupFactRevisionCurrent(factRevision) else { return }
            publishJITLessReadiness(observationToReuse.readiness,
                activeCertificateAvailable: observationToReuse.activeCertificateAvailable,
                status: status, factRevision: factRevision)
        } else {
            do {
                let health = try await V3ServiceBridge.shared.request(operation: "healthSnapshot")
                guard status.isSetupFactRevisionCurrent(factRevision) else { return }
                let certificate = health["certificateState"] as? [String: Any] ?? [:]
                let activeCertificateAvailable = V3ServiceBridge.strictBool(certificate["active"]) == true
                let readiness = await V3JITLessStatusReader.read(serviceCertificate: certificate)
                guard status.isSetupFactRevisionCurrent(factRevision) else { return }
                publishJITLessReadiness(readiness.readiness,
                    activeCertificateAvailable: activeCertificateAvailable,
                    status: status, factRevision: factRevision)
            } catch {
                guard status.isSetupFactRevisionCurrent(factRevision) else { return }
                jitlessHasActiveCertificate = false
                // Not observed is published as unknown, so Home keeps the item
                // outstanding instead of assuming a certificate exists.
                status.recordJITLessReadiness(.unknown, revision: factRevision)
                jitless = V3SetupStepState(state: "warning", detail: "Could not verify JIT-Less certificate state" + "\nError ID: SS-SIGN-D097")
            }
        }
        network = V3SetupStepState(state: "checking", detail: "Checking Wi-Fi…")
        let wifi = await LiveContainerNetworkPreflight.wifiAvailable()
        guard status.isSetupFactRevisionCurrent(factRevision) else { return }
        // Published so the shared setup-completion policy and the Home banner
        // observe the same authoritative Wi-Fi fact instead of each deciding.
        status.recordWifiAvailability(wifi, revision: factRevision)
        status.markSetupFactsObserved(revision: factRevision)
        if !wifi {
            network = V3SetupStepState(state: "failed", detail: "Wi-Fi unavailable" + "\nError ID: SS-NET-D096")
            tunnel = V3SetupStepState(state: "unavailable", detail: "Needs Wi-Fi first")
            NSLog("[V3_SETUP] STATUS step=network state=failed")
        } else {
            network = V3SetupStepState(state: "complete", detail: "Wi-Fi available")
            NSLog("[V3_SETUP] STATUS step=network state=ready")
            if LiveContainerNetworkPreflight.hasTunnelInterface() {
                tunnel = V3SetupStepState(state: "complete", detail: "Tunnel interface present (not a CoreDevice proof)")
                NSLog("[V3_SETUP] STATUS step=tunnel state=ready")
            } else {
                tunnel = V3SetupStepState(state: "actionRequired", detail: "Tunnel not present")
                NSLog("[V3_SETUP] STATUS step=tunnel state=action_required")
            }
        }
        switch UIApplication.shared.backgroundRefreshStatus {
        case .available:
            background = V3SetupStepState(state: "complete", detail: "Background App Refresh available")
        case .denied:
            background = V3SetupStepState(state: "warning", detail: "Background App Refresh denied")
        case .restricted:
            background = V3SetupStepState(state: "warning", detail: "Background App Refresh restricted")
        @unknown default:
            background = V3SetupStepState(state: "warning", detail: "Background App Refresh state unknown")
        }
        NSLog("[V3_SETUP] STATUS step=background state=\(background.state)")
        if let defaults = groupDefaults, defaults.bool(forKey: "liveContainerAutoRefreshEnabled") {
            let frequency = defaults.string(forKey: "liveContainerAutoRefreshFrequency") ?? "interval"
            scheduleFrequency = frequency
            scheduleDeadline = defaults.object(forKey: "liveContainerAutoRefreshTargetDeadline") as? Date
            var summary = "Scheduled refresh enabled (\(frequency))"
            if let deadline = defaults.object(forKey: "liveContainerAutoRefreshTargetDeadline") as? Date {
                summary += ", next expected " + deadline.formatted(date: .abbreviated, time: .shortened)
            }
            schedule = V3SetupStepState(state: "complete", detail: summary)
        } else {
            scheduleDeadline = nil
            schedule = V3SetupStepState(state: "actionRequired", detail: "Scheduled refresh disabled")
        }
        NSLog("[V3_SETUP] STATUS step=schedule state=\(schedule.state)")
        refreshVerificationRow()
        NSLog("[V3_SETUP] STATUS step=account state=\(account.state) step=pairing state=\(pairing.state)")
    }

    private func verificationManifest() -> [String: Any]? {
        groupDefaults?.dictionary(forKey: "liveContainerAutoRefreshVerification")
    }

    private func refreshVerificationRow() {
        // History display only. A past manifest updates the timestamp row but
        // never satisfies the current setup test; only checkTestResult() may
        // mark verification complete, and only for a new fully-covered run.
        if let manifest = verificationManifest(),
           let date = manifest["date"] as? Date {
            lastVerified = date
        }
        if verification.state == "checking" {
            verification = V3SetupStepState(state: "actionRequired", detail: "No verified refresh in this session yet")
        }
    }

    func recordFailure(operation: String, stage: String, code: String, correlation: String, retryable: String) {
        failureOperation = operation
        failureStage = stage
        failureCode = code
        failureCorrelation = correlation
        failureRetryable = retryable
        NSLog("[V3_SETUP] FAILURE operation=%@ stage=%@ code=%@ correlation=%@", operation, stage, code, correlation)
    }

    /// Records a Test Refresh failure for the Setup Assistant.
    ///
    /// V3_FAILURE_GUIDANCE_V1: the row caption is product copy, so it shows
    /// guidance. It previously appended the numeric NSError domain and code,
    /// which is the practice the failure policy exists to remove, and the branch
    /// that did it also made the final `else` unreachable: every Swift Error
    /// bridges to `NSError?`, so that arm could never run. The domain and code
    /// remain available in the diagnostics block, which is where a support reader
    /// looks for them.
    func recordError(_ error: Error, operation: String) {
        if let failure = error as? CombinedFailure {
            recordFailure(operation: operation, stage: failure.stage.rawValue, code: failure.code.rawValue,
                          correlation: failure.correlationID,
                          retryable: failure.retryable.map { $0 ? "true" : "false" } ?? "")
            verification = V3SetupStepState(state: "failed", detail: failure.safeMessage)
            verificationGuidance = failure.recovery
            return
        }
        // No stage or code is invented for an untyped error. Nothing claimed a
        // cause, so nothing is asserted about one.
        recordFailure(operation: operation, stage: "", code: "", correlation: "", retryable: "")
        verification = V3SetupStepState(state: "warning",
            detail: "Test refresh could not be completed, and the cause is not known." + "\nError ID: SS-VERIFY-D044")
        verificationGuidance = V3FailureGuidance.message(error)
    }

    // V3_REFRESH_PREREQUISITE_POLICY_V1: Test Refresh uses the same
    // authoritative prerequisite contract as Home Refresh All. A known-missing
    // pairing file must never post the scheduler notification, so no backend
    // mutation is started, and it must never be reported as an unexplained
    // refresh failure.
    func runTestRefresh(status: V3SideStoreStatusStore) {
        guard !testRunning else { return }
        let disposition = testRequestDisposition()
        let requestID: String
        let shouldPostRequest: Bool
        switch disposition {
        case .waitForActiveRun:
            let defaults = groupDefaults
            // We cannot safely attach an uncorrelated pending request to this
            // different active run. It did not create a second scheduler run.
            defaults?.removeObject(forKey: Self.pendingTestRequestIDKey)
            defaults?.removeObject(forKey: Self.pendingTestRequestDateKey)
            testRequestStartedAt = nil
            testRequestID = nil
            testRunID = nil
            testAttemptID = nil
            testRunning = false
            failureOperation = ""
            failureStage = ""
            failureCode = ""
            failureCorrelation = ""
            failureRetryable = ""
            verification = V3SetupStepState(state: "warning",
                detail: "Another refresh is already running. Test Refresh did not start." + "\nError ID: SS-CMD-D045")
            verificationGuidance = "Wait for the current refresh to finish, then start Test Refresh again."
            NSLog("[V3_SETUP] TEST_REFRESH_BLOCKED reason=activeRun")
            return
        case .resumeExisting(let existingRequestID):
            requestID = existingRequestID
            shouldPostRequest = false
            testRequestID = existingRequestID
            testRequestStartedAt = groupDefaults?.object(
                forKey: Self.pendingTestRequestDateKey) as? Date ?? Date()
            let ledger = groupDefaults?.dictionary(forKey: "liveContainerAutoRefreshRunLedger") ?? [:]
            testRunID = V3RefreshAllAttemptState.record(in: ledger,
                requestID: existingRequestID)?["run_id"] as? String
        case .startNew:
            requestID = UUID().uuidString
            shouldPostRequest = true
            if let failure = V3RefreshPrerequisite.evaluate(statusConnected: status.connected,
                    pairingStatus: status.pairing)
                .failure(correlationID: requestID) {
                testRunning = false
                testRequestID = nil
                testRunID = nil
                testAttemptID = nil
                testTask = nil
                testRequestStartedAt = nil
                recordFailure(operation: failure.operation, stage: failure.stage.rawValue,
                              code: failure.code.rawValue, correlation: failure.correlationID,
                              retryable: "false")
                verification = V3SetupStepState(state: "actionRequired", detail: failure.safeMessage)
                verificationGuidance = "Place or import a valid pairing file, then try again."
                NSLog("[V3_SETUP] TEST_REFRESH_BLOCKED reason=pairing request_id=%@", requestID)
                return
            }
            let startedAt = Date()
            testRequestStartedAt = startedAt
            groupDefaults?.set(requestID, forKey: Self.pendingTestRequestIDKey)
            groupDefaults?.set(startedAt, forKey: Self.pendingTestRequestDateKey)
            testRequestID = requestID
            testRunID = nil
        }
        testRunning = true
        let attemptID = UUID().uuidString
        testAttemptID = attemptID
        testLedgerState = ""
        testSummarySchema = ""
        failureOperation = ""
        failureStage = ""
        failureCode = ""
        failureCorrelation = ""
        failureRetryable = ""
        verification = V3SetupStepState(state: "running",
            detail: shouldPostRequest ? "Test refresh running…" : "Resuming the current Test Refresh…")
        verificationGuidance = ""
        NSLog("[V3_SETUP] TEST_REFRESH_START request_id=%@ origin=setupAssistant resumed=%@",
              requestID, shouldPostRequest ? "false" : "true")
        if shouldPostRequest {
            NotificationCenter.default.post(name: Notification.Name("LiveContainerAutoRefreshRunNow"), object: nil,
                                            userInfo: ["requestID": requestID, "origin": "setupAssistant"])
        }
        startTestMonitor(requestID: requestID, attemptID: attemptID)
    }

    private func startTestMonitor(requestID: String, attemptID: String) {
        testTask = Task {
            do {
                let deadline = Date().addingTimeInterval(600)
                while !Task.isCancelled && Date() < deadline {
                    try await Task.sleep(nanoseconds: 2_000_000_000)
                    try Task.checkCancellation()
                    if await checkTestResult(attemptID: attemptID) { return }
                }
                if V3SetupTestAttemptPolicy.mayApply(capturedAttemptID: attemptID,
                    currentAttemptID: testAttemptID, taskCancelled: Task.isCancelled) {
                    verification = V3SetupStepState(state: "warning", detail: "No verified result yet. Check Refresh Manager for progress." + "\nError ID: SS-VERIFY-D046")
                    NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=timeout")
                    testRunning = false
                    testAttemptID = nil
                    testTask = nil
                }
            } catch is CancellationError {
                return
            } catch {
                guard V3SetupTestAttemptPolicy.mayApply(capturedAttemptID: attemptID,
                    currentAttemptID: testAttemptID, taskCancelled: Task.isCancelled) else { return }
                recordError(error, operation: "refresh")
                NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=error")
                testRunning = false
                testAttemptID = nil
                testTask = nil
            }
        }
    }

    private func checkTestResult(attemptID: String) async -> Bool {
        guard V3SetupTestAttemptPolicy.mayApply(capturedAttemptID: attemptID,
            currentAttemptID: testAttemptID, taskCancelled: Task.isCancelled) else { return false }
        guard let requestID = testRequestID else { return false }
        let ledger = groupDefaults?.dictionary(forKey: "liveContainerAutoRefreshRunLedger") ?? [:]
        guard let runRecord = V3RefreshAllAttemptState.record(in: ledger, requestID: requestID),
              let runID = runRecord["run_id"] as? String else {
            let age = testRequestStartedAt.map { Date().timeIntervalSince($0) } ?? 0
            let activeRunID = groupDefaults?.string(forKey: "liveContainerAutoRefreshActiveRunID") ?? ""
            let activeRecord = ledger[activeRunID] as? [String: Any]
            let activeRequestID = activeRecord?["request_id"] as? String
            if age >= V3SetupTestRequestPolicy.startGracePeriod,
               activeRunID.isEmpty || activeRequestID != requestID {
                recordFailure(operation: "refresh", stage: CombinedFailure.Stage.command.rawValue,
                    code: CombinedFailure.Code.busy.rawValue, correlation: requestID, retryable: "false")
                verification = V3SetupStepState(state: "failed", detail: "Refresh did not start." + "\nError ID: SS-CMD-D047")
                verificationGuidance = activeRunID.isEmpty
                    ? "Check Refresh Manager, then try Test Refresh again."
                    : "Another refresh took the scheduler first. Wait for it to finish, then start Test Refresh again."
                finishTestAttempt(requestID: requestID, attemptID: attemptID)
                NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=not_started request_id=%@", requestID)
                return true
            }
            return false
        }
        if let testRunID, testRunID != runID { return false }
        testRunID = runID
        let runState = runRecord["state"] as? String ?? ""
        testLedgerState = runState
        guard runState == "completed" || runState == "failed" else { return false }
        let manifest = runRecord["manifest"] as? [String: Any]
        let verifiedManifest = manifest.map {
            $0["run_id"] as? String == runID &&
                CombinedVerification.hasCompleteTerminalResults($0, runID: runID)
        } ?? false
        let verifiedSummary = V3RefreshAllTerminalEvidencePolicy.verifiedSummary(
            runRecord["manifest_summary"] as? [String: Any], record: runRecord, runID: runID)
        let summary = runRecord["manifest_summary"] as? [String: Any]
        testSummarySchema = summary?["schema"] as? String ?? "missing"
        switch V3SetupRefreshTerminalEvidencePolicy.outcome(state: runState,
            hasVerifiedManifest: verifiedManifest, hasVerifiedSummary: verifiedSummary) {
        case .pending:
            return false
        case .failed:
            let wire = runRecord["failure"] as? [String: Any]
            if let failure = wire.flatMap({ CombinedFailure.decode($0, expectedID: runID) }),
               failure.operation == "refresh" {
                recordFailure(operation: failure.operation, stage: failure.stage.rawValue,
                    code: failure.code.rawValue, correlation: failure.correlationID,
                    retryable: failure.retryable.map { $0 ? "true" : "false" } ?? "")
                verification = V3SetupStepState(state: "failed",
                    detail: V3DiagnosticPresentation.label(runRecord["message"] as? String ?? failure.safeMessage, context: .refresh))
                verificationGuidance = failure.recovery
            } else {
                recordFailure(operation: "refresh", stage: CombinedFailure.Stage.refreshVerification.rawValue,
                    code: "unknown", correlation: runID, retryable: "unknown")
                verification = V3SetupStepState(state: "failed",
                    detail: V3DiagnosticPresentation.label(runRecord["message"] as? String ?? "Refresh failed, but no safe underlying cause was available." + "\nError ID: SS-VERIFY-D048", context: .refresh))
                verificationGuidance = "Open Refresh Manager to inspect this run, then try Test Refresh again. Copy Diagnostics if the result remains unclear."
            }
            finishTestAttempt(requestID: requestID, attemptID: attemptID)
            NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=failed run_id=%@", runID)
            return true
        case .completedUnverified:
            recordFailure(operation: "refresh", stage: CombinedFailure.Stage.refreshVerification.rawValue,
                code: "invalidResponse", correlation: runID, retryable: "false")
            verification = V3SetupStepState(state: "failed",
                detail: "SideStore reported that refresh completed, but this run's result could not be verified." + "\nError ID: SS-VERIFY-D049")
            verificationGuidance = "Open Refresh Manager to reconcile the run, then run Test Refresh again. Copy Diagnostics if the result remains missing."
            finishTestAttempt(requestID: requestID, attemptID: attemptID)
            NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=unverified_completion run_id=%@", runID)
            return true
        case .verified:
            break
        }
        let results = manifest?["results"] as? [[String: Any]] ?? []
        if runState == "completed" && (verifiedSummary || results.allSatisfy({ $0["success"] as? Bool == true })) {
            verification = V3SetupStepState(state: "complete", detail: "Refresh verified")
            if let date = manifest?["date"] as? Date {
                lastVerified = date
            } else if let verifiedAt = (runRecord["manifest_summary"] as? [String: Any])?["verified_at"] as? Date {
                lastVerified = verifiedAt
            } else if let terminalAt = runRecord["terminal_at"] as? TimeInterval {
                lastVerified = Date(timeIntervalSince1970: terminalAt)
            }
        } else {
            var detail = "Refresh reported failures"
            if let failed = results.first(where: { $0["success"] as? Bool != true }) {
                recordFailure(operation: "refresh", stage: "", code: "", correlation: runID, retryable: "")
                // V3_FAILURE_GUIDANCE_V1: the manifest's "error" field is a
                // diagnostic block of message, recovery and technical details
                // joined by newlines. It was used verbatim as the row caption, so
                // a diagnostics line was rendered as product copy. The caption
                // takes the structured failure's safe message when the manifest
                // carries one, and the full text is still available below in the
                // diagnostics block.
                let wire = failed["failure"] as? [String: Any]
                let decoded = wire.flatMap { CombinedFailure.decode($0, expectedID: runID) }
                if let decoded {
                    detail = decoded.safeMessage
                    verificationGuidance = decoded.recovery
                } else if let message = failed["error"] as? String, !message.isEmpty,
                          let firstLine = message.split(separator: "\n").first {
                    detail = String(firstLine)
                }
                if let failure = wire {
                    recordFailure(operation: failure["operation"] as? String ?? "refresh",
                                  stage: failure["stage"] as? String ?? "",
                                  code: failure["code"] as? String ?? "",
                                  correlation: failure["correlationID"] as? String ?? runID,
                                  retryable: (failure["retryable"] as? Bool).map { $0 ? "true" : "false" } ?? "")
                }
            }
            verification = V3SetupStepState(state: "failed", detail: V3DiagnosticPresentation.label(detail, context: .refresh))
        }
        finishTestAttempt(requestID: requestID, attemptID: attemptID)
        if verification.state == "complete" {
            NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=verified")
        } else {
            NSLog("[V3_SETUP] TEST_REFRESH_TERMINAL result=failed")
        }
        return true
    }

    private func finishTestAttempt(requestID: String, attemptID: String) {
        guard V3SetupTestAttemptPolicy.mayApply(capturedAttemptID: attemptID,
            currentAttemptID: testAttemptID, taskCancelled: Task.isCancelled) else { return }
        testRunning = false
        testAttemptID = nil
        testTask = nil
        testRequestID = nil
        testRunID = nil
        testRequestStartedAt = nil
        if groupDefaults?.string(forKey: Self.pendingTestRequestIDKey) == requestID {
            groupDefaults?.removeObject(forKey: Self.pendingTestRequestIDKey)
            groupDefaults?.removeObject(forKey: Self.pendingTestRequestDateKey)
        }
    }

    func cancelTest() {
        guard testRunning else { return }
        let requestID = testRequestID
        testAttemptID = nil
        testTask?.cancel()
        testTask = nil
        testRunning = false
        verification = V3SetupStepState(state: "warning",
            detail: "Stopped waiting. The current refresh continues in the background.")
        verificationGuidance = requestID == nil
            ? "Check Refresh Manager before starting another Test Refresh."
            : "Tap Test Refresh again to resume this same request; it will not start a duplicate refresh."
    }

    // A human-copyable pairing word for the diagnostics block. The internal
    // policy states are not the vocabulary a person reading a support log wants.
    static func describePairing(_ state: V3RefreshPrerequisiteState) -> String {
        switch state {
        case .satisfied: return "available"
        case .unsatisfied: return "missing"
        case .unknown: return "unknown"
        }
    }

    func buildDiagnostics(status: V3SideStoreStatusStore) {
        var lines: [String] = ["Setup Assistant"]
        lines.append("Product: " + (Bundle.main.object(forInfoDictionaryKey: "LCProductLine") as? String ?? "unknown"))
        lines.append("iOS: " + UIDevice.current.systemVersion)
        lines.append("Pairing: " + V3SetupStore.describePairing(V3PairingPresentationPolicy.state(
            statusConnected: status.connected, pairingStatus: status.pairing)))
        lines.append("Account: " + (status.needsSignIn ? "signed out" : "signed in"))
        lines.append("Team: " + status.team)
        lines.append("Wi-Fi: " + (network.state == "failed" ? "unavailable" : "available"))
        lines.append("VPN interface: " + (LiveContainerNetworkPreflight.hasTunnelInterface() ? "present" : "absent"))
        lines.append("CoreDevice: " + (verification.state == "complete" ? "verified" : "not checked"))
        switch UIApplication.shared.backgroundRefreshStatus {
        case .available: lines.append("Background App Refresh: available")
        case .denied: lines.append("Background App Refresh: denied")
        case .restricted: lines.append("Background App Refresh: restricted")
        @unknown default: lines.append("Background App Refresh: unknown")
        }
        lines.append("Refresh schedule: " + schedule.detail)
        if let date = lastVerified {
            lines.append("Last verified refresh: " + date.formatted(date: .abbreviated, time: .shortened))
        } else {
            lines.append("Last verified refresh: none")
        }
        if !failureOperation.isEmpty {
            lines.append("Last structured failure: operation=\(failureOperation) stage=\(failureStage) code=\(failureCode) correlation=\(failureCorrelation) retryable=\(failureRetryable)")
        }
        if !testLedgerState.isEmpty {
            lines.append("Test refresh terminal state: \(testLedgerState)")
            lines.append("Verification summary schema: \(testSummarySchema.isEmpty ? "none" : testSummarySchema)")
        }
        diagnostics = lines.joined(separator: "\n")
    }
}

struct V3SetupAssistantView: View {
    @Environment(\.locale) private var dateLocale

    @EnvironmentObject private var status: V3SideStoreStatusStore
    @EnvironmentObject private var sharedModel: SharedModel
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss
    @StateObject private var setup = V3SetupStore()
    @State private var vpnWorking = false
    @State private var copiedDiagnostics = false
    // V3_RECHECK_PAIRING_ON_RETURN_V1: the pairing file is placed by an
    // external installation tool, so returning to a live Quick Setup is the
    // moment a newly placed file can be detected.
    @State private var showPairingSetup = false
    var body: some View {
        List {
            Section("Device") {
                setupRow(icon: "app.badge.checkmark", title: "App Running",
                         state: setup.device, destination: nil)
                setupRow(icon: "graduationcap", title: "Developer Mode",
                         state: V3SetupStepState(state: "warning", detail: "Guidance only: keep Developer Mode on in iOS Settings. Setup continues regardless."),
                         destination: nil)
            }
            // V3_PAIRING_PLACEMENT_FIRST_V1: the pairing mechanism works. The
            // documented normal path is placing the file with the installation
            // tool, so that is what the row explains first. Manual import stays
            // available as the secondary path.
            Section("Pairing") {
                setupRow(icon: "link", title: "Pairing File",
                         state: setup.pairing,
                         destination: AnyView(V3PairingView().environmentObject(status)))
                if setup.pairing.state == "actionRequired" {
                    Button {
                        showPairingSetup = true
                    } label: {
                        Label("Show Pairing Setup", systemImage: "link.badge.plus")
                    }
                    Button {
                        Task {
                            // The pairing file is placed by an external installation tool, so
                            // the authoritative snapshot is the only way to observe it.
                            // It is awaited before the setup steps are recomputed.
                            await setup.reloadAndRecalculate(status: status)
                        }
                    } label: {
                        Label("Re-check Pairing", systemImage: "arrow.clockwise")
                    }
                }
            }
            .sheet(isPresented: $showPairingSetup) {
                // NavigationView, not NavigationStack: the host target deploys
                // to iOS 15.
                NavigationView { V3PairingView().environmentObject(status) }
                    .navigationViewStyle(StackNavigationViewStyle())
            }
            Section("Apple Account") {
                setupRow(icon: "person.crop.circle", title: "Apple ID",
                         state: setup.account,
                         destination: AnyView(V3SignInView().environmentObject(status)))
            }
            if V3JITLessCompletionPolicy.isRequired(osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion) {
                // V3_JITLESS_PRESENTATION_V1: a ready JIT-Less state is rendered
                // as a completed result, not as an outstanding setup task. The
                // section only presents required actions while something is
                // actually outstanding.
                let jitless = V3JITLessPresentation.present(status.jitlessReadiness ?? .unknown)
                Section("JIT-Less Mode") {
                    setupRow(icon: jitless.icon, title: "JIT-Less",
                             state: V3SetupStepState(
                                state: jitless.isOutstandingSetupTask ? setup.jitless.state : "complete",
                                detail: jitless.title),
                             destination: jitless.isOutstandingSetupTask
                                ? setup.jitlessDestination(status: status)
                                : nil)
                    if !jitless.isOutstandingSetupTask {
                        // Optional diagnostic only. It must not look like setup.
                        Button {
                            sharedModel.selectedTab = .settings
                            sharedModel.deepLink = URL(string: "livecontainer://jitless-diagnose")
                        } label: {
                            Label("Open JIT-Less Diagnose", systemImage: "stethoscope")
                        }
                        .font(.caption)
                    } else {
                        switch V3JITLessSetupActionPolicy.action(
                            for: status.jitlessReadiness ?? .unknown) {
                        case .setUp:
                            Button("Set Up JIT-Less") { openCanonicalJITLessSetup() }
                        case .refreshCertificate:
                            Button("Refresh JIT-Less Certificate") { openCanonicalJITLessSetup() }
                        case .openCertificates:
                            Button("Open Certificates") { status.certificatesPresented = true }
                        case .openSetup:
                            Button("Open JIT-Less Setup") { openCanonicalJITLessSetup() }
                        case .none:
                            EmptyView()
                        }
                    }
                }
            }
            Section("Network") {
                setupRow(icon: "wifi", title: "Wi-Fi",
                         state: setup.network, destination: nil)
                setupRow(icon: "network", title: "VPN Tunnel",
                         state: setup.tunnel, destination: nil)
                if setup.tunnel.state == "actionRequired" {
                    Button {
                        openLocalVPN()
                    } label: {
                        V3LocalizedLabel(vpnWorking ? "Opening LocalDevVPN…" : "Open / Enable LocalDevVPN", systemImage: "network")
                    }
                    .disabled(vpnWorking)
                }
                setupRow(icon: "cpu", title: "CoreDevice",
                         state: coredeviceState(), destination: nil)
            }
            Section("Background Refresh") {
                setupRow(icon: "clock.arrow.circlepath", title: "Background App Refresh",
                         state: setup.background, destination: nil)
                if setup.background.state == "warning" {
                    Button {
                        openSystemSettings()
                    } label: {
                        Label("Open Settings", systemImage: "gearshape")
                    }
                }
            }
            Section("Notifications") {
                Text("Refresh start, completion and deadline warnings arrive as notifications.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Button {
                    Task { await LiveContainerAutoRefreshScheduler.requestNotificationPermissionFromUserAction() }
                } label: {
                    Label("Allow Refresh Notifications", systemImage: "bell.fill")
                }
            }
            Section("Automatic Refresh") {
                setupRow(icon: "calendar.badge.clock", title: "Schedule",
                         state: setup.localizedScheduleState(language: V3AppLanguage.resolve(dateLocale.identifier)),
                         destination: AnyView(V3RefreshDetailView()))
            }
            Section("Verification") {
                setupRow(icon: "signature", title: "Installed Host Signing",
                    state: V3SetupStepState(
                        state: status.installedHostSigningState == .compatible ? "complete" : "warning",
                        detail: status.installedHostSigningState.detail),
                    destination: AnyView(V3CertificatesView().environmentObject(status)))
                setupRow(icon: "checkmark.seal", title: "Test Refresh",
                         state: setup.verification, destination: nil)
                if !setup.verificationGuidance.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("What you can do").font(.caption.weight(.semibold))
                        V3LocalizedText(setup.verificationGuidance).font(.footnote)
                    }
                }
                // V3_REFRESH_PREREQUISITE_POLICY_V1: a structured prerequisite
                // failure is shown for failed and action-required states alike,
                // so a known-missing pairing file is never reported as
                // "no safe underlying cause was available".
                if (setup.verification.state == "failed" || setup.verification.state == "actionRequired")
                    && !setup.failureOperation.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("operation=\(setup.failureOperation) stage=\(setup.failureStage) code=\(setup.failureCode)")
                            .font(.caption2).foregroundColor(.secondary).textSelection(.enabled)
                        Text("correlation=\(setup.failureCorrelation) retryable=\(setup.failureRetryable)")
                            .font(.caption2).foregroundColor(.secondary).textSelection(.enabled)
                    }
                }
                if setup.verification.state == "actionRequired" && setup.failureStage == CombinedFailure.Stage.pairing.rawValue {
                    Button {
                        showPairingSetup = true
                    } label: {
                        Label("Show Pairing Setup", systemImage: "link.badge.plus")
                    }
                    Button {
                        Task {
                            // The pairing file is placed by an external installation tool, so
                            // the authoritative snapshot is the only way to observe it.
                            // It is awaited before the setup steps are recomputed.
                            await setup.reloadAndRecalculate(status: status)
                        }
                    } label: {
                        Label("Re-check Pairing", systemImage: "arrow.clockwise")
                    }
                }
                if setup.testRunning {
                    Button("Stop Waiting", role: .cancel) { setup.cancelTest() }
                } else if (setup.verification.state != "complete" || status.installedHostSigningState != .compatible)
                    && setup.pairing.state == "actionRequired" {
                    Text("Complete Pairing Setup before testing refresh.")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                } else if setup.verification.state != "complete" || status.installedHostSigningState != .compatible {
                    Button {
                        setup.runTestRefresh(status: status)
                    } label: {
                        Label("Run Test Refresh", systemImage: "arrow.clockwise")
                    }
                }
                if let date = setup.lastVerified {
                    Text(V3Localization.format("Last verified %@", V3Localization.date(date, date: .abbreviated, time: .shortened, language: V3AppLanguage.resolve(dateLocale.identifier))))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            // V3_SETUP_COMPLETION_POLICY_V1: when setup is not complete, name
            // the outstanding items from the one shared policy, so the assistant
            // and the Home banner can never disagree about what is left.
            if !setup.outstandingSetup(status: status).isEmpty {
                Section("Still Needed") {
                    ForEach(setup.outstandingSetup(status: status), id: \.self) { item in
                        V3LocalizedLabel(item.title, systemImage: V3StatusSeverity.warning.icon)
                            .font(.footnote)
                            .foregroundColor(.orange)
                    }
                }
            }
            if setup.isComplete(status: status) {
                Section("Setup Complete") {
                    Label("Ready to use", systemImage: "checkmark.circle.fill")
                        .foregroundColor(.green)
                    Text("Account, pairing and a verified refresh are all in place.")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                    Button("Done") { dismiss() }
                }
            }
            Section("Diagnostics") {
                Button(LocalizedStringKey(copiedDiagnostics ? "Copied" : "Copy Setup Diagnostics")) {
                    setup.buildDiagnostics(status: status)
                    UIPasteboard.general.string = V3DiagnosticCopy.details(visibleMessage: setup.verification.detail, technical: setup.diagnostics)
                    copiedDiagnostics = true
                    Task {
                        try? await Task.sleep(nanoseconds: 2_000_000_000)
                        copiedDiagnostics = false
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Setup Assistant")
        .task {
            // V3_AWAITABLE_RELOAD_V1: the first view of the assistant must be
            // built from an authoritative snapshot, not from whatever was left
            // over from a previous session.
            await setup.reloadAndRecalculate(status: status)
        }
        .onChange(of: setup.testRunning) { running in
            guard !running else { return }
            // A refresh mutation invalidates local signing observations. Once
            // this attempt settles (or waiting stops), re-observe without
            // dispatching another refresh or rewriting its historical result.
            Task { await setup.reloadAndRecalculate(status: status) }
        }
        .onChange(of: status.jitlessReadiness) { readiness in
            // A host-owned auth event may invalidate or refresh this fact while
            // Sign In is being dismissed. Recompute from the shared observation.
            guard readiness != nil else { return }
            Task { await setup.recalculate(status: status) }
        }
        .onChange(of: scenePhase) { phase in
            if phase == .active {
                Task {
                    // A pairing file placed by the installation tool while the app
                    // was backgrounded is detected here. The reload must complete
                    // before recalculate reads status, otherwise the setup rows
                    // are computed from the previous snapshot.
                    await setup.reloadAndRecalculate(status: status)
                }
            }
        }
        .onChange(of: showPairingSetup) { presented in
            if !presented {
                Task {
                    await setup.reloadAndRecalculate(status: status)
                }
            }
        }
        .onDisappear {
            if setup.testRunning { setup.cancelTest() }
        }
    }
    private func coredeviceState() -> V3SetupStepState {
        if setup.verification.state == "complete" {
            return V3SetupStepState(state: "complete", detail: "Verified by successful refresh")
        }
        return V3SetupStepState(state: "unavailable", detail: "Checked after a successful refresh")
    }
    @ViewBuilder
    private func setupRow(icon: String, title: String, state: V3SetupStepState, destination: AnyView?) -> some View {
        if let destination {
            NavigationLink(destination: destination.onDisappear {
                // V3_AWAITABLE_RELOAD_V1: returning from a setup destination may
                // follow an action that changed authoritative state, so the
                // snapshot is awaited before the steps are recomputed.
                Task {
                    await setup.reloadAndRecalculate(status: status)
                }
            }) {
                rowContent(icon: icon, title: title, state: state, linked: true)
            }
        } else {
            rowContent(icon: icon, title: title, state: state, linked: false)
        }
    }
    private func rowContent(icon: String, title: String, state: V3SetupStepState, linked: Bool) -> some View {
        HStack(spacing: 12) {
            Image(systemName: stateIcon(state.state))
                .foregroundColor(stateColor(state.state))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                V3LocalizedText(title).font(.headline)
                Group {
                    if state.detailIsOpaque { Text(state.detail) }
                    else { V3LocalizedText(state.detail.isEmpty ? stateLabel(state.state) : state.detail) }
                }
                .font(.caption)
                .foregroundColor(.secondary)
            }
            Spacer()
            if linked {
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(V3Localization.format("%@, %@", V3Localization.prose(title), V3Localization.prose(stateLabel(state.state))))
    }
    private func stateIcon(_ state: String) -> String {
        switch state {
        case "complete": return "checkmark.circle.fill"
        case "actionRequired": return "exclamationmark.circle.fill"
        case "checking", "running": return "clock.arrow.circlepath"
        case "warning": return "exclamationmark.triangle.fill"
        case "failed": return "xmark.circle.fill"
        default: return "minus.circle"
        }
    }
    private func stateColor(_ state: String) -> Color {
        switch state {
        case "complete": return .green
        case "actionRequired": return .orange
        case "warning": return .yellow
        case "failed": return .red
        default: return .secondary
        }
    }
    private func stateLabel(_ state: String) -> String {
        switch state {
        case "complete": return "Ready"
        case "actionRequired": return "Action required"
        case "checking": return "Checking"
        case "running": return "Running"
        case "warning": return "Warning"
        case "failed": return "Failed"
        default: return "Unavailable"
        }
    }
    private func openLocalVPN() {
        NSLog("[V3_SETUP] ACTION step=network action=open")
        vpnWorking = true
        defer { vpnWorking = false }
        guard UIApplication.shared.applicationState == .active,
              let scheme = UserDefaults.lcAppUrlScheme(), !scheme.isEmpty,
              var components = URLComponents(string: "localdevvpn://enable") else { return }
        components.queryItems = [URLQueryItem(name: "scheme", value: scheme)]
        if let url = components.url { UIApplication.shared.open(url) }
    }
    private func openSystemSettings() {
        NSLog("[V3_SETUP] ACTION step=background action=open-settings")
        if let url = URL(string: UIApplication.openSettingsURLString) {
            UIApplication.shared.open(url)
        }
    }

    private func openCanonicalJITLessSetup() {
        status.pendingCanonicalJITLessSetup = true
        status.returnToSetupAfterJITLess = true
        status.setupPresented = false
    }
}

// V3_STATUS_TINT_V1
// The semantic colour for a status. Declared here rather than beside the model
// because the behavioral primitives are also compiled into the SideStoreSupport
// target, which does not import SwiftUI. The model stays presentation-free and
// testable; only this mapping knows about Color.
extension V3StatusPresentation {
    var tint: Color {
        switch severity {
        case .working: return .blue
        case .completed: return .green
        case .warning: return .orange
        case .failed: return .red
        case .cancelled: return .orange
        case .unknown: return .secondary
        }
    }
}

// The JIT-Less presentation carries a severity of its own, so it reuses the
// same mapping rather than inventing a second colour vocabulary.
extension V3JITLessPresentation {
    var status: V3StatusPresentation {
        V3StatusPresentation(severity: severity, title: title, detail: detail)
    }

    var tint: Color { status.tint }
}

struct V3HomeServiceHeader: View {
    @Environment(\.locale) private var dateLocale

    let isConnected: Bool
    let isLoading: Bool
    let updatedAt: Date?
    var onReload: () -> Void = {}

    // V3_RELOAD_STATUS_VISIBILITY_V1
    // isConnected used to win over isLoading, so a reload in progress still
    // rendered a green "Active & Connected" and the only difference was a
    // disabled button, which read as "nothing happened". Loading now has
    // priority, and a successful manual reload is confirmed in place rather
    // than by an intrusive repeated alert.
    private var statusPresentation: V3StatusPresentation {
        V3StatusPresentation.connectionState(connected: isConnected, loading: isLoading)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                Image(systemName: "shippingbox.circle.fill")
                    .font(.system(size: 38))
                    .foregroundColor(.accentColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text("LiveContainer + SideStore")
                        .font(.headline)
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) {
                            if isLoading {
                                ProgressView()
                                    .controlSize(.mini)
                            } else {
                                Circle()
                                    .fill(statusPresentation.tint)
                                    .frame(width: 8, height: 8)
                            }
                            // Icon and text both carry the state, so the meaning
                            // does not depend on colour alone.
                            V3LocalizedLabel(statusPresentation.title, systemImage: statusPresentation.icon)
                                .font(.caption)
                                .foregroundColor(statusPresentation.tint)
                        }
                        if let updatedAt {
                            Text(V3Localization.format("Updated %@", V3Localization.date(updatedAt, date: .omitted, time: .standard, language: V3AppLanguage.resolve(dateLocale.identifier))))
                                .font(.caption2)
                                .foregroundColor(.secondary)
                                .accessibilityLabel(V3Localization.format("Status last updated %@", V3Localization.date(updatedAt, date: .abbreviated, time: .shortened, language: V3AppLanguage.resolve(dateLocale.identifier))))
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Button(action: onReload) {
                Label {
                    V3LocalizedText(isLoading ? "Reloading Status..." : "Reload Status")
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .fixedSize(horizontal: false, vertical: true)
                } icon: {
                    Image(systemName: "arrow.triangle.2.circlepath")
                }
                .font(.caption.weight(.semibold))
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .buttonStyle(.bordered)
            .buttonBorderShape(.capsule)
            .frame(maxWidth: .infinity)
            .disabled(isLoading)
            .accessibilityHint("Reloads the latest SideStore connection and account status. This does not refresh installed apps.")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 4)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(V3Localization.format("SideStore status: %@, %@", V3Localization.prose(statusPresentation.title), V3Localization.prose(statusPresentation.severityName)))
    }
}

private struct V3HomeView: View {
    @Environment(\.locale) private var dateLocale
    @EnvironmentObject private var sharedModel: SharedModel
    @EnvironmentObject private var status: V3SideStoreStatusStore
    @AppStorage("liveContainerAutoRefreshHealthState", store: V3SharedRefreshStore.defaults) private var refreshState = "UNKNOWN"
    private let defaults = V3SharedRefreshStore.defaults
    // The banner is a nudge, not acceptance: it hides only when account,
    // pairing, schedule, Background App Refresh and at least one verified
    // refresh are all in place. Acceptance itself stays in V3SetupStore.
    // V3_SETUP_COMPLETION_POLICY_V1: Home consumes the same policy as the Setup
    // Assistant. The banner used to omit JIT-Less, network, and tunnel, so it
    // could disappear while the assistant still considered setup incomplete.
    private var setupIncomplete: Bool { !V3HomeView.completionInputs(status: status, defaults: defaults).isComplete }

    /// Derived from the same authoritative facts the Setup Assistant uses.
    static func completionInputs(status: V3SideStoreStatusStore,
                                 defaults: UserDefaults?) -> V3SetupCompletionInputs {
        let verifiedRefresh = V3HomeRefreshVerificationPolicy.isVerified(
            manifest: defaults?.dictionary(forKey: "liveContainerAutoRefreshVerification"),
            ledger: defaults?.dictionary(forKey: "liveContainerAutoRefreshRunLedger") ?? [:],
            activeRunID: defaults?.string(forKey: "liveContainerAutoRefreshActiveRunID"),
            hostHandoffPending: defaults?.bool(forKey: "liveContainerAutoRefreshHostHandoff") ?? false,
            uncertainMutationRunID: defaults?.string(forKey: "liveContainerAutoRefreshUncertainMutationRunID"))
        return V3SetupCompletionInputs(
            accountComplete: !status.needsSignIn,
            provisioningIncomplete: status.provisioningIncomplete,
            pairingSatisfied: V3PairingPresentationPolicy.isConfirmed(
                statusConnected: status.connected, pairingStatus: status.pairing),
            // JIT-Less is only a prerequisite on the platforms that require it.
            jitlessRequired: V3JITLessCompletionPolicy.isRequired(
                osMajor: ProcessInfo.processInfo.operatingSystemVersion.majorVersion),
            // V3_SHARED_JITLESS_FACT_V1: Home reads the same observed readiness the
            // Setup Assistant uses. It previously hard-coded "incomplete wherever
            // JIT-Less is required", so a verified copy left the banner up forever
            // while the assistant showed the item complete.
            jitlessComplete: V3JITLessCompletionPolicy.isComplete(status.jitlessReadiness),
            networkComplete: status.wifiAvailable == true,
            tunnelComplete: LiveContainerNetworkPreflight.hasTunnelInterface(),
            backgroundRefreshAvailable: UIApplication.shared.backgroundRefreshStatus == .available,
            scheduleEnabled: defaults?.bool(forKey: "liveContainerAutoRefreshEnabled") ?? false,
            verifiedRefreshPresent: verifiedRefresh,
            installedHostSigningCompatible: status.installedHostSigningState == .compatible)
    }

    var body: some View {
        NavigationView {
            List {
                Section {
                    VStack(alignment: .leading, spacing: 14) {
                        V3HomeServiceHeader(isConnected: status.connected, isLoading: status.loading,
                                            updatedAt: status.updatedAt) {
                            status.reload()
                        }

                        Divider()

                        HStack(spacing: 0) {
                            Button {
                                sharedModel.selectedTab = .apps
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("\(sharedModel.apps.count)")
                                        .font(.title2.weight(.bold))
                                    Text("Guests")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .buttonStyle(.plain)

                            Divider().frame(height: 28)

                            Button {
                                sharedModel.selectedTab = .apps
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("\(status.installedAppCount)")
                                        .font(.title2.weight(.bold))
                                    Text("Sideloaded")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.leading, 12)
                            }
                            .buttonStyle(.plain)

                            Divider().frame(height: 28)

                            Button {
                                sharedModel.selectedTab = .apps
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    if let date = status.installedApps.filter({ $0.isActive }).compactMap(\.expirationDate).min() {
                                        Text(date, style: .relative)
                                            .font(.callout.weight(.bold))
                                            .foregroundColor(Calendar.current.dateComponents([.day], from: Date(), to: date).day ?? 0 <= 2 ? .red : .orange)
                                        Text("Next Expiry")
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    } else {
                                        Text("-")
                                            .font(.title2.weight(.bold))
                                        Text("Next Expiry")
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.leading, 12)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.vertical, 4)
                }

                if setupIncomplete {
                    Section {
                        Button {
                            NSLog("[V3_SETUP] OPEN source=home")
                            status.setupPresented = true
                        } label: {
                            HStack {
                                Label("Finish Setup", systemImage: "list.clipboard.fill")
                                Spacer()
                                Image(systemName: "chevron.right")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                }

                Section("Status & Identity") {
                    NavigationLink {
                        V3SignInView().environmentObject(status)
                    } label: {
                        HStack {
                            Label("Apple ID", systemImage: "person.crop.circle")
                            Spacer()
                            Group {
                            if status.activeAccountPresent { Text(status.account) }
                            else { Text("Not available") }
                        }
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }
                    NavigationLink {
                        V3DeveloperServicesView().environmentObject(status)
                    } label: {
                        HStack {
                            Label("Developer Team", systemImage: "person.2")
                            Spacer()
                            Group {
                            if status.activeTeamPresent { Text(status.team) }
                            else { Text("Not available") }
                        }
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }
                    NavigationLink {
                        V3CertificatesView().environmentObject(status)
                    } label: {
                        HStack {
                            Label("Signing Status", systemImage: "signature")
                            Spacer()
                            V3LocalizedText(status.signing)
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }
                    NavigationLink {
                        V3PairingView().environmentObject(status)
                    } label: {
                        HStack {
                            Label("Pairing Status", systemImage: "link")
                            Spacer()
                            V3LocalizedText(V3PairingPresentationPolicy.displayText(statusConnected: status.connected,
                                pairingStatus: status.pairing))
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }
                    if let date = status.certificateExpiration {
                        NavigationLink {
                            V3CertificatesView().environmentObject(status)
                        } label: {
                            HStack {
                                Label("Certificate Expiry", systemImage: "calendar.badge.clock")
                                Spacer()
                                Text(V3Localization.date(date, date: .abbreviated, time: .shortened, language: V3AppLanguage.resolve(dateLocale.identifier)))
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                }

                Section("Background Refresh") {
                    HStack {
                        Label("Daemon Health", systemImage: "bolt.badge.clock")
                        Spacer()
                        V3LocalizedText(refreshState.replacingOccurrences(of: "_", with: " ").capitalized)
                            .foregroundColor(.secondary)
                    }
                    if let date = defaults.object(forKey: "liveContainerAutoRefreshLastSuccessfulRefresh") as? Date {
                        HStack {
                            Label("Last Verified Run", systemImage: "checkmark.circle")
                            Spacer()
                            Text(V3Localization.date(date, date: .abbreviated, time: .shortened, language: V3AppLanguage.resolve(dateLocale.identifier)))
                                .foregroundColor(.secondary)
                        }
                    }
                    if let date = defaults.object(forKey: "liveContainerAutoRefreshTargetDeadline") as? Date {
                        HStack {
                            Label("Refresh Deadline", systemImage: "hourglass")
                            Spacer()
                            Text(V3Localization.date(date, date: .abbreviated, time: .shortened, language: V3AppLanguage.resolve(dateLocale.identifier)))
                                .foregroundColor(.secondary)
                        }
                    }
                    if let error = defaults.string(forKey: "liveContainerAutoRefreshLastError"), !error.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Label("Last Refresh Warning", systemImage: "exclamationmark.triangle")
                                .foregroundColor(.red)
                                .font(.caption)
                            V3LocalizedText(V3DiagnosticPresentation.label(error, context: .refresh))
                                .font(.caption2)
                                .foregroundColor(.red)
                        }
                    }
                    NavigationLink(isActive: $status.refreshPresented) {
                        V3RefreshDetailView().environmentObject(status)
                    } label: {
                        Label("Open Refresh Manager", systemImage: "arrow.clockwise")
                    }
                }

                Section("About") {
                    Text("LiveContainer + SideStore unified build")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                    if let url = URL(string: "https://github.com/NRG-Wardog") {
                        Link(destination: url) {
                            Label("NRG-Wardog on GitHub", systemImage: "link")
                        }
                    }
                    if let product = Bundle.main.object(forInfoDictionaryKey: "LCProductLine") as? String {
                        Text(product)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Home")
        }
        .navigationViewStyle(StackNavigationViewStyle())
    }
}
// V3_UNIFIED_SHELL_V1_END
