# P0 sign-in native rendering gate

The combined build's existing full-layout step now requires `--require-p0-signin`.
This runs four XCTest methods on each phone and iPad already selected and booted
by the layout runner. It creates no additional simulators, does not erase a
simulator, and leaves all 14 legacy reports, their source identities, process
bounds and cleanup unchanged. The user-requested outer layout allowance is 120
minutes, and the overall job allowance is 210 minutes. The measured inner
budget remains 35 minutes legacy plus 45 for sign-in (build 5 + phone 18 +
tablet 18 + recovery/export margin 4); the extra outer allowance does not
relax any individual command bound. Per-suite build/device timing is logged.
No test, screenshot or failure gate is optional.

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

## Early fail-closed ordering

The complete existing layout step runs after host source generation and exact authentication source-boundary verification, before native host/transport/backend builds. Its inputs are the same prepared LC grid/banner/model/shell Swift files, builder templates and fixtures, and immutable baseline Git object; it consumes no compiled app or transport product. The runner, source hashes, one shared two-device lifecycle, 14 legacy reports, 8 sign-in cases and 24 required PNGs are unchanged. The moved step uses default success gating; its failure prevents later builds and packaging. Evidence upload remains always-run, and packaging still requires every preceding gate. Outer bounds are 120 minutes for layout and 210 minutes for the job, while the measured per-case/device bounds below remain unchanged.


## Runtime query and failure evidence correction

Run `37448846642` passed compilation but exposed fixture-query failures before
any sign-in screenshots or input/tap checks. Its retained PNGs belong to the
legacy suite. No sign-in pixel acceptance is inferred from that run.

The collapsed diagnostic body now has a stable accessibility identifier. Tests
never use the full diagnostic string as an element identifier; clipboard and
visible-message comparisons still require exact content. The Cancel count is
checked after bounded scrolling has exposed the control. Reveal requires the
whole frame to fit a measured list region outside navigation, keyboard and
fixture telemetry, rather than accepting a partially hittable element.

The test verifies username input while that field is visible, then uses native
Return before finding the password field. After Submit, an independent fixture
state observation verifies the extracted failure-clear policy, and the test
returns to the header to check that stale error/copy controls are absent.

