#!/usr/bin/env python3
"""Adapt SideStore 0.7.0 resign flow to this project's self-refresh verification marker."""

from pathlib import Path
import sys

MARKER = "SIDESTORE_SIGN_PASS"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch(root: Path) -> None:
    path = root / "SideStore" / "Core" / "Operations" / "PipelineOperations" / "ResignAppOperation.swift"
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return

    old = '''        let resignedAppURL = try await self.resignAppBundle(at: appBundleURL, team: team, certificate: certificate, profiles: Array(profiles.values))
        guard let resignedAppBundle = ALTApplication(fileURL: resignedAppURL) else { throw OperationError.invalidApp }

        self.debugLog("[ResignAppOperation] Resigned app \\(self.context.bundleIdentifier) to \\(resignedAppBundle.bundleIdentifier).")
'''
    new = '''        let resignedAppURL = try await self.resignAppBundle(at: appBundleURL, team: team, certificate: certificate, profiles: Array(profiles.values))
        guard let resignedAppBundle = ALTApplication(fileURL: resignedAppURL) else { throw OperationError.invalidApp }
        #if !targetEnvironment(simulator)
        guard resignedAppBundle.provisioningProfile != nil else { throw OperationError.invalidApp }
        #endif

        if appBundle.isAltStoreApp {
            self.debugLog("[SELF_REFRESH] SIDESTORE_SIGN_PASS bundle_id=\\(resignedAppBundle.bundleIdentifier)")
        }
        self.debugLog("[ResignAppOperation] Resigned app \\(self.context.bundleIdentifier) to \\(resignedAppBundle.bundleIdentifier).")
'''
    text = replace_once(text, old, new, "SideStore 0.7.0 resign flow")
    path.write_text(text, encoding="utf-8")

    final = path.read_text(encoding="utf-8")
    if MARKER not in final or "provisioningProfile != nil" not in final:
        raise SystemExit("SideStore 0.7.0 signing verification adaptation missing")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: adapt_sidestore_070_signing.py SIDESTORE_ROOT")
    patch(Path(sys.argv[1]))
    print("SideStore 0.7.0 resign flow adapted for self-refresh verification")


if __name__ == "__main__":
    main()
