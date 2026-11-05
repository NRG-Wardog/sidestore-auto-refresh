# Documentation and repository map

Start with the [installation guide](../README.md#installation) and the
[latest published release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/latest).
The repository keeps current tooling alongside historical verification evidence.
A retained document or successful historical run does not qualify a newer build.

## User and contributor guides

- [Compatibility reports](COMPATIBILITY.md)
- [Verification scope and device evidence](VERIFICATION.md)
- [Error diagnostics](ERROR_DIAGNOSTICS.md)
- [Contribution and test instructions](../CONTRIBUTING.md)
- [Security reporting](../SECURITY.md)
- [Licenses and third-party notices](../THIRD_PARTY_NOTICES.md)

## Build and integration references

- [Maintained 6.0 build entry and reviewed packaging](MAINTAINED_BUILD.md)
- [Custom integration boundaries](CUSTOM_INTEGRATION_BOUNDARIES.md)
- [Validation history reconciliation](VALIDATION_HISTORY_RECONCILIATION.md)
- [Combined candidate acceptance](COMBINED_CANDIDATE_ACCEPTANCE.md)
- [CI harnesses and exact candidate evidence](../ci/README.md)
- [Frozen validation and maintenance controls](../.ci/README.md)
- [Migration and source provenance](../migration/README.md)

## Repository layout

- `scripts/`: build, packaging, audit and compatibility tools. Historical patch
  tools remain because their tests and source-provenance contracts use them.
- `tests/`: regression tests and fixtures, including platform-specific checks.
- `.github/workflows/`: active workflow entry points. Some are manual diagnostic
  or release tools, not automatic production build steps.
- `.github/workflow-archive/`: retained historical workflows, outside the active
  workflow directory.
- `docs/releases/`: historical release notes retained in this repository.
- `docs/validation/`: issue-specific validation records.
- `docs/screenshots/` and `docs/assets/`: documentation images.
- Root `README.*.md`: translated user guides with stable language-selector paths.
- Root `prepare_candidate_inventory.py`: shared read-only Git inventory module
  imported by migration and source-preparation tools; it is not a generated file.

## Historical material

[Historical release notes](releases/README.md), versioned audits and investigation
records describe their stated source revisions and validation limits. Consult the
published release for the current downloadable artifact and its release notes.
Keep frozen manifests, snapshots and fixtures at their existing paths: workflow,
source-integrity and provenance checks can bind both their paths and exact bytes.
