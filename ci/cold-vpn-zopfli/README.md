# Bounded lossless packaging for operation-local Apple profile origin

Exact native run `41441341176`, attempt `1`, builder
`0ce0b994e8be93e004a8777dad12846a6b6c97a9` passed; focused `41440088237` passed.
The complete input manifest binds the official archive and 25,028,009-byte IPA.
Both app builds passed. Source-owned native cases include policy 55, integration 64,
decoder 59, observations 189, nonce 7 and legacy 81. Exact same-run OpenSSL 3.5.9
passed C 58 and public 24 using actual production C and Swift wrappers.
Public profile fixtures were temporary and are not retained. No owner-device execution.

This candidate adds an operation-local exact-profile Apple-origin capability, not a
generic signer pin or trust-root expansion. The fixed HTTPS, no-redirect, bounded
read-only transport binds an existing record to exact installed bytes; renewed
candidate bytes require their own provenance. Cryptographic inspection alone exposes
explicitly untrusted content and does not authorize a signer role. Existing crypto,
identity, authority and final pre-injection checks remain mandatory. TLS uses normal
platform trust, not a new Apple certificate pin. Actual free-account endpoint
availability and device behavior remain unverified.

The actual transport/consumer fixture passed 58 cases plus 3 external compile-negative
visibility checks. These use URLProtocol, not real Apple network/TLS/account calls.
The test-only transport macro is rejected in effective and actual Release compiler
commands. Both CMS and Apple-origin real signing-bridge markers are mandatory;
finite per-target origin reasons survive the actual wrapper, wire and whole log.
No raw profiles, certificate bytes or private names are logged. No overwrite, re-sign,
replacement, historical-profile pruning, OS policy substitute or session guarantee.
Actual UIKit cleanup passed completeByCommand; no absence-readback fallback used.

Local fixed 5/10/20/30 compression completed all four rounds in 758.9947 seconds:
110 attempts per round, 440 verified streams, no timeout and no extra round.
The measured output is 24,346,034 bytes, SHA256
`156f16e0b5641358e462e665ad2aea00e580fb9eae11372c86c125bd1bd3804f`,
saving 681,975 bytes. Final CI output still requires independent payload/hash audit.
Two workers, 2 GiB/process, 600 seconds/entry, 900 seconds aggregate; no extra rounds.
Official Zopfli pin `df1517dd8518113a245e4d203a04b8a0e02ab493`; all 81 official
blobs/full tree verified before compilation. Payload, signatures, raw ZIP metadata
and nested Backup remain unchanged.

General target stays 24,000,000 bytes. For ONLY the complete exact input manifest
above, an explicitly reviewed measured upper bound of 24,346,034 bytes is allowed.
The local measurement exceeds the target by 346,034 bytes; it is NOT under 24 MB.
CI reruns the same bounded schedule; its independently audited final bytes may
differ, but must remain within this exact-candidate bound. No additional rounds. Size admission is never payload qualification:
matching packaging CI plus unchanged full payload/signature/raw ZIP metadata,
source/evidence/schedule and privacy audits remain mandatory. The actual final CI
output SHA256 must be independently audited and pinned before private delivery.
No generic ceiling increase, extra rounds or time extension. Wrong native inputs
and oversized outputs fail; below-bound altered payloads or metadata still fail
in the actual independent verifier. Historical artifacts are not interchangeable.

Official full evidence artifact 13143622423 is 408,789,083 bytes. Existing ephemeral
workflow token retrieves only that exact archive; SHA and all 162 references are
validated before producing a separate <=31 MiB projection. No archived content is
executed or permissions restored; symlinks/special types/bits/traversal/collisions
remain rejected, and original receipt bytes must match across both artifacts.
CMS admission binds 4 finite evidence files, 20 genuine source blobs, owner commits/
trees, exact helper snapshot, host recipe/source/header/libcrypto hashes, run/attempt
and 58/24 ordered case IDs. Mandatory before compression and repeated final audit;
cleanup's 3 extra sources are hash-only inputs. No credentials/tokens/private keys or
personal pairing/provisioning data may be delivered. Private owner-only, no release.
