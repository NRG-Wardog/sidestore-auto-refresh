import Foundation

// V3_LOAD_ACTIVITY_OWNERSHIP_V1
//
// This harness exercises the store's snapshot/mutation ownership machine and
// runs its interleavings against the real gate, epoch policy, and waiter
// registry.
//
// The defect it exists to catch: one `loading` flag meant both "a snapshot is in
// flight" and "a mutation is in flight". A caller awaiting authoritative status
// could therefore join a mutation, and the mutation's completion released it
// with a not-observed outcome before any snapshot had been performed.
//
// The model below is a faithful transcription of the store's own transitions:
// beginSnapshot / startSnapshot / beginMutation / finishSnapshot /
// finishMutation / drainOwedSnapshot. Continuations are represented by an
// index into a waiters list, while the freshness epoch and waiter selection use
// the same production helpers as V3SideStoreStatusStore.

@main
struct SnapshotOwnershipHarness {
    // A faithful model of the store's ownership state.
    final class Store {
        private(set) var activity: V3LoadActivity = .idle
        private(set) var loading = false
        private(set) var requiresConnectionRetry = false
        private var snapshotOwedIntent = V3SnapshotOwedIntent()
        var snapshotOwed: Bool { snapshotOwedIntent.isOwed }
        var manualSnapshotOwed: Bool { snapshotOwedIntent.requiresManualSnapshot }
        private(set) var waiters: [Bool] = []
        private var snapshotWaiterRegistry = V3SnapshotWaiterRegistry()
        private var waiterIDs: [UUID] = []
        private(set) var lastWaiterID: UUID?
        /// Every snapshot actually performed, in order. The tests assert on this
        /// so a duplicate or a missing fetch is observable.
        private(set) var snapshotsPerformed: [String] = []
        private(set) var snapshotsStarted = 0
        private(set) var snapshotGeneration: UInt64 = 0
        /// Resumptions, in order, as (waiterNeedsManual, outcome).
        private(set) var resumptions: [(Bool, String)] = []
        var presentationActive = false
        /// Set by a test to make the next snapshot fail.
        var nextSnapshotFails = false
        /// When true a snapshot finishes inside the call that started it, which
        /// is what a caller that owns the snapshot observes. When false the
        /// snapshot stays in flight until the test completes it, which is the
        /// window in which a second caller can join. The real store awaits the
        /// service, so both windows exist in production.
        var completesSnapshotImmediately = true

        /// The store latches this on a failed snapshot. A test sets it directly to
        /// reach the state where an owed non-manual snapshot is refused, which no
        /// single call sequence otherwise produces.
        func simulateFailedSnapshotLatch() {
            requiresConnectionRetry = true
        }

        func beginSnapshot(manual: Bool,
                           waiterWillBeInstalled: Bool = false) -> V3SnapshotDecision {
            let decision = V3SnapshotGate.decide(
                activity: activity, presentationActive: presentationActive,
                manual: manual, requiresConnectionRetry: requiresConnectionRetry)
            switch decision {
            case .performSnapshot:
                startSnapshot(manual: manual)
            case .joinSnapshot:
                break
            case .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
                snapshotOwedIntent.record(manual: manual && !waiterWillBeInstalled)
            case .doNotObserve:
                break
            }
            return decision
        }

        @discardableResult
        func cancelWaiter(_ id: UUID) -> Bool {
            guard snapshotWaiterRegistry.remove(id),
                  let index = waiterIDs.firstIndex(of: id) else { return false }
            let manual = waiters[index]
            waiterIDs.remove(at: index)
            waiters.remove(at: index)
            if lastWaiterID == id { lastWaiterID = nil }
            // Production cancelSnapshotWaiter resumes this exact caller with
            // notObserved while leaving any shared snapshot request alive.
            resumptions.append((manual, "notObserved"))
            return true
        }

        private func parkWaiter(manual: Bool, decision: V3SnapshotDecision) {
            let id = UUID()
            lastWaiterID = id
            waiterIDs.append(id)
            waiters.append(manual)
            snapshotWaiterRegistry.insert(id, manual: manual,
                requiredSnapshotGeneration: V3SnapshotWaiterEpochPolicy.requiredGeneration(
                    for: decision, currentGeneration: snapshotGeneration))
        }

        func startSnapshot(manual: Bool) {
            snapshotOwedIntent.clear()
            snapshotGeneration &+= 1
            if manual { requiresConnectionRetry = false }
            snapshotsStarted += 1
            activity = .snapshot
            loading = true
        }

