# Quick lossless pass after cold VPN stabilization

New lineage, not a rewrite of either previous final artifact. The input manifest
is deliberately invalid until the new cold-vpn-native workflow is fully
green and its actual downloaded native artifact supplies IDs, hashes and sizes.

Reuses the reviewed issue56 compressor/ZIP auditor with a narrower execution
schedule: five iterations first, then bounded 10, 20 and 30 iteration rounds only if the
size target is still unmet. Stop after the first complete round meeting the target;
retain the smallest verified stream from the original and every completed round. No further
rounds. Two workers, 2 GiB/process, 600 seconds maximum
per attempt, 900 seconds aggregate compression for this exact-result reproduction.
The prior measured 5/10 pass used 141.45 seconds and the 5/10/20/30 pass used
370.76 seconds, totaling 512.21 seconds. No additional iteration rounds are
permitted. Prior compression plus this cap is at most 1412.21 seconds; the
existing 60-second compilation and 180-second audit limits leave room within
the approved 1800-second experiment budget, including the earlier short
compilation/audit stages. This is not the prior two-hour sweep.

Google Zopfli remains pinned to df1517dd8518113a245e4d203a04b8a0e02ab493.
All81Gitblobs and fulltree verified before directC compilation with existingcc;
no installers/hooks/newsource. Copies only; preserve original and smallestvalid
stream. Failed/timedout attempts preserve original or prior verified stream.
The independent audit preserves all payload/signaturebytes and rawZIPmetadata
except compressedsizes/offsets, plus Russian resources and nestedBackupCRC.
The new source-bound language/recovery and cold-VPN receipts is required and carried forward.

The default final target remains <=24000000 bytes, not a claim of minimum or
installed size. A single reviewed closest-result exception accepts exactly
24,003,056 bytes and SHA-256
`21c3bcfc31565a9ef46356ae9017a8bdc5a996f1ddd3a347ee0f2b709f84046e`,
only for the complete native input manifest bound to builder
`99ed6179f6dda1f5770ea8b401985e052bde9e5d`, run `40791862589`, and input SHA-256
`64d3c6a273cfbbc52ea5ca4e4f4716f8226380ae9de77becabf97e8a073cb9da`.
It reports `target_met: false` and a 3056-byte excess, never a relaxed general
ceiling. All payload, signature, metadata and provenance audit requirements
remain mandatory for this exact candidate.
Private experimental delivery only after matchingCI and artifact verification.
