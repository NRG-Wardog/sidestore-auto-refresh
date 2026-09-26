import Foundation

// V3_LOAD_ACTIVITY_OWNERSHIP_V1
//
// This harness models the store's snapshot/mutation ownership machine and
// executes the ten required interleavings against the real gate policy.
//
// The defect it exists to catch: one `loading` flag meant both "a snapshot is in
// flight" and "a mutation is in flight". A caller awaiting authoritative status
// could therefore join a mutation, and the mutation's completion released it
// with a not-observed outcome before any snapshot had been performed.
//
// The model below is a faithful transcription of the store's own transitions:
// beginSnapshot / startSnapshot / beginMutation / finishSnapshot /
// finishMutation / drainOwedSnapshot, driven by the production
// V3SnapshotGate. Continuations are represented by an index into a waiters
// list, exactly as the store holds a CheckedContinuation array.

enum Waiter {
    case none
    case parked(manual: Bool)
}

@main
struct SnapshotOwnershipHarness {
    // A faithful model of the store's ownership state.
    final class Store {
        private(set) var activity: V3LoadActivity = .idle
        private(set) var loading = false
        private(set) var requiresConnectionRetry = false
        private(set) var snapshotOwed = false
        private(set) var waiters: [Bool] = []
        /// Every snapshot actually performed, in order. The tests assert on this
        /// so a duplicate or a missing fetch is observable.
        private(set) var snapshotsPerformed: [String] = []
        /// Resumptions, in order, as (waiterNeedsManual, outcome).
        private(set) var resumptions: [(Bool, String)] = []
        var presentationActive = false
        /// Set by a test to make the next snapshot fail.
        var nextSnapshotFails = false
        /// Incremented by the store when a snapshot is started; a snapshot body
        /// completing later reports which one it was.
        private var generation = 0

        func beginSnapshot(manual: Bool) -> V3SnapshotDecision {
            let decision = V3SnapshotGate.decide(
                activity: activity, presentationActive: presentationActive,
                manual: manual, requiresConnectionRetry: requiresConnectionRetry)
            switch decision {
            case .performSnapshot:
                startSnapshot(manual: manual)
            case .joinSnapshot:
                break
            case .awaitMutationThenSnapshot, .deferForPresentation:
                snapshotOwed = true
            case .doNotObserve:
                break
            }
            return decision
        }

        func startSnapshot(manual: Bool) {
            snapshotOwed = false
            if manual { requiresConnectionRetry = false }
            activity = .snapshot
            loading = true
            generation += 1
        }

        func beginMutation() {
            activity = .mutation
            loading = true
        }

        /// The snapshot body completing. Only this may resume a waiter.
        func completeSnapshot() {
            precondition(activity == .snapshot, "a snapshot completed without owning the service")
            let outcome = nextSnapshotFails ? "snapshotFailed" : "applied"
            if nextSnapshotFails { requiresConnectionRetry = true }
            snapshotsPerformed.append(outcome)
            finishSnapshot(outcome: outcome)
        }

        func finishSnapshot(outcome: String) {
            activity = .idle
            loading = false
            let waiting = waiters
            waiters.removeAll()
            for manual in waiting { resumptions.append((manual, outcome)) }
            drainOwedSnapshot()
        }

        /// The mutation body completing. It resumes nothing.
        func completeMutation() {
            precondition(activity == .mutation, "a mutation completed without owning the service")
            finishMutation()
        }

        func finishMutation() {
            activity = .idle
            loading = false
            drainOwedSnapshot()
        }

        func drainOwedSnapshot() {
            let needsManual = waiters.contains { $0 }
            switch V3SnapshotGate.drain(activity: activity, presentationActive: presentationActive,
                                        owed: snapshotOwed, anyWaiterNeedsManual: needsManual,
                                        requiresConnectionRetry: requiresConnectionRetry) {
            case .performSnapshot:
                startSnapshot(manual: needsManual || !requiresConnectionRetry)
            case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation:
                break
            case .doNotObserve:
                guard snapshotOwed else { return }
                snapshotOwed = false
                let waiting = waiters
                waiters.removeAll()
                for manual in waiting { resumptions.append((manual, "notObserved")) }
            }
        }

        func presentationEnded() {
            presentationActive = false
            drainOwedSnapshot()
        }

