import Foundation

@main
struct AuthIdentityBindingHarness {
    static func main() {
        var networkCalls = 0
        func teamRequest(sessionOwner: String?, teamOwner: String?, before: UInt64,
                         after: UInt64, cancelled: Bool = false) -> Bool {
            guard V3AuthIdentityBindingPolicy.mayDispatchTeamRequest(sessionOwner: sessionOwner,
                    teamOwner: teamOwner, generationBefore: before, generationAfter: after,
                    cancelled: cancelled) else { return false }
            networkCalls += 1
            return true
        }

        precondition(teamRequest(sessionOwner: "a@example.com", teamOwner: "A@EXAMPLE.COM",
            before: 7, after: 7), "A session with A team must reach the API")
        precondition(!teamRequest(sessionOwner: "b@example.com", teamOwner: "a@example.com",
            before: 7, after: 7), "B session with stale A team must be rejected")
        precondition(networkCalls == 1, "identity mismatch must reject before network invocation")

        // A prior auth success for A followed by a B route/team-fetch failure
        // cannot resume against A's saved team. A fresh B/B retry is allowed.
        precondition(!V3ProvisioningResumeAvailabilityPolicy.canResume(authenticated: true,
            currentAppleID: "b@example.com", resumableAppleID: "b@example.com",
            hasSession: true, hasTeamAccount: true, teamAccountAppleID: "a@example.com"))
        precondition(V3ProvisioningResumeAvailabilityPolicy.canResume(authenticated: true,
            currentAppleID: "B@example.com", resumableAppleID: "b@example.com",
            hasSession: true, hasTeamAccount: true, teamAccountAppleID: "b@example.com"))

        // Every legitimate team in B's multi-team response carries the same owner.
        for teamOwner in ["b@example.com", " B@EXAMPLE.COM "] {
            precondition(teamRequest(sessionOwner: "b@example.com", teamOwner: teamOwner,
                before: 8, after: 8))
        }
        // A shared team identifier may be returned under two Apple IDs; the
        // account-bound ALTTeam owner decides, never the identifier alone.
        precondition(V3AuthIdentityBindingPolicy.mayDispatchTeamRequest(sessionOwner: "a@example.com",
            teamOwner: "a@example.com", generationBefore: 4, generationAfter: 4))
        precondition(V3AuthIdentityBindingPolicy.mayDispatchTeamRequest(sessionOwner: "b@example.com",
            teamOwner: "b@example.com", generationBefore: 8, generationAfter: 8))

        // fetchTeams(account: A) is checked before the Apple API call under B.
        precondition(!V3AuthIdentityBindingPolicy.mayFetchTeams(sessionOwner: "b@example.com",
            requestedOwner: "a@example.com", generationBefore: 8, generationAfter: 8))
        precondition(V3AuthIdentityBindingPolicy.mayFetchTeams(sessionOwner: "b@example.com",
            requestedOwner: "B@example.com", generationBefore: 8, generationAfter: 8))

        // Password-only is a stored credential route, but cannot construct a usable session.
        precondition(!V3AuthIdentityBindingPolicy.hasUsableSession(credentialRoutePresent: true,
            dsid: nil, xcodeToken: nil, sessionDSID: nil, sessionXcodeToken: nil,
            generationBefore: 1, generationAfter: 1))
        precondition(V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: "b@example.com",
            teamOwner: "B@example.com"), "matching account remains distinct from provisioning state")
        precondition(V3AuthIdentityBindingPolicy.hasUsableSession(credentialRoutePresent: true,
            dsid: "dsid-b", xcodeToken: "token-b", sessionDSID: "dsid-b",
            sessionXcodeToken: "token-b",
            generationBefore: 8, generationAfter: 8))
        precondition(!V3AuthIdentityBindingPolicy.hasUsableSession(credentialRoutePresent: true,
            dsid: "dsid-b", xcodeToken: "token-b2", sessionDSID: "dsid-b",
            sessionXcodeToken: "token-b1", generationBefore: 8, generationAfter: 8),
            "a coalesced stale session with the same DSID but rotated token is rejected")
        let staleResumeSessionIsUsable = V3AuthIdentityBindingPolicy.hasUsableSession(
            credentialRoutePresent: true, dsid: "dsid-b", xcodeToken: "token-b2",
            sessionDSID: "dsid-b", sessionXcodeToken: "token-b1",
            generationBefore: 8, generationAfter: 8)
        precondition(!V3ProvisioningResumeAvailabilityPolicy.canResume(authenticated: true,
            currentAppleID: "b@example.com", resumableAppleID: "b@example.com",
            hasSession: staleResumeSessionIsUsable, hasTeamAccount: true,
            teamAccountAppleID: "b@example.com"),
            "provisioning retry cannot pair B credentials with a cached B session for an older token")
        precondition(V3AuthIdentityBindingPolicy.sameCredentialRoute(
            appleIDBefore: "b@example.com", appleIDAfter: "B@EXAMPLE.COM",
            dsidBefore: "dsid-b", dsidAfter: "dsid-b", tokenBefore: "token-b", tokenAfter: "token-b"))
        precondition(!V3AuthIdentityBindingPolicy.sameCredentialRoute(
            appleIDBefore: "a@example.com", appleIDAfter: "b@example.com",
            dsidBefore: "dsid-a", dsidAfter: "dsid-b", tokenBefore: "token-a", tokenAfter: "token-b"))

