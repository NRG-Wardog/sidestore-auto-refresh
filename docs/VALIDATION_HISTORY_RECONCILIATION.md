# SSA validation history consolidation

The retained P0 and P2 validation histories are consolidated through real merge
commits. Their source, regression fixtures and provenance are retained; this is
not a new application release and does not reactivate an old release builder.

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
- **Historical evidence:** `migration/` and `ci/p2/` include exact old pins,
  contracts, captured fixtures and reports. These describe their original
  experiments, not current release acceptance. Old admission flags must not be
  interpreted as permission to activate an experiment.
- **Inactive workflow provenance:** definitions introduced by the validation
  histories are archived under `migration/historical/ssa-validation/`, outside
  GitHub Actions' active workflow directory. The original conflicting P0 and P2
  build recipes and the P0 README are preserved there. No legacy workflow is
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

`.ci/validation-history/manifest.json` binds the unchanged maintained files and
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
