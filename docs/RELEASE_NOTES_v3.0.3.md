# Unified LiveContainer + SideStore v3.0.3 candidate

This document tracks an unpublished release candidate. The candidate product
identity is v3.0.3-rc. No v3.0.3 release or final tag is created by this
work.

## Candidate fixes

- Stage a picked IPA immediately into private shared App Group storage. The
  service receives a UUID token, derives and validates the staging path, and
  removes the staged file after the operation is acknowledged.
- Bind every operation attempt to a unique generation and session. Retry
  waits for session-scoped cancellation before starting the next mutation.
- Accept a refresh result only after its expected installed-app result is
  present and verified. Terminal success, failure, and cancellation are
  write-once.
- Show scheduler-backed Refresh All states from start through verified
  completion or failure, with safe diagnostics and duplicate-tap protection.
- Apply Start Dock Collapsed at the actual first-frame boundary for each fresh
  multitasking session. A user's toggle remains in effect for that session.
- Place Reload Status on its own full-width row below the service summary.
- Keep invalid credentials, app-specific-password requirements, verification
  codes, rate limiting, Anisette, network, account repair, provisioning,
  signing, installation, and unknown errors distinct.
- Disable SideSign and AnisetteKit log output at the embedded logging sink so
  authentication identifiers, headers, verification responses, and raw error
  bodies cannot reach Copy Logs.
- Restore failed Boolean, String, and Int settings writes from authoritative
  state without allowing an older failure to roll back a newer write.
- Defer post-mutation status reloads until the operation and presentation
  guards are clear.
- Deduplicate the two Dead10cc background notifications within one lifecycle
  transition, while preserving the original guest-process scope.
- Prevent repeated prompt responses and bound completed operation and
  authentication session retention.
- Keep one source-catalog Update action, gated by SideStore's hasUpdate.

## Physical-device acceptance remains open

- #30: update an installed old source version and verify the installed new
  version.
- #32: deliver and submit trusted-device, SMS, and voice verification on a
  real Apple account.
- #35: reproduce and verify the affected user's refresh result.
- #37: install a known-valid IPA successfully from the staged picker flow.
- Verify Start Dock Collapsed across fresh sessions, including manual toggle
  and rotation.
- Review Reload Status on narrow phones and tablets.
- Verify Refresh All's immediate states and terminal diagnostics on device.

CI evidence validates the candidate build and package only.

**CI GREEN != DEVICE VERIFIED.**
