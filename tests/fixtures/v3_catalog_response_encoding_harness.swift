import Foundation

// V3_CATALOG_RESPONSE_ENCODING_HARNESS_V1
// Executes the REAL wire contract and the REAL plist-safe dictionary builder
// against Foundation's actual PropertyListSerialization.
//
// This exists because the physical failure was not a classification problem: a
// catalog row placed `app.installedApp?.version` (a `String?`) straight into a
// `[String: Any]`. That boxes `Optional<String>.none` into `Any`, which
// PropertyListSerialization cannot encode, so the whole catalog response failed
// to serialize even though the Core Data read had succeeded. Asserting that the
// key "exists in the source text" would not have caught it, so the encode and
// decode are actually performed here.

@main
struct CatalogResponseEncodingHarness {
    static func main() {
        // A minimal stand-in for a decoded StoreApp, mirroring only the fields
        // the catalog row reads and their real optionality.
        struct Row {
            let identifier: String
            let name: String
            let version: String
            let developer: String
            let description: String
            let iconURL: String
            let downloadURL: String
            let canInstall: Bool
            let installedID: String
            let installedVersion: String?
        }

        func makeRow(identifier: String, installedVersion: String?) -> [String: Any] {
            let row = Row(identifier: identifier, name: "App \(identifier)",
                           version: "1.0", developer: "Dev", description: "Desc",
                           iconURL: "https://example.invalid/icon.png",
                           downloadURL: "https://example.invalid/app.ipa",
                           canInstall: true, installedID: "", installedVersion: installedVersion)
            // The exact construction the service uses.
            return V3WireContract.V3PropertyListValue.dictionary([
                "identifier": row.identifier,
                "bundleID": "com.example.\(row.identifier)",
                "name": row.name,
                "version": row.version,
                "developer": row.developer,
                "description": row.description,
                "iconURL": row.iconURL,
                "downloadURL": row.downloadURL,
                "canInstall": row.canInstall,
                "installedID": row.installedID,
                "installedVersion": row.installedVersion
            ])
        }

        // 1. An app that is NOT installed: installedVersion must be absent, and
        //    the response must still serialize.
        let notInstalled = makeRow(identifier: "notinstalled", installedVersion: nil)
        precondition(notInstalled["installedVersion"] == nil,
                     "an absent optional must be omitted, not represented as a placeholder")

        // 2. An app that IS installed: installedVersion is present.
        let installed = makeRow(identifier: "installed", installedVersion: "2.1")
        precondition(installed["installedVersion"] as? String == "2.1")

        // 3. A mixed catalog, exactly the shape that broke on device.
        let catalog: [String: Any] = ["apps": [notInstalled, installed], "nextCursor": -1]

        // The regression itself: the OLD construction, with the Optional boxed
        // into Any, must fail to serialize. If this ever starts succeeding, the
        // premise of the fix has changed and must be re-examined.
        func serializes(_ value: [String: Any]) -> Bool {
            (try? PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)) != nil
        }
        let boxed: [String: Any] = ["identifier": "x", "installedVersion": Optional<String>.none as Any]
        precondition(serializes(boxed) == false,
                     "a boxed Optional.none unexpectedly serialized; the P0 premise changed")

        // The fix: the real catalog response encodes and round-trips.
        guard let encoded = try? PropertyListSerialization.data(fromPropertyList: catalog,
                                                                 format: .binary, options: 0) else {
            preconditionFailure("the catalog response must serialize")
        }
        guard let decoded = try? PropertyListSerialization.propertyList(from: encoded, format: nil)
                as? [String: Any],
              let apps = decoded["apps"] as? [[String: Any]],
              apps.count == 2 else {
            preconditionFailure("the catalog response must round-trip with both rows")
        }
        precondition(apps[0]["installedVersion"] == nil,
                     "the not-installed row must stay absent after a round-trip")
        precondition(apps[1]["installedVersion"] as? String == "2.1",
                     "the installed row must keep its version after a round-trip")
        precondition(decoded["nextCursor"] as? Int == -1)

        // An empty catalog must also be valid: a source with zero apps is a
        // success, not a failure.
        let empty: [String: Any] = ["apps": [[String: Any]](), "nextCursor": -1]
        precondition((try? PropertyListSerialization.data(fromPropertyList: empty,
                                                          format: .binary, options: 0)) != nil,
                     "an empty catalog must serialize")

