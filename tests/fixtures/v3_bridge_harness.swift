import Foundation

@MainActor
final class FakeClient {
    var hold = false
    var stale = false
    var oversized = false
    var backendSettled = true
    var operationState = "working"
    var rejectOperationStart = false
    var replies: [() -> Void] = []
    var cancellations = 0
    var operations: [String] = []
    func v3Execute(_ data: Data, reply: @escaping (Data) -> Void) {
        let request = try! PropertyListSerialization.propertyList(from: data, format: nil) as! [String: Any]
        operations.append(request["operation"] as! String)
        if request["operation"] as? String == "cancel" { cancellations += 1; reply(Data()); return }
        let operation = request["operation"] as! String
        let payload = request["payload"] as? [String: Any] ?? [:]
        let target = request["target"] as? String ?? ""
        let operationResult: [String: Any]
        switch operation {
        case "opStart":
            if rejectOperationStart {
                let failure = CombinedFailure(operation: "install", stage: .command,
                    code: .busy, id: request["id"] as! String, retryable: true)
                let rejected: [String: Any] = ["version": 1, "id": request["id"]!,
                    "error": "busy", "failure": failure.wire,
                    "operationNotDispatched": true]
                let encoded = try! PropertyListSerialization.data(fromPropertyList: rejected,
                    format: .binary, options: 0)
                reply(encoded)
                return
            }
            operationResult = ["session": payload["session"] as? String ?? "", "state": "working"]
        case "opPoll":
            operationResult = ["session": target, "state": operationState,
                               "backendSettled": backendSettled,
                               "outcomeUnknown": !backendSettled]
        case "opCancel":
            operationResult = ["session": target, "state": operationState,
                               "backendSettled": backendSettled,
                               "outcomeUnknown": !backendSettled]
        default:
            operationResult = ["account": "fixture"]
        }
        let result: [String: Any] = ["version": 1, "id": stale ? UUID().uuidString : request["id"]!,
                                     "ok": true, "result": operationResult]
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
        let invalidStartSession = UUID().uuidString
        do {
            _ = try await bridge.request(operation: "opStart",
                payload: ["kind": "install", "session": invalidStartSession, "unplistable": NSNull()])
            preconditionFailure("unplistable opStart payload was accepted")
        } catch {}
        precondition(!bridge.isMutating,
                     "local plist encoding failure must not retain a synthetic operation session")
        let oversizedStartSession = UUID().uuidString
        do {
            _ = try await bridge.request(operation: "opStart",
                payload: ["kind": "install", "session": oversizedStartSession,
                          "extra": String(repeating: "x", count: 20_000)])
            preconditionFailure("oversized opStart request was accepted")
        } catch {}
        precondition(!bridge.isMutating,
                     "local request-size rejection must not retain a synthetic operation session")
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
        client.hold = false

        let operationSession = UUID().uuidString
        _ = try await bridge.request(operation: "opStart",
            payload: ["kind": "install", "session": operationSession])
        precondition(bridge.isMutating, "a returned opStart must retain service mutation ownership")
        client.hold = true
        let poll = Task { try await bridge.request(operation: "opPoll", target: operationSession) }
        await waitForRequest(client)
        do { _ = try await poll.value; preconditionFailure("stalled operation poll did not time out") }
        catch let error as CombinedFailure {
            precondition(error.operation == "command" && error.stage == .command)
        }
        precondition(handler.stops == stopsBeforeRecovery + 1,
                     "an opPoll timeout must not retire SideStore during a live mutation")
        precondition(bridge.isMutating, "poll timeout must preserve active operation ownership")
        client.flush()
        client.operationState = "failed"
        client.backendSettled = false
        client.hold = true
        let cancelRequest = Task { try await bridge.request(operation: "opCancel", target: operationSession) }
        await waitForRequest(client)
        do { _ = try await cancelRequest.value; preconditionFailure("unsettled opCancel did not time out") }
        catch let error as CombinedFailure { precondition(error.code == .timedOut) }
        precondition(bridge.isMutating,
                     "an opCancel timeout must keep mutation ownership until backend settlement")
        precondition(handler.stops == stopsBeforeRecovery + 1,
                     "an opCancel timeout must not retire the active SideStore mutation")
        client.flush()
        client.hold = false
        client.backendSettled = true
        let ownershipDeadline = Date().addingTimeInterval(3)
        while bridge.isMutating {
            precondition(Date() < ownershipDeadline, "the monitor did not observe backend settlement")
            await Task.yield()
        }
        let unresolvedSession = UUID().uuidString
        client.operationState = "working"
        client.backendSettled = false
        _ = try await bridge.request(operation: "opStart",
            payload: ["kind": "delete", "session": unresolvedSession])
        client.operationState = "failed"
        let unresolved = try await bridge.request(operation: "opPoll", target: unresolvedSession)
        precondition(unresolved["outcomeUnknown"] as? Bool == true && bridge.isMutating)
        let stopsBeforeConfirmation = handler.stops
        precondition(bridge.confirmUncertainOperationAfterDeviceCheck(sessionID: unresolvedSession),
                     "user-confirmed device reconciliation must retire the uncertain backend process")
        precondition(handler.stops == stopsBeforeConfirmation + 1 && !bridge.isMutating,
                     "the explicit confirmed-reconciliation path must release the host mutation gate")

        // A service-level rejection before the operation center creates a session
        // is authoritative proof that the mutation was never dispatched.
        client.rejectOperationStart = true
        let rejectedSession = UUID().uuidString
        do {
            _ = try await bridge.request(operation: "opStart",
                payload: ["kind": "install", "session": rejectedSession])
            preconditionFailure("the fake service start rejection was accepted")
        } catch {}
        precondition(!bridge.isMutating && !bridge.hasUncertainOperationSession(rejectedSession),
            "a structured pre-dispatch rejection must release session ownership")
        client.rejectOperationStart = false

        // Lose the first terminal poll response, then let the owner monitor find
        // the backend completion. The UI retry policy must preserve completion.
        let lostPollSession = UUID().uuidString
        client.operationState = "working"
        client.backendSettled = false
        _ = try await bridge.request(operation: "opStart",
            payload: ["kind": "install", "session": lostPollSession])
        client.hold = true
        let startCountBeforeLostPoll = client.operations.filter { $0 == "opStart" }.count
        let lostPoll = Task { try await bridge.request(operation: "opPoll", target: lostPollSession) }
        await waitForRequest(client)
        do { _ = try await lostPoll.value; preconditionFailure("stalled poll did not time out") } catch {}
        precondition(bridge.hasUncertainOperationSession(lostPollSession),
            "a lost poll must make the device outcome uncertain")
        client.flush()
        client.hold = false
        client.operationState = "completed"
        client.backendSettled = true
        let lostPollDeadline = Date().addingTimeInterval(4)
        while bridge.isMutating {
            precondition(Date() < lostPollDeadline, "owner monitor did not discover terminal completion")
            await Task.yield()
        }
        let recovered = try await bridge.request(operation: "opCancel", target: lostPollSession)
        precondition(recovered["state"] as? String == "completed" &&
            recovered["backendSettled"] as? Bool == true,
            "the settled session reply must preserve the old completion result")
        precondition(client.operations.filter { $0 == "opStart" }.count == startCountBeforeLostPoll,
            "a recovered completion must not start a duplicate operation")
        bridge.forgetSettledOperationSession(lostPollSession)

        // Disconnect while a native operation is unresolved cannot clear its gate.
        let disconnectedSession = UUID().uuidString
        client.operationState = "working"
        client.backendSettled = false
        _ = try await bridge.request(operation: "opStart",
            payload: ["kind": "delete", "session": disconnectedSession])
        bridge.disconnected()
        precondition(bridge.isMutating && bridge.hasUncertainOperationSession(disconnectedSession),
            "XPC loss cannot be treated as native operation cancellation")
        precondition(bridge.confirmUncertainOperationAfterDeviceCheck(sessionID: disconnectedSession))
        precondition(!bridge.isMutating)
        print("V3 lifecycle PASS")
    }
}
