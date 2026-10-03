# v3.0.3 stabilization ledger

Checkpoint: 2026-10-03. Resumed from clean `codex/v3r62-recovery` at
`1be7970350c6e7b4b282ffbb5059c81d35f7a9fd`. Work continues in isolated
`codex/v3r76-stabilization`. The workspace-root checkout is stale and untouched.
The matching CI run `37018361730` failed. Last green CI is `36862426586` at
`475362603783fa44f8766480230d72f935a4ce41`; that predates transient XPC answers.

## Completion ledger

| Issue | Observed failure / acceptance | Owner and existing path | Necessary work / verification | Remaining dependency |
|---|---|---|---|---|
| Auth response regression | Device `signIn/persistence/secretHandoffUnavailable` | Host admission -> XPC -> `V3AuthCenter.respond` -> SS `SignInOperation` / SideSign | Current HEAD already sends bounded transient `answer`; verify launched peer, service generation, cancel/disconnect, and production wiring. Keep persistent auth separate. | Matching CI and real sign-in/2FA/persistence/refresh |
| App Group regression | Device `storageUnavailable/appGroup`, service status connected | LC runtime group -> LiveProcess inherited group -> `V3SharedAppGroup` | Current HEAD contains group resolver/launch fixes; do not reapply old isolated drafts. Verify generated lookup and stores. | Effective iLoader signature/container access on device |
| #30 | Source Update device version-pair validation deferred by user | SS `InstalledApp.hasUpdate`, AppManager update operation | Preserve upstream update authority and single Update action; targeted regression only | Deferred physical version-pair test; keep open |
| #31 | Wrong password / misleading auth errors | SS typed SideSign/SignInOperation errors -> v3 mapping | Preserve top-level previousFailure, typed classification, post-auth provisioning distinction | Current candidate real Apple attempts |
| #32 | Delivery / wrong-code / method-change UX | Existing SS/SideSign challenge and prompt adapter | Verify transient prompt delivery with existing correlation and duplicate guards | Trusted-device/SMS/voice external delivery; previously deferred |
| #33 | Guest background death | LC `Dead10ccFix.m` upstream backport | Verify final prepared source has both background observers; preserve guest return | Same-PID background/foreground device test |
| #34 | Host/guest preferences lost | LC host/guest persistence semantics | Preserve independent defaults ownership and existing patches; regression checks | Cold relaunch and affected-device test |
| #35 | Refresh later stops | SS refresh pipeline plus intentional LocalDevVPN/CoreDevice transport | Preserve scheduler/run verification and current shared-store resolver; test persistence separately from prompt transport | Long-duration device/affected-user reproduction |
| #36 | Reload Status | v3 UI / authoritative SS snapshot | User previously accepted visual/function behavior; keep unchanged | Quick candidate regression |
| #37 | Local IPA first selection/reuse/install | Root picker -> durable token staging -> SS production pipeline | User previously accepted install/recovery/delete; retain architecture and current runtime-group selection | Current signer/device install and cleanup retest |
| #38 | False success adding source | SS fetchSource -> isAdded -> save -> fresh context verification | Persistence fix was physically accepted by user; new public report still concerns v3.0.2. Preserve backend flow; check catalog/relaunch separately. | Current source -> catalog -> relaunch validation |
| #39 | Stale JIT-Less certificate copy | Canonical LC certificate import/remove/diagnose; SS active cert remains separate | Keep canonical LC route, scoped observation, truthful revocation; check whether process-default auth/cert namespace affects import | Device certificate/signature validation |
| #40 | Add Source Cancel / stale preview | Host edit session -> SS preview/persistence | Preserve edit-generation ownership and zero-side-effect cancel | Focused behavioral checks and UI retest |
| #1 / #3 | Compatibility reports / cellular investigation | Device transport | No unrelated expansion of this maintenance task; preserve required transport | Hardware reports / separately tracked cellular work |

## Current scope

No publication, tags, issue closure, optional UI pruning, or broad dependency upgrade.
Use actual production/generated helpers for regression tests. A prior candidate's
device acceptance is history, not proof for this candidate. CI green is not device
verification. Keep credentials out of persistent request caches, logs, defaults,
environment variables, diagnostics, and ordinary files; interactive answers must
not be automatically replayed.
