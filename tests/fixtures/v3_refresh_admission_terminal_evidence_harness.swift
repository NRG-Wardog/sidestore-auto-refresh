import Foundation

@main
struct RefreshAdmissionTerminalEvidenceHarness {
    static func main() {
        let run = UUID().uuidString
        let previousRun = UUID().uuidString

        precondition(V3RefreshAdmissionFailureResolution.resolve(
            runID: run, dispatchedRunID: nil, terminalCallbackRunID: nil) == .releaseNotDispatched,
            "failure before native dispatch may release its reservation")
        precondition(V3RefreshAdmissionFailureResolution.resolve(
            runID: run, dispatchedRunID: run, terminalCallbackRunID: nil) == .retainUnknownOutcome,
            "post-dispatch XPC loss without a native terminal callback retains the durable lease")
        precondition(V3RefreshAdmissionFailureResolution.resolve(
            runID: run, dispatchedRunID: run, terminalCallbackRunID: previousRun) == .retainUnknownOutcome,
            "a callback from a previous run cannot settle this run")
        precondition(V3RefreshAdmissionFailureResolution.resolve(
            runID: run, dispatchedRunID: run, terminalCallbackRunID: run) == .releaseTerminalFailure,
            "a matching native terminal failure can release the durable lease")

        print("V3_REFRESH_ADMISSION_TERMINAL_EVIDENCE_PASS")
    }
}
