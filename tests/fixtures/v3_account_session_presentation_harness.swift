@main
struct V3AccountSessionPresentationHarness {
    static func main() {
        let staleIdentity = V3AccountSessionPresentationPolicy.resolve(
            authenticated: false,
            account: "saved@example.com",
            team: "Personal Team",
            certificate: "Active certificate available")
        precondition(staleIdentity.showSignIn && staleIdentity.showSavedAppleID &&
                     staleIdentity.showUnverifiedSavedState && staleIdentity.showSignOut,
                     "a saved identity with unreadable credentials must expose Sign In, stale-state notice, and explicit Sign Out")

        let noSavedState = V3AccountSessionPresentationPolicy.resolve(
            authenticated: false,
            account: "Not signed in",
            team: "No active team",
            certificate: "No active certificate")
        precondition(noSavedState.showSignIn && !noSavedState.showSavedAppleID &&
                     !noSavedState.showUnverifiedSavedState && !noSavedState.showSignOut,
                     "with no saved account state, show Sign In only")

        let activeTeamWithoutAccount = V3AccountSessionPresentationPolicy.resolve(
            authenticated: false,
            account: "Not signed in",
            team: "Personal Team",
            certificate: "No active certificate")
        precondition(activeTeamWithoutAccount.showSignOut &&
                     activeTeamWithoutAccount.showUnverifiedSavedState,
                     "stale local team state must preserve explicit sign-out recovery")

        let authenticated = V3AccountSessionPresentationPolicy.resolve(
            authenticated: true,
            account: "saved@example.com",
            team: "Personal Team",
            certificate: "Active certificate available")
        precondition(!authenticated.showSignIn && !authenticated.showUnverifiedSavedState &&
                     authenticated.showSignOut,
                     "a verified signed-in state keeps the existing Sign Out action")
        print("V3_ACCOUNT_SESSION_PRESENTATION_PASS")
    }
}
