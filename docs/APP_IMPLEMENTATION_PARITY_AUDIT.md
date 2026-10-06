# Application implementation parity audit

Review scope: the complete user-visible flow inventory below, compared at function and owner boundaries. This is not a claim that every source line or every device/OS combination has been verified.

## Source identities and comparison rules

- Builder baseline: `33f66940391d7489431aa21ba193687c97b08ab8`.
- LiveContainer host: `12377cf3b91d51739a33f14a302e5f522b238593`, identical pinned tree supplied by `NRG-Wardog/LiveContainer`.
- Built embedded SideStore: `ff25922e5c13ccfafd83bda5092910d848ebd409`; SideSign `a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d`.
- Original LiveContainer-distributed SideStore source comparison: `LiveContainer/SideStore`, `LiveContainerSupport`, `12a496ca1c766a102193634879823d16610bf1cd`; its SideSign gitlink is `df2b8e4257454f0c7629276d409d6e9d7953fdf6`.
- Original LC `.github/build_github.sh` downloads a moving SideStore nightly. The source comparison above does **not** prove the contents of any previously installed IPA.

The primary question is whether the host adapter preserves the contracts of the pipeline it actually builds. Newer original-LC code is also inspected, but replacing a pipeline wholesale would require reconciling its newer models, fingerprints, profile management and customization operations. A difference alone is not proof that the integrated implementation is wrong.

Keep the upstream owner for guest storage/launching, SideStore Core Data, signing, provisioning, installation and sources. Keep the integration's bounded XPC DTOs, prompt/session ownership, checked secure-storage transactions, durable uncertain-outcome recovery and verified host-replacement contract. These are required boundaries, not redundant implementations to remove for size.

## Proven findings

