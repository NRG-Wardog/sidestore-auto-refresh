# Anisette pair precondition

## Scope and evidence

This is a non-destructive admission guard, not a diagnosis of a particular device or a state-repair feature. The delivered 185341a namespace fix stopped per-key legacy Anisette fallback, but existing selected storage can still contain an ADI blob without its identifier. The old fallback could borrow a legacy identifier without persisting it, then store newly provisioned ADI bytes only in selected storage. Interrupted sequential migration is another source-proven route.

Pinned SideStore ff25922 resolves a missing/invalid identifier before its ODA provider reads the blob; therefore that state could generate a new UUID and pass the old blob to the provider. Whether this caused the reported device failure is unproven.

## Bounded behavior

- Both absent: first identifier creation and normal provisioning remain available.
- Valid UUID or 16-byte base64 identifier, absent blob: normal provisioning remains available.
- Valid identifier and usable blob: reuse the same bytes.
- Blob present with absent/invalid identifier: stop before generating an identifier or calling the provider.
- Invalid existing identifier without a blob: stop without replacing it.
- Invalid existing blob encoding: stop without implicit re-provisioning over it.
- Migration requires independently valid source and selected Anisette state before writing any migration keys. An orphan cannot borrow its missing counterpart from the other namespace. The existing complete-account/single-namespace/conflict rules remain in place.

The guard does not prove a well-formed blob belongs cryptographically to a well-formed identifier. It intentionally does not inspect, reset, delete, regenerate or guess existing identity/provisioning data.

## Atomicity

The two raw values are read and admitted in one existing process-shared lock transaction. ODA and remote provider continuations consume that immutable snapshot rather than separate getters around an await. A newly returned blob is written only after comparing both original raw values under the same lock, so a stale async result cannot overwrite a changed pair. SideSign's remote cache may return an earlier newAdiBlob even when the request supplies an existing blob. A returned blob that differs from an already-present snapshot blob is held without mutation; equal bytes are a no-op preserving the original encoding.

This uses the existing lock, with no new cross-process protocol and no lock held across provider/network work. Already-running older binaries do not participate in this lock; no guarantee is made against their writes. Concurrent callers using an absent blob can provision independently; only the first matching writeback commits, and a later mismatching writeback is held without overwriting the first.

## Diagnostics and recovery

Finite LCAnisettePairError cases are mapped to anisetteIdentityStateInvalid, appended T32 and A12 IDs. Display and Copy Details contain only finite classification/build/operation fields, never the identifier or blob. The result is a blocked hold, including credential loops and cached/post-auth provisioning paths. Re-entering a password or repeatedly resuming provisioning is not offered as a repair. Stored bytes are preserved for explicit recovery review.

## Verification

`test_anisette_pair_precondition.py` assembles the exact pinned/generated resolver, ODA method, remote provider and production keychain implementation with synthetic in-memory storage/provider doubles. Its native matrix covers six presence/validity states, invalid blob, base64 identifier, coherent migration, interrupted migration, both cross-namespace orphan permutations, old fallback creation and upgrade, stale async writeback and remote cached-blob results, locked reads, read failure and repeated use, on both provider routes. Baseline variants prove generation/provider reachability before the guard. No Apple request or device keychain access is involved.

The combined workflow discovers this harness through run_required_tests.py; its skip is not allowlisted. The generated Anisette files are required in delivery evidence. Local source/extraction checks can run on Linux; Swift execution and iOS compilation require the existing macOS/Xcode CI gate. No native execution is claimed when the compiler is unavailable.

## Prepared-source boundary

`verify_prepared_anisette.py` requires the exact embedded SideStore pin and derives its complete Anisette directory inventory and original bytes from pinned Git objects. It permits only the exact production transformations of AnisetteConfigManager.swift, OnDeviceAnisetteManager.swift and AnisetteProvider.swift. Every other file remains byte-identical to its pinned object. Missing, added (including staged, untracked or ignored), linked, executable-mode-changed, or otherwise altered files fail the boundary. Guard-marker presence and patch replay over working files are not acceptance criteria.

The workflow replaces its obsolete blanket Anisette diff with this read-only verifier; existing SideSign, authentication and logging boundaries remain intact. Regression tests alter each permitted file and every unrelated Anisette file, reject wrong pins/new files, and verify repeated checks do not change prepared bytes.
