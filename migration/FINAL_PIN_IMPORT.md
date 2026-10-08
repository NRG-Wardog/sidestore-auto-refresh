# Final production pin import and parity IPA acceptance

Prepared after integration code `84598d2d98306eac6e9c95868b52ce18f23c8108`.
This is a procedure, not authorization to activate pending inputs. The integration
pin map still has null SideSign/SideStore production commits. No runtime change,
new validation framework, separate validation phase, or authfix is part of it.

## Receipt and metadata prerequisites

The approved Phase 2 inputs are SideSign S1
`5ce52d12f1846e1a08fad30ed27c4cbadd176529` (tree
`352e92723c34a47bcfef5e0a984ba9828bade33b`) and SideStore T0
`f6e9e0ed6c3f4d02e99a0dcff0660faa0e5372b8` (tree
`2f33f7caed7f6ef13522421446ade4d79d8f901e`). These are Phase 2 inputs,
not the future S2/T1 final commits. Verified Phase 2 run
`37702648560`, host `5fcff4214fc459b30b37843aaa2cf075232f52f2`, completed
successfully. Its closed artifact is `11519176173`, ZIP SHA-256
`d8dd30c49648a5e2706edc185e9c1ce15c584075ce41e85cbde80ce4934cc75b`.
Final metadata-only S2/T1 publication and the full IPA build remain pending. Phase 1 observed absent originHash with
unchanged complete lock bytes; preserve actual resolver output and never require
or invent an originHash the resolver did not produce.

1. Verify the actual Phase 2 host/run/artifact, exact tested commits/trees,
   complete owner and compiler-source proofs, native tests and Xcode lock bytes.
   Retain the native tested identity independently of later metadata commits.
2. Review/publish SideSign S2's existing-proof-path-only transition from S1.
   Review/publish SideStore T1's observed lock and SideSign-child transition from
   T0, with unchanged minimuxer and all other build/runtime inputs. Use verified
   published SHAs, never API-equivalent local commit identities.
3. Run each final owner's existing `.ci/production-dependencies.py` proof.
   Both must report `exact_dependency_transition_pass`, `production_ready: true`
   and `readiness_scope: eligible_for_gated_full_build`. True means eligible for
   the mandatory final IPA build; it must not claim S2/T1 already compiled.
4. The existing integration `verify_all` owner-proof gate now enforces the
   coordinated receipt output schema. It requires the same approved Phase 2
   `native_run_url`, validation-host commit and artifact SHA-256; exact native
   tested commit/tree identities; receipt digest presence; and the complete
   tested-child maps. SideStore's
   `native_tested_children["Dependencies/SideSign"]` must equal SideSign's
   `native_tested_commit`. It also binds each proof to its final pinned current
   owner commit. The authoritative map's `native_validation` records the closed
   approval. Owner checkers retain responsibility for exact receipt hashes and
   complete metadata-only transition proofs. Execute this linked gate on the
   actual final published owner checkouts before activation; unit proof fixtures
   alone do not establish final owner eligibility.

## Two-field import

In `migration/maintained-sources.json`, replace only:

- `owners.SideSign.commit`: verified published S2 SHA
- `owners.SideStore.commit`: verified published T1 SHA

Preserve all seven `source_checkpoint` values: they already match native6's
exact source manifest. Preserve the approved contract registry digest and all
other owner pins, paths and URLs. Verify T1's actual child gitlinks equal S2 and
minimuxer `efbcab05d7d636aa37c6bf6c7f364d122c5610f6`. Keep the Anisette remote
URL and revision, genuine lock metadata, and every unrelated SwiftPM pin.

Review the exact map diff and owner proof results. Run the existing map/gate,
artifact-provenance and affected repository tests; `maintained_sources.py env`
then exports the seven exact revisions. Do not use `--after-build` to bypass
prebuild owner readiness proofs. Publish the reviewed integration commit only
through the separately authorized integration action and verify its branch SHA.

## Exact dispatch and required closure

Repository: `NRG-Wardog/sidestore-auto-refresh`.
Workflow: `livecontainer-build.yml`.
Ref: `fix/v3.1.0-audit` at the verified final integration commit.
Inputs: `{"mode":"release"}`.

An audit-branch push selects preflight and produces no IPA. The workflow's
same-ref concurrency group cancels an older run, so avoid overlapping accepted
runs. The release job uses macos-26, Xcode 26.4, a full-history builder checkout
and a 210-minute envelope. The reused native6 archive reader requires the
reviewed Rust 1.98.1 ARM64 toolchain and matching LLVM version; changed tools
fail closed rather than accepting partial symbol output.

The one mandatory final IPA workflow supplies exact-final-ref frozen resolution,
source/contracts/native compilation, layout, packaging and artifact closure.
Do not add another validation phase or create another owner receipt commit
solely to record this final build.

Acceptance requires all of the existing gates, including:

- Strict repository runner with unchanged empty skip allowlist, owner input and
  contract checks, parser/layout tests, locked Rust compilation, complete archive
  symbol read, exact generated header/archive staging, and both iOS builds
- Actual Xcode graph and before/after unchanged input evidence, including all
  seven real owner revisions and genuine lock metadata
- Raw IPA hash and embedded builder/run identity, seven-owner provenance,
  signing/entitlements/App Group/shared Keychain checks, arm64 Mach-O inventory,
  generated source bindings and matching dSYM UUID/hash evidence
- A successful primary/retry/recovery upload with a confirmed numeric artifact
  ID, download of that exact artifact plus debug evidence, and the same candidate
  verifier succeeding on the downloaded bytes

The retained filename is `LiveContainer-SideStore-v3.0.3-rc.ipa`; primary artifact
name is `LiveContainer-SideStore-v3.0.3-rc-IPA`. This procedure does not rename the
product. A locally built or recovery-preserved IPA alone is not delivery acceptance.
The provenance continues to record `physical_device_execution: false`.

## Current local required-suite result

Code commit `ab850604`: total **1,207**, passed **1,026**, skipped **181**,
failures **0**, errors **0**. The strict runner exits **2**, correctly rejecting
all 181 unallowlisted Swift/Xcode/macOS prerequisites on this Linux executor.
This is not a passing macOS required gate. No skip policy or test was weakened.
The workflow's macOS-specific requirement flags remain enabled; this local run
uses the five historical source fixtures and cannot execute the native checks.

Saved evidence: `cutover-evidence/final-required-suite-linux.json`, compressed
complete test log and per-test timings beside it. The JSON records uncompressed
SHA-256 values, exact command, fixture commits and all skip reasons. The package
and delivery gates above were inspected; no final product was built in this run.
