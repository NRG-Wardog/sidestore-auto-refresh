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

## Release instrumentation and additional unsigned helper experiment

The verified full candidate from run `40229079312` is 29,393,354 bytes.
Its native evidence archive SHA-256 is
`ac7b61198f2acfcee26e0a6a1e8b5e2925c52941aba4075a7a22a6629ab9e761`.
The effective LiveContainer Release setting was `ENABLE_CODE_COVERAGE=YES`;
actual Clang commands contained `-fprofile-instr-generate -fcoverage-mapping`
and Swift commands contained `-profile-generate -profile-coverage-mapping`.
No explicit source project/xcconfig/scheme opt-in was found. SideStore's build
log also exported that setting as YES, but did not contain these compiler flags.

The new source policy sets only project-level **Release**
`ENABLE_CODE_COVERAGE=NO` in each app project. Debug, test execution, runtime
diagnostic logging, `DEBUG_INFORMATION_FORMAT=dwarf-with-dsym`, architectures,
features, providers, deployment targets and signing settings are unchanged.
The native workflow checks effective Release settings for all project targets
and checks both actual successful native compiler logs for LLVM/Swift/GCC
coverage-generation flags. Synthetic test logs are not used for this gate.
Mapping configuration alone is recorded; actual generated-command absence is
checked separately. Native size savings are pending the rebuilt candidate,
not inferred from source edits.

Same-run debug-only stripping additionally admits exactly the unsigned
`SideStoreSupport.framework/SideStoreSupport` and `ZSign.dylib` binaries.
Each requires its complete same-run retained dSYM and matching UUID; original
binary and dSYM bytes remain in the diagnostic sidecar. The original narrow
Apple `strip -S` structural, export/import, section, UUID, signature, ZIP member
and unchanged-resource gates remain in force. These helpers must remain
unsigned; newly signed helpers are rejected before invoking native tools.
Historical fixed-artifact experiment defaults are not expanded.

The Widget binary has XML and DER entitlements and is deliberately not covered
by the empty-entitlement SideStore resealing policy. Nested SideBackup likewise
requires a separately reviewed archive/signature transformation. Neither is
removed or silently admitted by the unsigned-helper extension. No claim of
meeting the user's 24 MB goal is made until an exact rebuilt IPA is measured.
