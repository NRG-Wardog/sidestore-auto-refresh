import Foundation

@main
struct V3StatusAuthorityInterleavingsHarness {
    static func main() {
        staleSnapshotCannotCrossReservedMutationRevision()
        notDispatchedMutationReleasesItsLease()
        unknownMutationIsRetainedAndBlocksNewMutation()
        controlTerminalSettlesOnlyItsExactLease()
        replacementServiceEpochRejectsOldTicket()
        reconciliationUsesTheMappedOwnerAndUnblocksMutation()
        lateStaleCallbackCannotApplyOrClearUnknown()
        waitersRemainFIFOWithCancellationIsolation()
        print("V3_STATUS_AUTHORITY_INTERLEAVINGS_PASS")
    }

    private static func connectedAuthority() -> V3StatusWriteAuthority {
        var authority = V3StatusWriteAuthority()
        _ = authority.observeServiceInstance("service-1", continuingOwnerID: nil)
        return authority
    }

    private static func staleSnapshotCannotCrossReservedMutationRevision() {
        var authority = connectedAuthority()
        let oldSnapshot = authority.begin(ownerID: "snapshot:old",
            revision: authority.revision, serviceInstanceID: "service-1", kind: .snapshot)!
        precondition(authority.complete(oldSnapshot, outcome: .committed))

        let mutationRevision = authority.reserveMutationRevision()
        let mutation = authority.begin(ownerID: "request:write-1", revision: mutationRevision,
            serviceInstanceID: "service-1", kind: .mutation)!
        precondition(authority.complete(mutation, outcome: .committed))
        precondition(!authority.markSnapshotApplied(oldSnapshot,
            currentServiceEpoch: authority.serviceEpoch, currentServiceInstanceID: "service-1"),
            "a snapshot from before the reserved write revision must be rejected")
        precondition(!V3StatusReplyCommitPolicy.mayApply(oldSnapshot, authority: authority,
            currentServiceEpoch: authority.serviceEpoch,
            currentServiceInstanceID: "service-1"),
            "the production reply commit policy rejects the same stale snapshot")
    }

    private static func notDispatchedMutationReleasesItsLease() {
        var authority = connectedAuthority()
        let revision = authority.reserveMutationRevision()
        let mutation = authority.begin(ownerID: "request:cancelled-before-dispatch",
            revision: revision, serviceInstanceID: "service-1", kind: .mutation)!
        precondition(authority.complete(mutation, outcome: .notDispatched))
        precondition(!authority.hasActiveLease && !authority.hasUnresolvedMutation,
            "a confirmed pre-dispatch cancellation releases without unknown outcome")
        precondition(authority.canBegin(kind: .mutation),
            "the next mutation may begin after not-dispatched completion")
    }

    private static func unknownMutationIsRetainedAndBlocksNewMutation() {
        var authority = connectedAuthority()
        let revision = authority.reserveMutationRevision()
        let mutation = authority.begin(ownerID: "request:uncertain-write",
            revision: revision, serviceInstanceID: "service-1", kind: .mutation)!
        precondition(authority.complete(mutation, outcome: .outcomeUnknown))
        precondition(authority.hasUnresolvedMutation &&
                     authority.unresolvedOwnerIDs.contains("request:uncertain-write"),
            "an outcome-unknown dispatched write remains recorded")
        precondition(!authority.canBegin(kind: .mutation),
            "an unresolved write blocks new mutations")

        let snapshot = authority.begin(ownerID: "snapshot:observe-unknown",
            revision: authority.revision, serviceInstanceID: "service-1", kind: .snapshot)!
        precondition(authority.complete(snapshot, outcome: .committed))
        precondition(authority.hasUnresolvedMutation && !authority.canBegin(kind: .mutation),
            "a generic snapshot does not clear an unknown mutation")
    }

