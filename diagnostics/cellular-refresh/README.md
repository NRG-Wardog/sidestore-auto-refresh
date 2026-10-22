# Cellular refresh evidence diagnostics

These are isolated research tools for [the Phase 2 cellular-refresh plan](../../docs/research/CELLULAR_REFRESH_FEASIBILITY_AND_RECOVERY_PLAN.md). They are **not a cellular installer** and have no production call site. They help distinguish compatible profile preparation, an actual installed mutation, and an observed extension of usable lifetime.

**Validation in this environment:** 51 synthetic Python/OpenSSL tests passed. Native Swift compilation, signing, physical-device operation and every cellular/profile/host acceptance test are **NOT RUN**. No Apple profiles, credentials, pairing records, signed IPAs, device identifiers or private test keys are included.

## Files and responsibilities

| File | Purpose |
|---|---|
| [profile_preflight.py](profile_preflight.py) | Verify CMS signature/content integrity and compare supplied old/new profile metadata with the actual executable signer, signed entitlement values and device grant. |
| [verify_lifetime_receipts.py](verify_lifetime_receipts.py) | Check whether supplied before/after receipts and operator evidence consistently support a profile-only lifetime experiment. |
| [evidence.template.json](evidence.template.json) | Empty operator/provenance record; unknown values are deliberately `null`. |
| [ios/LifetimeProbeApp.swift](ios/LifetimeProbeApp.swift) | A public-API SwiftUI app that records hashes and observations about its own signed app and data container. |
| [ios/README.md](ios/README.md) | Xcode setup, receipt schema, capture/export and native limitations. |
| [test_profile_preflight.py](test_profile_preflight.py), [test_lifetime_receipts.py](test_lifetime_receipts.py) | Synthetic regression tests for tampering, incompatibility and misleading evidence. |

The Python tools use the standard library and an `openssl` executable. They were tested with Python 3.12 and OpenSSL 3.0.13. A platform's bundled LibreSSL or another OpenSSL version may not support every flag; a tool failure is **indeterminate**, not an iOS profile rejection. No network access is performed by these Python scripts.

They are **offline setup or post-run analysis tools**, not runtime dependencies of a compliant refresh. During an accepted complete phone-only run, profile acquisition, required compatibility decisions and installation must happen on the phone; a computer must not participate between those stages. Export evidence for Python analysis afterward. Using a computer to prepare/check inputs before an isolated installation experiment is a legitimate diagnostic control, but does not prove the complete account-to-renewal workflow. The on-device compatibility implementation remains explicit roadmap work in the plan.

## 1. Run the offline tests

From the repository root:

```bash
python -m unittest discover -s diagnostics/cellular-refresh -p 'test_*.py' -v
```

Tests create synthetic self-signed CMS profiles and temporary keys in temporary directories, then remove them. A synthetic CMS signature is useful for testing content integrity; it is not Apple authorization. The tests do not touch a phone, Apple account, installed profile or application.

## 2. Prepare a meaningful baseline

Use a disposable app before testing an important host. Initial build/install/pairing can use a setup computer. The actual refresh experiment starts only after USB/participating computers are absent and Wi-Fi is **Off in Settings**, with cellular enabled and no radio shortcuts or toggles.

Build the Swift probe as described in its [iOS instructions](ios/README.md). Keep the exact signed baseline archive and capture `before.json` on the phone. Use Release or disable debug-dylib mode; otherwise the main executable may be a loader stub. The capture covers one executable file, its embedded profile, its bundle/version identifiers and a persistent own-container canary. It does not hash every framework, extension or user file.

Obtain the old embedded `.mobileprovision`, actual signing certificate and actual signed entitlements from that same signed baseline build during setup. A certificate merely listed in an allowed-certificates array is not necessarily the executable's signer. Keep the target UDID in a private text file so it is not put in command-line arguments. These are private test inputs and must not be committed or attached to public issues.

**Establish the prior effective authorization deadline.** The embedded profile can be older than another compatible profile already installed in the system store. A unique bundle identifier does not rule out a later wildcard profile. The comparison requires one of:

- `compatible_profile_inventory`: a complete relevant profile-store inventory establishes that the old profile's expiry matches the previous effective deadline, considering actual signer, identifiers, device and entitlement compatibility.
- `observed_expiry_failure`: after the real old expiry and a reboot, the same installed app demonstrably fails to launch **before the renewal intervention**. Keep the earlier before-expiry receipt; a separate working broker/helper must perform renewal if the target can no longer launch.

The verifier records the supplied method. It cannot inspect the inventory or attest an operator's statement. If the prior effective deadline is unknown, the result remains incomplete. Merely launching beyond an old embedded expiry is insufficient to attribute added lifetime to the experiment.

## 3. Check old/new profile metadata

