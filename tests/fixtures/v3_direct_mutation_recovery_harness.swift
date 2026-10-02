import Foundation

extension Bundle {
    var altstoreAppGroup: String? { nil }
}

enum V3BackendCommands {
    static let boolSettings: Set<String> = ["isCellularRefreshEnabled"]
    static let intSettings: Set<String> = ["remotePairingPortOverride"]
    static let stringSettings: Set<String> = ["textInputSideJITServerurl"]
}

@main
struct DirectMutationRecoveryHarness {
    enum FixtureRunError: Error { case failedAfterDispatch }
    static let requestID = "10000000-0000-4000-8000-000000000001"
    static let oldInstance = "20000000-0000-4000-8000-000000000002"
    static let newInstance = "30000000-0000-4000-8000-000000000003"
    static let nextRequestID = "B0000000-0000-4000-8000-00000000000B"
    static let secretURL = "https://user:pass@example.invalid/repo.json?token=opaque"

    @MainActor
    static func main() async throws {
        guard CommandLine.arguments.count == 3 else { fatalError("expected seed|verify and root") }
        let mode = CommandLine.arguments[1]
        let root = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
        switch mode {
        case "seed": try await seed(root: root)
        case "verify": try verifyColdRelaunch(root: root)
        default: fatalError("unknown mode")
        }
    }

