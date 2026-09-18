# v3 headless SideStore UI migration

## Goal

LiveContainer owns every visible v3 surface. The embedded SideStore process may
continue to own Core Data, Keychain, authentication, signing, provisioning,
installation, refresh and source state, but it must not render user-facing UI in
normal v3 flows.

The end state is:

```text
Unified LiveContainer UI
    -> V3 service bridge
        -> SideStore business logic / persistence
```

and never:

```text
Unified LiveContainer UI
    -> AppSceneViewController(servicePID:)
        -> SideStore-owned UIViewController / SwiftUI
```

## Current presentation audit

| Flow | Current call site | Current SideStore UI dependency | Headless target | Status |
| --- | --- | --- | --- | --- |
| Certificates | `V3AccountSettings.panel("Certificates", ...)` -> `V3OperationSheet` | `CertificatesView(presentingViewController:)` via remote service scene | host-owned `V3CertificatesView` + certificate DTO/commands | converted in first slice |
| Pairing import | `status.perform("importPairing", ...)` | `PairingFileManager.importPairingFile(presentingVC:)` | host document picker + one-use bookmark + `savePairingFile(contents:)` | converted in first slice |
| Sign in / re-auth | `status.perform("signIn", ...)` | `AppManager.signIn(presentingViewController:)` / `SignInFlowHandler` | host auth state machine, service-owned `SignInOperation` | blocker |
| 2FA / delivery selection | inside `SignInFlowHandler` | alerts, code entry and phone selection | typed auth prompt/response protocol | blocker |
| Team selection | inside `SignInFlowHandler.resolveTeam` | SideStore storyboard controller | host list/select view | blocker |
| Certificate revocation during sign-in | `resolveRevocation` | SideStore alert/controller | host confirmation + selected certificate IDs | blocker |
| Account repair | `SignInFlowHandler.accountRepair` | SideStore alert/browser routing | host repair prompt + explicit response | blocker |
| Device/provisioning error decisions | `resolveDeviceRegistrationErrors`, `resolveProvisioningError` | SideStore alerts | typed retry/skip/cancel response | blocker |
| Post-auth / resign prompts | `resolvePostAuth`, `resolveResign` | SideStore controllers | host-owned completion/resign decision | blocker |
| Developer Services | `panel("Developer Services", ...)` | `DeveloperServicesView` | DTO screens + portal commands | blocker |
| Connection | `panel("Connection", ...)` | `ConnectionConfigView` | host settings backed by service snapshot/mutations | blocker |
| Anisette | `panel("Anisette Servers", ...)` | `AnisetteServersView` | host settings backed by Anisette config service | blocker |
| SideSign configuration | `panel("SideSign Configuration", ...)` | `SideSignConfigurationView` | host settings backed by SideSign config service | blocker |
| Install/signing options | `panel("Installation and Signing Options", ...)` | `UserCustomizationsView` | host settings backed by existing defaults/managers | blocker |
| Health | `panel("Health Check", ...)` | `HealthCheckView` | service health DTO + host diagnostics view | blocker |
| Backups | `panel("SideStore Backups", ...)` plus app backup/restore calls | `BackupAndRestoreView` and manager callbacks | host list/actions + command results | blocker |
| SideJIT settings | `panel("SideJIT Server", ...)` | `SideJITServerConfigView` | host settings + service commands | blocker |
| Release track | `panel("Update Channel", ...)` | `V3ReleaseTrackView` rendered in SideStore | host picker + service setting | blocker |
| Diagnostics / logs / experimental | `panel(...)` | SideStore views | bounded diagnostics DTOs + host views | blocker |
| Add/remove source | `AppManager.add/remove(...presentingViewController:)` | confirmation/error presentation may escape through presenter | service plan/confirm/execute protocol | blocker |
| Install/update/activate/deactivate/remove/delete | AppManager calls with `presentingViewController: Self.presenter` | confirmation/error presentation may escape through presenter | service plan/progress/confirm/result protocol | blocker |
| Refresh selected app | `AppManager.refresh(...presentingViewController: Self.presenter)` | presenter retained even when no prompt is expected | headless refresh adapter with typed failures | blocker |

## Extraction boundary

The SideStore process remains authoritative for:

- Core Data and existing migrations
- Apple account, team and certificate persistence
- Keychain-backed credentials/certificates
- SideSign and Anisette configuration
- signing/provisioning/install/refresh execution
- source/catalog records

The host remains authoritative for:

- tabs and navigation
- forms and text input
- document pickers
- alerts and confirmations
- progress and cancellation UI
- error/status presentation
- all v3 settings screens

No managed object crosses XPC. Stable IDs, bounded DTOs and typed command states
cross the bridge.

## Migration sequence

### Phase 1 - remove low-risk remote UI dependencies

- host-owned pairing picker
- certificate snapshot/list
- activate/delete locally cached certificates from host UI
- add regression assertions that these flows no longer use the remote presenter

### Phase 2 - authentication state machine

Keep SideStore's `SignInOperation` as the business operation. Replace
`SignInFlowHandler` with a service-side adapter that suspends on typed prompts.
The host owns credential, 2FA, delivery, team, revocation, account-repair,
provisioning-error and resign UI.

Proposed states:

```text
idle
-> credentials
-> verification / delivery
-> teamSelection
-> revocationDecision
-> provisioningDecision
-> resignDecision
-> completed | failed | cancelled
```

A session UUID scopes every prompt/response. Late responses are rejected. Secrets
must never be logged, persisted in refresh history, or returned in diagnostics.

### Phase 3 - settings and diagnostics

Replace every `panel` target with a bounded snapshot + explicit mutation
commands. Remove the host's `panel(...)` helper once the last target is migrated.

### Phase 4 - app/source operations

Separate planning/confirmation from execution:

```text
prepare operation
-> host confirmation if required
-> execute
-> progress/status
-> completed/failed
```

The service must never present an alert as a fallback.

### Phase 5 - remove remote presentation infrastructure

Only after no normal v3 operation depends on it:

- delete `V3RemoteServiceView`
- remove `AppSceneViewController(servicePID:)`
- remove SideStore `presenter`/blank service window
- remove presenter-specific cancellation/dismissal code
- add CI grep/assertions that prohibit these paths in v3 templates

## First-slice acceptance

The first implementation slice is intentionally narrow and must not regress the
working v3 build:

- Certificates opens a native host screen, not a remote SideStore scene.
- Pairing selection uses a host document picker.
- Pairing file bytes do not travel in the XPC request.
- Existing account/signing/source/install/refresh flows remain unchanged until
  their dedicated headless adapters are implemented.
