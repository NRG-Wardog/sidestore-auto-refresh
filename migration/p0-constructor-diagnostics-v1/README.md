# Focused P0 constructor diagnostics

This additive basis, `p0-constructor-diagnostics-v1`, extends delivered P0 only.
The original P0 admission module and approved metadata remain unchanged. The
independent diagnostic review anchor binds the exact reviewed native receipts,
source graph and active pins. This basis is eligible for the separately gated
full build; it does not claim a completed IPA, genuine ADI or device acceptance.

## Restored source stage

After workspace loss, the ten phase-2 contract files were reconstructed and
verified against their previously recorded digests. The registry is
`94fd38ce234622ac5429a2e2b454418f8c0a5251dd25812744e0fab4f511755b`;
the unchanged validator is
`0d5a5aa61dd37f566359312611826ae5dbbae00176c50b1e4c5e2143530bf520`.
All seven owner-manifest and source-evidence digests also match. The source
stage binds 95 runtime files, 13 contracts and 29 edges. Its new bounded
constructor contract connects AnisetteKit to SideStore to LiveContainer.
Existing ADI v2 contracts and the historical contract-family identifier remain
unchanged, as do SideSign and transport-owner runtime contracts.

Runtime hashes and modes for all 73 files in the four changed-owner checkouts
were reverified. The fresh native phase-two run also verified the three transport
roots and complete source graph. Source matching alone does not establish
native, ADI or physical-device success.

The genuine phase-1 SideSign resolver receipt and retained lock bytes derive
from run 38973641877, artifact 12078575730. Its archive and all 83 indexed files
were reverified. Phase two is run 39017114598, artifact 12095672731, with all
208 indexed files verified. Its nine suites executed all 42 exact test IDs with
zero failures, errors or skips; both unsigned iPhoneOS builds and the complete
95-source, 13-contract, 29-edge graph passed. Final SideStore lock-only successor
`aebf23d8129f831c1c706619a71c936ab73fb6e5` preserves the exact captured resolver
output. The tested LiveContainer successor is
`1df50257f6255522e6ff48db226508fe74d6b17d`. Failed run 39013295196 is not admitted.

The separately reviewed native approval digest is
`940879e3a378019d27a8ceaa014b9b31584229ed3220772b94cd1e44f73fa8ec`.
The final owner graph, source transitions and both lock-only resolver chains are
sealed with it. Full-build packaging and the IPA remain separate gates.

## Admission requirements

The admission code and focused tests were regenerated and freshly tested; their
bytes are not asserted to equal the lost unpublished scaffold. Admission checks:

- A separately assigned review digest, exact metadata inventory and original P0
  approval digest; all inherited evidence is retained byte for byte.
- Seven exact owner commits/trees, four linear successors from delivered P0,
  unchanged transport owners, exact source transition and cumulative inventory.
- Fresh native run, artifact, input and evidence-index identities; full child
  graph; retained resolver locks and originHash; lock-only descendants.
- Canonical fork 83 checks, constructor 2105 checks and all 15 exact case IDs,
  eight boundary tests, five constructor-consumer and six retained ADI-consumer
  tests with exact IDs and zero failures, errors or skips where applicable.
- Explicit false `ADI_executed` and `device_validation` flags. Native regression
  tests are not proof of genuine Apple ADI or physical-device acceptance.
- Retention of the native constructor prefix and five consumer keys in actual
  packaged executables, alongside every inherited ADI v2 compiled literal.

The exact metadata set is the prior P0 file inventory plus `source-transition.json`,
`inherited/p0-pins.json`, and every historical approved file (including its
approval) under `inherited/p0-native-basis-v1/`. New top-level files describe
only the successor basis. Admission requires the independently reviewed native
status `INDEPENDENTLY_REVIEWED_P0_CONSTRUCTOR_DIAGNOSTICS_NATIVE_PASS`; this source
basis carries that independently bound receipt, with explicit false ADI and
physical-device flags.

Required migration discovery includes admission, contract and package tests.
Synthetic fixtures exercise rejection paths without activating production
admission. The workflow retains an exact historical Anisette fixture for P0
package tests and separately tests the diagnostic Anisette source when active.

## Offline evidence finalization

`migration/finalize_p0_constructor_candidate.py` validates two actual downloaded
artifact ZIPs against separately fetched official GitHub run/artifact responses,
their exact file indexes, published runner Git objects, independently approved
phase-two input bytes and hash, and all four changed-owner Git repositories.
The caller must authenticate the GitHub responses and approve the input hash
before invoking this offline tool. An input hash is not itself an approval.

It requires phase-one 83 native checks, 2105 constructor checks with all 15 cases,
eight native boundary tests and five selected SideSign tests. Phase two must
execute all 42 exact test IDs across nine suites with no failures, skips,
expected failures or unexpected successes, complete unsigned iPhoneOS builds,
and validate the 95-source, 13-contract, 29-edge graph. Both phases must prove
their complete source graphs and unchanged sources before/after execution.
The final published SideStore commit must be a lock-only descendant whose lock
is byte-for-byte the actual phase-two resolver output. Both resolver chains and
all runtime source bytes/modes are rechecked against published Git objects.

Required arguments are:

```
--phase1-zip PATH --phase1-run-json PATH --phase1-artifact-json PATH
--phase2-zip PATH --phase2-run-json PATH --phase2-artifact-json PATH
--runner-repo PATH
--approved-inputs PATH --approved-inputs-sha256 SHA256
--owner AnisetteKit=PATH --owner LiveContainer=PATH
--owner SideSign=PATH --owner SideStore=PATH
--final-sidestore-commit COMMIT
--output FRESH_DIRECTORY_OUTSIDE_BUILDER
```

Only after every check succeeds, the tool atomically writes `metadata/`,
`candidate-review.json` and `candidate-summary.json` into a fresh directory.
It never edits the builder or activates admission. Its receipt remains
`CAPTURED_FOR_INDEPENDENT_REVIEW`, its readiness scope remains
`candidate_for_gated_full_build_review`, and its review digest is null.
Independent review, approved metadata and the separately assigned admission
anchor are required before production build eligibility. Synthetic fixtures
only test this process; they never stand in for native evidence.
