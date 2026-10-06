"""Mocked orchestration/source checks only; these never establish simulator UI success."""
import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load("preflight", "scripts/run_p0_signin_preflight.py")
fixtures = load("preflight_export_fixtures", "tests/test_p0_signin_rendering.py")
host_fixtures = load("preflight_host_fixtures", "tests/test_v3_unified_shell.py")
COMMIT = "0123456789abcdef0123456789abcdef01234567"
RUNTIME = "com.apple.CoreSimulator.SimRuntime.iOS-26-4"


class Commands:
    """Fake macOS commands while exercising real prepare/execute/export/cleanup code."""
    def __init__(self, output, failures=(), initially_booted=()):
        self.output = output
        self.failures = set(failures)
        self.calls = []
        self.states = {kind: "Booted" if kind in initially_booted else "Shutdown" for kind in ("phone", "tablet")}
        self.runs = {}
        self.commit_reads = 0

    def fail(self, stage):
        if stage in self.failures:
            raise RuntimeError("intentional " + stage + " failure")

    def __call__(self, *args, timeout=300):
        self.calls.append((args, timeout))
        if args[0] == "git":
            if "status" in args:
                return " M scripts/templates/v3_unified_shell.swift" if "dirty-source" in self.failures else ""
            self.commit_reads += 1
            if (("fixture-commit-drift" in self.failures and self.commit_reads == 2) or
                    ("final-commit-drift" in self.failures and self.commit_reads == 3)):
                return "a" * 40
            return "not-a-commit" if "identity" in self.failures else COMMIT
        if args[:2] == ("xcodebuild", "build-for-testing"):
            self.fail("build")
            products = self.output / "p0-build/DerivedData/Build/Products"
            products.mkdir(parents=True)
            app = products / "Runner.app"
            app.mkdir()
            (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "org.sidestore.p0.fixture.runner"}))
            (products / "fixture.xctestrun").write_bytes(plistlib.dumps({"P0SignInUITests": {
                "TestHostPath": "__TESTROOT__/Runner.app", "TestBundlePath": "__TESTROOT__/Runner.app/Tests.xctest"}}))
            return ""
        if args[:4] == ("xcrun", "simctl", "list", "runtimes"):
            return json.dumps({"runtimes": [{"identifier": RUNTIME, "isAvailable": True, "version": "26.4"}]})
        if args[:4] == ("xcrun", "simctl", "list", "devices"):
            self.fail("discovery" if "available" in args else "state")
            return json.dumps({"devices": {RUNTIME: [
                {"name": "iPhone 17" if kind == "phone" else "iPad Pro 13-inch", "udid": kind,
                 "state": state, "isAvailable": not (kind == "tablet" and "no-tablet" in self.failures)}
                for kind, state in self.states.items()]}})
        if args[:3] == ("xcrun", "simctl", "boot"):
            kind = args[3]
            self.states[kind] = "Booted"  # Simulate partial success even on a command timeout.
            self.fail("boot-" + kind)
            return ""
        if args[:3] == ("xcrun", "simctl", "bootstatus"):
            self.fail("bootstatus-" + args[3])
            return ""
        if args[:3] == ("xcrun", "simctl", "shutdown"):
            kind = args[3]
            self.fail("shutdown-" + kind)
            self.states[kind] = "Shutdown"
            return ""
        if args[:2] == ("xcodebuild", "test-without-building"):
            kind = args[args.index("-destination") + 1].removeprefix("id=")
            configuration = plistlib.loads(Path(args[args.index("-xctestrun") + 1]).read_bytes())
            run_id = configuration["P0SignInUITests"]["EnvironmentVariables"][preflight.p0.EVIDENCE_RUN_ID_KEY]
            self.runs[kind] = run_id
            durable = self.output / ("container-" + kind) / "Documents/p0-signin-evidence" / run_id
            durable.mkdir(parents=True)
            (durable / "p0-before-interaction.png").write_bytes(fixtures.image())
            (durable / "p0-progress.json").write_text(json.dumps({"complete": False, "passed": False}))
            if "interrupt-" + kind in self.failures:
                raise KeyboardInterrupt()
            self.fail("test-" + kind)
            Path(args[args.index("-resultBundlePath") + 1]).mkdir()
            return ""
        if args[:3] == ("xcrun", "simctl", "io"):
            kind = args[3]
            self.fail("screenshot-" + kind)
            Path(args[-1]).write_bytes(fixtures.image())
            return ""
        if args[:3] == ("xcrun", "simctl", "get_app_container"):
            self.fail("container-" + args[3])
            return str(self.output / ("container-" + args[3]))
        if args[:4] == ("xcrun", "xcresulttool", "export", "attachments"):
            kind = Path(args[args.index("--path") + 1]).stem
            self.fail("export-" + kind)
            directory = Path(args[args.index("--output-path") + 1])
            directory.mkdir()
            fixtures.P0SignInRenderingEvidenceTests().make_export(directory)
            if "missing-png-" + kind in self.failures:
                (directory / "p0-credentials-default-copy-details.png").unlink()
            if "missing-case-" + kind in self.failures:
                (directory / "credentials-default.json").unlink()
            return ""
        if args[:4] == ("xcrun", "xcresulttool", "get", "test-results"):
            kind = Path(args[args.index("--path") + 1]).stem
            self.fail("summary-" + kind)
            return json.dumps({"result": "Passed", "passedTests": 4, "failedTests": 0,
                               "skippedTests": 1 if "skip-" + kind in self.failures else 0})
        raise AssertionError("Unexpected command: " + repr(args))