    private static func controlTerminalSettlesOnlyItsExactLease() {
        var authority = connectedAuthority()
        let sessionID = "session-42"
        let ownerID = V3StatusAuthorityOperationPolicy.longOwnerID(
            operation: "opStart", sessionID: sessionID)!
        let revision = authority.reserveMutationRevision()
        let lease = authority.begin(ownerID: ownerID, revision: revision,
            serviceInstanceID: "service-1", kind: .mutation)!

        let unrelated = V3StatusWriteTicket(revision: lease.revision,
            serviceEpoch: lease.serviceEpoch, ownerID: "operation:other-session",
            serviceInstanceID: lease.serviceInstanceID, kind: lease.kind)
        precondition(!authority.complete(unrelated, outcome: .committed) &&
                     authority.activeLease == lease,
            "an unrelated control owner cannot settle the active lease")

        let controlOwnerID = V3StatusAuthorityOperationPolicy.controlOwnerID(
            operation: "opPoll", sessionID: sessionID)
        let terminal = V3StatusAuthorityOperationPolicy.terminalOutcome(operation: "opPoll",
            result: ["state": "completed", "backendSettled": NSNumber(value: true)])
        precondition(controlOwnerID == ownerID && terminal == .committed,
            "a terminal reply maps to the original operation owner")
        precondition(authority.complete(lease, outcome: terminal!),
            "the exact terminal owner releases its lease")
    }

    private static func replacementServiceEpochRejectsOldTicket() {
        var authority = connectedAuthority()
        let snapshot = authority.begin(ownerID: "snapshot:service-1",
            revision: authority.revision, serviceInstanceID: "service-1", kind: .snapshot)!
        precondition(authority.complete(snapshot, outcome: .committed))
        let oldEpoch = authority.serviceEpoch
        _ = authority.observeServiceInstance("service-2", continuingOwnerID: nil)
        precondition(authority.serviceEpoch > oldEpoch)
        precondition(!authority.mayApply(snapshot, currentServiceEpoch: authority.serviceEpoch,
            currentServiceInstanceID: "service-2"),
            "a reply from a retired service epoch cannot apply")
    }

    private static func reconciliationUsesTheMappedOwnerAndUnblocksMutation() {
        var authority = connectedAuthority()
        let sessionID = "operation-session-9"
        let operationOwner = V3StatusAuthorityOperationPolicy.longOwnerID(
            operation: "opStart", sessionID: sessionID)!
        let revision = authority.reserveMutationRevision()
        let mutation = authority.begin(ownerID: operationOwner, revision: revision,
            serviceInstanceID: "service-1", kind: .mutation)!
        precondition(authority.complete(mutation, outcome: .outcomeUnknown))

        let reconcileOwner = V3StatusAuthorityOperationPolicy.controlOwnerID(
            operation: "opRecoveryReconcile", sessionID: sessionID)!
        let wrongSessionOwner = V3StatusAuthorityOperationPolicy.controlOwnerID(
            operation: "opRecoveryReconcile", sessionID: "different-session")!
        precondition(reconcileOwner == operationOwner)
        precondition(!authority.resolveOwnerAfterReconciliation(wrongSessionOwner) &&
                     authority.hasUnresolvedMutation,
            "another operation's owner ID cannot clear this unresolved owner")
        precondition(authority.resolveOwnerAfterReconciliation(reconcileOwner),
            "the matching operation owner clears its unresolved record")
        precondition(!authority.hasUnresolvedMutation && authority.canBegin(kind: .mutation),
            "a settled reconciliation releases the mutation gate")
    }

    private static func lateStaleCallbackCannotApplyOrClearUnknown() {
        var authority = connectedAuthority()
        let revision = authority.reserveMutationRevision()
        let mutation = authority.begin(ownerID: "request:timed-out",
            revision: revision, serviceInstanceID: "service-1", kind: .mutation)!
        precondition(authority.complete(mutation, outcome: .outcomeUnknown))
        precondition(!authority.complete(mutation, outcome: .committed),
            "a late callback cannot complete a lease that already became unknown")
        precondition(authority.hasUnresolvedMutation &&
                     !authority.mayApply(mutation, currentServiceEpoch: authority.serviceEpoch,
                         currentServiceInstanceID: "service-1"),
            "the stale callback's ticket cannot apply or clear unresolved ownership")
        precondition(!V3StatusReplyCommitPolicy.mayApply(mutation, authority: authority,
            currentServiceEpoch: authority.serviceEpoch,
            currentServiceInstanceID: "service-1"),
            "the production reply commit policy rejects a late unknown write reply")
    }

    private static func waitersRemainFIFOWithCancellationIsolation() {
        var waiters = V3StatusLeaseWaiterOrder()
        waiters.enqueue("first")
        waiters.enqueue("cancelled")
        waiters.enqueue("third")
        waiters.remove("cancelled")
        precondition(waiters.count == 2)
        precondition(waiters.takeNext() == "first")
        precondition(waiters.takeNext() == "third")
        precondition(waiters.takeNext() == nil,
            "removing one cancelled waiter preserves FIFO order and its neighbors")
    }
}
