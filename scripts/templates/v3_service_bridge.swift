
// V3_HOST_COMMAND_BRIDGE_V1
@MainActor
public final class V3ServiceBridge {
    public static let shared = V3ServiceBridge()
    private var pending: [String: CheckedContinuation<Data, Error>] = [:]
    private var connecting: Task<Void, Error>?
    public var processID: Int32 { RefreshHandler.shared.sideStorePid }

    public func connect() async throws {
        if let connecting { return try await connecting.value }
        let task = Task { @MainActor in
            try await RefreshHandler.shared.startRefresh(identifier: "__v3_connect", mangledName: "")
        }
        connecting = task
        defer { connecting = nil }
        try await task.value
    }

    public func request(operation: String, target: String = "", value: Bool? = nil) async throws -> [String: Any] {
        try Task.checkCancellation()
        try await connect()
        let id = UUID().uuidString
        let timeout: TimeInterval = ["snapshot", "catalog"].contains(operation) ? 30 : 600
        var message: [String: Any] = ["version": 1, "id": id, "operation": operation,
                                      "target": target, "deadline": Date().addingTimeInterval(timeout)]
        if let value { message["value"] = value }
        let data = try PropertyListSerialization.data(fromPropertyList: message, format: .binary, options: 0)
        guard data.count <= 16384 else { throw failure("Request is too large.") }
        let response: Data = try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                if Task.isCancelled { continuation.resume(throwing: CancellationError()); return }
                pending[id] = continuation
                guard let client = RefreshHandler.shared.client else {
                    settle(id, .failure(failure("SideStore disconnected. Reconnect and try again.")))
                    return
                }
                client.v3Execute(data) { response in
                    Task { @MainActor in
                        guard response.count <= 4_194_304 else {
                            self.settle(id, .failure(self.failure("SideStore response exceeded the size limit."))); return
                        }
                        self.settle(id, .success(response))
                    }
                }
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                    if self.pending[id] != nil {
                        self.cancelRemote(id)
                        self.settle(id, .failure(self.failure("SideStore timed out. The operation may have completed; reload its status before retrying.")))
                    }
                }
            }
        }, onCancel: {
            Task { @MainActor in
                self.cancelRemote(id)
                self.settle(id, .failure(CancellationError()))
            }
        })
        guard let decoded = try PropertyListSerialization.propertyList(from: response, format: nil) as? [String: Any],
              decoded["id"] as? String == id else { throw failure("SideStore returned an invalid or stale response.") }
        if let code = decoded["error"] as? String {
            let messages = ["notReady": "SideStore is starting. Please retry shortly.",
                            "busy": "Another SideStore operation is running.",
                            "cancelled": "Operation cancelled. Reload status before retrying.",
                            "notFound": "This item no longer exists. Reload the library.",
                            "unsupported": "This action is not supported for this item.",
                            "operationFailed": "SideStore could not complete this operation. Check account, pairing and LocalDevVPN, then try again."]
            throw NSError(domain: "V3SideStoreService." + code, code: 1,
                          userInfo: [NSLocalizedDescriptionKey: messages[code] ?? "SideStore rejected the request."])
        }
        guard decoded["version"] as? Int == 1, decoded["ok"] as? Bool == true,
              let result = decoded["result"] as? [String: Any] else { throw failure("Invalid SideStore service response.") }
        return result
    }

    public func disconnected() {
        for id in Array(pending.keys) {
            settle(id, .failure(failure("SideStore stopped. Reconnect and reload status before retrying an operation.")))
        }
    }

    private func cancelRemote(_ target: String) {
        let value: [String: Any] = ["version": 1, "id": UUID().uuidString, "operation": "cancel",
                                    "target": target, "deadline": Date().addingTimeInterval(30)]
        if let data = try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0) {
            RefreshHandler.shared.client?.v3Execute(data) { _ in }
        }
    }

    private func settle(_ id: String, _ result: Result<Data, Error>) {
        pending.removeValue(forKey: id)?.resume(with: result)
    }
    private func failure(_ message: String) -> NSError {
        NSError(domain: "V3SideStoreService", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }
}
