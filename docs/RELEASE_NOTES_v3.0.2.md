# Unified LiveContainer + SideStore v3.0.2 (development line, unpublished)

Work-in-progress notes for the v3.0.2 development line. This is not a
published release: v3.0.1 remains the recommended download until v3.0.2 is
tagged and published. Release numbering does not replace upstream app
version numbers.

## Download and installation

No published v3.0.2 asset exists yet. Test builds are produced by CI from
the v3.0.2 development branch and verified locally by SHA-256 before
device testing. When published, install over the existing same-team,
same-identifier installation; do not delete LiveContainer first. Back up
important guest data.

## Headless SideStore backend

- SideStore runs as a headless backend service. No normal flow opens or
  renders SideStore UI: no remote scene, no SideStore-owned controllers,
  pickers, alerts, or navigation stacks in the unified path.
- Authentication runs as a host-driven state machine (credentials, 2FA
  delivery and code selection, team selection, account repair, revocation
  choice) over the existing upstream SignInOperation. No second auth stack.
- Install, update, refresh, activate, deactivate, remove, delete, backup,
  and restore run as command sessions with host-rendered confirmations
  and progress. Cancellation is safe and never replays uncertain work.
- Certificates, developer objects, sources, pairing files, SideSign
  configuration, Anisette servers, settings, logs, health, and account
  backup cross the service boundary as data. The host renders every screen.
- Same database, Keychain, accounts, certificates, pairing, sources,
  history, transport, and guest runtime. No mirrored state.

## Setup Assistant (Issue #12)

- Host-owned onboarding checklist: device, pairing, Apple account,
  network, Background App Refresh, schedule, test refresh, completion.
- Every row re-derives from authoritative runtime state on open, on
  foreground, and on return from child flows. No stored completion flags.
- Setup Complete requires pairing, signed-in team, acceptable network and
  tunnel, available Background App Refresh, enabled schedule, and a refresh
  verified in the current session. History alone never satisfies the test.
- The test requires a new run ID plus the authoritative complete-result
  contract, so partial manifests never verify.
- Entry points: Settings, conditional Home banner, `setup` deep-link host,
  and a "Set Up LiveContainer + SideStore" App Intent that only stores a
  pending flag. The app works fully without Shortcuts.

## UI and settings

- Guided 2FA screens, tappable Home status rows, navigable Refresh Manager,
  relevance-ordered Settings with Build Candidate at the bottom, dead-end
  account states linking to sign-in, and maintainer credit on Home.
- New persistent Multitasking setting "Start Dock Collapsed"
  (`LCMultitaskDockStartsCollapsed`): applied once at dock creation,
  OFF by default (starts expanded, current behavior). Independent from
  "Hide Collapsed Dock", which only hides an already-collapsed dock, and
  from Guest Controls "Start Collapsed", which controls only the Return
  control. Manual expand/collapse always wins afterwards; layout and
  rotation never reset it.
- First-launch notification permission prompt with plain-language
  explanation; coalesced manual refreshes report visibly instead of
  staying silent.

## Diagnostics and error handling

- Structured failures are preserved end to end and never double-wrapped;
  service-readiness timeouts stay timeouts with stable correlation IDs.
- Correlated startup, auth, operation, and setup markers carry stages,
  codes, and correlation only. No credentials, codes, tokens, headers,
  pairing contents, or private keys in logs or diagnostics.
- "Copy Setup Diagnostics" emits product/iOS versions and safe state only.

## Requirements and limitations

- Free Apple Account / Personal Team, pairing file, Developer Mode, Wi-Fi,
  and official unmodified App Store LocalDevVPN with a compatible local
  route, as with v3.0.1.
- Deployment targets are unchanged; optional newer APIs are
  availability-guarded.
- iOS may delay or omit background tasks. A deadline, submitted request,
  task launch, or installation handoff is not proof of completed refresh.
- App Intents require iOS 16+; older supported builds simply omit the
  Setup shortcut.

## Verification and provenance

- Builder: `719fb093bbb1537ed6a7fcfcd9656f5a3878f209`
- [Successful build 35513717909](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/35513717909)
- LiveContainer: `12377cf3b91d51739a33f14a302e5f522b238593`
- Embedded SideStore: `ff25922e5c13ccfafd83bda5092910d848ebd409`
- SideSign: `a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d` (GSA fix `35993d7`
  verified present in the prepared build tree by test)
- minimuxer: `98c3c79982f813878e922ab42f9545314a700f0c`
- CI: 190 tests passed; layout regression clean; host and embedded builds
  passed; transport checks passed.
- The shipped framework binary was checked for the headless markers and
  the new dock preference strings.
- Size: 38,736,074 bytes.
- SHA-256: `726B79B96922353E31083E1EDCE93B718F144B8191F34C69D7F061913F73EF6C`

Device acceptance for the Setup Assistant flow, the dock preference, and
the notification prompt is still pending; CI and simulator checks are not
physical-device proof. Keep credentials, pairing records, and private
signing material out of reports.
