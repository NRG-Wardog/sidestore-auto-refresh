# v3.1.0 audit: pinned LiveContainer source comparison

Read-only review performed on 2026-10-05 before executing the recovered
LiveContainer sources. This is a source/provenance review, not a malware-free
certificate, binary reverse-engineering audit, or device acceptance result.

## Independent source identity

The user-owned fork [NRG-Wardog/LiveContainer](https://github.com/NRG-Wardog/LiveContainer)
still contains the exact required revision. Its current `main` at review time is
`3afa9eb9a53625e9b8bd8b932e5785fd716ad5ae`, tree
`13ddb2b9cfb7bca080bc14117957e94be2fd0588`. That commit's direct parent is
the required pin `12377cf3b91d51739a33f14a302e5f522b238593`, tree
`06c7c54047734a7e19ec260d04dc073a52a1c872`.

The complete, non-truncated recursive tree was read independently from the
[user fork's GitHub API](https://api.github.com/repos/NRG-Wardog/LiveContainer/git/trees/06c7c54047734a7e19ec260d04dc073a52a1c872?recursive=1).
All **262 root-repository file blobs** in the recovered mirror checkout were
re-hashed using Git's blob framing and matched that tree. There were no content
mismatches. The root commit object itself also re-hashed to the required pin.

The full tree-to-tree diff from the required pin to the fork's `main` changes
only `LiveContainerSwiftUI/Views/Settings/LCJITLessDiagnoseView.swift`:
28 inserted and 10 removed lines add app-group warning colors and move the
existing team-entitlement read earlier. The full diff was reviewed. It does not
add an upload destination, hidden startup action, downloaded executable, or
destructive operation. Workflows, packaging scripts, project build phases,
dependency references and submodule gitlinks are identical between these two
trees. The pin predates this UI change; there are **no mirror-specific additions**
in the verified pinned tree.

## Dependencies and build surface

| Dependency | Required commit | Verified tree | Working-tree verification |
| --- | --- | --- | --- |
| litehook | `8025e0c8ebdf5cdd1d2a4f45025813234bf9dc55` | `62bc06a17a1c12d805aec1ada25910bfa9f89781` | 12 blobs match |
| OpenSSL | `623c84da314e85363236507ca38a4bde65df21c3` | `21bc09387e7f91a46eb8bd26587ce1d052277801` | 4,403 blobs/symlink targets match |

Both gitlinks are present in the independently retrieved user-fork tree. The
root `.gitmodules` also retains an old fishhook section, but the pinned tree has
no fishhook gitlink. It is not an additional initialized dependency.

Reviewed build/security-sensitive surfaces:

- Entire 609-line litehook runtime C/header implementation: Mach-O/dyld cache
  inspection, local memory protection and function rebinding. No network client,
  credential collection/upload, background persistence installer, or destructive
  filesystem routine was found there. Its test-only `Tests/upload.sh` builds a
  test binary and copies it to an explicitly supplied SSH host; it is not a
  runtime or Xcode build phase.
- Root workflows, packaging script, source-JSON updater, `.gitmodules` and the
  Xcode shell build phase. The Xcode phase reads version-control metadata, writes
  build-version metadata and copies localization resources.
- Runtime network callsites and sensitive-storage/deletion callsites. Inspected
  paths include user-configured source/IPA downloads, user-configured JIT
  endpoints, universal-link metadata and certificate OCSP checks. Existing
  keychain hooks, shared guest storage, certificate import and user-confirmed
  deletion tools remain part of LiveContainer's inherited trust model.
- `Resources/universal.js`: inherited debugger/JIT script, including evaluation
  of script text obtained from the debugged guest. This is intentional powerful
  functionality, not evidence that arbitrary guests are safe to trust.

## Packaging caveat that remains separate

The inherited `.github/build_github.sh` downloads a nightly SideStore IPA and
downloads/executes `LiveContainer/dylibify` release `1.0` without checking a
content digest. The builder's `package_livecontainer_combined.py` replaces the
nightly SideStore download with its locally built patched IPA, but at the
reviewed baseline `54eb199044311193dc5a70d39f4c646218804336` it **does not remove
the downloaded dylibify executable**.

Authenticating the LiveContainer source tree does not authenticate that separate
release asset. Its provenance/content must be verified or separately approved
before execution. Do not describe the packaging path as having no downloaded
tools. No dylibify asset or upstream build script was executed during this review.

## Conclusion and limits

The recovered pinned tree matches the user's own fork, including its dependency
commit identities; no malicious mirror-added change was found. Fetching the
same pin from the user-owned fork avoids relying on the mirror for the root
checkout. The exact litehook gitlink, verified above, remains separately pinned.

This does not establish that all inherited code is bug-free or malware-free.
Bundled CydiaSubstrate and OpenSSL binaries were verified as unchanged content,
not reverse-engineered or rebuilt for reproducibility. No app was installed,
no account was accessed, no credentials were inspected, and no source or build
script from the mirror was executed as part of this review.

## Recovered dylibify source candidate

The official repository and release-tag API returned 404 during this review.
GitHub repository metadata identifies `lgq2015/dylibify` as a direct fork of
`LiveContainer/dylibify`. Its recovered commit is
`5daf713df9fb07724510490c88aa8e00061be14a`, tree
`1c4f323e269ba3834b28b0a1b0d17c09f96b6926`. Its parent is original
`jakeajames/dylibify` commit `17cc528402714ad1bd918cbb2ebcb6976e80540a`.
No tag or release survives in the inspected fork, so this source cannot be
claimed to reproduce the former `1.0` executable byte-for-byte.

The complete 549-line `main.m`, parent diff, build recipe and MIT license were
read. The only new functional code is chained-fixup handling. No network,
credential access, shell/process execution or persistence installer was found.
It copies the caller-specified input to the caller-specified output and rewrites
Mach-O metadata. The desktop recipe is direct Clang compilation with Foundation
and ARC; it needs no package dependency or downloaded helper. Reviewed original
`main.m` SHA-256: `0c30a8bd4088aa891eb172c5d3a3e185cc9d00572b60c7b29f30e08c6245acd8`.

Source safety defects must be addressed before adopting that replacement:

- `patch_pagezero` allocates less than the 72-byte segment write for short
  output names, causing an out-of-bounds read. Its string sizing also uses
  NSString character count for a UTF-8 copy, and subsequent fat slices skip the
  install-name write
- File allocation/seek/read/write results are not checked
- `dylibify` returns failure even on its success path, while `main` discards the
  return value and returns success

These are source defects, not evidence of malicious intent. Any adopted
source-built replacement needs separate review of its fixes, bounded-input and
conversion tests, recorded source/output hashes and package-output validation.
No recovered dylibify code was executed for this review.

## Other inherited build inputs

The companion `audit-v3.1.0-input-provenance.json` records the source pins,
lockfile hashes and declared downloadable binary checksums inspected here.
These are expected identities, not proof that the assets were downloaded,
built or executed in this audit.

The baseline workflow uses Cargo without `--locked` for transport tests/checks/
builds. Its intentional local-jktcp rewrite needs a controlled lockfile update;
all other package identities/checksums must remain fixed. SwiftPM manifests
contain branch requirements, although the checked-in `Package.resolved` files
record exact revisions. Dependency resolution must not silently advance them.

Additional executable build inputs come from ordinary official distributions:
GitHub Actions (`actions/checkout@v4`, `actions/upload-artifact@v4`,
`maxim-lobanov/setup-xcode@v1.6.0`), the `macos-26` runner image and Xcode 26.4,
Homebrew `ldid`/`openssl@3`, crates.io `bindgen-cli` 0.72.1 with `--locked`, and
the selected Rust toolchain's `aarch64-apple-ios` target. These tags/formulae/
runner images can change; record actual action revisions, runner image, tool
versions and final resolved lockfiles for the real build. Baseline tool checks
skip installation when a command exists, so presence alone does not prove the
requested bindgen version. This review did not audit every transitive package
or declare these inherited inputs malicious.
