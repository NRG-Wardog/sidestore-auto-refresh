# JIT-Less readiness: canonical LiveContainer observation

## Evidence and ownership

The tester reports native LiveContainer Diagnose **Valid** and Remove Certificate available, while Setup Assistant says **JIT-Less certificate not configured**. Stored bytes and native validation exist; the setup result is therefore inconsistent. The original build does not expose its Security import status, so neither its exact import error nor a specific cipher incompatibility is claimed.

Pinned LC 12377cf and current upstream 4dbe0f9 use ZSigner/ZSignAsset.InitSimple for certificate acceptance and ZSigner.checkCert for validity/OCSP. V3 had separately required SecPKCS12Import plus a SecIdentity merely to recognize a copy. It also lacked the successful manual-import observation notification.

LC owns certificate storage, parsing, importing/removing, and validity. SideStore owns its active signing certificate. V3 observes public identity hashes, presents readiness, and invalidates stale observations; it does not import private keys as part of diagnosis.

## Invariants and smallest adapter

- A certificate that exists must not be described as missing because an independent parser rejects it.
- Readiness uses the same native parser/validator as LC, without suppressing revocation or claiming Ready when identity/validation is unknown.
- Exact DER SHA-256 comparison remains required to distinguish SideStore's current certificate from an older LC copy.
- Successful manual import invalidates an older pending headless import before canonical writes, then notifies after all writes. Failed/cancelled import changes nothing.
- The certificate notification advances the existing fact revision synchronously before scheduling reload. An already-queued older validation callback cannot commit after the writer; the same gate protects Setup, Home and Health. Native framework/input failure must complete validation rather than strand a continuation.

A read-only ZSigner metadata API invokes the existing InitSimple parser once and returns only team and DER certificate SHA-256. Its LCUtils adapter uses the existing framework loader. The separate Security parser is removed from readiness; missing storage and unverifiable identity remain distinct. Successful temporary key/certificate allocations are released. Canonical storage/import/signing/OCSP rules are unchanged.

The pinned LC validation wrapper previously could call a missing ZSigner class and return zero without a callback. The narrow wrapper patch completes with an unavailable result for missing input/framework/class, while delegating actual validation to the original checkCert.

## Verification boundaries

Immutable pinned-source transformation/idempotence, production-code reader interleavings/typed cases, actual native parser/hash observation, callback completion and manual writer wiring are tested separately. Synthetic certificates do not prove Apple OCSP or device code-signature validity. A prepared mutable checkout was rejected as a false positive; immutable git-show confirmed the pinned wrapper.

Device retest: keep the existing valid copy; reopen Setup/Health and verify Configured / Ready when it matches the active SideStore certificate. Re-import through each canonical method and confirm status updates immediately. A different or revoked certificate must remain Needs Refresh/Revoked, never Ready. Do not close #39 from CI alone.

## Accepted bounded upstream debt

Pinned/current-upstream `InitSimple` can leak its local EVP/X509 allocations if a decodable private key lacks a paired certificate, or has no subject CN, before assigning ownership to the asset. The observation adapter releases all successful allocations; it cannot recover unreturned local pointers. This release preserves the canonical parser rather than changing its failure internals. Canonical manual and built-in import validate a paired certificate before persistence; wrong-password/malformed stored values remain Unknown and never Ready. A corrupt/tampered key-only record could still exercise the pre-existing leak during a diagnostic attempt. Accepted P2 for this stabilization pass: bounded, noncanonical input, no signing/persistence success or secret export. Track a focused upstream failure-cleanup patch separately; no claim of leak-free parsing on those failure paths.
