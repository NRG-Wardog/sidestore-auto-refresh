# Same-session language switching candidate

The supported `cold-vpn-native.yml` entry runs the source-owned live language
transition suite as a mandatory step before candidate packaging. Failure blocks
packaging and uploads bounded diagnostics. The separate language-only native
build recipe has been retired; it duplicated the earlier build graph.

The component revisions come from `ci/issues-native/candidate-graph.json`.
Tests run against the acquired source owner, not a sibling or historical baseline.

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
