# v3 unified LiveContainer + SideStore

## Sources and patch model

The combined workflow pins LiveContainer `12377cf3b91d51739a33f14a302e5f522b238593`
and LiveContainerSupport SideStore `ff25922e5c13ccfafd83bda5092910d848ebd409`.
SideStore's own submodule resolution supplies minimuxer
`98c3c79982f813878e922ab42f9545314a700f0c` and SideSign
`a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d`.

The repository contains semantic build-time patches, not vendored replacements.
The workflow checks revisions before patching. `patch_v3_service.py` validates
both input revisions and resolves every anchor before writing. It records
output/template hashes; replay verifies them and rejects drift. Integration
patches are replayed in CI, with Swift parsing and transport diff/hash checks.

## Navigation and ownership

`V3UnifiedShell` supplies Home, Apps, Sources, Refresh and Settings.
`V3ApplicationRoot` retains upstream startup checks, download handling and window
lifecycle. Guest operations retain the original `LCAppListView` and
`LCAppModel`: hidden guests, confirmations, launch modes and LiveProcess.

| State or operation | Authoritative owner |
| --- | --- |
| Guest files, configuration, launching and Return controls | LiveContainer |
| Unified layout and normal navigation | Existing LiveContainer preferences |
| Installed apps, sources, catalogs, account/team records | SideStore Core Data |
| Credentials, certificates, signing and installation | SideStore managers/Keychain |
| Signing, connection, Anisette and developer preferences | SideStore configuration managers |
| Schedule, retries, orchestration history, verification UI | Existing host refresh scheduler/history |
| Refresh execution and installation evidence | SideStore pipeline/minimuxer |

The host does not open SideStore Core Data or maintain another account, source
or installation database. Status DTOs are in-memory projections with freshness
timestamps, not a new persistent snapshot store. Existing guest-only signing
configuration remains guest-scoped, not a second SideStore account.

Apps aggregates guests and separately installed apps. `V3AppIdentity` distinguishes
guest paths from installed-app object URIs, avoiding equal-name/bundle-ID
collisions. List, Grid and Compact List use existing layout preferences and
shared handlers. Details include expiration, activation and certificate status.
Supported actions include open, refresh, update, activate/deactivate,
backup/restore, JIT, library removal and device deletion. Host
deletion/deactivation is rejected.

Sources reads actual SideStore records and supported catalog versions. Addition,
removal, installation and updates run through headless service commands; every
confirmation renders in the host before the confirmed command is sent.
Oversized catalogs report an error rather than silently dropping entries.
The same catalog can install into LiveContainer using its existing guest download
flow. Previously saved guest-source URLs remain stored and can be explicitly
added to the unified SideStore catalog; no destructive conversion occurs.

Home combines live owner-supplied status with the host scheduler's last verified
run, deadline and failure information. Guest signature warnings remain separate
from verified host refresh state.

## Commands and headless backend

`V3ServiceBridge` calls SideStoreSupport's `v3Execute:reply:` XPC endpoint.
The embedded `V3SideStoreService` implements `execute:reply:`.
The shared `V3WireContract` validates operation/field allowlists, UUID,
version, deadline and sizes: 16 KiB requests and 4 MiB responses.
Credentials, private keys, pairing contents and auth tokens are not command
fields. Raw framework errors are not returned through this endpoint.

SideStore is a headless backend from the user's perspective. LiveContainer
owns 100% of visible presentation: navigation, tabs, sheets, alerts, forms,
confirmations, loading states, errors and progress. No normal user flow opens
or renders SideStore UI: there is no remote scene, no SideStore-owned
controller, picker, alert or navigation stack, and no visible transition into
a SideStore process. The SideStore scene runs windowless; the process exists
only to own Core Data, Keychain, authentication, signing, provisioning,
installation, refresh, sources and transport.

