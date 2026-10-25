@main struct V3SignOutPostconditionHarness {
    static func main() {
        let clean = V3SignOutOutcomePolicy.resolve(
            authenticated: false, activeAccountPresent: false, activeTeamPresent: false)
        precondition(clean == .confirmed)
        precondition(V3SignOutOutcomePolicy.successNotice(for: clean) == "Signed out successfully.")

        let staleAccount = V3SignOutOutcomePolicy.resolve(
            authenticated: false, activeAccountPresent: true, activeTeamPresent: false)
        precondition(staleAccount == .accountStateRemains)
        precondition(V3SignOutOutcomePolicy.successNotice(for: staleAccount) == nil,
                     "cleared credentials cannot hide a still-active account row")

        let staleTeam = V3SignOutOutcomePolicy.resolve(
            authenticated: false, activeAccountPresent: false, activeTeamPresent: true)
        precondition(staleTeam == .accountStateRemains)
        precondition(V3SignOutOutcomePolicy.successNotice(for: staleTeam) == nil,
                     "a still-active team prevents success")

        let stillAuthenticated = V3SignOutOutcomePolicy.resolve(
            authenticated: true, activeAccountPresent: false, activeTeamPresent: false)
        precondition(stillAuthenticated == .authenticationRemains)
        precondition(V3SignOutOutcomePolicy.successNotice(for: stillAuthenticated) == nil,
                     "credentials that remain present prevent success")

        for partial in [
            V3SignOutOutcomePolicy.resolve(
                authenticated: nil, activeAccountPresent: false, activeTeamPresent: false),
            V3SignOutOutcomePolicy.resolve(
                authenticated: false, activeAccountPresent: nil, activeTeamPresent: false),
            V3SignOutOutcomePolicy.resolve(
                authenticated: false, activeAccountPresent: false, activeTeamPresent: nil)
        ] {
            precondition(partial == .snapshotIncomplete,
                         "missing or malformed authoritative facts remain unknown")
            precondition(V3SignOutOutcomePolicy.successNotice(for: partial) == nil,
                         "incomplete snapshots cannot produce success copy")
            precondition(V3SignOutOutcomePolicy.whatHappened(for: partial) != nil)
            precondition(V3SignOutOutcomePolicy.whatToDo(for: partial) != nil)
        }

        print("V3_SIGNOUT_POSTCONDITION_PASS")
    }
}
