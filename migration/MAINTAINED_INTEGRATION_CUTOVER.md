# Maintained-source combined build candidate

This is a local integration candidate rooted at
`141776ba6ba38fc04a5e77f68b0cfc4e6c8842ee`. It changes build orchestration,
validation and evidence only. It has not been published or dispatched. No runtime
source, template, authfix, observer/P1 change, recovery policy, setup ordering,
or cleanup implementation is changed.

## Exact inputs and activation blockers

`migration/maintained-sources.json` is the authoritative seven-owner pin map.
`SideSign.commit` and `SideStore.commit` are intentionally null. Every command,
including environment export and acquisition, rejects either pending pin before
network acquisition or dependency execution. Checkpoint SHAs are provenance;
they are never substituted for missing production commits.

The reviewed SideSign source checkpoint is now
`ed30d3989ea0f80bcb91466d6d5ca043f4366df0` (tree
`d5d34cb72538f4070832b7150b69bacaf248dcdc`), whose parent is the preserved
`aaa4375a59075a7b0a446cf4c2dc8193c247a875` checkpoint. This published correction
adds one Foundation import in the sole Swift test target plus its exact parity
exception and mutation tests. Runtime/library sources, package files and licenses
remain unchanged; the reviewed 88-file / 22-edge contract check passes. The source
gate identifies this lineage as `exact_frozen_runtime_with_test_import_pass` and
reports the exact one-row test delta. Future production dependency changes are
compared against this corrected test checkpoint. This does not select a final
production SideSign commit: both pending production commit fields remain null,
and the other six owner checkpoints are unchanged.

Before activation, the integration owner must provide and review:

1. Final published SideSign commit with the real
   `https://github.com/NRG-Wardog/AnisetteKit.git` revision dependency and genuine
   native resolver-produced lock metadata. Its production dependency proof must
   report `exact_dependency_transition_pass` and `production_ready: true`.
2. Final published SideStore commit with child gitlinks to that exact SideSign
   commit and minimuxer `efbcab05d7d636aa37c6bf6c7f364d122c5610f6`, matching owner
   URLs, native-resolved lock metadata and the same successful production proof.
3. The exact final LiveContainer native-test metadata checkpoint if it differs
   from the recorded `47db163356dc419d2b6a894fdc5dbeb9d011dc69`, with independent
   evidence that its product bytes remain equal to the frozen baseline. Update
   its commit and reviewed checkpoint together; do not permit runtime diffs.
4. Complete native prerequisite results for the exact final owner graph, followed
   by the strict combined macOS workflow, IPA verification and uploaded-artifact
   re-verification. An isolated graph using local test overlays does not satisfy
   the real production dependency-resolver prerequisite.

Do not populate the two pending commits with local preparation tips, a branch,
a tag, an upstream checkpoint, or an unverified resolver lock. The code rejects
floating refs and only acquires from the seven NRG-Wardog repositories. No local
Anisette package override is used in this workflow.

## Checks replacing runtime rewriting

The build acquires LiveContainer, SideStore, AnisetteKit, idevice and jktcp by
exact commit. SideSign and minimuxer are acquired through SideStore's actual
recursive gitlinks, never by an independent replacement checkout. The existing
litehook transport mirror override remains bounded to its pinned gitlink.

Before compiling, `scripts/maintained_sources.py` checks full owner histories, exact commits, recursive
gitlinks, origin repositories, working bytes/modes against every committed blob,
index drift and untracked/ignored inputs. Proof commands disable lazy object fetches.
Git is bound to each canonical work-tree and actual gitfile, so a legitimate
symlinked system prefix is accepted and core.worktree cannot redirect checks. This does not trust index flags such
as `assume-unchanged`. It rejects runtime changes after the approved source
checkpoints, permits only the explicitly enumerated SideSign/SideStore production
metadata transition, runs their read-only production proofs and invokes the
unchanged 88-source contract registry with the approved digest
`8e7eba95b8bc69037ffed8931478cefd46984b458f767c2a067547e3cd60b467`.

After real Xcode resolution, the gate checks workspace-state dependencies,
including every locked remote checkout and all four local package identities.
The effective AnisetteKit must be the exact maintained remote checkout.
Direct owner clones retain strict HTTPS origin checks. Effective resolver
checkouts may retain SwiftPM's normal local origin only through one verified
chain: canonical `SourcePackages/checkouts/<package>` to a bare mirror directly
under that same `SourcePackages/repositories/`, terminating at the exact approved
HTTPS URL. Mirror full history, commit/tree identity, and any shared object-store
alternate are checked. Escaped/symlinked mirrors, wrong upstream URLs, shallow
mirrors, and additional object-store hops fail. Validation never rewrites origin.
This follows SwiftPM's noneditable `clone --shared --no-checkout` implementation:
https://github.com/swiftlang/swift-package-manager/blob/swift-6.2-RELEASE/Sources/SourceControl/GitRepository.swift#L212-L252
Local real-mirror tests include the exact maintained Anisette commit (31 files
and five native source bindings). Hosted Xcode's actual origin is still unobserved;
other cache layouts remain capture-and-review blockers. Every
unrelated SwiftPM pin remains equal to the source checkpoint. The existing Rust
sibling dependency and minimuxer LocalBinary package wiring are retained.

