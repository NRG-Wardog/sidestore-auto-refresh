# Parser-only replay of the exact P2 captures

These five modules are retained verbatim from independently reviewed parser
freeze `59274b3f / 4cb2a2d0 / 9e5f05f0 / e88e4a31 / 5ac53d3d`.
They read archive members as data and query supplied immutable local Git objects;
they never import or execute artifact code or binaries.

The candidate generator is `migration/finalize_p2_composed_candidate.py`. Supply
all native/UI ZIP and independently authenticated run/artifact JSON paths, exact
runner/LiveContainer/SideStore/AnisetteKit Git repositories, and a fresh output
directory. See its `--help` for required argument names. Outputs are pending
review; generation cannot change the review anchor or active pins.

Native capture: run 39172103613, attempt 1, artifact 12153216433, SHA256
`de1c4e93250ba5b021f50c9a9067b453a23d53b6770cdbd7dfc9145cbf46a0ea`.
Parent UI: run 39169380159, attempt 1, artifact 12152372036, SHA256
`e37521f34064bf1683779a99486f53a40c12f6b971b400ccc8fee5b936744903`.

The native parser validates all 13,566 indexed files and 6,775 settled commands,
exact source proofs, five initializer tests, and the compiled simulator product
and localization/plural/permission resources. OpenSSL/litehook Git identities
are independently validated; their redundant regular-blob SHA256 observation
fields are not independently recomputed and are excluded from that claim.

Parent UI evidence retains the prior visible Settings/persisted-preference
review only. UI was not reexecuted on the composition. The observed service
failure remains unresolved; full-app and final-composition acceptance are false.
The normal release must still pass fresh full source acquisition and verification
before compilation and again after building.

## Fixture-dependent mutation tests

The retained `test_p2_replay.py` has 24 parser tests. It is separate from generic
CI discovery because authenticated ZIPs and evidence are external review inputs.
Run it explicitly with these environment variables set to verified local inputs:
`P2_TEST_NATIVE_EVIDENCE`, `P2_TEST_UI_EVIDENCE`, `P2_TEST_HOST_REPO`,
`P2_TEST_RUNNER_REPO`, `P2_TEST_NATIVE_ZIP`, `P2_TEST_RUN_JSON`,
`P2_TEST_ARTIFACT_JSON`, `P2_TEST_NATIVE_CANDIDATE`, `P2_TEST_UI_BASIS`.

    python -B scripts/run_required_tests.py \
      --start-directory migration/p2_capture_parser \
      --allowlist scripts/required_test_skip_allowlist.json --verbosity 2

The empty skip allowlist requires all 24 tests to run. Tests mutate synthetic
copies only as rejection checks; they do not create native execution evidence.
