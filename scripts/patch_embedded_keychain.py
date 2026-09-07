#!/usr/bin/env python3
"""Explicit, shared Keychain namespace for combined SideStore UI and LiveProcess.

Run AFTER background and startup patches. No new entitlements, IDs, transport,
plaintext credential storage, or guard bypass. Existing secrets migrate only
within the exact SideStore service and already authorized access groups.
"""
from pathlib import Path
import shutil
import subprocess
import sys

MARKER = "LC_EMBEDDED_SHARED_KEYCHAIN_V1"
TEMPLATE = Path(__file__).parent / "templates/embedded_shared_keychain.swift"


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"embedded keychain: changed upstream anchor {old[:100]!r}")
    return text.replace(old, new, 1)


def patch(root: Path) -> None:
    path = root / "AltStore/Core/Components/Keychain.swift"
    operation = root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
    original = path.read_text(encoding="utf-8")
    text = original
    helper = TEMPLATE.read_text(encoding="utf-8")
    if MARKER not in text:
        text = once(text, "import Foundation\n", "import Foundation\nimport Security\n")
        text = once(text, "KeychainAccess.Keychain(service: Bundle.Info.appbundleIdentifier)\n                                            .accessibility(.afterFirstUnlock)\n                                            .synchronizable(true)",
                    "LCEmbeddedSharedKeychain.makeClient()")
        text = once(text, "case is Data.Type: return try? Keychain.shared.keychain.getData(self.key) as? Value",
                    "case is Data.Type: return LCEmbeddedSharedKeychain.read(self.key, client: Keychain.shared.keychain) as? Value")
        text = once(text, "case is String.Type: return try? Keychain.shared.keychain.getString(self.key) as? Value",
                    "case is String.Type: return LCEmbeddedSharedKeychain.readString(self.key, client: Keychain.shared.keychain) as? Value")
        text = once(text, "case is Data.Type: Keychain.shared.keychain[data: self.key] = newValue as? Data",
                    "case is Data.Type: LCEmbeddedSharedKeychain.write(self.key, data: newValue as? Data, client: Keychain.shared.keychain)")
        text = once(text, "case is String.Type: Keychain.shared.keychain[self.key] = newValue as? String",
                    "case is String.Type: LCEmbeddedSharedKeychain.write(self.key, data: (newValue as? String).map { Data($0.utf8) }, client: Keychain.shared.keychain)")
        text = once(text, "        self.migrateLegacyKeychainItems()", "        LCEmbeddedSharedKeychain.prepare(self.keychain)\n        if LCEmbeddedSharedKeychain.isReady(self.keychain) { self.migrateLegacyKeychainItems() }")
        text = once(text, 'get { try? self.keychain.getData("importedCert_" + serial) }',
                    'get { LCEmbeddedSharedKeychain.read("importedCert_" + serial, client: self.keychain) }')
        text = once(text, '''            if let data = newValue {
                try? self.keychain.set(data, key: "importedCert_" + serial)
            } else {
                try? self.keychain.remove("importedCert_" + serial)
            }''', '            LCEmbeddedSharedKeychain.write("importedCert_" + serial, data: newValue, client: self.keychain)')
        text = once(text, "        try? self.keychain.removeAll()", "        LCEmbeddedSharedKeychain.clearAll(self.keychain)")
        text += "\n" + helper + "\nextension Keychain {\n    func embeddedAuthenticationFailure() -> NSError { LCEmbeddedSharedKeychain.authenticationFailure() }\n}\n"
    elif helper not in text:
        raise ValueError("outdated shared keychain patch: apply to clean pinned source")
    op = operation.read_text(encoding="utf-8")
    if "Keychain.shared.embeddedAuthenticationFailure()" not in op:
        start = op.index("        guard hasPasswordCredentials || hasTokenCredentials || hasReusableSession else {")
        end = op.index('            debugLog("[AUTO_REFRESH] AUTH_PREFLIGHT_FAIL', start)
        # Keep the guard, logging, failure notification, and throwing behavior.
        replacement = "        guard hasPasswordCredentials || hasTokenCredentials || hasReusableSession else {\n            let error = Keychain.shared.embeddedAuthenticationFailure()\n"
        op = op[:start] + replacement + op[end:]
    assert "guard hasPasswordCredentials || hasTokenCredentials || hasReusableSession else" in op
    assert 'throw error' in op
    # Both transforms are validated before writing either file.
    path.write_text(text, encoding="utf-8")
    operation.write_text(op, encoding="utf-8")
    if compiler := shutil.which("swiftc"):
        for file in (path, operation):
            subprocess.run([compiler, "-frontend", "-parse", str(file)], check=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_embedded_keychain.py <embedded-sidestore-root>")
    patch(Path(sys.argv[1]))
