## Corrected direct refresh

This patch release promotes the exact tested LiveContainer + SideStore candidate, without rebuilding, relabeling or modifying its IPA bytes.

- Direct refresh now checks execution/admission ownership, rejects stale-run results and respects active-operation/recovery interlocks.
- Retains integration-boundary coverage (#52), borrowed-provider lifetime corrections (#53) and preservation-first database recovery (#56).
- Retains the narrowly scoped fallback after a verified initial On-Device provider rejection. This does not establish that every Apple sign-in or provider problem is fixed.

The owner reported that the delivered candidate works. Exact native build, packaging and integration-contract CI passed. The independent audit verified 189 evidence references and preservation of all 274 IPA members, signature bytes and the nested backup. Twelve same-session language transitions and owned simulator cleanup passed. These checks and the owner's report are not broad all-device, all-account or long-duration/scheduled-refresh acceptance.

## Download and upgrade

Download `LiveContainer-SideStore-v6.0.1.ipa` and update the existing installation with the same signing identity. Do not delete the app or reset its account, pairing or data as an upgrade step. Compatible signing, pairing, Developer Mode, network/LocalDevVPN and Apple provisioning prerequisites still apply; iOS controls background scheduling.

**Public release tag:** v6.0.1. **Unchanged binary identity:** CFBundleShortVersionString / CFBundleVersion remain `3.8.11`; LCBuilderCommit remains `02eef20523795f6ef912063a8fac712a7f28b401`, and LCVersionInfo remains `Release (HEAD/5d2498d)`. The release label is not a claim that the embedded binary was rebuilt with a new version. MinimumOSVersion remains 15.0.

**Size:** 24,409,600 bytes (24.4096 MB), 409,600 bytes above the 24,000,000-byte target. Four bounded lossless compression rounds were used; no extra round or general size-limit relaxation. Signature-byte preservation does not assert validity of every pre-existing signature after installation or re-signing.

- [Native CI](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/41950003202), builder `02eef20523795f6ef912063a8fac712a7f28b401`.
- [Package CI](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/41960917910) and [integration-contract CI](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/41960917902), package commit `fefb69346a4d3bb79ebbb4294b3b99bf81fcd045`.
- SHA-256: `eaca3f28b32b4a14acc0b1bf16237348f528de18a6eed37552b80d65fb9f84d3`.

The prior v6.0.0 release and assets remain unchanged. Detailed immutable lineage is included in `release-provenance.json` and the download checksum in `SHA256SUMS`.
