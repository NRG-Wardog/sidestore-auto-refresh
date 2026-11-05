# Maintained-source migration and provenance

This directory retains the source-migration contracts, historical source snapshots,
resolver locks, ownership records, replay tests and cutover evidence used by the
maintained build and its regression checks. It is not a temporary build directory.

Start here:

- [Maintained integration cutover](MAINTAINED_INTEGRATION_CUTOVER.md)
- [Final pin import](FINAL_PIN_IMPORT.md)
- [Runtime contracts](contracts/README.md)
- [Contract coverage](contracts/COVERAGE.md)

Directory roles:

- `contracts/`: machine-readable runtime compatibility and owner contracts.
- `tests/` and `local_replay_tests/`: migration and replay regressions.
- `p0-*`, `p1-*`, `p2-*`: versioned migration inputs, source owners and provenance.
- `diagnostics/`: bounded diagnostic experiments and frozen evidence.
- `cutover-evidence/`: retained test results and final source-graph receipts.
- `historical/`: original snapshots for comparison. References inside these files
  reflect their historical layout and are intentionally not rewritten.

Several scripts import the root `prepare_candidate_inventory.py` module. Paths,
source hashes and historical Git objects are part of the validation contract;
preserve them rather than merging these records into the ordinary documentation.

[Documentation and repository map](../docs/README.md)
