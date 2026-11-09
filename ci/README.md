# Build and validation

Application source is maintained in the component repositories. The [current
v6.0.1 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.1)
records the shipped source graph, exact IPA checksum and matching build runs.

## Build entry points

The manual `livecontainer-build.yml` and `build-current.yml` actions call the
same immutable LiveContainer native workflow at
`c7111b580254190a0c037037036aaed49bfce8c6`. They replay the pinned **6.0.0 graph**,
not the latest application source or the current 6.0.1 release. Outputs are
unsigned application archives, not installable IPAs.

Installable packaging is a separate exact-input admission step. The maintained
[package action](https://github.com/NRG-Wardog/LiveContainer/actions/workflows/package-diagnostic.yml)
consumes reviewed 6.0.0 native run `40102842840` and pinned evidence. It does not
select the newest run or publish a release. Expired artifacts require a new
build and explicit evidence review/rebinding, not substitution of arbitrary inputs.

For a new candidate, verify the source graph, workflow revision, run/attempt,
archive digests and admission receipts before packaging. A green run for a
different input is not qualification. See [contributing](../CONTRIBUTING.md)
for focused tests and prerequisites.

## Validation layout

- `issue52-integration/`: current integration inventory and regression coverage.
- `packaging/`: packaging compatibility contract.
- `diagnostics/`: structured error codes and presentation coverage.
- `issues-native/` and candidate-specific directories: native qualification,
  exact-input manifests and packaging checks.
- `maintained-build/`: immutable build-entry contracts.
- `source-integrity/`: reviewed current packaging, dependency and workflow hashes.
  Native source-owner pins and artifact admission remain independent build gates.

Validation evidence is not a device-compatibility guarantee. Keep app identity,
user data and signing/pairing state intact when testing updates. Do not commit
credentials, pairing records, private keys or personal logs.
