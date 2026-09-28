@main
struct V3AccountSessionPresentationHarness {
    static func main() {
        let staleIdentity = V3AccountSessionPresentationPolicy.resolve(
            authenticated: false,
            activeAccountPresent: true,
            activeTeamPresent: false,
            activeCertificatePresent: false)
        precondition(staleIdentity.showSignIn && staleIdentity.showSavedAppleID &&
                     staleIdentity.showUnverifiedSavedState && staleIdentity.showSignOut,
                     "a saved identity with unreadable credentials must expose Sign In, stale-state notice, and explicit Sign Out")

        let noSavedState = V3AccountSessionPresentationPolicy.resolve(
            authenticated: false,
            activeAccountPresent: false,
            activeTeamPresent: false,
            activeCertificatePresent: false)
        precondition(noSavedState.showSignIn && !noSavedState.showSavedAppleID &&
                     !noSavedState.showUnverifiedSavedState && !noSavedState.showSignOut,
                     "with no saved account state, show Sign In only")

        let teamOnly = V3AccountSessionPresentationPolicy.resolve(
            authenticated: false,
            activeAccountPresent: false,
            activeTeamPresent: true,
            activeCertificatePresent: false)
        precondition(!teamOnly.showSavedAppleID && teamOnly.showUnverifiedSavedState && teamOnly.showSignOut,
                     "team-only local state must preserve explicit sign-out recovery")

        let certificateOnly = V3AccountSessionPresentationPolicy.resolve(
            authenticated: false,
            activeAccountPresent: false,
            activeTeamPresent: false,
            activeCertificatePresent: true)
        precondition(!certificateOnly.showSavedAppleID && certificateOnly.showUnverifiedSavedState &&
                     certificateOnly.showSignOut,
                     "certificate-only local state must preserve explicit sign-out recovery")

        let authenticated = V3AccountSessionPresentationPolicy.resolve(
            authenticated: true,
            activeAccountPresent: false,
            activeTeamPresent: false,
            activeCertificatePresent: false)
        precondition(!authenticated.showSignIn && !authenticated.showUnverifiedSavedState &&
                     authenticated.showSignOut,
                     "a verified signed-in state keeps the existing Sign Out action")
        print("V3_ACCOUNT_SESSION_PRESENTATION_PASS")
    }
}