| ID | Original contract and current defect | Correction / disposition | Verification |
| --- | --- | --- | --- |
| P0-01 | `UserCustomizationOperation.execute` treats a nil bundle-ID answer as cancellation. Headless “Use Default” returned nil. | Return `(initialBundleID, true)`; custom input uses upstream trimming/empty fallback; Cancel remains cancellation. | Production-adapter harness in `test_v3_install_prompt_parity.py`; native execution required. |
| P0-02 | `RemoveAppExtensionsOperation` returns empty `excessExtensions` on fresh installs; upstream customization still presents all target extensions. Headless skipped fresh/unchanged installs, offered only excess extensions and omitted main-profile reuse. | Offer all target extensions, both explicit keep policies, selected/all removal and Cancel. Only an empty target skips presentation. Preserve offered-ID validation and no automatic removal. | Production-adapter harness plus corrected extension and host-selection suites. Prior shortcut assertions were corrected, not used as proof of parity. |
| P0-03 | Upstream `customizeAppExtensions` is a computed free/no-team default; `autoFixAppGroupIDs` defaults true. Raw `bool(forKey:)` displayed false despite the actual pipeline reading true. | Project these two upstream typed getters; explicit saved values remain authoritative. | `test_v3_settings_effective_defaults.py` extracts actual upstream getters and production settings projection; covers unset/free/paid/explicit cases. |
| P0-04 | Virtual-window decorated initialization error/early Close can fail to settle `pidAvailableHandler`, while `LCAppModel.runApp` awaits it. | Narrow one-shot terminal callback and existing pending-start retirement; implemented and independently source-reviewed. | Baseline and patched Objective-C method harness; macOS execution required. Pending, already-cleaned and nil-controller Close paths are covered; late OS process termination remains a separate device boundary. |
| P0-05 | `SignInOperation` reads `resolveResign`'s Bool as completed re-signing. Headless returned true for a button tap without re-signing anything. | Explicit “Finish Sign-In” acknowledgement and later Refresh All/Test Refresh guidance; return false. No nested refresh or implicit install. | Actual auth-adapter harness, plus existing auth ownership/provisioning tests. |
| P0-06 | Home accepted any verification manifest with a run ID, including failed/incomplete results. | Require complete successful results and the matching authoritative completed scheduler ledger, with no active/uncertain/pending host replacement. Implemented and independently source-reviewed. | Production evidence-policy harness covers failed/partial/mismatched/pending/settled history. |
| P0-07 | Headless integer setting writes did not call upstream `syncMinimuxerBackendFromUserDefaults`; running transport caches retained previous values. Invalid explicit ports were silently stored although runtime falls back. | Validate nonnegative values and UInt16 port bounds; persist then refresh only the requested integer cache using upstream effective-value semantics, without activating a separately saved backend. Independently source-reviewed. | Actual production setter and upstream effective-cache mapping harness; bounds/default/unchanged-on-rejection cases. |
| P0-08 | Historical refresh success and a matching imported JIT-Less copy did not prove the currently installed host supports the active signing context after a certificate/account change. | Add a separate current local-host compatibility observation using retained `CodeSignValidator` decisions; parse off MainActor, bound/cache by public context and installed files, reject stale identity/revision, and require it on both setup surfaces. Older iOS uses a local-only read; paid signer mismatch is explicitly unverified, not a forced re-sign. | Actual upstream-validator/cache/stale-observation harness and wire tests. No portal revocation or exact historical signer proof claimed. |
| P0-09 | Backup/restore completion was rejected by host, active-operation and recovery admission while its pipeline waited. SideBackup failure discarded the host target; the service-local URL route could notify an uncorrelated completion. | Bind a one-shot session/nonce/action to the actual external step; preserve return routing on failure; validate both ingress paths against live ownership and dispatched journal; keep cancellation ownership and reserved control reply capacity. | Pinned patch/idempotence, actual producer/registry/admission harness, malformed/stale/replay/journal cases. Actual iOS external-app round trip still required. |
| P0-10 | Late settings reads overwrote newer displayed and confirmed values. Writes already pending before a read and out-of-order backend acknowledgements also escaped generation-only guards. | Track per-key pending tickets, reject old/in-flight bulk snapshots, and reconcile every superseded completion only after all same-key writes settle. Apply the same contract to the single boolean row, preserve direct-mutation tickets, and reject stale error presentation. | Actual store/row methods test both reply orders, two/three writes, failed readback/rollback, unrelated keys and balanced ownership. Native Swift/Combine execution required. |
| P1-01 | Unsupported-version prompt said “Proceed anyway,” but upstream true selects the last compatible older download. | Correct explanation and button to the actual compatible download; included with install semantics correction. | Production adapter checks exact offered version and decision. |
| P1-02 | `profileRow` uses reflection; upstream `ListedProvisioningProfile.bundleIdentifier` is computed and is never included by Mirror. The host expects this field and omits useful profile identification. | Pending typed DTO mapping; do not dump reflected nested values or secrets. | Source-proven; no fix/native proof yet. |
| P1-03 | Backend/EMP controls omit upstream restart semantics; backend is arbitrary text. Number-pad port input also relies on Return-only commit. | Pending validated controls, explicit save and truthful restart status; no automatic service kill or transport redesign. | Source-proven UX/operational gaps; no device proof yet. |

## Function/flow coverage matrix

