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
            precondition(!center.answer(promptID: id, answer: ["code": "duplicate"]))
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
        precondition(center.answer(promptID: prompt, answer: ["action": "sms"]))
        precondition(!center.answer(promptID: prompt, answer: ["action": "voice"]),
                     "rapid duplicate answer was accepted")
        let answer = try await waiter.value
        precondition(answer["action"] == "sms")
        precondition(center.pendingCount == 0, "answered continuation was retained")
        print("V3_PROMPT_RACE_PASS")
    }
}
