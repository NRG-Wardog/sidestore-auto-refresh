#!/usr/bin/env python3
"""Align embedded verification with the host's run identity; combined build only."""
from pathlib import Path
import sys

MARKER = "COMBINED_REFRESH_MANIFEST_V2"


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"combined refresh contract: expected one anchor: {old[:100]!r}")
    return text.replace(old, new, 1)


def patch(root: Path) -> None:
    path = root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        verify(text)
        return
    text = replace_once(text,
        'defaults.set(refreshIdentifier, forKey: "liveContainerAutoRefreshHostHandoffRunID")',
        'defaults.set(defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? refreshIdentifier,\n                     forKey: "liveContainerAutoRefreshHostHandoffRunID")')
    text = replace_once(text,
        'defaults.set(["version": 1, "date": Date(),',
        '// COMBINED_REFRESH_MANIFEST_V2: omissions are not verified success.\n        defaults.set(["version": 2, "date": Date(),\n            "expected_ids": installedApps.map { $0.bundleIdentifier },')
    # The existing helper is itself a raw Python string; its diagnostic Swift
    # must interpolate values rather than print backslash-parenthesis literally.
    start = text.index("    private func automaticRefreshDefaults()")
    end = text.index("    private func startListeningForRunningApps()", start)
    section = text[start:end].replace(r"\\(", r"\(")
    text = text[:start] + section + text[end:]
    verify(text)
    path.write_text(text, encoding="utf-8")


def verify(text: str) -> None:
    for needle in (MARKER, '"expected_ids": installedApps.map',
                   'defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? refreshIdentifier'):
        if needle not in text:
            raise SystemExit(f"combined refresh contract missing {needle}")


def verify_ipa(path: Path) -> dict:
    import hashlib
    import json
    import plistlib
    import struct
    import zipfile
    base = "Payload/LiveContainer.app"
    images = []
    with zipfile.ZipFile(path) as archive:
        info = plistlib.loads(archive.read(base + "/Info.plist"))
        assert info.get("LCRefreshContractVersion") == 2, "Unpatched host configuration"
        assert {"fetch", "processing"} <= set(info.get("UIBackgroundModes", []))
        allowed = info.get("BGTaskSchedulerPermittedIdentifiers", [])
        tasks = [x for x in allowed if x.endswith(".sidestore.automatic-refresh")]
        assert len(tasks) == 1 and tasks[0] + ".watchdog" in allowed
        assert tuple(map(int, info.get("MinimumOSVersion", "999").split("."))) <= (15, 0, 0)
        embedded = archive.read(base + "/Frameworks/SideStoreApp.framework/SideStore")
        assert b"expected_ids" in embedded, "Incomplete-result verification contract not embedded"
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            with archive.open(name) as stream:
                magic = stream.read(4)
            if magic != b"\xcf\xfa\xed\xfe":
                continue
            image = archive.read(name)
            count = struct.unpack_from("<I", image, 16)[0]
            offset = 32
            alarm = None
            for _ in range(count):
                if offset + 8 > len(image):
                    raise ValueError(f"Truncated Mach-O load commands: {name}")
                command, size = struct.unpack_from("<II", image, offset)
                if size < 8 or offset + size > len(image):
                    raise ValueError(f"Malformed Mach-O load command: {name}")
                if command in (0xC, 0x80000018, 0x8000001F):
                    start = struct.unpack_from("<I", image, offset + 8)[0]
                    library = image[offset + start:offset + size].split(b"\0", 1)[0]
                    if b"AlarmKit.framework/AlarmKit" in library:
                        assert command == 0x80000018, f"Hard AlarmKit dependency: {name}"
                        alarm = "weak"
                offset += size
            images.append({"image": name, "alarmkit": alarm or "absent"})
    assert images, "No arm64 images were inspected"
    result = {"runtime_contract": 2, "configuration": "passed", "alarmkit_linkage": images,
              "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "device_runtime": "NOT TESTED; requires signed on-device validation"}
    output = path.with_suffix(".runtime-verification.json")
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--verify-ipa":
        result = verify_ipa(Path(sys.argv[2]))
        print("Combined runtime package verification passed; device runtime NOT TESTED")
        print("SHA256=" + result["sha256"])
    elif len(sys.argv) == 2:
        patch(Path(sys.argv[1]))
    else:
        raise SystemExit("usage: patch_combined_refresh_contract.py <embedded-root> | --verify-ipa <file.ipa>")
