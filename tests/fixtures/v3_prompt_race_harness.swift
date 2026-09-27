@main
struct PromptRaceHarness {
    static func main() async throws {
        let center = V3PromptCenter()
        for _ in 0..<256 {
            let id = UUID().uuidString
            let waiter = Task.detached { try await center.park(promptID: id) }
            waiter.cancel()
            do {
                _ = try await waiter.value
                preconditionFailure("cancelled prompt returned an answer")
            } catch is CancellationError {}
            precondition(center.answer(promptID: id, answer: ["code": "duplicate"]) == .unavailable)
        }
        precondition(center.pendingCount == 0, "cancellation retained a prompt continuation")

        let prompt = UUID().uuidString
        let waiter = Task.detached { try await center.park(promptID: prompt) }
        var spins = 0
        while center.pendingCount == 0 && spins < 10_000 {
            spins += 1
            await Task.yield()
        }
        precondition(center.pendingCount == 1, "prompt continuation was not installed")
        precondition(center.answer(promptID: prompt, answer: ["action": "sms"]) == .accepted)
        precondition(center.answer(promptID: prompt, answer: ["action": "voice"]) == .alreadySettled,
                     "rapid duplicate answer was accepted")
        let pendingReply = V3PromptResponseStatePolicy.responsePending(.unavailable,
            acceptedPromptID: prompt, promptID: prompt, sessionID: "session",
            revision: 2, state: "awaitingPrompt", prompt: ["id": prompt])
        precondition(pendingReply?["responsePending"] as? Bool == true &&
                     pendingReply?["state"] as? String == "awaitingPrompt",
                     "a lost reply for an accepted response remains pending rather than becoming expired")
        precondition(V3PromptResponseStatePolicy.responsePending(.unavailable,
            acceptedPromptID: "prompt-a", promptID: "prompt-a", sessionID: "session",
            revision: 3, state: "awaitingPrompt", prompt: ["id": "prompt-b"]) == nil,
            "a duplicate for prompt A must not label prompt B as an A response that is still pending")
        precondition(V3PromptResponseStatePolicy.shouldReturnCurrentStateAfterAcceptedDuplicate(
            acceptedPromptID: "prompt-a", currentPromptID: "prompt-b",
            submittedPromptID: "prompt-a"),
            "a delayed duplicate for A must return the already-installed prompt B")
        let acceptedA = V3PromptResponseStatePolicy.recordAcceptedPrompt([], promptID: "prompt-a")
        let acceptedAB = V3PromptResponseStatePolicy.recordAcceptedPrompt(acceptedA, promptID: "prompt-b")
        precondition(V3PromptResponseStatePolicy.shouldReturnCurrentStateAfterAcceptedDuplicate(
            acceptedPromptID: nil, acceptedPromptIDs: acceptedAB, currentPromptID: nil,
            submittedPromptID: "prompt-a", sessionTerminal: true),
            "a delayed duplicate for an earlier accepted prompt resolves to the terminal session result")
        var boundedAcceptedPrompts: [String] = []
        for index in 0..<80 {
            boundedAcceptedPrompts = V3PromptResponseStatePolicy.recordAcceptedPrompt(
                boundedAcceptedPrompts, promptID: "prompt-\(index)")
        }
        precondition(boundedAcceptedPrompts.count == 64 && boundedAcceptedPrompts.last == "prompt-79" &&
                     !V3PromptResponseStatePolicy.shouldReturnCurrentStateAfterAcceptedDuplicate(
                        acceptedPromptID: nil, acceptedPromptIDs: boundedAcceptedPrompts,
                        currentPromptID: nil, submittedPromptID: "prompt-0", sessionTerminal: true),
                     "accepted-prompt replay history stays bounded")
        let newerPromptCanApply = V3AuthPollResponsePolicy.mayApply(
            currentSessionID: "session", replySessionID: "session",
            cancellationInProgress: false, currentRevision: 3, replyRevision: 5,
            currentPromptID: "prompt-a", replyPromptID: "prompt-b")
        precondition(newerPromptCanApply,
            "the authoritative poll for B must advance beyond A and be accepted after the duplicate response")
        let answer = try await waiter.value
        precondition(answer["action"] == "sms")
        precondition(center.pendingCount == 0, "answered continuation was retained")
        precondition(center.answer(promptID: prompt, answer: ["action": "voice"]) == .unavailable,
                     "a consumed prompt cannot accept another response")
        print("V3_PROMPT_RACE_PASS")
    }
}
