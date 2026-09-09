# Standalone Cellular Probe

**Historical experiment.** The replacement bootstrap is an opt-in embedded
SideStore Diagnostics module described in [BOOTSTRAP.md](BOOTSTRAP.md). Do not
use PC-side pairing injection as a supported setup flow. The standalone report
does not establish a cellular comparison without a successful matching Wi-Fi
baseline. The embedded replacement is not yet device-validated.

This is a separate read-only iOS application, not LiveContainer or embedded
SideStore. Its dedicated workflow builds one app bundle and zero extensions.
It reuses the pinned idevice/jktcp revisions and the existing validated transport
patches, not the SideStore scheduler or Wi-Fi readiness guard.

The earlier diagnostic recipe used direct UIKit-to-Rust FFI compilation.
This app keeps that approach, but does not embed pairing records or private keys.
Import your own Lockdown pairing record from Files. It is stored with complete
file protection in Application Support, excluded from backup and log sharing.
Only the latest bounded report is saved in Documents. There is no Apple login.

## Test

1. Sign/install `CellularProbe.ipa` as a separate app. Do not replace LiveContainer.
2. Open it and choose the valid pairing record in the file picker once.
   Import is followed automatically by network detection, peer discovery and a
   read-only test. Later launches reuse the protected pairing file.
3. With Wi-Fi and official LocalDevVPN on, the first test is a Wi-Fi baseline.
4. Disable Wi-Fi in Settings (not merely disconnect in Control Center), enable
   cellular and keep official LocalDevVPN on. Reopen this diagnostic app.
5. After a successful baseline, returning with cellular-only connectivity starts
   the pending cellular test automatically. Keep the app foreground and share
   the report. **Run Again** explicitly starts a fresh test on the current path.
6. Repeat without USB attached once the first test completes.

No VPN Super, external relay, PC runtime service or automatic network toggle is
used. An `ipsec` interface flag alone does not identify a VPN provider.

Peer discovery follows the pinned minimuxer interface/route approach: active
IPv4 `utun`, route gateway, host destination, then point-to-point peer. Unlike
the upstream selection filter, this diagnostic does not discard an IPv4 tunnel
merely because it also has an IPv6 address. It rejects local/default/multicast
addresses, never guesses adjacent addresses, and never enumerates LAN subnets.
The native route parser bounds-checks every message and sockaddr. At most eight
discovered candidates receive one two-second TCP probe each. Exactly one must
answer; zero or multiple responders produce an explicit failure. The slider
button exposes an optional explicit peer override for ambiguous configurations.
TCP reachability does not identify the VPN provider or prove protocol success.

The app cannot toggle iOS Wi-Fi or read another app's private pairing storage.
It does not embed credentials, change VPN settings, or repeatedly retry on a
timer. Automatic continuation uses app lifecycle events and a single pending
cellular-test flag, not background polling.

The call named `tunnel_create_usb` is an upstream name: here it receives a TCP
provider addressed to the entered peer on port 62078, not a USB provider.
The path is CoreDeviceProxy with service TLS, contiguous CDTunnel, managed
heartbeat, jktcp IPv6, RSD and InstallationProxy Browse. No AFC writes, signing,
installation or refresh are performed. All handles are released after the call.

PASS only means non-empty Browse succeeded with the required network snapshots
before and after the operation, without an observed background interruption.
Snapshots are not continuous packet-route evidence. Cellular refresh is not
proven by this test. Failed/unknown snapshots fail closed. A blocked FFI call
retains ownership until it returns; no timeout handler frees in-use pointers.
The timestamp of the last BEGIN identifies the stage still waiting.

## Checks

`python -m unittest discover -s tests -p test_cellular_probe.py -v`

The workflow typechecks UIKit against the actual iOS SDK before iOS Rust
compilation, builds the pinned patched transport, links its reported native
dependencies, ad-hoc signs the app for later user re-signing, and verifies the
single-bundle IPA. No public release is created. Device behavior requires the
physical tests above and is not inferred from compilation.
