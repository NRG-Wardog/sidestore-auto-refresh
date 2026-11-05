# SSA validation history consolidation

The retained P0 and P2 validation histories are consolidated through real merge
commits. Their original source and provenance remain in Git history; this is
not a new application release and does not reactivate an old release builder.
Unused experimental harnesses and their obsolete capture-generation helpers have
been removed from the current tree. Maintained build and regression gates remain.

## Exact sources

- Baseline maintained main: `0678baab19c2ad456c0f5123b9c24c0959436821`.
- P0 login trace: `3edd9fa523a223bcd2898d5c960c5da7ff335be8`.
- P2 alert lifecycle: `1d09456b441912c12227052eb21a26389ac0a203`.

The two historical heads contain 917 shared commits, 10 P0-only commits and 37
P2-only commits outside the baseline main ancestry. Consolidation preserves all
964 distinct historical commits, not 1,881 independent fixes.

## Active versus historical material

- **Active maintained native build:** the two manual `.github/workflows` build
  wrappers still call LiveContainer's immutable maintained implementation. Their
  source graph, packaging code, signing audit, converter and localization checker
  are unchanged. See [Maintained build](MAINTAINED_BUILD.md).
- **Historical patch and diagnostic library:** `scripts/patch_*`, v3 templates,
  rendering harnesses and associated tests retain their old source contracts.
  They are not the application source selected by the current native wrappers.
- **Retained historical evidence:** `migration/` includes exact old pins,
  contracts, captured fixtures and reports. These describe their original
  experiments, not current release acceptance. Original removed harnesses remain
  available at their historical commits. Old admission flags must not be
  interpreted as permission to activate an experiment.
- **Inactive workflow provenance:** definitions introduced by the validation
  histories that are still retained are under `migration/historical/ssa-validation/`,
  outside GitHub Actions' active workflow directory. The original conflicting P0
  and P2 build recipes and the P0 README are preserved there; obsolete experimental
  workflow definitions whose harnesses were removed remain only in Git history. No legacy workflow is
  restored as an automatic or manual production build entrypoint.
- **Research excluded:** the dedicated cellular feasibility branch and its
  `diagnostics/cellular-refresh` / research-plan files remain separate. Some
  historical app fixtures already contain cellular-related symbols; their
  preservation does not enable the separate research experiment.

## Conflict decisions

1. Keep the current v6.0 README status and immutable native build wrapper; archive
   historical alternatives. Correct the README's stale legacy-workflow wording.
2. Preserve P0 constructor, bounds, allocator, composed-host and full-login
   admission dispatch; add P1 dispatch without relabeling P1 evidence as P0.
3. Combine genuine-source localization closure and retained source hashes with
   P2's four-language, persistence and copy-feedback fixture behavior. Project
   construction must reject missing sources, ambiguous anchors and duplicate IDs.
4. Keep the English-only P0 fixture locale only in its non-P2 compilation path;
   it must not override the P2-selected locale.
5. Update repository-contract tests for maintained wrappers. Historical workflow
   assertions inspect the archived definition instead of forcing obsolete build
   logic back into active workflows.

## Verification scope

`ci/validation-history/manifest.json` binds the unchanged maintained files and
archived workflow bytes. Its checker verifies both historical heads are ancestors,
the separate research files are absent, and restored Python sources parse. The
bounded CI runs maintained entry/packaging/localization, local evidence-runner,
repository, P1 admission and conflict-specific rendering tests, including the
source-built converter on macOS.

This is not a full historical simulator campaign, a fresh native app build, or
physical-device proof. Older broad suites may still require exact historical
external source trees and may assert obsolete workflow locations. They must not
be made green by weakening production guards. Preserved snapshot patches and
source captures retain original whitespace where byte identity matters.

Public 6.0 release assets, tags, source pins and packaging admission are unchanged.
No release-publication workflow is part of this consolidation.


## CI directory consolidation (2026-11-05)

The seven former `.ci/` directories now live directly under `ci/`, at the same
nesting depth. The original manifest and reviewed-successor JSON bytes remain
unchanged, as do the historical Git-object and qualified-tree assertions.
`ci/validation-history/relocation.py` enumerates each permitted source/destination
pair and each affected caller. It checks exact baseline objects from
`6f20f702daad7aa8f870a9a3fe0d0b321dc41741` against their original hashes before
applying only the enumerated literal path replacements. Other files still require
the original exact hashes. Missing destinations, symlinks, edited payloads and
revived old paths are rejected. Historical control inventories, pinned content-API
paths and other repositories' `.ci/` references are deliberately unchanged.

The six completed release-promotion, release-note amendment, branch-deletion,
classification, semantic-inventory and forwardport workflows are
retained byte-for-byte under `.github/workflow-archive/maintenance-v6.0.0/`.
They are explicitly excluded from the active workflow inventory so the directory
move cannot retrigger their historical write operations. Their receipts and tests
remain available; archiving them does not authorize any new release or branch
mutation. No production build pins or release assets change.

The semantic-inventory suite also hashes its own source and test file, so these
remain byte-identical rather than rewriting their archived-workflow references.
Run `python3 -B ci/validation-history/historical_semantic.py` to exercise all 21
retained tests. This runner checks each current local input against the exact
baseline blob before reconstructing only the six required files in an isolated
temporary historical layout. It runs the known unittest suite, not the collector
or an archived workflow, and propagates test failures. The same suite runs through
`test_source_contract.py`; negative tests reject missing or altered fixture inputs.
Running the frozen semantic suite directly from its relocated directory is not
supported because its workflow-location assertions intentionally describe the
original layout.
