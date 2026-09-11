# LiveContainer + SideStore Auto-Refresh

[![Combined Build](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/livecontainer-build.yml/badge.svg)](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/livecontainer-build.yml)
[![Standalone Build](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/build-publish-v1.0.3-r6.yml/badge.svg?branch=release%2Fv1.0.3)](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/build-publish-v1.0.3-r6.yml)
[![Release](https://img.shields.io/github/v/release/NRG-Wardog/sidestore-auto-refresh)](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An independent, open-source build based on **SideStore** and **LiveContainer**, focused on reliable same-device refresh, clear scheduling, verification, and beginner-friendly setup.

Two variants are available: **standalone SideStore** and **LiveContainer with this modified SideStore embedded**. A computer is needed for the initial install and pairing setup. After that, the stable refresh path is designed to run on the iPhone without keeping the computer connected.

> [!IMPORTANT]
> The current stable refresh path requires **Wi-Fi + the official App Store LocalDevVPN**. Cellular-only refresh is experimental and is not part of the stable release.

> [!NOTE]
> This is not an official SideStore or LiveContainer release. For stock behavior and upstream support, use the official [SideStore](https://github.com/SideStore/SideStore) and [LiveContainer](https://github.com/LiveContainer/LiveContainer) projects.

## What is this?

This project is a modified SideStore / LiveContainer build that makes same-device refresh easier to operate and easier to verify.

It adds:

- manual refresh directly on the iPhone
- six-hour, daily, and weekly refresh schedules
- preferred refresh time controls
- persistent refresh history
- bounded retry and recovery logic
- explicit refresh verification
- a same-device **LocalDevVPN + CoreDevice** transport path
- a combined LiveContainer build with the modified SideStore already embedded
- guest Return controls in the combined build
- **Start Collapsed** and **custom Return button colors** in combined v2.1.0

The goal is simple: after the initial installation and pairing setup, normal refresh should happen on the iPhone without leaving a PC connected.

## Preview

### Combined v2.1.0

These are actual screenshots from **LiveContainer + embedded SideStore v2.1.0**.

<table>
<tr>
<td align="center"><img src="docs/screenshots/WhatsApp%20Image%202026-09-10%20at%2016.31.46.jpeg" width="220" alt="Combined v2.1.0 refresh status"><br><strong>Refresh Status</strong></td>
<td align="center"><img src="docs/screenshots/WhatsApp%20Image%202026-09-10%20at%2016.31.45%20(1).jpeg" width="220" alt="Combined v2.1.0 refresh schedule"><br><strong>Refresh Schedule</strong></td>
<td align="center"><img src="docs/screenshots/WhatsApp%20Image%202026-09-10%20at%2016.31.45.jpeg" width="220" alt="Combined v2.1.0 refresh history"><br><strong>Refresh History</strong></td>
<td align="center"><img src="docs/screenshots/WhatsApp%20Image%202026-09-10%20at%2016.31.46%20(1).jpeg" width="220" alt="Combined v2.1.0 guest controls"><br><strong>Guest Controls</strong></td>
</tr>
</table>

The Guest Controls screen in v2.1.0 includes **Show Return Button**, **Start Collapsed**, **Use Custom Colors**, **Icon Color**, and **Button Background**.

### Standalone preview (v1.0.2 screenshots)

These screenshots show the earlier standalone SideStore v1.0.2 interface. The current standalone download is v1.0.3, based on SideStore 0.7.0 nightly.

<table>
<tr>
<td align="center"><img src="docs/screenshots/settings-refreshing-apps.png" width="230" alt="SideStore Refreshing Apps settings"><br><strong>Refreshing Apps</strong></td>
<td align="center"><img src="docs/screenshots/refresh-schedule-main.png" width="230" alt="SideStore Refresh Schedule"><br><strong>Refresh Schedule</strong></td>
<td align="center"><img src="docs/screenshots/refresh-history.png" width="230" alt="SideStore refresh history"><br><strong>Refresh History</strong></td>
</tr>
</table>

## Quick navigation

- [Choose a build](#which-version-should-i-download)
- [What is different from the original projects?](#how-this-project-differs-from-the-original-projects)
- [Recommended installer](#recommended-installer)
- [Global first-time setup](#global-first-time-setup)
- [Combined setup](#combined-setup-v2)
- [Standalone setup](#standalone-setup-v1)
- [Troubleshooting](#troubleshooting)
- [Technical architecture](#technical-architecture)

## Which version should I download?

| What you want | Use | Download |
| --- | --- | --- |
| LiveContainer with the modified SideStore built in | **Combined v2.1.0** | **[Download combined IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.1.0/LiveContainer-SideStore-AutoRefresh-v2.1.0.ipa)** |
| SideStore only, with normal separately installed sideloaded apps | **Standalone v1.0.3** | **[Download standalone IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.3/SideStore-CoreDevice-AutoRefresh-v1.0.3.ipa)** |

If you want LiveContainer, install **v2.1.0**. SideStore is already embedded inside it, so do not install a separate SideStore copy for the same combined setup.

**Combined:** [v2.1.0 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.0) | [SHA256SUMS](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.1.0/SHA256SUMS.txt)

**Standalone:** [v1.0.3 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.3) | [SHA256SUMS](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.3/SHA256SUMS.txt) | [release notes](docs/RELEASE_NOTES_v1.0.3.md)

## What's new in standalone v1.0.3?

Standalone v1.0.3 updates the app to **SideStore 0.7.0 nightly**, including the newer **SideSign authentication path**.

- Preserves LocalDevVPN -> Lockdown/CoreDevice -> RSD transport, manual refresh, scheduled refresh, history, retries, and verification.
- Composite pairing records prefer Lockdown/CoreDevice, and this path works with LocalDevVPN without an additional IKEv2/IPsec interface.
- Adapts signing instrumentation and background database startup to the SideStore 0.7 APIs.
- Records self-refresh reconciliation success only after the database update succeeds.

The IPA build, automated checks, and published-download verification passed. Device testing of this exact v1.0.3 build is still pending; earlier standalone device evidence is listed separately below. See the [release notes](docs/RELEASE_NOTES_v1.0.3.md) for exact source revisions and validation.

## What's new in v2.1.0?

Combined v2.1.0 keeps the existing v2 refresh stack and adds new Guest Return appearance controls:

- **Start Collapsed:** guests can open with the Return control already collapsed to an edge tab.
- **Use Custom Colors:** enables saved custom colors for the Return control.
- **Icon Color:** changes the Return icon and the collapsed tab icon.
- **Button Background:** changes the expanded Return button background.
- Guest-control appearance preferences persist across launches.
- Manual long-press collapse and tap-to-expand behavior remain available.
- The collapsed tab stays transparent while using the selected icon color.

The release still includes manual refresh, scheduled refresh, refresh history, verification, LocalDevVPN/CoreDevice transport, and the embedded SideStore integration.

## How this project differs from the original projects

This repository builds on upstream work. It does not claim to have invented SideStore background refresh or the LiveContainer + SideStore concept.

- **Upstream SideStore already supports sideloading and periodic background refresh.**
- **Upstream LiveContainer already offers a build with SideStore included.**

The purpose of this repository is to add a specific refresh transport, scheduling, verification, diagnostics, and LiveContainer integration on top of pinned upstream revisions.

| Area | Original / upstream behavior | This project adds or changes |
| --- | --- | --- |
| SideStore refresh | SideStore already resigns and refreshes apps | A patched same-device **LocalDevVPN + CoreDevice** refresh path, plus explicit transport diagnostics |
| Scheduling | Upstream SideStore already has background refresh behavior | Explicit **six-hour, daily, and weekly** schedules, preferred time controls, persistent history, bounded retry, and deadline protection |
| Verification | Normal upstream refresh state and results | Run-correlated verification so task launch or handoff is not treated as refresh success by itself |
| Combined LiveContainer build | Upstream already offers LiveContainer + SideStore | Embeds this repository's modified SideStore and adds host-level refresh coordination, result bridging, startup/authentication fixes, and shared-Keychain handling |
| Guest navigation | Upstream LiveContainer provides its normal guest controls | Adds project-specific Return controls, collapse behavior, startup-collapsed mode, and custom colors |
| Guest signature diagnostics | Upstream behavior differs | Keeps guest signature checks advisory so an unrelated guest check cannot overwrite a verified successful refresh |
| Provenance | Upstream releases are built by their own projects | Published binaries here are tied to documented builder commits, CI runs, checksums, and verification evidence |

### What this project does not change

- It does **not** remove the normal Personal Team signing expiration. It refreshes before expiration.
- It does **not** guarantee an exact background execution time. iOS controls task scheduling.
- It does **not** make cellular-only refresh a stable feature yet.
- It does **not** require a jailbreak for the supported stable path.
- It does **not** make LiveContainer guests equivalent to separately installed iOS apps.
- It does **not** guarantee that iOS will keep every guest process alive.

Because the combined build modifies LiveContainer and contains embedded SideStore code, use the same trust model you would use for any modified LiveContainer build. This repository is open source, and you can [build it yourself](#build-it-yourself) if you prefer to verify the build path personally.

## Setup at a glance

```text
Choose v1 or v2
    -> install iLoader
    -> connect and trust the iPhone
    -> Import IPA
    -> trust the sideloaded app
    -> enable Developer Mode
    -> place pairing data with iLoader
    -> install and configure LocalDevVPN
    -> run one manual refresh
    -> enable automatic refresh
```

## Recommended installer

For beginners, the recommended example installer is **[iLoader](https://github.com/nab138/iloader/releases/latest)**.

Official project: [github.com/nab138/iloader](https://github.com/nab138/iloader)

Use **iLoader 2.3.1 or newer** for current SideStore / LiveContainer installation flows.

It is a good fit because it can:

- import and sign a custom IPA
- detect and select the connected iPhone
- manage pairing data
- place pairing files into supported SideStore-compatible apps
- run on Windows, macOS, and Linux

The important point is to use **Import IPA** with the IPA downloaded from this repository. iLoader also offers built-in upstream SideStore and LiveContainer + SideStore installers, but those are not this project's modified builds.

### Windows prerequisite

Make sure Apple's device drivers are installed before using iLoader. iLoader's documented Windows path uses iTunes. Connect the iPhone by USB, unlock it, and tap **Trust** when iOS asks whether to trust the computer.

If iLoader cannot see the device, verify that Apple's software can see it before troubleshooting this project.

## What you need

Before starting, have the following ready:

- an iPhone or iPad supported by the build you want to test
- a Free Apple Account / Personal Team for signing
- a Windows, macOS, or Linux computer for the initial setup
- a USB cable for installation and pairing
- [iLoader](https://github.com/nab138/iloader/releases/latest), or another compatible installer
- the official [LocalDevVPN on the App Store](https://apps.apple.com/us/app/localdevvpn/id6755608044), by **Coxson Engineering LLC**
- a Wi-Fi network

See [Compatibility](docs/COMPATIBILITY.md) for the current device evidence. You do not need to understand CoreDevice, RSD, TLS, or CDTunnel before using the project.

## Global first-time setup

These steps apply to **both standalone v1 and combined v2**. Complete this section first, then finish the short setup for the variant you installed.

### 1. Download the IPA

Choose exactly one build for the setup you want:

**Combined LiveContainer + SideStore v2.1.0**

[Download `LiveContainer-SideStore-AutoRefresh-v2.1.0.ipa`](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.1.0/LiveContainer-SideStore-AutoRefresh-v2.1.0.ipa)

**Standalone SideStore v1.0.3**

[Download `SideStore-CoreDevice-AutoRefresh-v1.0.3.ipa`](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.3/SideStore-CoreDevice-AutoRefresh-v1.0.3.ipa)

### 2. Install iLoader and connect the device

1. Download the latest [iLoader release](https://github.com/nab138/iloader/releases/latest).
2. Connect the iPhone to the computer by USB.
3. Unlock the iPhone and leave it unlocked during the initial setup.
4. Tap **Trust** on the iPhone if the computer trust prompt appears.
5. Open iLoader.
6. Under the device section, select the connected iPhone.
7. Sign in to your Apple Account. iLoader currently labels this section **Apple ID**.

### 3. Import and install this project's IPA

In iLoader:

1. Find the **Installers** section.
2. Click **Import IPA**.
3. Select the v1 or v2 IPA you downloaded from this repository.
4. Let iLoader sign and install it with your Apple Account.
5. Wait for the installation to finish before disconnecting the device.

> [!IMPORTANT]
> Do not select iLoader's built-in stock **SideStore** or **LiveContainer + SideStore** build if your goal is to install this repository. Use **Import IPA** and choose the file downloaded from this repository.

### 4. Trust the sideloaded app on iPhone

If the installed app will not open or iOS shows **Untrusted Developer**:

`Settings -> General -> VPN & Device Management`

Under **Developer App**, select the Apple Account used to sign the IPA, then tap **Trust**.

### 5. Enable Developer Mode

Go to:

`Settings -> Privacy & Security -> Developer Mode`

Turn **Developer Mode** on. iOS may restart the device. After reboot, confirm **Turn On** if requested.

### 6. Place the pairing file with iLoader

A pairing file is the trust record that lets SideStore authenticate with the same iPhone it is running on. It must belong to the exact physical device you are setting up.

Keep the iPhone connected by USB and unlocked. Then in iLoader:

1. Open the **Management** section.
2. Open **Manage Pairing File**.
3. If the newly installed app is not shown, click **Rescan Installed Apps**.
4. Find the installed SideStore-compatible app.
5. Click **Place** next to that app. You can use **Place In All Apps** if that is intentionally what you want.
6. Wait for **Pairing file placed successfully!** before continuing.

> [!CAUTION]
> Pairing files contain private device credentials. Never upload them to GitHub, Discord, an issue report, a public file host, or a screenshot.

If pairing later fails, reconnect the same device by USB and repeat **Manage Pairing File -> Place** before changing unrelated settings.

### 7. Install the correct LocalDevVPN

Install the official **[LocalDevVPN](https://apps.apple.com/us/app/localdevvpn/id6755608044)** from the App Store.

Check the developer name: **Coxson Engineering LLC**.

LocalDevVPN creates the local virtual route used by the stable same-device refresh path. VPN Super and an additional IKEv2/IPSec tunnel are not required for this project's current stable CoreDevice path.

### 8. Find the iPhone's Wi-Fi subnet

LocalDevVPN must use addresses from the **same IPv4 subnet** as the Wi-Fi network currently joined by the iPhone.

On iPhone:

`Settings -> Wi-Fi -> tap the info button next to the connected network`

Look at both:

- **IP Address**
- **Subnet Mask**

Example:

```text
IP Address:  192.168.1.50
Subnet Mask: 255.255.255.0
```

That example is the `192.168.1.x/24` subnet.

> [!WARNING]
> If your subnet mask is not `255.255.255.0` and you are not sure which addresses belong to the subnet, do not guess. Check your router configuration or ask for help with the exact IP address and subnet mask.

### 9. Choose two unused addresses

You need two unused IPv4 addresses in that same subnet. The safest approach is to check the router's DHCP range or connected-client list and choose addresses that are not assigned to another device.

Example only for `192.168.1.x/24`:

```text
Tunnel IP: 192.168.1.240/32
Device IP: 192.168.1.241/32
```

Do not use:

- the iPhone's actual IP address
- the router or gateway address
- an IP currently assigned to another device
- an address outside the iPhone's current Wi-Fi subnet

### 10. Configure LocalDevVPN

Open:

`LocalDevVPN -> Settings -> Network Configuration`

Then:

1. Enter the chosen **Tunnel IP** with `/32`.
2. Enter the chosen **Device IP** with `/32`.
3. Enable **Allow Intermediate Addresses**.
4. Tap **Done**.
5. Tap **Save & Apply**.
6. Connect LocalDevVPN.
7. Open **Session Details** and confirm the custom addresses were retained.

> [!IMPORTANT]
> If you move to a Wi-Fi network with a different subnet, update the Tunnel IP and Device IP before refreshing again.

### 11. Enable Background App Refresh

Go to:

`Settings -> General -> Background App Refresh`

Make sure Background App Refresh is enabled globally and for the relevant installed app when iOS exposes a per-app toggle.

Allow notifications if you want refresh status and deadline warnings.

## Combined setup (v2)

Use this section only for **LiveContainer + SideStore v2.1.0**.

### 1. Open LiveContainer and embedded SideStore

Open **LiveContainer**, then open the embedded **SideStore**.

Sign into SideStore with the Apple Account you use for SideStore.

### 2. Run one manual refresh first

Open:

`LiveContainer -> SideStore Refresh -> Refresh SideStore now`

Keep Wi-Fi and LocalDevVPN available while the test runs.

**Do not enable scheduled refresh until this manual refresh succeeds.**

If host replacement closes or relaunches LiveContainer, reopen it and let verification finish. A handoff or background-task launch is not considered success by itself.

### 3. Enable automatic refresh

Open LiveContainer's SideStore refresh settings and enable automation.

Choose one of the supported schedules:

- Six-hour
- Daily
- Weekly

**Daily or Six-hour is recommended** because it gives iOS more opportunities to run before the normal seven-day Personal Team signing window expires.

The selected time is a **target deadline**, not an exact alarm. iOS controls when background work actually starts and may delay or omit a background launch.

## Standalone setup (v1)

Use this section for **standalone SideStore v1.0.3**.

### 1. Open SideStore

Open SideStore and sign in with the Apple Account you use for SideStore.

### 2. Run one manual refresh first

Keep Wi-Fi and LocalDevVPN connected, then perform a normal manual refresh. Confirm that the refresh succeeds before enabling automation.

### 3. Enable automatic refresh

Open:

`SideStore -> Settings -> Refreshing Apps -> Refresh Schedule`

Choose a schedule and verify the result in refresh history after the next run.

The standalone build supports manual, six-hour, daily, and weekly refresh options. The selected time is not a guaranteed iOS alarm. Background execution is controlled by iOS.

## Normal daily use

Once setup is working:

- keep Wi-Fi available
- keep LocalDevVPN correctly configured and connected when possible
- keep Background App Refresh enabled
- check refresh history occasionally for successful runs
- update the LocalDevVPN addresses if you move to a different Wi-Fi subnet

A computer should not normally be required during refresh runtime after initial setup.

Cellular-only refresh is not currently supported by the stable release.

## Updating without losing setup data

### Combined v2

Install the new combined IPA **over the existing LiveContainer installation** using the same Apple Account / Personal Team and matching identifiers.

**Do not delete LiveContainer first.** Back up important guest data before updating.

### Standalone v1

Install a new standalone SideStore build over the existing matching SideStore installation.

**Do not delete SideStore first.** Replacing the installation helps preserve pairing data, Apple account state, and the SideStore database.

Do not install the standalone IPA over LiveContainer. Standalone-to-combined data migration is not provided.

## Troubleshooting

| Problem | Check this first |
| --- | --- |
| iLoader does not see the iPhone on Windows | Confirm Apple's device drivers are installed, reconnect USB, unlock the phone, and tap **Trust** |
| iLoader says no device is selected | Select the iPhone in iLoader before running **Import IPA** or pairing actions |
| App shows Untrusted Developer | `Settings -> General -> VPN & Device Management -> Developer App -> Trust` |
| App will not open | Confirm Developer Mode is enabled in `Settings -> Privacy & Security` |
| Pairing error | Reconnect USB, open **Manage Pairing File**, use **Rescan Installed Apps**, then **Place** again for the exact device |
| LocalDevVPN will not connect | Recheck Network Configuration, both `/32` endpoints, and **Allow Intermediate Addresses** |
| LocalDevVPN connects but refresh fails | Confirm the addresses are in the iPhone's current Wi-Fi subnet and are not already in use |
| Refresh stopped after changing Wi-Fi | Reconfigure Tunnel IP and Device IP for the new subnet |
| Manual refresh fails | Confirm Wi-Fi, LocalDevVPN, pairing, SideStore sign-in, and the recorded refresh result/history |
| Scheduled refresh was missed | Confirm manual refresh works and Background App Refresh is enabled. The selected time is not an exact wake time |
| Not enough App IDs | Free Apple Accounts have registration limits. Do not repeatedly delete/reinstall builds. Wait for registrations to expire or reuse matching identifiers where supported |
| Guest signature warning | The warning is separate from verified refresh status. Open the named guest and report whether it actually launches |
| Cellular-only refresh fails | Cellular-only transport is experimental and is not supported by the stable release |

For deeper investigation, see [Verification](docs/VERIFICATION.md), [Compatibility](docs/COMPATIBILITY.md), and [Issue #1](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/1).

## Features

### Shared refresh features

- same-device LocalDevVPN + CoreDevice refresh path
- Free Apple Account / Personal Team support
- manual refresh
- six-hour, daily, and weekly schedules
- preferred refresh time controls
- persistent refresh history
- bounded retry and recovery logic
- deadline and notification support
- bounded diagnostics for troubleshooting
- no PC required during normal refresh runtime after initial setup

### Combined v2 additions

- LiveContainer with this repository's modified SideStore embedded
- host-level refresh coordination and handoff handling
- run-correlated verification result bridge
- embedded SideStore startup and authentication fixes
- shared-Keychain migration and host identity handling
- Guest Return controls
- advisory guest-signature checks do not overwrite a verified successful refresh
- **v2.1.0:** Start Collapsed
- **v2.1.0:** custom Return icon and background colors

## Guest Return behavior

| Execution mode | Return behavior |
| --- | --- |
| Windowed LiveProcess multitasking | Floating Return button is hidden. Use the normal window controls. |
| Fullscreen or maximized LiveProcess | Return activates or minimizes back to the host without intentionally terminating the guest. |
| Retained guest reopened | The existing instance is reused when it is still alive. Stale instances are cleaned before a cold launch. |
| Direct host-process guest | Uses the existing restart-return path. Guest memory is not preserved. |

Long-press the Return button to collapse it to an edge tab. Tap the tab to restore it. Its position is saved and clamped after resizing.

v2.1.0 adds these options in **LiveContainer Settings > Guest Controls**:

- **Start Collapsed:** new guests open with an edge tab. Tap once to expand, then tap Return to go back. Using Return collapses the control again, including when reopening a retained guest. The option is off by default.
- **Use Custom Colors:** choose the **Icon Color** and **Button Background**. The icon color also applies to the collapsed tab; the tab background stays transparent. Turn this option off to restore system colors. Color choices are saved for later use.

These preferences apply to both fullscreen LiveProcess and direct host-process guests. **Show Return Button** still controls visibility, and windowed multitasking still uses the normal window controls.

## More screenshots

These additional screenshots show the standalone SideStore refresh UI.

<table>
<tr>
<td align="center"><img src="docs/screenshots/refresh-schedule-options.png" width="220" alt="Six-hour, daily, and weekly refresh schedule options"><br><strong>Schedule Options</strong></td>
<td align="center"><img src="docs/screenshots/refresh-preferred-time-picker.png" width="220" alt="SideStore preferred refresh time picker"><br><strong>Preferred Time</strong></td>
<td align="center"><img src="docs/screenshots/refresh-skipped.jpeg" width="220" alt="Skipped SideStore refresh"><br><strong>Skipped Refresh</strong></td>
</tr>
</table>

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

The stable CoreDevice path preserves service TLS, contiguous CDTunnel writes, heartbeat during transport operations, packet-size and flow-control fixes, and corrected FFI ownership. Experimental cellular work is not part of the current stable product path.

### App Layout architecture (Issue #17)

Issue #17 is intentionally a **presentation-layer feature**. It must not modify the refresh transport, signing, authentication, background scheduling, database reconciliation, or CoreDevice pipeline.

```text
Interface settings
    -> persisted AppLayoutPreferences
        -> App Layout: list | grid | compactList
        -> Show app labels: true | false
    -> Apps screen presentation switch
        -> List renderer
        -> Grid renderer
        -> Compact List renderer
    -> shared app model + shared actions
        -> open / refresh / activate / deactivate / context menu
```

The implementation contract is:

- Add a small persisted layout model, for example `AppLayoutStyle: String`, with `list`, `grid`, and `compactList` cases.
- Keep **List** as the default so existing installs retain today's behavior.
- Persist `appLayoutStyle` and `showAppLabels` through SideStore's existing preferences/UserDefaults mechanism. Do not add a new database for UI preferences.
- Extend the existing **Interface -> App Layout** setting instead of creating a second layout control. Show **Show app labels** only when Grid is selected.
- Keep the current list/card renderer as the List path. Do not rewrite working list behavior simply to share code.
- Implement Grid with SwiftUI `LazyVGrid`. Grid cells are icon-first, use consistent adaptive spacing, and optionally render the app name below the icon.
- Implement Compact List with the same app data and actions as List, but with reduced icon size, row height, and vertical padding.
- Route all three renderers through the same existing app model and action handlers. Layout-specific views must not duplicate signing, refresh, activation, installation, or context-menu business logic.
- Hiding Grid labels is visual only. The app name must remain available as an accessibility label.
- Treat [SideloadLabs/AppNest](https://github.com/SideloadLabs/AppNest) as a visual reference only. Do not vendor or copy its implementation unless its code and license are reviewed separately.

#### Patch strategy

This repository should implement the feature as a deterministic build-time patch, consistent with the rest of the project. The expected implementation is a dedicated semantic patch such as `scripts/patch_app_layout.py` that targets the pinned SideStore source rather than vendoring whole upstream files.

Standalone and combined builds currently consume different SideStore source lines. The UI contract should remain the same, but source-specific anchors/adapters may be used where their view structure differs. Do not upgrade the embedded LiveContainer SideStore to official SideStore 0.7 solely to deliver this UI feature.

The patch must be idempotent and fail closed if upstream anchors move. It should touch only the minimum settings/preferences and Apps-screen presentation surface needed for the feature.

#### Verification gates

Before Issue #17 is considered complete:

1. Repository tests prove the patch is idempotent and rejects changed/ambiguous upstream anchors.
2. Generated Swift parses successfully and the build completes for every supported target that receives the feature.
3. List, Grid, Grid without visual labels, and Compact List are all reachable from settings.
4. Layout and label preferences survive relaunch.
5. Open, refresh, activation/deactivation, status display, and context-menu actions still use the same underlying handlers in every layout.
6. Accessibility still exposes the app name when Grid labels are hidden.
7. No transport, signing, authentication, scheduler, or CoreDevice source is changed by this feature.
8. Device screenshots/acceptance checks cover all three layouts before release.

This separation keeps Issue #17 low risk: the renderer changes, while the app lifecycle and refresh stack remain the same.

## Verification status

| Scope | Current evidence |
| --- | --- |
| Standalone v1.0.3 build and packaging | CI passed; published IPA checksum, arm64 executable, background-task configuration, and 13 feature markers independently verified |
| Standalone v1.0.3 device refresh | Device testing of this exact build is pending |
| Earlier standalone manual CoreDevice refresh | Verified on iPhone 12 / iOS 26.6.1; this predates v1.0.3 |
| Earlier standalone scheduled refresh with PC disconnected | Recorded proof predates v1.0.3 and is available in the verification report |
| Combined v2.1.0 build and packaging | Combined CI run completed successfully |
| v2.1.0 Guest Controls | Start Collapsed and custom colors are included in the published combined build |
| Background scheduling | Best effort. iOS controls task launch timing |
| Cellular-only refresh | Experimental, not supported in the stable release |
| Guest process retention | Best effort. iOS may suspend or terminate a guest |

A build completing, a background task starting, or a host handoff occurring is not automatically treated as proof that the signing lifetime was refreshed. See [docs/VERIFICATION.md](docs/VERIFICATION.md) for the exact proof model.

### v2.1.0 provenance

- Builder/tag commit: `2372c3e96132cb06394dafdd9ff74aeca416dd1c`
- Combined CI run: [34497164738](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34497164738)
- IPA: `LiveContainer-SideStore-AutoRefresh-v2.1.0.ipa`
- SHA-256: `6493e1e8c525a3b1ea343973bf199e30069f1c837f1a6175f3befa05d7a0eb50`
- Release: [LiveContainer + SideStore Auto-Refresh v2.1.0](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.0)

### v1.0.3 provenance

- Builder/tag commit: `07d52be7a49f6795b82f081ded2ec94eb44d50df`
- Standalone CI run: [34613911507](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34613911507)
- Upstream app version: SideStore `0.7.0`, build `0700`
- IPA: `SideStore-CoreDevice-AutoRefresh-v1.0.3.ipa`
- SHA-256: `ab35772fe3209618c7bec302e315faea0a35b7ae280edfb9b5390d2aa62c8940`
- Release: [SideStore CoreDevice Auto-Refresh v1.0.3](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.3)
- Pinned sources and validation: [v1.0.3 release notes](docs/RELEASE_NOTES_v1.0.3.md)

## Build it yourself

This repository contains build-time patches rather than permanent vendored copies of LiveContainer and SideStore. Workflows fetch pinned upstream revisions.

1. Fork the repository and enable GitHub Actions.
2. Run **LiveContainer embedded SideStore build** for the combined IPA, or **Build Current SideStore** for standalone.
3. Run the workflow on `main`.
4. Download the successful run artifact.
5. Sign the resulting IPA with your own Apple Account / Personal Team before installing it.

[Combined workflow](.github/workflows/livecontainer-build.yml) | [Standalone workflow](.github/workflows/build-current.yml)

## Development checks

```bash
python -m unittest discover -s tests -v
git diff --check
```

Compiler-dependent tests require their toolchains. Source and static checks do not replace real-device validation.

## Security, privacy, licensing, and support

Never publish pairing files, Apple credentials, private keys, personal signed IPAs, unnecessary device identifiers, or complete private device logs.

The combined build can access data used by LiveContainer and its guests according to LiveContainer's architecture. If the trust model matters to you, review the source and build the IPA yourself.

Original repository-authored work is MIT-licensed unless stated otherwise. Upstream code and derived binaries retain their applicable upstream licenses.

[Contributing](CONTRIBUTING.md) | [Security](SECURITY.md) | [License](LICENSE) | [Third-party notices](THIRD_PARTY_NOTICES.md)

When reporting a compatibility result, include the device model, iOS version, standalone or combined variant, Wi-Fi network family, LocalDevVPN status, and a non-sensitive refresh result. Do not attach secrets or complete private logs. Use [Issue #1](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/1) for compatibility reports.
