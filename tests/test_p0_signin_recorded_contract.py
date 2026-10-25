"""Replay real failed XCTest exports, not synthetic success-shaped UI evidence.

Run 37496696970 remains FAILED: all eight methods passed but the producer
serialized sixteen empty credential labels. No test rewrites those labels.
Mutations operate only on temporary copies and must add a specific failure.
"""
from collections import Counter
import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/p0_signin_recorded_contract"
spec = importlib.util.spec_from_file_location("recorded_p0_renderer", ROOT / "scripts/run_p0_signin_rendering.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
KINDS = ("phone", "tablet")


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def recorded_source_failures(directory):
    """Fixture integrity only; verify_export does not authenticate source identity."""
    identity = read_json(directory / "source-identity.json")["sourceSHA256"]
    failures = []
    for kind in KINDS:
        if read_json(directory / f"{kind}-verification.json")["sourceSHA256"] != identity:
            failures.append(f"{kind}: source identity metadata drift")
    for category in ("sources", "project"):
        for path in sorted((directory / category).rglob("*")):
            if path.is_file():
                key = path.name if category == "sources" else str(path.relative_to(directory))
                if renderer.digest(path) != identity.get(key):
                    failures.append(f"{key}: source identity bytes drift")
    return failures


class P0SignInRecordedContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summaries = {kind: read_json(FIXTURE / f"{kind}-xctest-summary.json") for kind in KINDS}
        cls.baselines = {kind: renderer.verify_export(FIXTURE / f"{kind}-attachments", cls.summaries[kind])
                         for kind in KINDS}

    def temporary_export(self, kind):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / f"{kind}-attachments"
        shutil.copytree(FIXTURE / f"{kind}-attachments", directory)
        return directory

    def report_path(self, directory, case="credentials-largest"):
        for path in directory.glob("*.json"):
            data = read_json(path)
            if isinstance(data, dict) and data.get("case") == case:
                return path
        self.fail(f"Missing recorded case {case}")

    def screenshot_row(self, manifest, name="p0-credentials-largest-copy-details"):
        rows = [row for group in manifest for row in group["attachments"]
                if row["suggestedHumanReadableName"].startswith(name + "_")]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def assert_additional_failure(self, kind, result, expected):
        self.assertFalse(result["passed"])
        additional = Counter(result["failures"]) - Counter(self.baselines[kind]["failures"])
        self.assertTrue(any(expected in failure for failure in additional), additional)

    def test_actual_exports_reproduce_exact_sixteen_empty_label_failures(self):
        count = 0
        for kind in KINDS:
            with self.subTest(kind=kind):
                result = self.baselines[kind]
                recorded = read_json(FIXTURE / f"{kind}-verification.json")
                self.assertFalse(result["passed"])
                self.assertFalse(recorded["passed"])
                self.assertEqual(result["failures"], recorded["failures"])
                self.assertEqual(result["reportCount"], 4)
                self.assertEqual(len(result["artifactSHA256"]), 16)  # 4 reports + 12 actual PNGs
                self.assertEqual(Counter(result["failures"]), Counter({
                    f"Invalid runtime measurement: {case}": 2 for case in renderer.REQUIRED_CASES}))
                for case in result["cases"]:
                    self.assertTrue(case["passed"])  # XCTest success is insufficient for export success.
                    self.assertEqual(case["failures"], [])
                    self.assertEqual({item["control"] for item in case["measurements"] if item["label"] == ""},
                                     {"username", "password"})
                    count += sum(item["label"] == "" for item in case["measurements"])
        self.assertEqual(count, 16)

    def test_fixture_bytes_and_sanitization_match_provenance(self):
        provenance = read_json(FIXTURE / "provenance.json")
        self.assertEqual(provenance["runID"], "37496696970")
        self.assertFalse(provenance["recordedOutcome"]["passed"])
        expected_files = set(provenance["files"]) | {"README.md", "provenance.json"}
        self.assertEqual({str(p.relative_to(FIXTURE)) for p in FIXTURE.rglob("*") if p.is_file()}, expected_files)
        for relative, record in provenance["files"].items():
            with self.subTest(file=relative):
                path = FIXTURE / relative
                self.assertEqual(path.stat().st_size, record["bytes"])
                self.assertEqual(renderer.digest(path), record["fixtureSHA256"])
                if record["transformation"] == "none":
                    self.assertEqual(record["fixtureSHA256"], record["originalSHA256"])
                elif path.suffix == ".txt":
                    lines = path.read_text().splitlines()
                    self.assertEqual(len(lines), 2)
                    self.assertIn("placeholderValue: 'Apple ID'", lines[0])
                    self.assertIn("placeholderValue: 'Password'", lines[1])
                    self.assertNotIn(", value:", path.read_text())
                    self.assertNotIn("0x", path.read_text())
                else:
                    # These are simulator IDs only; retain schema with a synthetic sentinel.
                    value = read_json(path)
                    devices = ([row for group in value for row in group["attachments"]]
                               if isinstance(value, list) else
                               [group["device"] for group in value["devicesAndConfigurations"]])
                    self.assertEqual({device["deviceId"] for device in devices},
                                     {"00000000-0000-0000-0000-000000000000"})
        screenshots = list(FIXTURE.glob("*-attachments/*.png"))
        self.assertEqual(len(screenshots), 24)
        self.assertEqual(sum(path.stat().st_size for path in screenshots), 5180333)

    def test_source_identity_matches_recorded_producer_not_current_checkout(self):
        self.assertEqual(recorded_source_failures(FIXTURE), [])
        provenance = read_json(FIXTURE / "provenance.json")
        info = plistlib.loads((FIXTURE / "project/Info.plist").read_bytes())
        self.assertEqual(info["LCBuilderCommit"], provenance["builderCommit"])
        producer = (FIXTURE / "sources/p0_signin_ui_tests.swift").read_text()
        self.assertIn('"label": element.label', producer)
        self.assertIn('username.typeText("p0-user@example.invalid")', producer)
        self.assertIn('password.typeText("p0-synthetic-password\\n")', producer)
        identity = read_json(FIXTURE / "source-identity.json")["sourceSHA256"]
        self.assertEqual(identity["V3SignInView.full-production"],
                         renderer.digest(FIXTURE / "sources/V3SignInView.production.txt"))

    def test_recorded_source_identity_metadata_and_file_drift_are_detected(self):
        for mutation in ("metadata", "source-bytes", "project-bytes", "missing-digest"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / "fixture"
                shutil.copytree(FIXTURE, directory)
                if mutation in ("metadata", "missing-digest"):
                    path = directory / "source-identity.json"
                    identity = read_json(path)
                    if mutation == "metadata": identity["sourceSHA256"]["generated-shell"] = "0" * 64
                    else: identity["sourceSHA256"].pop("p0_signin_ui_tests.swift")
                    write_json(path, identity)
                else:
                    path = directory / ("sources/p0_signin_ui_tests.swift" if mutation == "source-bytes"
                                        else "project/Info.plist")
                    path.write_bytes(path.read_bytes() + b"\n")
                self.assertTrue(recorded_source_failures(directory))

    def test_actual_summary_wrong_or_missing_metadata_adds_failure(self):
        for key, value, expected in (
            ("result", "Failed", "XCTest did not report Passed"),
            ("passedTests", 3, "Expected four passed XCTest methods"),
            ("skippedTests", 1, "Expected four passed XCTest methods"),
            ("failedTests", None, "XCTest did not report Passed"),
        ):
            with self.subTest(key=key):
                summary = copy.deepcopy(self.summaries["phone"])
                if value is None: summary.pop(key)
                else: summary[key] = value
                result = renderer.verify_export(FIXTURE / "phone-attachments", summary)
                self.assert_additional_failure("phone", result, expected)

    def test_actual_case_wrong_or_missing_metadata_adds_failure(self):
        for key, value, expected in (
            ("teardownCaptured", None, "Missing clean XCTest teardown proof"),
            ("xctestFailureCount", 1, "Missing clean XCTest teardown proof"),
            ("clipboardExactMatch", None, "Missing runtime proof clipboardExactMatch"),
            ("largestDynamicType", False, "Wrong fixture state or Dynamic Type"),
            ("submitting", True, "Wrong fixture state or Dynamic Type"),
            ("screenshots", [], "Screenshot matrix is incomplete"),
        ):
            with self.subTest(key=key):
                directory = self.temporary_export("phone")
                path = self.report_path(directory)
                case = read_json(path)
                if value is None: case.pop(key)
                else: case[key] = value
                write_json(path, case)
                self.assert_additional_failure("phone", renderer.verify_export(directory, self.summaries["phone"]), expected)

    def test_actual_case_duplicate_or_missing_report_is_rejected(self):
        for mutation in ("duplicate", "missing"):
            with self.subTest(mutation=mutation):
                directory = self.temporary_export("tablet")
                path = self.report_path(directory)
                if mutation == "duplicate": shutil.copyfile(path, directory / "duplicate.json")
                else: path.unlink()
                self.assert_additional_failure("tablet", renderer.verify_export(directory, self.summaries["tablet"]),
                                               "Missing or duplicated P0 sign-in case reports")

    def test_actual_png_missing_or_invalid_bytes_adds_failure(self):
        for mutation in ("missing", "truncated"):
            with self.subTest(mutation=mutation):
                directory = self.temporary_export("tablet")
                row = self.screenshot_row(read_json(directory / "manifest.json"))
                path = directory / row["exportedFileName"]
                if mutation == "missing": path.unlink()
                else: path.write_bytes(renderer.PNG_MAGIC)
                self.assert_additional_failure("tablet", renderer.verify_export(directory, self.summaries["tablet"]),
                                               "No exported PNG bytes for screenshot: p0-credentials-largest-copy-details")

    def test_actual_manifest_wrong_missing_duplicate_or_reused_mapping_is_rejected(self):
        name = "p0-credentials-largest-copy-details"
        for mutation in ("wrong-name", "missing-name", "duplicate-row", "reused-file", "aliased-file", "wrong-file"):
            with self.subTest(mutation=mutation):
                directory = self.temporary_export("phone")
                path = directory / "manifest.json"
                manifest = read_json(path)
                row = self.screenshot_row(manifest, name)
                expected = "Expected exactly one exported attachment for screenshot: " + name
                if mutation == "wrong-name": row["suggestedHumanReadableName"] = name + "-unrelated.png"
                elif mutation == "missing-name": row.pop("suggestedHumanReadableName")
                elif mutation == "duplicate-row": manifest[0]["attachments"].append(copy.deepcopy(row))
                elif mutation in ("reused-file", "aliased-file"):
                    other = self.screenshot_row(manifest, "p0-credentials-largest-prompt-top")
                    row["exportedFileName"] = ("./" if mutation == "aliased-file" else "") + other["exportedFileName"]
                    expected = "Exported PNG reused for screenshots:"
                else:
                    row["exportedFileName"] = None
                    expected = "No exported PNG bytes for screenshot: " + name
                write_json(path, manifest)
                self.assert_additional_failure("phone", renderer.verify_export(directory, self.summaries["phone"]), expected)

    def test_distinct_screenshot_files_may_have_identical_pixels(self):
        directory = self.temporary_export("phone")
        manifest = read_json(directory / "manifest.json")
        copy_row = self.screenshot_row(manifest)
        cancel_row = self.screenshot_row(manifest, "p0-credentials-largest-cancel-reachable")
        self.assertNotEqual(copy_row["exportedFileName"], cancel_row["exportedFileName"])
        shutil.copyfile(directory / copy_row["exportedFileName"], directory / cancel_row["exportedFileName"])
        result = renderer.verify_export(directory, self.summaries["phone"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"], self.baselines["phone"]["failures"])


if __name__ == "__main__":
    unittest.main()
