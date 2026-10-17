# Historical P2 runner generation capability

This bounded diagnostic checks the exact external UI-test runner retained from
run **39064168648**, builder **4cb67e4d252ba1b1f7a6fa6431560f75264f337c**.
It does not build, install or launch LiveContainer, run a test, boot/create a
simulator, modify any project/scheme, or edit a generated xctestrun.

## Why

The original host build, resource checks, installed identity checks and eight
strict signature checks passed. The actionless runner settings query then
failed with an empty supported-platform set. The scheme's sole target builds
for testing, not running. An implicit ordinary-build action is the leading
explanation, not a proven fix.

Apple documents the default build action and `build-for-testing`, which builds
tests without running them: <https://developer.apple.com/library/archive/technotes/tn2339/_index.html>.

## Exact experiment

1. Verify the bound historical input file set, every byte hash, and the unchanged
   existing validator sources before loading them. Compile their exact validation
   function ASTs and parser source directly, avoiding cached bytecode and the host
   orchestration entry point. Copy the three exact retained
   generated source/project/scheme files, without regeneration or substitutions.
2. Require Xcode 26.4.1/17E202, simulator SDK 26.4, macOS arm64, and an existing
   available iPhone 17 Pro on runtime iOS 26.5/23F77. Prefer a shutdown device;
   neither create nor boot one. Missing matching destination is a blocker.
3. Record the actionless `-showBuildSettings -json` control.
4. Record the explicit `build-for-testing -showBuildSettings -json` query.
5. Attempt the actual runner-only `build-for-testing`, even if the settings
   query failed, so query and compilation capability remain distinguishable.
6. Require one generated xctestrun and invoke the existing strict validator
   unchanged. Hash the file before and after. A source mutation stops later
   commands. No target-app, signing, architecture or platform overrides exist.

The query and build must both succeed and strict generated wiring must pass for
`RUNNER_GENERATION_CAPABLE`. The control's expected failure does not block this
result. A successful control would weaken the proposed action-mismatch diagnosis,
so all three outcomes remain separate. There is no fallback repair.

## Honest identity boundary

The Swift embeds the original host executable hash, source commit/tree and old
builder identity. These are historical references. The diagnostic's own commit
is recorded separately. This job does not authenticate a present or newly built
host and its runner products must not be reused for a new host.

The existing xctestrun validator's app-path argument only compares path identity.
This job uses an absent, disjoint reference path and explicitly labels this a
path-separation check, not proof of host existence or installation. All target,
bundle, path-containment, dependency and no-launch-override checks stay intact.

The retained artifact manifest checks integrity, not an independent authenticity
signature. Source/run provenance is bound by the approved binding hash and known
run/commit identity. No app binaries or downloaded executables are inputs.

## Retention and limits

Every command retains stdout, stderr, return code, stage and timeout. Failures
retain input archives, generated sources, partial DerivedData, result bundle,
raw xctestrun when present and final status. Archive creation never dereferences
symlinks. Before/after source inventories and a read-back of each ZIP verify
payloads, types and full modes, including hidden files. Commands own a separate
process group; timeouts terminate/kill only that group and bound pipe joins.
These receipts and stable snapshots are bounded retention evidence, not a proof
against arbitrary detached external writers. The final inventory records hashes. Any retention error blocks a
pass. Read-only `contents` permission and pinned existing actions suffice;
there is no cross-run artifact download or new token permission.

All native command phases, including provenance/toolchain preflight, share one
12-minute monotonic experiment deadline. Every command's timeout is capped to
the remaining budget; an exhausted deadline is recorded before a new process
can start. Bounded owned-group termination/pipe joins remain available afterward.
The 25-minute job leaves approximately 13 minutes for those bounded cleanups,
stable archive generation, verification and upload, less setup/test overhead.
The experiment never consumes separate full per-command maxima sequentially.

Passing this lane proves generation capability only. External app launch,
XCTest execution, Settings navigation, language changes, persistence and final
composition all remain untested. A later full probe must derive fresh identity,
compile a fresh runner and repeat every unchanged install/signature/wiring gate.

Portable checks (no native calls):

The new capability suite requires zero skips. The inherited host suite currently
has 93 tests with one intentional skip: its optional authenticated genuine-source
checkout check is out of scope because this lane does not fetch/build that app.
The remaining inherited checks include the unchanged strict validator/parser.

```sh
python3 -B -m unittest discover -s ci/p2/runner_capability -p 'test_*.py' -v
python3 -B -m unittest discover -s ci/p2/host_probe -p 'test_*.py' -v
```
