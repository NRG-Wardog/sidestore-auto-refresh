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

## Changes since the v3 release line

- Repaired the Apple authentication flow using the iLoader-compatible remote Mac anisette route, with stable protected identity reuse across restarts and ordinary sign-out. Authentication and incomplete device setup are represented separately.
- Repaired placed-pairing adoption, setup-state presentation and certificate import handling without replacing device identity or bypassing certificate ownership checks.
- Repaired self-refresh bundle preparation and the cache path normalization defect that blocked imported IPA installation. The working installation and extension-selection flow is retained.
- Added bounded automatic LocalDevVPN activation for eligible foreground manual-refresh readiness failures, including interfaces that exist without a usable address. The app invokes the supported enable link, waits for return and retries once. Pairing, unsupported transport and signing failures do not trigger a VPN retry loop.
- Preserved correlated operation errors through refresh manifests and host diagnostics, with finite non-secret failure context.
- Retained unified Home / Apps / Sources / Refresh / Settings, guest controls and source installation paths.

## DEVICE VERIFIED — 6.0

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
