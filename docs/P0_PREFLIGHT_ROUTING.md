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

`AUDIT_PUSH_LANE` is temporarily `input-diagnostic` for the investigation following
run 37533074107. Only the audit branch's push selection changes; other push
branches and default/manual release keep their previous behavior. The UI job is
explicitly named “Input focus diagnostic (not acceptance)”. Restore the constant
to `preflight` in the next reviewed commit after inspecting diagnostic evidence,
then require the complete two-device/eight-case/24-image preflight again before
requesting the full release on that frozen commit.

The existing fixture is built once. A previously shutdown phone is booted once;
`testCredentialsDefault` is requested in two separate xcodebuild invocations:
`cold-launch` and `fresh-app-relaunch`. Both observations are retained even if the
first fails. There is no script-level retry or retyping. XCTest can itself
restart after a crash; complete native summaries and logs remain visible, and
extra reported test executions cannot count as successful observations.

Each invocation keeps the 240-second case allowance and has a 360-second command
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
