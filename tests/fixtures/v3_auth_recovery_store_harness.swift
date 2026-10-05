
@MainActor
final class V3ServiceBridge {
    static let shared = V3ServiceBridge()
    static let authSessionLifetime: TimeInterval = 600
    static func strictBool(_ value: Any?) -> Bool? { V3WireContract.strictBool(value) }
    static func strictInt(_ value: Any?) -> Int? { V3WireContract.strictInt(value) }
    static func authSnapshot(_ value: [String: Any]) -> V3AuthServiceSnapshot? { V3WireContract.authSnapshot(value) }
    var snapshot: [String: Any] = [:]
    var starts: [[String: Any]] = []
    var terminalState = "authenticatedProvisioningIncomplete"
    func reconcileAuthSessionOwnership(sessionID: String, authenticationActive: Bool) {}
    func confirmAuthSessionUnavailable(sessionID: String) {}
    func request(operation: String, target: String = "", payload: [String: Any] = [:],
                 requestDeadline: Date? = nil) async throws -> [String: Any] {
        var envelope: [String: Any] = ["version": 1, "id": UUID().uuidString,
            "operation": operation, "target": target, "deadline": Date().addingTimeInterval(20)]
        if !payload.isEmpty { envelope["payload"] = payload }
        precondition(V3WireContract.encodeRequest(envelope) != nil, "real host request failed wire schema")
        let reply: [String: Any]
        switch operation {
        case "snapshot": reply = snapshot
        case "authBegin":
            starts.append(payload)
            reply = ["session": target, "state": "working", "revision": 0]
        case "authPoll", "authCancel":
            reply = ["session": target, "state": terminalState, "authenticated": true,
                "resumable": false, "revision": 1, "message": "Provisioning was deferred."]
        default: fatalError("Unexpected IO: \(operation)")
        }
        let bytes = try PropertyListSerialization.data(fromPropertyList: reply, format: .binary, options: 0)
        return try PropertyListSerialization.propertyList(from: bytes, format: nil) as! [String: Any]
    }
}

@MainActor
final class Button {
    static var captured: [Button] = []
    let title: String
    let action: () -> Void
    var isDisabled = false
    init(_ title: String, action: @escaping () -> Void) {
        self.title = title; self.action = action
        Self.captured.append(self)
    }
    @discardableResult func disabled(_ value: Bool) -> Button { isDisabled = value; return self }
    static func render(auth: V3AuthStore) -> [Button] {
        captured = []
        let recovery = auth.provisioningRecoveryActions
        __PRODUCTION_BUTTON_BRANCH__
        return captured
    }
}

@main
struct RecoveryStoreHarness {
    @MainActor static func main() async throws {
        let bridge = V3ServiceBridge.shared
        bridge.snapshot = ["authenticated": true, "credentialRoutePresent": true,
            "provisioningIncomplete": true, "provisioningRetryAvailable": false,
            "provisioningReauthenticationAvailable": true, "authenticationActive": false,
            "identityStable": true, "identityStamp": "service-A:1", "team": "Saved team",
            "provisioningState": "unknown", "accountRecoveryProtocol": 1]
        let oldPeer = V3AuthStore()
        bridge.snapshot.removeValue(forKey: "accountRecoveryProtocol")
        let oldPeerRead = await oldPeer.reconcile(force: true)
        precondition(oldPeerRead && Button.render(auth: oldPeer).isEmpty,
            "old peer must never receive a new recovery payload")
        bridge.snapshot["accountRecoveryProtocol"] = 1
        let store = V3AuthStore()
        let first = await store.reconcile(force: true)
        precondition(first && store.isSignedIn && store.hasProvisioningProblem)
        precondition(store.state == "authenticatedProvisioningIncomplete")
        precondition(Button.render(auth: store).count == 1)
        // Local Finish Later never erases the authoritative account warning.
        store.finishProvisioningLater()
        precondition(store.provisioningFinishedLater && store.hasProvisioningProblem)
        precondition(Button.render(auth: store).count == 1)
        let reloaded = await store.reconcile(force: true)
        precondition(reloaded && store.hasProvisioningProblem && !store.provisioningFinishedLater)
        // Real UI closure -> real store start -> real request producer.
        let button = Button.render(auth: store)[0]
        precondition(!button.isDisabled)
        button.action(); button.action()
        for _ in 0..<100 where bridge.starts.isEmpty { await Task.yield() }
        precondition(bridge.starts.count == 1, "double tap dispatched two sign-ins")
        precondition(bridge.starts[0]["reauthenticateProvisioning"] as? Bool == true)
        precondition(store.isSignedIn, "reauthentication erased the saved signed-in fact")
        // Server Finish Later is a terminal reply, distinct from local dismissal.
        try await Task.sleep(nanoseconds: 1_200_000_000)
        precondition(store.hasProvisioningProblem && !store.provisioningFinishedLater)
        let afterTerminal = await store.reconcile(force: true)
        precondition(afterTerminal && Button.render(auth: store).count == 1)
        let fresh = V3AuthStore()
        let reopened = await fresh.reconcile(force: true)
        precondition(reopened && fresh.hasProvisioningProblem && Button.render(auth: fresh).count == 1)
        // Another owner and a durable recovery hold each remove the live action.
        bridge.snapshot["authenticationActive"] = true
        bridge.snapshot["authenticationSessionID"] = UUID().uuidString
        bridge.snapshot["provisioningReauthenticationAvailable"] = false
        let other = await fresh.reconcile(force: true)
        precondition(other && Button.render(auth: fresh).isEmpty)
        bridge.snapshot["authenticationActive"] = false
        bridge.snapshot.removeValue(forKey: "authenticationSessionID")
        bridge.snapshot["provisioningRecoveryRequiresReconciliation"] = true
        let blocked = await fresh.reconcile(force: true)
        precondition(blocked && !fresh.canBegin && Button.render(auth: fresh).isEmpty)
        // Account change/new identity does not inherit the old dismissal.
        bridge.snapshot["provisioningRecoveryRequiresReconciliation"] = false
        bridge.snapshot["provisioningReauthenticationAvailable"] = true
        bridge.snapshot["identityStamp"] = "service-A:2"
        let changed = await fresh.reconcile(force: true)
        precondition(changed && fresh.hasProvisioningProblem && Button.render(auth: fresh).count == 1)
        print("V3_AUTH_RECOVERY_STORE_PASS")
    }
}
