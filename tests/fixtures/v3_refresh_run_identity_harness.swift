import Foundation

@main
struct RefreshRunIdentityHarness {
    static func main() {
        let schedulerRun = UUID().uuidString
        let unrelatedNewRun = UUID().uuidString
        let schedulerSelection = V3RefreshRunIdentitySelection.select(
            expectedRunID: schedulerRun, activeRunID: schedulerRun, newRunID: unrelatedNewRun)
        precondition(schedulerSelection == V3RefreshRunIdentitySelection(
            runID: schedulerRun, schedulerOwned: true),
            "the active scheduler's exact run identity must reach SideStore")

        let staleRun = UUID().uuidString
        let directRun = UUID().uuidString
        let directSelection = V3RefreshRunIdentitySelection.select(
            expectedRunID: staleRun, activeRunID: nil, newRunID: directRun)
        precondition(directSelection == V3RefreshRunIdentitySelection(
            runID: directRun, schedulerOwned: false),
            "a direct AppIntent run must not reuse a stale scheduler run ID")

        let malformed = V3RefreshRunIdentitySelection.select(
            expectedRunID: nil, activeRunID: nil, newRunID: "not-a-uuid")
        precondition(malformed == nil,
            "run identity selection rejects malformed generated IDs")
        print("V3_REFRESH_RUN_IDENTITY_PASS")
    }
}
