import Foundation

@MainActor
final class FakeClient {
    var hold = false
    var stale = false
    var oversized = false
    var backendSettled = true
    var operationState = "working"
    var rejectOperationStart = false
    var badVersionOperationStart = false
    var omitOutcomeUnknown = false
    var replies: [() -> Void] = []
    var cancellations = 0
    var operations: [String] = []
    var requests: [[String: Any]] = []
    var boolSettings: [String: Bool] = ["isCellularRefreshEnabled": false]
    func v3Execute(_ data: Data, reply: @escaping (Data) -> Void) {
        guard let request = V3WireContract.decodeRequest(data) else {
            preconditionFailure("bridge dispatched a request rejected by the production wire schema")
        }
        operations.append(request["operation"] as! String)
        requests.append(request)
        if request["operation"] as? String == "cancel" { cancellations += 1; reply(Data()); return }
        let operation = request["operation"] as! String
        let payload = request["payload"] as? [String: Any] ?? [:]
        let target = request["target"] as? String ?? ""
        let operationResult: [String: Any]
        switch operation {
        case "snapshot":
            operationResult = snapshotResult(busy: false)
        case "authBegin":
            operationResult = ["session": payload["session"] as? String ?? "", "state": "working"]
        case "authPoll":
            operationResult = ["session": target, "state": "completed", "authenticated": true]
        case "authCancel":
            operationResult = ["session": target, "state": "cancelled", "authenticated": false]
        case "opStart":
            if rejectOperationStart {
                let failure = CombinedFailure(operation: "install", stage: .command,
                    code: .busy, id: request["id"] as! String, retryable: true)
                let rejected: [String: Any] = ["version": badVersionOperationStart ? 2 : 1, "id": request["id"]!,
                    "error": "busy", "failure": failure.wire,
                    "operationNotDispatched": true]
                let encoded = try! PropertyListSerialization.data(fromPropertyList: rejected,
                    format: .binary, options: 0)
                reply(encoded)
                return
            }
            operationResult = ["session": payload["session"] as? String ?? "", "state": "working"]
        case "opPoll":
            var result: [String: Any] = ["session": target, "state": operationState,
                                         "backendSettled": backendSettled]
            if !omitOutcomeUnknown { result["outcomeUnknown"] = !backendSettled }
            operationResult = result
        case "opCancel":
            var result: [String: Any] = ["session": target, "state": operationState,
                                         "backendSettled": backendSettled]
            if !omitOutcomeUnknown { result["outcomeUnknown"] = !backendSettled }
            operationResult = result
        case "settingsSet":
            guard payload["key"] as? String == "isCellularRefreshEnabled",
                  payload["type"] as? String == "bool",
                  let value = V3WireContract.strictBool(payload["bool"]) else {
                preconditionFailure("fixture settings mutation is outside the production SideStore allowlist")
            }
            boolSettings["isCellularRefreshEnabled"] = value
            operationResult = snapshotResult(busy: true)
        default:
            operationResult = ["account": "fixture"]
        }
        let result: [String: Any] = ["version": 1, "id": stale ? UUID().uuidString : request["id"]!,
                                     "ok": true, "result": operationResult]
        let encoded = oversized ? Data(repeating: 0, count: 4_194_305) :
            try! PropertyListSerialization.data(fromPropertyList: result, format: .binary, options: 0)
        if hold { replies.append { reply(encoded) } } else { reply(encoded) }
    }
    private func snapshotResult(busy: Bool) -> [String: Any] {
        ["updatedAt": Date(), "busy": busy, "recoveryJournalUnreadable": false,
         "account": "fixture", "authenticated": false, "activeAccountPresent": false,
         "activeTeamPresent": false, "activeCertificatePresent": false,
         "authenticationActive": false, "provisioningIncomplete": false,
         "provisioningRetryAvailable": false, "team": "No active team", "teamID": "",
         "signing": "Sign in required", "certificate": "No active certificate",
         "certificateExpiration": Date.distantPast, "pairing": "missing",
         "installedApps": [], "sources": [],
         "settings": ["betaUpdates": false, "idleTimeoutDisabled": false,
                      "responseCachingDisabled": false, "verboseOperations": false]]
    }
    func flush() { let old = replies; replies = []; old.forEach { $0() } }
}

