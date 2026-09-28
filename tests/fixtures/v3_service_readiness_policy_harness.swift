@MainActor
final class ReadinessLatch {
    var ready = false
    var invalid = false
    var failed = false
}

@main
struct ServiceReadinessPolicyHarness {
    static func main() async {
        let requestID = UUID().uuidString
        let readyReply: [String: Any] = ["version": 1, "id": requestID,
            "ok": true, "result": ["ready": true]]
        let readyData = try! PropertyListSerialization.data(fromPropertyList: readyReply,
            format: .binary, options: 0)
        precondition(V3ServiceReadinessReply.decode(readyData, requestID: requestID) == .ready,
            "a minimal service-ready result is authoritative")
        let startingReply: [String: Any] = ["version": 1, "id": requestID,
            "ok": true, "result": ["ready": false]]
        let startingData = try! PropertyListSerialization.data(fromPropertyList: startingReply,
            format: .binary, options: 0)
        precondition(V3ServiceReadinessReply.decode(startingData, requestID: requestID) == .notReady,
            "a service still starting remains transient even after a successful reply")

        let transientRetryability = V3ServiceReadinessRetryPolicy.retryable(
            operation: "snapshot", stage: .serviceReadiness, code: .notReady, typedNotReady: true)
        precondition(transientRetryability == true,
            "only the early service-readiness snapshot notReady response is retryable")
        let startupFailure = CombinedFailure(operation: "snapshot", stage: .serviceReadiness,
            code: .notReady, id: requestID, retryable: transientRetryability)
        let startupReply: [String: Any] = ["version": 1, "id": requestID,
            "error": "notReady", "failure": startupFailure.wire]
        let startupData = try! PropertyListSerialization.data(fromPropertyList: startupReply,
            format: .binary, options: 0)
        precondition(V3ServiceReadinessReply.decode(startupData, requestID: requestID) == .notReady,
            "a real backend notReady envelope must be retried during startup")

        let unmarkedFailure = CombinedFailure(operation: "snapshot", stage: .serviceReadiness,
            code: .notReady, id: requestID)
        let unmarkedReply: [String: Any] = ["version": 1, "id": requestID,
            "error": "notReady", "failure": unmarkedFailure.wire]
        let unmarkedData = try! PropertyListSerialization.data(fromPropertyList: unmarkedReply,
            format: .binary, options: 0)
        if case .failed(let failure) = V3ServiceReadinessReply.decode(unmarkedData, requestID: requestID) {
            precondition(failure.retryable == nil,
                "an unmarked readiness failure must remain terminal rather than silently retry")
        } else { preconditionFailure("an unmarked readiness failure must remain terminal") }
        let misroutedFailure = CombinedFailure(operation: "authBegin", stage: .serviceReadiness,
            code: .notReady, id: requestID, retryable: true)
        let misroutedReply: [String: Any] = ["version": 1, "id": requestID,
            "error": "notReady", "failure": misroutedFailure.wire]
        let misroutedData = try! PropertyListSerialization.data(fromPropertyList: misroutedReply,
            format: .binary, options: 0)
        if case .failed(let failure) = V3ServiceReadinessReply.decode(misroutedData, requestID: requestID) {
            precondition(failure.operation == "authBegin",
                "a correlated notReady from the wrong operation must remain terminal")
        } else { preconditionFailure("a notReady response from a different operation must not be retried") }
        let scheduledRunID = UUID().uuidString
        let terminalReadiness = V3ServiceReadinessFailure(operation: "snapshot",
            stage: CombinedFailure.Stage.serviceReadiness.rawValue,
            code: CombinedFailure.Code.invalidResponse.rawValue,
            correlationID: requestID, underlyingDomain: "NSCocoaErrorDomain", underlyingCode: 4,
            safeCause: nil, sourceStep: nil, retryable: false)
        let scheduledFailure = terminalReadiness.combinedFailure(id: scheduledRunID, operation: "refresh")
        precondition(scheduledFailure.operation == "refresh" &&
                     scheduledFailure.correlationID == scheduledRunID &&
                     scheduledFailure.stage == .serviceReadiness &&
                     scheduledFailure.code == .invalidResponse &&
                     scheduledFailure.sourceStep == nil &&
                     scheduledFailure.retryable == false,
            "a terminal readiness cause retains its typed stage and code under the scheduler's runID")
        precondition(V3ServiceReadinessRetryPolicy.retryable(
            operation: "catalog", stage: .serviceReadiness, code: .notReady, typedNotReady: true) == nil,
            "notReady in another operation must not be relabeled as transient startup")
        precondition(V3ServiceReadinessRetryPolicy.retryable(
            operation: "snapshot", stage: .serviceReadiness, code: .notReady, typedNotReady: false) == nil,
            "an unrelated error that maps to notReady must not be retried as database startup")

        precondition(V3ServiceReadinessProbeState.resolve(ready: true, invalid: false,
            hasTerminalFailure: false, expired: true) == .ready,
            "a verified readiness reply received at the deadline wins over timeout")
        precondition(V3ServiceReadinessProbeState.resolve(ready: false, invalid: true,
            hasTerminalFailure: false, expired: true) == .invalid,
            "a malformed readiness reply stays a typed terminal failure")
        precondition(V3ServiceReadinessProbeState.resolve(ready: false, invalid: false,
            hasTerminalFailure: true, expired: true) == .failed)
        precondition(V3ServiceReadinessProbeState.resolve(ready: false, invalid: false,
            hasTerminalFailure: false, expired: true) == .timedOut)

        let latch = await ReadinessLatch()
        let queuedReply = Task { @MainActor in latch.ready = true }
        let boundaryResult = await V3ServiceReadinessProbeState.recheckAfterYield(
            ready: { latch.ready }, invalid: { latch.invalid }, hasTerminalFailure: { latch.failed })
        await queuedReply.value
        precondition(boundaryResult == .ready,
            "a readiness callback queued at the deadline must be applied before timeout wins")

        var schedule = V3ServiceReadinessBackoff()
        let expected: [TimeInterval] = [0.2, 0.4, 0.8, 1.0, 1.0]
        for value in expected {
            guard let actual = schedule.nextDelay(remaining: 30) else {
                preconditionFailure("readiness retry ended before its deadline")
            }
            precondition(abs(actual - value) < 0.000_001, "startup retry backoff did not grow to its cap")
        }

        var bounded = V3ServiceReadinessBackoff()
        var remaining: TimeInterval = 30
        var attempts = 0
        while let delay = bounded.nextDelay(remaining: remaining) {
            attempts += 1
            remaining = delay >= remaining ? 0 : remaining - delay
        }
        precondition(attempts <= 32, "startup readiness must not issue up to 150 snapshots in 30 seconds")

        var deadline = V3ServiceReadinessBackoff()
        let shortDelay = deadline.nextDelay(remaining: 0.05)!
        precondition(abs(shortDelay - 0.05) < 0.000_001)
        precondition(deadline.nextDelay(remaining: 0) == nil)
        print("V3_SERVICE_READINESS_POLICY_PASS")
    }
}
