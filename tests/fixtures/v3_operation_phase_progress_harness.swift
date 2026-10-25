import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

@main
struct OperationPhaseProgressHarness {
    static func main() {
        let pipelineSteps: [(String, V3OperationPhase, String)] = [
            ("downloadApp", .preparingIPA, "Preparing IPA..."),
            ("fetchProvisioningProfiles", .fetchingProvisioningProfile, "Fetching provisioning profile..."),
            ("resignApp", .signing, "Signing..."),
            ("sendApp", .transferringToDevice, "Transferring to device..."),
            ("installApp", .installing, "Installing..."),
            ("refreshApp", .refreshing, "Refreshing..."),
            ("uninstallApp", .deleting, "Removing app..."),
            ("backupAppData", .backingUp, "Backing up..."),
            ("restoreAppData", .restoring, "Restoring..."),
            ("cleanStagedApp", .cleaningUp, "Cleaning up...")
        ]
        for (step, expectedPhase, expectedLabel) in pipelineSteps {
            var tracker = V3OperationPhaseTracker()
            tracker.recordPipelineStep(step)
            require(tracker.phase == expectedPhase, "pipeline step did not select its backend phase: \(step)")
            require(tracker.phase.label == expectedLabel, "phase label mismatch: \(step)")
        }

        var tracker = V3OperationPhaseTracker()
        tracker.recordPipelineStep("downloadApp", downloadUsesNetwork: true)
        require(tracker.phase == .downloadingIPA,
                "network-backed pipeline download did not expose downloading")
        tracker.recordPipelineStep("downloadApp", downloadUsesNetwork: false)
        require(tracker.phase == .preparingIPA,
                "local IPA processing was mislabeled as a network download")
        tracker.recordPipelineStep("downloadApp", downloadUsesNetwork: true)
        require(tracker.phase == .downloadingIPA,
                "remote download phase did not follow the actual backend input")

        tracker = V3OperationPhaseTracker()
        tracker.recordPipelineStep("resignApp")
        require(tracker.phase == .signing, "actual signing step was not exposed")
        for value in [0.2, 0.3, 0.45, 0.9] {
            _ = V3NormalizedProgress.clamp(value)
            require(tracker.phase == .signing,
                    "progress percentage changed the authoritative signing phase")
        }
        tracker.recordPipelineStep("unknownFutureStep")
        require(tracker.phase == .working, "unknown pipeline steps must use a truthful broad label")
        require(V3OperationPhase.forPipelineStep("unknownFutureStep") == nil,
                "unknown pipeline step gained a fabricated phase")

        require(V3NormalizedProgress.clamp(0) == 0, "zero progress changed")
        require(V3NormalizedProgress.clamp(0.43) == 0.43, "intermediate progress changed")
        require(V3NormalizedProgress.clamp(1) == 1, "100 percent progress changed")
        require(V3NormalizedProgress.clamp(1.01) == 1, "progress above one was not clamped")
        require(V3NormalizedProgress.clamp(2) == 1, "large progress overflow was not clamped")
        require(V3NormalizedProgress.clamp(-0.2) == 0, "negative progress was not clamped")
        require(V3NormalizedProgress.clamp(Double.nan) == 0, "NaN progress was not made safe")
        require(V3NormalizedProgress.percent(0, state: "working") == 0, "zero did not render as 0%")
        require(V3NormalizedProgress.percent(0.43, state: "working") == 43,
                "intermediate progress did not render correctly")
        require(V3NormalizedProgress.percent(1, state: "working") == 100,
                "one did not render as 100%")
        require(V3NormalizedProgress.percent(1.01, state: "working") == 100,
                "backend overflow rendered above 100%")
        require(V3NormalizedProgress.displayValue(0.43, state: "completed") == 1,
                "completed did not force exactly 100 percent")
        require(V3NormalizedProgress.percent(0.43, state: "completed") == 100,
                "completed operation did not render as 100%")
        print("V3_OPERATION_PHASE_PROGRESS_PASS")
    }
}
