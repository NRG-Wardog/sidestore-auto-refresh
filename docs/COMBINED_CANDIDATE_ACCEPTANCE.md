# Combined candidate acceptance (v2 and v3)

These are separate draft prerelease candidates. Package identity is the full builder
commit and CI run in each package's Build Candidate diagnostics and matching evidence,
not the upstream displayed version 3.8.9. Static/package verification JSON is not
physical-device execution. Do not publish or close issues based on those checks alone.

## Automated evidence requirements

Each final run must pass the applicable repository suite, pinned patch application
and replay, generated Swift parsing, host and embedded source builds, CoreDevice
tests, package contract/transport checks, UUID-matched SideStoreSupport dSYM collection,
and artifact upload. Grid additionally requires the real-renderer simulator matrix
with defective/corrected measurements, not only template assertions. Preserve the
run's generated Swift, package hash, dependency pins, builder-commit.txt and both
verification JSON files alongside its IPA. Never pair another run's evidence with it.

Issue 24 is integrated in both rebuilt combined candidates. Their source retains
upstream SideStore ff25922e5c13ccfafd83bda5092910d848ebd409, minimuxer
98c3c79982f813878e922ab42f9545314a700f0c, SideSign
a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d and its GSA fix ancestry. This does not
establish universal login success or HTTP 429 resolution. Standalone has a separate
build identity and is not rebuilt or redesigned for these combined fixes.

## Physical-device acceptance

Use a backup and an in-place upgrade with the existing signing and bundle identity;
do not reset storage, clear preferences or delete accounts as a prerequisite.

- Record the product line, full builder SHA, device OS and framework UUID from
  matching evidence. Confirm account/team, existing apps/guests, sources, refresh
  history, LCAppLayoutStyle, LCShowAppLabels and Guest Return preferences survive.
- Cold-launch v3 without first opening legacy SideStore. Home must stay usable;
  status must not sign, install or refresh. Check first-run preparation separately
  from an existing-install upgrade. The original nil-bookmark trap is binary-proven;
  its discarded filesystem NSError is unknown and device retesting is still required.
- Exercise a startup failure and explicit Retry Connection. Confirm specific
  host/storage/bookmark/extension/XPC/readiness diagnostics, safely copyable details,
  no startup retry loop, cancellation and recovery after process termination.
- In both combined lines, switch List -> Settings -> Grid -> Apps, then repeat with
  labels off/on, Compact List, long names, missing icons, accessibility text sizes,
  rotation and narrow/wide iPad windows. Relaunch with Grid saved. Check identity,
  order, search, hidden/locked behavior, changing collections, tap and context actions.
  In v3, include both guests and SideStore-installed apps.
- Complete Apple ID login and 2FA in the SideStore-owned account flow. Check team,
  certificates, signing and expiration; no login success has been inferred from CI.
- In v3, browse/add/remove sources and install/update a test app through the unified
  UI. Test supported activate/deactivate/delete actions, guest launching/LiveProcess,
  Guest Return, Start Collapsed and custom colors without legacy normal navigation.
- Explicitly refresh with the computer disconnected through LocalDevVPN -> Lockdown
  -> CoreDeviceProxy/TLS -> CDTunnel -> RSD -> AFC/InstallationProxy. Verify installed
  expiration actually advances and correlated history records verification. A ready
  connection or completed command must not be presented as verified refresh success.
- Interrupt VPN/transport and distinguish endpoint, heartbeat, CoreDevice, CDTunnel,
  RSD, lockdownd and live UniqueDeviceID-query failures from concrete pairing failures.
  A pairing-file UDID must never substitute for the failed live query.
- Test background scheduling, preferred time, known preflight retries and host
  replacement. An uncertain mutation timeout/termination must pause automatic replay;
  review authoritative app state before an explicit retry. Check foreground/background
  transitions and late callbacks without duplicate operations.

The v3 architecture document describes implemented ownership and routes, not a
production-readiness certification. These acceptance checks remain necessary even
when all repository, simulator and packaging checks pass.
