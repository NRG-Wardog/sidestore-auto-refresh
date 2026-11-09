# Build and validation

Application source lives in the component repositories. The supported entry is
**Build and package pinned maintained graph (6.0.1 basis)**,
[`cold-vpn-native.yml`](../.github/workflows/cold-vpn-native.yml).
Run it manually on the reviewed branch. It records the exact builder and component
revisions and produces an experimental same-run IPA, not an automatic release.
See [build details](current-build/README.md).

The old unsigned 6.0.0 wrappers and their automatic release-branch dispatcher have
been retired. A release branch push no longer dispatches that obsolete graph.

## Checks and packaging

- `current-build/`: supported entry and mandatory receipt controls.
- `source-integrity/`: reviewed current source and workflow hashes.
- `issues-native/`: component identity, native tests and same-run packaging.
- `packaging/`: packaging compatibility contract.
- `issue52-integration/`: component integration inventory and regression checks.
- `diagnostics/`: structured error and presentation coverage.

Candidate-specific directories still support exact-input qualification and
packaging. Do not substitute the newest artifact into an old manifest. Zopfli
recompression is separate from native packaging and requires matching evidence.
Passing fixtures is not device qualification. Keep credentials, pairing records,
private keys and personal logs out of this repository.