        func beginMutation() {
            activity = .mutation
            loading = true
        }

        /// The snapshot body completing. Only this may resume a waiter.
        func completeSnapshot() {
            precondition(activity == .snapshot, "a snapshot completed without owning the service")
            let completedGeneration = snapshotGeneration
            let outcome = nextSnapshotFails ? "snapshotFailed" : "applied"
            if nextSnapshotFails { requiresConnectionRetry = true }
            snapshotsPerformed.append(outcome)
            finishSnapshot(outcome: outcome, generation: completedGeneration)
        }

        func finishSnapshot(outcome: String, generation: UInt64) {
            activity = .idle
            loading = false
            for id in snapshotWaiterRegistry.take(throughSnapshotGeneration: generation) {
                guard let index = waiterIDs.firstIndex(of: id) else { continue }
                let manual = waiters[index]
                waiterIDs.remove(at: index)
                waiters.remove(at: index)
                if lastWaiterID == id { lastWaiterID = nil }
                resumptions.append((manual, outcome))
            }
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
            let needsManual = snapshotWaiterRegistry.anyManualWaiter
            switch V3SnapshotGate.drain(activity: activity, presentationActive: presentationActive,
                                        owed: snapshotOwedIntent.isOwed, anyWaiterNeedsManual: needsManual,
                                        explicitManualOwed: snapshotOwedIntent.requiresManualSnapshot,
                                        requiresConnectionRetry: requiresConnectionRetry) {
            case .performSnapshot:
                startSnapshot(manual: snapshotOwedIntent.requiresManualSnapshot ||
                    needsManual || !requiresConnectionRetry)
            case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
                break
            case .doNotObserve:
                guard snapshotOwed else { return }
                snapshotOwedIntent.clear()
                _ = snapshotWaiterRegistry.takeAll()
                waiterIDs.removeAll()
                let waiting = waiters
                waiters.removeAll()
                for manual in waiting { resumptions.append((manual, "notObserved")) }
            }
        }

        func presentationEnded() {
            presentationActive = false
            drainOwedSnapshot()
        }

        /// reloadAndWait, modelled. Returns "parked" for a caller that must wait,
        /// "notObserved" for one the policy refuses, and the snapshot's real
        /// outcome for one that owns a snapshot which completed immediately.
        func reloadAndWait(manual: Bool) -> String {
            let decision = beginSnapshot(manual: manual, waiterWillBeInstalled: true)
            switch decision {
            case .performSnapshot:
                guard completesSnapshotImmediately else { return "inFlight" }
                completeSnapshot()
                return snapshotsPerformed.last ?? "applied"
            case .joinSnapshot, .awaitMutationThenSnapshot, .deferForPresentation, .stillBlocked:
                parkWaiter(manual: manual, decision: decision)
                return "parked"
            case .doNotObserve:
                return "notObserved"
            }
        }

        /// reload(), modelled: fire and forget, so it starts a snapshot and
        /// returns without waiting. Production does exactly this.
        func reload(manual: Bool) {
            switch beginSnapshot(manual: manual) {
            case .performSnapshot, .joinSnapshot, .awaitMutationThenSnapshot,
                 .deferForPresentation, .stillBlocked, .doNotObserve:
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
            precondition(s.reloadAndWait(manual: true) == "parked", "s.reloadAndWait(manual: true) == parked")
            precondition(s.snapshotsPerformed.isEmpty, "joining must not start a second snapshot")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed == ["applied"], "exactly one snapshot ran")
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "applied", "the waiter is resumed with the real snapshot result")
        }

