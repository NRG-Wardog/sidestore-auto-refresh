import Foundation

@main
struct OperationServiceAdmissionHarness {
    static func main() {
        let activeSession = UUID().uuidString
        let otherSession = UUID().uuidString
        var registry = V3OperationMutationRegistry()
        precondition(registry.begin(activeSession) == .started)

        precondition(V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            operation: "refreshAdmissionBegin", target: UUID().uuidString,
            activeOperationID: registry.activeID),
            "a surviving backend mutation must block a new refresh admission")
        precondition(V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            operation: "opStart", target: otherSession, activeOperationID: registry.activeID),
            "a second session must not start beside the active mutation")
        precondition(!V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            operation: "opPoll", target: activeSession, activeOperationID: registry.activeID),
            "the active operation must remain pollable")
        precondition(!V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            operation: "opAnswer", target: activeSession, activeOperationID: registry.activeID),
            "the active operation must accept its own prompt response")
        precondition(V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            operation: "opAnswer", target: otherSession, activeOperationID: registry.activeID),
            "a different session cannot answer the active operation's prompt")

        precondition(registry.finish(activeSession))
        precondition(!V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            operation: "refreshAdmissionBegin", target: UUID().uuidString,
            activeOperationID: registry.activeID),
            "authoritative backend settlement releases admission ownership")
        precondition(V3ServiceMutationBusyCausePolicy.safeCause(operation: "settingsSet",
            anotherMutationActive: false, responseCapacityAvailable: false,
            refreshActive: false, refreshRelease: false,
            authenticationActive: false, isAuthContinuation: false) == .responseCapacityUnavailable,
            "reply-cache admission pressure is not falsely blamed on a running operation")
        precondition(V3ServiceMutationBusyCausePolicy.safeCause(operation: "settingsSet",
            anotherMutationActive: true, responseCapacityAvailable: false,
            refreshActive: false, refreshRelease: false,
            authenticationActive: false, isAuthContinuation: false) == .operationInProgress,
            "an actual mutation owner remains the known busy cause when multiple guards reject")
        let capacityFailure = CombinedFailure(operation: "settingsSet", stage: .command,
            code: .busy, id: UUID().uuidString, retryable: true,
            safeCause: .responseCapacityUnavailable)
        precondition(capacityFailure.safeMessage.contains("cannot safely accept") &&
                     capacityFailure.recovery.contains("release earlier request results") &&
                     V3OperationFailureDetails(capacityFailure).retryDisposition == .prerequisite,
                     "capacity pressure has truthful wait guidance and no immediate Retry CTA")

        precondition(V3StagedIPALeasePolicy.isLeased(hasOperationTask: true,
            preparationFinished: true, ownsMutationRegistry: false),
            "a running operation task leases its staged IPA")
        precondition(V3StagedIPALeasePolicy.isLeased(hasOperationTask: false,
            preparationFinished: false, ownsMutationRegistry: false),
            "unfinished preparation leases its staged IPA")
        precondition(V3StagedIPALeasePolicy.isLeased(hasOperationTask: false,
            preparationFinished: true, ownsMutationRegistry: true),
            "mutation registry ownership leases the staged IPA")
        precondition(!V3StagedIPALeasePolicy.isLeased(hasOperationTask: false,
            preparationFinished: true, ownsMutationRegistry: false),
            "a fully settled session no longer leases its staged IPA")
        precondition(V3StagedIPACleanupFallbackPolicy.mayDeleteLocally(
            serviceReportsBusy: false, callerConfirmsNeverStartedOrSettled: true),
            "local cleanup fallback is allowed after a proven never-started or terminal attempt")
        precondition(!V3StagedIPACleanupFallbackPolicy.mayDeleteLocally(
            serviceReportsBusy: true, callerConfirmsNeverStartedOrSettled: true) &&
                     !V3StagedIPACleanupFallbackPolicy.mayDeleteLocally(
                        serviceReportsBusy: false, callerConfirmsNeverStartedOrSettled: false),
            "busy or outcome-unknown backend ownership always prevents local IPA deletion")
        print("V3_SERVICE_OPERATION_ADMISSION_PASS")
    }
}
