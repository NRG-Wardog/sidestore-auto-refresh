import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

@main
struct ReleaseBehaviorHarness {
    static func main() throws {
        try operationRetryCancelAndLateResult()
        try backendMutationCancellationOrdering()
        try refreshTerminalResultWinsOnce()
        settingsRollbackKeepsNewerWrites()
        print("V3_RELEASE_BEHAVIOR_PASS")
    }

    private static func backendMutationCancellationOrdering() throws {
        var registry = V3OperationMutationRegistry()
        let old = UUID().uuidString
        let next = UUID().uuidString
        require(registry.begin(old) == .started, "old backend session did not start")
        require(registry.cancel(old) == .active, "active cancellation was not scoped to its session")
        require(registry.begin(next) == .busy, "new mutation started before old cancellation completed")
        require(registry.finish(old), "old cancellation completion did not release its own session")
        require(registry.begin(next) == .started, "new mutation did not start after cancellation completion")
        require(!registry.finish(old), "late old completion released the new backend mutation")
        require(registry.activeID == next, "late old completion changed the active session")
        require(registry.finish(next), "new backend session did not finish")

        let cancelBeforeStart = UUID().uuidString
        require(registry.cancel(cancelBeforeStart) == .recordedBeforeStart,
                "cancel-before-start was not recorded")
        require(registry.begin(cancelBeforeStart) == .cancelledBeforeStart,
                "a cancelled opStart later began a mutation")
        require(registry.activeID == nil, "cancelled-before-start session became active")
    }

    private static func operationRetryCancelAndLateResult() throws {
        var attempt = V3OperationAttemptState()
        let firstGeneration = attempt.begin()
        let firstSession = firstGeneration.uuidString
        require(attempt.bind(sessionID: firstSession, generation: firstGeneration), "first session did not bind")
        require(attempt.accept(state: "failed", generation: firstGeneration, sessionID: firstSession),
                "first failure was not accepted")

        // Retry invalidates the old generation before sending the awaited
        // session-scoped cancellation. Only after that acknowledgement can the
        // new attempt be started.
        let cancelledSession = attempt.supersede()
        require(cancelledSession == firstSession, "retry lost the old session identity")
        let transitionGeneration = attempt.generation
        require(!attempt.accept(state: "failed", generation: firstGeneration, sessionID: firstSession),
                "late old result changed the superseded attempt")

        let secondGeneration = attempt.begin()
        let secondSession = secondGeneration.uuidString
        require(secondGeneration != firstGeneration && secondSession != firstSession,
                "retry reused the old generation")
        require(attempt.bind(sessionID: secondSession, generation: secondGeneration),
                "new session did not start")
        require(attempt.accept(state: "working", generation: secondGeneration, sessionID: secondSession),
                "new session did not enter working")
        require(!attempt.accept(state: "failed", generation: firstGeneration, sessionID: firstSession),
                "late old failure mutated the new UI")
        require(attempt.matches(generation: secondGeneration, sessionID: secondSession),
                "new UI lost its session")

        // Cancel -> Retry and rapid repeated Retry taps share one transition
        // gate; one tap owns cancellation until the backend acknowledges it.
        require(attempt.beginTransition(), "cancel transition was rejected")
        require(!attempt.beginTransition(), "rapid repeat began a second transition")
        let cancelledAgain = attempt.supersede()
        require(cancelledAgain == secondSession, "cancel lost the current session")
        attempt.endTransition()
        require(attempt.beginTransition(), "Retry after acknowledged Cancel was blocked")
        let thirdGeneration = attempt.begin()
        require(thirdGeneration != secondGeneration, "Cancel -> Retry reused a generation")
        attempt.endTransition()
        require(attempt.bind(sessionID: thirdGeneration.uuidString, generation: thirdGeneration),
                "Cancel -> Retry did not start a new session")
        require(transitionGeneration != secondGeneration, "supersede did not advance generation")
    }

    private static func refreshTerminalResultWinsOnce() throws {
        let failure = NSError(domain: "RefreshFixture", code: 42)
        let failed = V3TerminalResponse()
        do {
            _ = try V3RefreshResultVerifier.verified(expectedBundleID: "app.one",
                results: ["app.one": Result<String, Error>.failure(failure)], bundleIdentifier: { $0 })
            try require(false, "refresh failure was accepted as success")
        } catch {}
        // Drive() writes the error it received from verified pipeline results.
        require(failed.setIfEmpty(["state": "failed"]), "failure terminal was not written")
        require(!failed.setIfEmpty(["state": "completed"]), "late success overwrote refresh failure")
        require(failed.value?["state"] as? String == "failed", "refresh failure did not remain terminal")

        let completed = V3TerminalResponse()
        let verified = try V3RefreshResultVerifier.verified(expectedBundleID: "app.one",
            results: ["app.one": Result<String, Error>.success("app.one")], bundleIdentifier: { $0 })
        require(verified == "app.one", "success result was not verified")
        require(completed.setIfEmpty(["state": "completed"]), "verified success terminal was not written")
        require(!completed.setIfEmpty(["state": "failed"]), "late failure overwrote refresh success")
        require(completed.value?["state"] as? String == "completed", "refresh success did not remain terminal")
    }

    private static func settingsRollbackKeepsNewerWrites() {
        var versions = V3SettingsWriteGeneration()
        var boolValue = false
        let boolA = versions.begin("bool"); boolValue = true
        let boolB = versions.begin("bool"); boolValue = false
        if versions.isCurrent(boolA, for: "bool") { boolValue = false }
        require(!boolValue, "stale Boolean failure rolled back a newer success")
        require(versions.isCurrent(boolB, for: "bool"), "newer Boolean write lost authority")

        var stringValue = "old"
        let stringA = versions.begin("string"); stringValue = "A"
        let stringB = versions.begin("string"); stringValue = "B"
        if versions.isCurrent(stringA, for: "string") { stringValue = "old" }
        require(stringValue == "B", "stale String failure rolled back a newer success")

        var intValue = 0
        let intA = versions.begin("int"); intValue = 20
        let intB = versions.begin("int"); intValue = 30
        if versions.isCurrent(intA, for: "int") { intValue = 0 }
        require(intValue == 30, "stale Int failure rolled back a newer success")
    }
}
