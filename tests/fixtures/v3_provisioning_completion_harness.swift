
@main
struct CompletionHarness {
    static func main() {
        let defaults = UserDefaults(suiteName: "v3-completion-test-" + UUID().uuidString)!
        var facts = V3ProvisioningCompletionState(defaults: defaults)
        func status(_ f: V3ProvisioningCompletionState, owner: String = "same@example.invalid",
                    stamp: String = "boot:2", team: Bool = true, cert: Bool = true) -> String {
            f.status(owner: owner, identityStamp: stamp, identityStable: true,
                     activeAccountPresent: true, activeTeamPresent: team, activeCertificatePresent: cert, binding: "route-team-cert-device")
        }
        precondition(status(facts) == "unknown", "old account row is not completion")
        facts.begin(attemptID: "attempt-A", owner: "same@example.invalid", identityStamp: "boot:1")
        facts.authenticated(attemptID: "attempt-A", owner: "same@example.invalid", identityStamp: "boot:2")
        for missing in 0..<4 {
            precondition(!facts.complete(attemptID: "attempt-A", owner: "same@example.invalid", identityStamp: "boot:2",
                identityStable: true, fullProvisioningCompleted: missing != 0,
                activeAccountMatches: missing != 1, activeTeamMatches: missing != 2,
                activeCertificateMatches: missing != 3, binding: "route-team-cert-device"))
            precondition(status(facts) == "incomplete")
        }
        precondition(facts.complete(attemptID: "attempt-A", owner: "same@example.invalid", identityStamp: "boot:2",
            identityStable: true, fullProvisioningCompleted: true, activeAccountMatches: true,
            activeTeamMatches: true, activeCertificateMatches: true, binding: "route-team-cert-device"))
        precondition(status(facts) == "complete")
        precondition(status(V3ProvisioningCompletionState(defaults: defaults)) == "complete",
            "verified full provisioning must survive service restart")
        precondition(status(facts, team: false) == "incomplete")
        precondition(status(facts, cert: false) == "incomplete")
        precondition(status(facts, stamp: "boot:3") == "unknown")
        precondition(status(facts, owner: "different@example.invalid") == "unknown")
        facts.begin(attemptID: "attempt-B", owner: "same@example.invalid", identityStamp: "boot:2")
        precondition(status(facts) == "incomplete", "a new failing attempt cannot inherit old completion")
        precondition(!facts.complete(attemptID: "attempt-A", owner: "same@example.invalid", identityStamp: "boot:2",
            identityStable: true, fullProvisioningCompleted: true, activeAccountMatches: true,
            activeTeamMatches: true, activeCertificateMatches: true, binding: "route-team-cert-device"))
        precondition(status(V3ProvisioningCompletionState(defaults: defaults)) == "unknown", "restart must not infer completion")
        precondition(V3ProvisioningReauthenticationIdentityPolicy.mayAuthenticate(expectedOwner: "same@example.invalid",
            submittedOwner: " SAME@example.invalid ", currentOwner: "same@example.invalid", capturedStamp: "boot:2",
            currentStamp: "boot:2", identityStable: true))
        precondition(!V3ProvisioningReauthenticationIdentityPolicy.mayAuthenticate(expectedOwner: "same@example.invalid",
            submittedOwner: "other@example.invalid", currentOwner: "same@example.invalid", capturedStamp: "boot:2",
            currentStamp: "boot:2", identityStable: true))
        precondition(!V3ProvisioningReauthenticationIdentityPolicy.mayAuthenticate(expectedOwner: "same@example.invalid",
            submittedOwner: "same@example.invalid", currentOwner: "same@example.invalid", capturedStamp: "boot:2",
            currentStamp: "boot:3", identityStable: true))
        print("V3_PROVISIONING_COMPLETION_PASS")
    }
}
