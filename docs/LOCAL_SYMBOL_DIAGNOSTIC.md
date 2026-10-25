# Local symbol measurement, not package approval

Scope: copy-only Apple `strip -x` on LiveContainerSwiftUI and embedded SideStore
from an already qualified, STAB-stripped input IPA. No source/feature/provider/
architecture removal or app execution. Default mode emits no new IPA; optional
internal package qualification has separate strict signing/archive gates.
The original IPA retained in diagnostics is archival, not a transformed output.

The diagnostic guard allows deletion only of unreferenced non-external N_SECT
or N_ABS records in the local LC_DYSYMTAB category. It retains every external,
undefined, indirect-referenced or otherwise protected tuple and rejects added
or modified locals. The existing bounded parser still checks all ranges,
commands and LINKEDIT blobs. Exports, fixups, function starts, unwind/data-in-code,
all section descriptors, VM layout, all non-LINKEDIT bytes (including Swift and
Objective-C metadata), UUID and indirect symbol semantics remain exact. Signature
bytes are not claimed preserved or valid. Signing qualification is a separate
future decision.

## Source lookup audit

The reviewed LC `LCMachOUtils.m:LCFindSymbolOffset` delegates to litehook's file
symbol scanner. All observed callsites in `LiveContainer/Tweaks/Dyld.m` target
`/usr/lib/dyld` or `/usr/lib/dyld_sim`; in-memory `litehook_find_symbol` calls use
`getDyldBase()`. Shared-cache symbol queries target system libraries. These images
are outside the transform allowlist. The two `dladdr` uses consume `dli_fname`,
not local symbol names or symbol addresses. Dynamic loader lookups in
`LCBootstrap.m`, `main.c`, `LiveProcess/main.m` and the dlsym hook are backed by
unchanged exported/undefined/indirect metadata, including SwiftUI `main` and
host `LiveContainerMain`, `callAppMain`, and `installSideStoreHooks` routes.
AnisetteKit's owned C++ host loader calls normal `dlsym` for system/ELF-import
resolution, not a Mach-O local nlist scanner. SideStore source search found no
local nlist scanner. This is a bounded source audit, not a proof about arbitrary
injected guests or third-party diagnostic tools. Matching complete dSYMs and
original binaries remain retained for symbolication.

## Measured input inventory (before any new strip)

Exact qualified IPA SHA256:
`34e00688c9015d3b6fdd2bf6485fd20aa0396229fbd4962dfeea7ee8406079db`.

- SwiftUI: 58,764 eligible locals; 940,224 nlist bytes and 14,413,097 distinct
  local-only name bytes. These raw counts are not additive compressed savings.
- SideStore: 120,692 eligible locals; 1,931,072 nlist bytes and 6,983,412 distinct
  local-only name bytes. 2,918 protected external/undefined records remain.
- Original nightly SHA33831bb279d74f2da1be08eb3cc6d3646992a6120da32ffb763269696052b868:
  SwiftUI has 3,828 locals; SideStore has no non-STAB locals. It differs in code
  and features, so its size is a reference, never a replacement or equivalence
  claim. Removing functionality to match nightly is outside this experiment.

Actual Apple strip measurements and native signing remain pending. The target
is at most 24 MB or the closest demonstrated safe result; no minimum claim is
made from symbol counts alone. The existing safe -S packaging path is untouched.

## Finite Apple compatibility sentinel

