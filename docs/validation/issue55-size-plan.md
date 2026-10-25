# Size reduction candidate

## Controlled archive measurement

The published 6.0 IPA was downloaded from the exact v6.0.0 asset, size 30,416,579 bytes and SHA-256 `86db9a6bf4accf7e886d88563f1474063c85865682131a5ba0b13ed55a33f321`. Its 274 archive entries total 84,086,225 uncompressed bytes. This is a bundle-content proxy, not measured device allocation or user data.

A private local copy recompressed with ZIP deflate level 9 is 30,341,340 bytes, saving 75,239 download bytes. All entry names/order, decompressed bytes, CRC, timestamps, Unix modes/symlink attributes, comments and extra fields were verified identical. ZIP compression headers can differ. Installed bundle contents do not shrink. The published release is untouched. `issue55-size-baseline.json` retains the full inventory and hashes.

Largest files are the embedded SideStore binary (29,846,176 bytes), LiveContainerSwiftUI (22,193,704), widget (5,848,752), and two different OpenSSL binaries (5,113,136 and 4,423,680). Those OpenSSL copies are not byte duplicates and have independent runtime consumers. Deleting either without dynamic loading/provider qualification is unsafe. Repeated privacy manifests, localization files and intent definitions have required bundle-specific paths; duplicate bytes are only audit candidates, not deletion permission.

## Candidate build

The package pipeline adopts lossless recompression only if smaller and verifies every preserved field before replacing its temporary output. Source-owned tests cover corrupt/ambiguous paths, duplicate members, resource limits, symlinks, permissions and unchanged payloads. No binary/resource/license/architecture removal occurs.

The separate LiveContainer candidate selects Swift `-Osize` only for the UI framework Release target. Debug, LiveProcess dead-code retention, signing, runtime symbols and dSYM policy stay intact. This may reduce the largest host binary but has not yet been measured. Record actual build settings, final bundle/IPA sizes and regression results. Since the candidate also includes feature fixes, comparing its total size with 6.0 is not an isolated compiler-flag experiment.

## Acceptance still required

Native build and package validation, followed by installation, sign-in, refresh, Sources updates, extensions and guest device checks. Preserve separate symbols. No claim of minimum achievable size, runtime compatibility, installed-size reduction or RAM improvement follows from recompression. Additional stripping/provider consolidation requires independent qualification rather than removing supported functionality to meet a size number.