Launch and teardown attach diagnostic PNGs, and teardown retains a bounded
accessibility hierarchy as text. Teardown reports include XCTest's assertion
and exception count, which must be the integer zero for acceptance. This uses
[XCTest's documented teardown lifecycle](https://developer.apple.com/documentation/xctest/set-up-and-tear-down-state-in-your-tests)
and [totalFailureCount](https://developer.apple.com/documentation/xctest/xctestrun/totalfailurecount),
rather than relying on Swift defer after XCTest aborts a failed method. A hard
test-runner crash can still prevent teardown; missing reports remain failures.

The original 24 named acceptance screenshots remain required. Extra diagnostic
screenshots cannot replace them and do not claim post-cancellation product
layout parity. The workflow also retains the exported failure text, extracted
Swift inputs and fixture project alongside image/report evidence. Updated
query and geometry code still requires a complete macOS CI execution and
actual PNG review before UI acceptance.

## Interrupted-result evidence recovery

Run 37459827308 passed all 1,113 native checks, and its phone credentials
cases passed, but the sign-in suite did not finish. The submitting-header
check reached its full-frame containment predicate and failed after repeated
scrolls. Its final query trace proves existence and hittability had succeeded;
it does not identify the clipped edge. Tablet execution also slowed and failed
while terminating the fixture. The 480-second command bounds left incomplete
xcresult bundles, so no sign-in pixels could be exported or reviewed.

The fixture now writes the same screenshot bytes atomically into its own
Documents container before adding each XCTest attachment. A fresh per-device
run ID supplied in the test-runner environment isolates those files. Fail-closed
progress JSON, including exact element and safe-region rectangles before each
scroll, is persisted before launch and teardown RPCs. The unchanged containment
and hittability requirements remain mandatory. A reveal reuses the measured
region during its gesture-only loop and fails with diagnostic pixels after
three unchanged nonempty frames rather than repeating ineffective gestures.
Navigation bars may change size during scrolling. Every new reveal, candidate
success and stall remeasures its safe region, as do final control measurements.
The gesture-only loop does not introduce keyboard input.

The host always attempts a bounded terminal simulator screenshot and harvests
only bounded regular files from that exact runner/run directory before result
export and simulator cleanup. The runner bundle identifier comes from its built
Info.plist. This survives an unfinished xcresult when the runner container
remains available. Capture failures are recorded explicitly. Direct files stay
in a separate diagnostic directory: they cannot replace the required 24
acceptance PNGs, four passing tests per device, complete reports, or zero-skip
XCTest summaries. Terminal pixels may show the app after teardown or SpringBoard
and therefore do not establish any sign-in layout assertion.

This change adds evidence and reduces redundant accessibility queries. It does
not establish a product layout fix or successful real-account authentication.

## Measured XCTest execution bounds

Run 37472893006 passed all 1,124 native checks. Its durable captures now show
one credentials panel with visible Copy Details and reachable Cancel at normal
and largest text on both phone and tablet. Those diagnostics are not a passing
UI gate: the run failed, and the submitting cases did not reach their assertions.

The phone default case reached its final Cancel assertions at 119.26 seconds;
the 120-second watchdog fired during teardown before termination at 122.92
seconds. The same cancellation state completed successfully in a 109.287-second
phone largest case. Tablet largest hit the watchdog while checking Copy Details,
before Cancel, and continued through its report at 165.71 seconds. This evidence
supports undersized execution bounds, not a cancellation-state product defect.

Each case now has a 240-second allowance, covering the observed 166 seconds
plus 74 seconds for remaining work and cleanup. Each four-case device command
has 1,080 seconds (4 × 240 + 120 startup/result-finalization margin). The 300-second
build bound, 30/15-second recovery commands, simulator boot bounds, serial
execution, all eight cases and 24 required acceptance PNGs remain unchanged.
No test retry, optional gate, fixture cancellation change or UI assertion removal
is introduced. Full native execution and complete XCTest summaries remain required.

## User-requested outer allowance

After run 37484580818 began, the user requested a two-hour UI allowance. The workflow layout envelope is 120 minutes and the overall job 210 minutes (prior 170 plus 40). The measured internal controls remain 240 seconds per case, 1080 seconds per four-case device suite, 300 seconds for fixture build, 30/15 seconds for diagnostic capture/container lookup, and 120 seconds for result export. All eight cases, 24 acceptance screenshots and failure gates remain mandatory. This outer-budget update does not change an already-running job and must not cancel it merely to apply a timeout retroactively.

## Physical visibility versus gesture padding

Run 37484580818 retained the submitting-screen pixels and exact rectangles.
The phone header was visibly unobscured at y=168, while the fixture demanded
it begin at y=172. Tablet normal/largest cases showed the same four-point
mismatch (138 versus 142, and 159 versus 163). The fixture had incorrectly
reused its four-point gesture padding as a physical visibility boundary.

`available` now returns the actual region outside navigation, keyboard and
telemetry. Only swipe coordinates use the separately inset `gestureRegion`.
Full-frame physical containment, hittability, actual taps and all acceptance
cases remain mandatory. Native tests execute the extracted production-fixture
helper against those observed rectangles and against real navigation, keyboard
and horizontal clipping. Stall tracking alone treats changes up to 0.01 points
as floating-point noise, preventing repeated zero-progress swipes from resetting
the stall count. The containment tolerance itself is unchanged.

The inspected submitting captures show a visible header, disabled Submit and
visible Cancel. The failed run did not establish all required Cancel callbacks;
complete native acceptance remains pending. No app production layout changed.

## Runtime input identity recording

Run 37496696970 completed all eight XCTest cases with zero failures or skips,
but final evidence validation failed: username and password measurements recorded
an empty `label`. Their actual accessibility hierarchy exposes the names through
`placeholderValue` (`Apple ID` and `Password`). The recorder now reads that actual
property only when one of those two known input labels is empty, requires its
exact expected identity, and never uses the input's entered or secure value.
Other controls still require a nonempty label. The validator is unchanged.

The complete eight-case evidence audit found exactly these 16 empty-label
failures and no additional contract mismatch. All 24 required screenshot names
map uniquely to valid PNGs; exported pixels match the durable copies. Both
XCTest summaries contain four passes, zero failures and zero skips. All captured
source/project file hashes match their identity manifest. Copy Details and Cancel
captures were visually inspected across every device/state/text-size case.
In-memory label-only characterization was used to identify remaining validator
mismatches; original evidence was never modified and the failed run remains failed.

This local recorder correction still requires native execution. Linux source
checks cannot establish XCTest accessibility properties or simulator success;
publication is paused pending the requested CI-process review.

## Bounded username readback synchronization

Full release run 37523874365 passed seven of eight sign-in cases. Phone
submitting-largest failed the immediate username value assertion after one native
typing action. Its retained screenshot shows the correct focused field, keyboard
and expected visible suffix; the hierarchy truncates the value, so the exact
mismatch and its cause cannot be reconstructed. The same source passed preflight.

The fixture now waits for keyboard readiness before typing once, then uses the
existing five-second waiter for the unchanged exact username equality. It never
retypes, injects a value, or accepts partial content. A persistent mismatch still
fails. Up to 16 observations record only value type, UTF-8 length, equality and
keyboard presence; no entered text or password is serialized. The native pure
helper tests cover transient-to-exact success and persistent failure. This is
synchronization hardening with unproven causal attribution, not an auth fix.
