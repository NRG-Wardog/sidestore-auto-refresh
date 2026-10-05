# Account diagnostics and local persistence audit fixes

Scope: audit D01, D02 and R02 against pinned SideStore
`ff25922e5c13ccfafd83bda5092910d848ebd409`.

## Changes

- A credential-commit failure is a typed local-storage outcome after Apple
  authentication. It terminates the credential loop and silent-password fallback
  without retaining credentials for replay or clearing the existing account.
- Local storage failure and unconfirmed storage have distinct UI kinds. Neither
  is rendered as an incorrect Apple password. Keychain code 1009 is deliberately
  called validation failure: the producer also uses it outside read-back, so it
  is not proof that a particular read-back failed.
- Operation-owned stages cover authentication, credential commit, team fetch,
  account save, certificate fetch/activation, device registration and final
  account activation. Typed errors bypass description parsing. The actual
  `ServerError.underlyingError` associated result code remains separate from its
  Swift/NSError bridge code. Unobserved HTTP status remains unavailable.
- The certificate obtained from Apple is retained during a local activation
  retry. The same certificate is saved again; a retry does not repeat remote
  certificate fetch/creation.
- The generated finalizer no longer swallows account/team activation errors.
  Failed context changes roll back. A fresh context verifies committed active
  identities after save; derived defaults are updated only after a successful
  Core Data save.
- Activation has a local pending journal written before database mutation.
  Reconciliation clears it only when an independent committed-store read matches
  the exact prior or intended active identity set. A mixed/unreadable result or
  malformed journal remains blocked. Journal contents never enter diagnostics.

## Integration interfaces

`V3AccountOperationError.requiresReconciliation` identifies genuinely uncertain
Keychain/DB outcomes, distinct from verified rollback. Keychain pending markers
are owned by the Keychain transaction implementation.

`V3AccountDatabaseRecovery.requiresReconciliation` exposes a durable DB hold.
The explicit local repair action calls `v3ReconcileAccountDatabaseStorage()`.
Reading a normal status snapshot does not clear this journal or prove full
provisioning completion. Identity/completion gates remain independently required.

## Verification and limits

`test_v3_account_diagnostics.py` verifies the prepared, patched pinned
`SignInOperation`, patch idempotence, persistence ordering and operation stages.
Its executable Swift harnesses exercise the production credential-commit block,
auth loop, provisioning loop and finalizer with injected failures. They also
round-trip diagnostics through the production wire decoder and copied UI
format, including hostile descriptions and associated server codes, plus durable
journal reload, rollback/commit matching and malformed/mixed-state rejection.

On the Linux development host the full Python suite passed: 910 tests, 140
platform-dependent skips, with both pinned source checkouts supplied. No Swift
compiler or Apple SDK is available there. macOS CI must execute the Swift
harnesses and the actual iOS build. Physical-device Keychain entitlement,
Core Data disk-failure/conflict, upgrade, restart and end-to-end UI acceptance
remain required; the source/harness results do not claim those passed.
