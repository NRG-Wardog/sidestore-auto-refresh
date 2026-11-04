# Custom integration boundaries

This is the maintenance architecture for [issue #52](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/52).
Custom runtime source is maintained in its owning repository, not regenerated
from historical patch templates. Pure policy modules, narrow call-site adapters,
versioned wire/FFI contracts and fail-closed packaging rules form the integration
boundaries. This change adds the maintained inventory, reusable change review,
continuous checks and representative integration evidence needed to use those
boundaries consistently.

It does not require splitting every large application file. Large host/service
files still contain side-effect adapters; their documented call sites and native
regressions remain important. The objective is smaller, reviewable integration
work, not conflict-free updates or a claim that every custom feature is proven
on a device.

## Authoritative maintenance inventory

[`CUSTOM_INTEGRATION_CONTRACTS.json`](CUSTOM_INTEGRATION_CONTRACTS.json) records
17 feature boundaries across the builder and all seven maintained runtime owners.
Each entry names actual modules, integration call sites or build inputs, the
interface and rationale, regression commands, and the evidence limitations.
Paths are relative to their declared owner, not necessarily this repository.

| Boundary | Owned policy/module | Required integration review |
| --- | --- | --- |
| Packaging | Builder `packaging_adapters.py` | Eight exact upstream shell anchors and `package_livecontainer_combined.adapt` |
| Source catalog and App ID expiry | LiveContainer `V3SourceAppPresentation` | Catalog installer, Developer Services UI and SideStore portal-date bridge |
| Authentication/provider sequence | SideStore/LiveContainer provider policy; SideSign authentication policy | Authentication session, prompt adapter and actual request boundary |
| Owned on-device anisette | AnisetteKit attempt/lifecycle modules | Swift client and native entry points; remote fallback adapter |
| Extension signing | SideSign `ExtensionSigningPolicy` | Explicit signer overload and SideStore signing operation |
| Preserved refresh | SideStore profile decoder | Refresh, cache and install-success adapters |
| Refresh results/XPC | SideStoreClient and service selectors | Versioned backend commands and run-correlated results |
| Diagnostics/recovery | Bounded native diagnostics and typed errors | Host/service producers, presentation and recovery dispatch |
| Transport readiness | minimuxer policy and gateway API | Device provider, idevice gateway and backend readiness |
| FFI ownership | idevice Rust/C ABI | C++ and Swift consumers; paired generated headers |
| TCP reliability | jktcp adapter and handle | idevice dependency and TCP package interface |
| Guest input/geometry | LiveContainer safe-area policy | Scene editing cleanup and activation |
| Shared storage/startup | App Group identity/selection and shared storage | Host storage bridge, embedded launch and service initialization |
| Preserving database recovery | SideStore recovery engine and SQLite checkpoint shim | Retained source ownership, staged Core Data migration, paired Apps/database selection, exclusive publication and reset refusal |
| Localization | Language settings and lookup modules | Live presentation and compiled catalog |
| Build membership/dependencies | Maintained-source and candidate-graph validators | Xcode/SwiftPM/Cargo inclusion and exact owner pins |
| Maintenance tooling | Generic inventory/change planner | Registry, CI and regression/rehearsal evidence |

The JSON registry is revision-independent: no frozen commit hashes or source
slices are embedded in its interfaces. Current candidate revisions remain in the
existing candidate graph. Historical migration witnesses are neither rewritten
nor used to pretend that current source is unchanged.

The inventory is a finite review map, not a wildcard allowance for every path in
these repositories. A new or unrelated changed file is **unclassified** until it
is reviewed. Module changes, caller changes, target-membership changes, test
changes and unknown files all remain visible.

### Borrowed-provider ownership integration

The FFI boundary names each of the twelve C++ service wrappers, its public
header and its Rust FFI module, plus the shared provider tests. It also names
the source-owned deterministic fixture inputs, generated-header checker,
builder native runner and receipt tests. AFC has two separate connect paths,
so the twelve wrappers cover thirteen borrowed-provider methods. Contract
README changes are mapped as documentation, not executable evidence.

`tests/fixtures/issue53-ownership-delta.json` retains the exact reviewed 44-file
`e4cf9cb` to `72c0044` ownership delta and blob IDs solely as a regression input.
The inventory remains revision-independent; this fixture does not select a
production pin or replace candidate-graph verification. Tests require all 44
paths to map explicitly to the ownership boundary, remove each mapping in turn
to prove it becomes unclassified, and keep an unknown neighboring service in
manual review. Native ownership qualification still requires actual Rust tests,
fresh generated headers and both exact execution markers.

When composing this maintenance work with the ownership update, publish one
combined builder tree containing the updated registry, candidate graph and
native runner/receipt changes. Run the dedicated inventory workflow and full
native qualification on that same commit. A prior registry run against the old
owner graph does not qualify the new composition.

### Preserving database recovery integration

The database recovery boundary explicitly names the production Swift engine,
its SQLite C shim, all four startup/storage/reset callers and Xcode target
membership. It names every original model resource compiled by the native
Core Data fixture, the isolated qualification workflow, documentation, exact
native fixture inputs and portable runner/source controls. Both native runners
use the explicit target-settings helper to select the actual SideStore Release
Swift language mode; unknown or inherited modes must not silently fall back. These are finite
path entries, not an allowance for arbitrary files under a recovery directory.

Original databases and Apps remain retained. The selected recovery generation
owns source SQLite write reservations for its entire lifetime, and only an owned
staging copy may undergo schema migration. The complete generation is published
exclusively; divergent originals and unsafe layouts remain blocked. This does
not promise a merge of divergent databases, exclusion of an independently
running legacy filesystem-only Apps writer, or power-loss durability.

The SQLite engine suite and Core Data suite are separate evidence levels. The
first compiles the exact engine and C shim with a synthetic validation callback.
The second compiles the full production PersistentContainer and original model
resources, then exercises v17.6-to-v17.7 DELETE and committed-WAL stores using
generic managed-object classes. It does not execute account/keychain code or
establish an affected user's physical-device upgrade result. Set BUILDER_ROOT
for strict portable controls and distinct fresh RECOVERY_ENGINE_OUTPUT and
RECOVERY_COREDATA_OUTPUT paths outside the source checkout for native runs.
Set SIDESTORE_ROOT to the matching acquired SideStore checkout when running
the builder receipt controls: its --source gate compares the real source-owned
case and input inventory, followed by the strict no-skips runner. That source
inspection is not native execution. Unsupported hosts must fail, never turn a
missing native run into a pass.

`tests/fixtures/issue56-recovery-delta.json` retains the exact reviewed 20-file
`4f102127` to `dd7c095d` delta, modes and blob IDs only as a maintenance
regression input. It does not select the production candidate or claim native
success. Tests require every named change to be classified, make each removed
mapping unclassified, and keep unknown neighboring engine, fixture and model
paths in manual review. The earlier #53 lifetime and published-provenance
regressions remain unchanged.

The cache fixture depends on the production InstalledApp Apps getter. Its exact
source generator, native Cases.swift input and portable controls therefore map
to both preserving recovery and preserved refresh, as does the caller itself.
The generator retains the historical whole-file pin through a single reviewed
inverse of the recovered-root expression and emits the exact getter with an
inert optional-URL MaintenanceManager boundary. Native cases exercise both the
nil fallback and selected-root branch; they do not run the recovery engine.
Generation alone is not a compile or runtime pass. Native execution uses the
existing setup verifier and its same-graph projected contract: set BUILDER_ROOT,
SOURCES_ROOT, matching SWIFTC, fresh SETUP_RECOVERY_OUTPUT outside the source
checkouts and SETUP_RECOVERY_CONTRACT. Its source, compile, case and retained
receipt checks remain mandatory, including the unchanged original case IDs.

The native builder boundary names its recovery producer/verifier, adversarial
receipt tests, acquisition binding, mandatory native workflow step and the
record/package admission call sites. Their source-owned receipts must bind the
actual SQLite and Core Data executions and effective Release Swift build-setting
proof to the same workflow commit, run and attempt. Receipt-unit-test success is not a native pass. Later standalone
package-pipeline additions require their own explicit boundary review before
being added; nonexistent future paths are never silently inventoried.

The reviewed upstream successor keeps its predecessor descriptor frozen and
explicitly maps the replaced no-relocation source assertion to the new recovery
assertion. Its exact descriptor, projector, routes, controls and five workflow
call sites are inventoried. The projection must disclose that source-case change
rather than claiming all original cases are unchanged. Inventory push/PR triggers
name these builder admission paths explicitly, including the existing #53 runner
and receipt entrypoints.

The final builder must compose this registry with its exact reviewed SideStore
candidate graph and mandatory same-run recovery receipt admission. Rerun the
dedicated inventory workflow and full native/package qualification on that same
combined commit; prior #52/#53 or isolated #56 evidence cannot qualify a different
source composition.

## Reusable upstream change review

`python3 -B scripts/integration_contracts.py check-registry` validates the closed
registry schema. To verify the actual documented files, use `validate` and pass
one `--owner-root OWNER=/checkout` for **every** registered owner. Missing owners,
files, linked paths, duplicate definitions, unsafe paths or empty contracts fail.
Inventory success means source paths were checked and hashed, not that the
listed commands ran.

Before integrating an upstream update, inspect immutable local revisions:

```sh
python3 -B scripts/integration_contracts.py plan \
  --owner LiveContainer --repository /path/to/LiveContainer \
  --base FULL_OLD_COMMIT_SHA --candidate FULL_NEW_COMMIT_SHA
```

The planner reads local Git objects without fetching, executing source, changing
files or invoking external diff/textconv helpers. It disables replacement refs
and inherited Git-routing overrides. A dirty working tree cannot change its
immutable-commit comparison.

The report lists:

- Every changed exact path, its affected features and its module/caller/test role.
- Applicable regression commands as **instructions**, never executed commands.
- Unclassified paths, removed paths, structural type changes and gitlink changes.
- Both sides of renames, deliberately represented as delete/add.
- Exact old/new commit and tree identities, with no native/device success claim.

Exit 0 means `IMPACT_CLASSIFIED_NOT_TESTED`, not behavioral compatibility. Exit 3
means `MANUAL_REVIEW_REQUIRED`; unknown files, deleted hooks and gitlink
changes require manual review. Exit 2 means invalid input or a failed check.
Reviewing an unclassified upstream documentation change does not require moving
it into a runtime module. Review it explicitly; do not add broad path exclusions
just to turn the report green.

For mapped changes, run the listed commands from their owner checkouts and the
existing full source/native/ABI/artifact gates appropriate to the change. Some
native commands require their documented SDK/toolchain or verified input archive.
Commands marked `source-contract` are not executable behavior evidence.

## Named packaging adapter and minimal patch surface

`package_livecontainer_combined.adapt` delegates to
`scripts/packaging_adapters.py`. Its eight immutable `IntegrationRule` records are
the complete upstream-facing packaging patch surface.
[`PACKAGING_PATCH_SURFACE.json`](PACKAGING_PATCH_SURFACE.json) records each rule's
stable name, owner, rationale, exact anchor/replacement hashes and line counts.
Tests require exact equality between the inventory and production rules.

`apply_rules` is a generic, pure text engine with no network or process execution.
It validates all anchors against the **original input**, including overlapping
duplicate occurrences, and rejects overlapping rule spans. It then replaces
only those original spans. One rule can neither manufacture a missing anchor
for another nor hide an original duplicate; inserted custom text is never
reinterpreted as upstream source. Reordering disjoint rules has no effect.

Existing production outputs remain byte-identical to the retained pre-refactor
adapter witness at builder commit `eb1b05b94dee5170b5720c665c806be84e442f96`.
The current rule inventory and replacement bytes have not changed.

## Representative real-upstream integration

The retained real scripts and provenance are in
`tests/fixtures/upstream-packaging/`:

1. `NRG-Wardog/LiveContainer@433dd73edd78401bc5d38eef87d51414bb30f6f4`
   exercises the production profile and its pre-refactor output hash.
2. `LiveContainer/LiveContainer@2d092098cae6ef6d98eefe3943927f81d925e527`
   changes a service download URL and adds a widget-framework copy. The strict
   production adapter rejects its changed anchor. An explicit, source-hash-bound
   compatibility profile adjusts **only that anchor**, reuses every custom
   replacement unchanged, and preserves the unrelated upstream copy step.

The fixture manifest retains source URLs, exact source/output hashes, license and
profile. Tests reject wrong revisions, modified source, unknown rule names,
replacement injection, missing/duplicated/overlapping anchors and ambiguous
profiles. Synthetic unrelated adapters exercise reuse without packaging-specific
knowledge. The benign upstream drift tests preserve unrelated upstream steps.

A locally available historical revision can be rehearsed without shell execution:

```sh
python3 -B scripts/rehearse_packaging_integration.py \
  --repository /path/to/LiveContainer \
  --commit 2d092098cae6ef6d98eefe3943927f81d925e527 \
  --anchor-profile tests/fixtures/upstream-packaging/2d092098-anchor-profile.json \
  --output /path/to/fresh-rehearsal.json
```

This is a real-revision **packaging adapter integration**, not a full application
merge, native build, IPA signature check, device test or approval to ship a
historical upstream script. In particular, the added widget copy needs review
before any actual packaging execution. Profiles are never chosen automatically.

## Continuous checks and acceptance evidence

`.github/workflows/custom-integration-contract.yml` runs on relevant push/PR
changes and manual dispatch. It runs the generic planner controls, adapter tests
and integration rehearsals through the strict no-unexpected-skips runner. It then
acquires the exact seven owner revisions from the current reviewed candidate
graph as data, checks all inventory paths against their committed blobs, and
retains an identity/hash receipt. It does **not** execute fetched owner code or
claim their behavioral suites ran. It has read-only repository permissions.

The frozen `maintained-packaging-contract.yml` release witness is unchanged. The
new candidate-aware gate complements, rather than replaces, existing required
owner, native, compatibility, regression and artifact checks.

| Issue #52 acceptance | Concrete evidence |
| --- | --- |
| Document and independently test custom seams where practical | Seventeen owned feature contracts, concrete hook/build/test paths and practical regression commands; inventory checks against all current owner inputs |
| Complete a representative upstream integration without rewriting custom logic | Two retained real script revisions, one explicit anchor-only compatibility profile, identical custom replacements and preserved unrelated upstream change |
| Make conflicts and behavior changes visible | Exact-path impact reports; unknown/deleted/structural/gitlink review gates; missing/duplicate/overlap adapter negatives; existing owner behavioral/native tests remain required |
| Keep the approach generic and reusable | Revision-independent registry, language-independent exact-path planner, unrelated-owner fixture, generic original-span rule engine and immutable revision CLI |

Before closing #52, retain a passing run of the dedicated workflow for the exact
published implementation commit. That maintenance acceptance does not close
other device-dependent feature issues or establish that signing, authentication,
refresh or transport works on a particular physical device.