Acquire a genuine fresh profile for the existing app identity, actual signer, device and signed entitlement values through the authorized account workflow. Do not change the signing certificate merely to test profile renewal. Each extension needs its own check and outcome. If the new profile does not advance expiry, that input cannot establish added lifetime.

Run with private local paths:

```bash
python diagnostics/cellular-refresh/profile_preflight.py \
  --old /private/test/old.mobileprovision \
  --new /private/test/new.mobileprovision \
  --signer-cert /private/test/actual-signer.der \
  --signed-entitlements /private/test/actual-entitlements.plist \
  --device-udid-file /private/test/device.txt \
  --output /private/test/preflight.json
```

The example paths are placeholders; create/use your private test directory. The signer input can be one DER or PEM certificate. The entitlement input must be a plist of the **actual code-signed claims**, not a desired project entitlement file or the profile's entire allowlist. Optional `--at` accepts an explicit UTC ISO8601 instant for reproducibility; it does not change the phone clock or prove any runtime validity.

The tool checks bounded input/structure sizes, CMS integrity, date ordering/advance, team/application scope, supplied actual signer membership, supplied device authorization, and a conservative subset of entitlement semantics. It supports exact scalar/container values, array subsets and limited trailing wildcards for application identifier/keychain groups. Unsupported semantics stay indeterminate. It does not emulate every platform entitlement rule.

| Result | Exit code | Interpretation |
|---|---:|---|
| `SUPPORTED_BY_SUPPLIED_METADATA` | 0 | Supplied diagnostic metadata passes the implemented checks; it is not installation or Apple trust proof. |
| `INCOMPATIBLE` | 1 | The supplied inputs conflict under the implemented comparisons. Diagnose that exact input/operation, not the network. |
| `INDETERMINATE` | 2 | Required evidence is missing, semantics unsupported, certificate validity uncertain, or a tool/input error occurred. |

**Trust limitation:** `openssl cms -verify -noverify` checks CMS content/signature integrity with the embedded signer but deliberately does not establish an Apple certificate chain. Modern profiles have authoritative DER content inside a further representation; this tool reports that content as **not evaluated**. It does not check revocation, iOS policy or whether the supplied signer/entitlements truly came from the installed executable. Every report states `apple_trust=UNVERIFIED` and `device_validation=NOT_DEVICE_VALIDATED`. A self-signed attacker-created CMS can pass integrity while remaining wholly unacceptable to iOS; the tool never claims otherwise. Apple's [TN3125](https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles) explains the profile representations.

The certificate's `NotAfter` is reported and handled conservatively. It is not used as a universal formula for installed-app lifetime across distribution channels. Profile expiry, certificate expiry, revocation and distribution-specific validation are different questions.

## 4. Execute one actual authority experiment

