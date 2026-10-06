#!/usr/bin/env python3
"""Build exact production P0 sign-in controls, then exercise them using real XCTest UI input.

Called by run_issue25_rendering in its existing booted-device loop. This module
never discovers, boots, erases, or creates a simulator. Backend calls are local,
deterministic fixtures; these results do not establish real account operations.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import plistlib
from pathlib import Path
import re
import shutil
import stat
import struct
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
DECLARATIONS = (
    "struct V3PromptSection", "enum V3MultiSelectPromptAnswerPolicy",
    "enum V3AuthRepairURLPolicy", "enum V3TwoFactorStep",
    "enum V3AuthPromptFailurePolicy", "enum V3AuthFailureDiagnosticsPolicy",
)
VIEW_MEMBERS = (
    "var body: some View", "private var shouldShowAccountSection: Bool",
    "private var visiblePromptFailure: [String: Any]?",
    "private var promptFailureMessage: String", "private var promptFailureDetails: String",
)
REQUIRED_CASES = {"credentials-default", "credentials-largest", "submitting-default", "submitting-largest"}
REQUIRED_SCREENSHOTS = {name: {"prompt-top", "copy-details", "cancel-reachable"} for name in REQUIRED_CASES}
REQUIRED_CONTROLS = {"username", "password", "copy-details", "cancel"}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
EVIDENCE_RUN_ID_KEY = "P0_SIGNIN_EVIDENCE_RUN_ID"
MAX_DURABLE_FILES = 64
MAX_DURABLE_FILE_BYTES = 16 * 1024 * 1024
MAX_DURABLE_TOTAL_BYTES = 128 * 1024 * 1024


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_png(data: bytes) -> bool:
    """Check screenshot chunks AND bounded decoded pixel scanlines (including Adam7)."""
    if not data.startswith(PNG_MAGIC):
        return False
    offset = 8
    header = None
    compressed = bytearray()
    palette = False
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        end = offset + 12 + length
        if end > len(data):
            return False
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            return False
        if header is None:
            if kind != b"IHDR" or length != 13:
                return False
            header = struct.unpack(">IIBBBBB", payload)
            width, height, depth, color, compression, filtering, interlace = header
            allowed = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if (not 100 <= width <= 8192 or not 100 <= height <= 8192 or
                    depth not in allowed.get(color, set()) or compression != 0 or filtering != 0 or interlace not in (0, 1)):
                return False
        elif kind == b"IHDR":
            return False
        if kind == b"PLTE":
            palette = 0 < length <= 768 and length % 3 == 0
        if kind == b"IDAT":
            compressed.extend(payload)
        if kind == b"IEND":
            if not header or not compressed or length != 0 or end != len(data):
                return False
            width, height, depth, color, _, _, interlace = header
            if color == 3 and not palette:
                return False
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
            passes = [(0, 0, 1, 1)] if interlace == 0 else [
                (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
                (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
            rows = []
            for x, y, dx, dy in passes:
                pass_width = max(0, (width - x + dx - 1) // dx)
                pass_height = max(0, (height - y + dy - 1) // dy)
                if pass_width and pass_height:
                    rows.extend([1 + (pass_width * channels * depth + 7) // 8] * pass_height)
            expected = sum(rows)
            if expected > 128 * 1024 * 1024:
                return False
            try:
                decoder = zlib.decompressobj()
                pixels = decoder.decompress(compressed, expected + 1)
                if (len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail):
                    return False
            except zlib.error:
                return False
            position = 0
            for row_bytes in rows:
                if pixels[position] > 4:
                    return False
                position += row_bytes
            return True
        offset = end
    return False


def declaration(source: str, signature: str) -> str:
    """Extract an unmodified top-level declaration; reject ambiguity and drift.

    Production uses an unindented closing brace for top-level types. Anchoring
    to complete lines avoids treating interpolated/string braces as syntax.
    """
    matches = list(re.finditer(r"^" + re.escape(signature) + r"(?:\s|:|\{)", source, re.M))
    if len(matches) != 1:
        raise ValueError(f"Expected one production {signature}, found {len(matches)}")
    start = matches[0].start()
    # Preserve the actor annotation on the store exactly as generated.
    if source[max(0, start - 11):start] == "@MainActor\n":
        start -= 11
    closing = re.search(r"^}\s*$", source[matches[0].end():], re.M)
    if not closing:
        raise ValueError(f"Unclosed production declaration: {signature}")
    end = matches[0].end() + closing.end()
    return source[start:end].rstrip() + "\n"


def member(source: str, signature: str) -> str:
    """Extract one complete four-space member verbatim; fail on absent/drifted anchors."""
    matches = list(re.finditer(r"^    " + re.escape(signature) + r"(?:\s|\{|\()", source, re.M))
    if len(matches) != 1:
        raise ValueError(f"Expected one production member {signature}, found {len(matches)}")
    start = matches[0].start()
    line_end = source.find("\n", start)
    if source[start:line_end].rstrip().endswith("}"):
        return source[start:line_end + 1]
    closing = re.search(r"^    }[ \t]*$", source[matches[0].end():], re.M)
    if not closing:
        raise ValueError(f"Unclosed production member: {signature}")
    end = matches[0].end() + closing.end()
    return source[start:end] + "\n"


def extract_sources(source: Path, templates: Path) -> tuple[dict[str, str], dict]:
    """Only production code owns control layout and diagnostic rendering.

    The generated shell contains its behavioral primitives. Supporting templates
    are the unchanged production inputs from this exact builder checkout.
    """
    source_text = source.read_text()
    hashes = {"generated-shell": digest(source)}
    extracted = {}
    def retain(name, value):
        hashes[name] = hashlib.sha256(value.encode()).hexdigest()
        return value
    imports = "import Foundation\nimport SwiftUI\nimport UIKit\nimport CoreFoundation\n"
    for signature in DECLARATIONS:
        name = signature.split()[-1]
        extracted[name + ".swift"] = imports + retain(name, declaration(source_text, signature))
    view = declaration(source_text, "struct V3SignInView")
    members = "\n".join(retain("V3SignInView." + item, member(view, item)) for item in VIEW_MEMBERS)
    extracted["V3SignInView.swift"] = imports + """// Exact production body/properties, with injected fixture-owned service state.
