# v3.1.0 audit: remaining issue acceptance matrix

Review date: 2026-10-05. Scope: issues **#30, #38, #40, #33, #34, #35, #39,
#32, #3 and #1**, alongside the separately integrated D01–D10/R01–R02 work.
This document does not close any issue or declare a candidate device-accepted.

## Exact review identity and proof levels

- Published v3.1.0 builder: `651587433eb7142089509e558dcfba9eb69d0470`
- Source-review baseline: `54eb199044311193dc5a70d39f4c646218804336`
- Additional #33 source fix: `309be4a9417cf6da7211148cdf48fdd678652ec8`
- LiveContainer pin: `12377cf3b91d51739a33f14a302e5f522b238593`
- SideStore pin: `ff25922e5c13ccfafd83bda5092910d848ebd409`
- SideSign pin: `a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d`

Source locations below name builder templates/functions or exact upstream files,
not line numbers in a different generated artifact. The original clean pinned
checkouts were not mutated. The additional fix was applied to disposable copies
and replayed byte-identically. See [source provenance](audit-v3.1.0-source-provenance.md)
and [input identity inventory](audit-v3.1.0-input-provenance.json).

The final full Python suite on this review branch at `309be4a` ran **911 tests:
776 passed, 135 skipped, zero failures/errors**. The 135 skips require unavailable
Swift, macOS Foundation/Core Data/Darwin, Clang++ or Xcode/UI facilities on this
Linux executor. This is a partial automated result, not the required macOS gate.
The #33 focused suite ran **12 tests with zero skips**, including C execution.
`git diff --check` passed. Later integration changes require a new full run.

| Acceptance stage | Result for this review branch |
| --- | --- |
| Source reviewed | The ten issue tracks below were inspected; this is not an all-lines audit |
| Source fixed | Additional resource-lifetime defect in #33; other tracks retain their existing paths pending concrete failing evidence |
| Unit/source checks | Partial pass: 776 passed, 135 explicitly skipped; #33 all 12 passed |
| Native built | **Not run** here; needs macOS/Xcode with all required tests enabled |
| Packaged | **Not produced** here; verifier unit fixtures are not an IPA build |
| Post-resign verified | **Not run**; requires actual host/LiveProcess entitlements after the chosen installer signs them |
| Device accepted | **Not run** for any row below |

## Issue-by-issue source checks and remaining tests

Test counts here refer to named modules in the review run. Some shared modules
cover more than one row; do not add them together as independent test totals.