        // V3_PROPERTY_LIST_VALUE_V1: unwrapping removes the Optional box rather
        // than stringifying it.
        let wrapped: String? = "value"
        precondition(V3WireContract.V3PropertyListValue.unwrapOptional(wrapped) as? String == "value")
        precondition(V3WireContract.V3PropertyListValue.unwrapOptional(Optional<String>.none) == nil)
        precondition(V3WireContract.V3PropertyListValue.unwrapOptional(nil) == nil)
        precondition(V3WireContract.V3PropertyListValue.unwrapOptional(42) as? Int == 42)

        // A present but unrepresentable value is preserved, so serialization
        // fails loudly instead of silently dropping data.
        //
        // V3_PLIST_LEAF_CONTRACT_V1: the leaf set is Foundation's, and URL is
        // not a leaf. This assertion previously required the opposite, which is
        // how a URL was licensed onto the wire. The cross-check against real
        // PropertyListSerialization for every leaf lives in
        // v3_response_classification_harness.swift.
        final class Opaque {}
        precondition(!V3WireContract.V3PropertyListValue.isEncodable(Opaque()))
        precondition(!V3WireContract.V3PropertyListValue.isEncodable(URL(string: "https://example.invalid")!))
        precondition(V3WireContract.V3PropertyListValue.isEncodable(URL(string: "https://example.invalid")!.absoluteString))
        precondition(V3WireContract.V3PropertyListValue.isEncodable("text"))
        precondition(V3WireContract.V3PropertyListValue.isEncodable(1))
        precondition(V3WireContract.V3PropertyListValue.isEncodable(true))
        precondition(V3WireContract.V3PropertyListValue.isEncodable(Date()))
        precondition(!V3WireContract.V3PropertyListValue.isEncodable(Optional<String>.none as Any))

        // The encoder must distinguish the two failure modes. This runs the REAL
        // shared encoder rather than a copy of it: a mirrored copy can keep
        // passing after the production token names or limit change.
        func classify(_ value: [String: Any]) -> String {
            let reply = V3ResponseEncoder.encode(value, operation: "catalog", limit: V3WireContract.responseLimit)
            guard let decoded = (try? PropertyListSerialization.propertyList(from: reply, format: nil))
                    as? [String: Any] else { return "undecodable" }
            if let token = decoded["error"] as? String { return token }
            if let ok = decoded["ok"] as? Bool, ok { return "ok" }
            return "unknown"
        }
        precondition(classify(["version": 1, "id": "u", "ok": true]) == "ok")
        precondition(classify(["version": 1, "id": "u", "bad": Opaque()]) == "responseEncodingFailed")
        // A genuinely oversized but valid payload is a different defect.
        let oversized = "x".padding(toLength: V3WireContract.responseLimit + 16, withPad: "x", startingAt: 0)
        precondition(classify(["version": 1, "id": "u", "blob": oversized]) == "responseTooLarge")
        // The real fallback must itself always serialize, and must carry the
        // correlation, the operation, and the classification inside the
        // structured envelope the host actually reads.
        let fallbackID = UUID().uuidString
        let fallbackReply = V3ResponseEncoder.fallback(
            id: fallbackID, operation: "catalog",
            token: V3ResponseClassifier.Token.encodingFailed, code: .invalidResponse,
            safeCause: V3ResponseClassifier.safeCause(for: V3ResponseClassifier.Token.encodingFailed))
        precondition(fallbackReply.count > 0, "the correlated fallback must always serialize")
        let fallbackDecoded = try! PropertyListSerialization.propertyList(
            from: fallbackReply, format: nil) as! [String: Any]
        precondition(fallbackDecoded["id"] as? String == fallbackID)
        let envelope = fallbackDecoded["failure"] as! [String: Any]
        let decodedFailure = CombinedFailure.decode(envelope, expectedID: fallbackID)!
        precondition(decodedFailure.safeCause == .responseEncodingFailed,
                     "the classification must survive inside the structured envelope")
        precondition(decodedFailure.correlationID == fallbackID)
        precondition(decodedFailure.retryable == false)
        var fractionalFailureVersion = envelope
        fractionalFailureVersion["version"] = 1.0
        precondition(CombinedFailure.decode(fractionalFailureVersion, expectedID: fallbackID) == nil,
                     "an integral-looking plist real is not a protocol version")
        var fractionalUnderlyingCode = envelope
        fractionalUnderlyingCode["underlyingCode"] = 7.0
        precondition(CombinedFailure.decode(fractionalUnderlyingCode, expectedID: fallbackID) == nil,
                     "an integral-looking plist real is not an underlying integer code")
        var boolUnderlyingCode = envelope
        boolUnderlyingCode["underlyingCode"] = true
        precondition(CombinedFailure.decode(boolUnderlyingCode, expectedID: fallbackID) == nil,
                     "a Boolean cannot be decoded as a native error code")

