@main struct V3SignOutPostconditionHarness {
    static func snapshot(authenticated: Any = false,
                         activeAccountPresent: Any = false,
                         activeTeamPresent: Any = false) -> [String: Any] {
        ["authenticated": authenticated,
         "activeAccountPresent": activeAccountPresent,
         "activeTeamPresent": activeTeamPresent]
    }

    static func main() {
        let clean = snapshot()
        precondition(V3SignOutOutcomePolicy.resolve(snapshot: clean) == .confirmed)
        precondition(V3SignOutOutcomePolicy.successNotice(for: clean) == "Signed out successfully.")

        let staleAccount = snapshot(activeAccountPresent: true)
        precondition(V3SignOutOutcomePolicy.resolve(snapshot: staleAccount) == .accountStateRemains)
        precondition(V3SignOutOutcomePolicy.successNotice(for: staleAccount) == nil,
                     "cleared credentials cannot hide a still-active account row")

        let staleTeam = snapshot(activeTeamPresent: true)
        precondition(V3SignOutOutcomePolicy.resolve(snapshot: staleTeam) == .accountStateRemains)
        precondition(V3SignOutOutcomePolicy.successNotice(for: staleTeam) == nil,
                     "a still-active team prevents success")

        let stillAuthenticated = snapshot(authenticated: true)
        precondition(V3SignOutOutcomePolicy.resolve(snapshot: stillAuthenticated) == .authenticationRemains)
        precondition(V3SignOutOutcomePolicy.successNotice(for: stillAuthenticated) == nil,
                     "credentials that remain present prevent success")

        for partial in [
            ["activeAccountPresent": false, "activeTeamPresent": false] as [String: Any],
            ["authenticated": false, "activeTeamPresent": false],
            ["authenticated": false, "activeAccountPresent": false],
            snapshot(authenticated: NSNull()),
            snapshot(activeAccountPresent: "false")
        ] {
            let outcome = V3SignOutOutcomePolicy.resolve(snapshot: partial)
            precondition(outcome == .snapshotIncomplete,
                         "missing or malformed authoritative facts remain unknown")
            precondition(V3SignOutOutcomePolicy.successNotice(for: partial) == nil,
                         "incomplete snapshots cannot produce success copy")
            precondition(V3SignOutOutcomePolicy.whatHappened(for: outcome) != nil)
            precondition(V3SignOutOutcomePolicy.whatToDo(for: outcome) != nil)
        }

        precondition(V3IssueAction.reloadStatus.title == "Reload Status")
        precondition(V3IssueAction.reloadStatus.destination == nil)
        print("V3_SIGNOUT_POSTCONDITION_PASS")
    }
}