// accountContent is empty ONLY for unsigned-in credentials with no recovery,
// cancellation-in-progress, progress message, or account diagnostics content.
// No layout claims after Cancel: only callback and button-disabled state are tested.
// This does not cover signed-in, terminal, recovery or cancellation-pending layout.
@MainActor struct V3SignInView: View {
    @ObservedObject var auth: V3AuthStore
    @EnvironmentObject private var status: FixtureStatusStore
    private var accountContent: some View { EmptyView() }
""" + members + "}\n"
    hashes["V3SignInView.full-production"] = hashlib.sha256(view.encode()).hexdigest()
    extracted["V3SignInView.production.txt"] = view
    auth = declaration(source_text, "final class V3AuthStore")
    auth_members = ["static func failureMessage", "static func failureDetails"]
    if "failureMessageWithoutDiagnosticCode" in auth:
        auth_members.append("private static func failureMessageWithoutDiagnosticCode")
    extracted["V3AuthStoreDiagnostics.swift"] = imports + "extension V3AuthStore {\n" + "\n".join(
        retain("V3AuthStore." + item, member(auth, item)) for item in auth_members) + "}\n"
    for typename, filename, signatures in (
        ("V3WireContract", "v3_wire_contract.swift", ("static func strictBool", "static func strictInt")),
        ("V3ServiceBridge", "v3_service_bridge.swift", ("public static func strictBool", "public static func strictInt")),
    ):
        text = (templates / filename).read_text()
        hashes["production-template/" + filename] = digest(templates / filename)
        extracted[typename + ".swift"] = imports + "enum " + typename + " {\n" + "\n".join(
            retain(typename + "." + item, member(text, item)) for item in signatures) + "}\n"
    combined = templates / "combined_failure.swift"
    extracted["CombinedFailure.swift"] = combined.read_text()
    hashes["production-template/combined_failure.swift"] = digest(combined)
    return extracted, hashes


def prepare(output: Path, source: Path, command) -> dict:
    build = output / "p0-build"
    build.mkdir()
    sources = build / "Sources"
    sources.mkdir()
    extracted, hashes = extract_sources(source, ROOT / "scripts/templates")
    for filename, content in extracted.items():
        path = sources / filename
        path.write_text(content)
        hashes[filename] = digest(path)
    for filename in ("p0_signin_app.swift", "p0_signin_ui_tests.swift"):
        path = sources / filename
        shutil.copyfile(ROOT / "tests/fixtures" / filename, path)
        hashes[filename] = digest(path)
    project = build / "P0SignIn.xcodeproj"
    shutil.copytree(ROOT / "tests/fixtures/p0_signin_project", project)
    # The diagnostic build tag is the actual builder checkout, not a user value.
    builder_commit = command("git", "-C", str(ROOT), "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", builder_commit):
        raise ValueError("Cannot record a verified builder commit for the fixture")
    info = project / "Info.plist"
    plist = plistlib.loads(info.read_bytes())
    plist["LCBuilderCommit"] = builder_commit
    info.write_bytes(plistlib.dumps(plist))
    for path in sorted(project.rglob("*")):
        if path.is_file():
            hashes["project/" + str(path.relative_to(project))] = digest(path)
    evidence = output / "p0-signin"
    evidence.mkdir()
    shutil.copytree(sources, evidence / "sources")
    shutil.copytree(project, evidence / "project")
    identity = {"schemaVersion": 1, "sourceSHA256": hashes,
                "productionSource": str(source), "sourceInstrumentation": "none in extracted members",
                "scope": "unsigned-in credentials + prior unknown failure; no account/recovery content",
                "omittedProductionMember": "V3SignInView.accountContent (empty for this exact state)",
                "bridge": "deterministic state/cancel fixture; no backend or network execution"}
    (evidence / "source-identity.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    command("xcodebuild", "build-for-testing", "-project", str(project), "-scheme", "P0SignIn",
            "-sdk", "iphonesimulator", "-destination", "generic/platform=iOS Simulator",
            "-derivedDataPath", str(build / "DerivedData"), "-jobs", "2", "CODE_SIGNING_ALLOWED=NO",
            "CODE_SIGNING_REQUIRED=NO", timeout=300)
    candidates = list((build / "DerivedData/Build/Products").glob("*.xctestrun"))
    if len(candidates) != 1:
        raise RuntimeError(f"Expected exactly one P0 sign-in UI test run, found {len(candidates)}")
    return {"xctestrun": str(candidates[0]), "sourceSHA256": hashes}


def verify_export(directory: Path, summary: dict) -> dict:
    """Do not turn source/string checks or a partial xcresult into UI proof."""
    failures = []
    if summary.get("result") != "Passed" or summary.get("failedTests") != 0:
        failures.append("XCTest did not report Passed with zero failures")
    if summary.get("passedTests") != len(REQUIRED_CASES) or summary.get("skippedTests") != 0:
        failures.append("Expected four passed XCTest methods with zero skips")
    cases = []
    artifacts = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if data.startswith(PNG_MAGIC):
            if not valid_png(data):
                failures.append(f"Invalid or truncated screenshot: {path.name}")
            artifacts[str(path.relative_to(directory))] = hashlib.sha256(data).hexdigest()
        try:
            report = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(report, dict) and report.get("schema") == "p0-signin-case-v1":
            cases.append(report)
            artifacts[str(path.relative_to(directory))] = hashlib.sha256(data).hexdigest()
    if len(cases) != len(REQUIRED_CASES) or {case.get("case") for case in cases} != REQUIRED_CASES:
        failures.append("Missing or duplicated P0 sign-in case reports")
    for case in cases:
        if case.get("passed") is not True or case.get("failures") != []:
            failures.append(f"Failed P0 sign-in case: {case.get('case')}")
        if type(case.get("xctestFailureCount")) is not int or case.get("xctestFailureCount") != 0 or case.get("teardownCaptured") is not True:
            failures.append(f"Missing clean XCTest teardown proof: {case.get('case')}")
        measurements = case.get("measurements")
        if not isinstance(measurements, list) or not measurements:
            failures.append(f"Missing measured control geometry: {case.get('case')}")
        else:
            for measurement in measurements:
                frame = measurement.get("bounds", [])
                if (not isinstance(frame, list) or len(frame) != 4 or
                        not all(isinstance(value, (int, float)) and math.isfinite(value) for value in frame) or
                        frame[2] <= 0 or frame[3] <= 0 or measurement.get("hittable") is not True or
                        not measurement.get("label")):
                    failures.append(f"Invalid runtime measurement: {case.get('case')}")
        expected_largest = case.get("case", "").endswith("-largest")
        expected_submitting = case.get("case", "").startswith("submitting-")
        for proof in ("clipboardExactMatch", "safeDiagnosticOnly", "cancelInvoked", "noRedundantStatusPanel", "oneCredentialsPanel"):
            if case.get(proof) is not True:
                failures.append(f"Missing runtime proof {proof}: {case.get('case')}")
        if (case.get("largestDynamicType") is not expected_largest or case.get("submitting") is not expected_submitting or
                case.get("submissionInvoked") is not expected_submitting or case.get("priorFailureCleared") is not expected_submitting):
            failures.append(f"Wrong fixture state or Dynamic Type: {case.get('case')}")
        if isinstance(measurements, list):
            if {item.get("control") for item in measurements} != REQUIRED_CONTROLS:
                failures.append(f"Missing required control measurements: {case.get('case')}")
            for item in measurements:
                frame, viewport = item.get("bounds", []), item.get("viewportBounds", [])
                if (len(frame) != 4 or len(viewport) != 4 or
                        not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                                for value in frame + viewport) or
                        abs(viewport[2] - 320) > 1 or viewport[3] <= 0 or
                        item.get("viewportWidth") != viewport[2] or
                        item.get("largestDynamicType") is not expected_largest or
                        (item.get("control") in {"copy-details", "cancel"} and frame[3] < 43) or
                        frame[0] < viewport[0] - 1 or frame[0] + frame[2] > viewport[0] + viewport[2] + 1 or
                        frame[1] < viewport[1] - 1 or frame[1] + frame[3] > viewport[1] + viewport[3] + 1):
                    failures.append(f"Invalid 320-point visible control bounds: {case.get('case')}")
        required = case.get("screenshots")
        if not isinstance(required, list) or not required:
            failures.append(f"Missing screenshot requirements: {case.get('case')}")
        expected = {f"p0-{case.get('case')}-{suffix}" for suffix in REQUIRED_SCREENSHOTS.get(case.get("case"), set())}
        if not isinstance(required, list) or set(required) != expected:
            failures.append(f"Screenshot matrix is incomplete: {case.get('case')}")
    # Attachment export's manifest maps the XCTest name to its actual file.
    manifests = []
    for path in directory.rglob("manifest.json"):
        try:
            manifests.append(json.loads(path.read_text()))
        except ValueError:
            failures.append("Invalid attachment manifest")
    manifest_text = json.dumps(manifests)
    for case in cases:
        for name in case.get("screenshots", []):
            if name not in manifest_text:
                failures.append(f"Screenshot attachment absent from export manifest: {name}")
    # Verify each named screenshot is associated with actual exported PNG bytes,
    # rather than accepting a manifest name alone.
    def attachment_rows(value):
        if isinstance(value, dict):
            if "exportedFileName" in value:
                yield value
            for child in value.values():
                yield from attachment_rows(child)
        elif isinstance(value, list):
            for child in value:
                yield from attachment_rows(child)
    rows = list(attachment_rows(manifests))
    screenshot_files = {}
    for case in cases:
        for name in case.get("screenshots", []):
            # Xcode appends _<index>_<UUID> to the human-readable name. An
            # unrelated prefix match or a duplicate row is not one screenshot.
            def named_attachment(row):
                suggested = row.get("suggestedHumanReadableName", "")
                return (row.get("attachmentName") == name or isinstance(suggested, str) and
                        (suggested in (name, name + ".png") or suggested.startswith(name + "_")))
            matches = [row for row in rows if named_attachment(row)]
            if len(matches) != 1:
                failures.append(f"Expected exactly one exported attachment for screenshot: {name}")
            def valid_attachment(row):
                if not isinstance(row["exportedFileName"], str):
                    return False
                candidate = (directory / row["exportedFileName"]).resolve()
                if not candidate.is_relative_to(directory.resolve()):
                    return False
                return candidate.is_file() and valid_png(candidate.read_bytes())
            if not matches or not any(valid_attachment(row) for row in matches):
                failures.append(f"No exported PNG bytes for screenshot: {name}")
            if len(matches) == 1 and isinstance(matches[0]["exportedFileName"], str):
                candidate = (directory / matches[0]["exportedFileName"]).resolve()
                if candidate in screenshot_files:
                    failures.append(f"Exported PNG reused for screenshots: {screenshot_files[candidate]}, {name}")
                screenshot_files[candidate] = name
    return {"passed": not failures, "failures": failures, "cases": cases,
            "artifactSHA256": artifacts, "reportCount": len(cases)}


def configure_evidence_run(xctestrun: Path, kind: str) -> dict:
    """Inject a fresh ID into the test runner, preserving Xcode's relative roots.

