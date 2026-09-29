import Foundation

@MainActor
final class AuthLeaseFakeClient {
    enum AuthPollReply {
        case unavailable
        case malformedUnavailable
        case success
    }

    var authPollReply: AuthPollReply = .success
    var holdAuthPollReplies = false
    var heldReplies: [() -> Void] = []
    var requests: [[String: Any]] = []

    func v3Execute(_ data: Data, reply: @escaping (Data) -> Void) {
        guard let request = V3WireContract.decodeRequest(data) else {
            preconditionFailure("bridge dispatched a request rejected by the production wire schema")
        }
        requests.append(request)
        let operation = request["operation"] as! String
        AuthLeaseRetirementTests.mark("dispatched \(operation)")
        let requestID = request["id"] as! String
        let target = request["target"] as? String ?? ""
        let payload = request["payload"] as? [String: Any] ?? [:]
        if operation == "cancel" {
            reply(encode(["version": 1, "id": requestID, "ok": true, "result": [:]]))
            return
        }

        let response: [String: Any]
        switch operation {
        case "authBegin", "authRetryProvisioning":
            response = ["version": 1, "id": requestID, "ok": true,
                "result": ["session": payload["session"] as? String ?? target,
                           "state": "working"]]
        case "authPoll":
            switch authPollReply {
            case .unavailable:
                let failure = CombinedFailure(operation: "signIn", stage: .authentication,
                    code: .invalidResponse, id: requestID,
                    safeCause: .authSessionUnavailable)
                response = ["version": 1, "id": requestID, "ok": false,
                    "error": "invalidResponse", "failure": failure.wire]
            case .malformedUnavailable:
                let failure = CombinedFailure(operation: "signIn", stage: .authentication,
                    code: .invalidResponse, id: UUID().uuidString,
                    safeCause: .authSessionUnavailable)
                response = ["version": 1, "id": requestID, "ok": false,
                    "error": "invalidResponse", "failure": failure.wire]
            case .success:
                response = ["version": 1, "id": requestID, "ok": true,
                    "result": ["session": target, "state": "completed", "authenticated": true]]
            }
        case "snapshot":
            response = ["version": 1, "id": requestID, "ok": true,
                "result": ["busy": false, "activeMutation": false,
                           "recoveryHold": false, "authenticationActive": false]]
        default:
            response = ["version": 1, "id": requestID, "ok": true, "result": [:]]
        }

        let encoded = encode(response)
        if operation == "authPoll" && holdAuthPollReplies {
            AuthLeaseRetirementTests.mark("held authPoll response")
            heldReplies.append { reply(encoded) }
        } else {
            AuthLeaseRetirementTests.mark("replied \(operation)")
            reply(encoded)
        }
    }

    func flushAuthPollReplies() {
        let replies = heldReplies
        heldReplies.removeAll()
        AuthLeaseRetirementTests.mark("flushed \(replies.count) authPoll replies")
        replies.forEach { $0() }
    }

    private func encode(_ value: [String: Any]) -> Data {
        try! PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
    }
}

@MainActor
final class RefreshHandler {
    static let shared = RefreshHandler()
    var sideStorePid: Int32 = 321
    var v3RefreshToken: UUID?
    var v3RefreshAdmissionRunID: String?
    var client: AuthLeaseFakeClient? = AuthLeaseFakeClient()
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
struct AuthLeaseRetirementTests {
    static func mark(_ stage: String) {
        FileHandle.standardError.write(Data("[AUTH_LEASE_HARNESS] \(stage)\n".utf8))
    }

    @MainActor
    static func waitForRequest(_ client: AuthLeaseFakeClient, after count: Int,
                               operation: String, target: String) async {
        mark("wait request \(operation)")
        let deadline = Date().addingTimeInterval(2)
        while !client.requests.dropFirst(min(count, client.requests.count)).contains(where: {
            $0["operation"] as? String == operation && $0["target"] as? String == target
        }) {
            if Date() >= deadline { preconditionFailure("request not sent: \(operation)/\(target)") }
            await Task.yield()
        }
        mark("observed request \(operation)")
    }

    @MainActor
    static func startAuth(_ bridge: V3ServiceBridge, sessionID: String) async throws {
        mark("await authBegin")
        _ = try await bridge.request(operation: "authBegin", target: sessionID,
            payload: ["session": sessionID,
                     "sessionDeadline": Date().addingTimeInterval(600)])
        mark("returned authBegin")
    }

