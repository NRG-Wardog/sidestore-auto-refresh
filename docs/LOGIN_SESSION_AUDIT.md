# Team-list session rejection audit

## Scope and evidence

The reported audit-build failure is a typed `SideSign.ServerError.underlyingError`
with Apple result `1100` at `fetchTeams`, before team saving, certificate fetching,
or device registration. Swift's bridged error number `3` is not Apple's result.
[AltStore's official error guide](https://faq.altstore.io/altstore-classic/error-codes)
describes Apple developer result 1100 as an expired session. That identifies a
rejected portal session, not a wrong password, missing certificate, or outage.
It does not prove why this particular device's session was rejected.

## Original LiveContainer versus this build

Original LiveContainer at `12377cf3b91d51739a33f14a302e5f522b238593` does not
implement a separate Apple login protocol. Its `.github/build_github.sh` embeds
the nightly IPA from `LiveContainer/SideStore`. The original fork inspected for
this audit is `LiveContainerSupport` at
`12a496ca1c766a102193634879823d16610bf1cd`; this is source comparison, not proof
of which original IPA a particular device installed.

That fork's sequence is:

1. `SignInOperation.authenticationLoop` requests credentials.
2. `signIn` fetches Anisette data, resolves Xcode version, and calls
   `AuthManager.signIn` / `DeveloperPortalProxyWithAuth.signIn`.
3. SideSign performs GrandSlam authentication, optional 2FA, app-token retrieval,
   then `fetchAccount` (`viewDeveloper.action`) using the resulting session.
4. `SignInOperation` saves DSID, Xcode token, email, and password to keychain,
   and notifies the sign-in handler of authentication success.
5. `fetchTeam` calls `DeveloperPortalProxy.fetchTeams`. The proxy obtains a
   session using saved DSID/token and fresh Anisette data, then calls
   SideSign's `listTeams.action`.
6. Only after a team is returned does the flow save the account/team and begin
   certificate/device provisioning.

This build's pinned SideStore `ff25922e5c13ccfafd83bda5092910d848ebd409`
retains an older silent-token/saved-password prepass, retryable provisioning
loop, and cached-session fast path. Our patches constrain those paths with
identity checks and explicit reauthentication, replace UI callbacks with
headless prompts, and verify credential persistence transactionally. The
network methods, token selection, DSID source, endpoint, HTTP parameters and
headers remain upstream. The original fork's SideSign pin
`df2b8e4257454f0c7629276d409d6e9d7953fdf6` and this build's
`a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d` have identical
`Authentication.swift`, `DeveloperPortalAPI.swift`, `Teams.swift`, and
`Constants.swift` content. No endpoint, header, cookie or token substitution is
part of this correction.

## Introduced storage defect

Upstream reads both auth credentials and Anisette `identifier` / `adiPb` from
one keychain client. Our shared-keychain adapter had an overly broad read-only
legacy fallback intended for certificate-only installations:

- Before the selected namespace's authentication-ready marker existed, missing
  non-auth keys could be borrowed from the legacy namespace. This unintentionally
  included Anisette identity and ADI data.
- Successful authentication committed the four auth keys and the ready marker,
  but did not migrate those independently borrowed Anisette keys.
- The subsequent fresh-Anisette fetch stopped seeing the borrowed values. A
  missing identifier could generate a new device identity between the successful
  account lookup and the team request. Partial selected state could also be mixed
  with a legacy key from a different identity.

The correction restricts that independent fallback to its intended certificate
keys. Complete, coherent account migration still carries the complete supported
snapshot, including Anisette keys. If no such migration is possible, Anisette
uses the selected namespace before authentication starts; committing auth state
cannot switch its source namespace. Legacy values are not deleted.

This is a source-proven reachable defect and a plausible explanation of the
observed boundary. No private device storage was inspected, so attribution to
the reported device remains unconfirmed.

## Recovery and verification limits

A typed team-list 1100 now explains the rejected session in-app and directs the
user to explicit sign-in again. It does not suggest inspecting certificates or
retrying the same rejected token. Finishing the prompt preserves the typed
failure and retires only the process-local session; stored account information
and certificates remain intact.

Synthetic regression fixtures cover legacy-only and signed-out namespaces,
partial identity state, complete migration, typed result discrimination, and
non-destructive recovery. Python/source checks run locally. Native Swift tests,
iOS rendering, and the complete build run in the required macOS CI gates; local
Linux source checks cannot prove those passed. A successful device login and
team fetch are still required before calling this device issue fixed. No Apple
account request, certificate creation/revocation, or real credential test is
performed by these fixtures.
