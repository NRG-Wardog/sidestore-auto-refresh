# Same-session language switching candidate

This workflow extends the successful issue56 native source/build/package graph.
It retains every prior required suite and the safe size pipeline, adding a
mandatory source-owned live language transition suite. Failure blocks candidate
packaging and uploads early diagnostics. No public release is published.

Before dispatch, the coordinator must populate `ci/issues-native/candidate-graph.json`
with the exact reviewed LC commit, tree, and every changed blob. The previous
issue56 LC pin is not evidence of this fix and is intentionally not rewritten here
while the app implementation is pending. New tests must run against the acquired
source owner, not a sibling or historical baseline.

The native receipt and delivered artifact must bind the language-switch report.
Foundation state-machine tests alone are not UIKit/SwiftUI live-layout evidence.
A simulator harness, if provided, must operate the same live view/session across
Hebrew to English/Russian/Chinese and reverse without recreating app state, and
record its simulator/SDK identity. Physical-device rendering remains separate.

After this native candidate is green, use the already approved pinned Google
Zopfli source for a quick five-iteration copy-only pass, not the prior two-hour
sweep. Bind the new immutable input artifact IDs, hash and size; never reuse the
previous issue56 IPA or receipt as if it contained this language fix.

## Installed simulator selection policy

`installed-runtime-selected-sdk-family-v1` reads the SDK version and path from
`xcrun --sdk iphonesimulator` under the workflow-selected Xcode. It requires an
installed available iOS16+ runtime in exactly that SDK's major/minor family,
selects the highest patch (runtime identifier breaks ties), and retains the
existing supported-iPhone compatibility filters. Missing matching runtimes fail
closed; no runtime download, execution-failure fallback or retry is introduced.
The native receipt records the SDK version/path, runtime version/build/identifier,
policy and hash-bound inventory. Native and independent packaging verification
replay that selection and bind it to the actually executed simulator identity.
The twelve same-session transitions, four sheets, source pins, deployment targets,
assertions and boot/execution/cleanup budgets are unchanged.