    @MainActor
    static func expectStartBlocked(_ bridge: V3ServiceBridge, sessionID: String) async {
        mark("await blocked authBegin")
        do {
            try await startAuth(bridge, sessionID: sessionID)
            preconditionFailure("new authBegin passed unresolved auth ownership")
        } catch let failure as CombinedFailure {
            precondition(failure.code == .busy, "unresolved auth owner returned \(failure.code)")
            mark("blocked authBegin confirmed")
        } catch {
            preconditionFailure("unresolved auth owner returned unexpected error: \(error)")
        }
    }

    @MainActor
    static func pollUnavailable(_ bridge: V3ServiceBridge, sessionID: String) async {
        mark("await unavailable authPoll")
        do {
            _ = try await bridge.request(operation: "authPoll", target: sessionID)
            preconditionFailure("missing auth session was accepted")
        } catch let failure as CombinedFailure {
            precondition(failure.safeCause == .authSessionUnavailable,
                "the exact session-unavailable reply lost its typed cause")
            mark("authPoll unavailable confirmed")
        } catch {
            preconditionFailure("authPoll returned unexpected error: \(error)")
        }
    }

    @MainActor
    static func main() async throws {
        mark("begin owner isolation")
        let handler = RefreshHandler.shared
        let client = handler.client!

        let retainedOwners = ["request:direct", "operation:\(UUID().uuidString)",
                              "refresh:\(UUID().uuidString)"]
        let exactAuthOwner = "auth:\(UUID().uuidString)"
        var authority = V3StatusWriteAuthority()
        for ownerID in retainedOwners {
            let revision = authority.reserveMutationRevision()
            let ticket = authority.begin(ownerID: ownerID, revision: revision,
                serviceInstanceID: "321", kind: .mutation, allowUnresolvedMutation: true)!
            precondition(authority.complete(ticket, outcome: .outcomeUnknown))
        }
        let activeRevision = authority.reserveMutationRevision()
        _ = authority.begin(ownerID: exactAuthOwner, revision: activeRevision,
            serviceInstanceID: "321", kind: .mutation, allowUnresolvedMutation: true)!
        precondition(authority.resolveOwnerAfterAuthoritativeReconciliation(exactAuthOwner) &&
                     authority.activeLease == nil &&
                     authority.unresolvedOwnerIDs == Set(retainedOwners),
            "auth reconciliation must settle only its exact active owner")

        // A typed unavailable reply also settles a live active auth lease,
        // then a fresh snapshot can reconcile account state independently.
        mark("active lease unavailable path")
        let activeUnavailableBridge = V3ServiceBridge(readTimeout: 2, commandTimeout: 2)
        let activeUnavailableSession = UUID().uuidString
        try await startAuth(activeUnavailableBridge, sessionID: activeUnavailableSession)
        client.authPollReply = .unavailable
        await pollUnavailable(activeUnavailableBridge, sessionID: activeUnavailableSession)
        precondition(!activeUnavailableBridge.isMutating,
            "an exact unavailable reply must settle a matching active auth lease")
        mark("await account snapshot after unavailable")
        let freshSnapshot = try await activeUnavailableBridge.request(operation: "snapshot")
        mark("returned account snapshot after unavailable")
        precondition(freshSnapshot["authenticationActive"] as? Bool == false,
            "account state remains separately reconcilable from auth-session ownership")
        try await startAuth(activeUnavailableBridge, sessionID: UUID().uuidString)

        // A valid correlated unavailable reply from authPoll releases only the
        // retired session's shared status owner and allows a fresh authBegin.
        mark("retired lease unavailable path")
        let unavailableBridge = V3ServiceBridge(readTimeout: 2, commandTimeout: 2)
        let unavailableSession = UUID().uuidString
        try await startAuth(unavailableBridge, sessionID: unavailableSession)
        precondition(unavailableBridge.isMutating)
        unavailableBridge.disconnected()
        precondition(unavailableBridge.isMutating,
            "process retirement alone must preserve the unresolved auth status owner")
        client.authPollReply = .unavailable
        await pollUnavailable(unavailableBridge, sessionID: unavailableSession)
        precondition(!unavailableBridge.isMutating,
            "the exact typed unavailable reply must resolve auth and status ownership")
        let replacementSession = UUID().uuidString
        try await startAuth(unavailableBridge, sessionID: replacementSession)
        client.authPollReply = .success
        mark("await replacement authPoll")
        let replacementPoll = try await unavailableBridge.request(operation: "authPoll", target: replacementSession)
        mark("returned replacement authPoll")
        precondition(replacementPoll["authenticated"] as? Bool == true,
            "a new session must remain usable after exact unavailable reconciliation")

        // An unavailable reply for another session and a false snapshot fact
        // for the exact owner are kept distinct.
        mark("wrong session and snapshot path")
        let snapshotBridge = V3ServiceBridge(readTimeout: 2, commandTimeout: 2)
        let snapshotSession = UUID().uuidString
        try await startAuth(snapshotBridge, sessionID: snapshotSession)
        let wrongSession = UUID().uuidString
        client.authPollReply = .unavailable
        await pollUnavailable(snapshotBridge, sessionID: wrongSession)
        precondition(snapshotBridge.isMutating,
            "an unavailable response for another session cannot release the auth owner")
        await expectStartBlocked(snapshotBridge, sessionID: UUID().uuidString)
        snapshotBridge.reconcileAuthSessionOwnership(sessionID: snapshotSession, authenticationActive: true)
        precondition(snapshotBridge.isMutating,
            "a snapshot that still reports active auth cannot release the owner")
        snapshotBridge.reconcileAuthSessionOwnership(sessionID: snapshotSession, authenticationActive: false)
        precondition(!snapshotBridge.isMutating,
            "a validated inactive snapshot must release only its exact auth owner")
        try await startAuth(snapshotBridge, sessionID: UUID().uuidString)

        // A malformed structured failure and transport loss preserve ownership.
        mark("malformed response path")
        let malformedBridge = V3ServiceBridge(readTimeout: 2, commandTimeout: 2)
        let malformedSession = UUID().uuidString
        try await startAuth(malformedBridge, sessionID: malformedSession)
        client.authPollReply = .malformedUnavailable
        do {
            _ = try await malformedBridge.request(operation: "authPoll", target: malformedSession)
            preconditionFailure("malformed unavailable reply was accepted")
        }
        catch let failure as CombinedFailure {
            precondition(failure.safeCause != .authSessionUnavailable,
                "a malformed failure envelope retained an untrusted auth cause")
        }
        precondition(malformedBridge.isMutating,
            "a malformed unavailable reply cannot release the auth owner")
        await expectStartBlocked(malformedBridge, sessionID: UUID().uuidString)

        let transportBridge = V3ServiceBridge(readTimeout: 2, commandTimeout: 2)
        mark("transport loss path")
        let transportSession = UUID().uuidString
        try await startAuth(transportBridge, sessionID: transportSession)
        transportBridge.disconnected()
        client.authPollReply = .success
        client.holdAuthPollReplies = true
        let baseline = client.requests.count
        let interruptedPoll = Task {
            try await transportBridge.request(operation: "authPoll", target: transportSession)
        }
        await waitForRequest(client, after: baseline, operation: "authPoll", target: transportSession)
        transportBridge.disconnected()
        do { _ = try await interruptedPoll.value; preconditionFailure("transport loss was ignored") }
        catch let failure as CombinedFailure {
            precondition(failure.stage == .xpcConnection)
            mark("transport interruption confirmed")
        }
        precondition(transportBridge.isMutating,
            "transport loss without an authoritative reply cannot release the auth owner")
        await expectStartBlocked(transportBridge, sessionID: UUID().uuidString)
        client.holdAuthPollReplies = false
        client.flushAuthPollReplies()

        // A terminal success arriving after cancellation is still authoritative.
        mark("late success after cancellation path")
        let lateSuccessBridge = V3ServiceBridge(readTimeout: 2, commandTimeout: 2)
        let lateSuccessSession = UUID().uuidString
        try await startAuth(lateSuccessBridge, sessionID: lateSuccessSession)
        lateSuccessBridge.disconnected()
        client.holdAuthPollReplies = true
        let lateBaseline = client.requests.count
        let cancelledPoll = Task {
            try await lateSuccessBridge.request(operation: "authPoll", target: lateSuccessSession)
        }
        await waitForRequest(client, after: lateBaseline, operation: "authPoll", target: lateSuccessSession)
        cancelledPoll.cancel()
        do { _ = try await cancelledPoll.value; preconditionFailure("authPoll cancellation was ignored") }
        catch is CancellationError {} catch { preconditionFailure("wrong authPoll cancellation error") }
        mark("cancelled authPoll waiter completed")
        precondition(lateSuccessBridge.isMutating,
            "cancelling the waiter cannot release the retired auth owner")
        client.holdAuthPollReplies = false
        client.flushAuthPollReplies()
        let deadline = Date().addingTimeInterval(2)
        while lateSuccessBridge.isMutating {
            if Date() >= deadline { preconditionFailure("late authoritative auth success did not resolve ownership") }
            await Task.yield()
        }
        mark("late authPoll success released owner")
        let afterLateSuccess = UUID().uuidString
        try await startAuth(lateSuccessBridge, sessionID: afterLateSuccess)
        mark("completed")
        print("V3 auth lease retirement PASS")
    }
}
