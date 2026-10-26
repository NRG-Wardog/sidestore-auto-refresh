# Issue 56: post-build bounded Zopfli sweep

This is a **new lineage**, not a modification of the previous issue55 receipt.
The committed `input-manifest.json` is deliberately invalid until an actual
successful `.github/workflows/issue56-native.yml` run and downloaded artifact
have supplied their exact IDs, SHA256 hashes, sizes, member count and commit.
Acquisition rejects pending/failed runs, other workflows/owners and stale assets.

The workflow starts only on `validation/issue56-zopfli`. Do not start it before
the fixed native build is green. No app, compiler or compressor executes in
local unit tests: only owned Python fake compressors exercise the driver.

Approved source remains Google Zopfli commit
`df1517dd8518113a245e4d203a04b8a0e02ab493`. All 81 Git blobs and the complete
Git tree are reconstructed before direct compilation of its C files with the
existing compiler. No installer, build hook or alternate source is used.

## Budget and selection

- Two workers maximum, each limited to 2 GiB address space, no core dumps.
- Compression uses at most 6,900 seconds wall time, leaving 300 seconds of the
  approved two-hour execution budget for compilation and verification.
- Direct compiler execution is capped at 60 seconds; audit at 180 seconds.
- Individual attempts are capped at 1,800 seconds and remaining aggregate time.
- Iterations 5, 15, 50, 100, 300, 1,000 and 3,000 form an adaptive sweep.
- Baseline visits all entries of at least 1,024 uncompressed bytes. Later passes
  prioritize measured marginal savings per elapsed second. After iteration50,
  two successive non-improving attempts remove a member from further passes.
  Attempts whose estimated cost exceeds available time or per-attempt limits
  are omitted. These are heuristics, **not a proof of globally minimal size**.
- Each stream is independently decompressed before selection. Only strictly
  smaller verified streams replace the current best. Timeouts kill/wait for
  the sole exec-replaced compressor and preserve prior verified improvements.
  Nonzero exits/signals discard partial output and retire that entry from later
  passes, preserving its prior best without guessing the failure cause. Invalid
  streams from a nominally successful exit still fail the entire qualification.
- Progress receipts are written after every attempt. Reports state the actual
  runtime and stopping reason; this does not pretend to run two hours if the
  bounded search schedule finishes or plateaus earlier.

The independent auditor uses ZIP parsing separate from the driver's parser,
compares every decompressed member, exact raw local/central metadata except
compressed sizes and required offsets, unchanged Russian resources, widget
signature bytes and nested SideBackup ZIP CRC. It recomputes best-of selection
from every successful stream receipt and verifies the complete source tree.
Final qualification requires no growth and an IPA at most 24,000,000 bytes.
No native/device execution, installed-size improvement or public release is
claimed. Original native qualification is inherited only through identical
payload bytes. Preserve both original and best compressed copies.

Run source controls with:

```sh
python3 -B -m unittest discover -s ci/issue56-zopfli -p 'test_*.py' -v
```
