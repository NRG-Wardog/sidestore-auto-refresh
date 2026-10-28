# Quick lossless pass after cold VPN stabilization

New lineage, not a rewrite of either previous final artifact. The input manifest
is deliberately invalid until the new cold-vpn-native workflow is fully
green and its actual downloaded native artifact supplies IDs, hashes and sizes.

Reuses the reviewed issue56 compressor/ZIP auditor with a narrower execution
schedule: five iterations first, then at most one ten-iteration round only if the
size target is still unmet. Stop after the first complete round meeting the target;
retain the smallest verified stream from the original and either round. No further
rounds. Two workers, 2 GiB/process, 600 seconds maximum
per attempt, 1500 seconds aggregate compression. Compilation60seconds and audit
180seconds fit within the existing30minute approval, not the prior2hour sweep.

Google Zopfli remains pinned to df1517dd8518113a245e4d203a04b8a0e02ab493.
All81Gitblobs and fulltree verified before directC compilation with existingcc;
no installers/hooks/newsource. Copies only; preserve original and smallestvalid
stream. Failed/timedout attempts preserve original or prior verified stream.
The independent audit preserves all payload/signaturebytes and rawZIPmetadata
except compressedsizes/offsets, plus Russian resources and nestedBackupCRC.
The new source-bound language/recovery and cold-VPN receipts is required and carried forward.

Final target remains <=24000000bytes, not a claim of minimal or installedsize.
Private experimental delivery only after matchingCI and artifact verification.
