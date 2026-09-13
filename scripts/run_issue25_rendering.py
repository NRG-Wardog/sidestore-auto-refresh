#!/usr/bin/env python3
"""Execute generated production layout components on real iOS simulators.

This is a bounded rendering harness, not a signed product or on-device claim.
Dependencies controlling app data and action routing are isolated from production
guest execution. Baseline success REQUIRES a measured geometry/visibility failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "7d8ae12905f8baa6e0ecc4dbdd3f25e2aa0e43fa"
GRID = "scripts/templates/livecontainer_grid_app_cell.swift"


def command(*args: str, **kwargs) -> str:
    result = subprocess.run(list(args), check=True, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, **kwargs)
    if result.stdout:
        print(result.stdout, end="", flush=True)
    return result.stdout.strip()


def available_devices() -> list[tuple[str, str, str]]:
    result = json.loads(subprocess.check_output(["xcrun", "simctl", "list", "devices", "available", "--json"], text=True))
    runtimes = json.loads(subprocess.check_output(["xcrun", "simctl", "list", "runtimes", "--json"], text=True))["runtimes"]
    supported = sorted((item for item in runtimes if item.get("isAvailable") and ".iOS-" in item["identifier"]),
                       key=lambda item: tuple(int(part) for part in item["version"].split(".")))
    if not supported:
        raise RuntimeError("No available iOS simulator runtime; rendering was not validated")
    # The oldest actually installed runtime is exercised. Do not imply iOS 15 was
    # executed merely because compilation supports that deployment target.
    runtime = supported[0]
    devices = result["devices"].get(runtime["identifier"], [])
    selected = []
    for kind, needle in (("phone", "iPhone"), ("tablet", "iPad")):
        candidates = [device for device in devices if needle in device["name"] and device.get("isAvailable")]
        if not candidates:
            raise RuntimeError(f"No available {kind} simulator for oldest runtime {runtime['version']}")
        # Larger tablets accommodate logical window widths through 1024 points.
        candidates.sort(key=lambda item: ("13-inch" not in item["name"], "Pro" not in item["name"], item["name"]))
        selected.append((kind, candidates[0]["udid"], runtime["version"]))
    return selected


def build_app(output: Path, live: Path, baseline: bool) -> tuple[Path, str, dict]:
    name = "baseline" if baseline else "corrected"
    build = output / name
    build.mkdir(parents=True, exist_ok=True)
    bundle = build / "Issue25Rendering.app"
    bundle.mkdir(exist_ok=True)
    bundle_id = "org.sidestore.layout.fixture." + name
    grid = build / "LCGridAppCell.swift"
    if baseline:
        data = subprocess.check_output(["git", "-C", str(ROOT), "show", BASELINE + ":" + GRID])
        grid.write_bytes(data)
    else:
        shutil.copyfile(live / "LiveContainerSwiftUI/Views/AppList/LCGridAppCell.swift", grid)
    relative_sources = [
        "LiveContainerSwiftUI/Models/AppLayoutStyle.swift",
        "LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBanner.swift",
        "LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBannerView.swift",
    ]
    sources = [live / relative for relative in relative_sources]
    sources += [grid, ROOT / "tests/fixtures/issue25_rendering_dependencies.swift", ROOT / "tests/fixtures/issue25_rendering_harness.swift"]
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    sdk = subprocess.check_output(["xcrun", "--sdk", "iphonesimulator", "--show-sdk-path"], text=True).strip()
    architecture = "arm64" if platform.machine() == "arm64" else "x86_64"
    flags = [] if baseline else ["-D", "CORRECTED_GRID", "-D", "CORRECTED_BANNER"]
    command("xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-sdk", sdk,
            "-target", architecture + "-apple-ios15.0-simulator", "-g", "-Onone", *flags,
            *map(str, sources), "-o", str(bundle / "Issue25Rendering"))
    info = {
        "CFBundleExecutable": "Issue25Rendering", "CFBundleIdentifier": bundle_id,
        "CFBundleName": "Issue25 Rendering", "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1", "CFBundleShortVersionString": "1.0", "MinimumOSVersion": "15.0",
        "LSRequiresIPhoneOS": True, "UIDeviceFamily": [1, 2],
        "UILaunchScreen": {}, "UISupportedInterfaceOrientations": ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"],
    }
    (bundle / "Info.plist").write_bytes(plistlib.dumps(info))
    command("codesign", "--force", "--sign", "-", str(bundle))
    return bundle, bundle_id, hashes


def execute(bundle: Path, bundle_id: str, kind: str, device: str, output: Path, baseline: bool, cold: bool) -> dict:
    phase = "cold" if cold else "suite"
    command("xcrun", "simctl", "terminate", device, bundle_id) if cold else None
    if not cold:
        command("xcrun", "simctl", "install", device, str(bundle))
    data_root = Path(command("xcrun", "simctl", "get_app_container", device, bundle_id, "data"))
    report_path = data_root / "Documents" / f"{kind}-{phase}.json"
    if report_path.exists():
        # Exact fixture-owned stale result only; no app/user data is reset.
        report_path.unlink()
    args = ["--tablet"] if kind == "tablet" else []
    if baseline:
        args.append("--baseline")
    if cold:
        args.append("--cold")
    command("xcrun", "simctl", "launch", device, bundle_id, *args)
    deadline = time.monotonic() + 180
    while not report_path.exists() and time.monotonic() < deadline:
        time.sleep(0.5)
    if not report_path.exists():
        raise RuntimeError(f"Simulator rendering did not produce {kind}/{phase} evidence within 180 seconds")
    report = json.loads(report_path.read_text())
    destination = output / ("baseline" if baseline else "corrected")
    for path in (data_root / "Documents").iterdir():
        if path.name.startswith(kind + "-") and path.suffix in (".png", ".json"):
            shutil.copyfile(path, destination / path.name)
    print(json.dumps({"mode": report["mode"], "deviceClass": kind, "phase": phase,
                      "passed": report["passed"], "failures": report["failures"]}), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--livecontainer", type=Path, required=True, help="Already patched generated LiveContainer checkout")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if platform.system() != "Darwin":
        raise SystemExit("This executable rendering suite requires macOS with Xcode and iOS simulators")
    builds = {baseline: build_app(output, args.livecontainer.resolve(), baseline) for baseline in (True, False)}
    reports = []
    devices = available_devices()
    try:
        for kind, device, runtime in devices:
            state = json.loads(subprocess.check_output(["xcrun", "simctl", "list", "devices", "--json"], text=True))
            booted = any(item["udid"] == device and item["state"] == "Booted" for group in state["devices"].values() for item in group)
            if not booted:
                command("xcrun", "simctl", "boot", device)
            command("xcrun", "simctl", "bootstatus", device, "-b")
            for baseline in (True, False):
                bundle, bundle_id, _ = builds[baseline]
                reports.append(execute(bundle, bundle_id, kind, device, output, baseline, False))
                if not baseline:
                    reports.append(execute(bundle, bundle_id, kind, device, output, False, True))
            if not booted:
                command("xcrun", "simctl", "shutdown", device)
    finally:
        metadata = {
            "schemaVersion": 1, "builderCommit": command("git", "-C", str(ROOT), "rev-parse", "HEAD"),
            "ciRun": os.environ.get("GITHUB_RUN_ID"), "baselineBuilderCommit": BASELINE,
            "sourceSHA256": {"baseline": builds[True][2], "corrected": builds[False][2]},
            "simulatorRuntimes": sorted(set(runtime for _, _, runtime in devices)),
            "passed": len(reports) == 6 and all(report["passed"] for report in reports),
            "reportCount": len(reports), "physicalDeviceExecution": False,
        }
        (output / "rendering-verification.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    if not metadata["passed"]:
        raise SystemExit("Issue25 simulator rendering regression failed; inspect measured JSON, not only build/markers")


if __name__ == "__main__":
    main()
