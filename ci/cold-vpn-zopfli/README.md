# Verified short lossless packaging

This candidate is bound to native run `40890835931`, attempt `2`, builder
`90307b767156f8ceb061bd737e73ad116133dda3`. The native input is 24,687,019
bytes, SHA-256 `39e3d49cdc3f1744fd8ee328c71268f3e78810e79ee1fb9f642df6fa8def066d`.

The approved Google Zopfli source remains pinned to
`df1517dd8518113a245e4d203a04b8a0e02ab493`. All 81 Git blobs and the complete
tree must verify before direct C compilation. No installers or hooks run.

The unchanged short schedule is 5, 10, 20 and 30 iterations, stopping after
the first complete round meeting the target. Two workers, 2 GiB per process,
600 seconds per attempt, and 900 seconds aggregate compression per pass.
The local measurement completed all four rounds in 406.52 seconds. One
matching CI reproduction uses the same schedule and caps; no additional
iterations or native app rebuild are authorized by this packaging change.
Original bytes and the smallest verified stream survive failed attempts.

The default target remains <=24,000,000 bytes. The single candidate-specific
closest-result acceptance is exactly 24,021,064 bytes and SHA-256
`df1267dda7914bcdbcfd170396e35a445166a750fdc501866827d6c2e83099cb`, bound to
the full native input manifest above. It reports `target_met: false` and
`excess_bytes: 21064`; this is not a raised general ceiling or a minimum-size claim.

Every decompressed entry, binary/signature byte, and raw ZIP metadata field
except compressed sizes and offsets must remain identical. Russian resources,
nested backup CRC, startup, CoreData, language, cold-VPN and MULTI receipts
remain mandatory. Compression changes download size, not installed payload size.
Native and synthetic migration qualification does not prove physical-device
startup or authorize production database relocation/backup/reset.

Private owner-only experimental delivery requires matching successful packaging
CI, exact final bytes, provenance, privacy and independent archive audits.