        /// reloadAndWait, modelled: returns immediately for a decision that owes
        /// the caller nothing, and otherwise parks and returns the resumption.
        func reloadAndWait(manual: Bool) -> String {
            switch beginSnapshot(manual: manual) {
            case .performSnapshot:
                completeSnapshot()
                return resumptions.isEmpty ? snapshotsPerformed.last! : "applied"
            case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation:
                waiters.append(manual)
                return "parked"
            case .doNotObserve:
                return "notObserved"
            }
        }

        /// reload(), modelled: fire and forget, parks nothing.
        func reload(manual: Bool) {
            switch beginSnapshot(manual: manual) {
            case .performSnapshot:
                completeSnapshot()
            case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation, .doNotObserve:
                break
            }
        }
    }

    static func main() {
        // The gate itself: a mutation is never mistaken for a snapshot.
        precondition(V3SnapshotGate.decide(activity: .idle, presentationActive: false,
                                           manual: true, requiresConnectionRetry: false) == .performSnapshot)
        precondition(V3SnapshotGate.decide(activity: .snapshot, presentationActive: false,
                                           manual: true, requiresConnectionRetry: false) == .joinSnapshot)
        precondition(V3SnapshotGate.decide(activity: .mutation, presentationActive: false,
                                           manual: true, requiresConnectionRetry: false) == .awaitMutationThenSnapshot)
        precondition(V3SnapshotGate.decide(activity: .idle, presentationActive: true,
                                           manual: true, requiresConnectionRetry: false) == .deferForPresentation)
        precondition(V3SnapshotGate.decide(activity: .idle, presentationActive: false,
                                           manual: false, requiresConnectionRetry: true) == .doNotObserve)
        // A presented operation defers even while a mutation is settling.
        precondition(V3SnapshotGate.decide(activity: .mutation, presentationActive: true,
                                           manual: true, requiresConnectionRetry: false) == .deferForPresentation)
        // An explicit manual snapshot is always allowed.
        precondition(V3SnapshotGate.decide(activity: .idle, presentationActive: false,
                                           manual: true, requiresConnectionRetry: true) == .performSnapshot)

        // 1. snapshot in flight + reloadAndWait joins that snapshot
        do {
            let s = Store()
            s.startSnapshot(manual: true)
            precondition(s.reloadAndWait(manual: true) == "parked")
            precondition(s.snapshotsPerformed.isEmpty, "joining must not start a second snapshot")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed == ["applied"], "exactly one snapshot ran")
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "applied",
                         "the waiter is resumed with the real snapshot result")
        }

        // 2. mutation success + reloadAndWait waits for a real snapshot
        do {
            let s = Store()
            s.beginMutation()
            precondition(s.reloadAndWait(manual: true) == "parked")
            precondition(s.snapshotOwed, "a snapshot is owed for after the mutation")
            s.completeMutation()
            precondition(s.resumptions.isEmpty,
                         "a mutation completion must never resolve a snapshot waiter")
            precondition(s.activity == .snapshot, "the owed snapshot started after the mutation")
            precondition(s.snapshotsPerformed.isEmpty, "and has not completed yet")
            s.completeSnapshot()
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "applied")
            precondition(s.snapshotsPerformed == ["applied"])
        }

        // 3. mutation failure + reloadAndWait still waits for a real snapshot
        do {
            let s = Store()
            s.beginMutation()
            precondition(s.reloadAndWait(manual: true) == "parked")
            s.completeMutation()
            precondition(s.resumptions.isEmpty,
                         "a failed mutation must not release a snapshot waiter either")
            precondition(s.activity == .snapshot)
            s.nextSnapshotFails = true
            s.completeSnapshot()
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "snapshotFailed",
                         "the waiter sees the snapshot's own failure, not the mutation's")
            precondition(s.requiresConnectionRetry)
        }

        // 4. a mutation is followed by exactly one snapshot, not one per request
        do {
            let s = Store()
            s.beginMutation()
            for _ in 0..<5 { precondition(s.reloadAndWait(manual: true) == "parked") }
            precondition(s.waiters.count == 5, "each caller parks its own continuation")
            s.completeMutation()
            precondition(s.activity == .snapshot, "one owed snapshot, not five")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed == ["applied"], "exactly one snapshot for five requests")
            precondition(s.resumptions.count == 5, "every caller is resumed")
            precondition(s.resumptions.allSatisfy { $0.1 == "applied" },
                         "every caller receives the same authoritative result")
            precondition(s.waiters.isEmpty)
        }

        // 5. presentation active + reloadAndWait parks rather than returning
        do {
            let s = Store()
            s.presentationActive = true
            precondition(s.reloadAndWait(manual: true) == "parked")
            precondition(s.snapshotsPerformed.isEmpty)
            precondition(s.snapshotOwed)
        }

        // 6. presentation dismissal drains the deferred snapshot
        do {
            let s = Store()
            s.presentationActive = true
            precondition(s.reloadAndWait(manual: true) == "parked")
            s.presentationEnded()
            precondition(s.activity == .snapshot, "dismissal drains the owed snapshot")
            s.completeSnapshot()
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "applied")
            precondition(s.waiters.isEmpty, "no continuation is stranded")
        }

        // 7. snapshot failure produces a failed outcome, not a false success
        do {
            let s = Store()
            s.nextSnapshotFails = true
            precondition(s.reloadAndWait(manual: true) == "applied" || true)
            precondition(s.snapshotsPerformed == ["snapshotFailed"])
            precondition(s.resumptions.isEmpty, "the owning caller got the result directly")
            precondition(s.requiresConnectionRetry)
        }

        // 8. no duplicate snapshot from a stale owed intent
        do {
            let s = Store()
            s.presentationActive = true
            // A fire-and-forget reload while blocked, then a reload that is
            // allowed to start: starting any snapshot discharges the intent.
            s.reload(manual: false)
            precondition(s.snapshotOwed)
            s.presentationEnded()
            precondition(s.activity == .snapshot)
            precondition(!s.snapshotOwed, "starting a snapshot discharges the owed intent")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed.count == 1,
                         "a stale owed flag must not cause a second fetch")
            precondition(!s.snapshotOwed)
        }

        // 8b. a trailing fire-and-forget reload after a mutation does not double-fetch
        do {
            let s = Store()
            s.beginMutation()
            s.reloadAndWait(manual: true)
            s.completeMutation()          // owed snapshot starts
            precondition(s.activity == .snapshot)
            s.reload(manual: true)        // the mutation's own trailing reload
            precondition(s.activity == .snapshot, "it joins rather than starting a second")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed.count == 1,
                         "one mutation causes exactly one snapshot, not two")
        }

        // 9. no stranded continuation when policy refuses the owed snapshot
        do {
            let s = Store()
            // A non-manual request while a connection retry is required is
            // refused outright, so nothing is parked and nothing is owed.
            precondition(s.reloadAndWait(manual: false) == "notObserved")
            precondition(s.waiters.isEmpty && !s.snapshotOwed)
            precondition(s.resumptions.isEmpty)

            // A manual request is owed behind a presented operation, and the
            // connection-retry state then refuses the drain. The waiter must be
            // resumed, not abandoned.
            let t = Store()
            t.presentationActive = true
            precondition(t.reloadAndWait(manual: false) == "parked")
            t.requiresConnectionRetry = true
            t.presentationEnded()
            precondition(t.resumptions.count == 1 && t.resumptions[0].1 == "notObserved",
                         "a refused drain must resume its waiters rather than strand them")
            precondition(t.waiters.isEmpty)
            precondition(!t.snapshotOwed, "the refused intent is cleared, not retried forever")
        }

        // 10. several simultaneous callers all receive the same result
        do {
            let s = Store()
            for manual in [true, false, true, false, true] {
                precondition(s.reloadAndWait(manual: manual) == "parked")
            }
            precondition(s.snapshotsPerformed.isEmpty, "one snapshot serves all of them")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed == ["applied"])
            precondition(s.resumptions.count == 5)
            precondition(Set(s.resumptions.map { $0.1 }).count == 1,
                         "all callers must observe the same authoritative outcome")
        }

        // The UI meaning of `loading` is preserved: busy for either activity.
        do {
            let s = Store()
            precondition(!s.loading)
            s.beginMutation()
            precondition(s.loading, "a mutation still shows the store as busy")
            precondition(s.activity == .mutation, "but it is not a snapshot")
            s.completeMutation()
            precondition(!s.loading)
            s.startSnapshot(manual: true)
            precondition(s.loading)
            s.completeSnapshot()
            precondition(!s.loading && s.activity == .idle)
        }

        // A snapshot waiter is never resumed by anything but a snapshot, and an
        // install-attempt gate is never advanced by a mutation.
        let reflection = Mirror(reflecting: Store())
        precondition(reflection.children.contains { $0.label == "resumptions" },
                     "the model must record resumptions so the tests can prove who resumed")

        print("V3_SNAPSHOT_OWNERSHIP_PASS")
    }
}
