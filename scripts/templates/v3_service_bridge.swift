
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
    public var isMutating: Bool {
        authSessionOwnership.hasActiveSession() || activeMutation != nil ||
            !activeOperationSessions.isEmpty || !cancellationRecovery.isEmpty
    }
    public func hasUncertainOperationSession(_ sessionID: String) -> Bool {
        uncertainOperationSessions.contains(sessionID)
    }
    /// Clear only a host owner for a session SideStore explicitly reports as
    /// unavailable. Transport loss and malformed replies retain ownership.
    public func confirmAuthSessionUnavailable(sessionID: String) {
        authSessionOwnership.clear(sessionID: sessionID)
    }
    /// A validated service snapshot can retire a host owner when it proves
    /// there is no active authentication task, even if the terminal poll was lost.
    public func reconcileAuthSessionOwnership(sessionID: String, authenticationActive: Bool) {
        authSessionOwnership.reconcile(sessionID: sessionID, authenticationActive: authenticationActive)
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
        let operationSessionID: String? = {
            if operation == "opStart" { return payload?["session"] as? String }
            if ["opPoll", "opAnswer", "opCancel"].contains(operation) { return target }
            if ["authBegin", "authRetryProvisioning"].contains(operation) {
                return payload?["session"] as? String ?? (target.isEmpty ? nil : target)
            }
            if ["authPoll", "authRespond", "authCancel"].contains(operation) { return target }
            return nil
        }()
        let scopedSessionControl = ["opAnswer", "opCancel"].contains(operation) &&
            activeOperationSessions.contains(target)
        let explicitRecoveryConfirmation = operation == "opRecoveryReconcile" &&
            V3WireContract.strictBool(payload?["userConfirmed"]) == true &&
            UUID(uuidString: target)?.uuidString == target
        let scopedAuthSessionControl = ["authRespond", "authCancel"].contains(operation) &&
            authSessionOwnership.owns(target)
        let replacesAuthSession = ["authBegin", "authRetryProvisioning"].contains(operation) &&
            authSessionOwnership.hasActiveSession()
        let mutation = !V3WireContract.readOperations.contains(operation) ||
            ["opAnswer", "opCancel", "authCancel"].contains(operation)
        do {
            try await connect()
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
        let scopedRefreshAdmissionControl = V3ServiceMutationAdmissionPolicy.ownsRefreshAdmissionControl(
            operation: operation, target: target,
            activeRunID: RefreshHandler.shared.v3RefreshAdmissionRunID,
            refreshAttemptActive: RefreshHandler.shared.v3RefreshToken != nil,
            anotherHostMutationActive: isMutating,
            userConfirmedReconciliation: V3WireContract.strictBool(payload?["userConfirmed"]) == true)
        if mutation {
            guard scopedSessionControl || explicitRecoveryConfirmation || scopedAuthSessionControl || replacesAuthSession || scopedRefreshAdmissionControl ||
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
            if !scopedSessionControl && !explicitRecoveryConfirmation && !scopedAuthSessionControl { activeMutation = id }
        }
        defer { if activeMutation == id { activeMutation = nil } }
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
                client.v3Execute(data) { response in
                    Task { @MainActor in
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
        return result
    }

    public func disconnected() {
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
            settle(id, .failure(CombinedFailure(operation: pendingOperations[id] ?? "command", stage: .xpcConnection, code: .interrupted, id: id)))
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
                    self.cancellationRecovery.removeValue(forKey: requestID)?.cancel()
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
