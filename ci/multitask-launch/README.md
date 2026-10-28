# Focused multitasking launch qualification

This focused job is not an app build or a private-extension/device integration
test. It executes the candidate's actual extracted Objective-C launch boundary
with controlled extension outcomes, plus actual UIKit attachment/host selection
helpers on the installed iOS Simulator matching the selected Xcode SDK family.

The diagnostic baseline is app builder830e8277. Exact tested LC revision is in
`source.json`; the recorded baseline is retained rather than overwritten. No
submodules, credentials, pairing records, provisioning data, guest apps, account
data or downloaded simulator runtimes are used.

Required proof:
- Twelve actual Objective-C boundary cases and two rejected counterfactuals.
- Twelve bounded memory-only diagnostic cases, actual Swift copy/reload policy
  with a synthetic service, and exact-header iOS15 Swift overload typechecking.
- Five historical UIKit observations reproducing the language-bridge regression.
- Ten corrected attachment cases and fourteen host ownership cases.
- Actual candidate header/implementation/selector compilation, full tracked
  source inventories before/after, exact extraction and output hashes.
- Four hashed synthetic old/new screenshots, not user content.

Execution scope is precise: controlled extension adapters, marker-only guest
views, actual UIKit selection/attachment, and projected scene/session inputs.
No real extension, physical guest or OS scene registration is exercised. See
`attachment/README.md` for projection and lifecycle limits.

Portable controls:
```
python3 -B -m unittest discover -s ci/multitask-launch -p 'test_*.py' -v
```

Native execution requires macOS and installed Xcode/simulator. Runners reject
missing capabilities, skipped/failed tests, modified inputs and stale outputs.
Only their created simulator may be shut down/deleted. No app-launch/assertion
retry is allowed. Existing bounded boot observation and SDK-family selection
policy is reused unchanged from `ci/language-switch`.

`verify.py` replays source/run identity, full inventory, completion/counterfactual
receipts, attachment acceptance, extraction/hashes, SDK/runtime selection and
owned boot evidence. `attachment_contract.py` is dependency-free and shared with
native/package validators to prevent divergent acceptance. Existing product
source, native build, packaging and signature/size gates are unchanged by this
focused workflow itself.
