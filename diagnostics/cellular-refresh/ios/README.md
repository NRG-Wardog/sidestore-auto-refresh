# Lifetime Probe iOS source

This is an ordinary own-sandbox SwiftUI diagnostic app. It records evidence about itself and makes no installation or renewal decision. Native compilation and physical-device execution are **NOT RUN** in this environment. The supplied source is not a built IPA or an Xcode project.

## Build on the setup computer

1. Create an Xcode **iOS App** project with Swift and SwiftUI. Use iOS 15 or later and Swift 5 language mode. In Swift Compiler — Concurrency, set **Default Actor Isolation** to **nonisolated** if the setting is available; this source explicitly isolates its UI and moves file hashing off the main actor. Give the app a stable, unique bundle identifier and explicit marketing/build version values. Apple's [build-settings reference](https://developer.apple.com/documentation/xcode/build-settings-reference) describes `SWIFT_DEFAULT_ACTOR_ISOLATION`.
2. Replace the generated app entry point with `LifetimeProbeApp.swift`. There must be exactly one `@main` app in the target. Remove the generated unused `ContentView.swift` if desired.
3. Use the new project's generated Info.plist, or set `GENERATE_INFOPLIST_FILE=NO` and point `INFOPLIST_FILE` to the supplied `Info.plist`. Do not add that plist to Copy Bundle Resources. The supplied file uses Xcode's usual bundle/version build-setting substitutions.
4. Use the same legitimate signing identity and app identity for the experiment. Build the baseline in **Release** configuration, or disable `ENABLE_DEBUG_DYLIB` for this target. Otherwise Xcode may put the app's code in a separate `.debug.dylib` and leave the hashed executable as a loader stub; see Apple's [build-product layout documentation](https://developer.apple.com/documentation/xcode/understanding-build-product-layout-changes). No additional capabilities, entitlements, background modes, VPN, App Groups, URL schemes or profile-import handlers are required by this source. Build and test first; retain the exact signed baseline archive for comparison.
5. Initial installation may use the setup computer. The experiment's runtime controls must independently establish when the computer/USB is absent and Wi-Fi is off; the probe does not control or certify them.

The code uses public CryptoKit, Foundation, SwiftUI, UIKit, Network and UniformTypeIdentifiers APIs. `UTType(filenameExtension: "mobileprovision")` only queries type metadata. The app does not register itself as a profile handler or read arbitrary imported files.

## Capture and export

Launch the app, optionally enable the bounded foreground network-path observation, and tap **Capture receipt**. Tap **Share JSON…** to choose a destination yourself. JSON exports are cached in the app's own container and may be purged by the OS, so retain the baseline externally before proceeding. Sharing is user initiated.

The first successful capture creates a 32-byte random canary in the app's Application Support directory. Later captures hash the existing bytes. The app never replaces an existing malformed or unreadable canary automatically. Reinstalling in a way that deletes the app's data will normally create a different canary; preserve the original app/data for an update experiment. A canary match is evidence of those bytes' continuity, not a full audit of every user file or proof that no backup was restored.

Capture twice in the same process to check that `launch_id` and `canary_sha256` remain stable. A genuinely new process should get a different `launch_id`, while a nondestructive relaunch should retain the canary. These are device checks to perform, not results already obtained. Simulator observations do not establish signed-device behavior.

## Receipt schema, version 1

| Field | Meaning |
|---|---|
| `schema_version` | Integer `1`. |
| `captured_at_utc` | ISO 8601 UTC wall-clock time sampled after file hashing. |
| `uptime_seconds` | `ProcessInfo.systemUptime`, sampled adjacent to wall time. |
| `boot_epoch_estimate_utc` | Capture wall time minus uptime; an estimate, not reboot or clock attestation. |
| `launch_id` | Random UUID held once per running process. |
| `bundle_id_sha256` | Lowercase SHA-256 of the UTF-8 bundle identifier. |
| `version_sha256` | Lowercase SHA-256 of UTF-8 `CFBundleShortVersionString + "\n" + CFBundleVersion`; no other separator or trailing newline. |
| `executable_sha256` | SHA-256 of the app's executable file on disk, read in chunks. This is not its loaded memory, whole bundle, separate debug dylib, other frameworks/extensions, or Apple's CodeDirectory hash. |
| `embedded_profile_sha256` | SHA-256 of own `embedded.mobileprovision` bytes, or explicit JSON `null` when absent. No CMS parsing, signature authentication, expiry or revocation check is implied. |
| `canary_sha256` | SHA-256 of the persistent 32-byte own-container canary. The canary bytes are not exported. |
| `platform.system_version` | Public `UIDevice.current.systemVersion`. Exact build/model are recorded separately by the operator; no UDID is collected. |
| `canary_was_created` | Whether this capture created the canary. Useful for detecting a missing baseline/data reset. |
| `uti_observation` | Result of the filename-extension type query; no installation authority implied. |
| `network_observation` | Optional first `NWPath` callback, or nominal two-second timeout. Scheduling/suspension can delay completion. No request is sent. Does not establish radio state or the route used by another process. |
| `limitations` | Interpretation limits retained in every receipt. |

The probe deliberately omits raw bundle identifiers, profile contents, filesystem paths, Apple account identifiers and device identifiers. Hashes of predictable values remain linkable and are not anonymization. A receipt is an untrusted self-report, not a signed attestation.

## What a comparison can establish

Use the companion profile preflight, receipt verifier and separate operator evidence record. A standalone probe receipt concerns **only the probe**. It does not prove renewal of SideStore or of a combined LiveContainer host. Those targets need their own actual-host observation; do not label probe results as host results.

For profile-only renewal, the executable and embedded-profile hashes may correctly stay identical. A separately installed system profile can have a different effective lifetime without modifying either file. The decisive experiment needs a fresh launch after the old expiry, trustworthy controls for real elapsed time and intervening changes, and whatever effective profile-store evidence is available. For full IPA replacement, changed signed bytes require a different acceptance comparison. The app does not inspect the system profile store, all extensions, another app, installed-profile eligibility or future launch validity.

Keep the original data and signing artifacts. A failed capture provides no complete receipt and must not be treated as a successful launch/renewal result. No private API, install request, profile-store mutation, automatic uninstall, radio toggle or network-setting change is implemented here.

## Verification status

- Source and schema review: performed in this workspace.
- Info.plist parsing: performed with Python `plistlib` after creation.
- Swift/iOS build, signing, runtime permissions, share sheet, process/canary behavior, real cellular route and lifetime experiments: **NOT RUN**.

The absence of an Apple SDK/native toolchain prevents compilation here. These files are diagnostic source for a reviewable device experiment, not validated production code.
