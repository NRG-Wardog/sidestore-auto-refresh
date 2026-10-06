# P0 sign-in native rendering gate

The combined build's existing full-layout step now requires `--require-p0-signin`.
This runs four XCTest methods on each phone and iPad already selected and booted
by the layout runner. It creates no additional simulators, does not erase a
simulator, and leaves all 14 legacy reports, their source identities, process
bounds and cleanup unchanged. The required combined layout step is bounded at 60
minutes, and the job at 150 minutes. Legacy layout was measured at 22m27s and can
approach 30 minutes. Its prior 35-minute allowance is preserved, with 25 additional
minutes for sign-in (build 5 + phone 8 + tablet 8 + export/margin 4). The job retains
its prior 120-minute budget plus 25 for the new required suite and 5 minutes of
overall margin. Per-suite build/device timing is logged. No test, screenshot or
failure gate is optional.

## What executes

Each device runs the credentials prompt at a measured 320-point viewport with
normal and largest accessibility Dynamic Type, both before and during submission:

- One credentials panel and no redundant Status / Needs your input panel.
- The unknown-failure guidance and actual visible Copy Details button.
- Collapsed technical details; Copy does not require expanding the disclosure.
- Empty fields match the real credentials producer (appleID/password, no
  options and no returned password value). XCTest types both synthetic values
  using native keyboard input, then dismisses that keyboard with Return.
- A real XCTest Copy tap, observed inside the app through the pasteboard-change
  notification. Its result must exactly equal the production-rendered synthetic
  diagnostic and pass an independent finite field/value check. Neither the
  synthetic username nor password may be present.
- Measured username, secure password, Copy and Cancel controls inside the
  viewport, with Copy/Cancel at least 44 points high (one-point measurement
  tolerance).
- Submitting cases tap the actual Submit button after the copy assertions.
  The fixture mirrors the production admission transition, setting submission
  and clearing the prior credentials error through the extracted production
  policy. Copy is not incorrectly asserted visible after this transition.
- A real Cancel tap that invokes the fixture cancellation callback exactly once,
  including when submission is in progress. Cancel must then show Cancelling
  and disable. Cancel must not route as a credentials answer.

There are three pre-cancellation screenshots per case, 12 per device, 24 total.
After Cancel, only callback/button state is asserted. No post-cancel screenshot
or layout parity is claimed.

## Source identity and boundaries

The harness extracts complete, unmodified production `V3PromptSection`,
`V3AuthRepairURLPolicy`, `V3MultiSelectPromptAnswerPolicy`, `V3TwoFactorStep`,
`V3AuthPromptFailurePolicy` and `V3AuthFailureDiagnosticsPolicy` declarations
from the generated shell. The wrapper embeds the exact production
`V3SignInView.body`, account-section visibility and prior-failure properties.
It also uses actual `V3AuthStore.failureMessage` / `failureDetails`, including
the diagnostic-ID message helper when present, unchanged bridge/wire strict
integer and boolean helpers, and the complete production CombinedFailure file.

`V3SignInView.accountContent` is empty only for the pre-cancellation,
unsigned-in credentials state with no recovery, terminal message, progress or
supplementary account content. Its production source is preserved as evidence;
this fixture does not prove signed-in, terminal, provisioning-recovery or
cancellation-pending layout. Fixture stores simulate service state and record
callbacks; no Apple, SideStore backend, keychain or network operation executes.

Every extracted member, complete supporting source, fixture and project input
has a SHA-256 identity. The fixture Info.plist carries the actual builder commit
in `LCBuilderCommit` for the production diagnostic renderer. Supporting files
come from the same builder checkout, with no dependency on P1 settings/profile
sources. Production source is not instrumented; observed state is provided by
fixture-only telemetry outside the extracted controls.

The existing 14 layout reports must pass independently. The new gate also
requires two successful device reports, four passed and zero skipped XCTest
methods per device, complete JSON interaction proofs, and actual exported PNGs.
The verifier checks PNG chunk integrity and bounded decoded scanlines, required
attachment manifests, control geometry and the entire case/screenshot matrix.
Build/execution failure retains the legacy evidence and fails the required gate.

## Verification limits

The Linux editing environment has no Xcode, Swift compiler or iOS simulator.
Python extraction, source and negative-evidence tests do not establish a native
compile or executed UI pass. The macOS CI run must supply the `.xcresult`, PNGs,
per-device JSON reports and source identity before this is considered native
sign-in evidence. The fixture retains the product's iOS 15 compilation target;
it executes on the existing runner's oldest installed simulator runtime and
does not claim physical-device, spoken VoiceOver or iOS 15 runtime validation.