    The runner writes inside its own Documents container, not a host path. The
    actual built runner bundle supplies the ID used to harvest that container.
    """
    xctestrun = xctestrun.resolve()
    configuration = plistlib.loads(xctestrun.read_bytes())
    if "TestConfigurations" in configuration:
        targets = [target for group in configuration["TestConfigurations"]
                   for target in group.get("TestTargets", [])]
    else:
        targets = [value for value in configuration.values() if isinstance(value, dict)]
    targets = [target for target in targets if "TestHostPath" in target and "TestBundlePath" in target]
    if len(targets) != 1:
        raise ValueError("Expected exactly one P0 sign-in test runner target")
    target = targets[0]
    raw_host = target["TestHostPath"]
    if re.search(r"__[A-Z][A-Z0-9_]*__", raw_host.replace("__TESTROOT__", "")):
        raise ValueError("Unsupported placeholder in P0 sign-in test runner host path")
    host = raw_host.replace("__TESTROOT__", str(xctestrun.parent))
    host_path = Path(host)
    if not host_path.is_absolute():
        host_path = xctestrun.parent / host_path
    host_path = host_path.resolve()
    if not host_path.is_relative_to(xctestrun.parent) or host_path.suffix != ".app":
        raise ValueError("P0 sign-in test runner host is outside its build products")
    info = plistlib.loads((host_path / "Info.plist").read_bytes())
    runner_id = info.get("CFBundleIdentifier")
    if not isinstance(runner_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]+", runner_id):
        raise ValueError("Built P0 sign-in test runner has no usable bundle identifier")
    run_id = uuid.uuid4().hex
    environment = target.setdefault("EnvironmentVariables", {})
    if not isinstance(environment, dict):
        raise ValueError("P0 sign-in test runner environment is not a dictionary")
    environment[EVIDENCE_RUN_ID_KEY] = run_id
    # A sibling retains the meaning of __TESTROOT__ in every unchanged setting.
    configured = xctestrun.with_name(xctestrun.stem + f"-p0-{kind}-{run_id}.xctestrun")
    configured.write_bytes(plistlib.dumps(configuration))
    return {"xctestrun": str(configured), "runnerBundleIdentifier": runner_id, "runID": run_id}


def harvest_durable_files(container: Path, run_id: str, destination: Path) -> dict:
    """Copy only bounded, flat, regular files from this exact test-run directory."""
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Invalid P0 sign-in evidence run ID")
    if not container.is_absolute() or container.is_symlink() or not container.is_dir():
        raise ValueError("UI test runner data container is unavailable or a symlink")
    source = container
    for part in ("Documents", "p0-signin-evidence", run_id):
        source = source / part
        if source.is_symlink() or not source.is_dir():
            raise ValueError("Current-run UI test evidence directory is unavailable or a symlink")
    files, errors, total = {}, [], 0
    destination.mkdir(parents=True, exist_ok=False)
    with os.scandir(source) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_DURABLE_FILES:
                errors.append("UI test evidence exceeds the file-count bound")
                break
            if (not re.fullmatch(r"p0-[A-Za-z0-9_.-]+\.(?:png|json|txt)", entry.name)
                    or not entry.is_file(follow_symlinks=False)):
                errors.append("Rejected non-regular or unexpected evidence entry: " + entry.name)
                continue
            # O_NOFOLLOW also prevents a file replaced by a symlink between
            # directory enumeration and opening from escaping the runner folder.
            try:
                descriptor = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as stream:
                    details = os.fstat(stream.fileno())
                    if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_DURABLE_FILE_BYTES:
                        raise ValueError("Non-regular or oversized evidence file")
                    data = stream.read(MAX_DURABLE_FILE_BYTES + 1)
                if len(data) > MAX_DURABLE_FILE_BYTES or total + len(data) > MAX_DURABLE_TOTAL_BYTES:
                    raise ValueError("UI test evidence exceeds the byte bound")
                total += len(data)
                (destination / entry.name).write_bytes(data)
                files[entry.name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                if entry.name.endswith(".png") and not valid_png(data):
                    errors.append("Invalid durable diagnostic PNG: " + entry.name)
            except (OSError, ValueError) as error:
                errors.append(entry.name + ": " + str(error))
    if not files:
        errors.append("No durable files were recovered for the current test run")
    return {"files": files, "errors": errors, "totalBytes": total}


def capture_diagnostics(evidence: Path, kind: str, device: str, run: dict | None, command) -> dict:
    """Preserve pixels and runner files even when xcresult never finalizes.

