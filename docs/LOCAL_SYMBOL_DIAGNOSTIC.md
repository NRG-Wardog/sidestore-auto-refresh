# Local symbol measurement, not package approval

Scope: copy-only Apple `strip -x` on LiveContainerSwiftUI and embedded SideStore
from an already qualified, STAB-stripped input IPA. No source/feature/provider/
architecture removal, no app execution, no new IPA, no signing acceptance.
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

Apple `strip -x` was observed on both reviewed inputs to introduce one N_OPT
STAB (`radr://5614542`, type0x3c, section0, description0, value90260802).
The diagnostic captures that output, then runs Apple `strip -S` on a separate
copy. The unchanged debug-only gate must validate intermediate to cleaned bytes;
the unchanged original-to-final local-symbol gate still requires zero STAB.
There is no marker exemption, post-hoc mutation, or signing approval.
