# Isolated OpenSSL size-mode qualification

This experiment is not a dependency promotion or an app build. Both maintained
builders still default to `O2`. The dedicated branch workflow explicitly requests
`Os`, records it in receipts, and leaves every source, provider, architecture,
minimum OS, no-assembly setting, exported symbol and dSYM policy intact. No
arbitrary environment override or optimization string is admitted. The standalone experiment does not itself authorize staging. The later explicit
consumer policy described below requires fresh same-run qualification.

The recognized official OpenSSL 3.5.9 archive and Xcode 26.4.1 remain pinned.
The existing native macOS provider, modern/legacy PKCS12, actual production ZSign
CMS/Apple attribute, independent Apple Security and cold-process load-order
suite must pass before generating all three iOS slices. iOS probes compile/link;
they are not executed or represented as device evidence.

The exact O2 baseline is the verified all-eight-issues IPA from run 40229079312,
whose hash and both OpenSSL entry paths are in `baseline.json`. Both entries
contain identical binaries already transformed with the reviewed Apple `strip
-S` policy, not local-symbol stripping. Their actual ZIP-entry compressed sizes
are each 1,914,140 bytes, exactly equal to independently recomputed raw-DEFLATE
level 9. The baseline is evidence only and is not downloaded or executed by this
workflow. Its complete 10,815 defined-external-symbol-name multiset is SHA-bound.

The Os device binary is separately copied through the same Apple `strip -S`
operation, checked by the unchanged strict structural preservation gate, matched
to its complete retained dSYM, required to remain unsigned, and compared against
the baseline's entire defined-external API name closure. Measurements use the
same compressor settings on the resulting bytes. Reports give actual raw and
compressed differences per copy and for two separate copies, never inferred
whole-app savings. Increased size is a valid experimental result, not hidden.
No algorithms, providers, architectures, resources or debug sidecars are removed.

The workflow retains generated libraries, headers, receipts and complete matching
dSYMs only after qualification/measurement succeeds. Non-secret logs/configuration
and raw observed measurement evidence survive failures. Synthetic probe keys and
source working directories are not uploaded. The final app graph and the current
24 MB target remain unqualified until a separately reviewed integration build.

Nominal recipe options are insufficient: measurement also verifies the actual
native compiler invocations in the host and three slice build logs. Every parsed
compile command must use the pinned compiler and expected target; its final
optimization flag must be `-Os`. LLVM/GCC coverage flags and unrecognized feature
macro definitions/undefinitions are rejected. Both default and legacy provider
source compilations and the static-legacy marker must be present. Effective
`CFLAGS = ...` configdata lines are checked separately from the non-effective
upstream target default `CFLAGS => "-O3"`. Exact configuration/log hashes and
per-slice compile-command counts are retained in the measurement receipt.


## Conditional integration after the measured native result

Run `40236234277` passed the native qualification and measured 4,582,712 raw /
1,805,140 DEFLATE-9 bytes per equally STAB-stripped Os device copy. Relative to
the exact O2 baseline this saves 109,000 compressed bytes per copy, 218,000 for
two separate copies. All 10,815 defined-external names remain unchanged. This
is not a whole-app measurement or device-runtime claim.

The integration consumer policy now selects only `Os`; a default historical O2
build is not admitted into these consumers. Builder defaults remain O2. The
main native workflow regenerates and qualifies the host and all iOS slices in
the same run before either consumer is staged. `admission.verify_measurement`
checks the run/attempt/commit, official source, every relevant helper hash,
complete generated inventories, original and copied build receipts, three
crypto component receipts, live compiler/configuration evidence, both current
binary bytes and a freshly recomputed structural/export/dSYM check. It also
recomputes the reported size difference from the measured bytes. The recorder
must call it before mutation and again for each sealed snapshot/payload check.
A historical green job or self-consistent edited summary cannot substitute for
these current inputs. No access scope, algorithms, providers, architectures,
resource set, original diagnostics or signing policy is expanded or removed.
