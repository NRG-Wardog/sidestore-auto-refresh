#!/usr/bin/env python3
"""Disable sensitive SideSign and propagated SideStore logging at their sinks."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

PIN = "a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d"
SIDESTORE_PIN = "ff25922e5c13ccfafd83bda5092910d848ebd409"
MARKER = "SIDESIGN_USER_LOG_PRIVACY_V1"
SIDESTORE_MARKER = "SIDESTORE_TRANSITIVE_ERROR_LOG_PRIVACY_V1"
LOGGING = Path("Sources/Logging.swift")
SIDESTORE_LOGGING = Path("SideStore/Core/Logging/SideStoreLogging.swift")
OPERATION_LOGGING = Path("SideStore/Core/Logging/OperationLogging.swift")

SIDESTORE_LOG_FILTER = r'''// SIDESTORE_TRANSITIVE_ERROR_LOG_PRIVACY_V1
// SideSign errors can carry raw GrandSlam/portal/Anisette payloads. Omit those
// lines before they enter Copy Logs; bounded v3 diagnostics carry safe codes.
func shouldOmitUserCopyableSideStoreLog(_ message: String) -> Bool {
    let lowercased = message.lowercased()
    let markers = ["error", "failed", "failure", "cause", "response", "payload", "header",
                   "authorization", "cookie", "dsid", "phone", "pairing", "2fa", "verification",
                   "verification-code", "security code", "security-code", "password", "apple id",
                   "appleid", "token", "anisette", "private key", "certificate der",
                   "mobileprovision", "provisioning profile", "grandslam", "grand slam"]
    return markers.contains { lowercased.contains($0) }
}
'''


def replace_function(text: str, signature: str, replacement: str, next_signature: str) -> str:
    start = text.find(signature)
    if start < 0 or text.find(signature, start + len(signature)) >= 0:
        raise SystemExit(f"SideSign privacy: expected one function signature: {signature}")
    end = text.find(next_signature, start + len(signature))
    if end < 0:
        raise SystemExit(f"SideSign privacy: function end anchor missing: {next_signature}")
    return text[:start] + replacement + "\n\n" + text[end:]


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SystemExit("SideSign privacy: logging configuration anchor drifted")
    return text.replace(old, new, 1)


def patch_tree(root: Path) -> None:
    path = root / LOGGING
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        verify(root)
        return

    text = replace_once(
        text,
        "        isLoggingEnabled = enabled\n        AnisetteKitLogging.setLogging(enabled)",
        f"        // {MARKER}: never enable logs containing authentication or portal response data.\n"
        "        _ = enabled\n        isLoggingEnabled = false\n        AnisetteKitLogging.setLogging(false)",
    )
    text = replace_function(
        text,
        "public func debugLog(_ text: @autoclosure () -> String) {",
        f"public func debugLog(_ text: @autoclosure () -> String) {{\n"
        f"    // {MARKER}: DSID, headers, 2FA bodies, and raw causes stay out of copied logs.\n"
        "    _ = text\n}",
        "public func verboseLog(_ text: @autoclosure () -> String) {",
    )
    text = replace_function(
        text,
        "public func verboseLog(_ text: @autoclosure () -> String) {",
        f"public func verboseLog(_ text: @autoclosure () -> String) {{\n"
        f"    // {MARKER}: verbose output is silent even when callers request it.\n"
        "    _ = text\n}",
        "func prettyJSONString(from object: Any) -> String {",
    )
    path.write_text(text, encoding="utf-8")
    verify(root)


def patch_sidestore_tree(root: Path) -> None:
    side_path = root / SIDESTORE_LOGGING
    operation_path = root / OPERATION_LOGGING
    side_text = side_path.read_text(encoding="utf-8")
    operation_text = operation_path.read_text(encoding="utf-8")
    if SIDESTORE_MARKER in side_text or SIDESTORE_MARKER in operation_text:
        if SIDESTORE_MARKER not in side_text or SIDESTORE_MARKER not in operation_text:
            raise SystemExit("SideStore privacy patch is partial")
        verify_sidestore_tree(root)
        return

    side_text = replace_once(side_text, "public func debugLog(_ text: @autoclosure () -> String) {",
                             SIDESTORE_LOG_FILTER + "\npublic func debugLog(_ text: @autoclosure () -> String) {")
    side_text = replace_function(
        side_text,
        "public func debugLog(_ text: @autoclosure () -> String) {",
        r'''public func debugLog(_ text: @autoclosure () -> String) {
    let rawMessage = text()
    guard !shouldOmitUserCopyableSideStoreLog(rawMessage) else { return }
    let message = formatLogMessage(rawMessage)
    if !message.isEmpty && message.allSatisfy({ $0 == "\n" || $0 == "\r" }) {
        print(message, terminator: "")
    } else {
        print("\(getTag(level: "[D]"))\(message)")
    }
}''',
        "public func verboseLog(_ text: @autoclosure () -> String) {",
    )
    side_text = replace_function(
        side_text,
        "public func verboseLog(_ text: @autoclosure () -> String) {",
        r'''public func verboseLog(_ text: @autoclosure () -> String) {
    guard SideStoreLogging.isLoggingEnabled else { return }
    let rawMessage = text()
    guard !shouldOmitUserCopyableSideStoreLog(rawMessage) else { return }
    let message = formatLogMessage(rawMessage)
    if !message.isEmpty && message.allSatisfy({ $0 == "\n" || $0 == "\r" }) {
        print(message, terminator: "")
    } else {
        print("\(getTag(level: "[V]"))\(message)")
    }
}''',
        "public func formatLogMessage(_ message: String) -> String {",
    )

    operation_text = replace_function(
        operation_text,
        "    func debugLog(_ text: @autoclosure () -> String) {",
        r'''    func debugLog(_ text: @autoclosure () -> String) {
        // SIDESTORE_TRANSITIVE_ERROR_LOG_PRIVACY_V1
        let message = text()
        guard !shouldOmitUserCopyableSideStoreLog(message) else { return }
        if !message.isEmpty && message.allSatisfy({ $0 == "\n" || $0 == "\r" }) {
            print(message, terminator: "")
        } else {
            print("\(getOperationsLogTag(level: "[D]"))\(message)")
        }
    }''',
        "    func verboseLog(_ text: @autoclosure () -> String) {",
    )
    operation_text = replace_function(
        operation_text,
        "    func verboseLog(_ text: @autoclosure () -> String) {",
        r'''    func verboseLog(_ text: @autoclosure () -> String) {
        guard OperationsLoggingControl.isLoggingEnabled(for: type(of: self)) else { return }
        let message = text()
        guard !shouldOmitUserCopyableSideStoreLog(message) else { return }
        if !message.isEmpty && message.allSatisfy({ $0 == "\n" || $0 == "\r" }) {
            print(message, terminator: "")
        } else {
            print("\(getOperationsLogTag(level: "[V]"))\(message)")
        }
    }
}''',
        "func logOperationSummary(",
    )
    operation_text = replace_once(
        operation_text,
        '    if let error = error {\n'
        '        rows.append(contentsOf: bulletRow("Error", error.localizedDescription))\n'
        '    }',
        '    if error != nil {\n'
        '        rows.append(contentsOf: bulletRow("Error", "[details omitted]"))\n'
        '    }',
    )
    side_path.write_text(side_text, encoding="utf-8")
    operation_path.write_text(operation_text, encoding="utf-8")
    verify_sidestore_tree(root)


def verify_sidestore_tree(root: Path) -> None:
    side_text = (root / SIDESTORE_LOGGING).read_text(encoding="utf-8")
    operation_text = (root / OPERATION_LOGGING).read_text(encoding="utf-8")
    for text in (side_text, operation_text):
        if SIDESTORE_MARKER not in text:
            raise SystemExit("SideStore privacy marker is missing")
    if "shouldOmitUserCopyableSideStoreLog(rawMessage)" not in side_text:
        raise SystemExit("SideStore debug/verbose log sink bypasses privacy filter")
    if operation_text.count("shouldOmitUserCopyableSideStoreLog(message)") != 2:
        raise SystemExit("Operation log sinks bypass privacy filter")
    if "error.localizedDescription" in operation_text:
        raise SystemExit("operation summary still copies raw error descriptions")


def patch(root: Path, sidestore_root: Path | None = None) -> None:
    actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if actual != PIN:
        raise SystemExit(f"SideSign privacy: unpinned source {actual}; expected {PIN}")
    patch_tree(root)
    if sidestore_root is not None:
        actual_side = subprocess.check_output(["git", "-C", str(sidestore_root), "rev-parse", "HEAD"], text=True).strip()
        if actual_side != SIDESTORE_PIN:
            raise SystemExit(f"SideStore privacy: unpinned source {actual_side}; expected {SIDESTORE_PIN}")
        patch_sidestore_tree(sidestore_root)


def patch_sidestore(sidestore_root: Path) -> None:
    actual = subprocess.check_output(["git", "-C", str(sidestore_root), "rev-parse", "HEAD"], text=True).strip()
    if actual != SIDESTORE_PIN:
        raise SystemExit(f"SideStore privacy: unpinned source {actual}; expected {SIDESTORE_PIN}")
    patch_sidestore_tree(sidestore_root)


def verify(root: Path) -> None:
    path = root / LOGGING
    text = path.read_text(encoding="utf-8")
    for value in (
        MARKER,
        "AnisetteKitLogging.setLogging(false)",
        "public func debugLog(_ text: @autoclosure () -> String)",
        "public func verboseLog(_ text: @autoclosure () -> String)",
    ):
        if value not in text:
            raise SystemExit(f"SideSign privacy: missing {value}")
    for signature in ("public func debugLog", "public func verboseLog"):
        start = text.index(signature)
        end = text.index("\n}", start) + 2
        if "print(" in text[start:end] or "NSLog(" in text[start:end]:
            raise SystemExit(f"SideSign privacy: log sink still emits output in {signature}")

    # Logging.swift is the only stdout sink in the pinned library. Any new sink
    # must be audited explicitly instead of bypassing the privacy boundary.
    for source in (root / "Sources").rglob("*.swift"):
        contents = source.read_text(encoding="utf-8")
        if source == path:
            continue
        if any(token in contents for token in ("print(", "debugPrint(", "NSLog(", "os_log(")):
            raise SystemExit(f"SideSign privacy: unreviewed direct logging sink in {source.relative_to(root)}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: patch_sidesign_privacy.py <pinned-sidesign-root> <pinned-sidestore-root>")
    patch(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    print("SideSign sinks disabled and transitive SideStore error logs omitted")


if __name__ == "__main__":
    main()
