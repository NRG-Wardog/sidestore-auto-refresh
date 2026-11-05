# Unified LiveContainer + SideStore v3.0.2

Maintenance release in the v3 unified product line. Release numbering does
not replace upstream app version numbers.

## Major changes from v3.0.1

- Fully headless SideStore backend: no normal flow opens or renders
  SideStore UI. Authentication runs as a host-driven state machine with a
  guided sign-in and two-factor flow over the existing upstream
  SignInOperation. No second auth stack.
- Host-owned SideStore management UI: certificates, developer services,
  pairing, connection, Anisette, SideSign, installation options, health,
  backups with account export/import, SideJIT, update channel,
  diagnostics, logs, and experimental features.
- Setup Assistant (Issue #12): host-owned onboarding checklist covering
  device, pairing, Apple account, network, Background App Refresh,
  schedule, and a verified test refresh. State re-derives from
  authoritative runtime sources; Setup Complete requires pairing,
  signed-in team, acceptable network and tunnel, available Background App
  Refresh, enabled schedule, and a refresh verified in the current
  session. Entry through Settings, a conditional Home banner, the `setup`
  deep-link host, and a Shortcuts intent that only stores a pending flag.
- Service-readiness and structured diagnostics: structured failures are
  preserved end to end instead of being double-wrapped, with correlated
  startup, auth, operation, and setup markers.
- Refresh Manager uses normal navigation instead of sheet presentation;
  Home status rows are tappable; Settings sections are relevance-ordered
  with Build Candidate at the bottom.
- New persistent Multitasking setting "Start Dock Collapsed"
  (`LCMultitaskDockStartsCollapsed`): applied once at dock creation, off
  by default. Independent from "Hide Collapsed Dock" and Guest Controls.
- Manual refresh taps that coalesce onto a running refresh now report
  visibly instead of staying silent; first-launch notification permission
  prompt with plain-language explanation.

## Download and installation

- `LiveContainer-SideStore-AutoRefresh.ipa`

Sign with your own Apple account using a compatible installer. Update over
the existing same-team, same-identifier installation; do not delete
LiveContainer first. Existing v3 users upgrade in place with SideStore and
LiveContainer data preserved: Core Data, account and login state, Keychain,
certificates, teams, pairing, sources, installed apps, refresh history,
guest data, settings, and LiveProcess configuration. No database reset,
sign-out, pairing deletion, or Keychain reset is part of the upgrade.

## Requirements and limitations

- Free Apple Account / Personal Team, pairing file, Developer Mode, Wi-Fi,
  and official unmodified App Store LocalDevVPN with a compatible local
  route, as with v3.0.1.
- Deployment targets are unchanged; optional newer APIs are
  availability-guarded. App Intents require iOS 16+.
- iOS may delay or omit background tasks. A deadline, submitted request,
  task launch, or installation handoff is not proof of completed refresh.
- Physical-device validation so far covers sign-in, install/update/refresh
  flows, and background behavior on limited devices. Broader
  account/device configurations are not yet proven. Issue #18 (Apple ID
  503/429 reports on other setups) remains open pending evidence from
  affected users. Do not treat CI success as physical-device proof.

## Verification and provenance

- Builder: the tagged v3.0.2 commit (see release page for the exact SHA
  and CI run).
- LiveContainer: `12377cf3b91d51739a33f14a302e5f522b238593`
- Embedded SideStore: `ff25922e5c13ccfafd83bda5092910d848ebd409`
- SideSign: `a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d` (GSA fix `35993d7`
  verified present in the prepared build tree by test)
- minimuxer: `98c3c79982f813878e922ab42f9545314a700f0c`
- CI: repository tests, patch idempotence, generated-source verification,
  512 layout measurements, host and embedded builds, transport checks,
  IPA packaging and identity checks, App Intent metadata verification.
- The shipped framework binary is checked for the headless markers, the
  Setup Assistant surfaces, and the new dock preference strings. No
  credentials or pairing material are packaged.

Keep credentials, pairing records, and private signing material out of
reports.
