import Foundation

@main
struct OperationServiceAdmissionHarness {
    static func main() {
        let activeSession = UUID().uuidString
        let otherSession = UUID().uuidString
        var registry = V3OperationMutationRegistry()
        precondition(registry.begin(activeSession) == .started)

        precondition(V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            "refreshAdmissionBegin", target: UUID().uuidString,
            activeOperationID: registry.activeID),
            "a surviving backend mutation must block a new refresh admission")
        precondition(V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            "opStart", target: otherSession, activeOperationID: registry.activeID),
            "a second session must not start beside the active mutation")
        precondition(!V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            "opPoll", target: activeSession, activeOperationID: registry.activeID),
            "the active operation must remain pollable")
        precondition(!V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            "opAnswer", target: activeSession, activeOperationID: registry.activeID),
            "the active operation must accept its own prompt response")
        precondition(V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            "opAnswer", target: otherSession, activeOperationID: registry.activeID),
            "a different session cannot answer the active operation's prompt")

        precondition(registry.finish(activeSession))
        precondition(!V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            "refreshAdmissionBegin", target: UUID().uuidString,
            activeOperationID: registry.activeID),
            "authoritative backend settlement releases admission ownership")
        print("V3_SERVICE_OPERATION_ADMISSION_PASS")
    }
}
