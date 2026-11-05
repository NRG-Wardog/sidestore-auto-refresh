# Candidate validation harnesses

This directory contains issue-specific and cross-repository validation harnesses,
reviewed source projections, fixtures and exact-input manifests. The subdirectory
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

This directory is distinct from [`.ci/`](../.ci/README.md), which retains frozen
validation-history and repository-maintenance contracts.

[Documentation and repository map](../docs/README.md)
