# LiveContainer + SideStore Auto-Refresh

<!-- README language selector -->
[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [Русский](README.ru.md) · [עברית](README.he.md)
<!-- /README language selector -->


An independent LiveContainer + SideStore build with a unified interface for guest apps, signing, installation, and on-device refresh.

Normal use stays in **Home / Apps / Sources / Refresh / Settings**. A computer is needed for initial installation and pairing. The supported refresh path uses **Wi-Fi + official LocalDevVPN**; cellular-only refresh is not a supported promise.

This is not an official [LiveContainer](https://github.com/LiveContainer/LiveContainer) or [SideStore](https://github.com/SideStore/SideStore) release.

<a id="verification-status"></a>
## Download

**[Download v6.0.1 IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v6.0.1/LiveContainer-SideStore-v6.0.1.ipa)** · [Release notes and limitations](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.1)

File: `LiveContainer-SideStore-v6.0.1.ipa`

SHA-256: `eaca3f28b32b4a14acc0b1bf16237348f528de18a6eed37552b80d65fb9f84d3`

GitHub source archives are not installable IPAs. Verify the filename and checksum, not just the displayed app version. Read the release notes for exact-build verification and known limitations; a successful build is not proof that every device flow works.

<a id="installation"></a>
## Install

You need a supported iPhone or iPad, a computer, an Apple Account for signing, device-specific pairing data, and [iLoader 2.3.1 or newer](https://github.com/nab138/iloader/releases/latest) or a compatible IPA installer.

1. Download the IPA above. Connect the device by USB, unlock it, and trust the computer. On Windows, install Apple's device drivers if needed.
2. In iLoader, select the device, sign in with the signing Apple Account, then choose **Installers → Import IPA**. Select this project's IPA, not iLoader's stock SideStore or LiveContainer package.
3. Keep the required extensions, including **LiveProcess**, and finish installation with the device connected and unlocked.
4. If prompted, trust the developer under iOS **Settings → General → VPN & Device Management**. Enable **Settings → Privacy & Security → Developer Mode** and confirm after restarting.
5. In iLoader, open **Management → Manage Pairing File**, rescan installed apps if needed, and use **Place** for the installed app. The pairing data must belong to this exact device.

**Pairing files contain private device credentials. Never attach them to an issue, public log, or screenshot.**

### Configure LocalDevVPN

Install the official **[LocalDevVPN](https://apps.apple.com/us/app/localdevvpn/id6755608044)** by **Coxson Engineering LLC**. This route does not require an additional IKEv2/IPsec VPN.

1. In iOS **Settings → Wi-Fi → ⓘ**, check the current **IP Address** and **Subnet Mask**.
2. Choose **two different unused IPv4 addresses in the same subnet as the current Wi-Fi connection**. Do not use the phone, router, computer, or another device's address. Check the router's lease/client list; a failed ping alone does not prove an address is unused.
3. In **LocalDevVPN → Settings → Network Configuration**, enter the selected **Tunnel IP** and **Device IP**, each with **`/32`**.
4. Enable **Allow Intermediate Addresses**, tap **Done**, then **Save & Apply**.
5. Connect LocalDevVPN and verify the saved addresses in **Session Details**.

For example, only on a `192.168.1.x` network with mask `255.255.255.0`, unused `192.168.1.240/32` and `192.168.1.241/32` could be used. Do not copy these addresses without checking your network.

**When the Wi-Fi subnet changes, update both addresses before refreshing.**

### Sign in and refresh

Authentication uses the external Mac anisette provider `mac-ani.jkcoxson.com` and retains its identity in Keychain. This is a third-party network/privacy trust boundary, not entirely local authentication. Review it before signing in; do not publish provider identity material.

1. With Wi-Fi and LocalDevVPN connected, open **Settings → Account and Signing → Sign In / Authenticate** in the installed app.
2. Open **Refresh** and run a manual refresh.
3. Check the final result in refresh history/verification. Starting a task or handing it off is not a successful refresh.
4. Only after a manual refresh succeeds, enable automatic refresh in **Refresh** and choose an available six-hour, daily, or weekly schedule.
5. Enable iOS Background App Refresh and allow notifications if you want refresh and expiration alerts.

After initial setup, the computer should not need to remain connected for the supported refresh path. **Background execution is best-effort:** iOS controls when tasks run. Check the last verified refresh before signing expires; a preferred time is not an exact alarm.

<a id="updating-without-losing-your-setup"></a>
## Update safely

- Back up important guest data first and read the [release upgrade notes](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.1).
- Install over the matching existing **LiveContainer** app using the **same signing identity (Apple Account / Personal Team) and matching identifiers**.
- Keep required extensions, including **LiveProcess**. Do not delete the app first as a routine update step.
- A standalone SideStore IPA is not a replacement for the combined LiveContainer IPA.
- Do not reset account, app, or pairing data as speculative recovery.

<a id="troubleshooting"></a>
## Troubleshooting

Check these before changing data:

- Exact IPA/build, developer trust, Developer Mode, and required extensions.
- Official LocalDevVPN connected, two unused same-subnet `/32` addresses, **Allow Intermediate Addresses**, and **Save & Apply**.
- Pairing belongs to the device and **Account and Signing** authentication is complete.
- The reported failing stage: network/VPN, CoreDevice/RSD, pairing, authentication, signing, or installation.
- If automation did not run, try a manual refresh and check iOS Background App Refresh.

Report the build, device/iOS version, failing action, and a minimal redacted error in [Issues](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues). Never include pairing records, credentials, private keys, or unredacted device logs.

<a id="build-it-yourself"></a>
## Development

This repository maintains build scripts, patches, and tests for the combined app and its dependencies.

- [Documentation](docs/README.md)
- [Build and test guide](ci/README.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Follow [Contributing](CONTRIBUTING.md) for focused checks and their prerequisites, then run `git diff --check`. Full validation requires external source checkouts and native toolchains; native iOS builds use the supported macOS/Xcode workflow in the build guide.

## License

Repository-authored work is covered by [MIT](LICENSE) unless stated otherwise. Upstream components retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