        let sourceFailure = CombinedFailure(operation: "source", stage: .source, code: .invalidResponse,
            id: UUID().uuidString, safeCause: .sourceInvalidManifest, sourceStep: .manifestParsing)
        let sourceRoundTripData = try! PropertyListSerialization.data(
            fromPropertyList: sourceFailure.wire, format: .binary, options: 0)
        let sourceRoundTrip = try! PropertyListSerialization.propertyList(
            from: sourceRoundTripData, format: nil) as! [String: Any]
        let decodedSourceFailure = CombinedFailure.decode(sourceRoundTrip,
            expectedID: sourceFailure.correlationID)
        precondition(decodedSourceFailure?.safeCause == .sourceInvalidManifest &&
                     decodedSourceFailure?.sourceStep == .manifestParsing,
                     "safe cause and source step must survive a plist round trip")

        // V3_CATALOG_ROW_POLICY_V1: duplicates are removed within a page and
        // across pages, first-seen order preserved.
        func row(_ id: String) -> [String: Any] { ["identifier": id, "name": "n\(id)"] }
        let samePage = [[String: Any]]([row("a"), row("b"), row("a"), row("c"), row("b")])
        precondition(V3CatalogRowPolicy.isDisplayable(row("valid")) &&
                     !V3CatalogRowPolicy.isDisplayable(["identifier": "missing-name"]) &&
                     !V3CatalogRowPolicy.isDisplayable(["name": "missing-id"]),
                     "catalog row validation must match the display model without allocating a model per page")
        let dedupedSamePage = V3CatalogRowPolicy.dedupe(samePage)
        precondition(dedupedSamePage.count == 3, "a same-page duplicate survived")
        precondition(dedupedSamePage.compactMap { $0["identifier"] as? String } == ["a", "b", "c"],
                     "first-seen ordering was not preserved")
        var acrossPageAccumulator = V3CatalogRowsAccumulator()
        acrossPageAccumulator.append(samePage)
        acrossPageAccumulator.append([row("b"), row("d")])
        let acrossPages = acrossPageAccumulator.rows
        precondition(acrossPages.compactMap { $0["identifier"] as? String } == ["a", "b", "c", "d"])
        // A row with no usable identifier cannot be deduplicated, so it is
        // rejected rather than silently displayed.
        precondition(V3CatalogRowPolicy.dedupe([["name": "no id"]]).isEmpty)

        let cancellationID = UUID().uuidString
        func cancellationAck(_ reply: [String: Any]) -> Data {
            try! PropertyListSerialization.data(fromPropertyList: reply, format: .binary, options: 0)
        }
        precondition(V3RefreshAdmissionCancellationAckPolicy.accepts(cancellationAck([
            "version": 1, "id": cancellationID, "ok": true, "refreshAdmissionReleased": true
        ]), cancellationID: cancellationID))
        precondition(!V3RefreshAdmissionCancellationAckPolicy.accepts(cancellationAck([
            "version": 1, "id": cancellationID, "ok": false, "refreshAdmissionReleased": true
        ]), cancellationID: cancellationID))
        precondition(!V3RefreshAdmissionCancellationAckPolicy.accepts(cancellationAck([
            "version": 1.0, "id": cancellationID, "ok": true, "refreshAdmissionReleased": true
        ]), cancellationID: cancellationID))
        precondition(!V3RefreshAdmissionCancellationAckPolicy.accepts(cancellationAck([
            "version": 1, "id": UUID().uuidString, "ok": true, "refreshAdmissionReleased": true
        ]), cancellationID: cancellationID))
        precondition(!V3RefreshAdmissionCancellationAckPolicy.accepts(
            Data(repeating: 0, count: V3WireContract.responseLimit + 1), cancellationID: cancellationID))

