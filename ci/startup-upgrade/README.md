# Real Core Data v17.6 to v17.7 qualification

This is a qualification fixture, not a production migration feature. It compiles
all original model versions with Apple's `momc`, and executes the exact production
mapping-selection methods extracted from `PersistentContainer.swift` against real
SQLite stores. Generic managed-object runtime classes are used only for fixture
data; production account/keychain, app signing and recovery logic are not executed.

The exact schema difference is a new optional String `InstalledApp.appBundleFingerprint`.
The fixture checks account/team/app/extension attributes and relationships, source
metadata compatibility, target metadata, and a fresh nil fingerprint.

All work is in a new scratch directory. Originals are copied only after the fixture
writer has exited. A crash-style writer preserves a real uncheckpointed WAL; the
reader must recover its latest committed record on the scratch copy. Original
SQLite, WAL and SHM bytes are inventoried and must remain unchanged. This does NOT
prove safe copying of a live application store, cross-process ownership, crash-safe
publication, app-group permissions, physical-device keychain access or production
startup integration. Those are separate required gates before enabling relocation.

Source and destination conflicts and denied staging are rejected rather than
silently deleting or overwriting a store. No user data is used.

Framework discovery is observed separately for a conventional FMWK framework with
Resources and a production-like flat APPL-marked `.framework`. It uses a locally
compiled Objective-C-visible anchor and actual `Bundle(for:)`, `Bundle.allBundles`,
and source-store metadata. It does not assume the baseline lookup fails and does
not emulate LC's private main-bundle replacement. A supplied bundle must expose a
metadata-compatible old model for the fixture to be valid.

The no-overwrite check is a serial pre-existing-target control, NOT an atomic
cross-process publication guarantee. Permission rejection is tested with an
unprivileged process and an actually non-writable destination after the source
model and mapping were validated. No interrupted promotion or concurrent writer
safety is claimed by this schema-only fixture.