Interaction crosses the bridge as data. Mutating work runs in service-side
sessions (`opStart`/`opPoll`/`opAnswer`/`opCancel`) with states working,
awaitingPrompt, requiresSource, waitingForAuthentication, completed,
cancelled and failed, plus numeric progress. Sign-in runs as a state machine
(`authBegin`/`authPoll`/`authRespond`/`authCancel`) over the existing
`SignInOperation`: credentials, two-factor delivery/code selection, trusted
phone selection, team selection, account repair, revocation choice, resign
confirmation, provisioning retry and anisette warnings are prompts rendered
by the host; `AuthManager`, portal proxy, team/provisioning/certificate logic
and persistence are reused unchanged. Certificates, developer objects
(teams/devices/App IDs/groups/profiles), pairing files, SideSign
configuration, Anisette servers, settings, logs, health and account
backup/restore cross as DTOs/commands; the host renders every screen.

The normal launch button is removed. Old startup selections, share-extension imports and multi-instance installation
are routed back into the unified host. Local IPA import uses a one-use shared
file authorization referenced by UUID; file bookmarks are not sent over XPC.
Pairing/account/SideSign imports stage file bytes under one-use group-default
tokens that the service consumes; the service never presents its own picker.
SideStore-based guest JIT acquisition calls the service directly, while other
configured JIT providers and LiveProcess launch modes retain their existing paths.
Settings exposes account/sign-in/sign-out, certificates, developer services,
pairing import, connection, Anisette, SideSign
configuration, installation options, backups and diagnostics. Each remains
backed by its original owner. Existing automated refresh requires the upstream iOS 17 intent
runtime. Availability limits are displayed rather than opening legacy UI.

Deferred on purpose: Anisette ADI reset (tied to its alert-controller flow),
EMProxy developer test hooks, refresh-attempt clearing, raw database file
export, and SideJIT live device-refresh actions. These need product decisions
or streaming file transfer and stay out of normal flows.

## Lifecycle

Startup and refresh now have separate implementations. `ensureServiceConnected`
uses an injectable connection state machine and the authoritative host container;
`performRefresh` establishes readiness before dispatching an actual refresh.
The `__v3_connect` sentinel and empty mangled-name connection requests are removed.
Legacy bootstrap and service startup share idempotent directory preparation.
Bookmark NSError values are preserved and reported without force unwraps.
See [the matched-binary crash investigation](COMBINED_STARTUP_CRASH.md).

Connection attempts are coalesced. The launch continuation is registered before
LiveProcess startup, with a 45-second deadline. Requests settle once; completion
cancels their timeout tasks. Stale/late replies cannot complete another request.
Reads have a 30-second deadline and commands 600 seconds. Mutations are
serialized; completed mutation replies are retained until their deadline to
avoid duplicate execution.

Cancellation reaches the service task and available operation cancellation
handles. It does not promise to undo an installation already committed by iOS:
users must reload status before retrying. The service retains its mutation gate
while an operation unwinds. Refresh checks that gate before invoking its separate
intent. Disconnect settles pending callers and retires the old process before a
replacement opens the database. Extension and connection callbacks are checked
against the currently owned instance. Failed startup disables automatic status retries until explicit retry;
mutations are not automatically replayed after uncertain outcomes.
Cancelled native operations retain the host mutation gate for a three-second
grace period. If no completion arrives, the service is retired and reconnected
on demand. This prevents a missing native callback from wedging the product.
An idle service that times out on a read is also retired; read timeouts never
terminate an active signing, installation or refresh operation.

## Refresh and transport

`CombinedFailure` provides operation/stage/code/correlation and sanitized native
domain/code over a bounded version-1 envelope. Refresh metadata has a separate
field allowlist, including sanitized per-app failures. Connection or intent return
alone is not a verified refresh. Build Candidate in Settings identifies the product
line and immutable builder revision without changing signing or bundle identity.

Refresh is the only normal refresh interface, including selected-app refresh.
Manual/scheduled state, preferred time, retries, history, deadlines and correlated
verification continue through the existing scheduler. Selected-app completion
uses the same history without marking the host signing lifetime as verified.

The stable route remains:

`Wi-Fi/LocalDevVPN → Lockdown → CoreDeviceProxy/TLS → CDTunnel → RSD → AFC/InstallationProxy`.

