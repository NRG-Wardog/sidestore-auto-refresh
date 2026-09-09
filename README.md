# LiveContainer + SideStore Auto-Refresh

[![Combined Build](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/livecontainer-build.yml/badge.svg)](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/livecontainer-build.yml)
[![Release](https://img.shields.io/github/v/release/NRG-Wardog/sidestore-auto-refresh)](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

LiveContainer + SideStore Auto-Refresh provides two builds that share the same basic setup: a standalone SideStore build and a combined LiveContainer + embedded SideStore build.

A computer is required for the initial signing, installation, and pairing setup. Normal refresh operation is designed to run on the iPhone without keeping the computer connected.

> [!IMPORTANT]
> The current stable refresh path requires **Wi-Fi + the official App Store LocalDevVPN**. Cellular-only refresh is experimental and is not part of the stable release.

## Which version should I download?

| What you want | Recommended download |
| --- | --- |
| LiveContainer + SideStore in one app | **[Combined v2.0.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.0.1/LiveContainer-SideStore-AutoRefresh-v2.0.1.ipa)** |
| SideStore only | **[Standalone v1.0.2](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.2/SideStore-CoreDevice-AutoRefresh-v1.0.2.ipa)** |

If you want LiveContainer, use **v2.0.1**. SideStore is already embedded inside it, so you do not need to install a separate SideStore copy for that setup.

Combined release: [v2.0.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.0.1) | [Checksums](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.0.1/SHA256SUMS.txt)

Standalone release: [v1.0.2](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.2) | [Release notes](docs/RELEASE_NOTES_v1.0.2.md)

## Recommended installer

For beginners, **[iLoader](https://github.com/nab138/iloader/releases/latest)** is the recommended example installer.

Official iLoader project: [github.com/nab138/iloader](https://github.com/nab138/iloader)

Why it is a good fit for this project:

- It can install SideStore or LiveContainer + SideStore.
- It can import custom IPA files.
- It can generate and place pairing files.
- It supports pairing management for SideStore-compatible apps.
- It is available for Windows, macOS, and Linux.

Other compatible installers may work, but this README uses iLoader as the beginner example because it can handle both installation and pairing setup in one tool.

### Windows prerequisite for iLoader

Install Apple's device drivers first. The normal beginner route is installing iTunes on Windows, then connecting the iPhone by USB and tapping **Trust** when iOS asks whether to trust the computer.

## What you need

Before starting, make sure you have:

- An iPhone or iPad running a supported iOS version. See [Compatibility](docs/COMPATIBILITY.md).
- A Free Apple Account / Personal Team for signing.
- A Windows, macOS, or Linux computer for the initial setup.
- An installer. **iLoader is recommended for beginners.**
- The official **LocalDevVPN** from the App Store.
- A Wi-Fi connection.
- A USB cable for the initial installation and pairing step.

You do not need to understand CoreDevice, RSD, TLS, or the internal transport before using the project.

# Global first-time setup

The steps in this section apply to **both**:

- Standalone SideStore v1.x
- Combined LiveContainer + SideStore v2.x

Complete the global setup first, then follow the short variant-specific section for the build you installed.

## 1. Download your IPA

Choose one:

### Combined LiveContainer + SideStore

**[Download v2.0.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.0.1/LiveContainer-SideStore-AutoRefresh-v2.0.1.ipa)**

### Standalone SideStore

**[Download v1.0.2](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.2/SideStore-CoreDevice-AutoRefresh-v1.0.2.ipa)**

## 2. Install iLoader

Download the latest iLoader release for your computer:

**[Download iLoader](https://github.com/nab138/iloader/releases/latest)**

Then:

1. Connect the iPhone to the computer by USB.
2. Unlock the iPhone.
3. Tap **Trust** if iOS asks whether to trust the computer.
4. Open iLoader.
5. Sign in with the Apple Account you want to use for sideloading.
6. Use iLoader's IPA import/install option and select the IPA downloaded from this repository.

> [!NOTE]
> iLoader also has built-in SideStore and LiveContainer + SideStore installation actions. For this project, use the IPA from this repository so you install the modified Auto-Refresh build described here.

## 3. Trust the sideloaded app on iPhone

If iOS shows **Untrusted Developer** or refuses to open the installed app:

`Settings -> General -> VPN & Device Management`

Under **Developer App**, select the Apple Account used to sign the IPA and tap **Trust**.

The Developer App entry normally appears only after a sideloaded app has been installed.

## 4. Enable Developer Mode

Go to:

`Settings -> Privacy & Security -> Developer Mode`

Turn Developer Mode on.

iOS may restart the device. Confirm **Turn On** after the reboot if requested.

## 5. Set up the pairing file

A pairing file is the trust record that lets SideStore authenticate with the same iPhone it is running on.

For beginners, use iLoader's pairing management instead of manually moving pairing files through Files.

Recommended flow:

1. Keep the iPhone connected by USB and unlocked.
2. Open iLoader.
3. Use its pairing management / pairing placement feature for the installed SideStore-compatible app.
4. Let iLoader create and place the required pairing data for this exact iPhone.

The pairing file must belong to the **same physical iPhone or iPad** you are setting up.

> [!CAUTION]
> Pairing files contain private device credentials. Never upload them to GitHub, Discord, issue reports, public file hosts, or screenshots.

If pairing later fails, recreate or replace the pairing file for the same device before changing unrelated settings.

## 6. Install LocalDevVPN

Install the official, unmodified **LocalDevVPN** from the App Store.

LocalDevVPN creates the local route SideStore uses to communicate with the same iPhone.

You do not need VPN Super or an additional IKEv2/IPSec tunnel for the current stable CoreDevice path.

## 7. Find your Wi-Fi network range

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

If your Wi-Fi network is `192.168.1.x/24`, two possible unused addresses might be:

```text
Tunnel IP: 192.168.1.240/32
Device IP: 192.168.1.241/32
```

> [!WARNING]
> Do not copy the example addresses unless your Wi-Fi network actually uses the same subnet.

Do not use:

- the iPhone's current IP address
- the router or gateway address
- an address already used by another device
- an address outside the current Wi-Fi subnet

## 8. Configure LocalDevVPN

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

## 9. Enable Background App Refresh

Go to:

`Settings -> General -> Background App Refresh`

Make sure Background App Refresh is enabled.

Also allow notifications if you want deadline warnings and refresh alerts.

# Finish setup: Combined LiveContainer + SideStore

Use this section only if you installed **v2.0.1**.

## 1. Open LiveContainer and embedded SideStore

Open **LiveContainer**, then open the embedded **SideStore**.

Sign into SideStore with the Apple Account you use for SideStore.

If iOS asks for Local Network, notifications, or other required permissions, allow them before continuing.

## 2. Test manual refresh first

Open:

`LiveContainer -> SideStore Refresh -> Refresh SideStore now`

Keep Wi-Fi and LocalDevVPN available while the test runs.

**Do not enable scheduled refresh until the manual refresh succeeds.**

If host replacement closes or relaunches LiveContainer, reopen it and allow verification to finish. A refresh is successful only after the new result is verified.

## 3. Enable automatic refresh

In LiveContainer's SideStore refresh settings, enable automation and choose a schedule.

Available schedules include:

- Six-hour
- Daily
- Weekly

**Daily or Six-hour is recommended** because it gives iOS more chances to run before the normal seven-day Personal Team signing window expires.

The selected time is a **target deadline**, not an exact alarm time. iOS controls when background work actually starts and may delay or omit a background launch.

# Finish setup: Standalone SideStore

Use this section only if you installed **v1.0.2**.

## 1. Open SideStore

Open SideStore and sign in with the Apple Account you use for SideStore.

## 2. Test a manual refresh

Keep Wi-Fi and LocalDevVPN connected, then perform a normal manual SideStore refresh.

Confirm the refresh succeeds before enabling automation.

## 3. Enable automatic refresh

Open:

`SideStore -> Settings -> Refreshing Apps -> Refresh Schedule`

Choose the schedule you want and verify the refresh history after the next run.

The standalone build supports manual, six-hour, daily, and weekly refresh options.

The selected time is not a guaranteed iOS alarm. Background execution is controlled by iOS.

## Normal daily use

After setup, normal use is simple:

- Keep Wi-Fi available.
- Keep LocalDevVPN correctly configured and connected when possible.
- Keep Background App Refresh enabled.
- Check refresh history occasionally to confirm successful runs.
- If you change to a Wi-Fi network with a different subnet, update the LocalDevVPN Tunnel IP and Device IP.

A computer should not normally be required for refresh after the initial setup.

Cellular-only refresh is not currently supported by the stable release.

## Updating to a new version

### Combined LiveContainer + SideStore

Install the new combined IPA **over the existing LiveContainer installation** using the same Apple Account / Personal Team and matching identifiers.

**Do not delete LiveContainer first.** Back up important guest data before updating.

### Standalone SideStore

Install a new standalone SideStore build over the existing matching SideStore installation.

**Do not delete SideStore first.** Replacing the installation helps preserve pairing data, account state, and the SideStore database.

Do not install the standalone IPA over LiveContainer. Standalone-to-combined data migration is not provided.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| iLoader does not see the iPhone on Windows | Confirm iTunes / Apple device drivers are installed, reconnect USB, unlock the phone, and tap Trust |
| Untrusted Developer | `Settings -> General -> VPN & Device Management -> Developer App -> Trust` |
| App will not open | Confirm Developer Mode is enabled under `Settings -> Privacy & Security` |
| Pairing error | Use iLoader pairing management again and confirm the pairing belongs to this exact device |
| LocalDevVPN is not ready | Recheck Network Configuration, `/32` addresses, Allow Intermediate Addresses, and Session Details |
| Manual refresh fails | Confirm Wi-Fi, LocalDevVPN, pairing, SideStore sign-in, and refresh status/history |
| Refresh stopped after changing Wi-Fi | Reconfigure the LocalDevVPN addresses for the new Wi-Fi subnet |
| Scheduled refresh was missed | Confirm manual refresh works, Background App Refresh is enabled, and remember the selected time is not an exact wake time |
| Guest signature warning on v2.0.1 | The warning is separate from the verified refresh result. Try opening the named guest and report whether it launches |
| Cellular-only refresh does not work | Cellular-only transport is experimental and is not supported by the stable release |

For deeper troubleshooting and proof status, see [Verification](docs/VERIFICATION.md) and [Compatibility](docs/COMPATIBILITY.md).

## Features

### Shared SideStore refresh features

- On-device LocalDevVPN + CoreDevice refresh path.
- Manual refresh.
- Six-hour, daily, and weekly schedules.
- Persistent refresh history.
- Bounded retry and recovery logic.
- Deadline and notification support.
- Free Apple Account / Personal Team support.
- No PC required during normal refresh runtime after initial setup.

### Combined v2 features

- LiveContainer + embedded SideStore in one IPA.
- Embedded SideStore startup and authentication fixes.
- Host identity and shared-Keychain handling.
- Guest Return controls.
- Host handoff and verification logic.
- v2.0.1 guest-signature fix so advisory guest checks do not overwrite a verified successful refresh.

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