        // A stale database account A is never projected as B's authoritative account/team.
        precondition(!V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: "b@example.com",
            teamOwner: "a@example.com"))
        let staleActiveOwner = V3AuthIdentityBindingPolicy.resolveColdTeamOwner(
            storedTeamOwners: ["a@example.com"], activeTeamIdentifier: "team-shared",
            requestedTeamIdentifier: "team-shared",
            activeAccountOwner: "a@example.com", sessionOwner: "b@example.com")
        precondition(!V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: "b@example.com",
            teamOwner: staleActiveOwner), "B cannot adopt A's active account/team after B team fetch failed")
        let sharedOrganizationOwner = V3AuthIdentityBindingPolicy.resolveColdTeamOwner(
            storedTeamOwners: ["a@example.com"], activeTeamIdentifier: "team-shared",
            requestedTeamIdentifier: "team-shared",
            activeAccountOwner: "b@example.com", sessionOwner: "b@example.com")
        precondition(V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: "b@example.com",
            teamOwner: sharedOrganizationOwner),
            "the active B account may bind a shared organization team whose DB relationship stayed A")
        let inactiveStaleOwner = V3AuthIdentityBindingPolicy.resolveColdTeamOwner(
            storedTeamOwners: ["a@example.com"], activeTeamIdentifier: "team-other",
            requestedTeamIdentifier: "team-shared",
            activeAccountOwner: "b@example.com", sessionOwner: "b@example.com")
        precondition(!V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: "b@example.com",
            teamOwner: inactiveStaleOwner), "a nonactive A team never inherits B ownership")
        let mixedSnapshotOwner = V3AuthIdentityBindingPolicy.resolveColdTeamOwner(
            storedTeamOwners: ["a@example.com", "b@example.com"],
            activeTeamIdentifier: "team-a", requestedTeamIdentifier: "team-b",
            activeAccountOwner: "b@example.com", sessionOwner: "b@example.com")
        precondition(mixedSnapshotOwner == nil,
            "a conflicting/mixed owner snapshot cannot borrow the active B account")
        let coldMatchingOwner = V3AuthIdentityBindingPolicy.resolveColdTeamOwner(
            storedTeamOwners: ["b@example.com"], activeTeamIdentifier: nil,
            requestedTeamIdentifier: "team-b", activeAccountOwner: nil,
            sessionOwner: "b@example.com")
        precondition(V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: "b@example.com",
            teamOwner: coldMatchingOwner), "a unique cold Team.account owner matching B remains usable")
        precondition(!V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: "b@example.com",
            teamOwner: nil), "an account-less cold DB team with no unique owner fails closed")
        precondition(V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: "b@example.com",
            teamOwner: "b@example.com"), "a cold DB Team.account owner matching B is accepted")
        precondition(V3AuthIdentityBindingPolicy.normalizedOwner(" A@EXAMPLE.COM ") == "a@example.com")

        // An auth commit between session capture and team resolution invalidates the pair.
        precondition(!teamRequest(sessionOwner: "a@example.com", teamOwner: "a@example.com",
            before: 9, after: 10))
        precondition(!teamRequest(sessionOwner: "a@example.com", teamOwner: "a@example.com",
            before: 10, after: 10, cancelled: true))
        precondition(!V3AuthIdentityBindingPolicy.mayProjectIdentity(generationBefore: 11, generationAfter: 12),
            "a snapshot spanning an account switch cannot project either identity")
        precondition(networkCalls == 3, "switch and cancellation must not dispatch")
        print("V3_AUTH_IDENTITY_BINDING_PASS")
    }
}
