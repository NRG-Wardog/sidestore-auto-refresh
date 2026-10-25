import Foundation

@main
struct V3DirectRecoveryHostPolicyHarness {
    private static let requestID = "ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF"

    static func main() {
        parsesCanonicalRecords()
        rejectsMalformedRecords()
        acknowledgesOnlyVerifiedTerminalResults()
        print("V3_DIRECT_RECOVERY_HOST_POLICY_PASS")
    }

    private static func raw(operation: Any = "sourceAddConfirmed",
                            requestID: Any = V3DirectRecoveryHostPolicyHarness.requestID,
                            phase: Any = "prepared",
                            resultState: Any? = nil,
                            extra: [String: Any] = [:]) -> [String: Any] {
        var value: [String: Any] = [
            "requestID": requestID,
            "operation": operation,
            "phase": phase,
        ]
        if let resultState { value["resultState"] = resultState }
        value.merge(extra) { _, new in new }
        return value
    }

    private static func parsesCanonicalRecords() {
        for phase in ["prepared", "dispatched", "unknown"] {
            let record = V3HostDirectRecoveryRecord(raw(phase: phase))
            precondition(record?.requestID == requestID && record?.phase.rawValue == phase &&
                         record?.resultState == nil,
                         "valid nonterminal record parses with no resultState")
        }

        for operation in V3HostDirectRecoveryRecord.operations {
            let record = V3HostDirectRecoveryRecord(raw(operation: operation))
            precondition(record?.operation == operation,
                         "allowlisted direct operation parses: \(operation)")
        }

        for resultState in ["completed", "createdAndStored", "remoteCreatedLocalStorageUnverified"] {
            let record = V3HostDirectRecoveryRecord(raw(phase: "terminal", resultState: resultState))
            precondition(record?.phase == .terminal && record?.resultState == resultState,
                         "allowlisted terminal result parses: \(resultState)")
        }
    }

    private static func rejectsMalformedRecords() {
        let lowercasedID = requestID.lowercased()
        precondition(V3HostDirectRecoveryRecord(raw(requestID: lowercasedID)) == nil,
                     "a UUID spelling that is not canonical is rejected")
        precondition(V3HostDirectRecoveryRecord(raw(requestID: "not-a-uuid")) == nil)
        precondition(V3HostDirectRecoveryRecord(raw(operation: "snapshot")) == nil,
                     "non-direct operations are rejected")
        precondition(V3HostDirectRecoveryRecord(raw(phase: "finished")) == nil,
                     "unknown phase is rejected")

        precondition(V3HostDirectRecoveryRecord(raw(phase: "terminal")) == nil,
                     "terminal records require a resultState")
        for invalid in ["", "failed", "unknown-result"] {
            precondition(V3HostDirectRecoveryRecord(raw(phase: "terminal", resultState: invalid)) == nil,
                         "invalid terminal resultState is rejected: \(invalid)")
        }

        precondition(V3HostDirectRecoveryRecord(raw(resultState: "completed")) == nil,
                     "nonterminal records cannot carry a terminal result")
        precondition(V3HostDirectRecoveryRecord(raw(resultState: NSNumber(value: 1))) == nil,
                     "a non-String resultState is rejected rather than treated as absent")
        precondition(V3HostDirectRecoveryRecord(raw(resultState: NSNull())) == nil,
                     "NSNull is not an absent resultState")
        precondition(V3HostDirectRecoveryRecord(raw(extra: ["unexpected": "value"])) == nil,
                     "unknown record keys are rejected")
    }

    private static func acknowledgesOnlyVerifiedTerminalResults() {
        func terminal(_ operation: String, _ resultState: String) -> V3HostDirectRecoveryRecord {
            guard let record = V3HostDirectRecoveryRecord(raw(operation: operation,
                phase: "terminal", resultState: resultState)) else {
                fatalError("expected valid terminal \(operation)/\(resultState) record")
            }
            return record
        }

        let completedSourceAdd = terminal("sourceAddConfirmed", "completed")
        precondition(V3DirectRecoveryHostPolicy.mayAcknowledgeInspectedTerminal(
            completedSourceAdd, postcondition: .achieved))
        for postcondition in [V3DirectRecoveryPostcondition.notAchieved, .indeterminate,
                              .manualCheckRequired, .notDispatched] {
            precondition(!V3DirectRecoveryHostPolicy.mayAcknowledgeInspectedTerminal(
                completedSourceAdd, postcondition: postcondition),
                "only an achieved postcondition may acknowledge a terminal record")
        }

        let partialCertificate = terminal("certCreate", "remoteCreatedLocalStorageUnverified")
        precondition(!V3DirectRecoveryHostPolicy.mayAcknowledgeInspectedTerminal(
            partialCertificate, postcondition: .achieved),
            "partial remote certificate creation remains unacknowledged")
        precondition(!V3DirectRecoveryHostPolicy.mayAcknowledgeSuccessfulResponse(
            operation: "certCreate", result: ["outcome": "remoteCreatedLocalStorageUnverified"]))
        precondition(V3DirectRecoveryHostPolicy.mayAcknowledgeSuccessfulResponse(
            operation: "certCreate", result: ["outcome": "createdAndStored"]))
        precondition(!V3DirectRecoveryHostPolicy.mayAcknowledgeSuccessfulResponse(
            operation: "certCreate", result: ["outcome": "created"]),
            "certificate create ACK requires its verified stored outcome")
        precondition(V3DirectRecoveryHostPolicy.mayAcknowledgeSuccessfulResponse(
            operation: "sourceRemoveConfirmed", result: [:]))
        precondition(!V3DirectRecoveryHostPolicy.mayAcknowledgeSuccessfulResponse(
            operation: "accountImport", result: [:]),
            "account import has no automatic postcondition and requires a manual device check")
        precondition(!V3DirectRecoveryHostPolicy.mayAcknowledgeSuccessfulResponse(
            operation: "opStart", result: [:]), "non-direct operations cannot be acknowledged here")
    }
}
