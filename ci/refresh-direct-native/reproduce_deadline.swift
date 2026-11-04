import Foundation

// Native concurrency reproduction of the original task-group lifetime, not a
// physical-device/private-AppIntents test. Compile with swiftc -parse-as-library.
enum ProbeError: Error { case timedOut, foregroundRejected }
actor Evidence {
    var pipelineFinished = false
    func finish() { pipelineFinished = true }
}
@main struct ReproduceDeadline {
    static func main() async {
        let evidence = Evidence()
        do {
            try await withThrowingTaskGroup(of: Void.self) { group in
                group.addTask {
                    // Production backgroundRefresh bridges a detached operation
                    // via a continuation, so task cancellation cannot finish it.
                    await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
                        DispatchQueue.global().asyncAfter(deadline: .now() + 0.08) { c.resume() }
                    }
                    await evidence.finish()
                }
                group.addTask {
                    try await Task.sleep(nanoseconds: 10_000_000)
                    throw ProbeError.timedOut
                }
                do {
                    for try await _ in group.prefix(1) {
                        group.cancelAll()
                        break
                    }
                } catch ProbeError.timedOut {
                    throw ProbeError.foregroundRejected
                }
            }
            fatalError("expected foreground rejection")
        } catch ProbeError.foregroundRejected {
            let finished = await evidence.pipelineFinished
            precondition(finished, "task group must wait for completed pipeline")
            print("PASS: pipeline completed before outer foreground error returned")
        } catch { fatalError("unexpected error: \(error)") }
    }
}
