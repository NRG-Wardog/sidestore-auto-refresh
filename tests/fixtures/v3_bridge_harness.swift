import Foundation

@MainActor
final class FakeClient {
    var hold = false
    var stale = false
    var oversized = false
    var replies: [() -> Void] = []
    var cancellations = 0
    var operations: [String] = []
    func v3Execute(_ data: Data, reply: @escaping (Data) -> Void) {
        let request = try! PropertyListSerialization.propertyList(from: data, format: nil) as! [String: Any]
        operations.append(request["operation"] as! String)
        if request["operation"] as? String == "cancel" { cancellations += 1; reply(Data()); return }
        let result: [String: Any] = ["version": 1, "id": stale ? UUID().uuidString : request["id"]!,
                                     "ok": true, "result": ["account": "fixture"]]
        let encoded = oversized ? Data(repeating: 0, count: 4_194_305) :
            try! PropertyListSerialization.data(fromPropertyList: result, format: .binary, options: 0)
        if hold { replies.append { reply(encoded) } } else { reply(encoded) }
    }
    func flush() { let old = replies; replies = []; old.forEach { $0() } }
}

@MainActor
final class RefreshHandler {
    static let shared = RefreshHandler()
    var sideStorePid: Int32 = 123
    var v3RefreshToken: UUID?
    var client: FakeClient? = FakeClient()
    var connects = 0
    var stops = 0
    func v3_stopService() { stops += 1 }
    lazy var connection: CombinedServiceConnection = CombinedServiceConnection(dependencies: .init(
        resolveHost: { URL(fileURLWithPath: "/fixture") },
        prepareStorage: { $0.appendingPathComponent("Documents/SideStore") },
        createBookmark: { _ in Data([1]) },
        discoverExtension: {},
        launch: { [unowned self] id, _ in
            self.connects += 1
            Task { @MainActor in
                self.connection.signal(.launched, attempt: id)
                self.connection.signal(.connected, attempt: id)
                self.connection.signal(.ready, attempt: id)
            }
        }, retire: { _ in }))
    func ensureServiceConnected() async throws { try await connection.ensureConnected() }
}

@main
struct BridgeTests {
    @MainActor
    static func waitForRequest(_ client: FakeClient) async {
        let deadline = Date().addingTimeInterval(2)
        while client.replies.isEmpty {
            precondition(Date() < deadline, "request was never sent")
            await Task.yield()
        }
    }
    @MainActor
    static func main() async throws {
        let bridge = V3ServiceBridge(readTimeout: 0.25, commandTimeout: 1)
        let handler = RefreshHandler.shared
        let client = handler.client!
        async let a: Void = bridge.connect()
        async let b: Void = bridge.connect()
        _ = try await (a, b)
        precondition(handler.connects == 1, "launch must be coalesced")
        let value = try await bridge.request(operation: "snapshot")
        precondition(value["account"] as? String == "fixture")
        precondition(client.operations == ["snapshot"], "cold launch/status triggered a mutation")
        _ = try await bridge.request(operation: "signIn")
        _ = try await bridge.request(operation: "refreshApp", target: "fixture-app")
        precondition(client.operations == ["snapshot", "signIn", "refreshApp"], "explicit account/refresh integration order changed")
        client.stale = true
        do { _ = try await bridge.request(operation: "snapshot"); preconditionFailure("stale reply accepted") } catch {}
        client.stale = false; client.oversized = true
        do { _ = try await bridge.request(operation: "snapshot"); preconditionFailure("oversized reply accepted") } catch {}
        client.oversized = false; client.hold = true
        let cancelled = Task { try await bridge.request(operation: "install") }
        await waitForRequest(client)
        cancelled.cancel()
        do { _ = try await cancelled.value; preconditionFailure("cancel ignored") } catch is CancellationError {} catch { preconditionFailure("wrong cancellation") }
        precondition(client.cancellations == 1)
        client.flush() // Late success cannot resume an already completed continuation.
        let interrupted = Task { try await bridge.request(operation: "snapshot") }
        await waitForRequest(client)
        bridge.disconnected()
        do { _ = try await interrupted.value; preconditionFailure("disconnect ignored") }
        catch let error as CombinedFailure {
            precondition(error.operation == "status" && error.stage == .xpcConnection && error.code == .interrupted)
        }
        client.flush()
        do { _ = try await bridge.request(operation: "snapshot"); preconditionFailure("timeout ignored") } catch {}
        precondition(client.cancellations == 2, "expected cancellation plus timeout, received \(client.cancellations)")
        precondition(handler.stops == 1, "idle read timeout must reconnect the service")
        client.flush()
        let mutation = Task { try await bridge.request(operation: "install") }
        await waitForRequest(client)
        do { _ = try await bridge.request(operation: "signOut"); preconditionFailure("concurrent mutation accepted") } catch {}
        mutation.cancel()
        _ = try? await mutation.value
        client.flush()
        client.hold = false
        _ = try await bridge.request(operation: "snapshot")
        // Every boundary must retain the operation, including concurrent reads and a mutation.
        client.hold = true
        let installDisconnect = Task { try await bridge.request(operation: "install") }
        await waitForRequest(client)
        let catalogDisconnect = Task { try await bridge.request(operation: "catalog") }
        while client.replies.count < 2 { await Task.yield() }
        bridge.disconnected()
        for (task, operation) in [(installDisconnect, "install"), (catalogDisconnect, "catalog")] {
            do { _ = try await task.value; preconditionFailure("disconnect ignored") }
            catch let error as CombinedFailure {
                precondition(error.operation == operation && error.stage == .xpcConnection)
            }
        }
        client.flush()
        let recovery = V3ServiceBridge(readTimeout: 1, commandTimeout: 1, cancellationGrace: 0.02)
        let stopsBeforeRecovery = handler.stops
        client.hold = true
        let stuck = Task { try await recovery.request(operation: "signIn") }
        await waitForRequest(client)
        stuck.cancel()
        _ = try? await stuck.value
        precondition(recovery.isMutating, "cancel must retain the gate while native work unwinds")
        let deadline = Date().addingTimeInterval(2)
        while handler.stops == stopsBeforeRecovery {
            precondition(Date() < deadline, "stuck native operation was not retired")
            await Task.yield()
        }
        precondition(!recovery.isMutating)
        client.flush()
        print("V3 lifecycle PASS")
    }
}
