# Temporary ADI staging-order experiment

The physical-device report from accepted builder `f9f23d980df363eb0f6eb5093b48e3639b03e137` reached native OTP and returned `-45061`. Its untruncated consumer descriptor was:

`v1|0|5,0,1,1,0,0,0,-1|5,1,1,1,0,639,639,0`

The exact published schema identifies an OTP-phase open of the expected `adi.pb` path, followed by a read that requested and returned 639 bytes and a successful guest-memory copy. This excludes a failed open, short read or reported copy failure for that observed operation. It does not establish total file length, equality with the supplied blob, cryptographic binding to the UUID, or opaque ADI acceptance. No setup-phase imported open/read was observed; this weakens a setup content-cache hypothesis but does not observe metadata queries or other initialization state.

## One controlled runtime change

The normal provider now validates/creates the same UUID directory and checks/stages the same supplied bytes before common native setup. The provider, UUID and Android-ID derivation, path, native VM/cache policy, mutex, and single OTP invocation remain unchanged. Invalid library directories still fail before file mutation. File-staging failures stop before native setup; a later native setup failure can now occur after staging, which is the intended ordering difference. Native provisioning start/end and isolated APIs retain their prior behavior.

A cold VM sees the file before constructors and library initialization. Reused VMs remain reused. Fresh provisioning already initialized through start/end before its OTP call, so this experiment cannot retroactively change that initialization. The known Swift deferred-cleanup lifetime race is not fixed by this experiment. No identity reset, automatic recovery, legacy selection or additional OTP attempt is enabled.

This is an experiment against an observed device failure, not a proven authentication repair.

## Passive diagnostic evidence

`DEBUG TEMPORARY adi_consumption=v2|truncated|comparison|input_covered|rows...` preserves the existing eight-field event rows. Comparison is 0 for unavailable/unobserved, 1 when the compared host bytes match so far, and 2 for a sticky mismatch. Coverage is 1 only when comparison is 1 and one tracked sequential expected-file stream has compared the entire supplied input with successful guest copies and no tracking, offset, event or work-budget uncertainty. Coverage does not prove EOF, total file size or provisioning validity. Input/comparison work is bounded to 1 MiB per invocation. No paths, bytes, identifiers or hashes are emitted.

Only after an existing-blob, exact native-OTP `-45061` failure, a separate temporary observer compares selected and already-entitled legacy storage. It reports identifier and blob independently as missing, equal, different, ambiguous or unavailable. UUID text and Base64 UUID representations are compared canonically; blob bytes are compared after the existing decode. The selected snapshot and pending-journal absence are checked before and after the two exact-service pair-key queries under the existing transaction lock. The inherited Security query uses `kSecUseAuthenticationUIFail`, so this diagnostic does not request interactive keychain authentication. There are no writes, deletes, reconciliation, candidate selection or probes. Malformed/oversized data and read failures are unavailable; conflicting/partially populated groups remain ambiguous. Cancellation keeps its normal behavior.

The legacy observation is bounded to 64 rows, 128 encoded identifier bytes, 1,398,104 encoded blob bytes per value and 4 MiB aggregate input. A UUID-only legacy source can therefore report identifier different and blob missing without implying a complete valid pair.

Both consumers retain v1 compatibility, 32 event rows, 2,048 encoded diagnostic bytes and the existing 4,096-byte failure-wire cap. Optional rows can be trimmed without replacing the main error. The temporary release-visible trace switch remains the removal point once diagnosis is complete.

## Evidence boundaries

The focused macOS gate at run `37773853730` passed 7 Anisette and 6 paired SideStore/LiveContainer tests with zero skips. It exercised actual C/C++ wrapper/hooks, Swift decoding and cross-module rendering, and the extracted read-only storage wrapper with controlled doubles. The native library remains opaque, and these tests do not prove device authentication.

The v2 source basis uses the exact accepted f9 predecessor, sealed by its original pin-file digest and retained v1 registry/source/native receipts. It admits the complete reviewed predecessor-to-new source delta and checks actual Git ancestry, trees, modes and blobs. Genuine new dependency resolution, exact-source two-app native validation and the final uploaded IPA verification remain separate mandatory gates. Historical v1 receipts never establish v2 readiness.
