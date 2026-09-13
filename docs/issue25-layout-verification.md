# Issue 25 layout candidate verification

This is a presentation-only correction. Authentication, transport, databases,
guest files and preference values are outside this change. Publication and
the reporter's device confirmation remain pending.

## Baselines and observations

- v2 starting builder: `7d8ae12905f8baa6e0ecc4dbdd3f25e2aa0e43fa`.
- v3 starting builder: `9d1eed7992694aa0fb9a18742255c21c95b0e697`.
- Both use LiveContainer `12377cf3b91d51739a33f14a302e5f522b238593`.
- Issue 25 and all three attached screenshots were inspected. The blank Apps
  screenshot still reports **12 Apps in total**. Settings shows Grid with labels
  enabled. The About screenshot identifies upstream `12377cf`, not a builder SHA.
- Both combined lines use the same `LCGridAppCell` controller representable.
  Its original root has neither an intrinsic size nor a preferred controller
  size, and does not implement the iOS 16 sizing hook. The simulator harness
  compares this exact original renderer with the generated corrected renderer;
  the measured zero-height reproduction below confirms this defect.
- The original Compact List requests 56 points while keeping its 60-point icon
  and 88-point root intrinsic size. The correction makes the root and icon match
  the requested compact presentation and hides secondary visual metadata only.
- v3's SideStore-installed section is a different, native SwiftUI renderer. It
  retains content-derived height and existing identities/actions. Standalone
  SideStore uses a UIKit collection layout with explicit item sizes, not this
  controller representable; no standalone source change is made.

## Measured reproduction

The first simulator run,
[34761678177](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34761678177),
preserved before/after screenshots and JSON. In its 390-point-wide phone viewport,
the original renderer received six app models and created six controllers, but
all six root bounds had **height 0**, and all six preferred sizes were `[0, 0]`.
The same zero-height failure occurred on the tablet simulator. Thus the defect
is a missing sizing contract, not lost app records or a filter dropping models.

With the correction, the same roots measured **103 points high** with labels at
the default text size; saved-Grid cold launches passed on both simulators. This
height is computed from the icon, spacing and scaled caption font, not an
arbitrary fixed row height. Labels-off height is 68 points, and the largest
accessibility category produced 165-point cells. All tested width/category
combinations from 320 through 1024 points retained six non-overlapping cells.
The oldest installed simulator runtime was iOS 26.2; compiling for deployment
15.0 does not establish execution on iOS 15.

That first *whole suite* was not green: its banner measurement incorrectly
equated an SF Symbol image's bounds with its Auto Layout alignment rectangle,
and controller traversal counted a retained cell after a collection replacement.
Those test-instrumentation distinctions are being checked separately; the
zero-height before/after measurements remain evidence, not a claim that the
entire matrix or a physical device passed.

## Persistent acceptance checklist

| Requirement | Implementation | Executable test | Evidence | Remaining limitation |
| --- | --- | --- | --- | --- |
| Visible Grid from non-empty models | Intrinsic/preferred sizing and iOS 16 proposal sizing | Simulator before/after controlled models | Pending simulator run | Reporter devices pending |
| iOS 15 support | Intrinsic/preferred contract, availability-gated iOS 16 hook | Compile simulator target with deployment 15 | Pending CI | Runtime 15 only if installed in CI |
| Labels and accessibility | Scaled caption metrics, two lines; full accessible name even with labels hidden | Simulator label/category matrix | Pending CI | VoiceOver device check |
| Narrow/wide/resized windows | Adaptive columns, proposal width respected, content-derived height | Simulator phone/tablet/window widths | Pending CI | Physical rotations/Split View |
| Missing icons | Visible `app.fill` symbol | Simulator nil-icon fixture | Pending CI | None beyond device check |
| Launch/context actions | Existing child action router and containment unchanged | Simulator forwarding spies | Pending CI | Existing real guest action device check |
| Layout/saved preferences/data changes | Existing `LCAppLayoutStyle` and `LCShowAppLabels`; no model mutations | Simulator transitions, relaunch, insert/remove/filter fixtures | Pending CI | Real account/guest integration |
| Compact/List parity | Compact root 56/icon 40; List root 88/icon 60 retained | Generated component measurement | Pending CI | Subjective redesign deliberately excluded |
| Patch safety | Pinned source, transactional staging, replay hashes | Repository idempotence, drift and transaction tests | Pending complete suite | None |
| v3 installed-app section | Existing native SwiftUI renderer retained unless tests show a defect | Production section rendering fixture | Pending CI | Real installed-app integration |
| v2 and v3 artifacts | Separate builder branches and draft candidates | Both full combined pipelines | Recorded by final candidate provenance | Physical-device acceptance pending |

## Device acceptance

On each candidate, retain the existing installation and data. Switch List →
Settings → Grid → Apps, repeat with labels off, then relaunch with Grid saved.
Check visible and hidden/locked guests, search/order, long names, largest text,
phone/tablet rotation and Split View, tap-to-launch, context actions, and List /
Compact List transitions. On v3, repeat with both guests and SideStore-installed
apps. Do not reset preferences or delete app records to make the test pass.
