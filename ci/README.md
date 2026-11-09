# Build and validation

Run **Build and package pinned maintained graph** through
[`native-build.yml`](../.github/workflows/native-build.yml) on the reviewed branch.
It records exact component revisions and produces an experimental IPA with
same-run qualification evidence. It does not publish a release automatically.
See [build details](build/contracts/README.md).

## Layout

- `build/`: source acquisition, pinned graph, compiler and build receipts.
- `qualification/`: native runtime, language, database, VPN and other product checks.
- `packaging/`: IPA admission, size analysis and exact-input recompression.
- `contracts/`: source integrity and component integration inventories.
- `diagnostics/`: error classification and presentation coverage.
- `artifacts/`: authenticated, bounded artifact retrieval.
- [`../tests/ci/`](../tests/ci/): synthetic controls for these tools.

## Product regression gate

The repository contract workflow acquires the exact component graph before
running every module in `contracts/product-tests.json` on macOS through the
required-test runner. No skips are allowed. It retains test timings and failure
logs. This source and native-fixture gate does not replace the full IPA build.

## Focused checks

`focused-diagnostics.yml` runs language, multitasking, VPN, database startup and
refresh-truth checks independently. Select one suite or `all`. Pull requests
changing this dispatcher or its scoped reader run all five jobs. Each retains
failure evidence; none packages an app or replaces full native qualification.

`package-optimization.yml` is a separate exact-input Zopfli experiment. Its sealed
manifests and reviewed source snapshots are evidence, not current build inputs.
Never substitute a newer artifact or rewrite historical hashes to make it pass.

Passing synthetic fixtures is not device qualification. Keep credentials,
pairing records, private keys and personal logs out of the repository.