@MainActor
final class RefreshHandler {
    static let shared = RefreshHandler()
    var sideStorePid: Int32 = 123
    var v3RefreshToken: UUID?
    var v3RefreshAdmissionRunID: String?
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
    static func waitForRequest(_ client: FakeClient, bridge: V3ServiceBridge,
                               afterRequestCount: Int, operation: String,
                               target: String? = nil, sessionID: String? = nil,
                               context: String) async {
        let deadline = Date().addingTimeInterval(2)
        func matches(_ request: [String: Any]) -> Bool {
            guard request["operation"] as? String == operation else { return false }
            if let target, request["target"] as? String != target { return false }
            if let sessionID {
                let payload = request["payload"] as? [String: Any] ?? [:]
                let requestSession = operation == "opStart"
                    ? payload["session"] as? String
                    : (payload["session"] as? String ?? request["target"] as? String)
                guard requestSession == sessionID else { return false }
            }
            return true
        }
        while !client.requests.dropFirst(min(afterRequestCount, client.requests.count)).contains(where: matches) {
            if Date() >= deadline {
                let observed = client.requests.dropFirst(min(afterRequestCount, client.requests.count))
                    .compactMap { $0["operation"] as? String }.joined(separator: ",")
                preconditionFailure("request was never sent context=\(context) expected=\(operation) target=\(target ?? "-") session=\(sessionID ?? "-") observed_after_baseline=[\(observed)] bridge_mutating=\(bridge.isMutating) queued_replies=\(client.replies.count)")
            }
            await Task.yield()
        }
    }

