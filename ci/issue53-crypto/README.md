# Unchanged-pin synthetic crypto qualification probe

This is a **single-library test harness**, not a dependency upgrade or proof that
an IPA's two OpenSSL copies work together. It does not execute production ZSign,
SideSign/CodeSignKit, CMS/Mach-O signing, Apple authentication or a physical device.
The production host uses `PKCS12_parse` and loads `legacy`; this probe exercises
those public OpenSSL APIs with explicit `default` **and** `legacy` activation.
It does not prove the application's provider initialization order is correct.

## Current evidence and platform boundary

The verified experimental IPA from run `40213898605` contains host framework
version `3.3.3001` and embedded SideStore framework version `3.6.2000`, with distinct
binaries. The host gitlink remains `623c84da314e85363236507ca38a4bde65df21c3`.
Framework packaging versions are not substituted for `OpenSSL_version()` output.
The locally available library is system OpenSSL `3.5.7`, neither shipped copy.
The local smoke therefore validates this harness only.

An iOS arm64 or iOS simulator framework cannot be executed as a native macOS
command-line dependency. This runner rejects both; do not hide platform mismatch
with a different system library. Before a pinned run, inspect the exact archive's
XCFramework platform metadata. A matching macOS slice may use this executable
runner, but its result still does not qualify the iOS slice. If only iOS slices
exist, use a separate owned simulator/device app runner rather than claiming a
macOS pass. The bounded pinned workflow below installs no additional software.

## Invocation

Use an already reviewed compiler; Linux smoke additionally uses installed
`cryptography`. macOS uses built-in Apple Security instead:

```sh
python3 -B ci/issue53-crypto/test_controls.py
python3 -B ci/issue53-crypto/run.py \
  --library /absolute/path/to/libcrypto.so.3 \
  --headers /absolute/path/to/include \
  --sha256 VERIFIED_BINARY_SHA256 \
  --expected-version 'EXACT OpenSSL_version OUTPUT' \
  --provider-module /reviewed/path/to/legacy.so \
  --provider-module-sha256 VERIFIED_MODULE_SHA256 \
  --output /new/path/receipt.json --system-smoke
```

For a genuine reviewed macOS framework, supply its real framework executable,
matching headers and exact version/hash; explicitly choose a reviewed module or
`--builtin-providers` (an empty module search directory); omit `--system-smoke`. This flag is a
label, not permission to claim shipped-app qualification. Every receipt sets
`shipped_app_runtime_qualified`, `device_executed`, `coexisting_images_tested` and
`provider_module_provenance_qualified` to false.

The runner checks the binary hash before compilation, compile-time header
version, actual `dladdr` image path/hash and runtime version afterward. It removes
inherited loader/provider environment overrides and disables OpenSSL config
loading. Module search is explicitly restricted to a temporary directory containing
only the supplied hash-verified legacy module, or no modules in built-in-only mode.
Unknown installed-default modules are never intentionally selected. Actual provider dispatch images are checked against the supplied module/library
bytes. Transitive system dependencies remain outside the independent provenance
claim. Pinned macOS runs additionally bind the supplied header inventory and
inspect actual Mach-O build commands, as described below.
No success receipt is written after a failure or to an existing receipt path.
Compilation and execution have bounded timeouts.

## Executed synthetic checks

- Explicit default/legacy activation and legacy RC2 availability.
- Modern and legacy PKCS#12 export, DER decode, parse and exact key/certificate
  equality; wrong password rejection; changed MAC rejection.
- Fresh temporary RSA key and self-signed certificate, never a user's material.
- RSA/SHA256 signature checked by Apple Security on macOS or a separate Python
  `cryptography` consumer for Linux smoke, including altered-message rejection.
  The Linux path is independent API consumption, **not** a claim of an independent
  cryptographic implementation.
- Only public key/message/signature reach temporary disk; private key and PKCS#12
  stay in memory. Temporary outputs are deleted. The retained receipt has hashes,
  versions, fixed case labels and explicit limitations, not keys or passwords.

## Remaining qualification

Preserve pins and both copies. First bind each exact platform archive and any
external provider modules. Then run separately, record loaded images, test actual
application initialization and CMS/signing consumers, and finally test coexistence
and device install/refresh. Stop on any image/hash mismatch, provider failure,
PKCS#12/signature regression or platform mismatch; never substitute another
library to obtain a green result. A future upgrade is a separate experiment.

## Exact pinned macOS experiment now prepared

