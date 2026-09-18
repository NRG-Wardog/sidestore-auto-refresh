
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

    public func request(operation: String, target: String = "", value: Bool? = nil, cursor: Int? = nil, payload: [String: Any]? = nil) async throws -> [String: Any] {
        try Task.checkCancellation()
        try await connect()
        let id = UUID().uuidString
        let readOperations: Set<String> = ["snapshot", "catalog", "appIcon", "backupResult", "certificatesSnapshot", "signInState", "operationState", "settingsPanelSnapshot", "developerServicesSnapshot"]
        let interactiveControlOperations: Set<String> = ["signInRespond", "cancelSignIn", "operationRespond", "cancelOperation"]
        let mutation = !readOperations.contains(operation) && !interactiveControlOperations.contains(operation)
        if mutation {
            guard !isMutating, RefreshHandler.shared.v3RefreshToken == nil else {
                throw CombinedFailure(operation: operation, stage: .command, code: .busy, id: id, retryable: true)
            }
            activeMutation = id
        }
        defer { if activeMutation == id { activeMutation = nil } }
        let timeout = readOperations.contains(operation) || interactiveControlOperations.contains(operation) || operation == "beginSignIn" ? readTimeout : commandTimeout
        var message: [String: Any] = ["version": 1, "id": id, "operation": operation,
                                      "target": target, "deadline": Date().addingTimeInterval(timeout)]
        if let value { message["value"] = value }
        if let cursor { message["cursor"] = cursor }
        if let payload {
            let payloadData = try PropertyListSerialization.data(fromPropertyList: payload, format: .binary, options: 0)
            guard payloadData.count <= 32_768 else {
                throw CombinedFailure(operation: operation, stage: .command, code: .invalidConfiguration, id: id)
            }
            message["payload"] = payloadData
        }
        let data = try PropertyListSerialization.data(fromPropertyList: message, format: .binary, options: 0)
        guard data.count <= 16384 else { throw CombinedFailure(operation: operation, stage: .command, code: .invalidConfiguration, id: id) }
        let response: Data = try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                if Task.isCancelled { continuation.resume(throwing: CancellationError()); return }
                pending[id] = continuation
                pendingOperations[id] = operation
                guard let client = RefreshHandler.shared.client else {
                    settle(id, .failure(CombinedFailure(operation: operation, stage: .xpcConnection, code: .interrupted, id: id)))
                    return
                }
                client.v3Execute(data) { response in
                    Task { @MainActor in
                        self.cancellationRecovery.removeValue(forKey: id)?.cancel()
                        guard response.count <= 4_194_304 else {
                            self.settle(id, .failure(CombinedFailure(operation: operation, stage: .command, code: .invalidResponse, id: id))); return
                        }
                        self.settle(id, .success(response))
                    }
                }
                timeouts[id] = Task { @MainActor in
                    do { try await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000)) } catch { return }
                    if self.pending[id] != nil {
                        self.cancelRemote(id, mutation: mutation)
                        self.settle(id, .failure(CombinedFailure(operation: operation, stage: .command, code: .timedOut, id: id)))
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
        guard let decoded = try PropertyListSerialization.propertyList(from: response, format: nil) as? [String: Any],
              decoded["id"] as? String == id else { throw CombinedFailure(operation: operation, stage: .command, code: .staleResult, id: id) }
        if let envelope = decoded["failure"] as? [String: Any],
           let failure = CombinedFailure.decode(envelope, expectedID: id) { throw failure }
        if let code = decoded["error"] as? String {
            throw CombinedFailure(operation: operation, stage: code == "notReady" ? .serviceReadiness : .command,
                code: CombinedFailure.Code(rawValue: code) ?? .failed, id: id)
        }
        guard decoded["version"] as? Int == 1, decoded["ok"] as? Bool == true,
              let result = decoded["result"] as? [String: Any] else { throw CombinedFailure(operation: operation, stage: .command, code: .invalidResponse, id: id) }
        return result
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