Minimuxer was diffed against `d57586ff506199ecfa8b78048930da821e5237de`.
Retained upstream changes include scoped IPv6/fallback interfaces, IPv6 usbmux
addresses, corrected backend caching and package URL disambiguation.
Gateway patches change specific transport/staging methods, not whole files.
The updated pipeline adapter preserves structured async execution, cellular
readiness gating and MainActor completion. One transport lease covers a batch;
ordinary apps finish before host replacement. Returning success/error paths
release the lease. Idle deferred checks are not represented as verified
connectivity. Tests cover pairing selection, routing, staging, installation
identity, errors and cleanup.

## Issue 18 authentication integration

SideSign `a731c0d` descends from GSA 5XX fix `35993d7`; CI verifies ancestry
without independently checking out another SideSign revision. This uses the
fixed upstream source, not the old PR's isolated Connection header backport.

The upstream review covered AuthManager, SignInOperation, DeveloperPortalProxy,
SideSignConfigManager and Anisette configuration:

- AuthManager retains token-backed session coalescing and resolved Xcode/Anisette data.
- SignInOperation retains cached sessions, token/password silent sign-in,
  interactive verification/account repair, team selection, provisioning,
  certificate reuse/revocation, device registration and persistence.
- SideSign retains upstream header defaults and persisted customizations.
- Anisette retains its configuration and request construction.
- Developer Portal remains behind the upstream authenticated proxy.

The v3 adapter calls new `signIn`, `InstallTarget.app` and
`StandaloneOperationContext` APIs, not removed AuthFlowHandler or
AuthenticatedOperationContext APIs. Modern AuthManager is left byte-identical.
After all patches, CI rejects diffs in Auth, Anisette, SignInOperation and
SideSign. Standalone source, workflow and authentication patches are unchanged.

## Upgrade preservation

No v3 reset, database copy or Keychain replacement is introduced. The existing
SideStore home, app-group identity and Keychain migration remain. Upstream
database migration/coalesced async startup stay intact. Failed preparation
retries without reattaching SQLite. An unreadable host bundle/profile produces
an error instead of successful empty preparation.

Guest storage/configuration, layout keys, Return visibility, Start Collapsed,
custom colors and refresh-history storage are retained. Sign-out is an explicit
action and preserves reusable certificate/Anisette configuration using the
upstream options. No normal upgrade requires invoking a diagnostics reset.

## Validation and physical-device acceptance
The workflow runs repository tests, patch replay, Swift parsing, host and embedded
source builds, Rust/CoreDevice tests, IPA packaging and executable markers.
The IPA artifact contains verification JSON, builder commit and upstream-auth
provenance. Source-build evidence is uploaded separately. Local tests run in a
clean v3 checkout; unrelated private investigation refs are neither removed nor
published to satisfy security tests.

Physical-device acceptance must cover upgrade preservation, login/2FA,
certificates/teams, source/install/update and other app actions, all guest layouts,
LiveProcess/Return controls, manual/scheduled refresh with the computer
disconnected, host-replacement reconciliation, VPN failure/recovery, process
termination/reconnect and cancellation. CI cannot establish those outcomes.
Issue 18's integrated build evidence is not proof of successful on-device login.

## Operational evidence: authentication path and readiness propagation
Authentication reaches Apple's GrandSlam through the pinned SideSign
implementation, not a second stack: the headless sign-in handler drives the
upstream `SignInOperation` with its `SignInHandler`/`AnisetteServerHandler`
protocols, and every credential, 2FA, team, repair, revocation and
provisioning decision crosses to the host as data. The pinned SideSign tree
carries the official `Connection: close` fix in both GrandSlam request
builders (`sendAuthenticationRequest` for initial auth and
`makeTwoFactorAuthRequest` for trusted-device, SMS, voice and code
submission); no builder patch modifies those sources. Auth sessions are
single-flight per service process (a new begin cancels the previous one),
every retry is user-driven, and auth/open-operation prompts are logged with
session, kind, attempt and terminal stage/code only - never credentials,
codes, tokens or headers.

