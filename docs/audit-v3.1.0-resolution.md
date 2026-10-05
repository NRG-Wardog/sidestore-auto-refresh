# v3.1.0 audit resolution and evidence map

The audit reviewed released builder `651587433eb7142089509e558dcfba9eb69d0470`.
This work preserves the later fixes from `54eb199044311193dc5a70d39f4c646218804336`
and applies the remaining validated source corrections. Dependency revisions
remain pinned; no release, tag, account reset, certificate revocation, or issue
closure is part of this source/build change.

## Code findings

| Finding | Source disposition | Regression evidence |
| --- | --- | --- |
| D01 local credential commit misclassified as Apple auth | Typed local storage failures, safe user guidance, no automatic auth replay | `test_v3_account_diagnostics.py`, `test_keychain_recovery_transactions.py` |
| D02 provisioning diagnostics lose type/code/stage | Owned operation stages, typed safe causes, actual associated server codes, bounded wire/diagnostic propagation | `test_v3_account_diagnostics.py`, existing typed-guidance/portal suites |
| D03 incomplete provisioning without live session has no recovery | Explicit same-account reauthentication through the existing operation; strict DSID/stamp/owner checks; capability negotiation | `test_v3_auth_recovery_audit.py`, generated reauthentication and whole-store/button/wire harnesses |
| D04 Finish Later suppresses subsequent recovery | Dismissal is attempt-scoped and does not hide a newly observed provisioning problem | `test_v3_auth_recovery_audit.py` |
| D05 account row incorrectly proves completion | Completion evidence comes from full provisioning and verified local activation; durable binding survives valid restarts and is invalidated before a new attempt | `v3_provisioning_completion_harness.swift`, `v3_provisioning_snapshot_harness.swift` |
| D06 token-only legacy identity cannot bind | Apple verifies the legacy route; resolved identity is compare-and-committed without inventing a password or blending namespaces | `test_keychain_generated_auth_recovery.py`, `test_keychain_recovery_transactions.py` |
| D07 markerless selected namespace is inaccessible | Non-destructive recovery candidate; identity verification before adoption; signed-out/unknown markers remain authoritative | Same production Keychain fault/recovery suites |
| D08 missing UUID produces synthetic Cocoa 3587 | Existing later producer fix retained; launch observations stay distinct from real OS errors | `test_v3_preserved_launch_quota.py`, launch diagnostics suites |
| D09 synchronous IPA staging blocks MainActor | Detached coordinated copy, attempt-owned results, responsive cancellation, private partial files, cross-process leases and bounded orphan cleanup | `test_v3_async_ipa_staging.py`, actual coordinated-copy/host-race/lease harnesses |
| D10 App ID result 9120 lacks typed mapping | Existing registration-handler mapping retained and included in build diff allowlist; no unrelated NSError-number inference | `test_v3_preserved_launch_quota.py`, portal failure and quota suites |
| R01 certificate activation precedes verified storage | Confirmed in generated pinned CertificateManager; checked certificate pair/imported-copy transaction, readback/import validation before active state | `test_keychain_recovery_transactions.py`, pinned CertificateManager generation/idempotence |
| R02 failed account/team activation is swallowed | Confirmed in generated SignInOperation; errors propagate, rollback and independent committed-state readback, durable reconciliation journal | `test_v3_account_diagnostics.py`, generated activation fault harness |

Uncertain outcomes remain explicitly held across restarts. Local reconciliation
compares original/intended durable state; it does not replay Apple requests.
Retained journals remain authoritative even if a marker was already published.
Certificate creation/revocation is blocked while saved signing state is uncertain,
including a second check after asynchronous lookup; local reconciliation remains
available. See `test_v3_certificate_storage_admission.py`.

## Additional validated defects and build repairs

- Repeated guest background lock scans leaked allocated process information and
  file descriptors. The pinned generated scanner now frees/closes them on every
  checked path, including descriptor zero and failed lock inspection. The emitted
  C probe fails on the baseline and passes after the patch.
- Private-runtime CFBundle lookup contained unbounded instruction scans and
  unchecked adjacent/branch reads. Four guard-page failures now reproduce on the
  original implementation and pass a bounded, checked-read implementation. Existing
  supported layouts remain unchanged; unsupported/read/write failures return an
  explicit startup error. This is not a claim of full iOS 27 device acceptance.
- Deleted upstream download locations prevented any native build. The owner's
  LiveContainer fork supplies the identical commit/tree; the reviewed litehook
  mirror supplies its identical gitlink. Every recursive checkout must be pristine
  and identity-matching before dependency code executes.
- The packaging script inherited an unchecked downloaded dylibify executable.
  It now compiles reviewed vendored source, with bounded zeroed allocation, checked
  file I/O, failure propagation, input validation and exact output-byte validation.
  The former release binary's byte equivalence is not claimed.
- The SideSign changed-file gate omitted the already-required AppIDs transform;
  it now permits exactly that expected file too.
- New evidence inventory entries cover generated SignInOperation,
  CertificateManager, Dead10ccFix and NSUserDefaults. The v2 inventory remains
  compatible. Actual dependency locks and tool exit/version evidence are retained
  on failed builds as well as successful builds.

See [source/provenance review](audit-v3.1.0-source-provenance.md),
[input manifest](audit-v3.1.0-input-provenance.json), and
[converter provenance](../scripts/vendor/dylibify/README.md).

## Acceptance boundaries

The source-enabled Linux integration at the first published audit snapshot ran
957 tests: 804 passed and 153 explicitly skipped for native/platform facilities.
This is not the macOS gate. Each candidate must pass the workflow's strict
zero-unexpected-skip runner, native host/backend builds, package checks, and the
verification of the actual re-downloaded artifact. The candidate provenance
records its exact builder commit, workflow run, IPA hash and source/symbol hashes.
A green run for an earlier commit is not evidence for a later candidate.

The first native audit run, [37341898398](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/37341898398),
at `2859d969cc9c378279f4a30b3846016ae1051112` executed 966 tests with zero
skips and stopped on ten failures plus three setup errors. It exposed stale native
harness dependencies/assembly and a real readiness-envelope allowlist omission.
The test gate was not weakened. Later candidates must rerun the full native gate;
the first run did not reach iOS compilation or produce a candidate IPA.

- V01: fixes are published on a dedicated branch and must be built into a new IPA
- V02: source/unit/native/package/post-resign/device evidence remain separate
- V03: build-time entitlements do not prove an installer's final entitlements

None of D08/D10's improved diagnostics establishes that all launch failures or
all Apple rejections are eliminated. Real Keychain behavior, Apple/2FA delivery,
post-installer signing, device transport and long-running refresh require the
exact candidate on a device. Tests must preserve the user's existing installation
and data; deletion/revocation is not a default diagnostic step.

The [remaining issue matrix](audit-v3.1.0-acceptance.md) covers #30, #38, #40,
#33, #34, #35, #39, #32, #3 and #1. The auth/certificate findings above also map
to #42 and #31; D09/D10 plus the same-file installation checklist map to #37.
All thirteen issue tracks retain their explicit device acceptance requirements.
No issue is declared closed solely because this source branch or CI passes.
