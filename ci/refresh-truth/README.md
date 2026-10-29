# Refresh outcome native qualification

This qualification runs the exact candidate source-owned fixtures before native
app compilation, and rebinds their receipt to the same compiled source graph.
It does not connect to a device, export provisioning profiles, or prove which
profile iOS will select at launch. It cannot guarantee future certificate trust.

The focused `validation/refresh-truth-preflight` workflow acquires the existing
reviewed dependency graph, runs source-owned Swift policy/operation and fake-FFI
memory fixtures, then actual Rust parser and bounded-transaction unit tests.
Skipped tests, changed counts, missing test cases, dirty tracked sources, a
parent repository mistaken for an owner, or changed source hashes fail closed.
All output and Cargo target files are outside the source checkouts. The existing
Rust build script may generate its ignored header copies; no claim is made
that ignored headers are unchanged during fixture compilation. The full native
workflow separately verifies exact generated headers, signatures and all nine
required native exports against the archive and framework copies.

`refresh-truth-tests.json` is self-contained and binds the run, all source pins,
finite production/fixture input closure, non-skipped native test logs and scope
limits. It is checked at native receipt creation and both before and after
packaging. Private quick recompression requires the same receipt and hashes.
A failed native run or a receipt for different source bytes cannot qualify.

Before compilation, source owners must have matching Git root/HEAD/tree/index
and clean nonignored worktree state. After compilation the existing source
inventory seal permits only the already reviewed resolver metadata and exact
recorded generated files. The new recheck compares index entries to HEAD and
tracked bytes to that seal, while every tested production input remains equal
to its committed Git blob. It does not waive a dirty source tree generally.

Current source fixture counts are deliberately closed in
`ci/issues-native/refresh_truth_receipt.py` and must be updated only alongside
reviewed source tests. Pure Python controls here test evidence rejection; they
are not the Swift/Rust native qualification itself.
