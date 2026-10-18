# Authenticated reused-host, one Settings route

This dedicated lane reuses the original built `actual-LiveContainer.app.zip` from run 39064168648. It does not compile, modify, re-sign or repair the genuine app. The exact original archive SHA256 is `ffa6122ad883ceb35e18f43c8a33e59f1855683e16fcfef6938d783ea2999cb6`; the containing artifact SHA256 is `c2c4166674996460cc9c173871b456bae1fc0bc975b7538c011df350103c2f94`. Source is `eb10cedb772bc0496d67edd7a269679e24826b9f` / tree `d6960a99a2f022a333c364fa4d28e264568a28ca`; original builder is `4cb67e4d252ba1b1f7a6fa6431560f75264f337c`. Current probe-builder identity is recorded separately.

## Admission sequence

1. Download only artifact 12112643772 from the same repository, authenticating metadata, original run/commit, exact size and SHA256. The temporary job credential is granted `actions: read`, sent only to the two exact GitHub API endpoints, never forwarded to storage or retained. Redirect URLs and unexpected remote values are excluded from receipts. Stable failure stage/reason codes remain.
2. Authenticate every original evidence-index payload, original builder/source seals, all 300 host Git blobs, original dependency Git trees and build command/settings. These are historical provenance, not fresh dependency checkout or native-build proof. Safely restore only the built app and authenticated catalog; old installed-app evidence is never used as fresh proof. Validate all 123 built files, exact types, payloads and full modes, plus compiled localization/plural/permission resources.
3. Verify the restored app and each of its seven embedded libraries with unchanged `codesign --verify --deep --strict`. Create a fresh pinned simulator, confirm the app absent, install the authenticated app and run unchanged strict installation identity/parser checks and all eight strict signatures. Repeat identity/signature gates before launch and after navigation. No chmod, signing replacement or permission broadening.
4. Generate a fresh external runner. Reuse the immutable original project/scheme bytes and render only this lane's reviewed Swift template. Use explicit `build-for-testing` settings and actual runner build without product setting overrides. The entire generated xctestrun must equal `generated-contract.json`, with exact types, all three environment maps, empty language/region/arguments, `UseUITargetAppProvidedByTests=true` and performance-antipattern checker true. No xctestrun edits occur.
5. Bind independently expanded current test/developer/runtime roots; reject traversal, ambiguous components and links. Require the exact reviewed 75-entry complete Build/Products layout, including runner, swiftmodule and sole xctestrun. No additional app/framework/search-root products or genuine bundle-ID collision are admitted. Inspect actual runner/test plist identities and exact universal fat32 x86_64 subtype 3 + arm64 subtype 0, 64-bit simulator platform 7, MH_EXECUTE/MH_BUNDLE. Hash and inventory all actual payloads before and after execution. The layout/map reference comes from failed capability run 39069474554, artifact SHA256 `646204e802cb02736ca2c3baf3f486055edc116e974de41c5142f5438d896fe4`. Recognition does not convert that original failure into a pass or reuse its compiled runner.
6. Immediately before the only `XCUIApplication.launch()`, independently require the external app proxy's actual initial requested `launchEnvironment == [:]` and `launchArguments == []`. Authenticated failed run 39108204781 on builder 8843e1e3 observed exactly those getters, with zero launches; its original failure remains unchanged. The generated seven-key target map is independently bound and retained in every version-2 policy receipt, not presumed to equal the proxy getter. Any nonempty getter blocks, even if it equals that generated map. No dynamic baseline, alternative accepted map or calibration launch is allowed. Record only exact allowlisted values and mismatch/count metadata; never record unexpected values/arguments or unknown key names. Explicitly set both requested properties to empty and re-read them before launching. Successful admission requires all three ordered version-2 receipts in retained Xcode output and the final observation receipt to agree.
7. Take the ordinary Settings tab and existing language picker through English → Hebrew → English in one launch. The exact known app-owned notification explanation may be dismissed through Later; the separately described one-time SS-LAUNCH-C11-L12 OK acknowledgement is also permitted, while its service failure remains unresolved. Unknown/OS/account/authentication dialogs block. Require native XCTest success, exact navigation/selected values, screenshots and AX evidence, then repeat installed identity/signatures and runner/source stability.

