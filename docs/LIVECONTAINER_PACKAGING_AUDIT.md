# LiveContainer Packaging Audit

Date: 2026-09-07. No CI build dispatched during this investigation.

## Evidence

Downloaded official release 3.8.0 for comparison. This is an upstream reference,
not yet the exact IPA the user successfully installed. No same-account signing
A/B experiment has been completed.

| IPA | SHA-256 | Registration targets before reuse |
| --- | --- | --- |
| Official plain LiveContainer | See local inventory | 4 |
| Official LiveContainer+SideStore | 97dc0fd2202fd4460efcab389943b8d5fdbb4988efff76b116b92b84a4662425 | 5 |
| AutoRefresh | 948b8ae04a0fe6d7bc275d16771ea03a0597ddd68f25deb03fcbb7b62cbe633e | 4 |

`scripts/audit_ipa_signing.py` inventories every app, extension, and framework
Info.plist, provisioning-file presence, and Mach-O XML entitlements. Complete
local output is `.audit/livecontainer-signing-inventory.json`. DER entitlement
presence is recorded; DER contents are not decoded. ZIP members are read directly.

## Registration behavior proven from source

Source: https://github.com/nab138/isideload/blob/3d42025/isideload/src/sideload/application.rs
and sibling `sideloader.rs` and `bundle.rs` at that revision.

1. Sideloader rewrites the main identifier to `<original>.<team>` and preserves
   extension suffixes under it.
2. Registration considers the main bundle and direct PlugIns bundles, excluding
   frameworks from App ID registration.
3. Exact matches in `list_app_ids` are reused. There is no wildcard matching in
   this selection. Shared prefixes do not collapse registration targets.
4. The error prints the number of unmatched identifiers, compared against
   Apple's `available_quantity`. Therefore four targets can require zero to
   four NEW registrations, depending on the returned list.
5. SideStore-in-LiveContainer recognition requires a framework whose identifier
   is `com.SideStore.SideStore`. It changes certificate injection and app-group
   selection, but does not change this registration count.

## Confirmed package defects

Our workflow builds SideStore separately but never inserts it into the IPA.
Official combined packaging converts its executable using dylibify and embeds
it as `Frameworks/SideStoreApp.framework`. Our package lacks that framework,
its nested dependencies, and the relocated LiveWidget extension.

Official combined packaging adds `ALTAppGroups`, the `sidestore` and
`sidestore-com.kdt.livecontainer` URL schemes, supported intents and activities,
settings entries, and copied/rewritten intent metadata. Our package skips these.

Official host and extensions have embedded entitlement signatures. Our unsigned
host and all three extensions have no LC_CODE_SIGNATURE. Official host and
LiveProcess include application groups, keychain groups, HealthKit, increased
memory and get-task-allow entitlements; share/launch extensions carry app groups.
The signing placeholders are upstream distribution data, not user credentials.
Neither compared distribution contains embedded.mobileprovision in these bundles.

The automation patch adds processing/background identifiers and AlarmKit code;
it adds no extension target or entitlement file. Source-version differences
must not be attributed to automation without a same-revision comparison.

All three extensions exist in official plain AND combined 3.8.0:

| Extension / executable | Bundle identifier suffix | Extension point |
| --- | --- | --- |
| LiveProcess | .LiveProcess | com.apple.ar.viewer |
| ShareExtension | .ShareExtension | com.apple.share-services |
| LaunchAppExtension | .LaunchAppExtension | com.apple.share-services |

Main identifier is `com.kdt.livecontainer`; extensions use that prefix.
All three use CFBundlePackageType XPC!; main uses APPL. No evidence supports
blindly stripping these extensions as the remedy.

Upstream packaging source at the pinned host revision:
https://github.com/LiveContainer/LiveContainer/blob/12377cf3b91d51739a33f14a302e5f522b238593/.github/build_github.sh

## Corrective direction and unresolved signing failure

Reproduce upstream combined packaging using our patched SideStore product,
including dylib conversion, entitlement preparation, metadata and widget
relocation. Retain upstream identifiers and let iLoader apply its team rewrite.
Validate the embedded SideStore engine and signing metadata, not just ZIP CRC
or unrelated log strings. Do not claim installability based on compilation.

This repairs a proven packaging defect. It does NOT prove the cause of the
reported working-versus-new signing difference: registration precedes the
special certificate/group handling, and the plain reference has the same four
base IDs. Obtain the exact working IPA and iLoader options and compare normalized
IDs and Apple's returned ID list in the same session. Actual new-registration
counts for that reference remain unknown. Free-team installation after repair
remains unverified; no paid account is introduced as a requirement.

## Combined packaging correction

`package_livecontainer_combined.py` executes an adapted copy of the pinned
`.github/build_github.sh`, preserving its plist, dylibify, widget, and metadata
transformations. Intentional deviations:

- Supply the locally compiled PATCHED SideStore IPA instead of downloading nightly.
- Supply the built host through an archive-shaped staging directory. Deployment
  targets and bundle IDs are not rewritten by this adapter.
- Install ldid in CI once; check its availability in the upstream script.
- Enable fail-fast shell behavior, omit nonexistent zsign-cache cleanup, and fix
  upstream's `payloadlc/Payload` cleanup path to the actual fresh Payload folder.
- Restore host, LiveProcess, share and launch XML entitlements using the pinned
  source templates and upstream placeholder team AAAAA11111 after unsigned builds.
  Apply the pinned widget entitlement template. These are resignable signatures,
  not Apple account provisioning profiles.
- Fail package publication unless SideStore is MH_DYLIB, iLoader's SideStoreLc
  recognition identifier exists, all four extensions and their app groups exist,
  URL/group/intent metadata and automation code exist. Compare embedded SideStore
  resource/dependency bytes against the local build product (excluding relocated
  plugins, converted executable and replaced signatures).

All nested SideStore resources/dependencies are copied by upstream's complete
SideStore.app move. The widget retains its dependencies. Host framework intent
metadata, including our AlarmKit intent, remains in its original framework.
The generated JSON contains every bundle's full Info.plist and XML entitlements.
It reports five registration targets BEFORE reuse, not five newly allocated IDs.

## Validation result for the correction

- Local suite: 14 tests, 12 passed, two skipped (Swift compiler unavailable).
- Actual pinned upstream script adaptation: passed; former defective IPA rejected.
- Single CI attempt: https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34090510504
- Host compilation succeeded; embedded SideStore dependency resolution failed
  with OpenSSL cache entry already existing, exit 74. Packaging never executed.
- Resolver/build derived-data paths have since been aligned locally/source-side.
  This follow-up is not yet CI verified. No second build dispatched.
- No corrected combined IPA is available yet. Final artifact structural diff,
  actual iLoader recognition, and same-account signing remain unverified.
