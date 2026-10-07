# Bounded recovery after native OTP reports not provisioned

The physical-device result on `39c474ee` is now specific: AnisetteKit's
`ADIOTPRequest` returned `-45061`. It is not a loader error, a pending provisioning
start, an Apple credential rejection, or a verification-code response.
This code alone does not prove whether the stored pair is mismatched or native
runtime/file staging caused an otherwise usable pair to fail.

## Historical regression evidence

The reported last working login version is probably v3.0.1. Its release tag is
`6f0a144936c789bd7557d93ecf752888fddbb256`, build `35419528113`.
The actual build log resolved AnisetteKit `1f5a7e3`, the same native implementation
as the failed candidate. The headless LiveProcess path and on-device default
also already existed. Neither worker introduction nor a new native algorithm
is established as the regression boundary.

Old per-key fallback can nevertheless create this exact upgrade state:

1. A legacy namespace holds identifier A. A selected namespace has neither key.
2. v3.0.1 borrows A, then stores a newly provisioned blob A only in selected storage.
3. After fallback is disabled, the old unguarded resolver in 185341a creates B
   beside the selected blob A.
4. The later structural guard admits B/blob A because both values are well formed.

The historical executable fixture uses seven verbatim, hash-pinned declarations.
This proves the code path is reachable; it does not reveal the user's stored bytes.

## Recovery admission and ordering

Only a typed native OTP `-45061` with an existing nonempty blob enters recovery.
Fresh provisioning, other native operations, other codes and cancellation do not.

First, the exact current UUID/blob is checked in an isolated native OTP invocation.
The wrapper checks and stages a private copy before native initialization, following
the ordering used by primary Anisette server implementations. If valid headers are
returned and the stored snapshot is unchanged, those same headers continue to the
original authentication flow. No legacy enumeration or Keychain write occurs.
The finite local outcome is `isolated_current_pair`.

Only if that isolated current pair also returns exact native OTP `-45061` does
recovery inspect the already entitled groups of the exact SideStore Keychain service.
Only identifier/blob keys are queried. All eligible sources must agree on one
different canonical UUID. Any supplied legacy blob must equal the current blob.
Malformed, conflicting or ambiguous sources fail closed. A UUID-only source is an
untrusted candidate until native OTP validates it; it never becomes a normal pair
merely because it exists.

There is at most one legacy probe. It uses the unchanged compact Android-ID
derivation, not a remote-provider format fallback. Full canonical headers and
nonempty canonical Base64 OTP/MID are required. Validation performs no account
request and does not consume the returned headers; the original pipeline receives
the same successful result once.

## Isolation and storage

The native VM is process-global upstream, so a second Swift provider is not isolation.
The added native entrypoint saves/restores the regular VM and its cached state under
the same mutex, uses a fresh checked VM, and restores active-VM/logging state.
Probe guest filesystem mutation callbacks fail closed. Only the wrapper writes
its exclusively created temporary copy; no provisioning or network API is exposed.
An unsupported write requirement rejects recovery rather than relaxing isolation.
Native procedure limits are not a guaranteed overall wall-clock deadline: mutex
waiting and host I/O retain their existing behavior. Fatal process death cannot
produce a Keychain proof or commit.

After legacy OTP succeeds, the receipt rechecks selected raw bytes, selected group
and legacy sources under the existing process-shared lock. Only the identifier is
restored; the selected blob's encoding/bytes, legacy data, credentials, certificates
and authentication markers remain unchanged. The existing journal machinery provides
verified rollback and exact prior/intended reconciliation. Unknown state remains
blocked. Cancellation is checked before proof and throughout commit.

Copied recovery diagnostics contain only finite statuses and bounded native numbers.
No identifiers, blobs, OTP/MID, paths, session handles or provider descriptions are
serialized. The next candidate addresses a source-proven historical failure path;
its ability to restore this device's login remains a physical-device acceptance gate.
