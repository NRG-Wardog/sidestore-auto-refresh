# LiveContainer + SideStore Auto-Refresh v2.0.0

**Preview release.** This introduces the combined LiveContainer + embedded
SideStore variant. Compilation and package verification passed; successful
signing, installation, host replacement and unattended refresh of this combined
variant still require on-device validation. This release contains only the
LiveContainer combined build.

## Downloads

- `LiveContainer-SideStore-AutoRefresh-v2.0.0.ipa`: new combined preview.
- `SHA256SUMS.txt`: integrity checksum for the combined IPA.

Version v2.0.0 identifies this repository release, not the upstream applications'
internal version numbers. The combined IPA must be signed with your own account.

## Included in the combined preview

- LiveContainer with patched embedded SideStore, its dependencies and widget.
- Preserved LiveProcess, ShareExtension and LaunchAppExtension bundles.
- Upstream combined packaging, including executable conversion, entitlement
  preparation, app-group metadata, URL schemes and intent metadata.
- Six-hour, daily and weekly scheduling controls with local clock selection.
- Native background processing, a lightweight background-refresh watchdog and
  launch/resume recovery paths; Personal Automation is not required setup.
- Target-deadline scheduling with an initial one-hour lead time. iOS still
  controls execution and may delay or omit a background task.
- Shared run coalescing, compact due-state checks, persisted retry timing and
  refresh history.
- Host-handoff and post-relaunch verification logic, plus guest-signature checks.
  These are implemented paths, not proof of successful host or guest refresh.
- Optional AlarmKit deadline protection on iOS 26.1+ when authorized. The alarm
  is a warning with a user-operated Refresh Now action, not an automatic executor.
- Embedded SideStore console-log retention limits to avoid unlimited log growth.

## Requirements and limitations

- Free Apple Account / Personal Team, valid pairing file, Developer Mode,
  Wi-Fi and official unmodified App Store LocalDevVPN for the intended transport.
- The combined package has five App ID registration targets before exact-ID
  reuse. Sufficient Apple registration quota is required for initial signing;
  this is separate from the three-installed-app limit.
- Removing apps or App IDs does not guarantee immediately available quota.
- No deployment targets were raised by this release. Launch compatibility across
  older supported iOS versions still needs real-device testing.
- Background execution, deadline protection, expiration recovery and host
  self-replacement are not guaranteed by this preview.
- Do not infer successful refresh from a task launch or an accepted installation
  request. Check actual installation and signing validity after refresh.

Back up important app data before testing. Use your installer to sign the combined
IPA. A standalone-to-combined data migration is not provided here.
Keep credentials, pairing files and private signing material out of bug reports.

## Provenance

Combined preview:

- Builder commit: `85f024438c6412d9a14f11d9fe4a79134bfe39c3`
- [Successful build run 34091379185](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34091379185)
- LiveContainer revision: `12377cf3b91d51739a33f14a302e5f522b238593`
- Embedded SideStore revision: `10ffa01ecdfe4203a7ad5d7f41c0d5de03bd8abb`
- Size: 37,206,711 bytes
- SHA-256: `9fbbfbacbf8b31927cfde70fe1d799b6af4f1a9c6076819fc2849a00e861f58d`

The IPA is an existing build output; publishing this release did not rebuild
or alter it. Local checks: 13 tests passed, two Swift-dependent tests
skipped, and combined-package semantic verification passed.