Only declared untracked build outputs are admitted after compilation: idevice
and jktcp target directories, the three generated idevice headers, the two staged XCFramework locations, and the frozen SideBackup Makefile outputs
`build/sidebackup.xcarchive/` and `build/SideBackup.ipa`. Other files under
SideStore `build/` and `.swiftpm/` remain rejected. No tracked-source exception is made. The staged
arm64 iOS static archive must hash-identically match the Rust output; its header
must match all three generated/copied headers, and its module map must match the
committed Swift include module map. Before/after native evidence must be equal.
These narrow attribution checks reuse the reviewed native-assembly checks; they
do not replace native compilation or prove a physical-device outcome.

The resolved five-file Anisette source evidence retains its historical
transformation schema and upstream-origin revision for compatibility. A separate
`maintained_runtime_sources` field in candidate provenance records all actual
owner commits. The candidate verifier requires the independently supplied pin
map and binds all seven actual dependency revisions to that map; the explicit `ANISETTE_REF` and effective
Anisette native checkout is verified read-only when evidence is collected.

## Retained acceptance and historical tests

The workflow retains the complete required repository test runner and empty
skip allowlist, Swift parser checks, serial full layout regression, Rust tests,
iOS builds, generated FFI/symbol checks, packaging behavior, entitlements and
signing verification, source/dSYM/UUID evidence, archive checks and confirmed
artifact upload plus download/re-verification. `cargo fmt` becomes `--check`;
it cannot rewrite maintained Rust inputs.

Historical runtime patchers and their templates remain unchanged. Their tests
receive independent pristine OLD fixture checkouts which are never product
inputs. The exact old workflow is preserved as
`migration/historical/livecontainer-build-141776ba.yml`. Only historical fixture
pin and retired patch-order assertions read it; active build/layout/artifact,
required-skip and new no-rewrite assertions inspect the current workflow.
Its byte equality to the baseline Git object is tested. The standalone workflow
and unrelated historical workflows are unchanged.

The idevice archive symbol gate reuses `ci/native/verify_archive_symbols.py`
byte-for-byte from the successful native6 host
`9f7a566a17c59cac4121fb59b59d55dbfa574efb` (Git blob
`edfdc808264cf8abb7881535baa820c9714c79b0`), now at
`scripts/verify_archive_symbols.py`. It requires the reviewed Rust 1.98.1 ARM64
host and derives the LLVM reader from that producing compiler's official
component/sysroot. Nonzero reader exit, version mismatch, undefined/local/similar
symbols, or missing exact definitions fail before XCFramework staging. Commands,
stdout/stderr, reader/archive hashes and PASS/FAIL status are retained under
`artifacts/dependencies/idevice-symbols`, including on failed runs. No Rust source,
archive, compiler selection or product dependency was changed for this reader fix.

The only remaining `patch_*.py` invocation in the active combined release path
is `patch_combined_refresh_contract.py --verify-ipa`, its read-only artifact
validation mode. Imports that calculate historical expected evidence remain.

## Local verification

Local repository results are total 1,196 / passed 1,015 / skipped 181 / failed 0.
The final focused Anisette artifact suite is 14 / 14 / 0 / 0. The builder-history
correction passes all 37 gate tests normally and under Python `-O`; the exact
historical fixture assertion is retained. A depth-one clone reproduced the
missing-baseline failure, and fetching full history in that same clone passed.
The release builder checkout now explicitly sets `fetch-depth: 0`.

Local evidence is under `migration/cutover-evidence/`. This Linux executor has
no Swift compiler, Xcode, Apple SDK or simulator. Local unit results must not be
reported as the strict macOS required suite or a combined IPA build.

Commands:

- `python3 -B -m unittest discover -s migration/tests -v`
- `python3 -B -m unittest discover -s migration/contracts/tests -v`
- `python3 -B -m unittest discover -s tests` with pristine historical source
  paths set through the workflow's five test-source environment variables
- `python3 scripts/maintained_sources.py env` must currently fail on a pending
  production commit, before any owner acquisition
- After final inputs and native prerequisites: the unchanged strict runner
  `python3 builder/scripts/run_required_tests.py --start-directory builder/tests
  --allowlist builder/scripts/required_test_skip_allowlist.json` on macOS,
  followed by every workflow build and artifact gate

Runtime source parity is not device authentication acceptance. The nativeOTP /
-45061 incident and all frozen known defects remain unresolved by this cutover.
