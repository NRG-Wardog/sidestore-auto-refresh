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
removal, installation and updates call SideStore's confirmation/operation APIs.
Oversized catalogs report an error rather than silently dropping entries.
The same catalog can install into LiveContainer using its existing guest download
flow. Previously saved guest-source URLs remain stored and can be explicitly
added to the unified SideStore catalog; no destructive conversion occurs.

Home combines live owner-supplied status with the host scheduler's last verified
run, deadline and failure information. Guest signature warnings remain separate
from verified host refresh state.

## Commands and privileged presentation

`V3ServiceBridge` calls SideStoreSupport's `v3Execute:reply:` XPC endpoint.
The embedded `V3SideStoreService` implements `execute:reply:`.
The shared `V3WireContract` validates operation/field allowlists, UUID,
version, deadline and sizes: 16 KiB requests and 4 MiB responses.
Credentials, private keys, pairing contents and auth tokens are not command
fields. Raw framework errors are not returned through this endpoint.

Authentication and privileged configuration use SideStore-owned controllers
rendered remotely inside the host operation sheet. The service's existing PID
is attached by `AppSceneViewController.initWithServicePID`; it does not launch
another database owner or import managed objects. SwiftUI links retain a
navigation environment and native certificate actions use their actual hosting
controller. Closing a sheet releases presentation, not the service database.

The SideStore scene root is a service presenter, not the legacy tab controller.
The normal launch button is removed. Settings exposes account/sign-in/sign-out,
certificates, developer services, pairing import, connection, Anisette, SideSign
configuration, installation options, backups and diagnostics. Each remains
backed by its original owner. Interactive service presentation requires iOS 16
or newer; existing automated refresh requires the upstream iOS 17 intent
runtime. Availability limits are displayed rather than opening legacy UI.

## Lifecycle

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
against the currently owned instance. Startup read retries are bounded;
mutations are not automatically replayed after uncertain outcomes.
Cancelled native operations retain the host mutation gate for a three-second
grace period. If no completion arrives, the service is retired and reconnected
on demand. This prevents a missing native callback from wedging the product.

## Refresh and transport

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
