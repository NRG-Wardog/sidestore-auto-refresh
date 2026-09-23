#!/usr/bin/env python3
"""Disable user-copyable SideSign and AnisetteKit log output at its sink."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

PIN = "a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d"
MARKER = "SIDESIGN_USER_LOG_PRIVACY_V1"
LOGGING = Path("Sources/Logging.swift")


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


def patch(root: Path) -> None:
    actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if actual != PIN:
        raise SystemExit(f"SideSign privacy: unpinned source {actual}; expected {PIN}")
    patch_tree(root)


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
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_sidesign_privacy.py <pinned-sidesign-root>")
    patch(Path(sys.argv[1]).resolve())
    print("SideSign user-copyable logging disabled and verified")


if __name__ == "__main__":
    main()
