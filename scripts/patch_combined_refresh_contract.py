#!/usr/bin/env python3
"""Align embedded verification with the host's run identity; combined build only."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile

MARKER = "COMBINED_REFRESH_MANIFEST_V2"
PIN = "ff25922e5c13ccfafd83bda5092910d848ebd409"


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"combined refresh contract: expected one anchor: {old[:100]!r}")
    return text.replace(old, new, 1)


def verify_pin(root: Path) -> None:
    if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() != PIN:
        raise SystemExit("combined refresh contract requires pinned embedded SideStore")


def patch(root: Path) -> None:
    verify_pin(root)
    _patch_verified(root)


def _patch_verified(root: Path) -> None:
    path = root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
    manifest = root / ".combined-refresh-contract.json"
    patch_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    text = path.read_text(encoding="utf-8")
    if manifest.exists():
        if json.loads(manifest.read_text()) != {"pin": PIN, "patch": patch_hash, "output": hashlib.sha256(path.read_bytes()).hexdigest()}:
            raise SystemExit("combined refresh contract replay drift")
        verify(text)
        return
    if MARKER in text:
        raise SystemExit("combined refresh contract marker without matching provenance")
    text = replace_once(text,
        'defaults.set(refreshIdentifier, forKey: "liveContainerAutoRefreshHostHandoffRunID")',
        'defaults.set(defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? refreshIdentifier,\n                     forKey: "liveContainerAutoRefreshHostHandoffRunID")')
    text = replace_once(text,
        'defaults.set(["version": 1, "date": Date(),',
        '// COMBINED_REFRESH_MANIFEST_V2: bind verification to the apps this engine actually attempted.\n'
        '        let requestedIDs = installedApps.map { $0.bundleIdentifier }\n'
        '        let requestedSet = Set(requestedIDs)\n'
        '        let expectedIDs = attemptedAppIDs.filter { requestedSet.contains($0) }\n'
        '        let expectedSet = Set(expectedIDs)\n'
        '        let skippedIDs = requestedIDs.filter { !expectedSet.contains($0) }\n'
        '        defaults.set(["version": 2, "date": Date(),\n'
        '            "schema": "LiveContainerRefreshManifestV2",\n'
        '            "expected_ids": expectedIDs, "requested_ids": requestedIDs, "skipped_ids": skippedIDs,')
    # The existing helper is itself a raw Python string; its diagnostic Swift
    # must interpolate values rather than print backslash-parenthesis literally.
    start = text.index("    private func automaticRefreshDefaults()")
    end = text.index("    private func startListeningForRunningApps()", start)
    section = text[start:end].replace(r"\\(", r"\(")
    section = replace_once(section, '                let nsError = error as NSError', '''                let runID = defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? refreshIdentifier
                let failure = CombinedFailure.capture(error, operation: "refresh", stage: .refreshVerification, id: runID)''')
    section = replace_once(section,
        r'debugLog("[AUTO_REFRESH] REFRESH_FAILED bundle_id=\(bundleIdentifier) stage=refresh error_code=\(nsError.code) error_domain=\(nsError.domain) error=\(error.localizedDescription)")',
        r'debugLog("[AUTO_REFRESH] REFRESH_FAILED \(failure.technicalDetails)")')
    section = replace_once(section,
        '"error_code": nsError.code, "error_domain": nsError.domain,\n                    "error": error.localizedDescription',
        '"error_code": failure.underlyingCode, "error_domain": failure.underlyingDomain,\n                    "error": failure.message, "failure": failure.wire')
    text = text[:start] + section + text[end:]
    verify(text)
    path.write_bytes(text.encode("utf-8"))
    manifest.write_text(json.dumps({"pin": PIN, "patch": patch_hash, "output": hashlib.sha256(path.read_bytes()).hexdigest()}, sort_keys=True))


def patch_combined_cli(root: Path) -> None:
    # Validate the real source revision before even staging the existing Keychain patch.
    verify_pin(root)
    paths = [Path("AltStore/Core/Components/Keychain.swift"),
             Path("SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"),
             Path(".combined-refresh-contract.json")]
    originals = {relative: (root / relative).read_bytes() for relative in paths if (root / relative).exists()}
    from patch_embedded_keychain import patch as patch_shared_keychain
    with tempfile.TemporaryDirectory(prefix="combined-contract-") as directory:
        staged = Path(directory)
        for relative, data in originals.items():
            target = staged / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        if paths[2] in originals:
            # Detect drift before another transformer could accidentally conceal it.
            _patch_verified(staged)
        # Reuse the authoritative Keychain patch unchanged. It also edits the operation,
        # so run it before hashing the final contract output. Neither touches live source
        # until both transformations and Swift parsing have validated successfully.
        patch_shared_keychain(staged)
        for relative in paths[:2]:
            target = staged / relative
            target.write_bytes(target.read_text(encoding="utf-8").encode("utf-8"))
        _patch_verified(staged)
        updates = {relative: (staged / relative).read_bytes() for relative in paths}
    for relative, data in updates.items():
        if originals.get(relative) != data: (root / relative).write_bytes(data)


def verify(text: str) -> None:
    for needle in (MARKER, '"expected_ids": expectedIDs, "requested_ids": requestedIDs, "skipped_ids": skippedIDs',
                   'defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? refreshIdentifier',
                   '"failure": failure.wire', 'REFRESH_FAILED \\(failure.technicalDetails)'):
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
        assert b"LiveContainerRefreshManifestV2" in embedded, "Incomplete-result verification contract not embedded"
        assert b"[LC_KEYCHAIN] SHARED_GROUP_SELECTED" in embedded, "Shared Keychain route missing from embedded executable"
        assert b"LCSharedKeychainReadyV1" in embedded, "Legacy Keychain migration contract missing"
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
    result = {"runtime_contract": 2, "shared_keychain_contract": 1, "configuration": "passed", "alarmkit_linkage": images,
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
        # This CLI is invoked twice by the combined workflow, after the upstream
        # background-operation patch. Standalone SideStore is not changed.
        patch_combined_cli(Path(sys.argv[1]))
    else:
        raise SystemExit("usage: patch_combined_refresh_contract.py <embedded-root> | --verify-ipa <file.ipa>")
