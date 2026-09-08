# Standalone Cellular Probe

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
2. Import the valid pairing record, enter LocalDevVPN's Device / Peer IPv4.
3. With Wi-Fi and official LocalDevVPN on, run **Wi-Fi baseline** once.
4. Disable Wi-Fi in Settings (not merely disconnect in Control Center), enable
   cellular and keep official LocalDevVPN on. Reopen this diagnostic app.
5. Select **Cellular**, run once, keep the app foreground, and share the report.
6. Repeat without USB attached once the first test completes.

No VPN Super, external relay, PC runtime service or automatic network toggle is
used. An `ipsec` interface flag alone does not identify a VPN provider.

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