        let rejectedStartID = UUID().uuidString
        let rejectedStartFailure = CombinedFailure(operation: "signIn", stage: .serviceReadiness,
            code: .busy, id: rejectedStartID, retryable: true)
        let rejectedStart = try! PropertyListSerialization.data(fromPropertyList: [
            "version": 1, "id": rejectedStartID, "error": "busy",
            "failure": rejectedStartFailure.wire, "operationNotDispatched": true
        ] as [String: Any], format: .binary, options: 0)
        precondition(V3NotDispatchedReplyPolicy.confirms(rejectedStart, requestID: rejectedStartID,
            maximumBytes: V3WireContract.responseLimit),
                     "a correlated typed service rejection may release a phantom host owner")
        let ambiguousStart = try! PropertyListSerialization.data(fromPropertyList: [
            "version": 1, "id": rejectedStartID, "error": "busy",
            "failure": rejectedStartFailure.wire
        ] as [String: Any], format: .binary, options: 0)
        precondition(!V3NotDispatchedReplyPolicy.confirms(ambiguousStart, requestID: rejectedStartID,
            maximumBytes: V3WireContract.responseLimit),
                     "an unmarked error cannot prove the auth operation never started")
        let contradictoryStart = try! PropertyListSerialization.data(fromPropertyList: [
            "version": 1, "id": rejectedStartID, "error": "busy",
            "failure": rejectedStartFailure.wire, "operationNotDispatched": true,
            "result": ["state": "working"]
        ] as [String: Any], format: .binary, options: 0)
        precondition(!V3NotDispatchedReplyPolicy.confirms(contradictoryStart,
            requestID: rejectedStartID, maximumBytes: V3WireContract.responseLimit),
            "a reply cannot both reject dispatch and contain a result")
        precondition(V3WireContract.strictInt(NSNumber(value: true)) == nil,
            "Boolean auth revisions must not pass as integer revisions")
        precondition(V3WireContract.strictInt(NSNumber(value: 7)) == 7,
            "valid integer auth revisions remain accepted")
        let malformedAuthBooleans = try! PropertyListSerialization.data(fromPropertyList: [
            "authenticated": NSNumber(value: 1), "resumable": NSNumber(value: true)
        ] as [String: Any], format: .binary, options: 0)
        let decodedAuthBooleans = try! PropertyListSerialization.propertyList(
            from: malformedAuthBooleans, format: nil) as! [String: Any]
        precondition(V3WireContract.authSnapshot(decodedAuthBooleans) == nil,
            "an auth snapshot with an integer Boolean is rejected as a whole")
        precondition(V3WireContract.strictBool(decodedAuthBooleans["authenticated"]) == nil,
            "integer one cannot authenticate a user through a nested reply")
        precondition(V3WireContract.strictBool(decodedAuthBooleans["resumable"]) == true,
            "a real serialized Boolean remains accepted")
        let validAuthBooleans = try! PropertyListSerialization.data(fromPropertyList: [
            "authenticated": true, "provisioningIncomplete": false,
            "provisioningRetryAvailable": false
        ] as [String: Any], format: .binary, options: 0)
        let decodedValidAuthBooleans = try! PropertyListSerialization.propertyList(
            from: validAuthBooleans, format: nil) as! [String: Any]
        precondition(V3WireContract.authSnapshot(decodedValidAuthBooleans) == [
            "authenticated": true, "provisioningIncomplete": false,
            "provisioningRetryAvailable": false
        ], "a valid structured auth snapshot decodes all booleans strictly")

