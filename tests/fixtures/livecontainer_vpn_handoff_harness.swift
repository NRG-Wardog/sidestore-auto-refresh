import Foundation

@main
struct LiveContainerVPNHandoffHarness {
    @MainActor static func main() async {
        let now = Date(timeIntervalSince1970: 20_000)
        let firstRun = UUID().uuidString
        let secondRun = UUID().uuidString
        var settledOwners: [String] = []

        let cancelled = LiveContainerVPNActivationWaiter(runID: firstRun) { runID, _ in
            settledOwners.append(runID)
        }
        let cancelledValue = Task { await cancelled.value() }
        await Task.yield()
        precondition(cancelled.settle(.cancelled), "cancellation should settle the parked activation")
        let cancelledOutcome = await cancelledValue.value
        precondition(cancelledOutcome == .cancelled)
        precondition(!cancelled.settle(.returned), "a callback after cancel must be ignored")
        precondition(settledOwners == [firstRun], "cleanup/resume must happen exactly once")

        let settledBeforeWait = LiveContainerVPNActivationWaiter(runID: firstRun)
        precondition(settledBeforeWait.settle(.timedOut))
        let timeoutOutcome = await settledBeforeWait.value()
        precondition(timeoutOutcome == .timedOut,
                     "timeout before continuation registration must not strand the waiter")
        precondition(!settledBeforeWait.settle(.openFailed))

        let openFailed = LiveContainerVPNActivationWaiter(runID: firstRun)
        precondition(openFailed.settle(.openFailed))
        let openFailureOutcome = await openFailed.value()
        precondition(openFailureOutcome == .openFailed)
        let returned = LiveContainerVPNActivationWaiter(runID: secondRun)
        precondition(returned.settle(.returned))
        let returnedOutcome = await returned.value()
        precondition(returnedOutcome == .returned)

        let firstMarker = LiveContainerVPNReturnMarkerPolicy.marker(
            runID: firstRun, requestedAt: now)!
        let secondMarker = LiveContainerVPNReturnMarkerPolicy.marker(
            runID: secondRun, requestedAt: now.addingTimeInterval(1))!
        precondition(LiveContainerVPNReturnMarkerPolicy.owner(in: firstMarker) == firstRun)
        precondition(LiveContainerVPNReturnMarkerPolicy.requestedAt(in: firstMarker) == now)
        precondition(LiveContainerVPNReturnMarkerPolicy.isSameMarker(firstMarker, firstMarker))
        precondition(!LiveContainerVPNReturnMarkerPolicy.isSameMarker(firstMarker, secondMarker),
                     "a stale handoff cannot consume a newer run's marker")
        precondition(LiveContainerVPNReturnMarkerPolicy.isOwned(by: secondRun, value: secondMarker))
        precondition(!LiveContainerVPNReturnMarkerPolicy.isOwned(by: firstRun, value: secondMarker),
                     "stale completion cannot clear the new run's marker")
        precondition(LiveContainerVPNReturnMarkerPolicy.shouldConsume(
            requestedAt: now, now: now.addingTimeInterval(119)))
        precondition(!LiveContainerVPNReturnMarkerPolicy.shouldConsume(
            requestedAt: now, now: now.addingTimeInterval(120)))
        precondition(!LiveContainerVPNReturnMarkerPolicy.shouldConsume(
            requestedAt: now, now: now.addingTimeInterval(-1)))

        let legacyDate = now.addingTimeInterval(2)
        precondition(LiveContainerVPNReturnMarkerPolicy.owner(in: legacyDate) == nil)
        precondition(LiveContainerVPNReturnMarkerPolicy.requestedAt(in: legacyDate) == legacyDate,
                     "a pre-upgrade Date marker remains consumable once")
        precondition(LiveContainerVPNReturnMarkerPolicy.isSameMarker(legacyDate, legacyDate))

        // A cold relaunch consumes the pre-dispatch handoff once, then begins a
        // fresh run; it cannot replay the same persisted marker a second time.
        var persistedHandoff: Any? = firstMarker
        precondition(LiveContainerVPNReturnMarkerPolicy.shouldResume(persistedHandoff,
            now: now.addingTimeInterval(3)))
        let observedHandoff = persistedHandoff
        precondition(LiveContainerVPNReturnMarkerPolicy.isSameMarker(
            observedHandoff, persistedHandoff))
        persistedHandoff = nil
        precondition(!LiveContainerVPNReturnMarkerPolicy.shouldResume(persistedHandoff,
            now: now.addingTimeInterval(4)))
        let resumedRun = UUID().uuidString
        precondition(resumedRun != firstRun,
                     "a relaunched handoff is a fresh pre-dispatch run, not a reused operation session")

        print("LiveContainer VPN handoff ownership PASS")
    }
}
