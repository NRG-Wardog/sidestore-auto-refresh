import Foundation

@main
struct MalformedStructuredFailureHarness {
    static func main() throws {
        let id = UUID().uuidString
        func encoded(_ reply: [String: Any]) throws -> Data {
            try PropertyListSerialization.data(fromPropertyList: reply, format: .binary, options: 0)
        }

        let largestSigned = NSNumber(value: UInt64(Int.max))
        let unsignedOverflow = NSNumber(value: UInt64(Int.max) + 1)
        precondition(V3WireContract.strictInt(largestSigned) == Int.max &&
                     v3StrictPlistInteger(largestSigned) == Int.max,
            "all production decoders accept the largest representable signed integer")
        let rangeFailureWire: [String: Any] = ["version": 1, "operation": "refresh",
            "stage": "command", "code": "failed", "correlationID": id,
            "underlyingDomain": "redacted", "underlyingCode": largestSigned]
        let decodedRangeFailure = CombinedFailure.decode(rangeFailureWire, expectedID: id)
        precondition(decodedRangeFailure != nil && decodedRangeFailure?.underlyingDomain == "redacted" &&
                     decodedRangeFailure?.underlyingCode == 0,
            "decoding accepts an in-range integer and safely normalizes redacted/nonzero to redacted/0")
        var overflowFailureWire = rangeFailureWire
        overflowFailureWire["underlyingCode"] = unsignedOverflow
        precondition(V3WireContract.strictInt(unsignedOverflow) == nil &&
                     v3StrictPlistInteger(unsignedOverflow) == nil &&
                     CombinedFailure.decode(overflowFailureWire, expectedID: id) == nil,
            "unsigned integers that do not fit Int are rejected without truncation")
        if let overflowingReadinessData = try? encoded(["version": 1, "id": id, "ok": false,
            "failure": overflowFailureWire]) {
            precondition(V3ServiceReadinessReply.decode(overflowingReadinessData, requestID: id) == .invalid,
                "an oversized integer cannot wrap into a plausible readiness failure")
        }

        let malformedEnvelope: [String: Any] = [
            "version": 1, "operation": "refresh", "stage": "madeUpStage",
            "code": "busy", "correlationID": id, "underlyingDomain": "redacted",
            "underlyingCode": 0, "retryable": true
        ]
        let malformedReply: [String: Any] = [
            "version": 1, "id": id, "ok": false, "error": "busy",
            "failure": malformedEnvelope
        ]
        do {
            _ = try V3CatalogRequestContext.classifyReply(
                encoded(malformedReply), operation: "refresh", id: id)
            preconditionFailure("a present but malformed structured envelope must fail closed")
        } catch let failure as CombinedFailure {
            precondition(failure.code == .invalidResponse && failure.retryable == false &&
                         failure.safeCause == nil,
                "a malformed structured envelope cannot fall back to the retryable legacy busy token")
        }

        let typedFailure = CombinedFailure(operation: "refresh", stage: .signing,
            code: .failed, id: id, retryable: false, safeCause: .certificateUnavailable)
        precondition(V3ServiceReadinessReply.knownSafeCauseValues ==
                     Set(CombinedFailure.SafeCause.allCases.map(\.rawValue)) &&
                     V3ServiceReadinessReply.knownSourceStepValues ==
                     Set(CombinedFailure.SourceStep.allCases.map(\.rawValue)),
            "readiness vocabulary must cover every typed cause and source step")
        let typedReply: [String: Any] = ["version": 1, "id": id, "ok": false,
            "error": "busy", "failure": typedFailure.wire]
        do {
            _ = try V3CatalogRequestContext.classifyReply(
                encoded(typedReply), operation: "refresh", id: id)
            preconditionFailure("a typed failed reply must not be returned as success")
        } catch let failure as CombinedFailure {
            precondition(failure.stage == .signing && failure.code == .failed &&
                         failure.safeCause == .certificateUnavailable &&
                         failure.retryable == false,
                "a valid structured failure remains authoritative over the legacy token")
        }

        let readyLookingMalformed: [String: Any] = [
            "version": 1, "id": id, "ok": true,
            "failure": ["version": 1, "stage": "madeUpStage"],
            "result": ["ready": true]
        ]
        let malformedReadinessData = try encoded(readyLookingMalformed)
        let malformedReadiness = V3ServiceReadinessReply.decode(malformedReadinessData,
            requestID: id)
        precondition(malformedReadiness == .invalid,
            "a malformed structured failure cannot be ignored by readiness decoding")
        var unknownCauseFailure = typedFailure.wire
        unknownCauseFailure["safeCause"] = "futureUnrecognizedCause"
        let unknownCauseData = try encoded(["version": 1, "id": id, "ok": false,
            "failure": unknownCauseFailure])
        precondition(V3ServiceReadinessReply.decode(unknownCauseData, requestID: id) == .invalid,
            "a readiness decoder cannot silently erase an unknown typed cause")
        var unknownStepFailure = typedFailure.wire
        unknownStepFailure["sourceStep"] = "futureUnrecognizedStep"
        let unknownStepData = try encoded(["version": 1, "id": id, "ok": false,
            "failure": unknownStepFailure])
        precondition(V3ServiceReadinessReply.decode(unknownStepData, requestID: id) == .invalid,
            "a readiness decoder cannot silently erase an unknown typed step")
        let readyLookingLegacyError: [String: Any] = [
            "version": 1, "id": id, "ok": true, "error": "busy",
            "result": ["ready": true]
        ]
        let legacyReadinessData = try encoded(readyLookingLegacyError)
        let legacyReadiness = V3ServiceReadinessReply.decode(legacyReadinessData,
            requestID: id)
        precondition(legacyReadiness == .invalid,
            "an unstructured error cannot be accepted as a ready result")
        let readinessFailure: [String: Any] = [
            "version": 1, "id": id, "ok": false, "failure": typedFailure.wire
        ]
        if case .failed(let failure) = V3ServiceReadinessReply.decode(
            try encoded(readinessFailure), requestID: id) {
            precondition(failure.stage == "signing" && failure.safeCause == "certificateUnavailable")
        } else {
            preconditionFailure("a valid structured readiness failure remains authoritative without error token")
        }

        let persistenceFailure = CombinedFailure(operation: "install", stage: .persistence,
            code: .failed, id: id, retryable: false, safeCause: .operationPersistenceFailed)
        let persistenceReply: [String: Any] = ["version": 1, "id": id, "ok": false,
            "failure": persistenceFailure.wire]
        if case .failed(let failure) = V3ServiceReadinessReply.decode(
            try encoded(persistenceReply), requestID: id) {
            precondition(failure.operation == "install" && failure.stage == "persistence" &&
                         failure.safeCause == "operationPersistenceFailed" && failure.retryable == false,
                "a typed operation persistence failure must survive the readiness plist boundary")
        } else {
            preconditionFailure("the readiness decoder must preserve the typed operation persistence failure")
        }
        print("V3_MALFORMED_STRUCTURED_FAILURE_PASS")
    }
}
