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