| Issue | Source check and disposition | Local regression evidence | Required device postcondition |
| --- | --- | --- | --- |
| [#30](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/30): source updates | Retain `V3AppActions` and the service's authoritative `InstalledApp.hasUpdate`. Catalog returns the installed object's URI; `update` selects `storeApp.latestSupportedVersion` and forwards `customBundleIdentifier` to the existing pipeline. No additional proven defect in this inspected path | `test_v3_source_updates`: 18 passed; includes checks against exact pinned `InstalledApp.swift`. These are source assertions plus a small version-ordering model, not a real Core Data update/install | A source version increase becomes visible after refresh in Apps and catalog detail; Update preserves custom ID and data; actual installed version advances |
| [#38](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/38): source persistence/catalog | `headless_app_manager_source_mutations` preserves one AppManager persistence core, independent-context duplicate check, save and notification. `sourceAddConfirmed` verifies a fresh context and returns authoritative rows. Missing source is distinct from a valid empty catalog. Missing-source install recovery now carries the original URL separately from the lossy normalized identifier, checks preview/persisted identity, and falls back to manual Sources recovery for older replies. Do not restore the old same-context duplicate check | `test_v3_source_backend_migration`: 3 passed, 2 skipped (Swift/Core Data); shared source/certificate repair: 9 passed; catalog failures: 30 passed | Confirm add → catalog → kill/relaunch keeps exactly one source and its apps; duplicates do not add another row; malformed/unreachable source/save failure cannot appear as success |
| [#40](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/40): cancel Add Source | Keyboard Cancel restores the pre-edit value; form Cancel closes and invalidates preview ownership; `V3SourcePreviewSession.mayApply` requires current generation, URL and presented form. Existing UI/actions retained | Source/prompt setup: 1 passed, 2 Swift skips; async-owner interleavings: 1 Swift skip. UI accessibility has not been exercised | Cancel/Close/Back usable with keyboard open/closed on iPhone/iPad; no add dispatch; URL A late reply cannot repopulate URL B or a closed/reopened form |
| [#33](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/33): guest background termination | Both existing background observers and transition gate retained. **New confirmed source defect:** scanner retained its `pidinfo` allocation and opened file descriptors on repeated background scans. Narrow builder fix now frees/closes them, guards allocation failure, accepts every valid FD, and fails closed on drift | `test_dead10cc_fix`: 12 passed, zero skips. Exact pinned file + patch replay; old prepared-tree upgrade; actual emitted C descriptor block fails the old ownership test and passes the corrected one | Original affected guest survives the allowed background scenario without growing scan-owned descriptors/memory; compare PID and OS termination reason; legitimate cold launch retains durable data |
| [#34](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/34): settings persistence | Host settings and Guest Return use the shared app-group suite. Pinned `NSUserDefaults.m` redirects guest preferences through guest `HOME` and `lcGuestAppId`; this patch does not change that hook. No single proven root cause across reported host/guest failures | Settings persistence: 12 passed (source contracts); guest return: 13 passed, 3 Swift skips. No actual preference roundtrip or guest isolation proof here | Host plus guests A/B retain separate sentinels after normal background, process termination and cold relaunch; no cross-guest leakage |
| [#35](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/35): intermittent refresh | Scheduler result admission/terminal ledger, manifest correlation, host-result bridge and startup reconciliation retained. D08 and auth/persistence fixes are related boundaries, not a proven common cause. No blind replay added | Refresh-result bridge: 3 passed, 1 Swift skip; combined-refresh contract: 5 passed, 4 Swift skips; scheduler runtime: 2 Swift skips; network preflight: 5 passed | Success → disconnect → reconnect → background → service restart → explicit/manual or scheduled refresh settles with verified new expiry or a specific failure; no queued/admitted-as-success result or duplicate unknown mutation |
| [#39](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/39): JIT-Less guest signature | `V3JITLessStatusReader`, canonical LC certificate import/diagnose and generation-owned results remain distinct from SideStore active-certificate state. Native facts use the existing ZSign parser. R01 separately addresses local certificate persistence | Native-readiness: 2 passed, 2 skips; cancel ownership: 2 passed, 1 skip; import ownership: 5 passed, 2 skips; native certificate observation: 2 passed, 1 Clang++ skip | Reported guest signs and launches with expected team/fingerprint/key and fresh observation; mismatched/stale/expired/revoked copy is distinguished from invalid guest signature |
| [#32](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/32): 2FA delivery | `V3HeadlessAuthHandler.verificationCode`, delivery selection and `patch_sidesign_2fa_state.py` retain upstream-owned trusted-device/SMS/voice requests, validated phone selection and prompt/session ownership. No automatic resend loop introduced | `test_v3_2fa`: 16 passed (source contracts); prompt transport: 2 Swift skips; prompt selection: 1 passed, 1 Swift skip | Actual trusted-device notification, SMS and voice delivery tested separately; wrong/expired code, resend/change method, cancellation and account change reject stale answers |
| [#3](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/3): cellular-only | Explicit current support limitation: `LiveContainerNetworkPreflight.check` requires a satisfied Wi-Fi path before tunnel use, and a tunnel interface alone is not CoreDevice readiness. No transport rewrite justified by a successful Apple login | Network preflight: 5 passed; VPN handoff: 2 passed, 1 Swift skip | Current supported behavior is clear Wi-Fi-required guidance. Cellular support remains unverified until an authorized transport experiment proves Lockdown → CoreDevice → CDTunnel → RSD → install with Wi-Fi off |
| [#1](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/1): compatibility | Tracker, not one defect. Inspect final build identity, selected runtime groups, package contracts and actual installer result separately. Do not extrapolate a standalone device result to combined v3 | Candidate verifier: 37 passed; build evidence: 10 passed, using fixture artifacts. This does not mean any real candidate was built or accepted | Each device/OS/network/installer/variant combination has its own exact-IPA result and recorded post-resign evidence |

### #33 additional defect: exact source evidence and fix boundary

In pinned `LiveContainer/Tweaks/Dead10ccFix.m`,
`_lock_lockedFilePathsIgnoring:` allocates `pidinfo` with `malloc` and reaches its
return without `free`. Its non-SQLite branch opens a descriptor, then either
continues on `F_GETLKPID` failure or inspects the returned lock; neither path
closes that descriptor. `_terminateWithStatus:` repeats the scan after two
seconds while backgrounded. This proves repeated resource retention along
those paths; it does not prove that a reporter's termination was caused by it.

`scripts/patch_dead10cc_fix.py::patch_resource_lifetimes` now releases the list
before lock processing, returns safely on allocation failure and closes each
successfully opened descriptor immediately after `fcntl`, on both success and
failure. A valid descriptor 0 or 1 is no longer discarded without closure.
No new observer, network request, keepalive, database reset or guest-container
change is added. Existing guest-only scope and duplicate-transition gating stay
intact. OS lock discovery/private APIs are still device-dependent.

The C test extracts the emitted `open`/`fcntl`/`close` block unchanged and uses
syscall doubles. A pinned-source assertion proves it is exactly the production
block. It covers open failure, descriptors 0/1/2/42 and `fcntl` success/failure.
The old block returns the harness's resource-leak failure, while the corrected
block returns success. Allocation cleanup is source-order checked; the full
Objective-C scanner was not executed on Linux.

## Device test execution cards

### iOS 27 compatibility follow-up: bounded CFBundle lookup

The pinned LiveContainer already includes iOS 27-specific paths; absence of a
27.0 check is not the source defect. Existing adaptations include the TBZ-based
`CFBundleGetMainBundle` lookup, executable-path cached length in `DyldConfig`,
segment-count handling before `dlopen`, the dyld version-map symbol and scene
APIs. The original lookup comment specifically refers to observation on iOS 27
developer beta 1. No source or device evidence establishes that the instruction
layout changed in 27.0.1 or caused the reported extension activation failure.

**Additional source-proven defect:** both loops in
`LiveContainer/LCBootstrap.m::overwriteMainCFBundle` are unbounded. Their matched
branches read the preceding instruction, adjacent instruction or decoded branch
target before proving that it is readable. The existing ADRP/LDR emulator checks
the opcode/register pattern only after those unchecked reads. The final assert
cannot protect the earlier reads or a never-matching loop.

`scripts/patch_cf_bundle_scan.py` now bounds the existing instruction patterns
to 256 words, uses checked Mach reads, and rejects out-of-window predecessor/
load targets, unreadable memory and invalid decoded storage. The resolved slot
must contain the current CF main bundle before the caller changes NSBundle
identity. An unsupported/unreadable pattern returns an explicit startup error.
The later cache write is kernel-checked and returns failure instead of directly
dereferencing an unchecked pointer; VM protections are not relaxed. This is a
finite safety budget, not a newly inferred instruction layout or proof that all
valid future functions fit that budget.

`test_cf_bundle_scan` executes the original pinned loops and the emitted helper:
the original faults against protected guard pages for no pattern, TBZ at the
first/last word and an out-of-window legacy branch; the corrected helper returns
failure in all four cases. Positive existing-layout, invalid opcode/register,
unreadable target and integer-bound cases also execute. A separate adapter test
uses OS/NSBundle doubles to verify errors cannot be reported as launch success.
Patch replay is byte-identical and partial/changed source fails closed. These
tests do **not** run CoreFoundation/private system code on iOS. A write failure
after NSBundle mutation is reported, not represented as a rolled-back launch.
The existing legacy TBNZ decoder is retained verbatim; this is not generalized
ARM64 decoder verification. The local follow-up suite on baseline `2859d969`
plus this patch ran 961 tests: 811 passed, 150 platform-dependent skips, zero
failures/errors. All four focused scan/adapter tests passed without skips.
The adapter fixture replaces ObjC surfaces and Mach calls with C doubles;
it does not compile the real ObjC availability/bridge expressions.

Device acceptance remains blocked on the exact rebuilt/re-signed candidate:

- Exercise the existing legacy and iOS 27 paths on appropriate physical OS
  versions, including a controlled unavailable-pattern/read/write failure
- Verify no unsupported lookup writes a cache or reports guest startup success;
  after failure use a fresh process and confirm host/guest data remain intact
- Distinguish extension request UUID, PID, XPC connection, `appReady`,
  `serviceReady` and guest-bootstrap completion. Synthetic error 3587 correction
  and the CFBundle lookup operate at different boundaries
- Repeat affected guest launch, narrow/iPad keyboard flows, background expiry,
  offline interruption/recovery and result persistence on the same exact IPA
- Verify installed host/LiveProcess identities, provisioning and entitlements
  after installer re-signing. Do not wipe databases, add speculative entitlements
  or infer blanket TLS/Core Data incompatibility from an OS version

This is defensive compatibility hardening. It is **not** acceptance of iOS 27.0.1,
a confirmed repair for any reporter's launch failure, or justification for
replacing unattended scheduling with a foreground-user-triggered task API.

Run on a backed-up test installation or an authorized in-place upgrade. Preserve
the existing signing/bundle identity and user data. Account deletion, sign-out,
certificate revocation or clearing the database is not a diagnostic prerequisite.

1. **Update and source (#30/#38).** Use a controlled source with a known install
   version and a newer supported version. Include a custom-bundle-ID app and
   a version too new for the test OS. Add it, install the supported version,
   retain an in-app data sentinel, advance the manifest, refresh, and update
   once from Apps and once from catalog detail. Verify linked installed ID,
   actual version, unchanged custom ID/sentinel, and persistence after relaunch.
   Repeat add for duplicate handling; test a missing source, valid empty source,
   malformed manifest, unreachable URL and injected save failure separately.
2. **Source navigation (#40).** On narrow iPhone and iPad split-window layouts,
   start with a pre-existing URL, edit with the keyboard visible, press keyboard
   Cancel, reopen, and press form Cancel. Repeat during slow preview and after
   changing A to B. Navigate away/back. Confirm no mutation was sent, no late
   preview appeared, focus is dismissible and editing versus form cancellation
   restores the correct value.
3. **Lifecycle/preferences (#33/#34).** Record current guest PID, create one
   persistent sentinel per guest and host preference, and sample resource counts
   before/after repeated allowed background/foreground transitions. Distinguish
   `0xDEAD10CC`, jetsam/memory pressure, OS policy termination and planned cold
   relaunch. Reopen guests A/B and host separately after termination. Sentinels
   remain isolated even when a PID legitimately changes. Do not force iOS to
   retain a process using audio or other artificial background activity.
4. **Refresh recovery (#35).** Record one successful exact request and changed
   expiration. Lose network before dispatch, after dispatch and during result
   delivery in separate tests. Restore Wi-Fi/VPN and restart the service. A
   post-dispatch unknown result stays blocked until authoritative reconciliation;
   recovery must not repeat installation blindly. Verify waiter settlement,
   history, target manifest and expiry. Repeat from a real scheduled trigger
   with PC/USB disconnected; `REGISTER_PASS`/`SCHEDULE_PASS` alone are not enough.
5. **JIT-Less (#39).** Use the canonical setup/diagnose screen. Test matching,
   mismatched, absent, expired and stale certificate-copy observations. Cancel
   during import/validation, replace/remove the copy and deliver the old callback.
   Verify only the current identity wins. Sign and launch the specific reported
   guest, and record guest-signature validation independently of certificate OCSP
   status. Do not revoke a working certificate merely to simulate a test.
6. **2FA (#32).** With an authorized test account, separately request trusted
   device, SMS and voice. Verify selection reaches the intended available method
   and exactly the selected phone entry. Test successful code, incorrect code,
   expiry, manual resend/change method, Cancel, service restart and switching
   accounts. Delayed or duplicate replies must not satisfy a later prompt.
7. **Connectivity/coverage (#3/#1).** Record combined versus standalone variant,
   device model/OS, Wi-Fi network family, official LocalDevVPN state and installer
   route. First establish the supported Wi-Fi transport end to end. With Wi-Fi
   off, verify honest unsupported guidance; an Apple login on cellular is not a
   transport pass. Any separately authorized cellular experiment must establish
   reachability at each transport layer and verify actual refresh postconditions.

## Evidence to attach to each eventual acceptance row

- Full builder SHA, CI run URL, product/variant, IPA SHA-256 and byte size
- Final dependency/source hashes, final lockfiles and toolchain versions
- Host and embedded native-build results; required suite counts and all skips
- Package verifier result for that exact IPA and matching source/dSYM evidence
- Installer route and post-resign host/LiveProcess app-group/keychain entitlement
  compatibility, without uploading private provisioning material
- Device model, iOS version, coarse network family, foreground/background state,
  scenario steps, actual postcondition and pass/fail/blocked reason
- Minimal non-sensitive request/attempt correlation and stage markers; exclude
  Apple IDs, codes/tokens/passwords, pairing records, private keys, complete logs,
  exact private IPs and unnecessary device identifiers

Only advance a row to `device-accepted` after its postconditions have been
observed on the exact artifact. A patch commit, green fixture suite, native build
or UI success label cannot substitute for any later acceptance stage.
