import Foundation

@main
struct RefreshRunIdentityHarness {
    static func main() {
        let schedulerRun = UUID().uuidString
        let unrelatedNewRun = UUID().uuidString
        let schedulerSelection = V3RefreshRunIdentitySelection.select(
            schedulerRunID: schedulerRun, expectedRunID: schedulerRun,
            activeRunID: schedulerRun, newRunID: unrelatedNewRun)
        precondition(schedulerSelection == V3RefreshRunIdentitySelection(
            runID: schedulerRun, schedulerOwned: true),
            "the active scheduler's exact run identity must reach SideStore")

        let staleRun = UUID().uuidString
        let directRun = UUID().uuidString
        let directSelection = V3RefreshRunIdentitySelection.select(
            schedulerRunID: nil, expectedRunID: staleRun, activeRunID: nil,
            newRunID: directRun)
        precondition(directSelection == V3RefreshRunIdentitySelection(
            runID: directRun, schedulerOwned: false),
            "a direct AppIntent run must not reuse a stale scheduler run ID")

        let malformed = V3RefreshRunIdentitySelection.select(
            schedulerRunID: nil, expectedRunID: nil, activeRunID: nil,
            newRunID: "not-a-uuid")
        precondition(malformed == nil,
            "run identity selection rejects malformed generated IDs")

        let directCollision = V3RefreshRunIdentitySelection.select(
            schedulerRunID: nil, expectedRunID: schedulerRun, activeRunID: schedulerRun,
            newRunID: unrelatedNewRun)
        precondition(directCollision == nil,
            "an upstream direct AppIntent cannot claim an active scheduler request")

        let brokenSchedulerClaim = V3RefreshRunIdentitySelection.select(
            schedulerRunID: schedulerRun, expectedRunID: schedulerRun,
            activeRunID: nil, newRunID: unrelatedNewRun)
        precondition(brokenSchedulerClaim == nil,
            "scheduler origin alone cannot reuse a run after its active claim disappeared")

        let claimDeadline = Date().addingTimeInterval(30)
        precondition(V3DirectRefreshRunClaimPolicy.isActive(
            runID: directRun, deadline: claimDeadline),
            "a direct AppIntent leaves a cross-entrypoint claim while its refresh is active")
        precondition(!V3DirectRefreshRunClaimPolicy.isActive(
            runID: directRun, deadline: Date(timeIntervalSince1970: 0)),
            "a stale direct claim expires after process loss")
        print("V3_REFRESH_RUN_IDENTITY_PASS")
    }
}