Choose the relevant procedure in [Part 8 of the plan](../../docs/research/CELLULAR_REFRESH_FEASIBILITY_AND_RECOVERY_PLAN.md#part-8-minimal-proofs-of-concept):

- **RP/misagent:** prove the cold authorized profile service, install each exact compatible profile with durable intent, and read back the result. The current reference is Madeira's profile-refresh implementation. A diagnostic `CopyAll`/stage wrapper and actual SideStore host instrumentation remain specified work, not code supplied by this package.
- **System profile importer:** test whether the exact device/OS offers and accepts a provisioning-profile importer for these bytes. UTI lookup, Files sharing or a configuration-profile installation is not that result. This path remains experimental.
- **Local OTA:** independently test the system installer's acceptance of the actual free-account signed IPA through fully local serving. This is a full-package experiment and needs its own acceptance record; do not feed it to the profile-only verifier.

Do not call an existing whole-profile dump function a redacted logger. Store readback should be reduced locally to exact hash/UUID/identity/date evidence. Do not delete old apps/profiles, revoke certificates, toggle radios or blindly replay an uncertain mutation to obtain a green result. Reconcile a lost acknowledgement first.

For SideStore self-refresh, obtain evidence from the **actual installed SideStore host**. A receipt from the standalone probe or an embedded LiveContainer guest does not prove that host. To instrument a diagnostic host build, reuse the own-bundle capture logic without adding a second `@main`; any such host integration requires its own native build/review and is not included here. Combined-host acceptance concerns the outer installed host and its extensions.

## 5. Wait for the real lifetime boundary and capture again

Do not change the system clock to simulate expiration. Let the established prior effective deadline pass. Reboot after the old expiry plus the chosen margin, unlock, and freshly launch the unchanged target before the new profile expires. Capture/export `after.json`. The default margin in the evidence template is 300 seconds; it is a diagnostic allowance, not a platform guarantee.

A provisioning-profile-store update may correctly leave both the executable and the **embedded** profile bytes unchanged. The verifier expects that for this profile-only experiment. A changed executable, embedded profile, version, identity or canary belongs to a different operation or contradicts the controlled comparison. A recreated canary is also contradictory, even if someone supplies a matching hash. A data canary establishes only the continuity of those bytes; it does not prove that every file survived or that no backup was restored.

Reboot is part of the validation design, not a proposed radio workaround or a requirement to reboot during every production refresh. Apple's [RemoveProvisioningProfile documentation](https://developer.apple.com/documentation/devicemanagement/remove-provisioning-profile-command) warns that enterprise-app usability can persist until restart after a profile is removed. That scoped warning motivates avoiding cached same-session behavior as the only evidence; it is not a universal Personal Team policy assertion.

If feasible, use a separate untreated control with an authorization scope that cannot receive the tested profile, and observe its actual expiry failure. An explicitly surviving control makes the causal baseline incomplete. Absence of a control is reported as a limitation. Exercise each relevant extension independently.

## 6. Fill the evidence record and compare

Copy `evidence.template.json` into your private test directory. Supply exact device model, OS build, source revision and selected installation backend. Set `target_kind` to `probe`, `ordinary_app`, `standalone_host` or `combined_outer_host`. Select the supported `baseline_authorization_method` and fill the operator controls only from observations.

The required control `baseline_effective_expiry_matches_old_profile` is not satisfied by a newer UI date or a unique bundle ID. The other controls record Wi-Fi off before/throughout the run, cellular use, no toggles/PC/USB/installation relay, free Personal Team/no jailbreak, unchanged automatic time, post-expiry reboot/first unlock/fresh launch, no other signing changes and provenance of the signed build used for preflight. A false control is a contradiction; a missing/null control is incomplete evidence.

For profile-store readback, set `observed=true` only when the actual installed profile was read through an authorized mechanism. Supply its complete CMS hash and observation time. A matching profile hash must equal `preflight.new.cms_sha256`; a local downloaded file is not store readback. Without matching readback, a supportive lifetime observation retains a limitation about attribution to the particular submitted profile.

```bash
python diagnostics/cellular-refresh/verify_lifetime_receipts.py \
  --before /private/test/before.json \
  --after /private/test/after.json \
  --preflight /private/test/preflight.json \
  --evidence /private/test/evidence.json \
  --output /private/test/lifetime-comparison.json
```

| Result | Exit code | Meaning |
|---|---:|---|
| `RECORDED_OBSERVATIONS_SUPPORT_PROFILE_ONLY_LIFETIME_EXTENSION` | 0 | Supplied observations are internally consistent with this scoped claim; human review and unverified input provenance remain. |
| `EVIDENCE_INCOMPLETE` | 1 | Missing or ambiguous baseline, controls, timing or compatibility prevents the claim. |
| `RECORDED_OBSERVATIONS_CONTRADICT_REQUESTED_EXPERIMENT` | 1 | Inputs show an incompatible transition/control or inconsistent observation. |
| Invalid input/tool I/O | 2 | No complete comparison result; sanitized diagnostic on stderr. |

Creation, baseline, readback, boot-estimate and capture chronology are checked. Changed observed OS versions, an explicitly surviving untreated control and unavailable profile hashes remain incomplete; Wi-Fi-path observations or a recreated data canary conflict with the claimed controlled run. An NWPath observation cannot itself certify physical radio state, and uptime/wall-clock arithmetic cannot attest a reboot. These checks expose contradictions, not manufacture hardware attestation.

The verifier handles **profile-only** experiments. It rejects `full_reinstall` as the operation. Full IPA and full host replacement require intended package/signing identity, actual installed-state and new-host observations under their separate plan. Neither is a prerequisite for a useful, valid profile-only result.

## Evidence and privacy limits

Reports are unauthenticated self/operator observations. No JSON output from this package proves Apple trust, independent device identity, network path, every app's validity, future reliability or lifetime through the whole new expiration window. A successful launch past the old effective deadline demonstrates a bounded observed extension under its controls, not a guarantee against subsequent revocation.

`--output` uses an atomic replacement with mode 0600. Default stdout is redacted JSON; redirect it only to an appropriate private location. Hashes are identifiers and remain linkable. The iOS app shares only after the user selects the share action; no messages or uploads are sent automatically. Do not publish the private inputs, broad device logs or account frames from demonstration videos.

The normal repository test suite is separate from these diagnostics. The Phase 2 workspace reported 81 tests: 39 passed, 41 skipped, 1 pre-existing error in the full-history privacy scan because a partial clone lacks historical blob `7166d4cacc8d7f8d99d70ab9443ca1602baf7e33`. The privacy gate was not weakened. Native compiler/runtime/source-fixture skips are not validation passes. See the draft PR's final validation summary for the exact recorded result.
