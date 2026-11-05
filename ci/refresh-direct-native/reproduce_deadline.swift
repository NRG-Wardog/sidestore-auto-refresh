import Foundation

// Native concurrency reproduction of the original task-group lifetime, not a
// physical-device/private-AppIntents test. Compile with swiftc -parse-as-library.
// Force deadline-first ordering with a latch, not scheduler-dependent sleeps.
// Actual long-duration backend behavior is covered by the separate --long test.
enum ProbeError: Error { case timedOut, foregroundRejected }
actor Evidence {
    private var pipelineStarted = false
    private var released = false
    private var startWaiter: CheckedContinuation<Void, Never>?
    private var pipelineWaiter: CheckedContinuation<Void, Never>?
    private(set) var pipelineFinished = false
    private(set) var deadlineObserved = false

    func holdPipelineUntilDeadline() async {
        pipelineStarted = true
        startWaiter?.resume()
        startWaiter = nil
        if released { return }
        await withCheckedContinuation { pipelineWaiter = $0 }
    }
    func waitForPipelineStart() async {
        if pipelineStarted { return }
        await withCheckedContinuation { startWaiter = $0 }
    }
    func observeDeadlineAndReleasePipeline() {
        precondition(pipelineStarted && !pipelineFinished && !released)
        deadlineObserved = true
        released = true
        pipelineWaiter?.resume()
        pipelineWaiter = nil
    }
    func finish() {
        precondition(deadlineObserved && released)
        pipelineFinished = true
    }
}
@main struct ReproduceDeadline {
    static func main() async {
        let evidence = Evidence()
        do {
            try await withThrowingTaskGroup(of: Void.self) { group in
                group.addTask {
                    // Production backgroundRefresh bridges a detached operation
                    // via a continuation, so task cancellation cannot finish it.
                    await evidence.holdPipelineUntilDeadline()
                    await evidence.finish()
                }
                group.addTask {
                    await evidence.waitForPipelineStart()
                    throw ProbeError.timedOut
                }
                do {
                    for try await _ in group.prefix(1) {
                        group.cancelAll()
                        break
                    }
                } catch ProbeError.timedOut {
                    await evidence.observeDeadlineAndReleasePipeline()
                    throw ProbeError.foregroundRejected
                }
            }
            fatalError("expected foreground rejection")
        } catch ProbeError.foregroundRejected {
            let finished = await evidence.pipelineFinished
            let deadlineObserved = await evidence.deadlineObserved
            precondition(deadlineObserved, "deadline-first path must execute")
            precondition(finished, "task group must wait for completed pipeline")
            print("PASS: pipeline completed before outer foreground error returned")
        } catch { fatalError("unexpected error: \(error)") }
    }
}
