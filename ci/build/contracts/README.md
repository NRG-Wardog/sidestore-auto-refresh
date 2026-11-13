# Build and package the pinned maintained graph

Run **Build and package pinned maintained graph (6.0.1 basis)** manually from
Actions, selecting `main` after this change is reviewed and merged. Manual
dispatch also supports another branch in this repository, including a review
branch before merge; it builds that branch's committed graph and records its
builder SHA. It does not automatically build pull requests or publish releases. The workflow is `native-build.yml`; its source and receipt controls are checked together.

This is not a moving `latest` build. The complete component revisions are in
[`../candidate-graph.json`](../candidate-graph.json).
The job prints every revision and the graph digest before compilation. The
initial graph matches the v6.0.1 component basis. A changed builder commit means
a new candidate, not reproduction of the published IPA's bytes or a new release.

All native qualification, compiler-input, regression, signing, size-optimization
and same-run packaging steps are retained. `receipt-current` makes the full
runtime receipt gates mandatory independently of a historical branch name.
The successful output is an experimental installable IPA and its provenance.
Nothing is published automatically, and passing fixtures is not device testing.

Zopfli recompression remains separate. Its old exact-run manifest must not be
reused for a new run or relaxed to accept whichever artifact is newest. This
entry produces the qualified pre-Zopfli IPA, so its size is not the published
v6.0.1 compressed size. The old unsigned 6.0.0 wrappers and release-branch
dispatcher are retired; this is the supported build entry.

Portable entry checks:

```sh
python3 -B -m unittest discover -s tests/ci/build -p 'test_*.py' -v
```

A fresh macOS build and independent artifact review are required before using
this new entry for delivery. No source revision or release pointer is advanced
by this change.

The current refresh qualification also requires strict portal identity execution:
SideSign's production parser (172 assertions and 3 source controls), SideStore's
preservation runner (55 policy cases, 98 integration cases and 6 source controls),
and LiveContainer's portal wire runner (32 emitted checks and 2 source controls).
The existing refresh-observation runner retains all 189 previous cases and adds
27 portal producer-to-copy cases. Missing, skipped, duplicated, reordered or
incorrect native results cannot seal the current receipt or admit packaging.
All runners consume the acquired graph, including the explicitly selected
SideStore peer. Source input hashes, toolchain identity and failure logs stay in
the same-run refresh evidence. These fixtures do not exercise Apple services or
a physical device. Frozen historical receipts remain separate and unchanged.