Readiness failures preserve their structured codes: `failed()` forwards an
already structured `CombinedFailure` (for example the probe's
`serviceReadiness/timedOut` or `invalidResponse`) instead of re-wrapping it
into `failed/redacted`. Correlation IDs remain stable across the attempt.
Startup transitions emit correlated `[V3_SERVICE_START]` markers from process
launch through XPC connect, application ready, snapshot ready (or database
not ready / malformed snapshot / timeout) to process exit, plus `[V3_AUTH]`
and `[V3_OP]` session markers. The readiness probe polls the snapshot for 30
seconds before timing out; a persistent database failure still terminates as
a retryable timeout rather than a false success. Connection attempts coalesce
into one startup, late callbacks from retired processes are rejected by
attempt guards, retiring kills the failed process before replacement, and no
mutation can begin before readiness succeeds.

## Issue #12: Setup Assistant (onboarding only)

`V3SetupAssistantView` (with `V3SetupStore`) is a host-owned SwiftUI flow
that guides a fresh install to a usable state. It creates no scheduler,
authentication stack, database, Keychain entry, or persisted completion
flag: every row is re-derived from authoritative runtime state whenever the
screen opens, the app returns from background or Settings, a child flow
(pairing, sign-in, refresh schedule) is dismissed, or an underlying action
completes. Leaving halfway, cancelling sign-in or pickers, losing the
service connection, or rerunning cannot corrupt SideStore state, because the
assistant only reads state and invokes existing user-driven actions.

Setup Complete requires pairing, a signed-in account with team, acceptable
Wi-Fi and tunnel state, available Background App Refresh, an enabled
schedule, and a refresh verified in the current assistant session.
Developer Mode stays advisory and never gates. A historical manifest feeds
only the "last verified" history row: the acceptance step records its
baseline run ID and completes solely for a new run ID whose manifest
satisfies the authoritative `CombinedVerification.hasCompleteTerminalResults`
contract (every expected app present exactly once), so a partial manifest
with two expected apps and one result never verifies.

Automatically checked (authoritative): app running, pairing presence from
the SideStore snapshot, account/team from the snapshot, Wi-Fi path and
tunnel interface via `LiveContainerNetworkPreflight`, Background App Refresh
via `UIApplication`, scheduler configuration and verification manifest from
shared defaults. Advisory only: Developer Mode guidance (no private API to
query it; never blocks) and CoreDevice (reported as verified only after a
successful refresh - interface presence alone is never called ready).

User-driven actions reuse existing flows: pairing import uses the existing
host picker plus `pairingImportData`; sign-in routes into the existing v3
authentication state machine (no credentials or codes are persisted by the
assistant); LocalDevVPN opens via the official `localdevvpn://enable`
deep link with re-check on return (no polling, no monitor); refresh
scheduling navigates to the existing Refresh Manager; the test refresh posts
the same `LiveContainerAutoRefreshRunNow` notification as Refresh All and is
marked successful only when a new run ID appears with complete terminal
results - never on tap, launch, handoff, or scheduling.

Entry points: Settings -> Setup Assistant, a Home banner shown only while
account or pairing is missing, the `setup` deep-link host (sets a flag, runs
no mutation), and the "Set Up LiveContainer + SideStore" App Intent, which
only stores a pending flag and opens the app. The shortcut carries no
credentials, identifiers, pairing, network, or signing material and the app
works fully without it.

Failures preserve their structured cause (operation, stage, code,
correlation, retryable) through `CombinedFailure`, including the
service-readiness preservation behavior; unknown errors stay sanitized but
are never collapsed into a bare success or a generic failure. "Copy Setup
Diagnostics" emits product/iOS versions, pairing/account/team/Wi-Fi/VPN/
CoreDevice/BAR/schedule state, last verified refresh, and the last
structured failure only - no secrets or pairing contents. Console markers
(`[V3_SETUP]` open/status/action/test-refresh/failure) carry states and
correlation only.
