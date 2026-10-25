import Foundation

@main
struct V3AsyncOwnerInterleavingsHarness {
    static func main() {
        basicRequestAOwnsItsResult()
        lateReplyAfterCancellationIsRejected()
        newerRequestWinsWhenItCompletesFirst()
        olderReplyIsRejectedWhenItCompletesFirst()
        bindingChangeInvalidatesTheCapturedOwner()
        resetAndRemovalInvalidateOutstandingOwners()
        print("V3_ASYNC_OWNER_INTERLEAVINGS_PASS")
    }

    private static func basicRequestAOwnsItsResult() {
        var owners = V3AsyncRequestOwnerState()
        let requestA = owners.begin(bindingID: "account-A")
        precondition(owners.owns(requestA, bindingID: "account-A"),
            "request A may commit while it remains the current owner")
    }

    private static func lateReplyAfterCancellationIsRejected() {
        var owners = V3AsyncRequestOwnerState()
        let requestA = owners.begin(bindingID: "account-A")
        // Cancellation invalidates ownership but does not assume the service
        // stopped. A late completion can still arrive and must not commit.
        owners.invalidate()
        precondition(!owners.owns(requestA, bindingID: "account-A"),
            "a late reply from cancelled request A cannot commit")
    }

    private static func newerRequestWinsWhenItCompletesFirst() {
        var owners = V3AsyncRequestOwnerState()
        let requestA = owners.begin(bindingID: "source-A")
        let requestB = owners.begin(bindingID: "source-A")

        // B completes first, then A. Only B still owns the result slot.
        precondition(owners.owns(requestB, bindingID: "source-A"),
            "newer request B commits when it completes first")
        precondition(!owners.owns(requestA, bindingID: "source-A"),
            "older request A cannot overwrite B")
    }

    private static func olderReplyIsRejectedWhenItCompletesFirst() {
        var owners = V3AsyncRequestOwnerState()
        let requestA = owners.begin(bindingID: "source-A")
        let requestB = owners.begin(bindingID: "source-A")

        // A completes first, while B is still outstanding. A is stale even
        // before B completes; B remains the eventual owner.
        precondition(!owners.owns(requestA, bindingID: "source-A"),
            "older request A is rejected when it completes first")
        precondition(owners.owns(requestB, bindingID: "source-A"),
            "request B remains eligible after A's rejected completion")
    }

    private static func bindingChangeInvalidatesTheCapturedOwner() {
        var owners = V3AsyncRequestOwnerState()
        let accountARequest = owners.begin(bindingID: "account-A")
        precondition(!owners.owns(accountARequest, bindingID: "account-B"),
            "a request cannot commit into a different account binding")

        let accountBRequest = owners.begin(bindingID: "account-B")
        precondition(!owners.owns(accountARequest, bindingID: "account-A"),
            "starting the new binding also supersedes the old request")
        precondition(owners.owns(accountBRequest, bindingID: "account-B"),
            "the request captured under the new binding owns its result")
    }

    private static func resetAndRemovalInvalidateOutstandingOwners() {
        var resetOwners = V3AsyncRequestOwnerState()
        let beforeReset = resetOwners.begin(bindingID: "settings")
        resetOwners.invalidate()
        precondition(!resetOwners.owns(beforeReset, bindingID: "settings"),
            "reset invalidates outstanding reads of the prior state")

        var removalOwners = V3AsyncRequestOwnerState()
        let beforeRemoval = removalOwners.begin(bindingID: "resource-7")
        removalOwners.invalidate()
        precondition(!removalOwners.owns(beforeRemoval, bindingID: "resource-7"),
            "removal invalidates outstanding reads of the removed resource")
    }
}
