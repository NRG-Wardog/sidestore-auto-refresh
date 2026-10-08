# Temporary ADI consumption diagnostic source basis

This directory preserves the reviewed observer/decoder source contract separately
from the accepted `a939e4c` parity contract. Selecting it requires the explicit
`maintained-adi-consumption-v1` basis and its independently approved registry hash.
The historical source checkpoints and parity evidence remain unchanged.

The observer records bounded numeric guest open/read/copy outcomes. It does not
change authentication, storage identity, provisioning, provider selection, native
return values, or the disabled recovery policy. Native synthetic tests establish
the producer/decoder contract, not the cause of device OTP error -45061.

A diagnostic build also requires separately reviewed dependency bases and actual
resolver receipts at the fixed `dependencies/{SideSign,SideStore}-{basis,resolver}.json`
paths. Their exact bytes are bound by the reviewed integration pins. The complete
source delta must end at the final pinned owner; no unreviewed descendant is admitted.

Fresh two-app build eligibility comes only from the independently hash-approved
`provenance/diagnostic-native-readiness.json`. It binds the exact seven source trees,
child graph, actual lock bytes and originHash state, run/host/artifact identity, and
retained compiler/build/test evidence. Owner source proofs continue to report
`production_ready: false`. A final exact-ref IPA build and uploaded-binary verification
remain mandatory. Missing or pending receipts cannot establish eligibility.

The native receipt names the commits actually tested. A later SideStore commit
may differ only by the app lock captured during that same frozen native build:
ancestry, the complete Git delta, regular-file mode, and both before/after lock
hashes must match the retained resolver evidence. No runtime source, child pin,
or other metadata change is admitted by this exception. The descendant is not
described as directly compiled; the final IPA build compiles its exact ref.

The separately retained focused native verification binds the original observer
tuple to its seven producer and five decoder tests with no failures or skips.
Its hash is pinned independently; the two-app receipt does not claim those tests
were rerun. This reuses their verified result without duplicating execution.