    @MainActor
    static func waitForOwnershipRelease(_ bridge: V3ServiceBridge, handler: RefreshHandler,
                                        stopCountBefore: Int, context: String) async {
        let deadline = Date().addingTimeInterval(4)
        while bridge.isMutating {
            if Date() >= deadline {
                preconditionFailure("mutation ownership did not settle context=\(context) service_stops=\(handler.stops - stopCountBefore)")
            }
            await Task.yield()
        }
    }
    @MainActor
    static func main() async throws {
        let bridge = V3ServiceBridge(readTimeout: 0.25, commandTimeout: 1)
        let handler = RefreshHandler.shared
        let client = handler.client!
        let storeAppTarget = "x-coredata://A1B2C3D4-E5F6-47A8-9123-456789ABCDEF/StoreApp/p42"
        let installedAppTarget = "x-coredata://A1B2C3D4-E5F6-47A8-9123-456789ABCDEF/InstalledApp/p42"
        async let a: Void = bridge.connect()
        async let b: Void = bridge.connect()
        _ = try await (a, b)
        precondition(handler.connects == 1, "launch must be coalesced")
        let invalidStartSession = UUID().uuidString
        // Deliberate local property-list serialization failure. The operation
        // fields are valid so NSNull is the only reason this never dispatches.
        do {
            _ = try await bridge.request(operation: "opStart",
                payload: ["kind": "install", "target": storeAppTarget,
                          "session": invalidStartSession, "unplistable": NSNull()])
            preconditionFailure("unplistable opStart payload was accepted")
        } catch {}
        precondition(!bridge.isMutating,
                     "local plist encoding failure must not retain a synthetic operation session")
        let oversizedStartSession = UUID().uuidString
        // Deliberate local request-size rejection with a valid operation target.
        do {
            _ = try await bridge.request(operation: "opStart",
                payload: ["kind": "install", "target": storeAppTarget,
                          "session": oversizedStartSession,
                          "extra": String(repeating: "x", count: 20_000)])
            preconditionFailure("oversized opStart request was accepted")
        } catch {}
        precondition(!bridge.isMutating,
                     "local request-size rejection must not retain a synthetic operation session")
        let value = try await bridge.request(operation: "snapshot")
        precondition(value["account"] as? String == "fixture")
        precondition(client.operations == ["snapshot"], "cold launch/status triggered a mutation")
        let settingsSnapshot = try await bridge.request(operation: "settingsSet",
            payload: ["key": "isCellularRefreshEnabled", "type": "bool", "bool": true])
        precondition(settingsSnapshot["account"] as? String == "fixture" &&
                     settingsSnapshot["busy"] as? Bool == true &&
                     settingsSnapshot["settings"] is [String: Any],
                     "settingsSet must return the service's snapshot-shaped success reply")
        precondition(client.boolSettings["isCellularRefreshEnabled"] == true,
                     "the fake service must apply an allowlisted setting before reporting success")
        let signInSession = UUID().uuidString
        _ = try await bridge.request(operation: "authBegin", target: signInSession,
            payload: ["session": signInSession, "sessionDeadline": Date().addingTimeInterval(600)])
        let refreshSession = UUID().uuidString
        let refreshPayload: [String: Any] = ["kind": "refreshApp", "target": installedAppTarget,
                                             "session": refreshSession]
        let callsBeforeBlockedOpStart = client.operations.count
        do {
            _ = try await bridge.request(operation: "opStart", target: "", payload: refreshPayload)
            preconditionFailure("refresh opStart bypassed active authentication ownership")
        } catch let failure as CombinedFailure {
            precondition(failure.operation == "command" && failure.stage == .command &&
                failure.code == .busy && failure.safeCause == .operationInProgress,
                "opStart during an active auth session returns the typed ownership conflict")
        }
        precondition(client.operations.count == callsBeforeBlockedOpStart,
            "host admission rejects opStart before dispatch while auth is unresolved")
        _ = try await bridge.request(operation: "authPoll", target: signInSession)
        _ = try await bridge.request(operation: "opStart", target: "", payload: refreshPayload)
        let dispatchedStart = client.requests.last!
        let dispatchedPayload = dispatchedStart["payload"] as! [String: Any]
        precondition(dispatchedStart["target"] as? String == "" &&
                     dispatchedPayload["kind"] as? String == "refreshApp" &&
                     dispatchedPayload["target"] as? String == installedAppTarget &&
                     dispatchedPayload["session"] as? String == refreshSession,
            "opStart must use the payload schema, InstalledApp URI, and canonical session ID")
        client.operationState = "completed"
        let settledRefresh = try await bridge.request(operation: "opPoll", target: refreshSession)
        precondition(settledRefresh["state"] as? String == "completed" &&
                     settledRefresh["backendSettled"] as? Bool == true,
            "the refresh session must reach an authoritative terminal reply")
        precondition(!bridge.isMutating,
            "a settled opPoll releases the refresh session's host mutation ownership")
        precondition(client.operations == ["snapshot", "settingsSet", "authBegin", "authPoll", "opStart", "opPoll"],
            "explicit account/opStart/opPoll integration order changed")
        client.stale = true
        do { _ = try await bridge.request(operation: "snapshot"); preconditionFailure("stale reply accepted") } catch {}
        client.stale = false; client.oversized = true
        do { _ = try await bridge.request(operation: "snapshot"); preconditionFailure("oversized reply accepted") } catch {}
        client.oversized = false; client.hold = true
        var requestBaseline = client.requests.count
        let cancelled = Task {
            try await bridge.request(operation: "settingsSet",
                payload: ["key": "isCellularRefreshEnabled", "type": "bool", "bool": true])
        }
        await waitForRequest(client, bridge: bridge, afterRequestCount: requestBaseline,
                             operation: "settingsSet", context: "cancelled valid mutation late reply")
        cancelled.cancel()
        do { _ = try await cancelled.value; preconditionFailure("cancel ignored") } catch is CancellationError {} catch { preconditionFailure("wrong cancellation") }
        precondition(client.cancellations == 1)
        precondition(bridge.isMutating,
                     "an unacknowledged cancellation must retain mutation ownership until a late reply or service retirement")
        client.flush() // Late success cannot resume an already completed continuation.
        requestBaseline = client.requests.count
        let interrupted = Task { try await bridge.request(operation: "snapshot") }
        await waitForRequest(client, bridge: bridge, afterRequestCount: requestBaseline,
                             operation: "snapshot", context: "disconnect read")
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
        requestBaseline = client.requests.count
        let mutation = Task {
            try await bridge.request(operation: "settingsSet",
                payload: ["key": "isCellularRefreshEnabled", "type": "bool", "bool": true])
        }
        await waitForRequest(client, bridge: bridge, afterRequestCount: requestBaseline,
                             operation: "settingsSet", context: "cancellable valid mutation")
        do {
            _ = try await bridge.request(operation: "signOut")
            preconditionFailure("concurrent mutation accepted")
        } catch {}
        mutation.cancel()
        _ = try? await mutation.value
        client.flush()
        client.hold = false
        _ = try await bridge.request(operation: "snapshot")
        // The late success callback above is dispatched to MainActor by the
        // production bridge. Until that callback retires cancellation recovery,
        // a new mutation must remain blocked. Wait for the bridge's ownership
        // state, not for an assumed actor scheduling delay.
        let stopCountBeforeLateMutationReply = handler.stops
        await waitForOwnershipRelease(bridge, handler: handler,
            stopCountBefore: stopCountBeforeLateMutationReply,
            context: "late mutation reply or bounded service retirement")
        // Every boundary must retain the operation, including concurrent reads and a mutation.
        // Keep this disconnect race independent from the short request deadlines
        // used above to exercise timeout recovery. On a loaded CI runner, a
        // 250 ms read timeout can otherwise win before the harness gets to the
        // explicit disconnect below.
        let disconnectBridge = V3ServiceBridge(readTimeout: 5, commandTimeout: 5)
        client.hold = true
        requestBaseline = client.requests.count
        let disconnectRequestBaseline = requestBaseline
        let mutationDisconnect = Task {
            try await disconnectBridge.request(operation: "settingsSet",
                payload: ["key": "isCellularRefreshEnabled", "type": "bool", "bool": true])
        }
        await waitForRequest(client, bridge: disconnectBridge, afterRequestCount: requestBaseline,
                             operation: "settingsSet", context: "disconnect mutation")
        requestBaseline = client.requests.count
        let catalogDisconnect = Task { try await disconnectBridge.request(operation: "catalog") }
        await waitForRequest(client, bridge: disconnectBridge, afterRequestCount: requestBaseline,
                             operation: "catalog", context: "concurrent disconnect read")
        precondition(client.replies.count >= 2,
                     "both held requests must be awaiting their service replies before disconnect")
        let disconnectRequests = Array(client.requests.dropFirst(disconnectRequestBaseline))
        let settingsRequestID = (disconnectRequests.first { $0["operation"] as? String == "settingsSet" }?["id"] as? String)!
        let catalogRequestID = (disconnectRequests.first { $0["operation"] as? String == "catalog" }?["id"] as? String)!
        disconnectBridge.disconnected()
        do { _ = try await mutationDisconnect.value; preconditionFailure("disconnect ignored settingsSet") }
        catch let error as CombinedFailure {
            precondition(error.operation == "command" && error.stage == .xpcConnection &&
                         error.correlationID == settingsRequestID &&
                         error.requestContext == "request_operation=settingsSet request_correlation=\(settingsRequestID)",
                "settingsSet disconnect context lost: operation=\(error.operation) stage=\(error.stage) id=\(error.correlationID) context=\(error.requestContext ?? "-")")
        }
        do { _ = try await catalogDisconnect.value; preconditionFailure("disconnect ignored catalog") }
        catch let error as CombinedFailure {
            precondition(error.operation == "catalog" && error.stage == .xpcConnection &&
                         error.correlationID == catalogRequestID,
                "catalog disconnect context lost: operation=\(error.operation) stage=\(error.stage) id=\(error.correlationID)")
        }
        client.flush()
        let recovery = V3ServiceBridge(readTimeout: 1, commandTimeout: 1, cancellationGrace: 0.02)
        let stopsBeforeRecovery = handler.stops
        client.hold = true
        let stuckAuthSession = UUID().uuidString
        requestBaseline = client.requests.count
        let stuck = Task {
            try await recovery.request(operation: "authBegin", target: stuckAuthSession,
                payload: ["session": stuckAuthSession, "sessionDeadline": Date().addingTimeInterval(600)])
        }
        await waitForRequest(client, bridge: recovery, afterRequestCount: requestBaseline,
                             operation: "authBegin", target: stuckAuthSession,
                             sessionID: stuckAuthSession, context: "cancelled authentication start")
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
            payload: ["kind": "install", "target": storeAppTarget, "session": operationSession])
        precondition(bridge.isMutating, "a returned opStart must retain service mutation ownership")
        client.hold = true
        requestBaseline = client.requests.count
        let poll = Task { try await bridge.request(operation: "opPoll", target: operationSession) }
        await waitForRequest(client, bridge: bridge, afterRequestCount: requestBaseline,
                             operation: "opPoll", target: operationSession,
                             sessionID: operationSession, context: "timed out operation poll")
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
        requestBaseline = client.requests.count
        let cancelRequest = Task { try await bridge.request(operation: "opCancel", target: operationSession) }
        await waitForRequest(client, bridge: bridge, afterRequestCount: requestBaseline,
                             operation: "opCancel", target: operationSession,
                             sessionID: operationSession, context: "timed out operation cancellation")
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
            payload: ["kind": "delete", "target": installedAppTarget, "session": unresolvedSession])
        client.operationState = "failed"
        let unresolved = try await bridge.request(operation: "opPoll", target: unresolvedSession)
        precondition(unresolved["outcomeUnknown"] as? Bool == true && bridge.isMutating)
        let stopsBeforeConfirmation = handler.stops
        precondition(bridge.confirmUncertainOperationAfterDeviceCheck(sessionID: unresolvedSession),
                     "user-confirmed device reconciliation must retire the uncertain backend process")
        precondition(handler.stops == stopsBeforeConfirmation + 1 && !bridge.isMutating,
                     "the explicit confirmed-reconciliation path must release the host mutation gate")

        // A terminal authCancel reply arriving after its request times out is
        // late and bypasses ownership classification. It must not suppress the
        // bounded service-retirement path that clears the host auth owner.
        let lateAuthBridge = V3ServiceBridge(readTimeout: 0.05, commandTimeout: 0.05,
                                             cancellationGrace: 0.05)
        let authSession = UUID().uuidString
        _ = try await lateAuthBridge.request(operation: "authBegin", target: authSession,
            payload: ["session": authSession, "sessionDeadline": Date().addingTimeInterval(60)])
        precondition(lateAuthBridge.isMutating, "a live auth session owns host mutation admission")
        client.hold = true
        let stopsBeforeLateAuthCancel = handler.stops
        requestBaseline = client.requests.count
        let lateAuthCancel = Task {
            try await lateAuthBridge.request(operation: "authCancel", target: authSession)
        }
        await waitForRequest(client, bridge: lateAuthBridge, afterRequestCount: requestBaseline,
                             operation: "authCancel", target: authSession,
                             sessionID: authSession, context: "timed out authentication cancellation")
        do { _ = try await lateAuthCancel.value; preconditionFailure("authCancel timeout was lost") }
        catch let failure as CombinedFailure { precondition(failure.code == .timedOut) }
        client.flush()
        let authRetirementDeadline = Date().addingTimeInterval(2)
        while handler.stops == stopsBeforeLateAuthCancel {
            precondition(Date() < authRetirementDeadline,
                         "late authCancel reply incorrectly cancelled service-retirement recovery")
            await Task.yield()
        }
        precondition(!lateAuthBridge.isMutating,
                     "confirmed service retirement clears ownership after a late authCancel reply")
        client.hold = false

        // A service-level rejection before the operation center creates a session
        // is authoritative proof that the mutation was never dispatched.
        client.rejectOperationStart = true
        let rejectedSession = UUID().uuidString
        let requestsBeforeRejectedStart = client.requests.count
        do {
            _ = try await bridge.request(operation: "opStart",
                payload: ["kind": "install", "target": storeAppTarget, "session": rejectedSession])
            preconditionFailure("the fake service start rejection was accepted")
        } catch {}
        precondition(client.requests.dropFirst(requestsBeforeRejectedStart).contains {
            $0["operation"] as? String == "opStart" &&
            (($0["payload"] as? [String: Any])?["session"] as? String) == rejectedSession
        }, "a schema-valid service rejection must reach the fake service")
        precondition(!bridge.isMutating && !bridge.hasUncertainOperationSession(rejectedSession),
            "a structured pre-dispatch rejection must release session ownership")
        client.rejectOperationStart = false

        // A malformed root version cannot use the rejection marker to release
        // ownership; only a validated correlated envelope proves no dispatch.
        client.rejectOperationStart = true
        client.badVersionOperationStart = true
        let malformedRejectedSession = UUID().uuidString
        let requestsBeforeMalformedRejection = client.requests.count
        do {
            _ = try await bridge.request(operation: "opStart",
                payload: ["kind": "install", "target": storeAppTarget,
                          "session": malformedRejectedSession])
            preconditionFailure("an invalid-version start rejection was accepted")
        } catch {}
        precondition(client.requests.dropFirst(requestsBeforeMalformedRejection).contains {
            $0["operation"] as? String == "opStart" &&
            (($0["payload"] as? [String: Any])?["session"] as? String) == malformedRejectedSession
        }, "a schema-valid malformed service reply must be exercised after dispatch")
        precondition(bridge.isMutating && bridge.hasUncertainOperationSession(malformedRejectedSession),
            "a malformed rejection must preserve unknown operation ownership")
        precondition(bridge.confirmUncertainOperationAfterDeviceCheck(sessionID: malformedRejectedSession))
        client.rejectOperationStart = false
        client.badVersionOperationStart = false

        // Lose the first terminal poll response, then let the owner monitor find
        // the backend completion. The UI retry policy must preserve completion.
        let lostPollSession = UUID().uuidString
        client.operationState = "working"
        client.backendSettled = false
        _ = try await bridge.request(operation: "opStart",
            payload: ["kind": "install", "target": storeAppTarget, "session": lostPollSession])
        client.hold = true
        let startCountBeforeLostPoll = client.operations.filter { $0 == "opStart" }.count
        requestBaseline = client.requests.count
        let lostPoll = Task { try await bridge.request(operation: "opPoll", target: lostPollSession) }
        await waitForRequest(client, bridge: bridge, afterRequestCount: requestBaseline,
                             operation: "opPoll", target: lostPollSession,
                             sessionID: lostPollSession, context: "lost terminal poll")
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

        // Production terminal replies include backendSettled and omit the
        // optional outcomeUnknown field; this normal shape must release ownership.
        let standardTerminalSession = UUID().uuidString
        client.operationState = "working"
        client.backendSettled = false
        _ = try await bridge.request(operation: "opStart",
            payload: ["kind": "install", "target": storeAppTarget, "session": standardTerminalSession])
        client.omitOutcomeUnknown = true
        client.operationState = "completed"
        client.backendSettled = true
        let standardTerminal = try await bridge.request(operation: "opPoll", target: standardTerminalSession)
        precondition(standardTerminal["outcomeUnknown"] == nil && !bridge.isMutating,
            "a settled terminal reply without outcomeUnknown must release ownership")
        bridge.forgetSettledOperationSession(standardTerminalSession)
        client.omitOutcomeUnknown = false

        // Disconnect while a native operation is unresolved cannot clear its gate.
        let disconnectedSession = UUID().uuidString
        client.operationState = "working"
        client.backendSettled = false
        _ = try await bridge.request(operation: "opStart",
            payload: ["kind": "delete", "target": installedAppTarget,
                      "session": disconnectedSession])
        bridge.disconnected()
        precondition(bridge.isMutating && bridge.hasUncertainOperationSession(disconnectedSession),
            "XPC loss cannot be treated as native operation cancellation")
        precondition(bridge.confirmUncertainOperationAfterDeviceCheck(sessionID: disconnectedSession))
        precondition(!bridge.isMutating)
        print("V3 lifecycle PASS")
    }
}