        let readinessID = UUID().uuidString
        func readinessReply(_ value: [String: Any]) -> Data {
            try! PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
        }
        precondition(V3ServiceReadinessReply.decode(readinessReply([
            "version": 1, "id": readinessID, "ok": true, "result": ["busy": false]
        ]), requestID: readinessID) == .ready)
        if case .invalid = V3ServiceReadinessReply.decode(readinessReply([
            "version": 1, "id": readinessID, "ok": 1, "result": ["busy": false]
        ]), requestID: readinessID) {} else {
            preconditionFailure("NSNumber(1) is not a Boolean readiness acknowledgment")
        }
        if case .invalid = V3ServiceReadinessReply.decode(readinessReply([
            "version": 1.0, "id": readinessID, "ok": true, "result": ["busy": false]
        ]), requestID: readinessID) {} else {
            preconditionFailure("a plist real cannot impersonate the readiness protocol version")
        }
        if case .invalid = V3ServiceReadinessReply.decode(readinessReply([
            "version": 1, "id": readinessID, "ok": true
        ]), requestID: readinessID) {} else {
            preconditionFailure("readiness requires the typed snapshot result payload")
        }
        precondition(V3ServiceReadinessReply.decode(readinessReply([
            "version": 1, "id": readinessID, "error": "notReady"
        ]), requestID: readinessID) == .failed("notReady"))

