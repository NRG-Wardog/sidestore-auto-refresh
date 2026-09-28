import Foundation

@main
struct MalformedStructuredFailureHarness {
    static func main() throws {
        let id = UUID().uuidString
        func encoded(_ reply: [String: Any]) throws -> Data {
            try PropertyListSerialization.data(fromPropertyList: reply, format: .binary, options: 0)
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
        print("V3_MALFORMED_STRUCTURED_FAILURE_PASS")
    }
}
