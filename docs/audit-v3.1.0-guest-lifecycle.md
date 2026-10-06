# Guest launch and lifecycle parity review

Reviewed against builder `33f66940391d7489431aa21ba193687c97b08ab8` and clean
LiveContainer `12377cf3b91d51739a33f14a302e5f522b238593` on 2026-10-05.
This is a focused source review, not device acceptance or an all-lines audit.

## Confirmed pending-launch defects

The pinned `MultitaskSupport/DecoratedAppSceneViewController.m` calls
`pidAvailableHandler` only from the success branch of
`appSceneVC:didInitializeWithError:`. Its error branch cleans up and displays an
alert without completing the caller. `LCUtils.launchMultitaskGuestApp` installs
its completion in that property. `LCAppModel.runApp` awaits it, including an
explicit checked continuation for multitask JIT. A failed virtual-window launch
can therefore leave the caller pending and its launch-in-progress state set.
The previous Guest Return changes fixed native-window callback ownership but
left this virtual-window failure path unchanged.

The same pinned class's `closeWindow` directly calls its own exit handler when
there is no running PID. That removes the presentation without retiring the
pending `AppSceneViewController`. A later request completion can still register
that closed guest. This is separate from minimizing a live guest.

The narrow `patch_guest_return.py` correction:

- Consumes the virtual-window completion once, before cleanup and reentrant
  callback execution. Startup errors and early exits now settle the caller.
- Keeps the original extension cancellation `NSError` on the owning controller
  before cleanup, on the main queue. No OS error domain/code is synthesized.
  An exit with no supplied error uses a project-owned `LiveContainerReturn`
  failure, independently of OS error classification.
- Releases old container ownership before invoking the completion. A duplicate
  or late initialization callback cannot complete a new launch or overwrite a
  previously settled outcome.
- Routes pending Close through the existing controller cleanup. The already
  present request-completion guard then prevents late container registration.
  Explicit Close for an already-cleaned terminated view or nil initializer
  controller still uses upstream decorated-view removal.
- Rejects a delayed success when that virtual guest is already closed or has no
  live PID. Successful launch remains a PID-level result, not proof that a guest
  scene or app has completed initialization.

This does **not** establish that iOS terminates an extension process whose request
completes after pending Close. The cleanup guard suppresses registration, but
there is no new request-cancellation API or kill path. Exact extension process
lifetime after that race remains a separate device/OS verification boundary.

## Preserved upstream behavior and justified differences

- LiveProcess Return still minimizes a virtual window or requests activation of
  the host's real scene. It does not intentionally terminate the guest.
- Direct-process guests retain their separate upstream process-restart escape
  route. They cannot preserve an in-process guest while replacing that process.
- Per-window UUID generations, one-shot native callbacks and cleanup before
  container reuse remain justified ownership corrections. They are not changes
  to the guest database or signing model.
- Dock collapse and edge-tuck are session-initial preferences. Existing manual
  toggles, reuse, delayed creation and hide/re-show handling remain unchanged.
- Guest `NSUserDefaults`/CFPreferences redirection in
  `LiveContainer/Tweaks/NSUserDefaults.m` is unchanged. Guest `HOME`, guest app
  identity and the separate host app-group suite remain separate mechanisms.
  Source preservation alone does not prove cross-process preference durability.
- The existing bounded CFBundle scan is defensive checking of the pinned
  instruction patterns. It does not establish iOS 27.0.1 compatibility or prove
  any reported launch error's root cause.
- No Dead10cc scanner change is included. The independently confirmed
  descriptor-cursor defect remains blocked and unimplemented. Its earlier
  resource-lifetime correction and observer backport do not fix that cursor
  defect or establish affected-device survival.

## Open-issue evidence and acceptance

The latest inspected [#33 maintainer comment](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/33#issuecomment-5978974699)
explicitly keeps same-PID background survival unverified after the released
observer/ownership corrections. The new launch completion fix is relevant to
interrupted guest startup; it does not identify the cause of background death.

The [#34 maintainer comment](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/34#issuecomment-5978974894)
keeps host relaunch, guest cold-relaunch persistence and cross-container isolation
open independently of #33. No additional preference root cause was established
in this review. Test host and guests A/B with separate durable sentinels, normal
backgrounding, termination and cold relaunch, without deleting existing data.

The [#39 maintainer comment](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/39#issuecomment-5978976007)
separates certificate-copy readiness from actual guest signature validity.
Callback settlement cannot repair a genuinely invalid or revoked signature.
The reported app must still sign and launch on the exact re-signed candidate.

The remaining tracks continue under the
[thirteen-issue resolution/acceptance map](audit-v3.1.0-resolution.md). No issue
is closed by this review. Exact artifact/native build, post-installer signing,
real extension startup, Return/resume, background survival and persistence must
be observed separately.

## Regression evidence and limits

`test_guest_launch_completion.py` uses pristine pinned sources and applies the
actual builder patch twice. Source checks establish the omitted baseline error
completion, corrected one-shot/cleanup order, original-error retention, pending
Close retirement, late-registration guard and rejection of a partial patch.

The added macOS Foundation harness extracts the actual baseline and generated
Objective-C helper/delegate/Close/exit methods. It requires the baseline error
case to fail with exit 23 and the corrected methods to pass startup error before
PID, pending Close, retained terminated-view/nil-controller Close, duplicate
terminals, reentrant replacement and success-only once. UIKit and OS ownership surfaces are doubles; even a native harness pass
is not extension/device acceptance. This harness is mandatory on macOS and was
**not executed** on this Linux executor.

Local focused run: **22 tests, 18 passed, 4 platform skips**, zero failures/errors.
Local full source-enabled run: **1003 tests, 846 passed, 157 skipped**, zero
failures/errors. This rerun used the supported `EMBEDDED_SIDESTORE_TEST_SOURCE`
variable; an earlier invocation without it hit the pre-existing shallow-worktree
fixture fallback error and is not represented as a pass.
Swift, Objective-C/macOS and iOS behavior are not verified by these Linux results.
`git diff --check` passes. Later integration changes require a fresh full run.
