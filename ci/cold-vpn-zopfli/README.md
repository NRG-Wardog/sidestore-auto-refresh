# Quick lossless pass after cold VPN stabilization

Current input: successful native run `40959544542`, attempt `2`, builder
`2f6099ca2c9ebd3a712144205bfe6de57be84541`. The downloaded artifact and
24,778,987-byte IPA are hash-bound in `input-manifest.json`. The same-run
refresh-truth qualification receipt is mandatory; focused-only evidence cannot substitute.
Measured run `40969951797` exhausted the unchanged 5/10/20/30 schedule in
373.7416 seconds, saving 675,536 bytes. It failed the default size gate; it
is not a qualified delivery run. The exact measured result below now has
a candidate-specific closest-result acceptance; a matching green replay
and independent payload/provenance audit remain required before delivery.

Reuses the reviewed issue56 compressor/ZIP auditor with a narrower execution
schedule: five iterations first, then bounded 10, 20 and 30 iteration rounds only if the
size target is still unmet. Stop after the first complete round meeting the target;
retain the smallest verified stream from the original and every completed round. No further
rounds. Two workers, 2 GiB/process, 600 seconds maximum
per attempt, 900 seconds aggregate compression for this exact-result reproduction.
No additional iteration rounds are permitted. Existing 60-second compilation
and 180-second audit limits remain unchanged. This is not the prior two-hour sweep.

Google Zopfli remains pinned to df1517dd8518113a245e4d203a04b8a0e02ab493.
All81Gitblobs and fulltree verified before directC compilation with existingcc;
no installers/hooks/newsource. Copies only; preserve original and smallestvalid
stream. Failed/timedout attempts preserve original or prior verified stream.
The independent audit preserves all payload/signaturebytes and rawZIPmetadata
except compressedsizes/offsets, plus Russian resources and nestedBackupCRC.
The new source-bound language/recovery and cold-VPN receipts is required and carried forward.

The default final target remains <=24,000,000 bytes, not a claim of minimum or
installed size. This single reviewed closest-result exception accepts exactly
24,103,451 bytes (24.103451 decimal MB), SHA-256
`24061c255877f67ddd9e2734dafb23038c3e3c4354708897f71640f4a2df07c4`,
only for the complete native input manifest bound to builder
`2f6099ca2c9ebd3a712144205bfe6de57be84541`, run `40959544542`, attempt `2`,
and input SHA-256
`d962f2d208da186b420757feec467ec4b0645e22bb530f8e9883d6bb9530c042`.
It reports `target_met: false` and a 103,451-byte excess. This is not under
24 MB and does not raise the general ceiling. Every other input or output
hash/size must still satisfy the standard target. Historical exceptions cannot
substitute for this measured result. No further optimization rounds are allowed;
the same bounded schedule may be replayed only to reproduce the accepted bytes.
All payload, signature, metadata and provenance audit requirements remain mandatory.
Private experimental delivery only after matching CI and artifact verification.
