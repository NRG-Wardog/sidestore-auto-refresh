# LiveContainer + SideStore Auto-Refresh v2.0.0

Stable release of LiveContainer with patched embedded SideStore, native refresh
automation, LocalDevVPN/CoreDevice transport, and guest Return controls.
Updated September 8, 2026 with build `f20e14e`.

This release contains only the combined LiveContainer build, not standalone
SideStore v1.0.2. Release numbering does not replace upstream app version numbers.

## Download and installation

- `LiveContainer-SideStore-AutoRefresh-v2.0.0.ipa`
- `SHA256SUMS.txt`
- `RELEASE_NOTES_v2.0.0.md`

Sign with your own Apple account using a compatible installer. Update over the
existing same-team, same-identifier installation; do not delete LiveContainer
first. Back up important guest data. Standalone SideStore-to-combined data
migration is not provided.

## Combined application and transport

- Upstream combined packaging: converted SideStoreApp.framework, nested
  dependencies, relocated widget, app groups, URL schemes, and intent metadata.
- Preserves LiveProcess, ShareExtension, LaunchAppExtension, and the combined widget.
- Ports the LocalDevVPN/CoreDevice path into embedded SideStore: pairing selection,
  service TLS, contiguous CDTunnel handshake, RSD, AFC/install routing, and
  transport dependency fixes.
- Explicit transport-selection/readiness diagnostics. The CoreDevice path does
  not require VPN Super or an additional IKEv2/IPSec VPN.
- Existing upstream transport modes remain available where supported.

## Embedded SideStore startup and authentication

- Corrects startup hooks and host bundle/profile identity handling.
- Prevents database retries from attaching the same persistent store twice and
  masking the original startup error.
- Accepts upstream reusable authentication paths instead of requiring saved email.
- Shared-Keychain credential handling and migration, with non-secret
  presence/status diagnostics for UI and refresh-process access.
- Navigation back from embedded SideStore to LiveContainer.

## Refresh automation and status

- Six-hour, daily, and weekly schedules with local clock selection.
- Native background processing, lightweight watchdog, and launch/resume recovery;
  Shortcuts Personal Automation is not required setup.
- Deadline-oriented scheduling, initially with a one-hour lead time, compact due
  checks, run coalescing, persisted retry backoff, and expiration monitoring.
- Optional AlarmKit safety alerts on iOS 26.1+ when available and authorized, with
  local-notification fallback. Alarm actions require user interaction.
- Host-handoff/post-relaunch verification and guest-signature health paths.
- Wi-Fi preflight before expensive transport work; foreground LocalDevVPN enable
  handoff with readiness rechecking rather than assuming activation succeeded.
- Background VPN failures use explicit status and bounded retry, not app launching.
- Reactive readiness/status UI and preserved backoff across initial scheduling.
- Manual/scheduled history, lifecycle notifications, stable selection/deletion,
  and bounded embedded console-log retention.
- Correlated run IDs and verification results across the existing XPC bridge.
  Missing, mismatched, and empty results now have distinct explanations.

## Guest Return controls and multitasking

- Movable control with safe-area/keyboard-aware placement and saved position.
- Long-press collapse to an edge tab; tap to restore. Collapse does not change the
  global Guest Controls preference.
- Floating control automatically hides in windowed multitasking and is available
  in fullscreen/maximized guests, subject to the global preference.
- Corrected virtual-window input layering and control cleanup.
- LiveProcess Return uses upstream minimize/host-activation without intentionally
  terminating the guest; reopening prefers its retained instance.
- Per-window launch ownership, container identity checks, and stale-instance
  cleanup prevent cross-guest callbacks and duplicate launches.
- Direct host-process guests use restart-return, not preserved resume.
  iOS can still suspend or terminate retained LiveProcess guests.

## Requirements and limitations

- Free Apple Account / Personal Team, pairing file, Developer Mode, Wi-Fi, and
  official unmodified App Store LocalDevVPN with a compatible local route.
- The intended refresh runtime does not require a PC, USB, external relay, paid
  developer membership, or custom VPN extension. Initial signing/install requires
  a compatible setup and sufficient account quota.
- Five App ID registration targets before exact-ID reuse, separate from the
  installed-app limit. Uninstalling apps does not immediately release quota.
- Cellular-only refresh is not supported.
- Deployment targets are unchanged; optional newer APIs are availability-guarded.
  Older-device compatibility still needs testing on those devices.
- iOS may delay or omit background tasks. A deadline, submitted request, task
  launch, or installation handoff is not proof of completed refresh.
- Host self-replacement and unattended combined refresh are not universally proven.
  Check actual signing validity, not just a successful UI or handoff message.

## Verification and provenance

- Builder: `f20e14e43b4048c5b5791e7d4b0bb6e32930c10a`
- [Successful build 34238644076](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34238644076)
- LiveContainer: `12377cf3b91d51739a33f14a302e5f522b238593`
- Embedded SideStore: `10ffa01ecdfe4203a7ad5d7f41c0d5de03bd8abb`
- CI: 76 tests, 2 skipped; both Release builds passed.
- Combined semantic/runtime package and embedded transport checks passed.
  The downloaded IPA was independently rechecked locally.
- Size: 37,389,675 bytes.
- SHA-256: `1c29648ee99abd67cd6244d0405f2ab9df0beca92adb4c35bed5c133eb7d974e`

Earlier device testing confirmed embedded UI operation, Return-control interaction,
and transfer of refresh verification results, with a newly installed provisioning
profile observed. The final windowed/fullscreen visibility change passed
source/build checks but has not yet been confirmed on-device. Package checks
do not establish same-PID resume or complete host replacement.

This replaces the earlier preview asset without rebuilding the IPA. The existing
release tag is retained; the explicit builder commit above identifies the source
for the updated binary.

Keep credentials, pairing records, and private signing material out of reports.