class P0SignInPreflightTests(unittest.TestCase):
    def run_fake(self, output, commands):
        with patch.object(preflight.platform, "system", return_value="Darwin"), \
                patch.object(preflight.rendering, "command", side_effect=commands):
            return preflight.run(output)

    def assert_failed_manifest(self, output):
        report = json.loads((output / preflight.MANIFEST).read_text())
        self.assertFalse(report["passed"])
        self.assertFalse(report["releaseEligible"])
        self.assertFalse(report["fullSuiteValidated"])
        self.assertEqual(report["status"], "failed")
        self.assertFalse((output / "rendering-verification.json").exists())
        return report

    def test_exact_production_composition_and_patch_host_use_one_helper(self):
        expected = "\n".join(path.read_text(encoding="utf-8") for path in (
            preflight.shell.BEHAVIOR_TEMPLATE, preflight.shell.SECRET_HANDOFF_TEMPLATE,
            preflight.shell.IPA_STAGING_TEMPLATE, preflight.shell.TEMPLATE))
        self.assertEqual(preflight.shell.generated_shell_source(), expected)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live, _ = host_fixtures.fixture(root)
            with patch.object(preflight.shell, "generated_shell_source", wraps=preflight.shell.generated_shell_source) as generate:
                preflight.shell.patch_host(live)
            generate.assert_called_once_with()
            source = live / "LiveContainerSwiftUI/Views/V3UnifiedShell.swift"
            self.assertEqual(source.read_bytes(), expected.encode("utf-8"))
            with patch.object(preflight.shell, "generated_shell_source", return_value=expected + "\n// drift\n"):
                with self.assertRaisesRegex(SystemExit, "existing v3 shell differs"):
                    preflight.shell.patch_host(live)
            self.assertEqual(source.read_bytes(), expected.encode("utf-8"))

    def test_prepared_shell_equivalence_includes_every_exact_extracted_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live, _ = host_fixtures.fixture(root)
            preflight.shell.patch_host(live)
            source = live / "LiveContainerSwiftUI/Views/V3UnifiedShell.swift"
            output = root / "proof"; output.mkdir()
            generated, identity = preflight.prepare_source(output, source)
            self.assertEqual(generated.read_bytes(), source.read_bytes())
            self.assertEqual(identity["preparedShellComparison"], "exact extracted-source equality")
            self.assertEqual(identity["preparedShellSHA256"], preflight.p0.digest(source))
            for old, new in (("signin.prompt.previous-error-body", "signin.prompt.changed-error-body"),
                             ("private var shouldShowAccountSection: Bool", "private var changedSection: Bool")):
                source.write_text(preflight.shell.generated_shell_source().replace(old, new))
                destination = root / new.split(":")[0].replace(" ", "-")
                destination.mkdir()
                with self.assertRaises(ValueError):
                    preflight.prepare_source(destination, source)

    def test_mocked_success_requires_complete_matrix_and_records_actual_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            commands = Commands(output)
            report = self.run_fake(output, commands)
            self.assertTrue(report["passed"])
            self.assertEqual((report["reportCount"], report["caseCount"], report["screenshotCount"]), (2, 8, 24))
            self.assertEqual(report["builderCommit"], COMMIT)
            self.assertEqual(report["inputSHA256"], preflight.input_hashes())
            self.assertFalse(report["releaseEligible"])
            self.assertFalse(report["fullSuiteValidated"])
            self.assertEqual(report["legacyReportCount"], 0)
            self.assertFalse(report["ipaBuilt"])
            self.assertEqual(report["sourceSHA256"]["generated-shell"], report["sourceGeneration"]["generatedSourceSHA256"])
            plist = plistlib.loads((output / "p0-signin/project/Info.plist").read_bytes())
            self.assertEqual(plist["LCBuilderCommit"], COMMIT)
            self.assertEqual(commands.states, {"phone": "Shutdown", "tablet": "Shutdown"})
            builds = [(args, timeout) for args, timeout in commands.calls if args[0] == "xcodebuild"]
            self.assertEqual([args[1] for args, _ in builds], ["build-for-testing", "test-without-building", "test-without-building"])
            self.assertEqual([timeout for _, timeout in builds], [300, 1080, 1080])
            for args, _ in builds[1:]:
                for flag in ("-default-test-execution-time-allowance", "-maximum-test-execution-time-allowance"):
                    self.assertEqual(args[args.index(flag) + 1], "240")
                self.assertEqual(args[args.index("-parallel-testing-enabled") + 1], "NO")
            self.assertEqual([timeout for args, timeout in commands.calls if "bootstatus" in args], [600, 600])
            self.assertEqual([timeout for args, timeout in commands.calls if "screenshot" in args], [30, 30])
            self.assertEqual([timeout for args, timeout in commands.calls if "get_app_container" in args], [15, 15])
            calls = [args for args, _ in commands.calls]
            self.assertLess(calls.index(("xcrun", "simctl", "shutdown", "phone")), calls.index(("xcrun", "simctl", "boot", "tablet")))
            self.assertFalse(any("create" in args or "erase" in args or "delete" in args for args in calls))

    def test_build_identity_discovery_and_missing_device_fail_closed_before_boot(self):
        for stage in ("identity", "dirty-source", "fixture-commit-drift", "build", "discovery", "no-tablet", "state"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "evidence"
                commands = Commands(output, [stage])
                self.run_fake(output, commands)
                self.assert_failed_manifest(output)
                self.assertFalse(any("boot" in args or "shutdown" in args for args, _ in commands.calls))
                if stage == "dirty-source":
                    report = json.loads((output / preflight.MANIFEST).read_text())
                    self.assertTrue(report["builderWorkingTreeDirty"])
                    self.assertEqual(report["inputSHA256"], preflight.input_hashes())

    def test_device_failures_preserve_capture_cleanup_and_attempt_other_device(self):
        for stage in ("boot-phone", "bootstatus-phone", "test-phone", "export-phone", "summary-phone",
                      "missing-png-phone", "missing-case-phone", "skip-phone", "shutdown-phone"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                # Exercise macOS /var -> /private/var semantics on every platform.
                root = Path(temporary)
                real = root / "real"; real.mkdir()
                alias = root / "alias"; alias.symlink_to(real, target_is_directory=True)
                output = alias / "evidence"
                commands = Commands(output, [stage])
                self.run_fake(output, commands)
                report = self.assert_failed_manifest(output)
                calls = [args for args, _ in commands.calls]
                self.assertIn(("xcrun", "simctl", "shutdown", "phone"), calls)
                self.assertIn(("xcrun", "simctl", "shutdown", "tablet"), calls)
                screen = ("xcrun", "simctl", "io", "phone", "screenshot", str(output.resolve() / "p0-signin/phone-terminal-diagnostic.png"))
                self.assertLess(calls.index(screen), calls.index(("xcrun", "simctl", "shutdown", "phone")))
                self.assertEqual(len(report["devices"]), 2)
                self.assertTrue((output / "p0-signin/phone-capture-diagnostics.json").exists())
                if stage not in ("boot-phone", "bootstatus-phone"):
                    self.assertTrue((output / "p0-signin/phone-durable-diagnostics/p0-before-interaction.png").is_file())
                self.assertTrue(next(item for item in report["reports"] if item["deviceClass"] == "tablet")["passed"])

    def test_already_booted_simulators_are_never_shutdown_even_after_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            commands = Commands(output, ["test-phone"], initially_booted=("phone", "tablet"))
            self.run_fake(output, commands)
            self.assert_failed_manifest(output)
            self.assertFalse(any("boot" in args or "shutdown" in args for args, _ in commands.calls))
            self.assertEqual(commands.states, {"phone": "Booted", "tablet": "Booted"})

    def test_diagnostics_failure_cannot_prevent_owned_shutdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            commands = Commands(output, ["test-phone", "screenshot-phone", "container-phone"])
            self.run_fake(output, commands)
            self.assert_failed_manifest(output)
            capture = json.loads((output / "p0-signin/phone-capture-diagnostics.json").read_text())
            self.assertEqual(len(capture["errors"]), 2)
            self.assertIn((("xcrun", "simctl", "shutdown", "phone"), 300), commands.calls)

    def test_interrupt_preserves_durable_capture_and_owned_shutdown_and_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            commands = Commands(output, ["interrupt-phone"])
            with self.assertRaises(KeyboardInterrupt):
                self.run_fake(output, commands)
            report = self.assert_failed_manifest(output)
            self.assertIn("Interrupted: KeyboardInterrupt", report["failures"])
            self.assertIn((("xcrun", "simctl", "shutdown", "phone"), 300), commands.calls)
            self.assertTrue((output / "p0-signin/phone-durable-diagnostics/p0-before-interaction.png").is_file())

    def test_existing_evidence_or_symlink_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evidence"; output.mkdir()
            original = output / preflight.MANIFEST
            original.write_text("prior evidence")
            link = root / "symlink"; link.symlink_to(output, target_is_directory=True)
            for path in (output, link, original):
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, "fresh evidence"):
                    preflight.run(path)
            self.assertEqual(original.read_text(), "prior evidence")
            self.assertEqual(list(output.iterdir()), [original])

    def test_linux_failure_has_real_source_identity_but_no_native_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            commands = Commands(output)
            with patch.object(preflight.platform, "system", return_value="Linux"), \
                    patch.object(preflight.rendering, "command", side_effect=commands):
                report = preflight.run(output)
            self.assert_failed_manifest(output)
            self.assertIn("macOS", report["failures"][0])
            self.assertIn("sourceGeneration", report)
            self.assertFalse(any(args[0] != "git" for args, _ in commands.calls))

    def test_midrun_input_changes_cannot_pass_and_command_log_is_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            original = preflight.input_hashes()
            marker = Path(temporary) / "prior-log"
            with patch.object(preflight.rendering, "COMMAND_LOG", marker), \
                    patch.object(preflight, "input_hashes", side_effect=[original, {**original, "changed": "yes"}]):
                self.run_fake(output, Commands(output))
                self.assertEqual(preflight.rendering.COMMAND_LOG, marker)
            report = self.assert_failed_manifest(output)
            self.assertIn("changed during", report["failures"][0])

    def test_builder_commit_change_after_execution_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            commands = Commands(output, ["final-commit-drift"])
            self.run_fake(output, commands)
            report = self.assert_failed_manifest(output)
            self.assertIn("Builder commit changed", report["failures"][0])
            self.assertEqual(commands.states, {"phone": "Shutdown", "tablet": "Shutdown"})

    def test_aggregate_rejects_duplicate_devices_missing_cases_and_duplicate_screenshot_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            summary = fixtures.P0SignInRenderingEvidenceTests().make_export(directory)
            report = preflight.p0.verify_export(directory, summary)
            reports = [{**copy.deepcopy(report), "deviceClass": kind} for kind in ("phone", "tablet")]
            self.assertEqual(preflight.matrix_counts(reports), (8, 24, True))
            for mutation in ("device", "case", "screen", "passed", "failure"):
                changed = copy.deepcopy(reports)
                if mutation == "device": changed[1]["deviceClass"] = "phone"
                if mutation == "case": changed[1]["cases"].pop()
                if mutation == "screen": changed[1]["cases"][0]["screenshots"] *= 2
                if mutation == "passed": changed[0]["passed"] = False
                if mutation == "failure": changed[0]["failures"] = ["failed"]
                self.assertFalse(preflight.matrix_counts(changed)[2])


if __name__ == "__main__":
    unittest.main()
