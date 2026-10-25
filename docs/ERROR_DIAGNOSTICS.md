# Stable error IDs, version 1

This is a diagnostic categorization change. It does not establish or fix the
reported Apple authentication root cause. Existing English wording and actions
are retained, except for the guest-launch privacy boundary described below.

## Contract

- `Error ID` / `diagnostic_code` identifies finite observed failure evidence.
  It is stable between attempts, launches and builds for the same observed fields.
  Wire-carried fields retain their code through a round trip. Host-only launch
  context is deliberately not serialized: its optional L token is available
  only where that local launch step was observed; a remote copy retains the
  base stage/code instead of inventing the missing step.
- `correlation` identifies an individual request or attempt. It remains separate.
- `builder_commit` is the installed bundle's `LCBuilderCommit`, only when it is
  exactly a 40-digit hexadecimal public commit identifier. Missing or malformed
  metadata is `unknown`. No build identity is guessed.
- A code is not a diagnosis of an unobserved cause. For example, `SS-AUTH-C11`
  means an authentication-stage failure with no more specific captured evidence.
  It does not mean that a password is wrong. `A00` means unknown auth kind.
- No code is derived from a provider description, account, endpoint, token,
  arbitrary NSError domain, correlation UUID, randomized hash or array index.

The append-only registry is [ERROR_CODES_V1.json](ERROR_CODES_V1.json).
Existing token assignments must never be changed or reused. Add new assignments
explicitly and update the exhaustive executable and source contract tests.

## Structured code grammar

`SS-<stage>-Cnn[-Snn][-Fnn][-Tnn][-Lnn][-Vnn][-Pnn][-Ann]`

| Part | Evidence |
| --- | --- |
| stage | Explicit `CombinedFailure.Stage`, e.g. AUTH, PROV, IPA, INSTALL, PAIR |
| C | Explicit generic failure code such as timedOut, busy or failed |
| S | Observed typed source step, if available |
| F | Typed safe cause, if available |
| T | Allowlisted typed account/signing error, if available |
| L | Typed extension-launch step, if available |
| V01/V02 | Existing typed installation verification evidence only |
| P01 | Typed fetchTeams server result 1100 only |
| A | Finite authentication envelope kind; A00 explicitly means unknown |

Example: `SS-PROV-C11-S06-T01-P01` identifies a failed provisioning
fetchTeams step with a typed server-reported result 1100. The authentication
presentation may add its separately observed `A` kind. This is not evidence of
wrong credentials, a blocked account or a particular Anisette server defect.

Authentication has a different wire schema from `CombinedFailure`. Its adapter
uses only validated finite fields, replaces a previous generated Error ID label,
and puts exactly the same canonical ID in the displayed authentication message
and copied diagnostic payload. It does not classify display prose.

## Local conditions and presenter-only IDs

`SS-<stage>-Dnnn` identifies a known repository-owned local UI condition. These
are explicit labels at the producer, not a runtime substring classifier.
They cover busy/blocked actions, unconfirmed outcomes, file presentation,
reconciliation, settings, signing, pairing, setup and scheduler warnings.
A local condition may accompany a more specific underlying typed failure.
Existing Copy Diagnostics controls include its final `visible_error_id` alongside
the underlying diagnostic details, rather than replacing the underlying evidence.

Native presenters use the fixed `SS-NATIVE-<site>` registry in
`scripts/patch_native_error_presenters.py`, documented in
[native-error-presenters.md](native-error-presenters.md). These site IDs cover
13 transformed files plus the separately owned guest-launch alert. They identify
the presenter only; their underlying cause remains unknown. The root crash-report
sheet is intentionally unchanged.

The complete literal-to-file matrix is
[ERROR_PRESENTATION_COVERAGE.json](ERROR_PRESENTATION_COVERAGE.json). It records
original wording, each frozen ID, producer files, and the native presenter audit.
Normal progress, successful operations, normal cancellation, confirmations,
explanatory settings text and expected not-yet-configured setup are excluded.

## Coverage and unchanged controls

| Flow | ID source | Copy/action coverage |
| --- | --- | --- |
| Authentication / verification prompts | finite auth envelope and local conditions | Existing Copy Details/Diagnostics and typed recovery actions |
| Anisette / provisioning / certificates | CombinedFailure, typed recovery-only guidance, local conditions | Existing actions; some settings pages retain text selection without a dedicated copy button |
| Install / update / refresh / operation cancellation | CombinedFailure plus explicit local condition | Existing operation/global Copy Diagnostics; current and previous attempts retain separate correlations |
| Pairing / sources / catalog | CombinedFailure plus local condition | Existing typed panels and copy buttons; generic catalog message retains its current controls |
| Network / shared storage / recovery | stage and typed safe cause, local storage conditions | Existing global/recovery controls; unconfirmed storage stays unconfirmed |
| Settings / backup / restore / logs | common typed or untyped guidance | Existing message-only screens retain existing controls |
| Scheduler / setup / refresh history | structured failures and local warning labels | Existing refresh/setup copy controls; old persisted pre-upgrade messages may have no original typed evidence |
| Guest UIKit launch | SS-GUEST-EXIT or SS-GUEST-UNKNOWN | Existing alert and copy action, safe native evidence and separate correlation |
| Native LiveContainer presenters | fixed presenter/site identity | Generator coverage recorded separately; does not infer a cause from raw strings |

A dedicated copy control is not added to every existing message-only screen in
this P0 change. The code is visible with the message. Presentation, action and
wording consistency belong to the separate P1 UI work.

## Privacy boundary and residuals

The repository-owned UIKit guest launch alert formerly displayed and copied
arbitrary `NSError.localizedDescription`. It now shows the known own-domain 410
exit message, or a fixed unknown-cause message. Copy retains the finite site ID,
stage, correlation, validated builder SHA and allowlisted native domain/numeric
code. Unknown domains/codes are redacted together. It never copies provider text
or userInfo.

Other pre-existing native LiveContainer error strings may still contain arbitrary
upstream descriptions. Presenter IDs do not make those strings safe, and this
change must not be described as a full privacy scrub. Their detailed privacy and
wording review remains explicit P1 work. New IDs do not add provider data to any
copy payload. System-owned alerts, OS permission errors and content rendered
inside guest apps are outside the host's error presentation layer.

Old persisted warnings have no recoverable original typed category unless they
also contain a valid structured failure envelope. Do not guess a cause from
historical text. Unknown/untyped categories remain explicitly unknown.

## Verification

`tests/test_v3_diagnostic_codes.py` checks the frozen tables, exhaustive enum
coverage, unique assignments, every inventoried local producer and the guest
privacy boundary. Its Swift harness exercises real production code, stable
correlation-independent IDs, wire round trips, unknown/malicious input, precise
fetchTeams evidence, auth single-label display/copy consistency and build-value
validation. It is required in the macOS aggregate, not skip-allowlisted there.

Linux source checks are not an iOS compile or device proof. The native build,
mandatory auth render fixture and real-device report remain separate gates.
