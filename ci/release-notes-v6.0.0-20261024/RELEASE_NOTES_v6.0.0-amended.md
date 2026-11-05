# Unified LiveContainer + SideStore 6.0

6.0 replaces the v3 combined product line. It promotes the exact IPA tested by the maintainer, without rebuilding, modifying or re-signing it.

## Download

Download `LiveContainer-SideStore-v6.0.0.ipa`. `SHA256SUMS` and `release-provenance.json` are optional verification files, not installation files. GitHub source archives are for developers.

- Builder: `177ac31e90499231191b25f761014c17d9f2ba7b`
- Package commit: `0403c859f943ffe4f17a048ac1309dd009f1f17b`
- IPA size: **30,416,579 bytes**
- IPA SHA-256: `86db9a6bf4accf7e886d88563f1474063c85865682131a5ba0b13ed55a33f321`
- [Successful native build and regression checks](https://github.com/NRG-Wardog/LiveContainer/actions/runs/40102842840)
- [Successful exact-input packaging](https://github.com/NRG-Wardog/LiveContainer/actions/runs/40104361491)

The public version is **6.0** (tag `v6.0.0`). To preserve the tested bytes, the retained CI build-label metadata still says `6.0 pairing and deferred setup experimental` with an experimental channel. This is not a rewritten app-bundle version. Upstream bundle versions remain LiveContainer `3.8.11` and SideStore `0.7.0` (`0700`). Use the builder revision and checksum to identify this release; these retained labels do not identify a different IPA.

## Download size compared with official combined LiveContainer + SideStore

These are compressed **combined LiveContainer + SideStore IPA downloads**, not standalone LiveContainer, TrollStore builds, source archives or installed sizes. Official asset metadata was checked on 2026-10-25.

| Combined release | Compressed bytes | MB (decimal) | MiB (binary) |
| --- | ---: | ---: | ---: |
| [Official stable 3.8.0](https://github.com/LiveContainer/LiveContainer/releases/tag/3.8.0), `LiveContainer+SideStore.ipa` | 35,403,538 | 35.403538 | 33.763445 |
| This v6.0.0, `LiveContainer-SideStore-v6.0.0.ipa` | 30,416,579 | 30.416579 | 29.007510 |
| [Official nightly published 2026-10-17](https://github.com/LiveContainer/LiveContainer/releases/tag/nightly), `LiveContainer+SideStore.ipa` | 24,155,286 | 24.155286 | 23.036276 |

Compared with **official stable 3.8.0**, this release is **4,986,959 bytes smaller, a 14.09% reduction** (approximately 4.99 MB / 4.76 MiB).

For completeness, the official nightly available at the time of comparison is smaller than both: this release is **6,261,293 bytes larger, or 25.92% larger**, than that nightly. The nightly tag is mutable; the checked asset is ID `649977564`, SHA-256 `33831bb279d74f2da1be08eb3cc6d3646992a6120da32ffb763269696052b868`. The stable asset is ID `480113517`, SHA-256 `97dc0fd2202fd4460efcab389943b8d5fdbb4988efff76b116b92b84a4662425`.

These are different release snapshots. An exact matching official LiveContainer 3.8.11 / SideStore 0.7.0 baseline has not been established, so this is not a controlled same-source optimization benchmark or a claim that this IPA is smaller than every official build. No RAM, installed-size or performance claim follows from these download sizes. One MB is 1,000,000 bytes; one MiB is 1,048,576 bytes. Reduction uses `(official stable bytes - our bytes) / official stable bytes`; the nightly increase uses the nightly size as its denominator.

Historical comparison with our previous combined v3.1.0: 25,122,692 bytes to 30,416,579 bytes, an increase of 5,293,887 bytes (21.07%). That is separate from the official-build comparison above.

## New changes and fixes since v3.1.0

These are changes in the tested source lineage promoted by 6.0, not additional code changes made while publishing the release. Source/fixture verification and physical-device acceptance are listed separately below.

### Apple sign-in and anisette identity

- Repaired the Apple authentication path using the iLoader-compatible **remote Mac anisette** route. The remote provider is `mac-ani.jkcoxson.com`; its privacy boundary is disclosed below.
- Added protected persistence and reuse of the remote installation/provider identity and provisioning state across restarts and ordinary sign-out, rather than generating an unrelated identity for each attempt. Local and remote anisette identities stay separate.
- Carried the selected authentication context through onboarding, team lookup, profile operations and queued/coalesced work. Cancellation, stale attempts and identity changes cannot silently become a successful new attempt; a remote-provider failure is not silently retried through the local provider.

### Pairing, setup and certificate handling

- Separated **successful account authentication** from **incomplete device setup**. Eligible sign-in can finish with pairing/provisioning still required, while the setup UI shows what remains. Invalid or unreadable pairing, network failures, certificate errors and cancelled work are not relabelled as successful setup.
- Repaired adoption of a pairing file placed by the installer and the recovery bookkeeping around that action. Saved pairing state is reconciled with the acknowledged operation instead of retaining a stale recovery owner; uncertain recovery is not silently cleared.
- Corrected certificate/private-key validation for fresh PEM material and reloaded PKCS#1/PKCS#8 forms. Certificate and private key must still match, and the selected identity/team must own the result. This does not bypass ownership checks, repair a genuinely revoked certificate or establish every Keep/Reuse scenario.
- Improved Setup Assistant status and guidance for account, pairing, installed-host signing, JIT-less, device connection and refresh. A detected tunnel is not labelled as a verified connection; stale success, an active handoff or an uncertain result cannot create a false green status. Setup/diagnostic copy has English, Hebrew, Simplified Chinese and Traditional Chinese coverage.

### IPA installation, extensions and self-refresh

- Fixed the cache-path normalization defect that rejected an otherwise trusted app-storage root and blocked imported IPA installation. Canonicalizing the trusted root retains destination-containment and no-overwrite checks. The existing filename/size cache-identity compatibility and its integrity limitation are disclosed below.
- Repaired the extension-selection path for the reported fresh IPA installation workflow. Selection is carried into the existing install pipeline; cancellation does not become an implicit destructive selection. Source-owned regression cases cover fresh install, reinstall/update and selection variants, while the separate device result covers the specific tested installation and working app ([#48](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/48)).
- Repaired **self-refresh bundle preparation** when the refresh cache has no usable target bundle. The canonical installed host is prepared in an owned temporary copy, with host/extension identity and profile checks. Cleanup does not modify the original installed source or delete another operation's files; this recovery is not applied indiscriminately to other apps.

### LocalDevVPN, refresh results and diagnostics

- Added bounded **automatic LocalDevVPN activation** for eligible foreground manual-refresh readiness failures, including an interface that exists but has no usable address. The app opens the supported enable link, waits for return, rechecks and permits only one pipeline retry. It does not configure the VPN, force a ready result or loop indefinitely.
- Kept automatic activation narrowly scoped: background refresh, missing/invalid pairing, unsupported transport, unrelated signing failures, mixed/partially successful batches, cancellation, changed request ownership and uncertain mutations do not gain a new automatic-retry path.
- Preserved structured failure details through early refresh boundaries, per-app results, manifests, the host/service bridge and copied diagnostics. Certificate selection, provisioning preconditions, refresh metadata, profile installation and record-update failures keep finite request/stage context rather than collapsing into an unexplained wrapper error or blaming pairing/VPN by default.
- Made setup connection status depend on the latest matching, verified refresh evidence. Starting a task, finding an interface, receiving a callback or observing a signing stage is not independently presented as successful device refresh.

### Reproducibility and release documentation

- The release records the exact native workflow/source graph, successful native run and independently bound packaging run. Public promotion preserves the tested IPA bytes; the retained CI label and actual upstream bundle versions are explicitly distinguished from public version 6.0.
- Updated the English, Hebrew, Russian, Simplified Chinese and Traditional Chinese READMEs so **6.0 replaces v3 as the current combined line**, while keeping historical release records and older product instructions. Current build references point to the exact LiveContainer native/packaging workflows, not the older SAR combined workflow.

### Retained features, not newly introduced in 6.0

Unified **Home / Apps / Sources / Refresh / Settings**, the headless SideStore backend, source add/cancel handling, the canonical JIT-less setup integration, the guest-background notification fix, app layouts, Guest Return controls and refresh scheduling already existed in the v3 lineage. They remain part of 6.0; the newly recorded device confirmations below must not be mistaken for a claim that all of those features were first implemented in this release. Sources, JIT-less, background guest behavior and cancellation are verified only within the stated device-test scope.

## DEVICE VERIFIED: 6.0

The maintainer reported successful device testing in this exact release lineage, culminating in builder `177ac31e` acceptance. These are scoped observations on the maintainer's setup, not certification of every device or account:

- Apple sign-in and manual Refresh All.
- Imported IPA installation, extension selection and successful use of the installed app (#48).
- Adding Sources and adding an app from a Source as an LC Guest (#38).
- Cancelling Add Source (#40).
- JIT-less operation (#39).
- Guest apps working in the background (#33).
- Automatic LocalDevVPN activation during the tested manual-refresh flow.
- Refresh stability and guest status after refresh reported as looking correct. Follow-up tracking is consolidated in #35; #13 remains the historical report.

This does not independently verify long-duration scheduled background refresh, Source app update parity (#30), preference persistence (#34), or every certificate-limit/Keep/Reuse scenario (#44). Those scopes remain separate. iOS controls background execution timing.

## Upgrade and requirements

Install over the existing matching LiveContainer installation with the same signing identity and identifiers. Back up important guest data. Do not delete the app, reset the database, sign out, erase pairing data or replace certificates as a routine upgrade step.

Sign the IPA with your own Apple account using a compatible installer. Keep the required extensions, including LiveProcess. Device-specific pairing, Developer Mode, appropriate Wi-Fi and the official LocalDevVPN configuration remain required for the supported same-device refresh path. Automatic activation does not configure LocalDevVPN or guarantee connectivity. Cellular-only refresh is not established by this release.

## Privacy and known limitations

The default sign-in route uses the external anisette provider `mac-ani.jkcoxson.com`. Anisette provisioning communicates with that provider; it is a separate trust boundary from Apple authentication. Remote installation/provider identity and provisioning state are retained in protected Keychain storage for reuse and are kept separate from the local anisette identity. Do not send credentials, pairing records, private keys or complete personal logs in issue reports.

The cache compatibility behavior retains filename/size-based cache identity and existing-payload reuse. A cache hit does not independently prove content integrity. Destination containment and no-overwrite safety boundaries remain in place.

An inherited CydiaSubstrate binary has a CodeDirectory hash mismatch on page 0. The binary matches the existing upstream component and was not altered by this release. The static inspection does not establish its original signature trust, and ordinary installer re-signing has not been verified as a remedy. No new corruption or related device failure was established. This limitation remains disclosed rather than silently treated as a valid original signature.

Native builds, source-owned regression fixtures and static package verification passed for the exact hashes above. No new native build is implied by this public promotion. Published assets contain the unsigned/ad-hoc packaged build, not the maintainer's personally signed installation, pairing data, private device logs or account credentials.

This is an independent project, not an official LiveContainer or SideStore release. Upstream licenses and third-party notices continue to apply.
