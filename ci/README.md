# Candidate validation harnesses

This is the single directory for repository CI tooling: issue-specific and
cross-repository validation harnesses, reviewed source projections, fixtures,
exact-input manifests, frozen validation and maintenance controls. The subdirectory
names identify the investigation or candidate that introduced them; older names
do not mean the contents are unused.

Useful starting points:

- [Cold-VPN bounded packaging and final candidate evidence](cold-vpn-zopfli/README.md)
- [Cold-VPN native validation](cold-vpn/README.md)
- [Language-switch validation](language-switch/README.md)
- [Startup and upgrade validation](startup-upgrade/README.md)
- [Refresh truth checks](refresh-truth/README.md)
- [Multitask launch checks](multitask-launch/README.md)
- `issues-native/`: shared native runners, source contracts and reviewed fixtures.
- `issue52-integration/`: integration maintenance inputs and coverage checks.

The workflows under `../.github/workflows/` select the relevant harness and exact
source graph. Do not run an arbitrary historical harness as evidence for a newer
release. Preserve referenced files, snapshot bytes and fixture paths when making
layout changes.

## Repository validation and maintenance

- `validation-history/`: frozen validation manifest, exact reviewed successors,
  and the finite old-to-current path mapping for this directory consolidation.
- `maintained-build/`: immutable maintained build entry contracts.
- `branch-semantic-review/`, `branch-cleanup/`, `branch-forwardports/`: reviewed
  inventories and bounded maintenance tools.
- `release-v6.0.0/`: original promotion inputs, notes and provenance.
- `release-notes-v6.0.0-20261024/`: historical release-note amendment records.

These controls previously lived under `.ci/`. Their historical manifests retain
original paths and bytes; the current source gate verifies relocation explicitly.
Other repositories can still have their own `.ci/` directories. Do not rewrite
those external references or historical Git-object paths.

Versioned release records are not the current release notes. Completed one-time
release and branch-maintenance workflows are retained under
[the workflow archive](../.github/workflow-archive/maintenance-v6.0.0/README.md),
outside the active Actions directory, so this layout change cannot rerun them.
Their scripts may have external side effects; retaining them is not an instruction
to rerun them.

[Documentation and repository map](../docs/README.md)
