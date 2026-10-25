---
name: v3-release-review
description: Use when reviewing, fixing, or cutting a LiveContainer + SideStore v3 release candidate. Encodes the architecture invariants, the structured error contract, the operation/session rules, the #30-#40 acceptance criteria, and the rule that CI green is not device validation.
---

# v3 release review

LiveContainer is the visible host and UI. SideStore is an embedded headless
backend. Never move a responsibility across that line.

## Architecture invariants

- Apple authentication, 2FA, certificates, signing, provisioning, Core Data,
  sources/catalog, install/update, refresh, account state and pairing stay in
  SideStore.
- **One** authentication implementation. The existing SideSign /
  SignInOperation flow is the only path. Do not add a second.
- The refresh transport is LocalDevVPN -> Lockdown -> CoreDeviceProxy/TLS ->
  CDTunnel -> RSD -> AFC / InstallationProxy. It stays PC-free.
- IPA staging stays in private App Group storage behind canonical tokens.
- Never surface credentials, 2FA codes, private keys, pairing contents, raw
  Apple responses, tokens, or unnecessary private paths.

## Operation invariants

- Unique operation sessions and generations.
- No blind retry of an uncertain mutation.
- Terminal operation results are write-once.
- Refresh success is verified from a manifest and installed-app state, never
  inferred from a command returning.
- Correlation IDs, manifests and installed-app verification are preserved.
- An unknown NSError integer stays unknown. Do not infer a cause from a number.
- Do not invent causes, and do not claim a side effect that nothing proved.

## Structured error contract

- Failures cross as `CombinedFailure`: operation, stage, code, correlation,
  retryability, safe cause, source step.
- The host prefers the structured `failure` envelope over a legacy string
  `error` token. **Anything that must reach the user travels inside the
  structured envelope.** A classification carried only by the legacy token is
  dead on arrival.
- A safe cause needs a producer. `SafeCause.responseEncodingFailed` existed with
  exactly one producer, in a branch the host could never reach.
- `loading` used to mean both "a snapshot" and "a mutation". Activity is now
  named (`V3LoadActivity`); only a snapshot completion may resolve a snapshot
  waiter.
- Diagnostics are not product copy. `technicalDetails`, `underlyingCode` and
  `error.localizedDescription` belong in a log or a Copy Diagnostics payload.

## Property lists

- `URL` is **not** a property-list leaf. Send `url.absoluteString`.
- A Swift `Optional` boxed into `Any` is not encodable. Omit the key instead.
- The accepted leaf set is Foundation's, so it cannot drift from CoreFoundation.
- Anything you accept must agree with `PropertyListSerialization` itself. A
  hand-maintained type list is a duplicated copy of CFPropertyList.c and is
  wrong the day it is written.

## Reviewing

- Do not trust a prior commit's claim. Re-derive from the current HEAD.
- Do not trust a test. Several tests here once pinned the defect they were
  written to catch, including one asserting `isEncodable(URL(...)) == true` and
  one requiring a numeric error code in a user-facing caption. Check what a test
  *asserts*, not that it passes.
- Prefer executing behaviour. Extract pure functions, compile harnesses, run
  `PropertyListSerialization` round trips and real state machines.
- If a shape appears in production, model that exact shape in a harness. A
  harness that builds a different shape certifies nothing.
- Watch for a second authority: two views answering the same question from two
  sources, or a fact nothing ever produces. An unobserved fact that is
  outstanding by policy means a banner that can never clear.

## #30-#40 acceptance

| Issue | Code fix | Also needs a device |
|---|---|---|
| #30 Update button / parity | yes | update smoke test |
| #31 Apple auth errors | yes | real Apple credentials |
| #32 2FA delivery | yes | a real code delivery |
| #33 Guest background death | backport check | guest survives background/foreground |
| #34 Preferences persistence | host and guest separately | cold relaunch, both sides |
| #35 Refresh stops working | audit only | affected-user retest over real transport |
| #36 Reload Status UX | yes | visual check |
| #37 Install fails, iLoader works | yes | **the same IPA must install**, or a shown Apple rejection |
| #38 Unable to Add Source | yes, two stages | full add -> relaunch -> reopen sequence |
| #39 JIT-Less certificate | diagnosis, possibly fix | code-signature validation |
| #40 Add Source Cancel | yes | phone and iPad, hardware keyboard |

## The rule that matters

CI green is not device validated. Never mark an issue fixed because the source
looks right, a grep passes, a harness passes, or Actions is green. Issues whose
acceptance involves Apple services, InstallationProxy, signing, JIT-Less,
LiveProcess lifecycle, persistence, refresh transport, or a physical iPhone or
iPad need the code fix and the device result recorded separately.

`physical_device_execution: false` in the artifact provenance is the machine-
readable form of the same fact. Read it before quoting any run.