## Environment policy and limits

Runner `EnvironmentVariables`, test `TestingEnvironmentVariables`, and `UITargetAppEnvironmentVariables` are distinct exact maps. The target map contains App Store-distributor override and generated DYLD search/build-root settings that could affect app behavior if applied. Public XCTest setters clear only requested target inputs. The empty initial getter is an empirical prerequisite for this pinned external-proxy configuration. It does not establish how Xcode combines its generated maps with requested inputs. Apple documents [launchEnvironment](https://developer.apple.com/documentation/xcuiautomation/xcuiapplication/launchenvironment) and [launchArguments](https://developer.apple.com/documentation/xcuiautomation/xcuiapplication/launcharguments) as mutable launch inputs, and [init(bundleIdentifier:)](https://developer.apple.com/documentation/xcuiautomation/xcuiapplication/init(bundleidentifier:)) as a proxy for the specified bundle; those contracts do not promise a one-to-one getter representation of the generated target-app dictionary. No calibration/fixture launch or alternate prelaunch/activate route is allowed. The performance-antipattern flag remains true outside these dictionaries, and runner/test instrumentation remains enabled. No effective-process-environment-empty, instrumentation-free or unstyled-runtime claim is made. External pinned toolchain/runtime paths are lexically bound, not exhaustively byte-hashed.

This observes only the isolated unchanged app's ordinary EN→HE→EN Settings route. It is not whole-P2, final composition, account/guest/backend, physical-device, signed-IPA, geometry/RTL, relaunch-persistence or model/root-lifetime acceptance. Screenshots require independent visual review. No production/guest/security/account code changes or broad Settings matrix.

## Bounds and retention

The workflow has a 40-minute hard cap. Download has a shared 180-second deadline enforced by a one-shot POSIX real-time signal timer around synchronous I/O, including slow-drip reads; unsupported timer contexts fail closed before HTTP; native experiment has a shared monotonic 18-minute command deadline and finalization a separate 6-minute reserve, leaving scheduling/portable tests/upload margin. Every command, including preflight, is capped by remaining budget and refused when exhausted. Commands own process groups. This lane overrides the inherited command cleanup: only the actual unreaped child identity under default SIGCHLD may authorize TERM/KILL. One poll may establish/reap that child; after a successful unreaped observation, no poll/wait/communicate occurs until both signals finish. Once reaped, group checks are read-only forever. Surviving/reused/unknown groups fail closed. Bounded join and group-disappearance receipts never authorize unrelated-process cleanup. Finalization only shuts down the simulator created by this invocation. Archives have deadline admission and completion checks; file-copy completion is not a separate hard real-time guarantee.

Failures retain their primary reason plus independent retention errors. Generated sources, full DerivedData, both xcresults, attachments, restored and installed app bytes are ZIP archived with raw link targets, full modes and hidden entries. No external links are followed. Each archive compares source inventories before/after and verifies payload hashes/types/modes. Unresolved owned writers or unstable archives revoke acceptance. Owned-group stop plus stable snapshots is bounded evidence, not proof against arbitrary detached external writers. Full regular-file evidence index and actual command receipts are retained with original artifact and download receipt. The exact workflow is separately retained as visible workflow.yml with full/normalized hashes and current seal, avoiding hidden-file upload omissions.

`binding.json` hashes all lane inputs and unchanged imported dependencies. Its own seal is embedded in the workflow/orchestrator; those two source hashes normalize exactly that one seal field to `TO_BE_SEALED`, avoiding a circular hash. Transitive parser source is pinned before its importing module executes; unreviewed Python bytecode caches are rejected. Portable tests run without network, downloaded executable execution or macOS dependencies and have no skips.

The new workflow triggers only for this dedicated directory/workflow on the approved validation branch. Existing host, Settings and companion workflows are unchanged.

## Failure-only late-foreground evidence

Authenticated run 39109943797 attempt 2 failed inside the single `app.launch()` call after approximately 30 seconds. Its diagnostic Session log subsequently recorded the exact app running foreground, but the test never advanced to Settings. The original failed result remains unchanged. Its generated seven-key target map also appears in XCTest's launch request and launchd job configuration despite empty requested getters; that is not an in-process environment observation.

On this specific launch-call failure only (`launchCallEntered == true`, `launchCallReturned == false`, blocked launch stage, one attempt, no navigation/observations), teardown first emits the original report unbuffered and attaches its unchanged JSON. It then performs one public `wait(for: .runningForeground, timeout: 10)` with before/after state and runner-local monotonic receipts. This does not extend the unchanged 240-second native command or other job/experiment deadlines. Public state, screenshot and AX calls can themselves stall; the outer command remains the hard bound. There is no issue interception, second launch, activation, continuation of navigation, or conversion of the failure into success.

Only a true foreground result within the measured ten-second wait and a foreground post-state permit one diagnostic PNG and one UTF-8 AX snapshot capped at 524,288 bytes. Truncation and skipped/incomplete stages are explicit secondary diagnostic errors. Each stage is written before/after its public call and attached independently. An interrupted sequence remains a partial prefix, never acceptance. XCTest output forwarding and attachment preservation require native evidence; the writes alone do not prove durability across a crash. The original blocked report is preserved first even if later diagnostic calls fail.

For later route failures after launch returned, the existing generic failure snapshot remains, now taken after the original report is preserved. It does not enter the new late-launch sequence.

An observational entry flag is set only after the final policy-emission guard and immediately before the sole launch call; a failed policy serialization cannot trigger this diagnostic route.

The separate strict parser binds identity, source, exact launch policy, original report, unreturned launch flag, sequence, finite ordered times, eligibility, attachment association and payload hashes. It retains raw-stream diagnostics before attempting xcresult exports. Export/parser failures are secondary and cannot replace the original native failure. Successful route admission still requires all existing native/navigation/resource/identity gates and absence of failure-only events. All captured pixels/AX remain diagnostic and require review; they do not establish a successful launch deadline, Settings route, all-text localization or device acceptance.

## Exact startup service-error acknowledgement

Run 39118510081 genuinely launched the authenticated app and reached Home. After
its source-owned notification explanation was cancelled with Later, the distinct
SideStore alert appeared with the exact message:

`The embedded SideStore process could not be launched.\nError ID: SS-LAUNCH-C11-L12`

The old generic first-alert absence query could bind to this successor. This
revision checks disappearance of the exact notification title, then permits one
real, uniquely identified, hittable OK tap only for this exact successor title,
message and two-button inventory (Copy Diagnostics / OK). Authenticated production
OK calls only `status.clearIssue()`, which assigns `issue = nil` and `error = nil`.
It neither retries the backend nor proves recovery. The service query is bound to both exact title and body. Exactly the two observed
StaticText labels, the two buttons, unique OK, query identity and hittability are
rechecked immediately before the tap. Unknown, multiple, repeated, reappearing
or OS alerts block the probe. No Copy Diagnostics action is taken.

An unresolved `serviceFailure` is retained before the OK lookup/tap and in every
later native report. Identity-bound, unbuffered identified and acknowledged
receipts preserve the exact action order and post-OK disappearance before Settings.
The Python collector retains the fault before xcresult export, including partial
failed outcomes. A successful Settings result requires both receipts to agree
exactly with the final report, and still reports `serviceFailureUnresolved=true`
and `backendReadinessProven=false`. The original failed run remains failed.

The local startup verifier replaces only the inherited startup-action block;
portable source-equality tests preserve every other identity, picker, frame,
selected-language, screenshot and accessibility verification byte. The existing
notice-after PNG/AX records the exact pre-OK error; the following Settings captures
supply visual evidence after dismissal. No extra capture pair is added: at most
13 successful or 15 ordinary-failure probe attachments fit the inherited limit16.
The single launch, exact generated maps, eight-object signature gates, original
Settings route and all native/experiment/job deadlines remain unchanged. The
SideStore fault, effective Xcode environment, simulator-only scope and pending
full composition/device gates prevent any all-P2 or backend-readiness claim.
