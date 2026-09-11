#!/usr/bin/env python3
"""Adapt SideStore 0.7.0 minimuxer pairing parser to the project's Lockdown-first policy."""

from pathlib import Path
import sys

MARKER = "Composite records must use Lockdown/CoreDevice"


def main(root: Path) -> None:
    pairing_file = root / "Common" / "PairingFile.swift"
    protocol_file = root / "Common" / "PairingProtocol.swift"

    text = pairing_file.read_text(encoding="utf-8")
    if MARKER not in text:
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
        if text.count(old) != 1:
            raise SystemExit(f"SideStore 0.7.0 pairing parser anchor mismatch: found {text.count(old)}")
        text = text.replace(old, new, 1)
        pairing_file.write_text(text, encoding="utf-8")

    protocol = protocol_file.read_text(encoding="utf-8")
    if MARKER not in protocol:
        anchor = "import Foundation\n"
        if protocol.count(anchor) != 1:
            raise SystemExit("PairingProtocol.swift import anchor mismatch")
        protocol = protocol.replace(
            anchor,
            anchor + "\n// Composite records must use Lockdown/CoreDevice; parser policy lives in PairingFile.swift.\n",
            1,
        )
        protocol_file.write_text(protocol, encoding="utf-8")

    final = pairing_file.read_text(encoding="utf-8")
    lockdown = final.index("let missingLockdown = LockdownPairingFile.missingKeys(in: plist)")
    remote = final.index("let missingRP = RPPairingFile.missingKeys(in: plist)", lockdown)
    if lockdown >= remote:
        raise SystemExit("Lockdown-first composite pairing policy was not established")
    print("SideStore 0.7.0 pairing parser adapted: Lockdown preferred for composite records")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: adapt_sidestore_070_pairing.py MINIMUXER_ROOT")
    main(Path(sys.argv[1]))