        var replyBudget = V3MutationReplyCacheBudget()
        precondition(replyBudget.canReserve())
        let nearBudget = V3MutationReplyCacheBudget.maximumStoredBytes -
            V3MutationReplyCacheBudget.reservedControlBytes - V3WireContract.responseLimit
        precondition(replyBudget.record(nearBudget))
        precondition(replyBudget.canReserve(),
                     "ordinary replies must retain the reserved space for control replies")
        precondition(replyBudget.record(V3WireContract.responseLimit))
        precondition(!replyBudget.canReserve())
        precondition(replyBudget.canReserve(maximumResponseBytes: V3WireContract.responseLimit,
            preservingControlCapacity: false),
            "refresh release can use the capacity reserved for terminal control replies")
        let authControlCapacity = replyBudget.canReserve(
            maximumResponseBytes: V3WireContract.responseLimit, preservingControlCapacity: false)
        precondition(!V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: true, isAuthContinuation: true,
            responseCapacityAvailable: replyBudget.canReserve()),
            "the exhausted ordinary reserve blocks another normal mutation")
        precondition(V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: true, isAuthContinuation: true,
            responseCapacityAvailable: authControlCapacity),
            "an authRespond continuation can use reserved control capacity")
        precondition(V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: false, isAuthContinuation: false,
            responseCapacityAvailable: authControlCapacity),
            "an opAnswer continuation can use reserved control capacity")
        precondition(V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
            anotherMutationActive: false, authenticationActive: false, isAuthContinuation: false,
            responseCapacityAvailable: authControlCapacity, refreshActive: true, isRefreshRelease: true),
            "refreshAdmissionEnd can use reserved control capacity and release its active lease")
        precondition(replyBudget.record(V3WireContract.responseLimit, controlResponse: true))
        precondition(!replyBudget.canReserve(maximumResponseBytes: 1, preservingControlCapacity: false),
                     "reply-cache accounting must never exceed its byte ceiling")
        let ordinaryReplyLimit = V3MutationReplyCacheBudget.responseCountLimit(isControlResponse: false)
        let allReplyLimit = V3MutationReplyCacheBudget.responseCountLimit(isControlResponse: true)
        precondition(ordinaryReplyLimit == 382 && allReplyLimit == 512,
                     "ordinary replies preserve one maximum authentication lifecycle")
        precondition(allReplyLimit - ordinaryReplyLimit == V3MutationReplyCacheBudget.reservedControlReplies,
                     "the configured reply reserve remains executable and symmetric")
        for operation in ["refreshAdmissionEnd", "authBegin", "authRetryProvisioning", "authRespond",
                          "opStart", "opAnswer"] {
            precondition(V3MutationReplyCacheBudget.isControlReply(operation: operation),
                         "session continuation \(operation) must use the reserved reply capacity")
        }
        precondition(V3MutationReplyCacheBudget.canAdmit(operation: "authBegin",
            completedReplyCount: 382) &&
                     !V3MutationReplyCacheBudget.canAdmit(operation: "authBegin",
                        completedReplyCount: 383),
            "authBegin is not dispatched unless its begin, prompt, and provisioning-retry replies fit")
        precondition(V3MutationReplyCacheBudget.canAdmit(operation: "authRetryProvisioning",
            completedReplyCount: 447) &&
                     !V3MutationReplyCacheBudget.canAdmit(operation: "authRetryProvisioning",
                        completedReplyCount: 448),
            "a provisioning retry is not dispatched unless its prompt replies fit")
        precondition(V3MutationReplyCacheBudget.canAdmit(operation: "opStart",
            completedReplyCount: 447) &&
                     !V3MutationReplyCacheBudget.canAdmit(operation: "opStart",
                        completedReplyCount: 448),
            "an operation that may ask prompts retains enough replies for its bounded prompt sequence")
        precondition(!V3MutationReplyCacheBudget.isControlReply(operation: "sourceAddConfirmed"),
                     "ordinary source mutations cannot consume all continuation capacity")
        replyBudget.remove(V3WireContract.responseLimit)
        precondition(!replyBudget.canReserve(),
                     "ordinary requests still preserve reserved control capacity after release")
        precondition(replyBudget.canReserve(preservingControlCapacity: false))

        // V3_REFRESH_ADMISSION_WIRE_V1: reservation/release must cross the
        // actual plist contract as mutations with a run-scoped UUID.
        let refreshRunID = UUID().uuidString
        for operation in ["refreshAdmissionBegin", "refreshAdmissionEnd"] {
            let request: [String: Any] = ["version": 1, "id": UUID().uuidString,
                "operation": operation, "target": refreshRunID,
                "deadline": Date().addingTimeInterval(30)]
            let requestData = try! PropertyListSerialization.data(
                fromPropertyList: request, format: .binary, options: 0)
            guard let decodedRequest = V3WireContract.decodeRequest(requestData) else {
                preconditionFailure("refresh admission operation must be accepted by the wire contract")
            }
            precondition(decodedRequest["operation"] as? String == operation)
            precondition(decodedRequest["target"] as? String == refreshRunID)
            precondition(!V3WireContract.readOperations.contains(operation),
                         "refresh admission must acquire the mutation gate")
        }
        // Auth request deadlines bound only the XPC start RPC. The separate
        // sessionDeadline keeps a real credentials/2FA session alive after the
        // start reply returns.
        let authSessionID = UUID().uuidString
        let authRequest: [String: Any] = ["version": 1, "id": UUID().uuidString,
            "operation": "authBegin", "target": authSessionID,
            "deadline": Date().addingTimeInterval(30),
            "payload": ["session": authSessionID,
                        "sessionDeadline": Date().addingTimeInterval(V3WireContract.authSessionLifetime)]]
        let authRequestData = try! PropertyListSerialization.data(
            fromPropertyList: authRequest, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(authRequestData) != nil,
                     "an auth session may outlive its bounded start request")
        var missingAuthSessionDeadline = authRequest
        missingAuthSessionDeadline["payload"] = ["session": authSessionID]
        let missingAuthDeadlineData = try! PropertyListSerialization.data(
            fromPropertyList: missingAuthSessionDeadline, format: .binary, options: 0)
        precondition(V3WireContract.decodeRequest(missingAuthDeadlineData) == nil,
                     "auth start must carry its separate authoritative session deadline")

        let invalidRequestID = UUID().uuidString
        let invalidRequestData = try! PropertyListSerialization.data(fromPropertyList: [
            "id": invalidRequestID, "operation": "authBegin", "padding": "x"
        ] as [String: Any], format: .binary, options: 0)
        let invalidIdentity = V3WireContract.invalidRequestIdentity(from: invalidRequestData)
        precondition(invalidIdentity.id == invalidRequestID && invalidIdentity.operation == "authBegin")
        let oversizedInvalidRequest = try! PropertyListSerialization.data(fromPropertyList: [
            "id": invalidRequestID, "operation": "authBegin",
            "padding": String(repeating: "x", count: V3WireContract.requestLimit * 2)
        ] as [String: Any], format: .binary, options: 0)
        let oversizedIdentity = V3WireContract.invalidRequestIdentity(from: oversizedInvalidRequest)
        precondition(oversizedIdentity.id == nil && oversizedIdentity.operation == nil,
                     "an over-limit request is not reparsed for correlation")

        print("V3_CATALOG_RESPONSE_ENCODING_PASS")
    }
}
