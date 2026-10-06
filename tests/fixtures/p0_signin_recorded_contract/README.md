# Recorded P0 sign-in export contract (failed run)

This is a compact, real-artifact regression from [run 37496696970](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/37496696970), builder commit `d259819dc88a12a982d45a7995b7bc4428050bab`.

**The historical run remains failed.** Its eight XCTest methods passed, and all 24 required screenshots were exported, but its producer serialized empty username/password labels in all eight cases. Replaying the reports through the production `verify_export` must reproduce exactly those 16 failures. Tests do not fill in labels, turn the old result green, or claim fresh simulator, physical-device, backend, or spoken VoiceOver verification.

## Retained evidence and provenance

- All eight case JSON reports, both failed verification reports, 24 required PNGs, source identity, extracted source files, and project files are byte-for-byte originals.
- Both complete nested xcresult attachment manifests and XCTest summaries retain their actual schema, attachment names, filenames, metadata and ordering. Only simulator `deviceId` values are replaced with an all-zero UUID; JSON keys are sorted. Manifests also retain rows for omitted optional diagnostic attachments.
- Each of the eight hierarchy files is reduced to its actual text-field and secure-text-field lines. Entered `value` fields and process memory addresses are removed. The observed `placeholderValue: 'Apple ID'` and `placeholderValue: 'Password'` remain; these excerpts are teardown diagnostics and do not substitute for measurement-time metadata.
- Input was exclusively the producer's synthetic `p0-user@example.invalid` and `p0-synthetic-password`. No real-account operations occurred.
- `provenance.json` records every retained file's original SHA-256, fixture SHA-256, byte count and exact transformation. The 24 unchanged PNGs total 5,180,333 bytes (4.94 MiB). All retained evidence is about 5.23 MiB, excluding this README and provenance.
- Omitted: diagnostic PNGs, durable duplicates, terminal simulator screenshots, video, xcresult/build products and unrelated legacy-layout evidence. The original downloaded export is never rewritten.

## Regression boundary

Run `python -m unittest discover -s tests -p 'test_p0_signin_recorded_contract.py' -v`.

The tests exercise the real consumer against actual reports and filenames, then mutate temporary copies to verify rejection of missing/wrong metadata, incomplete reports, missing/corrupt PNGs, duplicate named attachment rows and reuse of one exported file for multiple required screenshots. Distinct PNG files with identical pixels remain valid: consecutive UI screenshots can legitimately be identical.

The unmodified baseline must have no other consumer failures. That characterizes the remaining contract checks without accepting the failed run. Source-file and source-metadata drift are checked against recorded identity/provenance **inside this regression**. The current `verify_export` consumer does not authenticate source identity; it checks XCTest and attachment evidence. Recorded `generated-shell`, member and production-template hashes are retained as historical metadata, not proof that those omitted original input files were rehashed here. Current-checkout identity must never be substituted for the recorded producer.
