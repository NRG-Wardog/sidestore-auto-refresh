# Focused sign-in preflight routing

This is a diagnostic CI lane, not a replacement for release validation.
It runs the existing production-derived sign-in fixture and evidence verifier
for all four cases on phone and tablet, with all 24 required screenshots.
It creates no IPA and cannot satisfy the release package gate.

## Selection

| Event | Ref / mode | Lane |
| --- | --- | --- |
| Push | `fix/v3.1.0-audit` | Preflight |
| Push | Either other existing configured build branch | Release |
| Manual dispatch | Missing/empty mode, or `release` | Release |
| Manual dispatch | `preflight` | Preflight |
| Unsupported event, push ref, or mode | Any | Fail closed |

The registered workflow's default-branch form may not show the new input while
running a selected audit-branch revision. Empty input therefore explicitly
selects release. The local subprocess regression proves this routing behavior;
it does not prove the external GitHub form has dispatched a run. That must be
confirmed from the resulting workflow event/ref and selected job when authorized.

The routing job emits only a finite `release` or `preflight` output. Both jobs
require routing success. The full release job's steps are preserved, including
all pinned/native/layout/package/re-download checks. No `skip ci`, path-ignore,
optional failure gate, or global push disable is introduced.

Preflight runs the focused repository/native helper checks, then the shared
fixture build, simulator execution, durable capture and strict verifier.
Generation and fixture-source identity are recorded. Its manifest is explicitly
ineligible for release, and original failed artifacts are never rewritten to
claim success. A passing preflight is a reason to start the authorized full
release lane, not proof that an IPA has been built or verified.

The existing per-ref concurrency policy is unchanged. Freeze branch writes until
the preflight finishes, then dispatch the release on the same selected branch
revision without intervening pushes. This avoids a new push cancelling a running
release and keeps the established runner-concurrency behavior.

## Temporary input-focus diagnostic lane

`AUDIT_PUSH_LANE` was temporarily `input-diagnostic` for the investigation following
run 37533074107. It is restored to `preflight` after inspecting both observations
from diagnostic run 37538455300. Other push branches and default/manual release
were unchanged. Explicit manual diagnostics remain labeled “Input focus diagnostic
(not acceptance)”. Complete two-device/eight-case/24-image preflight is required
again before requesting the full release on that frozen commit.

The existing fixture is built once. A previously shutdown phone is booted once;
`testCredentialsDefault` is requested in two separate xcodebuild invocations:
`cold-launch` and `fresh-app-relaunch`. Both observations are retained even if the
first fails. There is no script-level retry or retyping. XCTest can itself
restart after a crash; complete native summaries and logs remain visible, and
extra reported test executions cannot count as successful observations.

Each invocation keeps the 240-second case allowance and has a 660-second command
bound. Build (300 seconds), boot (600 seconds), export and capture bounds remain
in force. These limits are not completion estimates. The preceding complete
preflight took about 34 minutes including both simulator lifecycles; this lane
omits the tablet and requests two cases rather than eight, but simulator startup
and XCTest idling can still dominate its duration.

`input-diagnostic.json` always records `passed: false`, `releaseEligible: false`,
`fullSuiteValidated: false`, and `ipaBuilt: false`. Separate observation success
requires exactly one successful native test and complete valid evidence for that
case. The unchanged full-matrix verifier still rejects the partial matrix; its
two expected matrix failures are retained. Any additional verifier failure makes
the observation unsuccessful. This evidence cannot replace release acceptance.

The original 360-second diagnostic wrapper cut the cold observation at 362.56s
before its 240-second case allowance elapsed; the result bundle was incomplete.
Both fields had already established and dismissed focus correctly and Copy
Details had succeeded. The separately planned relaunch passed in 139.10s native
and 177.56s total. The cold observation is still failed, never retroactively green.

Retained diagnostic 660s = 240s case + 360s startup/result allowance + 60s margin.
The latest full-phone run measured 862.93s total minus 513.53s native cases,
or 349.40s non-case overhead. Normal full-device 1080s and per-case 240s bounds
remain unchanged; the diagnostic is not rerun merely to obtain a green label.


## Restore the original ARM64 release runner

Controlled preflight 37545707720 completed all eight UI cases with unchanged
fixture/source hashes on the standard `macos-26` ARM64 runner using the same
Xcode 26.4.1 (17E202). This supports using that runner; it does not establish
architecture alone as the cause of the intermittent Intel input/readback failures.

The release job returns to its original `macos-26` selection. Intel had replaced
it in commit 6baf641 only during an ARM64 acquisition outage. Native probes and
layout builds select the actual host architecture, OpenSSL is located through
`brew --prefix`, Cargo download caches are architecture-keyed, and device Rust
and Xcode products remain ARM64/iPhoneOS. No product, fixture, pinned source or
acceptance condition changes. The complete preflight and all full-release gates
must run again on the final frozen commit; earlier evidence is not substituted.
