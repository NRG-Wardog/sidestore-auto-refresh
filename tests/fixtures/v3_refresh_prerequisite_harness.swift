import Foundation

// V3_REFRESH_PREREQUISITE_HARNESS_V1
// Executes the single authoritative refresh prerequisite policy.
//
// The physical defect this locks down: Quick Setup already displayed
// "Pairing File: Action Required", yet Run Test Refresh still started the
// scheduler mutation and later reported a generic "no safe underlying cause was
// available" for a prerequisite the host already knew was missing.

@main
struct RefreshPrerequisiteHarness {
    static func main() {
        let id = UUID().uuidString

        // A pairing file is available: the refresh may start.
        let available = V3RefreshPrerequisite.evaluate(pairingStatus: "Pairing file available")
        precondition(available.state == .satisfied)
        precondition(!available.blocksRefresh)
        precondition(!available.blocksTargetedRefresh)
        precondition(available.failure(correlationID: id) == nil)
        precondition(V3PairingPresentationPolicy.isConfirmed(statusConnected: true,
            pairingStatus: "Pairing file available"))
        let failedRecheck = V3PairingPresentationPolicy.state(statusConnected: false,
            pairingStatus: "Pairing file available")
        precondition(failedRecheck == .unknown &&
                     !V3PairingPresentationPolicy.isConfirmed(statusConnected: false,
                         pairingStatus: "Pairing file available"),
            "a failed authoritative reload cannot leave cached pairing presented as ready")
        let staleMissingAfterFailedSnapshot = V3RefreshPrerequisite.evaluate(
            statusConnected: false, pairingStatus: "Pairing file required")
        precondition(staleMissingAfterFailedSnapshot.state == .unknown &&
                     !staleMissingAfterFailedSnapshot.blocksRefresh &&
                     staleMissingAfterFailedSnapshot.failure(correlationID: id) == nil,
            "a cached missing-pairing result cannot block refresh after its snapshot fails")

        // A pairing file is known to be missing: the refresh is blocked before
        // any mutation, with the canonical structured identity.
        let required = V3RefreshPrerequisite.evaluate(pairingStatus: "Pairing file required")
        precondition(required.state == .unsatisfied)
        precondition(required.blocksRefresh)
        precondition(required.blocksTargetedRefresh)
        precondition(required.detail == "A valid pairing file is required before device refresh.")
        precondition(required.recoveryDestination == "pairing")
        precondition(required.recoveryActionTitle == "Show Pairing Setup")
        precondition(required.recommendedAction == "Place or import a valid pairing file, then try again.")

        guard let failure = required.failure(correlationID: id) else {
            preconditionFailure("a known-missing pairing file must produce a failure")
        }
        precondition(failure.operation == "refresh")
        precondition(failure.stage == .pairing)
        precondition(failure.code == .notReady)
        precondition(failure.safeCause == .pairingRequired)
        precondition(failure.retryable == false)
        precondition(failure.correlationID == id)
        precondition(failure.safeMessage == "A pairing file is required before this device can be refreshed.\n" + failure.diagnosticLabel)
        precondition(failure.recovery == "Add the pairing file, then retry the refresh.")
        // The technical line carries the exact identifiers the defect report
        // asked for and is safe to copy.
        precondition(failure.technicalDetails.contains("operation=refresh"))
        precondition(failure.technicalDetails.contains("stage=pairing"))
        precondition(failure.technicalDetails.contains("code=notReady"))
        precondition(failure.technicalDetails.contains("safe_cause=pairingRequired"))
        precondition(failure.technicalDetails.contains("correlation=\(id)"))
        // A blocked prerequisite must not be presented as retryable.
        precondition(V3OperationFailureDetails(failure).retryDisposition == .blocked)

        // Unknown is never treated as missing, so a host restart or a failed
        // snapshot can never permanently disable a correctly configured device.
        for unknown in [nil, "", "Unknown", "Pairing file pending"] as [String?] {
            let evaluated = V3RefreshPrerequisite.evaluate(pairingStatus: unknown)
            precondition(evaluated.state == .unknown, "an unreadable status must stay unknown")
            precondition(!V3RefreshPrerequisite.isConfirmed(pairingStatus: unknown),
                "an unknown pairing snapshot must never satisfy Setup Complete")
            precondition(!evaluated.blocksRefresh)
            precondition(evaluated.failure(correlationID: id) == nil)
        }
        let invalidPairing = V3RefreshPrerequisite.evaluate(pairingStatus: "Pairing file invalid")
        precondition(invalidPairing.state == .unsatisfied && invalidPairing.blocksRefresh &&
                     !V3RefreshPrerequisite.isConfirmed(pairingStatus: "Pairing file invalid"),
            "an unparseable pairing file is never presented as ready")

        // Nothing else is claimed to be a refresh prerequisite.
        precondition(V3RefreshPrerequisite.evaluate(pairingStatus: "Pairing file available").blocksRefresh == false)
        precondition(V3RefreshPrerequisite.isConfirmed(pairingStatus: "Pairing file available"))

        print("V3_REFRESH_PREREQUISITE_PASS")
    }
}
