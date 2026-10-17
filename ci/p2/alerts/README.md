# Isolated P2 UIKit alert lifecycle

This lane is pinned to LiveContainer `eb10cedb772bc0496d67edd7a269679e24826b9f` and descends from the separately accepted helper/resource preflight `0cc9078e`. It uses a standalone hosted XCTest target with an actual UIWindowScene, the exact production localization file and four exact alert/diagnostic declarations. The exact catalog is compiled into the host app. No production source is modified.

Seven serial XCTest methods are mandatory, with zero skips, failures or missing/duplicate names. Each method retains JSON observations as xcresult attachments. Actual UIKit presentation, replacement, dismissal, input retention and callback gates are exercised. The rapid language-switch case relies on the production defaults observer and checks actual presented title, body, actions, direction, text, selection and focus. The scene case delivers a synthetic notification to the actual production scene observer. The controlled async-error case covers all four languages and preserves opaque input and original diagnostic bytes.

Confirm/cancel delivery is simulated by direct calls to the production session's `finish(from:)` callback gate. This does **not** establish real action-button taps or distinct UIAlertAction handler wiring. The presenter-disappearance notification is synthetic, not an OS-driven scene destruction. The fixture lacks LiveContainer's host-capture selectors and uses the documented standalone defaults/bundle fallback. Counts and absence of resurrection are observed after settled transitions and an additional 700 ms interval; this is bounded runtime observation, not proof of all possible schedules.

Raw xcodebuild logs, xcresult, exported attachments, exact generated/source/project files, toolchain/device inventory, all compiled resources and their hashes are retained even on failure. The verifier rejects any incomplete run and binds every exact test name to passed XCTest output. No automatic retry or test repetition is enabled.

The previous helper/resource preflight remains a separate accepted result. This lane makes no claim about full Settings persistence/relaunch, the whole LiveContainer target's resource packaging, P1 recovery/layout/Copied feedback, Objective-C guest-launch alert races, physical keyboard/IME behavior, runtime plural-count combinations, P0/P1/P2 combined integration, a signed IPA or device acceptance. Existing OS-owned alarm/notification/AppIntent text limits also remain.

## Diagnosed alert delegate correction

Run 39012547539 built the fixture and verified its compiled resources, then all seven methods crashed before observations on UIKit's `UIAlertController` presentation-delegate assertion. The production successor removes only the unsupported delegate assignment, conformance and callback. Explicit action, binding-stop, managed-dismiss and scene-disconnect completion gates remain. All seven test methods and their assertions are unchanged. Arbitrary external programmatic dismissal outside those managed paths remains unproved.

The result summary now retains the raw native counts and reports command, XCTest and observation gate failures independently, so a crash cannot be described only as missing observations. Both native results and complete observations remain mandatory.

The input field `inheritedSourceManifestSHA256` identifies the earlier dba20ab3 archive for lineage only. The current successor is bound by its exact source commit, full Git tree and complete `sourceFiles` hashes; the inherited archive is not a manifest of eb10cedb.
