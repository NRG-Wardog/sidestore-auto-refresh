"""Runner capture failure tests; these fixtures are not simulator/UI evidence."""
import importlib.util
import json
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("p0_durable_renderer", ROOT / "scripts/run_p0_signin_rendering.py")
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def image():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (renderer.PNG_MAGIC + chunk(b"IHDR", struct.pack(">IIBBBBB", 100, 100, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x00" * 300) * 100)) + chunk(b"IEND", b""))


class DurableSignInEvidenceTests(unittest.TestCase):
    def make_run(self, root, modern=False):
        products = root / "Products"
        host = products / "Debug-iphonesimulator/P0SignInUITests-Runner.app"
        host.mkdir(parents=True)
        (host / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "fixture.actual.runner-id"}))
        target = {"TestHostPath": "__TESTROOT__/Debug-iphonesimulator/P0SignInUITests-Runner.app",
                  "TestBundlePath": "__TESTHOST__/PlugIns/P0SignInUITests.xctest", "IsUITestBundle": True,
                  "EnvironmentVariables": {"PRESERVE_ME": "yes"},
                  "UITargetAppEnvironmentVariables": {"AUT_ONLY": "yes"}}
        if modern:
            configuration = {"__xctestrun_metadata__": {"FormatVersion": 2},
                             "TestConfigurations": [{"TestTargets": [target]}]}
        else:
            configuration = {"P0SignInUITests": target, "__xctestrun_metadata__": {"FormatVersion": 1}}
        source = products / "fixture.xctestrun"
        source.write_bytes(plistlib.dumps(configuration))
        return source

    def own_files(self, container, run_id):
        source = container / "Documents/p0-signin-evidence" / run_id
        source.mkdir(parents=True)
        (source / "p0-credentials-default-prompt-top.png").write_bytes(image())
        (source / "p0-credentials-default-progress.json").write_text('{"passed": false}')
        (source / "p0-credentials-default-hierarchy.txt").write_text("Synthetic fixture hierarchy")
        return source

    def test_run_id_is_fresh_in_runner_environment_and_testroot_is_unchanged(self):
        for modern in (False, True):
            with self.subTest(modern=modern), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); original = self.make_run(root, modern)
                before = original.read_bytes()
                first = renderer.configure_evidence_run(original, "phone")
                second = renderer.configure_evidence_run(original, "tablet")
                self.assertNotEqual(first["runID"], second["runID"])
                self.assertEqual(original.read_bytes(), before)
                self.assertEqual(Path(first["xctestrun"]).parent, original.parent.resolve())
                # macOS /var aliases /private/var; compare directory identity,
                # and exercise an equivalent alias on every platform.
                alias = root / "ProductsAlias"
                alias.symlink_to(original.parent, target_is_directory=True)
                aliased = renderer.configure_evidence_run(alias / original.name, "phone")
                self.assertEqual(Path(aliased["xctestrun"]).parent, original.parent.resolve())
                self.assertEqual(first["runnerBundleIdentifier"], "fixture.actual.runner-id")
                configured = plistlib.loads(Path(first["xctestrun"]).read_bytes())
                target = configured["TestConfigurations"][0]["TestTargets"][0] if modern else configured["P0SignInUITests"]
                self.assertEqual(target["EnvironmentVariables"], {
                    "PRESERVE_ME": "yes", renderer.EVIDENCE_RUN_ID_KEY: first["runID"]})
                self.assertEqual(target["UITargetAppEnvironmentVariables"], {"AUT_ONLY": "yes"})
                self.assertTrue(target["TestHostPath"].startswith("__TESTROOT__/"))

    def test_missing_or_ambiguous_runner_identity_fails_closed(self):
        for mutation in ("missing-info", "missing-bundle-id", "two-targets", "outside-products", "placeholder"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); source = self.make_run(root)
                configuration = plistlib.loads(source.read_bytes())
                target = configuration["P0SignInUITests"]
                info = source.parent / "Debug-iphonesimulator/P0SignInUITests-Runner.app/Info.plist"
                if mutation == "missing-info": info.unlink()
                elif mutation == "missing-bundle-id": info.write_bytes(plistlib.dumps({}))
                elif mutation == "two-targets": configuration["Other"] = target.copy()
                elif mutation == "outside-products": target["TestHostPath"] = str(root / "other.app")
                elif mutation == "placeholder": target["TestHostPath"] = "__UNKNOWN__/runner.app"
                source.write_bytes(plistlib.dumps(configuration))
                with self.assertRaises((OSError, ValueError)):
                    renderer.configure_evidence_run(source, "phone")

    def test_harvest_only_current_run_flat_regular_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); container = root / "Container"; run_id = "a" * 32
            source = self.own_files(container, run_id)
            stale = self.own_files(container, "b" * 32)
            (stale / "p0-stale.txt").write_text("must not copy")
            (source / "p0-linked.txt").symlink_to(stale / "p0-stale.txt")
            (source / "nested").mkdir()
            (source / "unrelated.txt").write_text("must not copy")
            destination = root / "Recovered"
            report = renderer.harvest_durable_files(container, run_id, destination)
            self.assertEqual(set(report["files"]), {
                "p0-credentials-default-prompt-top.png", "p0-credentials-default-progress.json",
                "p0-credentials-default-hierarchy.txt"})
            self.assertEqual(len(report["errors"]), 3)
            self.assertFalse((destination / "p0-stale.txt").exists())
            self.assertEqual((destination / "p0-credentials-default-prompt-top.png").read_bytes(), image())

    def test_symlinked_directories_and_wrong_run_id_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); container = root / "Container"; run_id = "a" * 32
            source = self.own_files(container, run_id)
            source.rename(source.with_name("saved"))
            source.symlink_to(source.with_name("saved"), target_is_directory=True)
            for requested in (run_id, "b" * 32, "../saved"):
                with self.subTest(requested=requested), self.assertRaises(ValueError):
                    renderer.harvest_durable_files(container, requested, root / "Recovered")

    def test_harvest_enforces_file_and_byte_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); container = root / "Container"; run_id = "a" * 32
            self.own_files(container, run_id)
            with patch.object(renderer, "MAX_DURABLE_FILES", 1):
                report = renderer.harvest_durable_files(container, run_id, root / "CountBound")
            self.assertEqual(len(report["files"]), 1)
            self.assertTrue(any("file-count" in error for error in report["errors"]))
            with patch.object(renderer, "MAX_DURABLE_FILE_BYTES", 1):
                report = renderer.harvest_durable_files(container, run_id, root / "SizeBound")
            self.assertEqual(report["files"], {})
            self.assertTrue(report["errors"])
            with patch.object(renderer, "MAX_DURABLE_TOTAL_BYTES", 1):
                report = renderer.harvest_durable_files(container, run_id, root / "TotalBound")
            self.assertEqual(report["files"], {})
            self.assertTrue(report["errors"])

    def test_killed_suite_preserves_pixels_before_result_export_or_cleanup(self):
        for partial_result in (False, True):
            with self.subTest(partial_result=partial_result), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); source = self.make_run(root); calls = []
                container = root / "Container"; evidence = root / "p0-signin"
                def command(*args, **kwargs):
                    calls.append((args, kwargs))
                    if args[0] == "xcodebuild":
                        configuration = plistlib.loads(Path(args[args.index("-xctestrun") + 1]).read_bytes())
                        run_id = configuration["P0SignInUITests"]["EnvironmentVariables"][renderer.EVIDENCE_RUN_ID_KEY]
                        self.own_files(container, run_id)
                        if partial_result: (evidence / "phone.xcresult").mkdir(parents=True)
                        self.assertEqual(kwargs["timeout"], 1080)
                        raise RuntimeError("bounded XCTest timeout")
                    if args[1:4] == ("simctl", "io", "device"):
                        self.assertEqual(kwargs["timeout"], 30)
                        Path(args[-1]).write_bytes(image()); return ""
                    if args[1:3] == ("simctl", "get_app_container"):
                        self.assertEqual(args[4:], ("fixture.actual.runner-id", "data"))
                        self.assertEqual(kwargs["timeout"], 15)
                        return str(container)
                    if args[1] == "xcresulttool": raise RuntimeError("xcresult is incomplete")
                    self.fail("Unexpected external command: " + str(args))
                expected = "xcresult is incomplete" if partial_result else "no result bundle"
                with self.assertRaisesRegex(RuntimeError, expected):
                    renderer.execute({"xctestrun": str(source), "sourceSHA256": {}}, "phone", "device", root, command)
                self.assertEqual(calls[1][0][1:5], ("simctl", "io", "device", "screenshot"))
                self.assertEqual(calls[2][0][1:3], ("simctl", "get_app_container"))
                report = json.loads((evidence / "phone-capture-diagnostics.json").read_text())
                self.assertTrue(report["diagnosticOnly"])
                self.assertEqual(report["errors"], [])
                self.assertEqual(len(report["durableEvidence"]["files"]), 3)
                self.assertTrue((evidence / "phone-terminal-diagnostic.png").is_file())
                # These recovered pixels cannot fill an absent acceptance export.
                verification = renderer.verify_export(evidence / "phone-attachments", {
                    "result": "Passed", "passedTests": 4, "failedTests": 0, "skippedTests": 0})
                self.assertFalse(verification["passed"])

    def test_screenshot_failure_does_not_prevent_harvest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); container = root / "Container"; run_id = "a" * 32
            self.own_files(container, run_id); calls = []
            def command(*args, **kwargs):
                calls.append(args)
                if "screenshot" in args: raise RuntimeError("simctl screenshot timed out")
                return str(container)
            result = renderer.capture_diagnostics(root / "Evidence", "phone", "device", {
                "runnerBundleIdentifier": "fixture.runner", "runID": run_id}, command)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(result["durableEvidence"]["files"]), 3)
            self.assertTrue(any("screenshot timed out" in error for error in result["errors"]))

    def test_unknown_identity_never_guesses_a_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); calls = []
            def command(*args, **kwargs):
                calls.append(args); Path(args[-1]).write_bytes(image()); return ""
            result = renderer.capture_diagnostics(root, "phone", "device", None, command)
            self.assertEqual(len(calls), 1)
            self.assertTrue(any("no container guessed" in error for error in result["errors"]))

    def test_container_lookup_failure_is_durable_and_does_not_erase_terminal_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def command(*args, **kwargs):
                if "screenshot" in args:
                    Path(args[-1]).write_bytes(image()); return ""
                raise RuntimeError("runner container not installed")
            result = renderer.capture_diagnostics(root, "phone", "device", {
                "runnerBundleIdentifier": "fixture.runner", "runID": "a" * 32}, command)
            self.assertIn("terminalScreenshot", result)
            self.assertTrue(any("not installed" in error for error in result["errors"]))
            self.assertEqual(json.loads((root / "phone-capture-diagnostics.json").read_text()), result)

    def test_diagnostics_and_even_clean_export_cannot_rescue_a_timed_out_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.make_run(root); evidence = root / "p0-signin"
            evidence.mkdir(); (evidence / "phone.xcresult").mkdir()
            def command(*args, **kwargs):
                if args[0] == "xcodebuild": raise RuntimeError("bounded XCTest timeout")
                if args[1:3] == ("xcresulttool", "get"):
                    return json.dumps({"result": "Passed", "passedTests": 4, "failedTests": 0, "skippedTests": 0})
                return ""
            with patch.object(renderer, "capture_diagnostics", return_value={"diagnosticOnly": True}), \
                    patch.object(renderer, "verify_export", return_value={"passed": True, "failures": [], "reportCount": 4}):
                result = renderer.execute({"xctestrun": str(source), "sourceSHA256": {}}, "phone", "device", root, command)
            self.assertFalse(result["passed"])
            self.assertIn("bounded XCTest timeout", result["failures"])


if __name__ == "__main__":
    unittest.main()
