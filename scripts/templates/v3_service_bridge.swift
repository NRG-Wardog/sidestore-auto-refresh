
// V3_CATALOG_OPERATION_CONTEXT_V1
// A catalog request must keep its operation context even when the failure
// happens before the backend catalog query runs. The truthful failing
// component is never rewritten: a connection failure stays operation=connect
// with its real stage and code, and the waiting request is recorded alongside
// it as host-rendered request context. This keeps service startup, XPC, busy,
// and invalid-response failures distinguishable instead of collapsing into
// "SideStore could not start or complete the requested action."
enum V3CatalogRequestContext {
    /// The stage a host-side catalog boundary failure belongs to. A catalog read
    /// is reported against the catalog stage; every other operation keeps the
    /// generic command wire boundary.
    static func hostStage(for operation: String) -> CombinedFailure.Stage {
        operation == "catalog" ? .catalog : .command
    }

    /// A successfully received reply that the service could not encode, or
    /// that exceeded the shared byte limit, failed at the reply boundary.
    static func replyEncodingStage(for operation: String) -> CombinedFailure.Stage {
        _ = operation
        return .replyEncoding
    }

    /// Map a plain service error token to a typed host failure without inventing
    /// a cause. "notReady" is service startup, "busy" is service contention,
    /// and an oversized or unparseable reply is an invalid response.
    static func hostFailure(errorToken: String, operation: String, id: String) -> CombinedFailure {
        let stage = hostStage(for: operation)
        switch errorToken {
        case "notReady":
            return CombinedFailure(operation: operation, stage: .serviceReadiness, code: .notReady,
                                   id: id, retryable: true)
        case "busy":
            return CombinedFailure(operation: operation, stage: stage, code: .busy, id: id, retryable: true)
        case "responseTooLarge":
            // V3_RESPONSE_ENCODING_CLASSIFICATION_V1: correctly serialized, but
            // too large to transfer. Distinct from both an encoding failure and a
            // reply that could not be parsed.
            return CombinedFailure(operation: operation, stage: replyEncodingStage(for: operation),
                                  code: .invalidResponse, id: id,
                                  safeCause: .responseTooLarge)
        case "responseEncodingFailed":
            // V3_RESPONSE_ENCODING_CLASSIFICATION_V1: the service could not
            // serialize its reply at all. This is a distinct defect from an
            // oversized reply and is never reported as one.
            return CombinedFailure(operation: operation, stage: replyEncodingStage(for: operation),
                                  code: .invalidResponse, id: id,
                                  safeCause: .responseEncodingFailed)
        case "invalidRequest":
            return CombinedFailure(operation: operation, stage: .command, code: .invalidConfiguration, id: id)
        case "cancelled":
            return CombinedFailure(operation: operation, stage: stage, code: .cancelled, id: id)
        default:
            if let code = CombinedFailure.Code(rawValue: errorToken) {
                return CombinedFailure(operation: operation, stage: stage, code: code, id: id)
            }
            // No cause is invented for an unknown token. The source manifest is
            // explicitly not blamed, because nothing proved it failed to parse.
            return CombinedFailure(operation: operation, stage: stage, code: .failed, id: id)
        }
    }

    // V3_RESPONSE_CLASSIFICATION_CARRIER_V1
    // Classifies one service reply. This is the exact production path, kept pure
    // so a real service fallback envelope can be run through it.
    //
    // Precedence is deliberate and unchanged: the structured `failure` envelope
    // wins over the legacy string `error` token, because it carries the
    // operation, stage, correlation, retryability and safe cause that the token
    // cannot express. The token is consulted only when there is no decodable
    // envelope, which is the case for a foreign or older service. The
    // classification of a reply the service could not deliver therefore has to
    // travel inside the structured envelope, which is what
    // V3ResponseClassifier.safeCause(for:) is for.
    static func classifyReply(_ response: Data, operation: String, id: String) throws -> [String: Any] {
        guard let decoded = try PropertyListSerialization.propertyList(from: response, format: nil) as? [String: Any] else {
            throw CombinedFailure(operation: operation, stage: hostStage(for: operation),
                                  code: .invalidResponse, id: id)
        }
        guard V3WireContract.strictInt(decoded["version"]) == 1 else {
            throw CombinedFailure(operation: operation, stage: hostStage(for: operation),
                                  code: .invalidResponse, id: id)
        }
        guard let responseID = decoded["id"] as? String,
              UUID(uuidString: responseID) != nil else {
            throw CombinedFailure(operation: operation, stage: hostStage(for: operation),
                                  code: .invalidResponse, id: id)
        }
        guard CombinedFailure.uuidCorrelationMatches(responseID, expectedID: id) else {
            // Genuine cross-request protocol evidence. It is never resolved to
            // the waiting caller, and it is never reported as a serialization
            // defect it did not prove.
            throw CombinedFailure(operation: operation, stage: .command, code: .staleResult, id: id)
        }
        if decoded["failure"] != nil {
            guard let envelope = decoded["failure"] as? [String: Any],
                  let failure = CombinedFailure.decode(envelope, expectedID: id) else {
                throw CombinedFailure(operation: operation, stage: hostStage(for: operation),
                    code: .invalidResponse, id: id, retryable: false)
            }
            throw failure
        }
        if let code = decoded["error"] as? String {
            throw hostFailure(errorToken: code, operation: operation, id: id)
        }
        guard V3WireContract.strictBool(decoded["ok"]) == true,
              let result = decoded["result"] as? [String: Any] else {
            throw CombinedFailure(operation: operation, stage: hostStage(for: operation),
                                  code: .invalidResponse, id: id)
        }
        return result
    }

    /// Attach the waiting request to a pre-dispatch connection failure. The
    /// connection failure's own operation, stage, code, retryability, safe
    /// cause, source step, and correlation are preserved exactly, because
    /// operation=connect is what proves no mutation ran.
    static func annotating(_ error: Error, requestedOperation: String, requestID: String) -> Error {
        guard var combined = error as? CombinedFailure else {
            return CombinedFailure(operation: requestedOperation, stage: .command, code: .failed,
                                   id: requestID, underlying: error)
        }
        guard combined.operation != requestedOperation else { return combined }
        combined.annotatingRequest(requestedOperation: requestedOperation, requestID: requestID)
        return combined
    }
}

