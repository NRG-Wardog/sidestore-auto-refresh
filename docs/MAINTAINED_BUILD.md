# Maintained 6.0 build and reviewed packaging

## Native applications

The two build entries now call one immutable maintained native workflow instead of rewriting old upstream application sources. Both compile the same reviewed 6.0 graph and retain unsigned LiveContainer and SideStore application archives plus exact-source evidence. The SideStore entry also compiles the host because this graph's existing combined integration gates cover both applications.

Run either entry on main:
- LiveContainer and embedded SideStore: `.github/workflows/livecontainer-build.yml`.
- SideStore shared graph: `.github/workflows/build-current.yml`.

The workflow implementation is pinned to LiveContainer commit `c7111b580254190a0c037037036aaed49bfce8c6`. It checks out the immutable native harness `177ac31e90499231191b25f761014c17d9f2ba7b`, which contains the exact device-tested 6.0 source graph. Its 24 original test/build steps remain unchanged. Invocation evidence identifies the SAR caller separately from the immutable harness. Later source fixes on repository defaults are not silently included in this fixed release graph.

## Installable IPA

Unsigned application archives are not an installable combined IPA. Packaging is an explicit separate admission step, not an automatic public release.

The artifact-only [Package reviewed 6.0 inputs action](https://github.com/NRG-Wardog/LiveContainer/actions/workflows/package-diagnostic.yml) on LiveContainer main preserves the already reviewed native run `40102842840`, attempt 1, artifact hashes, source-tree checks, App Intents, signing, resource and provenance checks. It uses the packaging implementation now retained on SAR main at `3523c26dbdea4c49d2d113845af0721c9772a300`, byte-identical to the implementation used by public 6.0. It produces an experimental IPA artifact, not a GitHub release asset. GitHub repository visibility controls workflow-artifact access; this document does not promise private storage.

That packaging action intentionally does not select the latest run or accept a newly compiled archive without review. To package a newer native run, first verify its exact repository, workflow, commit, tree, attempt, source graph, unsigned archive digests and admission receipts; then update the finite bindings in the packaging workflow. Never substitute a generic green run. This preserves the existing reviewed handoff rather than weakening it into an unchecked automatic download.

The old reviewed native artifacts are subject to GitHub retention. If they have expired, the fixed replay action fails instead of substituting other inputs. Run the maintained native entry and review/rebind its new evidence using the same procedure.

## Release and legacy behavior

Public [6.0.0](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.0) remains unchanged. These entries do not publish releases, create tags, use Apple credentials, or alter device settings. The inherited CydiaSubstrate signature limitation remains as documented in the release.

Historical patch scripts are retained for provenance and their existing tests, but the current manual build entries no longer invoke them. The source-built converter, packaging code and focused regressions were recovered exactly from SAR `3edd9fa523a223bcd2898d5c960c5da7ff335be8`; no experimental auth or research workflow is activated.
