# Quick lossless pass for explicit legacy replacement repair

Input is successful native run `41114358965`, attempt `1`,
builder `f9407ceda3b28f62be9c479a89e79f0acf30691d`. Input ZIP and 24,878,481-byte IPA are
hash-bound in `input-manifest.json`. Same-run refresh qualification is required.

The unchanged bounded 5/10/20/30 schedule completed locally in
346.7116 seconds, saving 672,091 bytes. This measurement
alone is not qualified delivery. Matching green packaging CI and independent
payload/provenance review remain mandatory.

Two workers, 2 GiB/process, 600 seconds per entry and 900 seconds aggregate.
Stop after the first complete round meeting the target, otherwise retain the
smallest verified stream across all four rounds. No extra optimization rounds.
The official Google Zopfli pin remains df1517dd8518113a245e4d203a04b8a0e02ab493.
Verify all 81 Git blobs and the full tree before direct C compilation.
Keep all payload, signature, raw ZIP metadata and nested Backup bytes unchanged.

Default target remains <=24,000,000 bytes. This one exact measured exception
accepts 24,206,390 bytes (24.206390 decimal MB), SHA-256
`77a161cb4376c0819ddfaa3623214e28b78e8c998ec350fa7b84e0c67a9064ca`, only for the complete current native input manifest.
It reports target_met=false and a 206,390-byte excess. It is not
under 24 MB and does not raise the general ceiling or authorize more rounds.

The native evidence download connector timed out on the 408,285,162-byte archive.
The packaging workflow therefore downloads the exact hash-bound artifact 13008771710
using its existing ephemeral token, verifies all 152 receipt references, and
uploads a separate DEFLATE evidence projection for independent byte-level review.
The original archive is not uploaded again. This projects evidence only, with no
app execution or build changes. Projection source and candidate identity, archive
safety and full reference-set equality remain mandatory. Projection and final IPA
are separate artifacts, and the projection is capped at 31 MiB to preserve download
headroom. Private delivery waits for all matching CI and audit gates.