// V3_HOST_COMMAND_BRIDGE_V1
@MainActor
public final class V3ServiceBridge {
    public static let shared = V3ServiceBridge()
    public static func strictBool(_ value: Any?) -> Bool? {
        V3WireContract.strictBool(value)
    }
    public static func strictInt(_ value: Any?) -> Int? {
        V3WireContract.strictInt(value)
    }
    public static var authSessionLifetime: TimeInterval {
        V3WireContract.authSessionLifetime
    }
    public static func authSnapshot(_ reply: [String: Any]) -> V3AuthServiceSnapshot? {
        V3WireContract.authSnapshot(reply)
    }
    private var pending: [String: CheckedContinuation<Data, Error>] = [:]
    private var pendingOperations: [String: String] = [:]
    private var timeouts: [String: Task<Void, Never>] = [:]
    private var cancellationRecovery: [String: Task<Void, Never>] = [:]
    private var activeOperationSessions: Set<String> = []
    private var uncertainOperationSessions: Set<String> = []
    private var knownOperationSessions: [String: Date] = [:]
    private var operationMonitors: [String: Task<Void, Never>] = [:]
    private let readTimeout: TimeInterval
    private let commandTimeout: TimeInterval
    private let cancellationGrace: TimeInterval
    private var activeMutation: String?
    private var authSessionOwnership = V3AuthSessionOwnership()
    private var statusWriteAuthority = V3StatusWriteAuthority()
    private var hostRecoveryHoldActive = false
    private struct StatusLeaseWaiter {
        let id: String
        let ownerID: String
        let revision: UInt64
        let kind: V3StatusAuthorityLeaseKind
        let allowUnresolvedMutation: Bool
        let continuation: CheckedContinuation<V3StatusWriteTicket, Error>
    }
    private var statusLeaseWaiters: [StatusLeaseWaiter] = []
    private var statusLeaseWaiterOrder = V3StatusLeaseWaiterOrder()
    private var statusLeaseByRequestID: [String: V3StatusWriteTicket] = [:]
    private var statusReplyTicketByRequestID: [String: V3StatusWriteTicket] = [:]
    private var statusDispatchedRequestIDs: Set<String> = []
    public var isMutating: Bool {
        authSessionOwnership.hasActiveSession() || activeMutation != nil ||
        !activeOperationSessions.isEmpty || !cancellationRecovery.isEmpty ||
            statusWriteAuthority.hasActiveWrite || statusWriteAuthority.hasUnresolvedMutation ||
            hostRecoveryHoldActive || RefreshHandler.shared.v3RefreshToken != nil
    }

    private func anotherHostMutationActiveForRefreshControl(operation: String, target: String) -> Bool {
        let ownsRefreshRun = ["refreshAdmissionBegin", "refreshAdmissionEnd"].contains(operation) &&
            RefreshHandler.shared.v3RefreshToken != nil &&
            RefreshHandler.shared.v3RefreshAdmissionRunID == target
        let matchingStatusLease = statusWriteAuthority.activeLease?.ownerID == "refresh:\(target)"
        return authSessionOwnership.hasActiveSession() || activeMutation != nil ||
            !activeOperationSessions.isEmpty || !cancellationRecovery.isEmpty ||
            statusWriteAuthority.hasUnresolvedMutation || hostRecoveryHoldActive ||
            (statusWriteAuthority.hasActiveWrite && !matchingStatusLease) ||
            (RefreshHandler.shared.v3RefreshToken != nil && !ownsRefreshRun)
    }

    private var currentStatusServiceInstanceID: String {
        String(RefreshHandler.shared.sideStorePid)
    }

    public func setHostRecoveryHold(_ active: Bool) {
        hostRecoveryHoldActive = active
    }

