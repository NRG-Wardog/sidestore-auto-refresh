# Custom integration boundaries

This is the maintenance map for issue #52. It describes small owned seams, their contracts, and regression evidence. It does not promise conflict-free merges, replace an upstream merge review, or mark the broader modularization effort complete. The October 25 candidate isolates new source/App ID presentation decisions in a pure policy module rather than adding more decision logic to the unified shell. Other areas still have large integration files and should be extracted incrementally, not rewritten during unrelated fixes.

## Source ownership and seams

| Concern | Maintained owner and boundary | Contract to retain | Regression evidence |
| --- | --- | --- | --- |
| Guest input/safe area (#47) | `NRG-Wardog/LiveContainer`: `AppSceneViewController.lcEndGuestEditing`, `DecoratedAppSceneViewController.lcPrepareForGuestActivation`, `MultitaskSupport/LCGuestSafeAreaGeometry.h` | Cleanup affects only outgoing guest ownership; measured insets remain valid across orientation changes. Existing scene-host integration remains the adapter. | `tests/test_guest_input_lifecycle.py`, `tests/test_guest_safe_area_geometry.py`; device keyboard and geometry matrix is separate. |
| Source updates (#30) | `NRG-Wardog/LiveContainer`: `LiveContainerSwiftUI/Utilities/V3SourceAppPresentation.swift`, catalog helpers in `LiveContainerSwiftUI/Views/V3UnifiedShell.swift` | Pure version comparison cannot install anything. Catalog selection passes its source download URL through the existing installer and replacement confirmation. Hidden guests stay hidden. SideStore updates use its canonical installed-app identity. | `tests/test_source_app_presentation.py`; original Nuvio case and data preservation require device verification. |
| App ID expiry (#54) | `NRG-Wardog/SideStore`: `V3BackendCommands.developerAppIDRow`; `NRG-Wardog/LiveContainer`: `V3DeveloperAppID` and Developer Services rows | Bridge only the portal App ID date, not a profile/app signing date. Missing remains unknown. Account-stamped read admission stays authoritative. UI countdown does not poll the portal. | SideStore `tests/appid_expiry/test_appid_expiry_bridge.py`; LiveContainer `tests/test_source_app_presentation.py`. |
| Provider sequencing (#51) | `NRG-Wardog/SideStore`: diagnostic provider plan and authentication-session adapter; LiveContainer unified authentication prompt | Keep ordered bounded policy separate from account mutations. Stop on non-retryable errors, retain cancellation/2FA boundaries, and never log authentication material. This candidate boundary must not be treated as runtime-qualified until native/device tests pass. | Inspect the candidate's provider-sequence tests and run controlled success, timeout, cancellation, cache isolation and 2FA scenarios. |
| ReuseMain (#50) | `NRG-Wardog/SideSign`: opt-in compatibility policy; `NRG-Wardog/SideStore`: installation/signing adapter | Never make main-profile reuse the implicit default. Entitlement/profile compatibility decides admission; incompatible extensions retain per-extension signing or an explicit failure. | Candidate policy and signing tests; installation and actual extension behavior on device remain required. |
| Dependency qualification (#53) | `NRG-Wardog/idevice` and independently owned OpenSSL copies | Keep matching ownership/ABI contracts together. A gitlink or successful compile does not prove provider activation, loaded-image choice, signing or transport behavior. | Exact dependency hashes, regenerated headers, focused ownership tests, native build, then separate runtime qualification. |
| Packaging/size (#55) | `NRG-Wardog/sidestore-auto-refresh`: `package_livecontainer_combined.adapt/verify`, `ipa_size_report.optimize` | Upstream packaging adaptation is fail-closed on changed or duplicated anchors. Compression never removes resources, architectures, entitlements or signatures. Archive verification runs after the transformation. | `tests/test_combined_packaging.py`, `tests/test_ipa_size_report.py`, `tests/test_custom_integration_boundaries.py`. |
| Source provenance | Builder maintained-entry/source contracts and the seven maintained repositories | Runtime code belongs in its maintained repository, not regenerated from historical patch templates. Pin exact reviewed owner commits. Preserve immutable historical manifests as evidence. | Existing maintained-entry and candidate provenance checks. Do not rewrite frozen migration hashes to make intentional runtime edits look unchanged. |

Paths in the first six rows are relative to the named source repository, not this builder repository. Candidate tests are additions on the integration branches until merged. No table row claims the feature is already released or accepted on a device.

## Representative upstream integration exercise

Run:

    python3 -B -m unittest discover -s tests -p test_custom_integration_boundaries.py -v

The exercise uses the existing synthetic upstream packaging fixture, not a copied production implementation. It makes a benign upstream change outside the integration anchors and runs the actual `adapt` function. The new upstream content must survive, the custom provenance/signing/intent boundaries must remain, and adaptation must be deterministic. Changing or duplicating a required upstream anchor must reject the candidate. Packaging custom behavior must not need to be copied or rewritten to accommodate the benign change.

This is a representative adapter contract test, not an actual merge of an upstream release, a shell execution, or an IPA/device test. A genuine upstream update still requires source review, native compilation, final-artifact checks and device validation.

## Integration procedure

1. Identify which maintained owner changes. Compare exact old/new source revisions and list affected seams from this map.
2. Keep independent policy decisions testable with synthetic, non-secret inputs. Place UIKit, portal, signing and transport side effects behind the smallest practical adapters.
3. When upstream changes an adapter input, update that adapter and its contract tests together. Do not broaden matching to silently accept unknown upstream shapes.
4. Preserve custom behavior not touched by the update. Run focused tests for changed contracts, then one coherent native build rather than a separate build per issue.
5. Record the exact multi-repository candidate tuple and artifact hashes. Inspect the actual packaged code/resources and distinguish source tests, native compilation and device observations.
6. Keep device-dependent issues open until their acceptance matrix is satisfied. Publish no claim of complete modularization or guaranteed merge compatibility from this first increment.
