# Frozen validation and maintenance controls

These paths are used directly by workflows and source-integrity checks. This
hidden directory is intentional and is separate from the [candidate CI harnesses](../ci/README.md).

- `validation-history/`: unified validation manifest, reviewed successor mappings
  and exact-source verification.
- `maintained-build/`: contracts for the immutable maintained build entry points.
- `branch-semantic-review/`, `branch-cleanup/`, `branch-forwardports/`: reviewed
  branch inventories and narrowly scoped maintenance tools.
- `release-v6.0.0/`: original release promotion inputs, notes and provenance.
- `release-notes-v6.0.0-20261024/`: bounded historical release-note amendment.

The versioned release directories are historical records, not the current release
notes. Their scripts and workflows may have external side effects; retaining them
is not an instruction to rerun them. Do not rewrite frozen receipts or relocate
hash-bound files as cosmetic cleanup.

[Documentation and repository map](../docs/README.md)