    private func acquireStatusLease(ownerID: String,
                                    kind: V3StatusAuthorityLeaseKind,
                                    allowUnresolvedMutation: Bool = false) async throws -> V3StatusWriteTicket {
        try Task.checkCancellation()
        if kind == .mutation && statusWriteAuthority.hasUnresolvedMutation && !allowUnresolvedMutation {
            throw CombinedFailure(operation: "command", stage: .command, code: .busy,
                id: ownerID, retryable: false, safeCause: .operationInProgress)
        }
        let waiterID = UUID().uuidString
        let reservedRevision: UInt64
        if kind == .mutation {
            reservedRevision = statusWriteAuthority.reserveMutationRevision()
            NotificationCenter.default.post(name: Notification.Name("V3StatusMutationReserved"), object: nil)
        } else {
            reservedRevision = statusWriteAuthority.revision
        }
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                if Task.isCancelled {
                    continuation.resume(throwing: CancellationError())
                } else if statusWriteAuthority.canBegin(kind: kind,
                            allowUnresolvedMutation: allowUnresolvedMutation) && statusLeaseWaiterOrder.count == 0,
                          let ticket = statusWriteAuthority.begin(ownerID: ownerID,
                            revision: reservedRevision,
                            serviceInstanceID: currentStatusServiceInstanceID, kind: kind,
                            allowUnresolvedMutation: allowUnresolvedMutation) {
                    continuation.resume(returning: ticket)
                } else {
                    statusLeaseWaiterOrder.enqueue(waiterID)
                    statusLeaseWaiters.append(StatusLeaseWaiter(id: waiterID,
                        ownerID: ownerID, revision: reservedRevision,
                        kind: kind, allowUnresolvedMutation: allowUnresolvedMutation,
                        continuation: continuation))
                }
            }
        }, onCancel: {
            Task { @MainActor [weak self] in self?.cancelStatusLeaseWaiter(waiterID) }
        })
    }

    private func cancelStatusLeaseWaiter(_ waiterID: String) {
        statusLeaseWaiterOrder.remove(waiterID)
        guard let index = statusLeaseWaiters.firstIndex(where: { $0.id == waiterID }) else { return }
        let waiter = statusLeaseWaiters.remove(at: index)
        waiter.continuation.resume(throwing: CancellationError())
    }

    private func drainStatusLeaseWaiters() {
        guard statusWriteAuthority.activeLease == nil else { return }
        while let waiterID = statusLeaseWaiterOrder.takeNext() {
            guard let index = statusLeaseWaiters.firstIndex(where: { $0.id == waiterID }) else { continue }
            let waiter = statusLeaseWaiters.remove(at: index)
            if waiter.kind == .mutation && statusWriteAuthority.hasUnresolvedMutation &&
               !waiter.allowUnresolvedMutation {
                waiter.continuation.resume(throwing: CombinedFailure(operation: "command",
                    stage: .command, code: .busy, id: waiter.id,
                    retryable: false, safeCause: .operationInProgress))
                continue
            }
            guard let ticket = statusWriteAuthority.begin(ownerID: waiter.ownerID,
                revision: waiter.revision,
                serviceInstanceID: currentStatusServiceInstanceID, kind: waiter.kind,
                allowUnresolvedMutation: waiter.allowUnresolvedMutation) else {
                waiter.continuation.resume(throwing: CombinedFailure(operation: "command",
                    stage: .command, code: .interrupted, id: waiter.id, retryable: true))
                continue
            }
            waiter.continuation.resume(returning: ticket)
            return
        }
    }

    private func completeStatusLease(_ ticket: V3StatusWriteTicket,
                                     outcome: V3StatusWriteOutcome,
                                     replyRequestID: String? = nil) {
        guard statusWriteAuthority.complete(ticket, outcome: outcome) else { return }
        if outcome != .outcomeUnknown || ticket.kind == .snapshot {
            for requestID in Array(statusLeaseByRequestID.keys) where
                statusLeaseByRequestID[requestID]?.ownerID == ticket.ownerID {
                statusLeaseByRequestID.removeValue(forKey: requestID)
            }
        }
        if let replyRequestID { statusReplyTicketByRequestID[replyRequestID] = ticket }
        drainStatusLeaseWaiters()
    }

    private func observeConnectedStatusService(ownerID: String?) -> V3StatusWriteTicket? {
        guard let ticket = statusWriteAuthority.observeServiceInstance(
            currentStatusServiceInstanceID, continuingOwnerID: ownerID) else { return nil }
        for requestID in Array(statusLeaseByRequestID.keys) where
            statusLeaseByRequestID[requestID]?.ownerID == ticket.ownerID {
            statusLeaseByRequestID[requestID] = ticket
        }
        return ticket
    }

    private func resolveUnknownStatusOwner(_ ownerID: String) {
        let resolved = statusWriteAuthority.resolveOwnerAfterReconciliation(ownerID)
        for requestID in Array(statusLeaseByRequestID.keys) where
            statusLeaseByRequestID[requestID]?.ownerID == ownerID {
            statusLeaseByRequestID.removeValue(forKey: requestID)
        }
        if resolved {
            NotificationCenter.default.post(name: Notification.Name("V3StatusAuthorityChanged"), object: nil)
        }
    }

    private func statusResponse(_ data: Data, requestID: String) -> [String: Any]? {
        guard data.count <= V3WireContract.responseLimit,
              let envelope = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              V3WireContract.strictInt(envelope["version"]) == 1,
              envelope["id"] as? String == requestID else { return nil }
        return envelope
    }

    private func observeStatusLeaseResponse(requestID: String, operation: String,
                                            target: String, payload: [String: Any]?,
                                            data: Data) {
        let ticket = statusLeaseByRequestID[requestID]
        guard let envelope = statusResponse(data, requestID: requestID) else {
            if let ticket,
               ticket.kind == .snapshot || ticket.ownerID == "request:\(requestID)" {
                completeStatusLease(ticket, outcome: .outcomeUnknown)
            } else if operation == "directRecoveryReconcile",
                      let ticket, let active = statusWriteAuthority.activeLease, active == ticket {
                // A malformed control reply cannot create a second unknown
                // owner; the exact target recovery record remains authoritative.
                completeStatusLease(ticket, outcome: .failed)
            }
            return
        }
        let result = envelope["result"] as? [String: Any] ?? [:]
        let confirmedNotDispatched = V3WireContract.strictBool(envelope["operationNotDispatched"]) == true
        if V3AuthSessionUnavailableReplyPolicy.confirmsUnavailable(
            operation: operation, target: target, requestID: requestID, envelope: envelope) {
            confirmAuthSessionUnavailable(sessionID: target)
            return
        }
        if operation == "directRecoveryReconcile",
           V3WireContract.strictBool(envelope["ok"]) != true {
            if let ticket, let active = statusWriteAuthority.activeLease, active == ticket {
                completeStatusLease(active, outcome: .failed)
            }
            return
        }
        if ["directRecoveryInspect", "directRecoveryReconcile"].contains(operation),
           (UUID(uuidString: target)?.uuidString != target || result["requestID"] as? String != target) {
            if operation == "directRecoveryReconcile", let ticket,
               let active = statusWriteAuthority.activeLease, active == ticket {
                completeStatusLease(active, outcome: .failed)
            }
            return
        }
        let requestedStartSession = operation == "opStart" ? payload?["session"] as? String : nil
        if !confirmedNotDispatched && !V3OperationSessionCorrelationPolicy.matches(operation: operation, target: target,
            requestedStartSession: requestedStartSession, resultSession: result["session"] as? String) { return }
        if !confirmedNotDispatched && ["authBegin", "authRetryProvisioning", "authPoll", "authRespond", "authCancel"].contains(operation),
           let expectedSession = operationSessionID(operation: operation, target: target, payload: payload),
           result["session"] as? String != expectedSession { return }
        let candidateOwner = ticket?.ownerID ??
            V3StatusAuthorityOperationPolicy.controlOwnerID(
            operation: operation, sessionID: operationSessionID(operation: operation,
                target: target, payload: payload))
        guard let ownerID = ticket?.ownerID ?? candidateOwner else { return }
        guard let active = statusWriteAuthority.activeLease else {
            let outcome: V3StatusWriteOutcome?
            if ownerID == "request:\(requestID)" {
                outcome = confirmedNotDispatched ? .notDispatched :
                    (V3WireContract.strictBool(envelope["ok"]) == true ? .committed : .outcomeUnknown)
            } else {
                outcome = V3StatusAuthorityOperationPolicy.terminalOutcome(
                    operation: operation, result: envelope)
            }
            if let outcome, outcome != .outcomeUnknown { resolveUnknownStatusOwner(ownerID) }
            return
        }
        guard active.ownerID == ownerID else {
            let outcome: V3StatusWriteOutcome?
            if ownerID == "request:\(requestID)" {
                outcome = confirmedNotDispatched ? .notDispatched :
                    (V3WireContract.strictBool(envelope["ok"]) == true ? .committed : .outcomeUnknown)
            } else {
                outcome = V3StatusAuthorityOperationPolicy.terminalOutcome(
                    operation: operation, result: envelope)
            }
            if let outcome, outcome != .outcomeUnknown {
                resolveUnknownStatusOwner(ownerID)
            }
            return
        }

        let outcome: V3StatusWriteOutcome?
        if active.kind == .snapshot {
            if confirmedNotDispatched { outcome = .notDispatched }
            else { outcome = V3WireContract.strictBool(envelope["ok"]) == true ? .committed : .failed }
        } else if active.ownerID == "request:\(requestID)" {
            if confirmedNotDispatched { outcome = .notDispatched }
            else { outcome = V3WireContract.strictBool(envelope["ok"]) == true ? .committed : .outcomeUnknown }
        } else if let ticket, ticket.ownerID == active.ownerID {
            if confirmedNotDispatched { outcome = .notDispatched }
            else { outcome = V3StatusAuthorityOperationPolicy.terminalOutcome(
                operation: operation, result: envelope) }
        } else if confirmedNotDispatched {
            return
        } else {
            outcome = V3StatusAuthorityOperationPolicy.terminalOutcome(
                operation: operation, result: envelope)
        }
        guard let outcome else { return }
        let replyCanReturn = pending[requestID] != nil && outcome == .committed
        completeStatusLease(active, outcome: outcome,
            replyRequestID: replyCanReturn &&
                (active.kind == .snapshot || active.ownerID == "request:\(requestID)")
                ? requestID : nil)
        if operation == "directRecoveryReconcile",
           result["requestID"] as? String == target,
           V3WireContract.strictBool(result["reconciled"]) == true {
            resolveUnknownStatusOwner("request:\(target)")
            NotificationCenter.default.post(name: Notification.Name("V3StatusAuthorityChanged"), object: nil)
        }
    }

    private func attachStatusReplyTicket(_ result: [String: Any], requestID: String) -> [String: Any] {
        guard let ticket = statusReplyTicketByRequestID.removeValue(forKey: requestID) else { return result }
        var tagged = result
        tagged["_v3StatusAuthorityTicket"] = ticket
        tagged["_v3StatusAuthorityRequestID"] = requestID
        return tagged
    }

    /// ACKs only an exact terminal direct request after its feature caller has
    /// accepted the successful result. The certCreate partial-remote outcome is
    /// deliberately left in recovery for a manual check.
    public func acknowledgeDirectRecoveryAfterSuccess(_ result: [String: Any],
                                                       operation: String) async -> Bool {
        guard V3DirectRecoveryHostPolicy.mayAcknowledgeSuccessfulResponse(
                operation: operation, result: result),
              let requestID = result["_v3StatusAuthorityRequestID"] as? String,
              UUID(uuidString: requestID)?.uuidString == requestID else { return false }
        // Close host admission while the durable terminal slot is being
        // acknowledged. A current quiescent snapshot reopens it after proof.
        setHostRecoveryHold(true)
        do {
            let ack = try await request(operation: "directRecoveryReconcile", target: requestID,
                payload: ["ackTerminal": true])
            guard ack["requestID"] as? String == requestID,
                  V3WireContract.strictBool(ack["reconciled"]) == true else {
                NotificationCenter.default.post(name: Notification.Name("V3StatusAuthorityChanged"), object: nil)
                return false
            }
            NotificationCenter.default.post(name: Notification.Name("V3StatusAuthorityChanged"), object: nil)
            return true
        } catch {
            // Preserve the original successful result in the caller. The durable
            // recovery record remains visible on the next authoritative snapshot.
            NotificationCenter.default.post(name: Notification.Name("V3StatusAuthorityChanged"), object: nil)
            return false
        }
    }


    public func statusReplyMayApply(_ reply: [String: Any]) -> Bool {
        guard let ticket = reply["_v3StatusAuthorityTicket"] as? V3StatusWriteTicket else { return false }
        let busyValue: Bool?
        if ticket.kind == .snapshot {
            guard let typedBusy = V3WireContract.strictBool(reply["busy"]),
                  V3WireContract.strictBool(reply["activeMutation"]) == false else { return false }
            // These ownership facts are emitted on every current service
            // snapshot. Missing or malformed fields cannot authorize a commit.
            guard V3WireContract.strictBool(reply["activeMutation"]) != nil,
                  V3WireContract.strictBool(reply["recoveryHold"]) != nil else { return false }
            busyValue = typedBusy
        } else {
            busyValue = false
        }
        return V3StatusReplyCommitPolicy.mayApply(ticket, authority: statusWriteAuthority,
            currentServiceEpoch: statusWriteAuthority.serviceEpoch,
            currentServiceInstanceID: currentStatusServiceInstanceID,
            busySnapshot: busyValue == true)
    }

    public func statusReplyMayApplyRecoveryEvidence(_ reply: [String: Any]) -> Bool {
        guard let ticket = reply["_v3StatusAuthorityTicket"] as? V3StatusWriteTicket,
              ticket.kind == .snapshot,
              let busy = V3WireContract.strictBool(reply["busy"]), busy else { return false }
        let hasActiveMutationField = reply.keys.contains("activeMutation")
        let activeMutation = V3WireContract.strictBool(reply["activeMutation"])
        guard hasActiveMutationField, activeMutation == false,
              V3WireContract.strictBool(reply["recoveryHold"]) == true,
              V3StatusRecoveryEvidencePolicy.mayApply(
                busySnapshot: busy, activeMutation: activeMutation,
                hasDurableRecoveryEvidence: V3StatusRecoveryEvidencePolicy.hasRecoveryEvidence(reply)) else {
            return false
        }
        return V3StatusReplyCommitPolicy.mayApply(ticket, authority: statusWriteAuthority,
            currentServiceEpoch: statusWriteAuthority.serviceEpoch,
            currentServiceInstanceID: currentStatusServiceInstanceID)
    }

    public func hasUncertainOperationSession(_ sessionID: String) -> Bool {
        uncertainOperationSessions.contains(sessionID)
    }
    /// Clear only a host owner for a session SideStore explicitly reports as
    /// unavailable. Transport loss and malformed replies retain ownership.
    public func confirmAuthSessionUnavailable(sessionID: String) {
        guard UUID(uuidString: sessionID)?.uuidString == sessionID else { return }
        authSessionOwnership.clear(sessionID: sessionID)
        resolveUnknownStatusOwner("auth:\(sessionID)")
    }
    /// A validated service snapshot can retire a host owner when it proves
    /// there is no active authentication task, even if the terminal poll was lost.
    public func reconcileAuthSessionOwnership(sessionID: String, authenticationActive: Bool) {
        authSessionOwnership.reconcile(sessionID: sessionID, authenticationActive: authenticationActive)
        guard !authenticationActive,
              UUID(uuidString: sessionID)?.uuidString == sessionID else { return }
        resolveUnknownStatusOwner("auth:\(sessionID)")
    }
    public var processID: Int32 { RefreshHandler.shared.sideStorePid }

    init(readTimeout: TimeInterval = 30, commandTimeout: TimeInterval = 600, cancellationGrace: TimeInterval = 3) {
        self.readTimeout = readTimeout
        self.commandTimeout = commandTimeout
        self.cancellationGrace = cancellationGrace
    }

    public func connect() async throws {
        try await RefreshHandler.shared.ensureServiceConnected()
    }

    /// Retire a session whose native result is unknown only after the user
    /// confirms that the device operation has stopped. Process retirement alone
    /// never declares the mutation successful.
    @discardableResult
    public func confirmUncertainOperationAfterDeviceCheck(sessionID: String) -> Bool {
        guard uncertainOperationSessions.contains(sessionID) else { return false }
        activeOperationSessions.remove(sessionID)
        uncertainOperationSessions.remove(sessionID)
        operationMonitors.removeValue(forKey: sessionID)?.cancel()
        knownOperationSessions.removeValue(forKey: sessionID)
        RefreshHandler.shared.v3_stopService()
        disconnected()
        return true
    }

    /// Retire the service after the explicit recovery RPC has cleared the exact
    /// durable session. This never runs on process restart or a timeout.
    public func retireReconciledOperationService(sessionID: String) {
        activeOperationSessions.remove(sessionID)
        uncertainOperationSessions.remove(sessionID)
        operationMonitors.removeValue(forKey: sessionID)?.cancel()
        knownOperationSessions.removeValue(forKey: sessionID)
        RefreshHandler.shared.v3_stopService()
        disconnected()
    }

    public func retireReconciledRefreshService(runID: String) {
        guard UUID(uuidString: runID)?.uuidString == runID else { return }
        RefreshHandler.shared.v3_stopService()
        disconnected()
    }

    public func forgetSettledOperationSession(_ sessionID: String) {
        guard !activeOperationSessions.contains(sessionID),
              !uncertainOperationSessions.contains(sessionID) else { return }
        knownOperationSessions.removeValue(forKey: sessionID)
    }

    private func operationSessionID(operation: String, target: String,
                                    payload: [String: Any]?) -> String? {
        if operation == "opStart" { return payload?["session"] as? String }
        if ["opPoll", "opAnswer", "opCancel"].contains(operation) { return target }
        if ["authBegin", "authRetryProvisioning"].contains(operation) {
            return payload?["session"] as? String ?? (target.isEmpty ? nil : target)
        }
        if ["authPoll", "authRespond", "authCancel",
            "opRecoveryReconcile", "directRecoveryInspect", "directRecoveryReconcile",
            "refreshAdmissionBegin", "refreshAdmissionEnd", "refreshAdmissionReconcile"].contains(operation) {
            return target
        }
        return nil
    }

    public func request(operation: String, target: String = "", cursor: Int? = nil,
                        payload: [String: Any]? = nil, requestDeadline: Date? = nil) async throws -> [String: Any] {
        try Task.checkCancellation()
        if ["authBegin", "authRetryProvisioning", "signOut", "accountImport"].contains(operation) {
            NotificationCenter.default.post(name: Notification.Name("V3AuthIdentityTransition"), object: nil)
        }
        // V3_CATALOG_OPERATION_CONTEXT_V1: the request correlation is minted
        // before connecting, so a failure that happens before the service
        // receives the request can still be attributed to the caller's actual
        // operation instead of only to the connection attempt.
        let id = UUID().uuidString
        let operationSessionID = operationSessionID(operation: operation, target: target, payload: payload)
        let scopedSessionControl = ["opAnswer", "opCancel"].contains(operation) &&
            activeOperationSessions.contains(target)
        let explicitRecoveryConfirmation = operation == "opRecoveryReconcile" &&
            V3WireContract.strictBool(payload?["userConfirmed"]) == true &&
            UUID(uuidString: target)?.uuidString == target
        let directRecoveryTerminalAck = V3WireContract.strictBool(payload?["ackTerminal"]) == true
        let directRecoveryUserCheck = V3WireContract.strictBool(payload?["userConfirmed"]) == true
        let explicitDirectRecoveryControl = operation == "directRecoveryReconcile" &&
            UUID(uuidString: target)?.uuidString == target &&
            directRecoveryTerminalAck != directRecoveryUserCheck
        let scopedAuthSessionControl = ["authRespond", "authCancel"].contains(operation) &&
            authSessionOwnership.owns(target)
        let replacesAuthSession = ["authBegin", "authRetryProvisioning"].contains(operation) &&
            authSessionOwnership.hasActiveSession()
        let mutation = !V3WireContract.readOperations.contains(operation) ||
            ["opAnswer", "opCancel", "authCancel"].contains(operation)
        let scopedRefreshAdmissionControl = V3ServiceMutationAdmissionPolicy.ownsRefreshAdmissionControl(
            operation: operation, target: target,
            activeRunID: RefreshHandler.shared.v3RefreshAdmissionRunID,
            refreshAttemptActive: RefreshHandler.shared.v3RefreshToken != nil,
            anotherHostMutationActive: anotherHostMutationActiveForRefreshControl(
                operation: operation, target: target),
            userConfirmedReconciliation: V3WireContract.strictBool(payload?["userConfirmed"]) == true)
        if mutation {
            guard scopedSessionControl || explicitRecoveryConfirmation || explicitDirectRecoveryControl ||
                    scopedAuthSessionControl || replacesAuthSession || scopedRefreshAdmissionControl ||
                    (!isMutating && RefreshHandler.shared.v3RefreshToken == nil) else {
                if ["authBegin", "authRetryProvisioning"].contains(operation) {
                    let failure = CombinedFailure(operation: "signIn", stage: .command,
                        code: .busy, id: id, retryable: true)
                    throw V3AuthAttemptStartFailurePolicy.confirmedNotDispatched(failure, operation: operation)
                }
                if operation == "sourceRemoveConfirmed" {
                    throw CombinedFailure(operation: "source", stage: .source, code: .busy,
                        id: id, retryable: true, safeCause: .sourceRemoveBusy)
                }
                if operation == "opStart" {
                    throw CombinedFailure(operation: operation, stage: .command, code: .busy,
                        id: id, retryable: true, safeCause: .operationInProgress)
                }
                if ["refreshAdmissionBegin", "refreshAdmissionEnd"].contains(operation) {
                    throw CombinedFailure(operation: "refresh", stage: .command, code: .busy,
                        id: id, retryable: true, safeCause: .operationInProgress)
                }
                throw CombinedFailure(operation: operation, stage: .command, code: .busy,
                                      id: id, retryable: true, safeCause: .operationInProgress)
            }
            if !scopedSessionControl && !explicitRecoveryConfirmation && !explicitDirectRecoveryControl &&
               !scopedAuthSessionControl { activeMutation = id }
        }
        defer { if activeMutation == id { activeMutation = nil } }
        var statusLeaseTicket: V3StatusWriteTicket?
        let directStatusOwner = explicitDirectRecoveryControl ? "recovery-control:\(target)" :
            V3StatusAuthorityOperationPolicy.directWriteOwnerID(operation: operation, requestID: id)
        let longStatusOwner = V3StatusAuthorityOperationPolicy.longOwnerID(
            operation: operation, sessionID: operationSessionID)
        let statusOwnerID: String?
        let statusLeaseKind: V3StatusAuthorityLeaseKind?
        if operation == "snapshot" {
            statusOwnerID = "snapshot:\(id)"
            statusLeaseKind = .snapshot
        } else if let directStatusOwner {
            statusOwnerID = directStatusOwner
            statusLeaseKind = .mutation
        } else if let longStatusOwner {
            statusOwnerID = longStatusOwner
            statusLeaseKind = .mutation
        } else {
            statusOwnerID = nil
            statusLeaseKind = nil
        }
        if let statusOwnerID, let statusLeaseKind {
            statusLeaseTicket = try await acquireStatusLease(ownerID: statusOwnerID, kind: statusLeaseKind,
                allowUnresolvedMutation: explicitDirectRecoveryControl)
            statusLeaseByRequestID[id] = statusLeaseTicket
        }
        defer {
            let wasDispatched = statusDispatchedRequestIDs.remove(id) != nil
            if !wasDispatched, let statusLeaseTicket {
                completeStatusLease(statusLeaseTicket, outcome: .notDispatched)
            }
        }
        do {
            try await connect()
            if let connectedTicket = observeConnectedStatusService(ownerID: statusOwnerID ??
                V3StatusAuthorityOperationPolicy.controlOwnerID(
                    operation: operation, sessionID: operationSessionID)) {
                if statusLeaseTicket?.ownerID == connectedTicket.ownerID {
                    statusLeaseTicket = connectedTicket
                    statusLeaseByRequestID[id] = connectedTicket
                }
            }
        } catch {
            if ["authBegin", "authRetryProvisioning"].contains(operation) {
                NotificationCenter.default.post(name: Notification.Name("V3AuthIdentityTransitionFinished"), object: nil)
            }
            if error is CancellationError { throw CancellationError() }
            monitorOperationSessionIfNeeded(operation: operation, sessionID: operationSessionID)
            let annotated = V3CatalogRequestContext.annotating(error, requestedOperation: operation, requestID: id)
            if ["authBegin", "authRetryProvisioning"].contains(operation) {
                let failure = (annotated as? CombinedFailure) ?? CombinedFailure.capture(
                    annotated, operation: "signIn", stage: .xpcConnection, id: id)
                throw V3AuthAttemptStartFailurePolicy.confirmedNotDispatched(failure, operation: operation)
            }
            throw annotated
        }
        let isBoundedSessionCreation = ["authBegin", "authRetryProvisioning",
            "refreshAdmissionBegin", "refreshAdmissionEnd"].contains(operation)
        let configuredTimeout = (V3WireContract.readOperations.contains(operation) || operation == "opCancel" ||
            isBoundedSessionCreation) ? readTimeout : commandTimeout
        let timeout = requestDeadline.map { min(configuredTimeout, max(0, $0.timeIntervalSinceNow)) }
            ?? configuredTimeout
        guard timeout > 0 else {
            throw CombinedFailure(operation: operation, stage: V3CatalogRequestContext.hostStage(for: operation),
                                  code: .timedOut, id: id, retryable: true)
        }
        var message: [String: Any] = ["version": 1, "id": id, "operation": operation,
                                      "target": target, "deadline": Date().addingTimeInterval(timeout)]
        if let cursor { message["cursor"] = cursor }
        var requestPayload = payload ?? [:]
        if ["authBegin", "authRetryProvisioning"].contains(operation) {
            if requestPayload["sessionDeadline"] as? Date == nil {
                requestPayload["sessionDeadline"] = Date().addingTimeInterval(V3WireContract.authSessionLifetime)
            }
        }
        if operation == "opCancel" {
            requestPayload["knownStarted"] = knownOperationSessions[target] != nil
        }
        if !requestPayload.isEmpty { message["payload"] = requestPayload }
        guard let data = V3WireContract.encodeRequest(message), data.count <= 16384 else {
            throw CombinedFailure(operation: operation, stage: .command, code: .invalidConfiguration, id: id)
        }
        let response: Data
        do {
            response = try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                if Task.isCancelled { continuation.resume(throwing: CancellationError()); return }
                pending[id] = continuation
                pendingOperations[id] = operation
                guard let client = RefreshHandler.shared.client else {
                    let failure = CombinedFailure(operation: operation, stage: .xpcConnection,
                        code: .interrupted, id: id, retryable: V3WireContract.readOperations.contains(operation))
                    let terminalFailure = ["authBegin", "authRetryProvisioning"].contains(operation)
                        ? V3AuthAttemptStartFailurePolicy.confirmedNotDispatched(failure, operation: operation)
                        : failure
                    settle(id, .failure(terminalFailure))
                    return
                }
                // Track ownership only once a valid request is about to cross
                // XPC. Local encoding, size, or pre-dispatch cancellation
                // failures must not leave a synthetic active session behind.
                if operation == "opStart", let session = operationSessionID {
                    activeOperationSessions.insert(session)
                    knownOperationSessions[session] = Date()
                    pruneKnownOperationSessions()
                }
                if ["authBegin", "authRetryProvisioning"].contains(operation),
                   let session = operationSessionID,
                   let sessionDeadline = requestPayload["sessionDeadline"] as? Date {
                    authSessionOwnership.register(sessionID: session, deadline: sessionDeadline)
                }
                statusDispatchedRequestIDs.insert(id)
                client.v3Execute(data) { response in
                    Task { @MainActor in
                        self.observeStatusLeaseResponse(requestID: id, operation: operation,
                            target: target, payload: requestPayload, data: response)
                        if V3CancellationRecoveryReplyPolicy.mayCancelRetirement(
                            operation: operation, requestStillPending: self.pending[id] != nil) {
                            self.cancellationRecovery.removeValue(forKey: id)?.cancel()
                        }
                        guard response.count <= V3WireContract.responseLimit else {
                            // V3_RESPONSE_ENCODING_CLASSIFICATION_V1: a reply that
                            // arrived but exceeded the transport limit is its own
                            // defect. It was reported as a plain invalidResponse,
                            // which is the same shape as a reply that could not be
                            // parsed, so the two were indistinguishable. The stage
                            // follows the request so a catalog read is not reported
                            // as a generic command failure.
                            self.settle(id, .failure(CombinedFailure(operation: operation,
                                stage: V3CatalogRequestContext.replyEncodingStage(for: operation),
                                code: .invalidResponse, id: id, safeCause: .responseTooLarge))); return
                        }
                        self.settle(id, .success(response))
                    }
                }
                timeouts[id] = Task { @MainActor in
                    do { try await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000)) } catch { return }
                    if self.pending[id] != nil {
                        let retireIfStuck = V3RequestRetirementPolicy
                            .shouldRetireServiceIfRequestStaysPending(operation)
                        self.monitorOperationSessionIfNeeded(operation: operation, sessionID: operationSessionID)
                        let (cancelTarget, cancelScope) = self.remoteCancellation(operation: operation,
                            operationSessionID: operationSessionID, requestID: id)
                        self.cancelRemote(cancelTarget, requestID: id, scope: cancelScope,
                                          mutation: mutation, retireIfStuck: retireIfStuck)
                        // V3_CATALOG_FAILURE_STAGE_V1: a read timeout is reported
                        // against the request's own operation and stage, so a
                        // catalog read never collapses into a generic command
                        // failure. A read is always safe to retry.
                        self.settle(id, .failure(CombinedFailure(operation: operation,
                            stage: V3CatalogRequestContext.hostStage(for: operation), code: .timedOut, id: id,
                            retryable: mutation ? nil : true)))
                        if !mutation && V3IdleReadRetirementPolicy.shouldRetireService(
                            operation: operation, hostMutationActive: self.isMutating,
                            refreshAttemptActive: RefreshHandler.shared.v3RefreshToken != nil) {
                            // An idle service that cannot answer a read needs a fresh process.
                            // Never retire it for a read while signing/install/refresh is active.
                            RefreshHandler.shared.v3_stopService()
                            self.disconnected()
                        }
                    }
                }
            }
            }, onCancel: {
            Task { @MainActor in
                guard self.pending[id] != nil else { return }
                let retireIfStuck = V3RequestRetirementPolicy
                    .shouldRetireServiceIfRequestStaysPending(operation)
                self.monitorOperationSessionIfNeeded(operation: operation, sessionID: operationSessionID)
                let (cancelTarget, cancelScope) = self.remoteCancellation(operation: operation,
                    operationSessionID: operationSessionID, requestID: id)
                self.cancelRemote(cancelTarget, requestID: id, scope: cancelScope,
                                  mutation: mutation, retireIfStuck: retireIfStuck)
                self.settle(id, .failure(CancellationError()))
            }
            })
        } catch {
            monitorOperationSessionIfNeeded(operation: operation, sessionID: operationSessionID)
            throw error
        }
        // V3_RESPONSE_CLASSIFICATION_CARRIER_V1: the reply classification is a
        // pure function so the exact production path can be executed against a
        // real service fallback envelope, rather than only asserted in source
        // text. Precedence is unchanged: the structured envelope is authoritative
        // and the legacy token is only consulted when there is no decodable one.
        let result: [String: Any]
        do {
            result = try V3CatalogRequestContext.classifyReply(response, operation: operation, id: id)
        } catch {
            let authStartNotDispatched = ["authBegin", "authRetryProvisioning"].contains(operation) &&
                V3NotDispatchedReplyPolicy.confirms(response, requestID: id,
                    maximumBytes: V3WireContract.responseLimit)
            if operation == "opStart", serviceRejectedOperationStart(response, requestID: id),
               let sessionID = operationSessionID {
                activeOperationSessions.remove(sessionID)
                uncertainOperationSessions.remove(sessionID)
                knownOperationSessions.removeValue(forKey: sessionID)
                operationMonitors.removeValue(forKey: sessionID)?.cancel()
            } else if ["authBegin", "authRetryProvisioning"].contains(operation),
                      let sessionID = operationSessionID,
                      V3NotDispatchedReplyPolicy.confirms(response, requestID: id,
                          maximumBytes: V3WireContract.responseLimit) {
                authSessionOwnership.clear(sessionID: sessionID)
            } else {
                monitorOperationSessionIfNeeded(operation: operation, sessionID: operationSessionID)
            }
            if authStartNotDispatched {
                let failure = (error as? CombinedFailure) ?? CombinedFailure.capture(
                    error, operation: "signIn", stage: .authentication, id: id)
                throw V3AuthAttemptStartFailurePolicy.confirmedNotDispatched(failure, operation: operation)
            }
            throw error
        }
        let requestedStartSession = operation == "opStart" ? payload?["session"] as? String : nil
        guard V3OperationSessionCorrelationPolicy.matches(operation: operation, target: target,
            requestedStartSession: requestedStartSession, resultSession: result["session"] as? String) else {
            monitorOperationSessionIfNeeded(operation: operation, sessionID: operationSessionID)
            throw CombinedFailure(operation: operation, stage: .command, code: .staleResult,
                                  id: id, retryable: false)
        }
        updateOperationSessionOwnership(operation: operation, target: target,
                                        payload: payload, result: result)
        updateAuthSessionOwnership(operation: operation, sessionID: operationSessionID, result: result)
        if ["accountImport"].contains(operation) ||
           (["authBegin", "authRetryProvisioning", "authPoll"].contains(operation) &&
            (result["state"] as? String).map { !["working", "awaitingPrompt"].contains($0) } == true) {
            NotificationCenter.default.post(name: Notification.Name("V3AuthIdentityTransitionFinished"), object: nil)
        }
        return attachStatusReplyTicket(result, requestID: id)
    }

    public func disconnected() {
        if let retired = statusWriteAuthority.retireService() {
            if retired.kind == .snapshot {
                for requestID in Array(statusLeaseByRequestID.keys) where
                    statusLeaseByRequestID[requestID]?.ownerID == retired.ownerID {
                    statusLeaseByRequestID.removeValue(forKey: requestID)
                }
            }
            for requestID in Array(statusReplyTicketByRequestID.keys) where
                statusReplyTicketByRequestID[requestID]?.ownerID == retired.ownerID {
                statusReplyTicketByRequestID.removeValue(forKey: requestID)
            }
        }
        drainStatusLeaseWaiters()
        for task in cancellationRecovery.values { task.cancel() }
        cancellationRecovery.removeAll()
        // Every caller first requests SideStore service retirement. Auth state
        // cannot outlive that process; clear host-only owners from lost starts.
        authSessionOwnership.clearAll()
        for task in operationMonitors.values { task.cancel() }
        operationMonitors.removeAll()
        // XPC loss does not prove that native InstallationProxy/device work
        // stopped. Preserve the mutation gate and require an authoritative
        // terminal reply or the explicit device-check reconciliation action.
        uncertainOperationSessions.formUnion(activeOperationSessions)
        for id in Array(pending.keys) {
            let requestedOperation = pendingOperations[id] ?? "command"
            let failure = CombinedFailure(operation: requestedOperation, stage: .xpcConnection,
                code: .interrupted, id: id)
            let contextualFailure = V3CatalogRequestContext.annotating(failure,
                requestedOperation: requestedOperation, requestID: id)
            settle(id, .failure(contextualFailure))
        }
    }

    private func cancelRemote(_ target: String, requestID: String, scope: String = "request",
                              mutation: Bool = false, retireIfStuck: Bool = true) {
        let cancellationID = UUID().uuidString
        let value: [String: Any] = ["version": 1, "id": cancellationID, "operation": "cancel",
                                    "target": target, "payload": ["scope": scope],
                                    "deadline": Date().addingTimeInterval(30)]
        if let data = try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0) {
            RefreshHandler.shared.client?.v3Execute(data) { response in
                Task { @MainActor in
                    guard V3RefreshAdmissionCancellationAckPolicy.accepts(response,
                        cancellationID: cancellationID) else { return }
                    // ACK confirms only the cancel request. The original
                    // request's correlated callback or explicit retirement
                    // owns status-lease completion and timer cancellation.
                }
            }
        }
        if mutation && retireIfStuck {
            // Keep the host mutation gate held until completion or process retirement.
            // A native callback that never returns cannot strand the product forever.
            // The recovery key is the request ID, while the remote cancellation
            // target may be an operation/auth session ID.
            cancellationRecovery[requestID] = Task { @MainActor in
                do { try await Task.sleep(nanoseconds: UInt64(cancellationGrace * 1_000_000_000)) } catch { return }
                guard cancellationRecovery[requestID] != nil else { return }
                RefreshHandler.shared.v3_stopService()
                disconnected()
            }
        }
    }

    private func settle(_ id: String, _ result: Result<Data, Error>) {
        timeouts.removeValue(forKey: id)?.cancel()
        pendingOperations.removeValue(forKey: id)
        pending.removeValue(forKey: id)?.resume(with: result)
    }

    private func updateOperationSessionOwnership(operation: String, target: String,
                                                 payload: [String: Any]?,
                                                 result: [String: Any]) {
        let sessionID = operation == "opStart" ? payload?["session"] as? String : target
        guard let sessionID,
              ["opStart", "opPoll", "opAnswer", "opCancel"].contains(operation) else { return }
        guard let state = result["state"] as? String,
              ["completed", "failed", "cancelled", "requiresSource", "waitingForAuthentication"].contains(state) else { return }
        let rawOutcomeUnknown = result["outcomeUnknown"]
        let parsedOutcomeUnknown = V3WireContract.strictBool(rawOutcomeUnknown)
        let outcomeUnknown = parsedOutcomeUnknown ?? (rawOutcomeUnknown != nil)
        let backendSettled = !outcomeUnknown &&
            V3WireContract.strictBool(result["backendSettled"]) == true
        if backendSettled {
            activeOperationSessions.remove(sessionID)
            uncertainOperationSessions.remove(sessionID)
            operationMonitors.removeValue(forKey: sessionID)?.cancel()
        } else {
            uncertainOperationSessions.insert(sessionID)
            monitorOperationSessionUntilSettled(sessionID)
        }
    }

    private func updateAuthSessionOwnership(operation: String, sessionID: String?,
                                            result: [String: Any]) {
        guard ["authBegin", "authRetryProvisioning", "authPoll", "authRespond", "authCancel"].contains(operation),
              let sessionID else { return }
        authSessionOwnership.observe(operation: operation, sessionID: sessionID,
                                     replySessionID: result["session"] as? String,
                                     state: result["state"] as? String)
    }

    private func monitorOperationSessionIfNeeded(operation: String, sessionID: String?) {
        guard ["opStart", "opPoll", "opAnswer", "opCancel"].contains(operation),
              let sessionID, activeOperationSessions.contains(sessionID) else { return }
        uncertainOperationSessions.insert(sessionID)
        monitorOperationSessionUntilSettled(sessionID)
    }

    private func remoteCancellation(operation: String, operationSessionID: String?,
                                    requestID: String) -> (String, String) {
        if operation == "opStart", let operationSessionID { return (operationSessionID, "operation") }
        if operation == "opCancel", let operationSessionID { return (operationSessionID, "operation") }
        if ["authBegin", "authRetryProvisioning"].contains(operation), let operationSessionID,
           !operationSessionID.isEmpty {
            return (operationSessionID, "auth")
        }
        if operation == "authCancel", let operationSessionID { return (operationSessionID, "auth") }
        return (requestID, "request")
    }

    private func serviceRejectedOperationStart(_ data: Data, requestID: String) -> Bool {
        V3NotDispatchedReplyPolicy.confirms(data, requestID: requestID,
            maximumBytes: V3WireContract.responseLimit)
    }

    private func pruneKnownOperationSessions() {
        guard knownOperationSessions.count > 256 else { return }
        let settled = knownOperationSessions.filter {
            !activeOperationSessions.contains($0.key) && !uncertainOperationSessions.contains($0.key)
        }.sorted { $0.value < $1.value }
        for (id, _) in settled.prefix(max(0, knownOperationSessions.count - 256)) {
            knownOperationSessions.removeValue(forKey: id)
        }
    }

    private func monitorOperationSessionUntilSettled(_ sessionID: String) {
        guard operationMonitors[sessionID] == nil else { return }
        operationMonitors[sessionID] = Task { @MainActor in
            let backoff: [UInt64] = [1, 2, 5, 10, 15]
            var index = 0
            while !Task.isCancelled && activeOperationSessions.contains(sessionID) {
                let seconds = backoff[min(index, backoff.count - 1)]
                index += 1
                do { try await Task.sleep(nanoseconds: seconds * 1_000_000_000) }
                catch { break }
                guard activeOperationSessions.contains(sessionID) else { break }
                do {
                    _ = try await request(operation: "opPoll", target: sessionID)
                } catch let failure as CombinedFailure where failure.code == .invalidConfiguration {
                    // A replacement service cannot find the old in-memory
                    // session. Stop polling but retain ownership because service
                    // loss does not prove that the device mutation stopped.
                    uncertainOperationSessions.insert(sessionID)
                    break
                } catch { }
            }
            operationMonitors[sessionID] = nil
        }
    }
}
