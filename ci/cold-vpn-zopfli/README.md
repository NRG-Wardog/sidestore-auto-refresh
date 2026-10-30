# Quick lossless pass after per-target refresh diagnostics

Current input: successful native run `41090098529`, attempt `1`, builder
`9c01331ca37db3c9443e2b5f5a51973e89b239d2`. The downloaded artifact and
24,804,243-byte IPA are hash-bound in `input-manifest.json`. The same-run
refresh-truth qualification receipt is mandatory; focused-only evidence cannot substitute.
Local copy-only measurement exhausted the unchanged 5/10/20/30 schedule in
352.3366 seconds, saving 675,047 bytes. This measurement is not a qualified
CI delivery run. A matching green replay and independent payload/provenance
audit remain required before delivery.

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
24,129,196 bytes (24.129196 decimal MB), SHA-256
`ae383e90c4c7c2c5a2665d6b3eda6fd14f17c9591fc955e46a96c044b5047d27`,
only for the complete native input manifest bound to builder
`9c01331ca37db3c9443e2b5f5a51973e89b239d2`, run `41090098529`, attempt `1`,
and input SHA-256
`c1f37cbb27795cea362445f1309f124d018906799c8717d98956a7527ded055e`.
It reports `target_met: false` and a 129,196-byte excess. This is not under
24 MB and does not raise the general ceiling. Every other input or output
hash/size must still satisfy the standard target. Historical exceptions cannot
substitute for this measured result. No further optimization rounds are allowed;
the same bounded schedule may be replayed only to reproduce the accepted bytes.
All payload, signature, metadata and provenance audit requirements remain mandatory.
Private experimental delivery only after matching CI and artifact verification.
