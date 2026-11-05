# Read-only branch semantic evidence inventory

This collector prepares evidence for independent review of the exact 97 nondefault branch heads retained in the verified October 25 receipt. It does not merge, delete, push, publish a release, change permissions, or execute fetched source. Public version 6.0 and its published assets remain untouched. The SAR PR 49 research branch and AnisetteKit runtime-source migration branch carry explicit preservation holds.

## Trust and launch contract

- The workflow runs only in NRG-Wardog/sidestore-auto-refresh, on main, after a push changing the exact .ci/branch-semantic-review/inventory.json path. There is no dispatch or pull-request trigger and no mutation job.
- The workflow pins the manifest SHA-256. The manifest pins the collector and tests, the exact source receipt, all eight default branches/heads, all 97 branch names/heads, explicit limits, and six exact control paths.
- The SAR default may advance from the receipt only through at most 100 linear control-only commits touching the six literal allowed paths. Every intervening commit is checked, including changes later reverted. Semantic comparisons still use the original receipt default, not the workflow successor, to avoid control-file contamination. Every other default must still have its exact receipt hash.
- Public HTTPS Git only. No token, askpass, credential helper, configured remote, redirects, SSH, user Git config, hooks, checkout, submodule recursion, LFS download, repository scripts, or fetched executable is used by the collector.
- Network Git calls are sequential, with at least 3.1 seconds after the preceding call finishes. This is also applied before the first collector call. The only network operations are exact-ref ls-remote and fetch.
- Each repository gets three exact-ref observations: before fetch, immediately after fetch, and after analysis. The symbolic remote HEAD, default hash, and every reviewed branch hash must match. Missing/moved refs fail closed. New branches are not enumerated or included.
- Fetches preserve complete source ancestry in fresh bare repositories. No shallow or partial clone, tag fetch, ref rewriting, history pruning, or history mutation is performed. Source Git history stays in the job work directory for the job lifetime. Evidence is uploaded separately; raw Git databases are not uploaded.

## Evidence

inventory-report.json contains the per-branch machine summaries and collection status. Each numbered branch directory includes summary.json, git-cherry.json, default-to-branch.patch, and one merge-base-to-branch patch for each available merge base. Prefixes are explicit if a byte cap is reached; never apply a truncated patch.

commit-evidence.json contains bounded per-commit parent, tree, subject, timestamp, patch-ID, and actual patch files. Non-merge commits use stable Git patch IDs. Merge commits have separately labelled diffs against each parent and no fabricated merge patch ID. git cherry provides each unique non-merge commit's default-exclusive patch-equivalence result. patch-id-index.json links equal collected patch IDs across reviewed branches; this is not an exhaustive default-history negative test.

default-tree-history.json maps bounded reachable default commits to exact Git tree IDs. Each branch reports equality with the current comparison default, matching reachable default-history trees, matching reviewed branch-tip trees, and zero-net changes relative to each merge base. These are content observations, not unproven semantic supersession or deletion decisions. A reverted patch, dependent branch, open PR, merge-only change, or retained workflow can still need preservation.

ancestry-cover.json uses git merge-base --independent and cached ancestry checks to identify a minimal set of distinct maximal reviewed branch heads retaining all reviewed branch history. Equal-head aliases are listed. This is structural coverage, not a merge/delete decision or tag creation.

artifact-index.json gives patch sizes, SHA-256 values, completeness flags, truncation/timeouts, and explicit omissions. network-operations.json records public read-only operations and timing without credentials. The source receipt protection flags are historical observations, not fresh branch protection or PR checks. PR state, dependency reachability outside these exact refs, CI state, release tags, runtime behavior, and merge suitability must be reviewed separately.

## Bounds and incomplete runs

Limits are fixed and hashed in the manifest: 500 unique commits per branch, 50,000 default-history tree entries, eight merge bases, eight merge parents, 4 MiB per branch diff, 2 MiB per commit/parent patch, 384 MiB total patch artifacts, 8 MiB generic capture output, 3 GiB per fetched bare repository, 180 seconds per local command, 900 seconds per remote command, and 7,200 seconds total collector time. Workflow time is bounded separately.

If a bound is exceeded, a command fails, or a ref moves, evidence is explicitly incomplete or blocked. Partial artifacts are retained and uploaded with an always condition. The collector exits nonzero for incomplete branch evidence or a blocked run. No absence-of-difference or deletion conclusion can be inferred from omitted evidence. Default-history tree search incompleteness is separately labelled even when other branch evidence is complete.

## Offline validation

Run python3 -m unittest discover -s .ci/branch-semantic-review -p test_inventory.py -v from the repository root. Tests create disposable local Git fixtures and mock remote calls; they do not contact GitHub. Fixtures cover patch-equivalent cherry-picks, reverted unchanged trees, duplicate branch patch IDs, merge parents, empty commits, unrelated histories, output/commit/history bounds, exact-control-path enforcement, reverted disallowed changes, missing/moved refs, sterile credentials, and request spacing.

To validate only the pinned inputs, set REVIEWED_MANIFEST_SHA256 to the exact workflow value and run python3 .ci/branch-semantic-review/inventory.py --verify-manifest-only. This performs no network operation.

The locally prepared package is not evidence that the 97 remote branches have been semantically reviewed. Only a successful exact-commit Actions run and independent inspection of its actual diffs can supply that evidence. No branch should be deleted merely because its name looks experimental or its patch ID matches another commit.
