# Completed maintenance and release workflows

These six workflow definitions are preserved byte-for-byte from
`6f20f702daad7aa8f870a9a3fe0d0b321dc41741`. They are historical records outside
GitHub Actions' active workflow directory, not supported current entry points.

- `publish-v6.0.0.yml`: the completed exact-artifact release promotion.
- `amend-v6.0.0-notes.yml`: the completed body-only release-note amendment.
- `reviewed-branch-cleanup.yml`: the reviewed, recoverable branch-deletion campaign.
- `reviewed-branch-classify.yml`: its pinned read-only ancestry classification.
- `reviewed-branch-semantic-inventory.yml`: its bounded read-only semantic inventory.
- `reviewed-branch-forwardports.yml`: the pinned recovered-forwardport validation.

Archiving prevents a directory-only cleanup from retriggering old release writes,
branch deletion attempts or obsolete validation campaigns. Original `.ci/` paths
inside these records describe their historical execution layout and are intentional.
Receipts, inputs and testable helpers now live directly under `ci/`. The source
history contract enumerates these relocations and checks the original bytes.
Do not reactivate a workflow without a fresh review of its inputs and authority.

Maintained build entry points, packaging contracts and current candidate workflows
remain in `.github/workflows/`.
