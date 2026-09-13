# Issue 25 executable rendering evidence

`scripts/run_issue25_rendering.py` builds fixture applications for the iOS simulator
from the **generated** `LCGridAppCell.swift`, `LCAppBanner.swift`,
`LCAppBannerView.swift`, and layout enum. The UIKit/SwiftUI rendering components
are production source. App models and the action controller are controlled fixture
dependencies; the real Grid controller's tap and menu forwarding are executed.

For final v2 package validation, after all host patches:

```sh
python3 scripts/run_issue25_rendering.py --livecontainer LiveContainer --skip-v3-native --output v2-layout-evidence
```

For final v3 validation, additionally provide its exact generated shell:

```sh
python3 scripts/run_issue25_rendering.py --livecontainer LiveContainer --v3-source path/to/generated/V3UnifiedShell.swift --output v3-layout-evidence
```

The standalone `issue25-rendering.yml` investigation workflow validates both
renderers. Without `--v3-source`, it takes the current v3 template when present,
or the explicitly pinned `9d1eed7992694aa0fb9a18742255c21c95b0e697` investigation
baseline when running from v2. Final v2 evidence excludes that historical v3
source; final v3 evidence must pass the generated source explicitly.

Each execution uses a fresh evidence directory. The metadata records the builder
commit, CI run, product scope, source hashes, actual simulator runtime, and result
count. The output includes bounded measurements and screenshots using synthetic
app names only. Simulator fixture binaries are ad-hoc signed and are not product
IPAs or distribution artifacts.

## What is executed

- The defective Grid template from full builder commit
  `7d8ae12905f8baa6e0ecc4dbdd3f25e2aa0e43fa`. A passing *baseline expectation*
  requires a measured geometry/visibility failure with non-empty input, not merely
  a missing icon or a source marker.
- The corrected generated Grid on phone and tablet simulators, across saved Grid
  cold process launches, preference changes, label visibility, dynamic text,
  window widths, repeated List/Grid/Compact List changes, collection replacement,
  reorder, empty state and repopulation. Detached controllers retained by SwiftUI
  are reported separately, not counted as visible cells.
- Positive bounds, visible icon fallback, icon and label containment, row overlap,
  identity/order preservation, action-router parenting, tap forwarding, and menu
  forwarding. Symbol alignment rectangles are used when verifying Auto Layout
  icon dimensions; actual visible bounds must still fit the cell.
- Production banner geometry: the previous 60-point icon in a 56-point compact
  row is measured as a baseline failure, and the corrected 40-point icon is checked
  within the 56-point row. The baseline uses the same root source without invoking
  its new compact-layout method; this reproduces the old geometry, not every old
  controller behavior.
- A separate fallback-contract variant removes only the Grid's iOS 16+
  `sizeThatFits` hook to exercise its intrinsic/preferred sizing path. This is
  **not** proof of execution on iOS 15.
- For v3, the exact `V3InstalledAppsSection` is extracted and instrumented with
  background-only geometry probes. Its native layout, filtering, identities and
  preferences execute unchanged; source hashes distinguish the original section
  from the instrumented test copy. Destinations and operation menus are stubs.

The harness chooses the oldest iOS simulator runtime actually installed and
compiles with an iOS 15 deployment target. Neither compilation nor the fallback
variant substitutes for running iOS 15 on a device/runtime when it is unavailable.

## What remains outside this harness

Full application navigation, UIKit's presented context-menu interaction, guest
execution, hidden-app authentication, the v3 service lifecycle, account/signing,
and physical-device rotation/upgrade are not simulated by these controlled
dependencies. Those have separate tests or belong to the device acceptance
checklist. Marker verification and a successful host build are not described as
rendering proof.