| Flow | Upstream implementation | Integrated implementation / justified boundary | Result and remaining proof |
| --- | --- | --- | --- |
| Guest browse/import/launch | `LCAppListView`, `LCAppModel`, existing launch modes and LiveProcess | Original model/view remain; host tabs and layout decorate them | Retain upstream ownership. Virtual-window failure settlement is P0-04. Device launch remains required. |
| Guest Return/multitask | `AppSceneViewController`, `MultitaskWindowManager`, decorated scene controller | Per-window identity, retire-before-relaunch, direct-process restart vs LiveProcess minimize distinction | Keep race guards. Late process teardown and actual app background behavior require device evidence. |
| Guest data/settings | LC guest HOME/NSUserDefaults redirection | No guest preference-hook rewrite; host appearance/Return use shared app group | Keep isolation. Separate A/B guest persistence roundtrip remains unverified. |
| Local IPA installation | `AppManager.readAppMetadata`, `AnyApp`, `AppOperation.install`, `PipelineRunner` | Detached coordinated staging and token/lease validation before the same pipeline | Keep staging and cancellation ownership; P0-01/02 restore prompt contracts. |
| URL/catalog installation | `StoreApp`/`AnyApp`, download and install pipeline | Catalog identity and supported version resolution, bounded source recovery | Keep canonical/original source URL distinction and authoritative save checks. Real download/install needed. |
| Update | `InstalledApp.hasUpdate`, latest supported version, existing pipeline | Update preserves custom ID; catalog points to installed object URI | Retain; same-data update/device version proof remains required. |
| Refresh/manual/scheduled | SideStore pipeline/minimuxer; LC host replacement | Scheduler admission, exact-run manifest verification, host handoff/relaunch evidence | Keep no-blind-replay and unknown-outcome holds. P0-06 corrects Home's weaker evidence check. |
| Activate/deactivate/backup/restore/remove/delete | Existing `AppOperation` cases and `PipelineRunner` | Host deletion/deactivation blocked; deletion waits for native outcome plus authoritative absence | P0-09 repairs external callback admission/routing without abandoning backend ownership or callback-settlement reconciliation. Native device behavior remains required. |
| Sign-in/2FA/team/provisioning | SideSign and `SignInOperation`/`AuthManager`/portal proxy | Session-owned headless prompts and typed error/stage propagation | Current authentication comparison is in `LOGIN_SESSION_AUDIT.md`. Do not replace live token/session transactions with UI-only state. P0-05 fixes false re-sign claim. |
| Certificates/JIT-Less | `CertificateManager`, native LC import and ZSign validation | Checked certificate persistence; separate imported-copy readiness and active SideStore certificate | Preserve secure storage and generation ownership. P0-08 now separately observes installed-host local signing compatibility; imported-copy readiness and portal revocation remain distinct. |
| Sources/catalog | `AppManager.fetchSource/addSource/removeSource/updateAllSources`, Core Data | Headless presentation only; independent-context persistence verification and exact URL recovery | Retain existing corrected core. Add/cancel/kill/relaunch/device acceptance remains open. |
| Settings/transport | Upstream typed UserDefaults, ConnectionConfig, Minimuxer cache sync | Read-through backend ConnectionConfig avoids stale UI-model capture | P0-03/07/10; pending explicit restart/input UX P1-03. No alternate transport protocol introduced. |
| Developer data | `DeveloperPortalProxy`, typed models | Identity-stamped read DTOs and stale-reply rejection | Keep identity gating; P1-02 fixes projection quality separately. |
| Pairing/config/account import/export | Existing parser/managers plus checked secret handoff | Data-only opaque tokens, bounded validated files and transaction ownership | Keep safety boundary; actual import/export and post-installer entitlements need device proof. |
| UI/resources/package | Existing host SwiftUI and headless backend | SideStore visible UI excluded; required models, icons, widgets and runtime lookup retained | Separate measured size map. Do not delete dynamic assets/models or merge incompatible OpenSSL versions for an assumed saving. |

## Thirteen issue tracks

No issue is closed by this report. See `audit-v3.1.0-acceptance.md` and `audit-v3.1.0-resolution.md` for the original detailed acceptance cards.

| Issue | Current source disposition | Outstanding acceptance |
| --- | --- | --- |
| #42 authentication | Existing checked storage/staged diagnostics and current session-identity patch retained; P0-05 makes host re-sign messaging truthful | Actual Apple login, fetchTeams, provisioning and exact-IPA device result |
| #31 certificates/provisioning | Verified storage/activation and recovery ownership retained | Actual free/paid team, certificate and device registration paths |
| #37 install/file/quotas | Existing async staging and typed quota error mapping retained; P0-01/02 restore customization | Same IPA via file/URL/catalog, success/cancel/failure, extension choices and preserved data |
| #30 source updates | Retained authoritative update detection/version/custom-ID pipeline | Real source version bump and installed update |
| #38 source persistence/catalog | Existing independent-context save and original-URL recovery retained | Add/relaunch/duplicate/malformed-source behavior |
| #40 cancel Add Source | Existing form generation/URL/presentation ownership retained | Keyboard open/closed, close/back, late replies on iPhone/iPad |
| #33 guest background/launch | Resource-lifetime repairs retained; virtual-window completion repair P0-04 | Real guest background/close/relaunch; previously identified scanner cursor correction is explicitly platform-blocked and not implemented here |
| #34 preference/data persistence | Guest hook retained; effective setting projection P0-03, transport cache P0-07 and concurrent read/write truthfulness P0-10 | Host and two guest sentinels across cold relaunch; no cross-guest leakage |
| #35 intermittent refresh | Exact-run ledger/recovery/handoff retained; Home evidence P0-06 | Disconnect/reconnect/background/restart and verified expiry without duplicate uncertain mutation |
| #39 JIT-Less guest signing | Native cert observation/import and checked storage retained | Matching/mismatched/expired/revoked guest certificate and actual launch |
| #32 2FA | Trusted-device/SMS/voice retain upstream requests and validated prompt ownership | Real delivery, wrong/expired code, resend/change-method and cancellation |
| #3 cellular-only | Current Wi-Fi prerequisite remains explicit; no unproven transport bypass | Cellular is not claimed supported; needs separately proven full transport/install path |
| #1 compatibility | Exact pins/build/artifact/post-installer evidence required per combination | Per-device/OS/network/installer exact-IPA results |