    @MainActor
    private static func seed(root: URL) async throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try testV1OperationRecord(root: root.appendingPathComponent("operation"))
        try testPreparedCancellationAndSingleSlot(root: root.appendingPathComponent("prepared"))
        try await testDirectWriteAhead(root: root)
        try testCanonicalRecoveryRequestIDs(root: root.appendingPathComponent("canonical-ids"))
        try await testCancellationAfterTerminalPersisted(root: root.appendingPathComponent("cancel-after-terminal"))
        try testSettingsStringPrivacy(root: root.appendingPathComponent("settings-string"))
        try testPrivacyForAccountImport(root: root.appendingPathComponent("account"))
        try testUnknownV2FailsClosed(root: root.appendingPathComponent("corrupt"))
        try testWireContract()
        print("V3_DIRECT_MUTATION_SEEDED")
    }

    private static func verifyColdRelaunch(root: URL) throws {
        let direct = try V3OperationRecoveryJournal.direct(containerRoot: root)
        precondition(direct?.requestID == requestID && direct?.phase == .dispatched)
        let unknown = try V3OperationRecoveryJournal.markDirectUnknownIfOwnerLost(
            requestID: requestID, currentServiceInstanceID: newInstance, containerRoot: root)
        precondition(unknown?.phase == .unknown, "dispatched work becomes unknown under a new service instance")
        let nextRequest: [String: Any] = ["id": nextRequestID, "operation": "settingsSet", "target": "",
            "payload": ["key": "isCellularRefreshEnabled", "type": "bool", "bool": true]]
        try expect(!(try V3OperationRecoveryJournal.reserveDirect(request: nextRequest,
            requestID: nextRequestID, serviceInstanceID: newInstance, containerRoot: root)),
            "cold-relaunch hold rejects a new eligible mutation before dispatch")
        var rejected: [String: Any] = ["id": nextRequestID, "version": 1, "error": "busy"]
        try expect(V3DirectMutationPreDispatchReplyPolicy.annotate(request: nextRequest,
            heldRequestID: requestID, response: &rejected))
        try expect(rejected["operationNotDispatched"] as? Bool == true &&
            rejected["id"] as? String == nextRequestID,
            "busy rejection is correlated and proves the new request did not dispatch")
        try expect(V3DirectMutationPreDispatchReplyPolicy.mayClaimInvalidRequestNotDispatched(
            operation: "settingsSet", requestID: nextRequestID, identifierCollision: false,
            heldRequestID: requestID, journalReadable: true))
        try expect(!V3DirectMutationPreDispatchReplyPolicy.mayClaimInvalidRequestNotDispatched(
            operation: "settingsSet", requestID: requestID, identifierCollision: false,
            heldRequestID: requestID, journalReadable: true))
        try expect(!V3DirectMutationPreDispatchReplyPolicy.mayClaimInvalidRequestNotDispatched(
            operation: "settingsSet", requestID: nextRequestID, identifierCollision: false,
            heldRequestID: nil, journalReadable: false))
        var originalReplay: [String: Any] = ["id": requestID, "version": 1, "error": "busy"]
        var originalRequest = nextRequest
        originalRequest["id"] = requestID
        try expect(!V3DirectMutationPreDispatchReplyPolicy.annotate(request: originalRequest,
            heldRequestID: requestID, response: &originalReplay),
            "the unresolved original cannot be mislabeled not-dispatched")
        try expect(originalReplay["operationNotDispatched"] == nil)
        try expect(!(try V3OperationRecoveryJournal.reconcileDirect(requestID: requestID,
            allowUnknownDeviceCheck: false, containerRoot: root)))
        try expect(try V3OperationRecoveryJournal.settleDirect(requestID: requestID,
            terminalOutcome: "remoteCreatedLocalStorageUnverified", containerRoot: root),
            "the exact late callback may settle a recovered unknown record")
        let terminal = try V3OperationRecoveryJournal.direct(containerRoot: root)
        precondition(terminal?.phase == .terminal &&
            terminal?.terminalOutcome == "remoteCreatedLocalStorageUnverified")
        try expect(!(try V3OperationRecoveryJournal.reconcileDirect(requestID: newInstance,
            allowUnknownDeviceCheck: true, containerRoot: root)), "wrong request IDs cannot clear the hold")
        try expect(try V3OperationRecoveryJournal.reconcileDirect(requestID: requestID,
            allowUnknownDeviceCheck: false, containerRoot: root), "host can acknowledge exact terminal result")
        guard case nil = try V3OperationRecoveryJournal.currentState(containerRoot: root) else {
            fatalError("terminal acknowledgement must remove the single-slot record")
        }
        let nextRequestAfterAcknowledgement = "A0000000-0000-4000-8000-00000000000A"
        let next = ["operation": "settingsSet", "target": "", "payload": [
            "key": "isCellularRefreshEnabled", "type": "bool", "bool": false
        ]] as [String: Any]
        try expect(V3OperationRecoveryJournal.reserveDirect(request: next,
            requestID: nextRequestAfterAcknowledgement, serviceInstanceID: newInstance, containerRoot: root))
        try expect(V3OperationRecoveryJournal.clearPreparedDirectAfterNotDispatched(
            requestID: nextRequestAfterAcknowledgement, containerRoot: root))
        let cancelledRoot = root.appendingPathComponent("cancel-after-terminal")
        let cancelledTerminal = try V3OperationRecoveryJournal.direct(containerRoot: cancelledRoot)
        precondition(cancelledTerminal?.requestID == nextRequestID &&
                     cancelledTerminal?.phase == .terminal &&
                     cancelledTerminal?.terminalOutcome == "completed",
                     "terminal state remains durable after post-run cancellation and relaunch")
        try expect(try V3OperationRecoveryJournal.reconcileDirect(requestID: nextRequestID,
            allowUnknownDeviceCheck: false, containerRoot: cancelledRoot),
            "the exact terminal request can be acknowledged after relaunch")
        print("V3_DIRECT_MUTATION_RELAUNCH_PASS")
    }

    private static func testV1OperationRecord(root: URL) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let session = "40000000-0000-4000-8000-000000000004"
        try expect(try V3OperationRecoveryJournal.reserve(sessionID: session, kind: "delete",
            containerRoot: root))
        let stored = try PropertyListSerialization.propertyList(
            from: Data(contentsOf: recordURL(root)), format: nil) as! [String: Any]
        precondition(stored["version"] as? Int == 1)
        precondition(Set(stored.keys) == Set(["version", "session", "kind", "phase"]))
        guard case .operation(let decoded)? = try V3OperationRecoveryJournal.currentState(containerRoot: root) else {
            fatalError("v1 operation record must decode as operation")
        }
        precondition(decoded.sessionID == session && decoded.kind == "delete" && decoded.phase == .prepared)
        try expect(try V3OperationRecoveryJournal.beginDispatch(sessionID: session, kind: "delete",
            containerRoot: root))
        try expect(try V3OperationRecoveryJournal.settle(sessionID: session, replySessionID: session,
            state: "completed", backendSettled: true, containerRoot: root))

        let refresh = "50000000-0000-4000-8000-000000000005"
        try expect(try V3OperationRecoveryJournal.reserve(sessionID: refresh, kind: "refreshAll",
            containerRoot: root))
        try expect(try V3OperationRecoveryJournal.beginDispatch(sessionID: refresh, kind: "refreshAll",
            containerRoot: root))
        try expect(try V3OperationRecoveryJournal.settleRefreshAdmission(runID: refresh,
            terminalState: "completed", terminalConfirmed: true, containerRoot: root))
    }

    private static func testPreparedCancellationAndSingleSlot(root: URL) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let request = ["operation": "settingsSet", "target": "", "payload": [
            "key": "isCellularRefreshEnabled", "type": "bool", "bool": true
        ]] as [String: Any]
        precondition(V3DirectMutationRecoveryRecord.isEligible(request: request))
        try expect(try V3DirectMutationRecoveryLifecycle.reserve(request: request,
            requestID: requestID, serviceInstanceID: oldInstance,
            teamIdentifier: nil, identityStamp: nil, containerRoot: root))
        try expect(!V3DirectMutationRecoveryLifecycle.clearPreparedAfterFailure(
            requestID: newInstance, containerRoot: root))
        try expect(V3DirectMutationRecoveryLifecycle.clearPreparedAfterFailure(
            requestID: requestID, containerRoot: root))
        guard case nil = try V3OperationRecoveryJournal.currentState(containerRoot: root) else {
            fatalError("prepared cancel clears only the exact request before dispatch")
        }

        try expect(try V3DirectMutationRecoveryLifecycle.reserve(request: request,
            requestID: requestID, serviceInstanceID: oldInstance,
            teamIdentifier: nil, identityStamp: nil, containerRoot: root))
        do {
            _ = try V3OperationRecoveryJournal.reserve(sessionID: "60000000-0000-4000-8000-000000000006",
                kind: "delete", containerRoot: root)
            fatalError("one-slot lease admitted conflicting operation recovery")
        } catch let failure as V3RecoveryStorageFailure {
            try expect(failure.kind == .incompatibleRecord && !failure.clearEligible)
        }
        try expect(try V3OperationRecoveryJournal.direct(containerRoot: root)?.requestID == requestID)
    }

    private static func testCanonicalRecoveryRequestIDs(root: URL) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let request = ["operation": "settingsSet", "target": "", "payload": [
            "key": "isCellularRefreshEnabled", "type": "bool", "bool": true
        ]] as [String: Any]
        let lowercaseID = "c0000000-0000-4000-8000-00000000000c"
        try expect(!(try V3OperationRecoveryJournal.reserveDirect(request: request,
            requestID: lowercaseID, serviceInstanceID: oldInstance, containerRoot: root)),
            "direct records require the canonical uppercase UUID representation")
        guard case nil = try V3OperationRecoveryJournal.currentState(containerRoot: root) else {
            fatalError("a noncanonical UUID must not reserve the journal")
        }
        let canonicalID = lowercaseID.uppercased()
        try expect(try V3OperationRecoveryJournal.reserveDirect(request: request,
            requestID: canonicalID, serviceInstanceID: oldInstance, containerRoot: root),
            "the canonical UUID can reserve the same journal after the rejected spelling")
        try expect(try V3OperationRecoveryJournal.clearPreparedDirectAfterNotDispatched(
            requestID: canonicalID, containerRoot: root))
    }

    @MainActor
    private static func testDirectWriteAhead(root: URL) async throws {
        let request = ["operation": "sourceAddConfirmed", "target": secretURL] as [String: Any]
        try expect(try V3DirectMutationRecoveryLifecycle.reserve(request: request,
            requestID: requestID, serviceInstanceID: oldInstance,
            teamIdentifier: nil, identityStamp: nil, containerRoot: root))
        let plist = try PropertyListSerialization.propertyList(
            from: Data(contentsOf: recordURL(root)), format: nil) as! [String: Any]
        precondition(plist["version"] as? Int == 2 && plist["recordType"] as? String == "directMutation")
        precondition(plist["requestID"] as? String == requestID && plist["phase"] as? String == "prepared")
        let persisted = try PropertyListSerialization.propertyList(
            from: Data(contentsOf: recordURL(root)), format: nil)
        let persistedXML = try PropertyListSerialization.data(fromPropertyList: persisted,
            format: .xml, options: 0)
        let serialized = String(data: persistedXML, encoding: .utf8) ?? ""
        precondition(!serialized.contains("user:pass") && !serialized.contains("opaque") && !serialized.contains(secretURL),
            "payload and source URL must never be persisted")
        do {
            _ = try await V3DirectMutationRecoveryLifecycle.dispatchAndSettle(
                requestID: requestID, operation: "sourceAddConfirmed",
                serviceInstanceID: oldInstance, containerRoot: root) {
                    throw FixtureRunError.failedAfterDispatch
                }
            fatalError("the fixture operation should fail after dispatch")
        } catch FixtureRunError.failedAfterDispatch { }
        try expect(!V3DirectMutationRecoveryLifecycle.clearPreparedAfterFailure(
            requestID: requestID, containerRoot: root),
            "post-dispatch failure retains the ambiguous record")
        try expect(try V3OperationRecoveryJournal.direct(containerRoot: root)?.phase == .unknown,
            "run failure becomes reconcilable in the same live service")
        try expect(try V3OperationRecoveryJournal.reconcileDirect(requestID: requestID,
            allowUnknownDeviceCheck: true, containerRoot: root),
            "explicit user check clears only the matching same-process unknown")

        let nextRequest = ["operation": "settingsSet", "target": "", "payload": [
            "key": "isCellularRefreshEnabled", "type": "bool", "bool": false
        ]] as [String: Any]
        let nextID = "C0000000-0000-4000-8000-00000000000C"
        try expect(try V3DirectMutationRecoveryLifecycle.reserve(request: nextRequest,
            requestID: nextID, serviceInstanceID: oldInstance,
            teamIdentifier: nil, identityStamp: nil, containerRoot: root),
            "a write is admitted after explicit same-process reconciliation")
        try expect(V3DirectMutationRecoveryLifecycle.clearPreparedAfterFailure(
            requestID: nextID, containerRoot: root))

        // Leave a dispatched record for the second process invocation, which
        // verifies cold-service relaunch behavior independently of run failure.
        try expect(try V3DirectMutationRecoveryLifecycle.reserve(request: request,
            requestID: requestID, serviceInstanceID: oldInstance,
            teamIdentifier: nil, identityStamp: nil, containerRoot: root))
        try expect(try V3OperationRecoveryJournal.beginDirectDispatch(requestID: requestID,
            serviceInstanceID: oldInstance, containerRoot: root))
    }

    @MainActor
    private static func testCancellationAfterTerminalPersisted(root: URL) async throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let request = ["operation": "sourceAddConfirmed", "target": secretURL]
        try expect(try V3DirectMutationRecoveryLifecycle.reserve(request: request,
            requestID: nextRequestID, serviceInstanceID: oldInstance,
            teamIdentifier: nil, identityStamp: nil, containerRoot: root))

        let task = Task { () async throws -> Void in
            guard let result = try await V3DirectMutationRecoveryLifecycle.dispatchAndSettle(
                requestID: nextRequestID, operation: "sourceAddConfirmed",
                serviceInstanceID: oldInstance, containerRoot: root, run: {
                    // Cancellation arrives after the remote operation returned.
                    withUnsafeCurrentTask { $0?.cancel() }
                    return [:]
                }) else { fatalError("dispatch should be accepted") }
            precondition(result.isEmpty)
            try Task.checkCancellation()
        }
        do {
            try await task.value
            fatalError("post-run cancellation must be observed after terminal persistence")
        } catch is CancellationError { }

        try expect(!V3DirectMutationRecoveryLifecycle.clearPreparedAfterFailure(
            requestID: nextRequestID, containerRoot: root),
            "post-run cancellation must not erase an already-terminal record")
        let terminal = try V3OperationRecoveryJournal.direct(containerRoot: root)
        precondition(terminal?.phase == .terminal && terminal?.terminalOutcome == "completed",
                     "terminal state is durable before cancellation is returned")
    }

    private static func testPrivacyForAccountImport(root: URL) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let token = "70000000-0000-4000-8000-000000000007"
        let secret = "80000000-0000-4000-8000-000000000008"
        let request = ["operation": "accountImport", "target": token,
            "payload": ["answer": ["value": secret]]] as [String: Any]
        precondition(V3DirectMutationRecoveryRecord.isEligible(request: request))
        try expect(try V3OperationRecoveryJournal.reserveDirect(request: request,
            requestID: requestID, serviceInstanceID: oldInstance, containerRoot: root))
        let persisted = try PropertyListSerialization.propertyList(
            from: Data(contentsOf: recordURL(root)), format: nil)
        let persistedXML = try PropertyListSerialization.data(fromPropertyList: persisted,
            format: .xml, options: 0)
        let serialized = String(data: persistedXML, encoding: .utf8) ?? ""
        precondition(!serialized.contains(token) && !serialized.contains(secret),
            "account-import file and password tokens must never be persisted")
        try expect(try V3OperationRecoveryJournal.clearPreparedDirectAfterNotDispatched(
            requestID: requestID, containerRoot: root))
    }

    private static func testSettingsStringPrivacy(root: URL) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let value = "https://user:token@example.invalid/private"
        let request = ["operation": "settingsSet", "target": "", "payload": [
            "key": "textInputSideJITServerurl", "type": "string", "string": value
        ]] as [String: Any]
        try expect(try V3OperationRecoveryJournal.reserveDirect(request: request,
            requestID: requestID, serviceInstanceID: oldInstance, containerRoot: root))
        let plist = try PropertyListSerialization.propertyList(
            from: Data(contentsOf: recordURL(root)), format: nil) as! [String: Any]
        try expect(plist["settingsKey"] as? String == "textInputSideJITServerurl")
        try expect(plist["settingsType"] as? String == "string")
        try expect(plist["settingsValueDigest"] == nil)
        let persistedXML = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try expect(!(String(data: persistedXML, encoding: .utf8) ?? "").contains(value))
        try expect(try V3OperationRecoveryJournal.clearPreparedDirectAfterNotDispatched(
            requestID: requestID, containerRoot: root))
    }

    private static func testUnknownV2FailsClosed(root: URL) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        var record: [String: Any] = ["version": 2, "recordType": "directMutation",
            "requestID": requestID, "operation": "certCreate", "phase": "dispatched",
            "serviceInstanceID": oldInstance]
        record["unrecognizedPayload"] = "must be rejected"
        let data = try PropertyListSerialization.data(fromPropertyList: record, format: .binary, options: 0)
        let journalURL = recordURL(root)
        try FileManager.default.createDirectory(at: journalURL.deletingLastPathComponent(),
            withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try data.write(to: journalURL, options: .atomic)
        do {
            _ = try V3OperationRecoveryJournal.currentState(containerRoot: root)
            fatalError("unknown v2 keys must be treated as unreadable")
        } catch let failure as V3RecoveryStorageFailure {
            try expect(failure.kind == .incompatibleRecord && failure.clearEligible)
        }
        try expect(!(try V3OperationRecoveryJournal.discardUnreadableAfterDeviceCheck(
            userConfirmed: false, containerRoot: root)))
        try expect(try V3OperationRecoveryJournal.discardUnreadableAfterDeviceCheck(
            userConfirmed: true, containerRoot: root))
    }

    private static func testWireContract() throws {
        let id = "90000000-0000-4000-8000-000000000009"
        let deadline = Date().addingTimeInterval(30)
        let inspect: [String: Any] = ["version": 1, "id": UUID().uuidString,
            "operation": "directRecoveryInspect", "target": id, "deadline": deadline]
        precondition(V3WireContract.encodeRequest(inspect) != nil)
        let reconcile: [String: Any] = ["version": 1, "id": UUID().uuidString,
            "operation": "directRecoveryReconcile", "target": id, "deadline": deadline,
            "payload": ["ackTerminal": true]]
        precondition(V3WireContract.encodeRequest(reconcile) != nil)
        var malformed = reconcile
        malformed["payload"] = ["userConfirmed": false]
        precondition(V3WireContract.encodeRequest(malformed) == nil)
        malformed["payload"] = ["ackTerminal": true, "userConfirmed": true]
        precondition(V3WireContract.encodeRequest(malformed) == nil)
        var userResolution = reconcile
        userResolution["payload"] = ["userConfirmed": true]
        precondition(V3WireContract.encodeRequest(userResolution) != nil)
        precondition(!V3RequestReplayPolicy.mayClaimNotDispatched(
            operation: "sourceAddConfirmed", identifierCollision: false),
            "the generic replay policy does not infer direct-mutation dispatch state")
        precondition(V3DirectMutationPreDispatchReplyPolicy.mayClaimInvalidRequestNotDispatched(
            operation: "sourceAddConfirmed", requestID: nextRequestID,
            identifierCollision: false, heldRequestID: nil, journalReadable: true),
            "direct mutations use the journal-aware pre-dispatch policy")
        precondition(!V3RequestReplayPolicy.mayClaimNotDispatched(
            operation: "sourceAddConfirmed", identifierCollision: true))
        print("V3_DIRECT_MUTATION_WIRE_PASS")
    }

    private static func recordURL(_ root: URL) -> URL {
        ["Library", "Application Support", "LiveContainer", "operation-recovery.plist"]
            .reduce(root.standardizedFileURL) { $0.appendingPathComponent($1) }
    }

    private static func expect(_ condition: Bool,
                               _ message: String = "expectation failed") throws {
        precondition(condition, message)
    }
}
