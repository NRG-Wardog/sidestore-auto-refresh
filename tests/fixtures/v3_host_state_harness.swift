import Foundation

@main
struct V3HostStateHarness {
    static func main() {
        var waiters = V3SnapshotWaiterRegistry()
        let canceled = UUID()
        let survivingManual = UUID()
        let survivingAutomatic = UUID()
        waiters.insert(canceled, manual: true)
        waiters.insert(survivingManual, manual: true)
        waiters.insert(survivingAutomatic, manual: false)
        precondition(waiters.anyManualWaiter)
        precondition(waiters.remove(canceled))
        precondition(!waiters.remove(canceled), "a canceled waiter is removed once")
        let resumed = Set(waiters.takeAll())
        precondition(resumed == Set([survivingManual, survivingAutomatic]))
        precondition(waiters.isEmpty && !waiters.anyManualWaiter)
        precondition(waiters.takeAll().isEmpty, "completion drains each waiter once")

        precondition(!V3SnapshotErrorPolicy.shouldMarkDisconnected(CancellationError()))
        precondition(V3SnapshotErrorPolicy.shouldMarkDisconnected(NSError(domain: "service", code: 1)))

        var revision: UInt64 = 4
        let oldHealthRevision = revision
        revision &+= 1 // certificate update invalidates the in-flight Health result
        precondition(!V3SetupFactRevisionPolicy.mayApply(captured: oldHealthRevision,
                                                         current: revision))
        let replacementHealthRevision = revision
        precondition(V3SetupFactRevisionPolicy.mayApply(captured: replacementHealthRevision,
                                                        current: revision))

        var health = V3HealthReloadQueue()
        precondition(health.request(), "first health request starts immediately")
        precondition(!health.request(), "an overlapping request queues a rerun")
        precondition(!health.request(), "multiple notifications coalesce")
        precondition(health.finishIteration(), "the queued request runs after the stale request")
        precondition(!health.finishIteration(), "the queued request runs exactly once")
        precondition(!health.isChecking)

        print("V3_HOST_STATE_PASS")
    }
}
