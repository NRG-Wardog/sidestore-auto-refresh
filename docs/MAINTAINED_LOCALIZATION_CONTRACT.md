# Maintained compiled-localization contract

This is a narrow forward port of useful SAR branch tests, not a replacement build pipeline or a new localization implementation.

## Source provenance

- Origin: `NRG-Wardog/sidestore-auto-refresh` commit `0cc9078ef6cb57d8b3512306fd48f376e601040d`.
- Original functions: `scripts/p2_localization_contract.py` `validate_int64_format` and `verify_compiled_resources`.
- Original negative controls: `tests/test_p2_localization.py` `test_compiled_resources_reject_missing_and_malformed_plural`; `tests/test_p2_four_languages.py` `test_compiled_traditional_language_cannot_fall_back_silently`.
- Current workflow catalog: `NRG-Wardog/LiveContainer` commit `8808211d340c6e9e2a6274c0eaa863d7ab09474f`, `Resources/Localizable.xcstrings`, SHA256 `b28c367b2106668a8bbd42b0ad6b42590583947fa64322d42bb24a71c129e03b`.

No old sealed translation manifest, simulator renderer, historical source selection, capability exception or production string change is included. Catalog expectations are derived from the exact supplied catalog and unsupported source shapes fail closed. The four required languages are English, Hebrew, Simplified Chinese and Traditional Chinese. Other catalog languages are outside this check's declared coverage.

The current catalog has 2,143 scalar, six single-argument plural and one compound plural key per required language. These counts are an observation, not hard-coded expectations. Exact scalar translations, simple plural categories and argument formats, and compound plural argument identities/categories/values are checked against actual compiler output. Missing language resources and silent cross-language fallback fail.

## Validation

Run the 14 synthetic tests with `python3 -B -m unittest discover -s tests -p test_compiled_localization.py -v`.

On macOS with the selected Xcode installed, the workflow runs `xcrun xcstringstool compile --output-directory compiled source/Resources/Localizable.xcstrings`, then the checker against `compiled`. It records input/output hashes and retains compiler logs and actual compiled resources. Apple's `plutil` is used read-only when its catalog compiler emits OpenStep strings rather than binary/XML plists.

The locally run tests are synthetic negative controls. Their passing does not mean Xcode compiled this catalog. Native compilation is established only by a passing exact workflow run. Neither check proves runtime locale switching, plural rendering by Foundation, Settings persistence, authentication, device behavior or final IPA resource retention. The existing native setup-localization suite remains unchanged.
