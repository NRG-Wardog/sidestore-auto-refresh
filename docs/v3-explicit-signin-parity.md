# Explicit sign-in parity with original LiveContainer

## Scope and source evidence

This change restores the meaning of an explicit manual sign-in attempt: request
credentials for that attempt before contacting Apple with any saved account.
It is a control-flow correction, not proof of the cause of the reported device
failure between password submission and the first 2FA prompt.

Original LiveContainer's SideStore revision
`12a496ca1c766a102193634879823d16610bf1cd` calls `authenticationLoop()` directly
from `SignInOperation.startAuthentication`. Its history explicitly removed both
cached success and the silent credential prepass:

- [`4ef10090d437de2066fb72458d459b7837be130b`](https://github.com/SideStore/SideStore/commit/4ef10090d437de2066fb72458d459b7837be130b):
  “fix: removed cache returning in sign in flow coz sign-in is explicit action”.
- [`2a2ea867cd942b78d0a05b4a972a6026474b3175`](https://github.com/SideStore/SideStore/commit/2a2ea867cd942b78d0a05b4a972a6026474b3175):
  “ditch silent sign-in coz we dont need it anymore, we use proper explicit sign
  in since sign-in flow doesnt require auto sign in anymore”. This commit removes
  `silentSignIn()` and replaces its selection branch with `authenticationLoop()`.

The combined builder still pins SideStore
`ff25922e5c13ccfafd83bda5092910d848ebd409`, whose operation retains the prepass.
The headless service already sets `v3RequireFullProvisioning` to prevent the
cached-success shortcut, but previously retained token/password pre-authentication.

Before presenting manual credentials, that prepass can fetch Anisette, verify a
stored token, write verified authentication, or send a stored password and request
2FA for its identity. Existing identity checks reject mismatched saved identity
commits. This audit does not assert that cross-account persistence corruption
occurred, or that these extra requests caused the reported unknown failure.

## Narrow implementation

`SignInOperation.v3RequireInteractiveCredentials` defaults to `false` and is
supplied as `true` only by `V3AuthCenter.run` for `.interactive` sessions.
When true, it excludes both cached-success reuse and `silentSignIn()`.

- Ordinary background/default callers retain their existing saved-token and
  saved-password recovery behavior.
- `.resumeProvisioning` retains its independent existing-session branch, with
  the same owner/session checks and no new credential prompt.
- `.reauthenticateProvisioning` retains its explicit same-owner flow and existing
  owner, identity-stamp, and returned-DSID checks.
- The credential loop does not call `silentSignIn()`, including after a rejected
  submitted account. A subsequent manual attempt asks for credentials again.
- No account reset, keychain deletion, certificate revocation, provider switch,
  or relaxed identity check is introduced.

## Credential-to-2FA ownership audit

The original and pinned SideSign authentication implementations are identical:
`Authentication.swift` has Git blob
`f60d4c67dcca0f093dc3ac949fdd0492ef7431a2` in both original SideSign
`df2b8e4257454f0c7629276d409d6e9d7953fdf6` and pinned
`a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d`.
The typed 2FA retry patch changes retry error representation, not the initial
`au` dispatch or callback order.

SideSign recognizes its six supported `au` values and invokes the supplied
verification handler with `selectDeliveryMethod` before sending a 2FA delivery
request. The operation passes the callback directly to the headless handler.

The headless `ask` allocates a fresh prompt UUID and its `defer` retires the
matching prompt before `credentials()` returns. Therefore the proposed race in
which a resumed credential continuation enters the next verification callback
before its predecessor's cleanup is not supported by this source. The sole
`V3Prompt/1` rejection is reuse of an identical UUID in `V3PromptCenter`, not an
existing prompt in the same session. Cancellation produces `CancellationError`;
the auth watchdog owns the distinct timed-out terminal.

New prompts increment the service revision. Host poll policy accepts an advancing
revision with a different prompt ID; duplicate earlier answers return current
state and cannot settle the next continuation. None of these paths demonstrates
that an Apple 2FA callback was actually received on the affected device.

## Regression coverage and limits

`test_v3_explicit_signin.py` generates operation methods from the pinned source
through the real service and embedded-keychain patches. Its native fixture runs
those exact `startAuthentication`, `silentSignIn`, `authenticationLoop`, `signIn`,
and reauthentication-validation methods with the production keychain adapter,
in-memory platform storage, and isolated Apple/input/provisioning IO doubles.
Scenarios cover manual valid/invalid saved routes, a different submitted owner,
cancellation, rejection without saved-account fallback, repeated manual attempts,
legacy token/password fallback, and same-account reauthentication owner/DSID checks.

The existing generated execute/provisioning harness also verifies that the new
flag bypasses cached success independently of the full-provisioning flag and that
provisioning resume never prompts for credentials.

A separate fixture extracts the actual prompt center, headless credential and
verification methods, answer/poll methods, and host revision policy. It exercises
an immediate verification callback, late duplicate credential answers, and
cancellation before/after the 2FA prompt. Provider types are doubles; this is a
continuation/ownership regression, not an SRP, network, or Apple-authentication test.

Linux checks validate generation, extraction, patch idempotence, and source
contracts. Native Swift compilation/execution, the actual iOS build, and a real
device attempt remain required. This environment has no Swift compiler or Apple
SDK. No Apple authentication or credential transmission was performed here.

Local validation on 2026-10-06: the complete Python suite ran 1,075 tests
with 167 documented platform/source skips and no failures. The v3-only subset
ran 730 tests with 145 skips and no failures. The two new Swift execution tests
were among the skipped tests; their source assembly completed before skipping.
Independent static review found and corrected a missing extracted
`V3AuthReadStampPolicy` dependency in the native fixture. Native validation is
still pending, not inferred from these Python results.
