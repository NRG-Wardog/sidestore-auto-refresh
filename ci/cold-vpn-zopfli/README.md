# Bounded lossless packaging for authenticated preservation qualification

Input is successful native run `41247261999`, attempt `1`, builder
`00062ed170dc16e9ba61a8ca2940d6386ed00791`; focused run `41246627577` passed.
The exact archive and 24,975,298-byte IPA are hash-bound in `input-manifest.json`.
Both real app builds passed. Source-owned fixtures include policy 55, actual refresh
integration 38, strict decoder 59, observations 94, nonce producer-consumer 7 and legacy 81.
Same-run OpenSSL 3.5.9 qualification passed 28 synthetic C cases and 16 actual public
CMS/Swift decoder cases using production C and wrappers. Public input bytes were
temporary and are not retained. These checks are not execution on the owner's device.

Raw DER equality remains the first path. Only formerly rejected differing-DER
comparisons may use the bounded authenticated outer/inner authority projection.
Identity, entitlements, certificates, devices, TTL and supported unknown-field values
remain strict; only specifically reviewed lifecycle fields are omitted from authority
comparison. Unsupported representations fail closed. No app overwrite/re-sign/
replacement, historical-profile pruning, OS trust-policy substitute or claim of future
launch, Keychain, App Groups or session preservation. Actual UIKit cleanup passed by
both owned shutdown/delete commands exiting 0; no absence-readback fallback was used.

The unchanged bounded 5/10/20/30 schedule completed locally in 328.0548
seconds with 440 verified attempts, saving 683,033 bytes. Two workers,
2 GiB/process, 600 seconds/entry, 900 seconds aggregate; preserve best verified streams
and do not add rounds. Official Zopfli pin remains
`df1517dd8518113a245e4d203a04b8a0e02ab493`; all 81 Git blobs/full tree are verified
before C compilation. Payload/signatures/raw ZIP metadata/nested Backup stay unchanged.

General target remains 24,000,000 bytes. Exact candidate-only measured acceptance
binds this complete input manifest to 24,292,265 bytes, SHA256
`7ea32a7d06a3775fce488fb9e4f6fca62047dedcd3a467b69083ac31d452ef11`.
It reports target_met=false and 292,265 bytes excess; NOT under 24 MB.
No generic ceiling is raised; prior candidate outputs are rejected for this input.
Matching packaging CI and independent final payload/privacy/evidence audit are required.

Full native evidence archive: 408,603,208 bytes, artifact 13061410759. Existing ephemeral
workflow token retrieves only the exact official archive. Packaging verifies its SHA
and all 160 native receipt references, then emits a separate bounded 31 MiB projection.
Archive data is never executed or permission-restored; links/special bits/types and
traversal/collisions remain rejected. Source receipt.json must equal IPA native receipt.
The CMS gate binds four actual finite evidence files, all 18 genuine source blobs,
exact owner commits/trees, helper snapshot, host recipe/source/header/libcrypto hashes,
run/attempt and 28/16 case identities. It runs before compression and again in final
independent audit; its admission report is retained. Cleanup's three additional source
files are copied only for hash verification.

Do not retain or deliver credentials, tokens, private keys or personal pairing/
provisioning material. Keep delivery private and owner-only; no public release.
