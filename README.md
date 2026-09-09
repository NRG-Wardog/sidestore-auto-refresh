# LiveContainer + SideStore Auto-Refresh

[![Combined Build](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/livecontainer-build.yml/badge.svg)](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/livecontainer-build.yml)
[![Release](https://img.shields.io/github/v/release/NRG-Wardog/sidestore-auto-refresh)](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

LiveContainer + SideStore Auto-Refresh provides a combined LiveContainer build with SideStore already embedded, plus on-device manual and scheduled refresh support for a Free Apple Account / Personal Team.

**If you download the combined v2 build, you do not need to install LiveContainer and SideStore separately.** A computer is required for the initial signing, installation, and pairing setup. Normal refresh operation is designed to run on the iPhone without the computer.

> [!IMPORTANT]
> The current stable refresh path requires **Wi-Fi + the official App Store LocalDevVPN**. Cellular-only refresh is experimental and is not part of the stable release.

## Which version should I download?

| What you want | Recommended download |
| --- | --- |
| LiveContainer + SideStore in one app | **[Combined v2.0.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.0.1/LiveContainer-SideStore-AutoRefresh-v2.0.1.ipa)** |
| SideStore only | **[Standalone v1.0.2](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.2/SideStore-CoreDevice-AutoRefresh-v1.0.2.ipa)** |

If you want to use LiveContainer, choose **v2.0.1**. SideStore is already included inside it.

Combined release: [v2.0.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.0.1) | [Checksums](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.0.1/SHA256SUMS.txt)

Standalone release: [v1.0.2](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.2) | [Release notes](docs/RELEASE_NOTES_v1.0.2.md)

## What you need

Before starting, make sure you have:

- An iPhone or iPad running a supported iOS version. See [Compatibility](docs/COMPATIBILITY.md).
- A Free Apple Account / Personal Team for signing.
- A Windows, macOS, or Linux computer for the initial installation and pairing setup.
- A compatible IPA installer such as the one you normally use for SideStore.
- The official **LocalDevVPN** from the App Store.
- A Wi-Fi connection.
- Developer Mode enabled on the device.
- A valid pairing file for the exact iPhone or iPad you are using.

After setup, the normal refresh flow is designed to work without keeping the computer connected.

## First-time setup: LiveContainer + SideStore

Follow these steps in order. Do not enable scheduled refresh until the manual refresh test succeeds.

### 1. Install the combined IPA

Download the latest combined build:

**[LiveContainer-SideStore-AutoRefresh-v2.0.1.ipa](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.0.1/LiveContainer-SideStore-AutoRefresh-v2.0.1.ipa)**

Sign and install it with your own Apple Account using a compatible installer.

You are installing one combined IPA. **Do not install a separate copy of SideStore or LiveContainer for this setup.**

### 2. Trust the sideloaded app

If iOS shows **Untrusted Developer** or refuses to open the app:

`Settings -> General -> VPN & Device Management`

Under **Developer App**, select the Apple Account used to sign the IPA and tap **Trust**.

The Developer App entry normally appears only after a sideloaded app has been installed.

### 3. Enable Developer Mode

Go to:

`Settings -> Privacy & Security -> Developer Mode`

Turn Developer Mode on. iOS may restart the device and ask you to confirm after the reboot.

### 4. Install LocalDevVPN

Install the official, unmodified **LocalDevVPN** from the App Store.

LocalDevVPN creates the local route SideStore uses to communicate with the same iPhone. You do not need VPN Super or an additional IKEv2/IPSec tunnel for the current CoreDevice path.

### 5. Find your Wi-Fi network range

The LocalDevVPN addresses must match the subnet of the Wi-Fi network the iPhone is currently using.

On the iPhone:

`Settings -> Wi-Fi -> tap the info button next to the connected network -> IP Address`

On Windows you can also open Command Prompt or PowerShell and run:

```powershell
ipconfig
```

Look for the IPv4 address of the active Wi-Fi adapter.

Example only:

```text
192.168.1.50
```

If your network is `192.168.1.x/24`, two possible unused addresses might be:

```text
Tunnel IP: 192.168.1.240/32
Device IP: 192.168.1.241/32
```

> [!WARNING]
> Do not copy the example addresses unless your Wi-Fi network actually uses that subnet.

Do not use:

- the iPhone's current IP address
- the router or gateway address
- an address already used by another device
- an address outside the current Wi-Fi subnet

### 6. Configure LocalDevVPN

Open:

`LocalDevVPN -> Settings -> Network Configuration`

Then:

1. Enter the **Tunnel IP** with `/32`.
2. Enter the **Device IP** with `/32`.
3. Enable **Allow Intermediate Addresses**.
4. Tap **Done**.
5. Tap **Save & Apply**.
6. Connect LocalDevVPN.
7. Open **Session Details** and confirm the custom addresses were retained.

The two addresses have different jobs:

- **Tunnel IP** is the LocalDevVPN side of the local route.
- **Device IP** is the local peer address SideStore connects to.

If you later move to a Wi-Fi network with a different subnet, update these addresses before refreshing again.

### 7. Create and place the pairing file

A pairing file is the trust record that allows SideStore to authenticate with your own iPhone.

The pairing file must belong to the **exact device** you are setting up. Create it using the SideStore-compatible pairing workflow provided by your installer or pairing tool while the iPhone is connected, unlocked, and trusted by the computer.

If your installer supports placing the pairing file directly into SideStore or the combined app, use that workflow. If SideStore asks you to import a pairing file, select the file created for this exact iPhone.

> [!CAUTION]
> A pairing file contains private device credentials. Do not upload it to GitHub, Discord, issue reports, public file hosts, or screenshots.

If pairing fails, generate or place the file again for the same device before changing any LocalDevVPN settings.

### 8. Open LiveContainer and SideStore

Open **LiveContainer**, then open the embedded **SideStore** and sign in with the Apple Account you use for SideStore.

If iOS asks for Local Network, notification, or other required permissions during setup, allow them before continuing.

### 9. Test manual refresh first

Before enabling automation, verify that the basic setup works.

Open:

`LiveContainer -> SideStore Refresh -> Refresh SideStore now`

Keep Wi-Fi and LocalDevVPN available while the test runs.

**Do not continue to scheduled refresh until the manual refresh succeeds.**

If host replacement closes or relaunches LiveContainer, reopen it and allow the verification step to finish. A refresh is successful only after the new result is verified.

### 10. Enable Background App Refresh

Go to:

`Settings -> General -> Background App Refresh`

Make sure Background App Refresh is enabled for the relevant app.

Also allow notifications if you want deadline warnings and refresh alerts.

### 11. Enable automatic refresh

In LiveContainer's refresh settings, enable automation and choose a schedule.

Available schedules include:

- Six-hour
- Daily
- Weekly

**Daily or Six-hour is recommended** because it gives iOS more chances to run before the normal seven-day Personal Team signing window expires.

The selected time is a **target deadline**, not an exact alarm time. iOS decides when background work actually starts and may delay or omit a background launch.

## Normal daily use

After setup, normal use is simple:

- Keep Wi-Fi available.
- Keep LocalDevVPN correctly configured and connected when possible.
- Keep Background App Refresh enabled.
- Check refresh history occasionally to confirm successful runs.
- If you change to a Wi-Fi network with a different subnet, update the LocalDevVPN Tunnel IP and Device IP.

A computer should not normally be required for refresh after the initial setup. Cellular-only refresh is not currently supported by the stable release.

## Updating to a new version

### Combined LiveContainer + SideStore

Install the new combined IPA **over the existing LiveContainer installation** using the same Apple Account / Personal Team and matching identifiers.

**Do not delete LiveContainer first.** Deleting it can remove data you expected to keep. Back up important guest data before updating.

### Standalone SideStore

Install a new standalone SideStore build over the matching standalone SideStore installation.

Do not install the standalone IPA over LiveContainer. Standalone-to-combined data migration is not provided.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Untrusted Developer | `Settings -> General -> VPN & Device Management -> Developer App -> Trust` |
| App will not open | Confirm Developer Mode is enabled under `Settings -> Privacy & Security` |
| Pairing error | Confirm the pairing file belongs to this exact device and was placed/imported correctly |
| LocalDevVPN is not ready | Recheck Network Configuration, `/32` addresses, Allow Intermediate Addresses, and Session Details |
| Manual refresh fails | Confirm Wi-Fi, LocalDevVPN, pairing, SideStore sign-in, and the refresh status/history |
| Refresh stopped after changing Wi-Fi | Reconfigure the LocalDevVPN addresses for the new Wi-Fi subnet |
| Scheduled refresh was missed | Confirm manual refresh works, Background App Refresh is enabled, and remember the selected time is not an exact wake time |
| Guest signature warning on v2.0.1 | The warning is separate from the verified refresh result. Try opening the named guest and report whether it launches |
| Cellular-only refresh does not work | Cellular-only transport is experimental and is not supported by the stable release |

For deeper troubleshooting and proof status, see [Verification](docs/VERIFICATION.md) and [Compatibility](docs/COMPATIBILITY.md).

## Features

- **Combined LiveContainer + SideStore:** one v2 IPA with SideStore embedded.
- **On-device refresh transport:** LocalDevVPN + CoreDevice path without a PC during normal refresh runtime.
- **Manual and scheduled refresh:** six-hour, daily, and weekly scheduling options.
- **Recovery logic:** bounded retry, launch/resume recovery, and explicit failure states.
- **Refresh history:** persistent results with deletion and clear controls.
- **Deadline protection:** optional AlarmKit alerts on supported iOS versions, with notification fallback.
- **Embedded SideStore fixes:** startup, authentication, host identity, database retry, and shared-Keychain handling.
- **Guest Return controls:** return from fullscreen LiveProcess guests to LiveContainer without intentionally terminating a healthy retained guest.
- **v2.0.1 guest-signature fix:** guest signature diagnostics are advisory and no longer turn an otherwise verified refresh into a false failure.

## Guest Return behavior

| Execution mode | Return behavior |
| --- | --- |
| Windowed LiveProcess multitasking | Floating Return button is hidden. Use the normal window controls. |
| Fullscreen or maximized LiveProcess | Return activates or minimizes back to the host without intentionally terminating the guest. |
| Retained guest reopened | The existing instance is reused when it is still alive. Stale instances are cleaned before a cold launch. |
| Direct host-process guest | Uses the existing restart-return path. Guest memory is not preserved. |

Long-press the Return button to collapse it to an edge tab. Tap the tab to restore it. Position is saved and clamped after resizing.

iOS may still suspend or terminate guest processes because of crashes, memory pressure, or system lifecycle policy.

## Standalone SideStore screenshots

The screenshots below show the standalone SideStore interface. They are useful for the refresh settings, but they do not represent the combined LiveContainer UI.

<p align="center">
  <img src="docs/screenshots/settings-refreshing-apps.png" width="240" alt="SideStore Refreshing Apps settings">
  <br>
  <strong>Refreshing Apps settings</strong>
</p>

<p align="center">
  <img src="docs/screenshots/refresh-schedule-main.png" width="240" alt="SideStore Refresh Schedule">
  <br>
  <strong>Refresh Schedule</strong>
</p>

<p align="center">
  <img src="docs/screenshots/refresh-schedule-options.png" width="240" alt="Six-hour, daily, and weekly refresh schedule options">
  <br>
  <strong>Six-hour, daily, or weekly</strong>
</p>

<p align="center">
  <img src="docs/screenshots/refresh-history.png" width="240" alt="SideStore refresh history">
  <br>
  <strong>Persistent refresh history</strong>
</p>

<p align="center">
  <img src="docs/screenshots/refresh-preferred-time-picker.png" width="240" alt="SideStore preferred refresh time picker">
  <br>
  <strong>Preferred refresh time picker</strong>
</p>

## Technical architecture

```text
LiveContainer triggers / embedded SideStore
    -> refresh coordination and eligibility
    -> Wi-Fi / LocalDevVPN preflight
    -> signing and refresh pipeline
    -> Lockdown -> CoreDeviceProxy TLS -> CDTunnel
    -> userspace adapter -> RSD
    -> AFC staging / InstallationProxy and profile operations
    -> correlated verification results and history
```

The CoreDevice path preserves service TLS, contiguous CDTunnel writes, heartbeat during transport operations, packet-size and flow-control fixes, and corrected FFI ownership.

The stable product path does not currently use the experimental cellular research work.

## Verification status

| Scope | Current evidence |
| --- | --- |
| Standalone manual CoreDevice refresh | Verified on iPhone 12 / iOS 26.6.1 |
| Standalone scheduled refresh with PC disconnected | Recorded proof is available in the verification report |
| Combined v2.0.1 build and packaging | Release builds and package/runtime checks passed |
| v2.0.1 guest-signature regression fix | 77 repository tests: 75 passed, 2 skipped; affected-device confirmation is still being expanded |
| Background scheduling | Best effort. iOS controls task launch timing |
| Cellular-only refresh | Experimental, not supported in the stable release |
| Guest process retention | Best effort. iOS may suspend or terminate a guest |

See [docs/VERIFICATION.md](docs/VERIFICATION.md) for the distinction between build verification, device observation, and proven refresh success.

### v2.0.1 provenance

- Builder/tag commit: `348af2d3f4e411f7c02cc225aac20ac4fcc8983a`
- CI run: [34313715646](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34313715646)
- SHA-256: `486d8c55810e421d6fa7fda0897832e92d3d3045c52d4ec070e14d80fd92c25e`
- Release: [LiveContainer + SideStore Auto-Refresh v2.0.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.0.1)

## Build it yourself

This repository contains build-time patches rather than permanent vendored copies of LiveContainer and SideStore. The workflows fetch pinned upstream revisions.

1. Fork the repository and enable GitHub Actions.
2. Run **LiveContainer embedded SideStore build** for the combined IPA, or **Build Current SideStore** for standalone.
3. Run the workflow on `main`.
4. Download the successful run artifact.
5. Sign the IPA with your own Apple Account / Personal Team before installation.

[Combined workflow](.github/workflows/livecontainer-build.yml) | [Standalone workflow](.github/workflows/build-current.yml)

## Development checks

```bash
python -m unittest discover -s tests -v
git diff --check
```

Compiler-dependent tests require their toolchains. Source and static checks do not replace real-device validation.

## Security, licensing, and contributions

Never publish pairing files, credentials, private keys, personal signed IPAs, unnecessary device identifiers, or complete private device logs.

Original repository-authored work is MIT-licensed unless stated otherwise. Upstream code and derived binaries retain their applicable licenses.

[Contributing](CONTRIBUTING.md) | [Security](SECURITY.md) | [License](LICENSE) | [Third-party notices](THIRD_PARTY_NOTICES.md)

Compatibility reports are welcome. Include your device model, iOS version, standalone or combined variant, Wi-Fi network family, LocalDevVPN status, and non-sensitive refresh result in [Issue #1](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/1).
