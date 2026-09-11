#!/usr/bin/env python3
"""Adapt SideStore 0.7.0 minimuxer layout to this project's Lockdown/CoreDevice policy."""

from pathlib import Path
import sys

PAIRING_MARKER = "Composite records must use Lockdown/CoreDevice"
UTUN_MARKER = "[SIDESTORE_COREDEVICE] LOCALVPN_UTUN_ACCEPTED"
PAIRING_MODE_MARKER = "[SIDESTORE_COREDEVICE] PAIRING_MODE_SELECTED"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_pairing(root: Path) -> None:
    pairing_file = root / "Common" / "PairingFile.swift"
    protocol_file = root / "Common" / "PairingProtocol.swift"

    text = pairing_file.read_text(encoding="utf-8")
    if PAIRING_MARKER not in text:
        old = '''        let missingRP = RPPairingFile.missingKeys(in: plist)
        if missingRP.isEmpty {
            return .rppairing
        }

        let missingLockdown = LockdownPairingFile.missingKeys(in: plist)
        if missingLockdown.isEmpty {
            return .lockdown
        }
'''
        new = '''        let missingLockdown = LockdownPairingFile.missingKeys(in: plist)
        // Composite records must use Lockdown/CoreDevice; RemotePairing TCP is not
        // viable for same-device operation on current iOS releases.
        if missingLockdown.isEmpty {
            return .lockdown
        }

        let missingRP = RPPairingFile.missingKeys(in: plist)
        if missingRP.isEmpty {
            return .rppairing
        }
'''
        text = replace_once(text, old, new, "SideStore 0.7.0 pairing parser")
        pairing_file.write_text(text, encoding="utf-8")

    protocol = protocol_file.read_text(encoding="utf-8")
    if PAIRING_MARKER not in protocol:
        anchor = "import Foundation\n"
        protocol = replace_once(
            protocol,
            anchor,
            anchor + "\n// Composite records must use Lockdown/CoreDevice; parser policy lives in PairingFile.swift.\n",
            "PairingProtocol.swift import",
        )
        protocol_file.write_text(protocol, encoding="utf-8")

    final = pairing_file.read_text(encoding="utf-8")
    lockdown = final.index("let missingLockdown = LockdownPairingFile.missingKeys(in: plist)")
    remote = final.index("let missingRP = RPPairingFile.missingKeys(in: plist)", lockdown)
    if lockdown >= remote:
        raise SystemExit("Lockdown-first composite pairing policy was not established")


def patch_localvpn_readiness(root: Path) -> None:
    path = root / "Sources" / "MinimuxerImpl.swift"
    text = path.read_text(encoding="utf-8")
    if UTUN_MARKER in text:
        return

    old = '''                // check iKEv2 too if in lockdown mode and ios >= 26.4
                if self.gateway.pairingFileType != .rppairing && !net.isIKEv2IPSecAvailable {
                    if #available(iOS 26.4, *) {
                        debugLog("[minimuxer] minimuxer not ready: no ipsec interface (required for lockdown on iOS 26.4+)")
                        return .failure(.invalidVPN("utun is present but no ipsec/IKEv2 interface found — LocalDevVPN may not support the lockdown protocol on iOS 26.4+"))
                    }
                }
'''
    new = '''                // The CoreDevice Lockdown path uses the LocalDevVPN utun directly.
                // An additional IKEv2/IPsec interface is not required by this build.
                if self.gateway.pairingFileType != .rppairing {
                    verboseLog("[SIDESTORE_COREDEVICE] LOCALVPN_UTUN_ACCEPTED transport=lockdown-coredevice")
                }
'''
    text = replace_once(text, old, new, "SideStore 0.7.0 LocalDevVPN readiness")
    path.write_text(text, encoding="utf-8")


def patch_pairing_mode_logging(root: Path) -> None:
    path = root / "DeviceGateway" / "idevice" / "IdeviceGateway.swift"
    text = path.read_text(encoding="utf-8")
    if PAIRING_MODE_MARKER in text:
        return

    old = '''            parsedPairingFile = try PairingFileParser.parse(content: pairingFileContent)
            setPairingFileData(parsedPairingFile.rawData)
            setPairingFileType(parsedPairingFile.mode)
'''
    new = '''            parsedPairingFile = try PairingFileParser.parse(content: pairingFileContent)
            setPairingFileData(parsedPairingFile.rawData)
            setPairingFileType(parsedPairingFile.mode)
            debugLog("[SIDESTORE_COREDEVICE] PAIRING_MODE_SELECTED mode=\\(parsedPairingFile.mode)")
'''
    text = replace_once(text, old, new, "SideStore 0.7.0 pairing mode logging")
    path.write_text(text, encoding="utf-8")


def main(root: Path) -> None:
    patch_pairing(root)
    patch_localvpn_readiness(root)
    patch_pairing_mode_logging(root)
    print("SideStore 0.7.0 adapted: Lockdown-first pairing, CoreDevice LocalDevVPN readiness, and pairing diagnostics")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: adapt_sidestore_070_pairing.py MINIMUXER_ROOT")
    main(Path(sys.argv[1]))