## Verification and stage boundary

Integrated private source commits (on baseline `33f6694`): `2a85380`, `5a81918`, `ce86008`, `a698373`, `a0c7560`, `45c4d55`, followed by `68734a9`, `700e899`, `40683eb`, and `1b439db`. None of these corrections has been published or built into a verified IPA at the time of this report.

The final integrated source suite at `45c4d55` ran **1,015 tests: 853 passed, 162 explicitly skipped, zero failures/errors**, using the exact pinned embedded SideStore and LiveContainer source environment variables. `git diff --check` passed. This run precedes integration with the later auth harness-only commit `b6f243d`; the final publication candidate must rerun after that integration.

At the effective-defaults commit, source-enabled Linux testing ran 1,000 tests: 843 passed, 157 explicitly skipped; no failures/errors. At the install-parity commit, the corresponding run was 1,002 tests with 157 skips and no failures/errors. These are **not** the final combined macOS gate, and native Objective-C/Swift/UI checks were not executed here.

Final reviewed source integration at `1b439db` ran **1,034 tests: 867 passed, 167 explicit native/platform skips, zero failures/errors**. Both patch-registration merge conflicts were resolved by preserving the validator and backup transforms and their verification registrations. Exact modified launch, validator and backup producer/consumer sources are included in package evidence.

Before the P0 checkpoint: integrate independently reviewed corrections, run the final full pinned-source suite and strict macOS zero-unexpected-skip gate, build/package the exact commit, verify the re-downloaded IPA and preserve its hash/artifact. Report remaining platform/device holds explicitly. Source review, native build, package verification, post-resign verification and device acceptance are separate stages.

P0-08 resolves the source-level readiness gap through an independent current installed-host compatibility check rather than rewriting historical manifests. This deliberately does not claim exact historical signer provenance, online revocation status, or device acceptance. P0-09 retains the external-copy ownership hold when the callback cannot be verified; a foreground transition or Cancel alone cannot prove that copying stopped.

Remaining research: original `12a496c` contains substantial pipeline/model evolution (including fingerprint caching, Info.plist/entitlement customization and provisioning changes). Selected handler contracts and the ordered authentication path were compared; importing all newer features without matching dependent models is not validated by this audit. No claim of byte-for-byte original-LC behavior is made.

## P1/P2/P3 handoff constraints

P1 retains the typed profile projection, explicit numeric save/validation and truthful restart UI work above, then checks meaningful keyboard/cancel/repeated-action/large-text layouts. P2 uses the measured per-file inventory; no code/model/widget/dynamic-icon resource is deleted on file size alone. Preserve language infrastructure: the later P3 stage requires English, Simplified Chinese and Hebrew, a persisted in-app selection, and Hebrew RTL. Existing Hebrew resources containing only the display name are not full UI localization. No P1/P2/P3 implementation is claimed in this P0 report.

## Auth-baseline integration checkpoint

The reviewed source batch was reapplied onto authentication follow-up `b6f243d6253b71e8000fc77ea1716b4a54368621`, producing candidate code `687ff23e95a60585729ea3aa9f65dd5d5bb84596`. The duplicate-prompt cleanup-interleaving fix remains byte-for-byte unchanged. This integration passed **1,034 tests: 867 passed, 167 explicit native/platform skips, zero failures/errors**, plus whitespace checks. No branch was pushed and no workflow was started or cancelled during this preparation. Native gates, candidate packaging, exact re-downloaded IPA verification, post-installer verification and device acceptance remain pending; this is a source checkpoint only.
