# Embedded cellular diagnostic bootstrap

Status: implementation in progress, not a device-validated feature. Experimental
branch only. No public release or deployment target changes.

The source implementation is selected by the combined workflow's explicit
`cellular_bootstrap` input (default false, investigation branch only). No workflow
has been dispatched for this redesign. Entry: embedded SideStore Settings,
Experimental Features, Cellular Diagnostics. This is not a new standalone IPA.

## Architecture

Embedded SideStore Diagnostics owns the screen. The standalone probe is a
historical experiment, not the supported bootstrap. The canonical source is
`PairingFileManager.shared.fetchPairingFile()`, including its existing combined
bundle hooks and reset semantics. No second pairing file is maintained.

```text
Canonical PairingFileManager source
  -> existing storage, or explicit security-scoped import to canonical storage
  -> production PairingFileParser + idevice pairing parser
  -> production utun/route candidate extraction (no subnet enumeration)
  -> bounded TCP probes, exactly one reachable candidate
  -> Lockdown StartSession with canonical pairing + read-only device identity
  -> production CoreDeviceProxy (service TLS + contiguous CDTunnel)
  -> production managed heartbeat + jktcp adapter
  -> production RSD handshake
  -> matching Wi-Fi baseline required before cellular comparison
```

The diagnostic does not sign, stage, install, refresh, pair a new host, or change
the normal refresh Wi-Fi policy. It does not open a PC/USB transport. Existing
production CoreDevice/jktcp fixes and combined packaging remain prerequisites.

## Pairing and ownership

Existing canonical content is parsed without a picker. Missing content presents
an explicit import action, never an automatic private-container copy. An import
is parsed before replacing canonical storage, uses atomic protected writing and
backup exclusion, and leaves the old record intact on failure. The diagnostic
does not export a record or invent one. A clean installation without any pairing
still needs a legitimate user-supplied record through the existing pairing setup.
Imported records use protection until first user authentication, preserving
SideStore's ability to use its canonical record for legitimate locked-device
refresh after unlock. A same-directory protected temporary file is excluded from
backup before atomic rename. No private material is added to the IPA.

`PAIRING_FOUND` and `PAIRING_PARSE_OK` do not imply device acceptance.
`PAIRING_READY` requires authenticated Lockdown acceptance. Malformed records
fail parsing; a device rejection fails authentication; TCP refusal leaves
authentication NOT_ATTEMPTED, not rejected. A previously accepted record can be
displayed as previously validated, but never as accepted on the failing run.

The pinned `lockdownd_connect` FFI frees its provider on failure. New validation
must not use that ownership-ambiguous wrapper: borrow the provider in Rust, use
the production Lockdown client with RAII, and return only stage/error metadata.
Do not free in-use handles on cancellation. The current CoreDevice heartbeat is
process-global, so read-only diagnostics must serialize with the production FFI
queue and refuse an active batch or existing transport, not create an unguarded
second CoreDevice session.

## Discovery and comparison

Reuse `NetworkIfaceScanner` and `DeviceConnectionManager` candidate extraction.
Do not copy the standalone Objective-C route parser. Probe only route/P2P
candidates, cap work, report no candidate and ambiguity explicitly, and never
choose the first responder when multiple candidates answer. A utun interface is
not proof that its provider is LocalDevVPN. Only the full authenticated chain
proves transport readiness.

Network selection is automatic using bounded Network framework snapshots.
An initial cellular-only launch asks for Wi-Fi; it is not a cellular experiment.
A baseline is bound to build, canonical pairing, authenticated device identity,
peer/tunnel configuration and resolver implementation. Keep this comparison
state in memory, not in exported reports. Relaunch, pairing/configuration changes,
unknown path or interruption invalidates it. Interface index/name changes alone
need not imply a different VPN configuration; addresses/masks/peer do.
The live process scopes the baseline to one installed build. Rust checks actual
authenticated UniqueDeviceID against the canonical record, without returning
that identifier to the UI. The record digest therefore also binds device
identity; it is never exported. Configuration and canonical content are checked
again after the operation. A failed TCP attempt cannot claim current device
identity validation, even when its candidate configuration matches the baseline.

Only successful Wi-Fi TCP, authenticated Lockdown, CoreDevice and RSD on an
uninterrupted path arms cellular continuation. The user turns Wi-Fi off in
Settings and keeps cellular and LocalDevVPN enabled. One become-active event
consumes one pending continuation. Duplicate lifecycle events or button presses
cannot run a second attempt. No polling or background retry loop.

## UI and privacy

Primary rows: Pairing, LocalDevVPN, Network, Tunnel, Peer, TCP 62078, Lockdown,
CoreDevice, RSD, Wi-Fi baseline, Cellular comparison. Use NOT_ATTEMPTED downstream
of a failed prerequisite. Show first failure and next action, not raw logs.

Opening official LocalDevVPN is only a request. Recheck on return; do not report
configuration success from URL-open completion. No attempt to modify Wi-Fi or
another app's VPN configuration.

Reports allowlist state names, timing, numeric error codes and necessary route
metadata. Never include pairing contents, certificates, keys, EscrowBag, tokens,
raw Rust error messages, raw device identity or pairing fingerprints. Keep only
a bounded in-memory report. No export of private data to Downloads or clipboard.

## Acceptance and release gates

Local tests cover absent/malformed/rejected pairing separately from refusal,
timeout, ambiguity, baseline mismatch, interruption, lifecycle deduplication and
transport ownership. Generated pinned-source patches must be idempotent.

No new IPA before integration review and local checks. Native Swift/Rust build
and a clean-install physical test remain required; static markers are not proof.
Required device tests are canonical pairing reuse, secure one-time import,
invalid pairing rejection, VPN off, full Wi-Fi baseline, one cellular continuation,
and TCP refusal correctly leaving Lockdown/CoreDevice/RSD unattempted.

## Intentional differences from the historical probe

- No separate storage, automatic picker or PC injection.
- Production IPv4-only utun filter and route extractor are reused unchanged,
  including the upstream exclusion of tunnels that also have IPv6 addresses.
  This restriction is reported by discovery, not silently relaxed in diagnostics.
- All bounded candidates are considered; ambiguity is refused rather than using
  production's first-responder choice. No manual peer override is exposed yet.
- The production TCP helper now retains numeric socket errors while its existing
  Boolean caller interface is preserved. The experimental patch alone selects it.
- Authenticated read-only Lockdown is explicit before the same production tunnel
  implementation. No InstallationProxy Browse is necessary for the RSD gate.
- An active process-local production batch/transport refuses diagnostics. Other
  process and device-side contention remains an on-device test requirement; this
  is not claimed to be a system-wide refresh lock.

## Verification status

Windows repository check: 78 discovered tests, 59 passed, 19 explicitly skipped
at the first full run. Focused pinned-source patch/idempotence and privacy/ownership
source checks pass. The executable Swift state tests are present but were skipped
because `swiftc` is unavailable on this host. No Rust compilation, native iOS
typecheck, combined packaging or on-device acceptance is claimed for this change.
The existing upstream missing-pairing prompt supports cancel without terminating
the app; clean-install navigation to this diagnostic still requires physical
verification. These gates remain open, so this is not ready for a public release.