    This runs before result export and the caller's simulator cleanup. Nothing
    collected here can substitute for acceptance screenshots or XCTest success.
    """
    evidence.mkdir(parents=True, exist_ok=True)
    report = {"schemaVersion": 1, "deviceClass": kind, "diagnosticOnly": True,
              "runID": run.get("runID") if run else None, "errors": []}
    screenshot = evidence / (kind + "-terminal-diagnostic.png")
    try:
        command("xcrun", "simctl", "io", device, "screenshot", str(screenshot), timeout=30)
        data = screenshot.read_bytes()
        if not valid_png(data):
            raise ValueError("Terminal simulator screenshot is not a valid PNG")
        report["terminalScreenshot"] = {"file": screenshot.name, "sha256": hashlib.sha256(data).hexdigest()}
    except Exception as error:
        report["errors"].append("Terminal simulator screenshot: " + str(error))
    try:
        if not run:
            raise ValueError("Verified test runner identity/run ID unavailable; no container guessed")
        container = command("xcrun", "simctl", "get_app_container", device,
                            run["runnerBundleIdentifier"], "data", timeout=15)
        report["runnerBundleIdentifier"] = run["runnerBundleIdentifier"]
        report["durableEvidence"] = harvest_durable_files(
            Path(container.strip()), run["runID"], evidence / (kind + "-durable-diagnostics"))
        report["errors"].extend(report["durableEvidence"]["errors"])
    except Exception as error:
        report["errors"].append("Durable UI test evidence: " + str(error))
    (evidence / (kind + "-capture-diagnostics.json")).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def execute(prepared: dict, kind: str, device: str, output: Path, command,
            diagnostic_case: str | None = None) -> dict:
    if diagnostic_case not in (None, "credentials-default"):
        raise ValueError("Unsupported diagnostic case")
    evidence = output / "p0-signin"
    evidence.mkdir(parents=True, exist_ok=True)
    result = evidence / (kind + ".xcresult")
    exports = evidence / (kind + "-attachments")
    failure = None
    run = None
    try:
        run = configure_evidence_run(Path(prepared["xctestrun"]), kind)
        # Run 37472893006 completed assertions/reporting in up to 166 seconds.
        # 240/case includes cleanup; 1080 covers four cases plus 120s startup.
        # The prior 120s watchdog interrupted UI work; termination/relaunch then failed.
        command("xcodebuild", "test-without-building", "-xctestrun", run["xctestrun"],
                "-destination", "id=" + device, "-resultBundlePath", str(result),
                "-parallel-testing-enabled", "NO", "-maximum-concurrent-test-simulator-destinations", "1",
                "-test-timeouts-enabled", "YES", "-default-test-execution-time-allowance", "240",
                "-maximum-test-execution-time-allowance", "240",
                *(["-only-testing:P0SignInUITests/P0SignInUITests/testCredentialsDefault"]
                  if diagnostic_case else []), timeout=660 if diagnostic_case else 1080)
    except Exception as error:
        failure = str(error)
    finally:
        capture_diagnostics(evidence, kind, device, run, command)
    if not result.exists():
        raise RuntimeError("P0 sign-in XCTest produced no result bundle: " + (failure or kind))
    command("xcrun", "xcresulttool", "export", "attachments", "--path", str(result),
            "--output-path", str(exports), timeout=120)
    summary = json.loads(command("xcrun", "xcresulttool", "get", "test-results", "summary", "--path", str(result)))
    (evidence / (kind + "-xctest-summary.json")).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    report = verify_export(exports, summary)
    report.update({"schemaVersion": 1, "mode": "p0-signin", "deviceClass": kind,
                   "sourceSHA256": prepared["sourceSHA256"], "physicalDeviceExecution": False,
                   "voiceOverSpokenOutputValidated": False,
                   "interaction": "XCTest taps on exact extracted production prompt/sign-in body; fixture state/cancel only",
                   "scope": "unsigned-in/no-recovery credentials, 320-point viewport, default and largest Dynamic Type"})
    if failure:
        report["passed"] = False
        report["failures"].append(failure)
    if diagnostic_case:
        # Keep the full-matrix verifier's rejection: a one-case observation can
        # never become preflight/release acceptance, even when XCTest succeeds.
        report.update({"mode": "p0-signin-input-diagnostic", "diagnosticOnly": True,
                       "releaseEligible": False, "passed": False,
                       "diagnosticCase": diagnostic_case,
                       "xcodebuildInvocationCount": 1, "nativeSummary": summary,
                       "frameworkRestartPolicy": "XCTest defaults; retained summary and logs expose framework restarts",
                       "diagnosticExecutionSucceeded": failure is None and
                           summary.get("result") == "Passed" and summary.get("passedTests") == 1 and
                           summary.get("failedTests") == 0 and summary.get("skippedTests") == 0 and
                           summary.get("totalTestCount") == 1 and
                           all(type(summary.get(key)) is int for key in ("passedTests", "failedTests", "skippedTests", "totalTestCount")) and
                           report["failures"] == ["Expected four passed XCTest methods with zero skips",
                                                  "Missing or duplicated P0 sign-in case reports"] and
                           len(report.get("cases", [])) == 1 and
                           report["cases"][0].get("case") == diagnostic_case and
                           report["cases"][0].get("passed") is True})
    (evidence / (kind + "-verification.json")).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report
