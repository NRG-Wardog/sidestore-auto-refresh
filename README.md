# LiveContainer + SideStore Auto-Refresh

[![Combined Build](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/livecontainer-build.yml/badge.svg)](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/livecontainer-build.yml)
[![Standalone Build](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/build-publish-v1.0.3-r6.yml/badge.svg?branch=release%2Fv1.0.3)](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/build-publish-v1.0.3-r6.yml)
[![Release](https://img.shields.io/github/v/release/NRG-Wardog/sidestore-auto-refresh)](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An independent, open-source build based on **SideStore** and **LiveContainer**, focused on reliable same-device refresh, clear scheduling, verification, and beginner-friendly setup.

The recommended combined build is **v3.0.2**, which unifies LiveContainer and SideStore in one interface. **Standalone SideStore v1** remains available, and **combined v2** is the previous interface line. A computer is needed for the initial install and pairing setup. After that, the stable refresh path is designed to run on the iPhone without keeping the computer connected.

> [!IMPORTANT]
> The current stable refresh path requires **Wi-Fi + the official App Store LocalDevVPN**. Cellular-only refresh is experimental and is not part of the stable release.

> [!NOTE]
> This is not an official SideStore or LiveContainer release. For stock behavior and upstream support, use the official [SideStore](https://github.com/SideStore/SideStore) and [LiveContainer](https://github.com/LiveContainer/LiveContainer) projects.


## Why this project exists

I built this project because the stock SideStore refresh path was not reliable on my setup. The key fix was not another shortcut or trigger around the same flow; it was changing the same-device transport used to reach the iPhone's device services.

The stable route used here is:

```text
official LocalDevVPN -> Lockdown -> CoreDeviceProxy TLS -> CDTunnel -> RSD -> AFC / InstallationProxy
```

Once that route was working, the project grew into a more complete LiveContainer + SideStore experience: explicit refresh scheduling, history and verification, stage-specific diagnostics, layout choices, Guest Return controls, and the unified v3 interface.

### What this project improves

| Area | What this project provides |
| --- | --- |
| **Reliable same-device transport** | Replaces the Lockdown service route that failed on my setup with the **LocalDevVPN -> CoreDevice -> RSD** path used by this project |
| **A real unified LiveContainer + SideStore experience** | v3 keeps LiveContainer as the host and exposes SideStore through a bounded service interface, so normal use stays inside **Home / Apps / Sources / Refresh / Settings** instead of jumping into a separate embedded SideStore flow |
| **Explicit refresh automation** | User-controlled **six-hour, daily, and weekly** schedules, preferred time, notifications, deadline awareness, and persistent history |
| **Verified refresh results** | Correlates each run with its actual result, so a task starting, a handoff occurring, or a request being sent is not reported as a successful refresh by itself |
| **Useful diagnostics** | Keeps VPN, CoreDevice, heartbeat, RSD, Lockdown, signing, and installation failures distinguishable instead of collapsing unrelated failures into one generic message |
| **Better app browsing** | Adds **List, Grid, and Compact List**, persistent layout preferences, and optional Grid labels; these project-specific layout controls are not present in the upstream LiveContainer app list checked for this comparison |
| **Better guest navigation** | Adds the project-specific **Return** control, **Start Collapsed**, saved position, and custom icon/background colors for fullscreen/direct guest flows |
| **PC-free refresh runtime after setup** | A computer is still used for initial installation and pairing, but the supported refresh path is designed to operate on the iPhone with Wi-Fi + the official LocalDevVPN and without leaving the PC connected |

<details>
<summary><strong>Technical comparison with upstream</strong></summary>

This is the source-level difference behind the product behavior above:

| Area | Upstream code checked | This project |
| --- | --- | --- |
| Lockdown service route | Direct TCP provider for Lockdown service calls | **CoreDeviceProxy TLS -> CDTunnel -> userspace IPv6 -> RSD** |
| Local-VPN readiness on iOS 26.4+ | Lockdown local-VPN readiness checks for an IKEv2/IPsec interface | The CoreDevice route uses the official LocalDevVPN `utun` path without requiring a second IKEv2/IPsec tunnel |
| Composite pairing file | RemotePairing keys are checked before Lockdown keys | Valid **Lockdown** data is preferred so the CoreDevice route is selected |
| Combined LC + SS navigation | Current LiveContainer code opens/selects a built-in SideStore flow through `openSideStore` / `builtinSideStore` | v3 routes normal SideStore operations through the unified host UI and service bridge instead of switching the user into the embedded SideStore UI |

SideStore's own README describes periodic background refresh. The improvement here is not a claim that background refresh never existed; it is the combination of the changed transport route with explicit schedules, observability, history, verification, and failure handling.

_Comparison basis: upstream SideStore `develop` at `797e0d46c46491c7fba1192c789c016d24b35591`, minimuxer `20248550bbe014805460d4fa22ea69f146d338a0`, and LiveContainer source checked on September 16, 2026. Upstream can change after this snapshot._

</details>

---

<h1 align="center"><a href="#installation">GO TO INSTALLATION</a></h1>
<p align="center"><strong>Download the correct IPA, install it, pair the device, configure LocalDevVPN, and verify your first refresh.</strong></p>
<p align="center"><a href="#what-this-project-improves">See what this project improves</a> · <a href="#installation">Start installation</a></p>

---

## Installation

### Before you start

For most users, install **Unified v3.0.2**. It is the recommended LiveContainer + SideStore build.

<p align="center"><a href="https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v3.0.2/LiveContainer-SideStore-AutoRefresh.ipa"><strong>DOWNLOAD v3.0.2 IPA</strong></a></p>

> [!IMPORTANT]
> For installation, download **`LiveContainer-SideStore-AutoRefresh.ipa`**. You do **not** need `build-evidence.zip` or GitHub's source-code archives to install the app.

Check these items before installing:

| Check | What you need |
| --- | --- |
| **Device** | A supported iPhone or iPad, unlocked during initial setup |
| **Computer** | Windows, macOS, or Linux for initial installation and pairing only |
| **Installer** | [iLoader](https://github.com/nab138/iloader/releases/latest) **2.3.1 or newer**, or another compatible IPA installer |
| **Apple account** | The Apple Account / Personal Team that will sign the IPA |
| **Developer Mode** | Enabled after installation |
| **Pairing** | Pairing data generated for the **exact physical device** being used |
| **Network** | Wi-Fi, with the iPhone's current **IP Address and Subnet Mask** available |
| **VPN** | Official App Store **LocalDevVPN** by **Coxson Engineering LLC** |
| **App extensions** | Keep the required combined-build extensions, including **LiveProcess** |
| **Existing install** | For upgrades, install over the matching existing LiveContainer build using the same signing identity; do not delete it first as a routine step |

If you want to understand the differences first, see [What this project improves](#what-this-project-improves). Otherwise, follow the steps below in order.

### Step 1: Download the correct IPA

Download the recommended v3 build:

**[LiveContainer-SideStore-AutoRefresh.ipa](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v3.0.2/LiveContainer-SideStore-AutoRefresh.ipa)**

Do not use iLoader's built-in stock SideStore or stock LiveContainer + SideStore installer if you want this project's modified build. Use **Import IPA** and choose the IPA downloaded from this repository.

Alternative product lines:

- **Standalone SideStore:** [v1.0.4](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.4)
- **Previous combined interface:** [v2.1.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.1)
- **Recommended unified build:** [v3.0.2](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.0.2)

### Step 2: Install with iLoader

1. Install/open [iLoader](https://github.com/nab138/iloader/releases/latest).
2. On Windows, make sure Apple's device drivers are installed.
3. Connect the iPhone by USB, unlock it, and tap **Trust** if prompted.
4. In iLoader, select the connected device.
5. Sign in with the Apple Account you want to use for signing.
6. Open **Installers -> Import IPA**.
7. Select `LiveContainer-SideStore-AutoRefresh.ipa`.
8. Keep the required app extensions, including **LiveProcess**. Do not strip all extensions.
9. Let iLoader sign and install the IPA. Keep the device connected and unlocked until installation finishes.

### Step 3: Trust the app and enable Developer Mode

If iOS shows **Untrusted Developer**:

`Settings -> General -> VPN & Device Management -> Developer App -> Trust`

Then enable:

`Settings -> Privacy & Security -> Developer Mode`

The device may restart. Confirm **Turn On** after reboot if requested.

### Step 4: Place the pairing file

Keep the device connected by USB and unlocked.

In iLoader:

1. Open **Management**.
2. Open **Manage Pairing File**.
3. If the app is not listed, use **Rescan Installed Apps**.
4. Find the SideStore-compatible app you just installed.
5. Click **Place** for that app, or **Place In All Apps** only if that is intentionally what you want.
6. Wait for the success message before continuing.

> [!CAUTION]
> Pairing files contain private device credentials. Never upload them to GitHub, Discord, an issue, a public file host, or a screenshot.

A transport, RSD, or service error does **not** automatically mean the pairing file is bad. Replace pairing data only when diagnostics actually identify pairing as the failing stage.

### Step 5: Install the correct LocalDevVPN

Install the official **[LocalDevVPN](https://apps.apple.com/us/app/localdevvpn/id6755608044)** from the App Store and verify the developer is **Coxson Engineering LLC**.

The supported CoreDevice route does not require a second IKEv2/IPsec VPN.

### Step 6: Find the iPhone's Wi-Fi subnet

On the iPhone, open:

`Settings -> Wi-Fi -> ⓘ next to the connected network`

Write down:

- **IP Address**
- **Subnet Mask**

Example:

```text
IP Address:  192.168.1.50
Subnet Mask: 255.255.255.0
```

That example is the `192.168.1.x/24` subnet.

> [!WARNING]
> Do not copy example addresses blindly. Use addresses that belong to **your current Wi-Fi subnet**.

### Step 7: Choose two unused LocalDevVPN addresses

Choose **two different, unused IPv4 addresses inside the same subnet as the iPhone's current Wi-Fi connection**.

Example only for `192.168.1.x/24`:

```text
Tunnel IP: 192.168.1.240/32
Device IP: 192.168.1.241/32
```

Do **not** use:

- the iPhone's actual Wi-Fi IP
- the router/default-gateway IP
- the computer's IP
- an IP already assigned to another device
- an IP outside the current Wi-Fi subnet
The safest check is the router's DHCP lease or connected-client list. A failed ping alone is not proof that an address is unused.

### Step 8: Configure LocalDevVPN

Open:

`LocalDevVPN -> Settings -> Network Configuration`

Then:

1. Enter the selected **Tunnel IP** with `/32`.
2. Enter the selected **Device IP** with `/32`.
3. Enable **Allow Intermediate Addresses**.
4. Tap **Done**.
5. Tap **Save & Apply**.
6. Connect LocalDevVPN.
7. Open **Session Details** and confirm the custom values were retained.

> [!IMPORTANT]
> If you move to a Wi-Fi network with a different subnet, update the Tunnel IP and Device IP for that network before refreshing again.

### Step 9: Open v3 and complete Account & Signing

Open the installed app. Normal v3 use stays in **Home / Apps / Sources / Refresh / Settings**.

Go to:

`Settings -> Account and Signing -> Sign In / Authenticate`

Complete the account flow while Wi-Fi and LocalDevVPN are connected.

### Step 10: Run one manual refresh before enabling automation

Open **Refresh** and run a manual refresh first.

Before moving on, verify that:

- LocalDevVPN is connected
- transport is reported as reachable/ready
- refresh reaches the signing/install pipeline
- the final result is recorded as successful in refresh history/verification

A task starting or a handoff occurring is not enough. Confirm the final refresh result.

If it fails, read the reported stage before changing anything. Diagnostics are intended to distinguish VPN, CoreDevice, heartbeat, RSD, Lockdown, signing, installation, and pairing failures.

### Step 11: Enable automatic refresh

Only after a manual refresh succeeds:

1. Open the **Refresh** tab/settings.
2. Enable automatic refresh.
3. Choose **Every Six Hours**, **Daily**, or **Weekly** where available.
4. Set the preferred time/day.
5. Prefer a time when the iPhone is normally connected to the Wi-Fi network for which LocalDevVPN is configured.
6. Allow notifications if you want start, failure, and deadline alerts.

> [!NOTE]
> iOS background execution is best-effort. The selected time is not an exact alarm and iOS may run the task later. Check the last verified refresh before the signing window gets close to expiration.

### Step 12: Confirm PC-free runtime

After the initial installation and pairing setup, the supported refresh runtime is designed to use:

```text
iPhone/iPad + Wi-Fi + official LocalDevVPN
```

The computer and USB cable should not need to remain connected for normal refresh operation.

### Updating without losing your setup

For v3/v2 combined builds, install the new matching IPA **over the existing LiveContainer installation** using the same Apple Account / Personal Team and matching identifiers.

Do **not** delete the app first as a routine update step. Back up important guest data before updating.

For standalone SideStore, install the new standalone build over the existing matching SideStore installation. Do not install a standalone SideStore IPA over LiveContainer.

### If something does not work

Check these first, in order:

1. Exact release/build and IPA filename.
2. Developer Mode and trust status.
3. Pairing data belongs to the exact device.
4. LocalDevVPN is the official App Store build and is connected.
5. Tunnel IP and Device IP are **unused addresses in the same Wi-Fi subnet**, not the iPhone, router, or computer IPs.
6. **Allow Intermediate Addresses** is enabled and **Save & Apply** was used.
7. Account/signing authentication is complete.
8. Read the exact diagnostic failure stage before resetting pairing/account data.
9. If Wi-Fi changed, reconfigure LocalDevVPN for the new subnet.
10. If automatic refresh did not run, test manual refresh first; iOS does not guarantee exact background execution timing.

For deeper troubleshooting, see [Troubleshooting](#troubleshooting) and [Verification](docs/VERIFICATION.md).

## What is this?

**v3 brings Home, Apps, Sources, Refresh, and Settings into one app experience.** You no longer need to switch between LiveContainer and the old embedded SideStore interface for normal operations. LocalDevVPN remains a separate app used by the refresh transport.


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

The screenshots below are historical v2 and standalone previews, not screenshots of the new unified v3 interface. Their version labels are retained to avoid confusion.

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

These screenshots show the earlier standalone SideStore v1.0.2 interface. The current standalone download is v1.0.4, based on SideStore 0.7.0 nightly.

<table>
<tr>
<td align="center"><img src="docs/screenshots/settings-refreshing-apps.png" width="230" alt="SideStore Refreshing Apps settings"><br><strong>Refreshing Apps</strong></td>
<td align="center"><img src="docs/screenshots/refresh-schedule-main.png" width="230" alt="SideStore Refresh Schedule"><br><strong>Refresh Schedule</strong></td>
<td align="center"><img src="docs/screenshots/refresh-history.png" width="230" alt="SideStore refresh history"><br><strong>Refresh History</strong></td>
</tr>
</table>

## Quick navigation

- [Why this project exists](#why-this-project-exists)
- [Choose a build](#which-version-should-i-download)
- [What is different from the original projects?](#how-this-project-differs-from-the-original-projects)
- [Installation](#installation)
- [Previous product lines](#previous-product-lines)
- [Troubleshooting](#troubleshooting)
- [Technical architecture](#technical-architecture)

## Which version should I download?

| What you want | Use | Download |
| --- | --- | --- |
| One unified LiveContainer + SideStore interface | **Unified v3.0.2 (recommended)** | **[Download v3 IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v3.0.2/LiveContainer-SideStore-AutoRefresh.ipa)** |
| The previous combined interface | **Combined v2.1.1 (previous release)** | [Previous v2 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.1) |
| SideStore only, with normal separately installed sideloaded apps | **Standalone v1.0.4** | **[Download standalone IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.4/SideStore.ipa)** |

For LiveContainer + SideStore, choose **v3.0.2**. SideStore is already included, so a separate SideStore installation is not needed for this setup. The v2 download is retained for users who need the previous interface; it does not contain the v3 fixes.

**Recommended unified build:** [v3.0.2 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.0.2)

**Previous unified build:** [v3.0.1 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.0.1) (previous v3 line, still available)

**Previous combined line:** [v2.1.1 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.1)

**Standalone:** [v1.0.4 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.4)

## What's new in v3.0.2?

v3.0.2 is the current maintenance release for the unified LiveContainer + SideStore v3 line.

- **Setup Assistant:** host-owned onboarding checklist covering device, pairing, Apple account, network, Background App Refresh, schedule, and a verified test refresh. Setup Complete requires a signed-in team, acceptable network and tunnel, available Background App Refresh, enabled schedule, and a refresh verified in the current session.
- **Start Dock Collapsed:** new persistent Multitasking preference. The dock can begin collapsed; manual expand/collapse always wins afterwards and layout or rotation never reset it. Independent from Hide Collapsed Dock and Guest Controls.
- **Refresh target crash fix:** the targeted-app refresh section no longer traps when rendered without an inherited store.
- **First-launch notification prompt:** plain-language permission request so refresh start/completion/failure banners can arrive.
- **Visible coalescing:** manual refresh taps that land on an already-running refresh now report instead of staying silent.

## What's new in v3.0.1?

v3.0.1 was the previous maintenance release for the unified LiveContainer + SideStore v3 line. It keeps the working v3 architecture and focuses on making SideStore a backend implementation detail while LiveContainer owns the visible experience.

- **Headless SideStore backend:** normal v3 flows no longer depend on remote SideStore UI presentation. LiveContainer owns the user-facing navigation, forms, sheets, alerts, confirmations, progress, and error presentation.
- **Guided sign-in and 2FA:** account authentication is driven from the unified UI with a clearer two-step 2FA flow, including trusted-device and phone delivery choices followed by code entry.
- **Host-owned management screens:** certificates, developer data, pairing, sources, settings, diagnostics, and related flows use the v3 service/DTO command model instead of exposing SideStore screens directly.
- **Home is actionable:** key status rows now navigate directly to the relevant Apps, Sign In, Developer Services, Certificates, Pairing, or Refresh destination.
- **Settings cleanup:** sections are ordered by relevance, Guest Runtime is last, and Build Candidate information is moved to the bottom.
- **Fewer dead ends:** account-dependent destinations route to Sign In when no account is available.
- **2FA phone-selection fix:** selecting a phone number requests delivery to that number instead of cancelling the flow.
- **Release/runtime stability:** the release retains the current refresh, transport, signing, persistence, guest-runtime, and LiveProcess behavior rather than introducing unrelated refactors.

The published v3.0.1 IPA was built from the same tested v3 behavior, with the release/version and evidence updates applied afterward. Existing v3 users should install it over the matching LiveContainer installation with the same signing identity rather than deleting the app first.

SideStore continues to own its database, Keychain, authentication logic, signing, provisioning, sources, and installation. LiveContainer continues to own guests and guest execution. The v3 service bridge connects those owners without duplicating persistent state.

Wi-Fi and the official LocalDevVPN app are still required for the supported refresh path. iOS controls background execution timing; a schedule is not an exact alarm. Issue #18 remains tracked separately because one affected user's Apple authentication result cannot be proven universally by CI or one device.

## What's new in v1.0.4 and v2.1.1?

Issue 17 adds saved app layout choices under **Interface**:

- **List** keeps the detailed card layout.
- **Grid** shows an icon-first app grid. You can hide visual labels while keeping each app name available to VoiceOver.
- **Compact List** uses shorter rows for denser browsing.

The selected layout and label preference persist across launches. Changing either setting refreshes the Apps screen immediately. Grid actions continue to use the existing launch, multitasking, context-menu, confirmation, and error-handling paths.
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

SideStore and LiveContainer are the upstream foundations; this repository turns them into a different end-to-end experience for this use case. The main advantages are the **CoreDevice/RSD refresh route**, the **unified v3 LiveContainer + SideStore workflow**, explicit scheduling and history, run-level verification, structured diagnostics, and the additional app/guest UI controls described above.

For the exact source-level differences, expand **Technical comparison with upstream** near the top of this README.

### What this project does not change

- It does **not** remove the normal Personal Team signing expiration. It refreshes before expiration.
- It does **not** guarantee an exact background execution time. iOS controls task scheduling.
- It does **not** make cellular-only refresh a stable feature yet.
- It does **not** require a jailbreak for the supported stable path.
- It does **not** make LiveContainer guests equivalent to separately installed iOS apps.
- It does **not** guarantee that iOS will keep every guest process alive.

Because the combined build modifies LiveContainer and contains embedded SideStore code, use the same trust model you would use for any modified LiveContainer build. This repository is open source, and you can [build it yourself](#build-it-yourself) if you prefer to verify the build path personally.

## Previous product lines

The main [Installation](#installation) guide above is written for the recommended **v3.0.2** build.

### Combined v2.1.1

Use the [v2.1.1 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.1) only if you specifically want the previous combined interface. Open LiveContainer, enter the embedded SideStore flow, run one manual refresh first, and enable automation only after that manual refresh succeeds.

### Standalone SideStore v1.0.4

Use the [v1.0.4 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.4) if you want SideStore without LiveContainer. Install it over the matching existing standalone SideStore installation, keep LocalDevVPN configured for the current Wi-Fi subnet, run one manual refresh, then enable its refresh schedule.

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

### Unified v3 and combined v2

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
| Confirmed missing/invalid pairing | Reconnect USB, open **Manage Pairing File**, use **Rescan Installed Apps**, then **Place** again for the exact device |
| v3 service-startup or transport error | Read the stage and error code in diagnostics. Do not reset account or pairing data merely because a service or network connection failed |
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

### Combined features retained from v2

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

For the exact v3 implementation and ownership boundaries, read the [architecture at the v3.0.2 tag](https://github.com/NRG-Wardog/sidestore-auto-refresh/blob/v3.0.2/docs/V3_UNIFIED_ARCHITECTURE.md). The shared refresh transport below remains separate from the unified UI and service lifecycle.

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
- Preserve the combined-build keys `LCAppLayoutStyle` and `LCShowAppLabels` in the existing shared preferences. Standalone uses its own existing preference names. Do not add a new database for UI preferences.
- Extend the existing **Interface -> App Layout** setting instead of creating a second layout control. Show **Show app labels** only when Grid is selected.
- Keep the current list/card renderer as the List path. Do not rewrite working list behavior simply to share code.
- Implement Grid as an icon-first adaptive grid, with an optional app name below each icon.
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
| Unified v3.0.2 build and packaging | CI 35518324344 passed 190 repository tests, 512 layout measurements with 0 failures, host + embedded source builds, transport checks, and IPA packaging verification. Matching evidence is attached to the release; this is not exhaustive physical-device validation |
| Unified v3.0.1 build and packaging | CI 35419528113 passed 155 repository tests, 512 layout measurements with 0 failures, host + embedded source builds, transport checks, and IPA packaging verification. Matching evidence is attached to the release; this is not exhaustive physical-device validation |
| Standalone v1.0.4 build and packaging | CI passed; published IPA checksum, arm64 executable, background-task configuration, and layout patch parsing verified |
| Standalone v1.0.3 device refresh | Device testing of this exact build is pending |
| Earlier standalone manual CoreDevice refresh | Verified on iPhone 12 / iOS 26.6.1; this predates v1.0.3 |
| Earlier standalone scheduled refresh with PC disconnected | Recorded proof predates v1.0.3 and is available in the verification report |
| Combined v2.1.1 build and packaging | Combined CI run completed successfully, including the LiveContainer grid implementation |
| v2.1.0 Guest Controls | Start Collapsed and custom colors are included in the published combined build |
| Background scheduling | Best effort. iOS controls task launch timing |
| Cellular-only refresh | Experimental, not supported in the stable release |
| Guest process retention | Best effort. iOS may suspend or terminate a guest |

A build completing, a background task starting, or a host handoff occurring is not automatically treated as proof that the signing lifetime was refreshed. See [docs/VERIFICATION.md](docs/VERIFICATION.md) for the exact proof model.

### v3.0.2 provenance

- Builder/tag commit: `35c6c28c98e7261afe6049a133a9ae542d666d57`
- Combined CI run: [35518324344](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/35518324344)
- IPA: `LiveContainer-SideStore-AutoRefresh.ipa` (38,736,104 bytes)
- SHA-256: `5AAB622EB91CA9007BD4A29E5AD8FEB90791AC8EBB1AB7393022A1C7474CA02E`
- LiveContainer: `12377cf3b91d51739a33f14a302e5f522b238593`
- Embedded SideStore: `ff25922e5c13ccfafd83bda5092910d848ebd409`
- SideSign: `a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d` (GSA fix `35993d7` is ancestor-verified in CI)
- minimuxer: `98c3c79982f813878e922ab42f9545314a700f0c`
- Release: [Unified LiveContainer + SideStore v3.0.2](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.0.2)

v3.0.2 is the current recommended unified release. The previously published v3.0.1 provenance is retained below for reproducibility and historical verification.

### v3.0.1 provenance

- Builder/tag commit: `6f0a144936c789bd7557d93ec1752888fddbb256`
- Combined CI run: [35419528113](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/35419528113)
- IPA: `LiveContainer-SideStore-AutoRefresh.ipa` (38,644,788 bytes)
- SHA-256: `A8A783D4FB6D229FAA449024872F1C56755351F9593349C6BC10B9CD76FE16311`
- LiveContainer: `12377cf3b91d51739a33f14a302e5f522b238593`
- Embedded SideStore: `ff25922e5c13ccfafd83bda5092910d848ebd409`
- SideSign: `a731c0d5a9a661c7b385ae493e07ffb7f81cd5d` (GSA fix `35993d7` is ancestor-verified in CI)
- minimuxer: `98c3c79982f813878e922ab42f9545314a700f0c`
- Release: [Unified LiveContainer + SideStore v3.0.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.0.1)

v3.0.1 is retained below for reproducibility and historical verification. The previously published v3.0.0 provenance follows it.

### v3.0.0 provenance

- Builder/tag commit: `6bbddd55531ac2497707fce184e5370f76d7991f`
- Combined CI run: [34763923268](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34763923268)
- IPA: `LiveContainer-SideStore-AutoRefresh.ipa`
- SHA-256: `699f20bd839136b652e30c6c4de1d9237aa79b91437202daf4f8e59684358268`
- SideStoreSupport and matching dSYM UUID: `C59B7B1F-1CC8-3667-AE90-2514C2F11869`
- Release: [Unified LiveContainer + SideStore v3.0.0](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.0.0)

The release promotes the verified preview.2 IPA without rebuilding or re-signing it. Its internal upstream version can still display `3.8.9`; identify the package by the release tag and **Settings > Build Candidate** revision. v2 and standalone attachments retain separate provenance.

### v2.1.1 provenance

- Builder/tag commit: `3d4a7c49f9c202159397c25106149a3bad2b2902`
- Combined CI run: [34689847917](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34689847917)
- IPA: `LiveContainer-SideStore-AutoRefresh.ipa`
- SHA-256: `94a95f1b51066db0552c65ac5e4f3cf67efc3baa293adc2eca077228adabb583`
- Release: [LiveContainer + SideStore Auto-Refresh v2.1.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.1)

### v1.0.4 provenance

- Builder/tag commit: `3d4a7c49f9c202159397c25106149a3bad2b2902`
- Standalone CI run: [34689425130](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34689425130)
- IPA: `SideStore.ipa`
- SHA-256: `f9641f559e922ea4de5099d9857438a7970e216f40536cf7fece8f21a1ee3692`
- Release: [SideStore CoreDevice Auto-Refresh v1.0.4](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.4)

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
3. For the current published v3 package, use the **`v3.0.1` tag** (`6f0a144936c789bd7557d93ec1752888fddbb256`), not older combined sources on `main`. Ensure this tag or a branch at that commit exists in your fork, then dispatch `livecontainer-build.yml` at that ref. The publication updates documentation on `main`; it does not merge the v3 source branch. Use the matching release ref when reproducing a previous or standalone build.
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

When reporting a compatibility result, include the exact release tag and builder revision, device model, iOS version, product line (v1, v2, or v3), download source, Wi-Fi network family, LocalDevVPN status, and a non-sensitive refresh result. Do not attach secrets or complete private logs. Use [Issue #1](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/1) for compatibility reports.