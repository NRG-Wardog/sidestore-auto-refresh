
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
            return CombinedFailure(operation: operation, stage: stage, code: .invalidResponse, id: id,
                                  safeCause: .responseTooLarge)
        case "responseEncodingFailed":
            // V3_RESPONSE_ENCODING_CLASSIFICATION_V1: the service could not
            // serialize its reply at all. This is a distinct defect from an
            // oversized reply and is never reported as one.
            return CombinedFailure(operation: operation, stage: stage, code: .invalidResponse, id: id,
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
        guard decoded["id"] as? String == id else {
            // Genuine cross-request protocol evidence. It is never resolved to
            // the waiting caller, and it is never reported as a serialization
            // defect it did not prove.
            throw CombinedFailure(operation: operation, stage: .command, code: .staleResult, id: id)
        }
        if let envelope = decoded["failure"] as? [String: Any],
           let failure = CombinedFailure.decode(envelope, expectedID: id) { throw failure }
        if let code = decoded["error"] as? String {
            throw hostFailure(errorToken: code, operation: operation, id: id)
        }
        guard decoded["version"] as? Int == 1, decoded["ok"] as? Bool == true,
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
    private var pending: [String: CheckedContinuation<Data, Error>] = [:]
    private var pendingOperations: [String: String] = [:]
    private var timeouts: [String: Task<Void, Never>] = [:]
    private var cancellationRecovery: [String: Task<Void, Never>] = [:]
    private let readTimeout: TimeInterval
    private let commandTimeout: TimeInterval
    private let cancellationGrace: TimeInterval
    private var activeMutation: String?
    public var isMutating: Bool { activeMutation != nil || !cancellationRecovery.isEmpty }
    public var processID: Int32 { RefreshHandler.shared.sideStorePid }

    init(readTimeout: TimeInterval = 30, commandTimeout: TimeInterval = 600, cancellationGrace: TimeInterval = 3) {
        self.readTimeout = readTimeout
        self.commandTimeout = commandTimeout
        self.cancellationGrace = cancellationGrace
    }

    public func connect() async throws {
        try await RefreshHandler.shared.ensureServiceConnected()
    }

    public func request(operation: String, target: String = "", cursor: Int? = nil, payload: [String: Any]? = nil) async throws -> [String: Any] {
        try Task.checkCancellation()
        // V3_CATALOG_OPERATION_CONTEXT_V1: the request correlation is minted
        // before connecting, so a failure that happens before the service
        // receives the request can still be attributed to the caller's actual
        // operation instead of only to the connection attempt.
        let id = UUID().uuidString
        let mutation = !V3WireContract.readOperations.contains(operation)
        do {
            try await connect()
        } catch {
            if error is CancellationError { throw CancellationError() }
            throw V3CatalogRequestContext.annotating(error, requestedOperation: operation, requestID: id)
        }
        if mutation {
            guard !isMutating, RefreshHandler.shared.v3RefreshToken == nil else {
                throw CombinedFailure(operation: operation, stage: .command, code: .busy, id: id, retryable: true)
            }
            activeMutation = id
        }
        defer { if activeMutation == id { activeMutation = nil } }
        let timeout = V3WireContract.readOperations.contains(operation) ? readTimeout : commandTimeout
        var message: [String: Any] = ["version": 1, "id": id, "operation": operation,
                                      "target": target, "deadline": Date().addingTimeInterval(timeout)]
        if let cursor { message["cursor"] = cursor }
        if let payload { message["payload"] = payload }
        let data: Data
        do {
            data = try PropertyListSerialization.data(fromPropertyList: message, format: .binary, options: 0)
        } catch {
            throw CombinedFailure(operation: operation, stage: .command, code: .invalidConfiguration, id: id)
        }
        guard data.count <= 16384 else { throw CombinedFailure(operation: operation, stage: .command, code: .invalidConfiguration, id: id) }
        let response: Data = try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                if Task.isCancelled { continuation.resume(throwing: CancellationError()); return }
                pending[id] = continuation
                pendingOperations[id] = operation
                guard let client = RefreshHandler.shared.client else {
                    settle(id, .failure(CombinedFailure(operation: operation, stage: .xpcConnection,
                        code: .interrupted, id: id, retryable: V3WireContract.readOperations.contains(operation))))
                    return
                }
                client.v3Execute(data) { response in
                    Task { @MainActor in
                        self.cancellationRecovery.removeValue(forKey: id)?.cancel()
                        guard response.count <= V3WireContract.responseLimit else {
                            // V3_RESPONSE_ENCODING_CLASSIFICATION_V1: a reply that
                            // arrived but exceeded the transport limit is its own
                            // defect. It was reported as a plain invalidResponse,
                            // which is the same shape as a reply that could not be
                            // parsed, so the two were indistinguishable. The stage
                            // follows the request so a catalog read is not reported
                            // as a generic command failure.
                            self.settle(id, .failure(CombinedFailure(operation: operation,
                                stage: V3CatalogRequestContext.hostStage(for: operation),
                                code: .invalidResponse, id: id, safeCause: .responseTooLarge))); return
                        }
                        self.settle(id, .success(response))
                    }
                }
                timeouts[id] = Task { @MainActor in
                    do { try await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000)) } catch { return }
                    if self.pending[id] != nil {
                        self.cancelRemote(id, mutation: mutation)
                        // V3_CATALOG_FAILURE_STAGE_V1: a read timeout is reported
                        // against the request's own operation and stage, so a
                        // catalog read never collapses into a generic command
                        // failure. A read is always safe to retry.
                        self.settle(id, .failure(CombinedFailure(operation: operation,
                            stage: V3CatalogRequestContext.hostStage(for: operation), code: .timedOut, id: id,
                            retryable: mutation ? nil : true)))
                        if !mutation, !self.isMutating, RefreshHandler.shared.v3RefreshToken == nil {
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
                self.cancelRemote(id, mutation: mutation)
                self.settle(id, .failure(CancellationError()))
            }
        })
        // V3_RESPONSE_CLASSIFICATION_CARRIER_V1: the reply classification is a
        // pure function so the exact production path can be executed against a
        // real service fallback envelope, rather than only asserted in source
        // text. Precedence is unchanged: the structured envelope is authoritative
        // and the legacy token is only consulted when there is no decodable one.
        return try V3CatalogRequestContext.classifyReply(response, operation: operation, id: id)
    }

    public func disconnected() {
        for task in cancellationRecovery.values { task.cancel() }
        cancellationRecovery.removeAll()
        for id in Array(pending.keys) {
            settle(id, .failure(CombinedFailure(operation: pendingOperations[id] ?? "command", stage: .xpcConnection, code: .interrupted, id: id)))
        }
    }

    private func cancelRemote(_ target: String, mutation: Bool = false) {
        let value: [String: Any] = ["version": 1, "id": UUID().uuidString, "operation": "cancel",
                                    "target": target, "deadline": Date().addingTimeInterval(30)]
        if let data = try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0) {
            RefreshHandler.shared.client?.v3Execute(data) { _ in }
        }
        if mutation {
            // Keep the host mutation gate held until completion or process retirement.
            // A native callback that never returns cannot strand the product forever.
            cancellationRecovery[target] = Task { @MainActor in
                do { try await Task.sleep(nanoseconds: UInt64(cancellationGrace * 1_000_000_000)) } catch { return }
                guard cancellationRecovery[target] != nil else { return }
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
}
