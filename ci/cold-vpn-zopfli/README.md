# Quick lossless pass after cold VPN stabilization

Current input: successful native run `40959544542`, attempt `2`, builder
`2f6099ca2c9ebd3a712144205bfe6de57be84541`. The downloaded artifact and
24,778,987-byte IPA are hash-bound in `input-manifest.json`. The same-run
refresh-truth qualification receipt is mandatory; focused-only evidence cannot substitute.
No recompressed result has been measured or accepted yet for this input.

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

The default final target remains <=24000000 bytes, not a claim of minimum or
installed size. A historical, different-input closest-result exception accepts exactly
24,021,064 bytes and SHA-256
`df1267dda7914bcdbcfd170396e35a445166a750fdc501866827d6c2e83099cb`,
only for the complete native input manifest bound to builder
`90307b767156f8ceb061bd737e73ad116133dda3`, run `40890835931`, and input SHA-256
`39e3d49cdc3f1744fd8ee328c71268f3e78810e79ee1fb9f642df6fa8def066d`.
It reports `target_met: false` and a 21064-byte excess, never a relaxed general
ceiling. All payload, signature, metadata and provenance audit requirements
remain mandatory for this exact candidate.
That historical exception cannot authorize the current input. A new exception,
if needed, requires actual measured bytes/hash and independent review; the
current input remains subject to the default 24 MB gate.
Private experimental delivery only after matching CI and artifact verification.
