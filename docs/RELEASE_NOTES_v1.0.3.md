# SideStore CoreDevice Auto-Refresh v1.0.3

Standalone v1.0.3 updates the app to **SideStore 0.7.0 nightly** while preserving this project's LocalDevVPN → Lockdown/CoreDevice → RSD refresh path. Combined LiveContainer remains on its separate v2.1.0 release.

[Download the IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.3/SideStore-CoreDevice-AutoRefresh-v1.0.3.ipa) | [Release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.3) | [SHA256SUMS](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.3/SHA256SUMS.txt)

## Changes

- Retains SideStore 0.7's authentication implementation, including its newer SideSign authentication path.
- Adapts the new pairing parser so composite records prefer Lockdown/CoreDevice over RemotePairing.
- Allows the CoreDevice path to use LocalDevVPN's tunnel without an additional IKEv2/IPsec interface.
- Adapts the new signing block while retaining provisioning-profile checks and self-refresh markers.
- Uses the new asynchronous database-startup API for scheduled refresh; the older combined build retains its callback API.
- Prevents a database save error from producing a successful self-refresh reconciliation marker.
- Preserves manual and scheduled refresh, history, retry/recovery behavior, cancellation, and verification diagnostics.
- Corrects repository security checks so legitimate source filenames are accepted while sensitive artifact paths remain rejected across public branches, tags, and reachable history.

## Pinned sources

| Component | Commit |
| --- | --- |
| SideStore | `c6f28864dc99ed03ad35f2ca58967247036b7b53` |
| minimuxer | `9f038e5223a8cacf9d15beae7da6969d617ae1e8` |
| SideSign | `8385fab56e7bd5aea8110e1b85ab069a71e45ce0` |
| idevice | `ebd7dadfc55d1c4facee3d11ecf5b28e20548b57` |
| jktcp | `e674e1eee6d5943e13b1eba0bd24a9dd0b2fa020` |
| Builder / release tag | `07d52be7a49f6795b82f081ded2ec94eb44d50df` |

The app reports version `0.7.0`, build `0700`; `v1.0.3` identifies this project's standalone release.

## Validation

[Release CI run 34613911507](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34613911507) completed successfully:

- 83 repository tests passed in macOS CI. Two additional LiveContainer tests were skipped because their fixture lookup used a fixed local path; both subsequently passed locally against the pinned fixtures.
- 14 Rust transport tests passed. The existing `local_tcp` and `handle_speed` tests remained filtered out.
- The full standalone patch chain applied twice without further changes. Executable Swift checks covered pairing selection, both database-startup APIs, startup failures, task expiry, and self-refresh reconciliation.
- SideStore authentication sources and the pinned SideSign dependency were verified unchanged by the patches.
- The arm64 iOS archive, IPA packaging, and final feature-marker checks passed.
- The published download independently passed SHA-256, ZIP integrity, app metadata, background-task configuration, and all 13 required feature-marker checks.

IPA: `SideStore-CoreDevice-AutoRefresh-v1.0.3.ipa`

SHA-256: `ab35772fe3209618c7bec302e315faea0a35b7ae280edfb9b5390d2aa62c8940`

Device testing of this exact build is pending. Earlier standalone device evidence in [VERIFICATION.md](VERIFICATION.md) predates v1.0.3.

## Updating

Install the standalone IPA over the existing matching SideStore installation using the same Apple Account / Personal Team and identifiers. Keep the existing installation to preserve its data and configuration.

Follow the [standalone setup instructions](../README.md#standalone-setup-v1). The supported setup uses Wi-Fi and the official LocalDevVPN. Cellular-only refresh remains experimental, and iOS controls when scheduled background tasks run.