Official Apple cctools commit `e0d56624eca2a76c2ace4c21850df9e666de4ca5`,
`misc/strip.c` SHA256 `53d2ce75886237bfd99e953790e48920bb9dbfd58873fb2a5c46d25dfe195ddb`,
[documents intentional sentinel insertion](https://github.com/apple-oss-distributions/cctools/blob/e0d56624eca2a76c2ace4c21850df9e666de4ca5/misc/strip.c#L4148-L4264).
This source is explanatory evidence, not a claim that the pinned Xcode binary
was built from that revision. Independently captured Xcode outputs match it.

The local-only policy permits exactly one tuple: name `radr://5614542`, type
0x3c, section0, description0, value0x05614542, at index0 as the sole local entry.
Indirect symbols must exist and none may reference it. It prevents an indirect
symbol from taking index0; it is deliberately retained, not manually deleted.
Other STAB records, altered/duplicate/misplaced sentinels, protected symbol
changes, or any export/section/VM drift fail. Original inputs still require zero
STAB. The ordinary production debug-only guard is unchanged. Apple `-S`
reintroduces the same sentinel; a second `-S` is not used. `-N` would remove
protected symbol tables and is forbidden.

## Optional internal package qualification

`--qualify-package` additionally verifies original SideStore with Apple strict
codesign before any transform. Original and resealed SideStore must both satisfy
the existing low-level page/special-slot verification with exact flags2,
dual SHA1/SHA256, SideStore identifier, empty requirements/CMS, no entitlements,
team or runtime flags, and unchanged executable geometry. Only validated unused
signature padding can be zeroed. Final Apple strict codesign and original-to-final
local semantics must pass before the exact two-member ZIP replacement. Every
other member byte/metadata, closure and CRC remains checked, with measured full
archive size and immutable original hash. SwiftUI stays unsigned. Success permits
an internal experimental artifact, not release, device qualification, or delivery
as the current complete Russian candidate. The original and full dSYMs remain
archived separately. Any failed gate prevents derivative artifact promotion.

## App Intent runtime keep closure

The qualified-source adapter calls `_getTypeByMangledNameInContext` from
`SideStoreSupport/SideStoreClient.swift` for both SideStore refresh intent types.
The original package's two complete mangled stems occur only in LINKEDIT. The
unchanged package guard correctly rejected their loss after unconstrained `-x`.

For SideStore only, `-x -s` now retains all original private-extern/global tuples
containing either required intent stem, plus every protected external/undefined
symbol name. Both types' exact `Ma`, `Mn` and `N` metadata roots are mandatory.
For the measured baseline this is172 private-extern family tuples plus2918
external/undefined names,3090 distinct save-list entries. Every retained tuple
(name/type/section/description/value and multiplicity) is protected by the local
semantic gate, not merely by a substring check. No names are injected.

The pinned Apple implementation's local branch (`strip.c:3623–3805`) only honors
save lists for private externals; `-R` operates in the global branch. Therefore65
ordinary local family labels still disappear. These include async continuation
and conformance-cache labels, not just implementation-function labels. Their
code, descriptor, cache and other non-LINKEDIT bytes remain identical; this is
not a device/runtime proof. All172 private-extern family tuples and six metadata
roots remain, and the original App Intent package guards are unchanged.

The save-list option may invoke export-trie pruning. The strict unchanged export
blob gate still applies; the measured SideStore source has an empty8-byte export
trie. SwiftUI receives no save-list and its exports remain protected. Support is
not transformed here, retaining its369 matching host-wrapper symbol records and
all metadata. The full original packaging contract is checked before tools and
again on the final derivative. Runtime tuple loss now fails immediately after
strip, before resealing or archive generation.

## Qualified same-run integration boundary

The App Intent-preserving pipeline passed native qualification in
[run 40235661995](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/40235661995)
at `8c15dcc4cd99f6ac16ab53fae6ca2a18a9a57932`. On its exact pre-Russian measurement
input, the IPA changed from 29,393,354 to 26,558,052 bytes. The output SHA-256 is
`1b917b314b29bc30393057279be4bd19f775546b6f9f2944750798768a374506`.
This is not the final candidate, a 24 MB result, or physical-device evidence.

The complete-candidate packager explicitly enables this second stage only after
its existing six-target debug-only stage. Both stage receipts, original inputs,
matching full dSYMs, exact run identity and cumulative measured sizes are retained.
Every future candidate must repeat all native signing, runtime-anchor and archive
gates on its own exact compiled inputs. Qualification does not bypass these gates.