Both unchanged inputs have real `macos-arm64_x86_64` and
`ios-arm64_x86_64-simulator` slices. This removes the earlier metadata uncertainty;
it does not make the iOS slice executable as a macOS command-line binary.

- Host source commit `623c84da314e85363236507ca38a4bde65df21c3`, tree
  `21bc09387e7f91a46eb8bd26587ce1d052277801`; actual macOS library SHA-256
  `6c35a25d7af774b6530cf8b38040f541f14d212c38513cbf1d588e95ab20c647`.
  Header runtime string: `OpenSSL 3.3.3 11 Feb 2025`.
- Embedded release archive version `3.6.2000`, unchanged declared SHA-256
  `37846a8bd302cb2443eff47f1045ab844d0cd40bf82cc6159cfad9aa5c3eff9e`;
  actual macOS library SHA-256
  `e705420af1b8bd3376dd50ce4f993dc958a29543778cd90800f4433056228787`.
  Header runtime string: `OpenSSL 3.6.2 7 Apr 2026`.

`pinned-inputs.json` plus the two header inventories bind these binaries, plists
and all 140/144 supplied framework header files. `acquire.py` materializes only
those reviewed paths, never executes upstream build scripts and never changes
product dependency pins. The isolated workflow uses existing macOS 26/Xcode
26.4.1 tooling, a 15-minute job bound, separate processes for each library, and
uploads only receipts/logs. It installs no software and builds no application.

For these runs the runner checks actual Mach-O build platforms with `vtool`,
not just Info.plist; verifies the complete supplied OpenSSL header inventory;
and resolves provider dispatch function pointers with `dladdr`, binding their
actual image bytes to the requested library in built-in-only mode. The separate
signature consumer is Apple's Security framework (`SecKeyVerifySignature`), not
another OpenSSL verification call. System SDK/transitive dependency provenance,
iOS execution, initialization inside the production apps and coexistence still
remain outside this experiment. A green result is only a pinned **macOS synthetic
crypto** pass, never completion of all #53 runtime/device qualification.

## Actual consumer and coexistence qualification extension

The next experiment keeps the same two library inputs and adds:

- Four fresh processes: host-first and embedded-first, each under `RTLD_LOCAL`
  and `RTLD_GLOBAL`. Every handle's version, PKCS#12/signing/provider function
  binding and default/legacy provider dispatch image must stay on its exact
  requested library. Global-namespace resolution is recorded, not mistaken for
  proof of iOS's production loader behavior. Unexpected bindings are quarantined;
  all four cases are collected even after one fails.
- PKCS#12 malformed/truncated DER rejection and synthetic private-key/certificate
  PEM/DER roundtrips. Keys remain ephemeral and never enter evidence artifacts.
- Actual maintained `ZSign::SlotBuildCodeDirectory` and
  `ZSign::SlotBuildCMSSignature` from the exact LiveContainer source tree in
  `zsign-source.json`, compiled unchanged with their real common implementations.
  The harness does not copy/reimplement these signing algorithms.
- Independent Python checks of every synthetic code-page hash, CodeDirectory
  metadata, CMS slot framing, detached message digest, signer issuer/serial,
  canonical signed attributes, Apple CDHash plist and digest-agility attribute.
  Truncated code/directories/CMS/wrapper must fail closed.
- Independent Apple Security verification of the actual CMS signed-attribute
  signature, rejection after message alteration, and confirmation that the
  synthetic leaf is **not trusted**. Trust-network fetching is disabled and no
  trust anchors or stores are changed.

The production signer only accepts recognized Apple issuer-name hashes. The
fixture therefore uses a PUBLIC CA subject as the issuer name but signs its leaf
with an unrelated ephemeral key. This is intentionally an invalid/untrusted Apple
certificate chain, not a real signing credential or an Apple-trusted signature.
The test exercises cryptographic CMS creation and attribute correctness only.

The Linux smoke compiled these exact source files, generated actual CMS and code
directories, passed the independent parser and a separate cryptography verifier.
That smoke uses system OpenSSL 3.5.7 and is not a pinned-platform result. The
workflow must run the native pinned tests before any such result is claimed.

No new OpenSSL release is selected by these baseline passes. A later supported
3.5 LTS/provider-enabled option or proposed 3.6.5 wrapper still needs its own
immutable inputs, provider-enabled artifacts and the same qualification. No
product pin, framework count or dependency packaging changes here.
