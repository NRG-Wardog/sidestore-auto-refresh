# Cold VPN source qualification

The focused workflow runs three immutable source-owned suites on macOS before
any full app build: host post-activation observation, backend pre-mutation
network readiness, and minimuxer owned observation/endpoint-refresh coordination.
The bounded network grace does not claim to bound native initialization or
fetchUDID: the original native readiness check remains separate and runs once.
Native Swift execution is mandatory. Skips, expected failures, missing suites,
source drift and stale/mismatched receipts fail closed. The LC and SS tests also
typecheck their extracted production helpers with the actual iOS 15 simulator
SDK; minimuxer additionally checks its iOS 14 deployment target. Full native integration compilation remains a separate mandatory gate.
The 105-case host VPN fixture (all original 89 cases, eight grace-recovery cases and eight required-Wi-Fi cases) is also generated, compiled and executed
in this focused stage; its exact case list and execution marker are mandatory.

The runner hashes complete tracked source inventories for all three owners,
including tests and deployment configuration. The sole normalization is the
existing reviewed SideStore app-lock originHash-only resolver transition. It
requires committed baseline bytes, exact retained before/after bytes, unchanged
pins, record.py-equivalent transition fields and matching native receipt hashes
and identity. Unknown source/lock changes remain rejected. It binds suite logs and exact
workflow/source identity. Symlink contents are recorded without following them;
fixture entrypoints themselves cannot be links or untracked files.

This is synthetic source execution, not real VPN activation, account refresh,
physical-device validation or a claim about every network. All previous startup,
language simulator, Russian, recovery and native qualification gates remain.
The package requires the new receipt in addition to all previous provenance.
Quick lossless packaging is separate and cannot start until exact green native
artifact metadata replaces the intentionally invalid input manifest.

The grace-recovery extension extracts the actual backend wait and host activation
policy. It preserves every original case and binds the sixteen additional ordered
cases to exact generator/case-file hashes; this is not a real VPN device test.