        // 2. mutation success + reloadAndWait waits for a real snapshot
        do {
            let s = Store()
            s.completesSnapshotImmediately = false
            s.beginMutation()
            precondition(s.reloadAndWait(manual: true) == "parked", "s.reloadAndWait(manual: true) == parked")
            precondition(s.snapshotOwed, "a snapshot is owed for after the mutation")
            s.completeMutation()
            precondition(s.resumptions.isEmpty, "a mutation completion must never resolve a snapshot waiter")
            precondition(s.activity == .snapshot, "the owed snapshot started after the mutation")
            precondition(s.snapshotsPerformed.isEmpty, "and has not completed yet")
            s.completeSnapshot()
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "applied", "s.resumptions.count == 1 && s.resumptions[0].1 == applied")
            precondition(s.snapshotsPerformed == ["applied"], "s.snapshotsPerformed == [applied]")
        }

        // 3. mutation failure + reloadAndWait still waits for a real snapshot
        do {
            let s = Store()
            s.completesSnapshotImmediately = false
            s.beginMutation()
            precondition(s.reloadAndWait(manual: true) == "parked", "s.reloadAndWait(manual: true) == parked")
            s.completeMutation()
            precondition(s.resumptions.isEmpty, "a failed mutation must not release a snapshot waiter either")
            precondition(s.activity == .snapshot, "s.activity == .snapshot")
            s.nextSnapshotFails = true
            s.completeSnapshot()
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "snapshotFailed",
                         "the waiter sees the snapshot's own failure, not the mutation's")
            precondition(s.requiresConnectionRetry, "s.requiresConnectionRetry")
        }

        // 4. a mutation is followed by exactly one snapshot, not one per request
        do {
            let s = Store()
            s.beginMutation()
            for _ in 0..<5 { precondition(s.reloadAndWait(manual: true) == "parked", "s.reloadAndWait(manual: true) == parked") }
            precondition(s.waiters.count == 5, "each caller parks its own continuation")
            s.completeMutation()
            precondition(s.activity == .snapshot, "one owed snapshot, not five")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed == ["applied"], "exactly one snapshot for five requests")
            precondition(s.resumptions.count == 5, "every caller is resumed")
            precondition(s.resumptions.allSatisfy { $0.1 == "applied" }, "every caller receives the same authoritative result")
            precondition(s.waiters.isEmpty, "s.waiters.isEmpty")
        }

        // 5. presentation active + reloadAndWait parks rather than returning
        do {
            let s = Store()
            s.presentationActive = true
            precondition(s.reloadAndWait(manual: true) == "parked", "s.reloadAndWait(manual: true) == parked")
            precondition(s.snapshotsPerformed.isEmpty, "s.snapshotsPerformed.isEmpty")
            precondition(s.snapshotOwed, "s.snapshotOwed")
        }

        // 6. presentation dismissal drains the deferred snapshot
        do {
            let s = Store()
            s.presentationActive = true
            precondition(s.reloadAndWait(manual: true) == "parked", "s.reloadAndWait(manual: true) == parked")
            s.presentationEnded()
            precondition(s.activity == .snapshot, "dismissal drains the owed snapshot")
            s.completeSnapshot()
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "applied", "s.resumptions.count == 1 && s.resumptions[0].1 == applied")
            precondition(s.waiters.isEmpty, "no continuation is stranded")
        }

        // 7. snapshot failure produces a failed outcome, not a false success
        do {
            let s = Store()
            s.nextSnapshotFails = true
            // The caller that owns the snapshot gets its result directly, and
            // nothing was parked, so there is no resumption to observe.
            precondition(s.reloadAndWait(manual: true) == "snapshotFailed",
                         "the owning caller must see the failure, not a success")
            precondition(s.snapshotsPerformed == ["snapshotFailed"], "s.snapshotsPerformed == [snapshotFailed]")
            precondition(s.resumptions.isEmpty, "the owning caller got the result directly")
            precondition(s.requiresConnectionRetry, "a failed snapshot latches the connection retry")
        }

        // 7b. a caller that joined a failing snapshot sees the same failure
        do {
            let s = Store()
            s.startSnapshot(manual: true)
            precondition(s.reloadAndWait(manual: true) == "parked", "s.reloadAndWait(manual: true) == parked")
            s.nextSnapshotFails = true
            s.completeSnapshot()
            precondition(s.snapshotsPerformed == ["snapshotFailed"], "s.snapshotsPerformed == [snapshotFailed]")
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "snapshotFailed", "a joiner must observe the snapshot's own failure")
        }

        // 8. no duplicate snapshot from a stale owed intent
        do {
            let s = Store()
            s.presentationActive = true
            // A fire-and-forget reload while blocked, then a reload that is
            // allowed to start: starting any snapshot discharges the intent.
            s.reload(manual: false)
            precondition(s.snapshotOwed, "s.snapshotOwed")
            s.presentationEnded()
            precondition(s.activity == .snapshot, "s.activity == .snapshot")
            precondition(!s.snapshotOwed, "starting a snapshot discharges the owed intent")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed.count == 1, "a stale owed flag must not cause a second fetch")
            precondition(!s.snapshotOwed, "!s.snapshotOwed")
        }

        // 8b. a trailing fire-and-forget reload after a mutation does not double-fetch
        do {
            let s = Store()
            s.beginMutation()
            _ = s.reloadAndWait(manual: true)
            s.completeMutation()          // owed snapshot starts
            precondition(s.activity == .snapshot, "s.activity == .snapshot")
            s.reload(manual: true)        // the mutation's own trailing reload
            precondition(s.activity == .snapshot, "it joins rather than starting a second")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed.count == 1,
                         "one mutation causes exactly one snapshot, not two")
        }

        // 9. no stranded continuation when policy refuses the owed snapshot
        do {
            // A non-manual request while a connection retry is required is
            // refused outright, so nothing is parked, nothing is owed, and the
            // caller is told truthfully that nothing was observed.
            let s = Store()
            s.simulateFailedSnapshotLatch()
            precondition(s.reloadAndWait(manual: false) == "notObserved", "a refused non-manual request must report notObserved")
            precondition(s.waiters.isEmpty && !s.snapshotOwed, "a refused request must park nothing and owe nothing")
            precondition(s.resumptions.isEmpty, "and must resume nothing")

            // An explicit manual request is never refused, so a caller that
            // needs one is always released by a real snapshot.
            let m = Store()
            m.simulateFailedSnapshotLatch()
            precondition(m.reloadAndWait(manual: true) == "applied", "a manual request must always be allowed to run")

            // A non-manual request owed behind a presented operation, with the
            // latch then refusing the drain, must be resumed rather than
            // abandoned. Abandoning it would hang the awaiting task forever.
            let t = Store()
            t.presentationActive = true
            precondition(t.reloadAndWait(manual: false) == "parked", "a presented operation must defer and park the caller")
            t.simulateFailedSnapshotLatch()
            t.presentationEnded()
            precondition(t.resumptions.count == 1 && t.resumptions[0].1 == "notObserved", "a refused drain must resume its waiters rather than strand them")
            precondition(t.waiters.isEmpty, "no continuation may remain parked")
            precondition(!t.snapshotOwed, "the refused intent is cleared, not retried forever")
        }

        // 9b. fire-and-forget manual intent survives a mutation while a retry
        // latch is set. The caller has no continuation, so the owed intent must
        // remember that it was explicit/manual rather than being inferred from
        // the waiter registry after the mutation finishes.
        do {
            let s = Store()
            s.simulateFailedSnapshotLatch()
            s.beginMutation()
            s.reload(manual: true)
            precondition(s.snapshotOwed, "the manual reload is owed behind the mutation")
            s.completeMutation()
            precondition(s.activity == .snapshot,
                         "the deferred manual request must start despite the retry latch")
            precondition(!s.requiresConnectionRetry,
                         "starting the manual snapshot clears the retry latch")
            precondition(s.snapshotsStarted == 1,
                         "one deferred manual request starts exactly one snapshot")
            precondition(!s.snapshotOwed, "starting consumes the owed request")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed == ["applied"],
                         "the actual manual snapshot completes once")
            precondition(s.snapshotsStarted == 1, "completion does not schedule a duplicate")
        }

        // 9c. the same contract holds when a presentation, rather than a
        // mutation, blocks an explicit fire-and-forget reload.
        do {
            let s = Store()
            s.simulateFailedSnapshotLatch()
            s.presentationActive = true
            s.reload(manual: true)
            precondition(s.snapshotOwed, "the manual reload is owed behind the presentation")
            s.presentationEnded()
            precondition(s.activity == .snapshot,
                         "presentation dismissal starts the owed manual snapshot")
            precondition(!s.requiresConnectionRetry,
                         "the owed manual snapshot clears the retry latch")
            precondition(s.snapshotsStarted == 1, "the owed request starts exactly once")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed == ["applied"] && s.snapshotsStarted == 1,
                         "the request completes without a duplicate snapshot")
        }

        // 9d. mutation completion is not a refusal when a presentation still
        // blocks the owed snapshot. Both a manual waiter and its manual intent
        // remain parked until presentation dismissal, then exactly one snapshot
        // starts and resolves the waiter with the authoritative result.
        do {
            let s = Store()
            s.simulateFailedSnapshotLatch()
            s.beginMutation()
            s.presentationActive = true
            precondition(s.reloadAndWait(manual: true) == "parked",
                         "manual caller parks behind the mutation and presentation")
            s.completeMutation()
            precondition(s.snapshotOwed && s.waiters.count == 1,
                         "mutation completion preserves owed intent and waiter while presentation remains")
            precondition(s.resumptions.isEmpty && s.snapshotsStarted == 0,
                         "a still-blocked drain neither resumes nor starts a snapshot")
            s.presentationEnded()
            precondition(s.activity == .snapshot && s.snapshotsStarted == 1,
                         "presentation dismissal starts exactly one manual snapshot")
            precondition(!s.requiresConnectionRetry && !s.snapshotOwed && s.waiters.count == 1,
                         "manual start clears the retry latch while preserving the parked waiter")
            s.completeSnapshot()
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "applied",
                         "the parked waiter receives the authoritative snapshot result")
            precondition(s.snapshotsStarted == 1 && !s.snapshotOwed && s.waiters.isEmpty,
                         "the overlap produces no duplicate snapshot or stranded waiter")
        }

        // 9e. a non-manual request remains policy-refused after every blocker
        // ends when the connection retry latch is still set.
        do {
            let s = Store()
            s.simulateFailedSnapshotLatch()
            s.beginMutation()
            s.presentationActive = true
            precondition(s.reloadAndWait(manual: false) == "parked",
                         "non-manual caller is parked while blocked")
            s.completeMutation()
            precondition(s.snapshotOwed && s.waiters.count == 1 && s.resumptions.isEmpty,
                         "ending only the mutation is still blocked, not a refusal")
            s.presentationEnded()
            precondition(s.activity == .idle && s.snapshotsStarted == 0,
                         "after blockers end policy refuses a non-manual retry")
            precondition(!s.snapshotOwed && s.waiters.isEmpty && s.resumptions.count == 1 &&
                         s.resumptions[0].1 == "notObserved",
                         "the refused request is cleared and its waiter is released truthfully")
        }

        // 9f. canceling the sole manual reloadAndWait caller removes its manual
        // requirement. Once mutation and presentation blockers end, the request
        // is refused by the retry latch instead of performing work for a caller
        // that already canceled.
        do {
            let s = Store()
            s.simulateFailedSnapshotLatch()
            s.beginMutation()
            s.presentationActive = true
            precondition(s.reloadAndWait(manual: true) == "parked",
                         "the manual waiter parks behind both blockers")
            guard let waiterID = s.lastWaiterID else {
                preconditionFailure("the parked caller has a waiter identity")
            }
            s.completeMutation()
            precondition(s.snapshotOwed && s.resumptions.isEmpty,
                         "mutation completion preserves the still-blocked waiter")
            precondition(s.cancelWaiter(waiterID), "cancel removes the sole waiter exactly once")
            precondition(!s.cancelWaiter(waiterID), "a canceled waiter cannot be removed twice")
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "notObserved",
                         "the canceled continuation settles once with notObserved")
            precondition(s.snapshotOwed && !s.manualSnapshotOwed,
                         "waiter cancellation removes manual intent without clearing the owed request")
            s.presentationEnded()
            precondition(s.activity == .idle && s.snapshotsStarted == 0,
                         "without any manual owner, the retry latch refuses the drain")
            precondition(s.requiresConnectionRetry && !s.snapshotOwed && s.waiters.isEmpty &&
                         s.resumptions.count == 1 && s.resumptions[0].1 == "notObserved",
                         "no snapshot or waiter survives the canceled request")
        }

        // 9g. an independent fire-and-forget manual reload remains authoritative
        // after the awaiting caller is canceled, so it still starts one snapshot.
        do {
            let s = Store()
            s.simulateFailedSnapshotLatch()
            s.beginMutation()
            s.presentationActive = true
            s.reload(manual: true)
            precondition(s.reloadAndWait(manual: true) == "parked",
                         "the awaiting caller joins the independent owed request")
            guard let waiterID = s.lastWaiterID else {
                preconditionFailure("the joined caller has a waiter identity")
            }
            s.completeMutation()
            precondition(s.cancelWaiter(waiterID), "the awaiting caller cancels independently")
            precondition(s.manualSnapshotOwed,
                         "the fire-and-forget request retains its manual intent")
            s.presentationEnded()
            precondition(s.activity == .snapshot && s.snapshotsStarted == 1 &&
                         !s.requiresConnectionRetry,
                         "the independent manual request starts exactly one snapshot")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed == ["applied"] && s.snapshotsStarted == 1 &&
                         s.waiters.isEmpty && s.resumptions.count == 1 &&
                         s.resumptions[0].1 == "notObserved",
                         "the snapshot completes once after the only waiter canceled")
        }

        // 10. several simultaneous callers all receive the same result
        do {
            let s = Store()
            // The snapshot stays in flight, which is the window in which the
            // remaining callers join it.
            s.completesSnapshotImmediately = false
            // The first caller owns the snapshot; the rest join it. Every one of
            // them must observe the same authoritative outcome.
            precondition(s.reloadAndWait(manual: true) == "inFlight",
                         "the first caller owns the snapshot, which is still in flight")
            for manual in [false, true, false, true] {
                precondition(s.reloadAndWait(manual: manual) == "parked",
                             "a concurrent caller must join, not start a second snapshot")
            }
            precondition(s.snapshotsPerformed.isEmpty, "no caller started a second snapshot")
            s.completeSnapshot()
            precondition(s.snapshotsPerformed == ["applied"],
                         "exactly one snapshot served them all")
            precondition(s.resumptions.count == 4, "every joined caller must be resumed")
            precondition(Set(s.resumptions.map { $0.1 }).count == 1,
                         "all callers must observe the same authoritative outcome")
            precondition(s.waiters.isEmpty, "no continuation may remain parked")
        }

        // 10b. A waiter deferred behind a presentation cannot be released by a
        // snapshot that was already in flight before the presentation began.
        // The production epoch policy and waiter registry are executed here.
        do {
            let s = Store()
            s.completesSnapshotImmediately = false
            precondition(s.reloadAndWait(manual: true) == "inFlight",
                         "the first caller starts snapshot generation 1")
            precondition(s.snapshotGeneration == 1 && s.activity == .snapshot,
                         "generation 1 remains active")

            s.presentationActive = true
            precondition(s.reloadAndWait(manual: true) == "parked",
                         "a caller behind the presentation must await a later snapshot")
            guard s.lastWaiterID != nil else {
                preconditionFailure("the deferred caller has a waiter identity")
            }
            precondition(s.waiters.count == 1,
                         "the deferred waiter remains registered")

            // The presentation ends before generation 1 returns. This is the
            // ordering that previously let finishSnapshot drain every waiter.
            s.presentationEnded()
            precondition(s.activity == .snapshot && s.resumptions.isEmpty,
                         "ending the presentation cannot substitute the active snapshot")
            s.completeSnapshot()
            precondition(s.resumptions.isEmpty && s.waiters.count == 1,
                         "generation 1 must leave the deferred waiter parked")
            precondition(s.activity == .snapshot && s.snapshotGeneration == 2,
                         "one owed post-presentation snapshot starts as generation 2")

            s.completeSnapshot()
            precondition(s.resumptions.count == 1 && s.resumptions[0].1 == "applied",
                         "only generation 2 resolves the deferred caller")
            precondition(s.waiters.isEmpty,
                         "the later snapshot leaves no waiter behind")
        }

        // The UI meaning of `loading` is preserved: busy for either activity.
        do {
            let s = Store()
            precondition(!s.loading, "!s.loading")
            s.beginMutation()
            precondition(s.loading, "a mutation still shows the store as busy")
            precondition(s.activity == .mutation, "but it is not a snapshot")
            s.completeMutation()
            precondition(!s.loading, "!s.loading")
            s.startSnapshot(manual: true)
            precondition(s.loading, "s.loading")
            s.completeSnapshot()
            precondition(!s.loading && s.activity == .idle, "!s.loading && s.activity == .idle")
        }

        // Execute the production bridge authority and the exact reply commit
        // policy. Snapshot R is invalidated as soon as write M reserves R+1,
        // even while M waits for R's correlated callback.
        do {
            var authority = V3StatusWriteAuthority()
            _ = authority.observeServiceInstance("pid-101", continuingOwnerID: nil)
            let snapshotRevision = authority.revision
            let snapshot = authority.begin(ownerID: "snapshot:R", revision: snapshotRevision,
                serviceInstanceID: "pid-101", kind: .snapshot)!
            let mutationRevision = authority.reserveMutationRevision()
            precondition(!V3StatusReplyCommitPolicy.mayApply(snapshot, authority: authority,
                currentServiceEpoch: authority.serviceEpoch, currentServiceInstanceID: "pid-101"),
                "a write intent invalidates snapshot R before dispatch")
            precondition(authority.complete(snapshot, outcome: .committed),
                "the original snapshot callback releases its lease even though its result is stale")
            let mutation = authority.begin(ownerID: "request:M", revision: mutationRevision,
                serviceInstanceID: "pid-101", kind: .mutation)!
            precondition(!V3StatusReplyCommitPolicy.mayApply(snapshot, authority: authority,
                currentServiceEpoch: authority.serviceEpoch, currentServiceInstanceID: "pid-101"),
                "late snapshot R cannot overwrite mutation M")
            precondition(authority.complete(mutation, outcome: .committed),
                "the correlated direct write callback releases mutation M")
            precondition(V3StatusReplyCommitPolicy.mayApply(mutation, authority: authority,
                currentServiceEpoch: authority.serviceEpoch, currentServiceInstanceID: "pid-101"),
                "only the current mutation ticket can apply its result")

            // The bridge's exact direct-write classification covers each
            // snapshot-bearing family and leaves file-only operations exempt.
            let writeOperations = ["sourceAddConfirmed", "sourceRemoveConfirmed", "certCreate",
                "certDelete", "certRevoke", "pairingImportData", "settingsSet", "sidesignReset",
                "opRecoveryPrepare", "recoveryDiscardUnreadable"]
            for operation in writeOperations {
                precondition(V3StatusAuthorityOperationPolicy.directWriteOwnerID(
                    operation: operation, requestID: "request-\(operation)") == "request:request-\(operation)",
                    "\(operation) owns an exact request ticket")
            }
            precondition(V3StatusAuthorityOperationPolicy.directWriteOwnerID(
                operation: "ipaCleanup", requestID: "file-cleanup") == nil &&
                V3StatusAuthorityOperationPolicy.directWriteOwnerID(
                operation: "backupResult", requestID: "file-transfer") == nil,
                "file transfer and staging cleanup do not change snapshot state")

            // Busy snapshots never satisfy the production commit gate.
            let busySnapshotRevision = authority.revision
            let busySnapshot = authority.begin(ownerID: "snapshot:busy", revision: busySnapshotRevision,
                serviceInstanceID: "pid-101", kind: .snapshot)!
            precondition(authority.complete(busySnapshot, outcome: .committed),
                "the busy reply is still a correlated snapshot completion")
            precondition(!V3StatusReplyCommitPolicy.mayApply(busySnapshot, authority: authority,
                currentServiceEpoch: authority.serviceEpoch, currentServiceInstanceID: "pid-101",
                busySnapshot: true), "busy=true cannot satisfy reloadAndWait")

            // A v1 durable operation hold can make a cold-relaunch snapshot
            // busy. Its current-ticket recovery fields may publish the recovery
            // banner without accepting account/app state or satisfying waiters.
            let recoveryTicket = authority.begin(ownerID: "snapshot:recovery", revision: authority.revision,
                serviceInstanceID: "pid-101", kind: .snapshot)!
            let recoveryReply: [String: Any] = [
                "busy": true,
                "operationRecovery": ["session": "12345678-1234-1234-1234-123456789ABC",
                    "kind": "install", "phase": "dispatched"]
            ]
            precondition(authority.complete(recoveryTicket, outcome: .committed),
                "the busy cold-relaunch reply has a correlated completion")
            precondition(!V3StatusReplyCommitPolicy.mayApply(recoveryTicket, authority: authority,
                currentServiceEpoch: authority.serviceEpoch, currentServiceInstanceID: "pid-101",
                busySnapshot: true), "the recovery-only path still cannot satisfy a reload waiter")
            precondition(V3StatusRecoveryEvidencePolicy.hasLegacyEvidence(recoveryReply) &&
                V3StatusRecoveryEvidencePolicy.mayApply(busySnapshot: true, activeMutation: nil,
                    hasDurableRecoveryEvidence: true) &&
                V3StatusReplyCommitPolicy.mayApply(recoveryTicket, authority: authority,
                    currentServiceEpoch: authority.serviceEpoch, currentServiceInstanceID: "pid-101"),
                "current-ticket v1 recovery evidence remains available while status rows stay stale")

            // Cancellation acknowledgement has no transition in this policy:
            // the lease remains exclusive until the original callback or an
            // explicit service retirement.
            let cancelRevision = authority.reserveMutationRevision()
            let canceledWrite = authority.begin(ownerID: "request:cancelled", revision: cancelRevision,
                serviceInstanceID: "pid-101", kind: .mutation)!
            precondition(!authority.canBegin(kind: .snapshot) && authority.activeLease == canceledWrite,
                "a cancel ACK cannot release the active write")
            let retired = authority.retireService()
            precondition(retired == canceledWrite && authority.hasUnresolvedMutation,
                "explicit retirement preserves an unknown owner")
            precondition(!authority.complete(canceledWrite, outcome: .committed),
                "the late callback cannot revive a retired ticket")
            _ = authority.observeServiceInstance("pid-202", continuingOwnerID: nil)
            let postRetirementSnapshot = authority.begin(ownerID: "snapshot:post-retirement",
                revision: authority.revision, serviceInstanceID: "pid-202", kind: .snapshot)!
            precondition(authority.complete(postRetirementSnapshot, outcome: .committed),
                "the new service can produce an authoritative snapshot")
            precondition(V3StatusReplyCommitPolicy.mayApply(postRetirementSnapshot, authority: authority,
                currentServiceEpoch: authority.serviceEpoch, currentServiceInstanceID: "pid-202") &&
                authority.hasUnresolvedMutation,
                "a generic snapshot does not resolve an ambiguous direct write")
            precondition(authority.canBegin(kind: .snapshot) && !authority.canBegin(kind: .mutation),
                "a fresh read can inspect state while mutation retry remains blocked")
            let beforeLateTerminal = authority.revision
            precondition(authority.resolveOwnerAfterReconciliation("request:cancelled") &&
                !authority.hasUnresolvedMutation && authority.revision == beforeLateTerminal &+ 1,
                "only exact terminal evidence clears that owner and invalidates older snapshots")

            // Confirmed pre-dispatch failure is different from an unknown
            // dispatched write and does not leave a retry-blocking owner.
            let notDispatchedRevision = authority.reserveMutationRevision()
            let notDispatched = authority.begin(ownerID: "request:not-dispatched",
                revision: notDispatchedRevision, serviceInstanceID: "pid-202", kind: .mutation)!
            precondition(authority.complete(notDispatched, outcome: .notDispatched) &&
                !authority.hasUnresolvedMutation, "confirmed not-dispatched leaves no unknown owner")

            let ambiguousRevision = authority.reserveMutationRevision()
            let ambiguousWrite = authority.begin(ownerID: "request:post-run-error",
                revision: ambiguousRevision, serviceInstanceID: "pid-202", kind: .mutation)!
            precondition(authority.complete(ambiguousWrite, outcome: .outcomeUnknown) &&
                authority.hasUnresolvedMutation && !authority.canBegin(kind: .mutation),
                "a dispatched error after a possible remote side effect blocks automatic retry")
            precondition(authority.resolveOwnerAfterReconciliation("request:post-run-error") &&
                !authority.hasUnresolvedMutation,
                "only exact correlated terminal evidence clears the ambiguous request owner")

            // Multiple waiters retain FIFO order and cancellation removes only
            // its own identity.
            var order = V3StatusLeaseWaiterOrder()
            order.enqueue("snapshot-1")
            order.enqueue("write-2")
            order.enqueue("snapshot-3")
            order.remove("write-2")
            precondition(order.takeNext() == "snapshot-1" && order.takeNext() == "snapshot-3" &&
                order.takeNext() == nil, "multiple waiters drain FIFO after exact cancellation")

            precondition(V3StatusAuthorityOperationPolicy.longOwnerID(
                operation: "authBegin", sessionID: "auth-session") == "auth:auth-session" &&
                V3StatusAuthorityOperationPolicy.controlOwnerID(
                operation: "authPoll", sessionID: "auth-session") == "auth:auth-session",
                "auth control retains exact long owner")
            precondition(V3StatusAuthorityOperationPolicy.longOwnerID(
                operation: "refreshAdmissionBegin", sessionID: "run-id") == "refresh:run-id" &&
                V3StatusAuthorityOperationPolicy.controlOwnerID(
                operation: "refreshAdmissionReconcile", sessionID: "run-id") == "refresh:run-id",
                "refresh reconciliation retains exact run owner")
        }

        // A snapshot waiter is never resumed by anything but a snapshot, and an
        // install-attempt gate is never advanced by a mutation.
        let reflection = Mirror(reflecting: Store())
        precondition(reflection.children.contains { $0.label == "resumptions" }, "the model must record resumptions so the tests can prove who resumed")

        print("V3_SNAPSHOT_OWNERSHIP_PASS")
    }
}
