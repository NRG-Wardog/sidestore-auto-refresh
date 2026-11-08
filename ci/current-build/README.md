# Build and package the pinned maintained graph

Run **Build and package pinned maintained graph (6.0.1 basis)** manually from
Actions, selecting `main` after this change is reviewed and merged. Manual
dispatch also supports another branch in this repository, including a review
branch before merge; it builds that branch's committed graph and records its
builder SHA. It does not automatically build pull requests or publish releases. The workflow
file retains its existing `cold-vpn-native.yml` identifier so dependent source
contracts keep referring to the same implementation.

This is not a moving `latest` build. The complete component revisions are in
[`../issues-native/candidate-graph.json`](../issues-native/candidate-graph.json).
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
v6.0.1 compressed size. The older `build-current.yml` / `livecontainer-build.yml`
wrappers still reproduce the separately pinned 6.0.0 unsigned-native graph;
do not use them to select this graph.

Portable entry checks:

```sh
python3 -B -m unittest discover -s ci/current-build -p 'test_*.py' -v
```

A fresh macOS build and independent artifact review are required before using
this new entry for delivery. No source revision or release pointer is advanced
by this change.
