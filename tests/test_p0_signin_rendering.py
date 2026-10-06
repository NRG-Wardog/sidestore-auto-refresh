"""Extraction and negative evidence gates; these are NOT simulator/UI results."""
import importlib.util
import contextlib
import io
import sys
from types import SimpleNamespace
import json
from pathlib import Path
import struct
import shutil
import subprocess
import tempfile
import unittest
import zlib
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("p0_rendering", ROOT / "scripts/run_p0_signin_rendering.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def image():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (renderer.PNG_MAGIC + chunk(b"IHDR", struct.pack(">IIBBBBB", 100, 100, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x00" * 300) * 100)) + chunk(b"IEND", b""))


class P0SignInRenderingEvidenceTests(unittest.TestCase):
    def make_export(self, directory):
        manifest = []
        for name in sorted(renderer.REQUIRED_CASES):
            screenshots = []
            for suffix in sorted(renderer.REQUIRED_SCREENSHOTS[name]):
                attachment_name = f"p0-{name}-{suffix}"
                screenshots.append(attachment_name)
                filename = attachment_name + ".png"
                (directory / filename).write_bytes(image())
                manifest.append({"exportedFileName": filename, "suggestedHumanReadableName": filename})
            (directory / (name + ".json")).write_text(json.dumps({
                "schema": "p0-signin-case-v1", "case": name, "passed": True, "failures": [],
                "xctestFailureCount": 0, "teardownCaptured": True,
                "measurements": [{"control": control, "bounds": [20, 100, 200, 44], "hittable": True,
                    "label": "Fixture " + control, "viewportBounds": [0, 0, 320, 640], "viewportWidth": 320,
                    "largestDynamicType": name.endswith("-largest")} for control in renderer.REQUIRED_CONTROLS],
                "clipboardExactMatch": True, "safeDiagnosticOnly": True, "cancelInvoked": True,
                "noRedundantStatusPanel": True, "oneCredentialsPanel": True,
                "largestDynamicType": name.endswith("-largest"), "submitting": name.startswith("submitting-"),
                "submissionInvoked": name.startswith("submitting-"), "priorFailureCleared": name.startswith("submitting-"),
                "screenshots": screenshots,
            }))
        (directory / "manifest.json").write_text(json.dumps([{"attachments": manifest}]))
        return {"result": "Passed", "passedTests": 4, "failedTests": 0, "skippedTests": 0}

    def test_extraction_preserves_interpolation_comments_and_actor(self):
        source = '@MainActor\nfinal class V3SettingsStore {\n    let text = "} {" // braces are text\n}\n\nstruct Other {}\n'
        self.assertEqual(renderer.declaration(source, "final class V3SettingsStore"),
                         source[:source.index("\n\n")] + "\n")

    def test_absent_ambiguous_or_unclosed_sources_fail_closed(self):
        for source in ("", "struct X {\n", "struct X {\n}\nstruct X {\n}\n"):
            with self.assertRaises(ValueError):
                renderer.declaration(source, "struct X")

    def test_xctest_exceptions_and_missing_teardown_cannot_look_successful(self):
        for changes in ({"xctestFailureCount": 1}, {"xctestFailureCount": None},
                        {"xctestFailureCount": False}, {"xctestFailureCount": 0.0}, {"teardownCaptured": False}, {"teardownCaptured": None}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); summary = self.make_export(root)
                path = root / "credentials-default.json"
                data = json.loads(path.read_text()); data.update(changes); path.write_text(json.dumps(data))
                self.assertFalse(renderer.verify_export(root, summary)["passed"])

    def test_diagnostic_screenshots_cannot_replace_the_acceptance_matrix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); summary = self.make_export(root)
            (root / "p0-credentials-default-diagnostic-launch.png").write_bytes(image())
            self.assertTrue(renderer.verify_export(root, summary)["passed"])
            (root / "p0-credentials-default-copy-details.png").unlink()
            self.assertFalse(renderer.verify_export(root, summary)["passed"])

    def test_queries_use_short_ids_and_teardown_preserves_early_failure(self):
        ui = (ROOT / "tests/fixtures/p0_signin_ui_tests.swift").read_text()
        app = (ROOT / "tests/fixtures/p0_signin_app.swift").read_text()
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text()
        self.assertNotIn('app.staticTexts[expected]', ui)
        self.assertIn('app.staticTexts["signin.prompt.previous-error-body"]', ui)
        self.assertIn('.accessibilityIdentifier("signin.prompt.previous-error-body")', shell)
        self.assertIn('addTeardownBlock { @MainActor [self] in', ui)
        self.assertLess(ui.index('addTeardownBlock'), ui.index('app.launch()'))
        self.assertLess(ui.index('diagnosticSnapshot("launch")'), ui.index('require(app.staticTexts["p0-ready"]'))
        self.assertNotIn('defer { finish() }', ui)
        self.assertIn('testRun?.totalFailureCount ?? 1', ui)
        self.assertIn('diagnosticSnapshot("teardown")', ui)
        self.assertIn('String(app.debugDescription.prefix(131072))', ui)
        self.assertIn('"passed": failures.isEmpty && frameworkFailures == 0', ui)
        self.assertLess(ui.index('record(cancel, name: "cancel")'), ui.index('let cancelCount ='))
        self.assertIn('p0-prior-failure', app)
        self.assertIn('reveal(header, name: "credentials header", towardTop: true)', ui)
        self.assertIn('Stale error or Copy Details remains after returning to the previous-error area', ui)
        self.assertIn('.accessibilityElement(children: .contain)', app)
        workflow = (ROOT / '.github/workflows/livecontainer-build.yml').read_text()
        self.assertIn('artifacts/layout-evidence/p0-signin/**/*.txt', workflow)
        self.assertIn('artifacts/layout-evidence/p0-signin/**/*.swift', workflow)
        self.assertIn('artifacts/layout-evidence/p0-signin/project/**', workflow)

    def test_durable_capture_precedes_attachments_and_remote_teardown(self):
        ui = (ROOT / 'tests/fixtures/p0_signin_ui_tests.swift').read_text()
        screenshot = renderer.member(ui, '@MainActor private func screenshot')
        self.assertLess(screenshot.index('persist(captured.pngRepresentation'), screenshot.index('add(attachment)'))
        self.assertEqual(screenshot.count('app.screenshot()'), 1)
        self.assertIn('options: .atomic', ui)
        self.assertIn('P0_SIGNIN_EVIDENCE_RUN_ID', ui)
        self.assertIn('^[0-9a-f]{32}$', ui)
        finish = renderer.member(ui, '@MainActor private func finish')
        self.assertLess(finish.index('persistProgress("teardown-started")'), finish.index('diagnosticSnapshot("teardown")'))
        self.assertLess(finish.index('persist(reportData'), finish.index('app.terminate()'))
        reveal = renderer.member(ui, '@MainActor private func reveal')
        self.assertLess(reveal.index('var region = visibleScrollRegion()'), reveal.index('for attempt in'))
        self.assertLess(reveal.index('persistProgress("before-scroll")'), reveal.index('start.press('))
        self.assertIn('unchangedFrames >= 3', reveal)
        self.assertIn('let freshRegion = visibleScrollRegion()', reveal)
        self.assertIn('P0SignInViewport.contains(element.frame, in: region)', reveal)
        self.assertIn('diagnosticSnapshot("scroll-stalled")', reveal)
        self.assertIn('element.exists && element.isHittable &&', reveal)
        self.assertIn('"passed": false, "complete": false', ui)
        self.assertIn('import Foundation\n', ui)
        self.assertIn('runID.utf8.count == 32', ui)
        self.assertLess(ui.index('Native Return did not dismiss the username keyboard'), ui.index('record(password, name: "password")'))
        self.assertLess(ui.index('Native Return did not dismiss the credentials keyboard'), ui.index('record(copy, name: "copy-details")'))
        self.assertNotIn('typeText(', reveal)
        self.assertNotIn('.tap()', reveal)


    def test_exact_runtime_viewport_math_executes(self):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler unavailable; actual sign-in viewport helper executes in required macOS CI')
        ui = (ROOT / 'tests/fixtures/p0_signin_ui_tests.swift').read_text()
        helper = renderer.declaration(ui, 'enum P0SignInViewport')
        self.assertIn('P0SignInViewport.contains(element.frame, in: region)', ui)
        self.assertIn('P0SignInViewport.available(viewport: viewport', ui)
        self.assertIn('import CoreGraphics\n', ui)
        harness = 'import Foundation\nimport CoreGraphics\n' + helper + r'''
@main struct Test {
    static func main() {
        let viewport = CGRect(x: 0, y: 0, width: 320, height: 844)
        let nav = CGRect(x: 0, y: 0, width: 320, height: 100)
        let keyboard = CGRect(x: 0, y: 500, width: 320, height: 344)
        let footer = CGRect(x: 0, y: 800, width: 320, height: 24)
        let region = P0SignInViewport.available(viewport: viewport, navigation: nav, keyboard: keyboard, footer: footer)
        precondition(region == CGRect(x: 4, y: 104, width: 312, height: 392))
        precondition(P0SignInViewport.contains(CGRect(x: 20, y: 120, width: 240, height: 44), in: region))
        precondition(!P0SignInViewport.contains(CGRect(x: 20, y: 470, width: 240, height: 44), in: region), "A hittable center does not prove full containment")
        precondition(!P0SignInViewport.contains(CGRect(x: 0, y: 120, width: 320, height: 44), in: region))
        let offscreenKeyboard = CGRect(x: 400, y: 500, width: 300, height: 300)
        let unaffected = P0SignInViewport.available(viewport: viewport, navigation: nav, keyboard: offscreenKeyboard, footer: footer)
        precondition(unaffected.maxY == 796)
        precondition(P0SignInViewport.available(viewport: .zero, navigation: nil, keyboard: nil, footer: nil) == .zero)
        precondition(!P0SignInViewport.contains(CGRect(x: 20, y: 120, width: 240, height: 44), in: .infinite))
        print("P0_SIGNIN_VIEWPORT_PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'main.swift'; source.write_text(harness)
            binary = root / 'viewport'
            compiled = subprocess.run([compiler, '-parse-as-library', str(source), '-o', str(binary)], capture_output=True, text=True, timeout=90)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn('P0_SIGNIN_VIEWPORT_PASS', executed.stdout)

    def test_valid_complete_export_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = renderer.verify_export(root, self.make_export(root))
            self.assertTrue(result["passed"], result["failures"])
            self.assertEqual(result["reportCount"], 4)
            self.assertEqual(len(result["artifactSHA256"]), 16)

    def test_skipped_failed_partial_or_wrong_xctest_summary_is_rejected(self):
        for changes in ({"skippedTests": 1}, {"failedTests": 1}, {"passedTests": 5}, {"result": "Failed"}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); summary = self.make_export(root); summary.update(changes)
                self.assertFalse(renderer.verify_export(root, summary)["passed"])

    def test_missing_duplicate_failed_or_unmeasured_case_is_rejected(self):
        for mutation in ("missing", "duplicate", "failed", "unmeasured", "nonfinite", "empty-screenshot-list"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); summary = self.make_export(root)
                path = root / "credentials-largest.json"; data = json.loads(path.read_text())
                if mutation == "missing": path.unlink()
                elif mutation == "duplicate": (root / "extra.json").write_text(path.read_text())
                else:
                    if mutation == "failed": data["passed"] = False
                    if mutation == "unmeasured": data["measurements"] = []
                    if mutation == "nonfinite": data["measurements"][0]["bounds"][0] = float("inf")
                    if mutation == "empty-screenshot-list": data["screenshots"] = []
                    path.write_text(json.dumps(data))
                self.assertFalse(renderer.verify_export(root, summary)["passed"])

    def test_missing_truncated_or_corrupt_screenshot_bytes_are_rejected(self):
        for mutation in ("missing", "truncated", "crc", "manifest-only"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); summary = self.make_export(root)
                path = root / "p0-credentials-largest-copy-details.png"
                if mutation in ("missing", "manifest-only"): path.unlink()
                elif mutation == "truncated": path.write_bytes(renderer.PNG_MAGIC)
                else:
                    data = bytearray(path.read_bytes()); data[20] ^= 1; path.write_bytes(data)
                self.assertFalse(renderer.verify_export(root, summary)["passed"])

    def test_valid_chunk_crcs_do_not_prove_decodable_pixels(self):
        good = image()
        start = good.index(b"IDAT") - 4
        old_length = struct.unpack(">I", good[start:start + 4])[0]
        for bad_stream in (b"not a compressed pixel stream", zlib.compress(b"too short"),
                           zlib.compress((b"\x05" + b"\x00" * 300) * 100)):
            chunk = (struct.pack(">I", len(bad_stream)) + b"IDAT" + bad_stream +
                     struct.pack(">I", zlib.crc32(b"IDAT" + bad_stream) & 0xffffffff))
            self.assertFalse(renderer.valid_png(good[:start] + chunk + good[start + 12 + old_length:]))

    def test_project_covers_every_exact_production_declaration(self):
        project = (ROOT / "tests/fixtures/p0_signin_project/project.pbxproj").read_text()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "generated.swift"
            source.write_text((ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text() + "\n" +
                              (ROOT / "scripts/templates/v3_unified_shell.swift").read_text())
            sources, hashes = renderer.extract_sources(source, ROOT / "scripts/templates")
        expected = {name for name in sources if name.endswith(".swift")}
        expected.update({"p0_signin_app.swift", "p0_signin_ui_tests.swift"})
        actual = {line.split("path = ", 1)[1].split(";", 1)[0] for line in project.splitlines()
                  if "lastKnownFileType = sourcecode.swift" in line}
        self.assertEqual(actual, expected)
        self.assertIn("com.apple.product-type.bundle.ui-testing", project)
        self.assertIn("TEST_TARGET_NAME = P0SignIn", project)
        self.assertIn("V3SignInView.full-production", hashes)
        self.assertIn("V3AuthStore.static func failureDetails", hashes)
        self.assertEqual(sources["CombinedFailure.swift"], (ROOT / "scripts/templates/combined_failure.swift").read_text())
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text()
        for signature in renderer.VIEW_MEMBERS:
            snippet = renderer.member(renderer.declaration(shell, "struct V3SignInView"), signature)
            self.assertIn(snippet, sources["V3SignInView.swift"])
        self.assertNotIn("V3Settings", " ".join(sources))
        self.assertNotIn("V3DeveloperProfile", " ".join(sources))

    def test_p0_reuses_devices_and_preserves_existing_gates(self):
        source = (ROOT / "scripts/run_issue25_rendering.py").read_text()
        workflow = (ROOT / ".github/workflows/livecontainer-build.yml").read_text()
        self.assertIn('len(reports) == (14 if native_build else 10)', source)
        self.assertIn('len(p0_reports) == 2 and all(report["passed"]', source)
        self.assertIn('p0.execute(p0_build, kind, device, output, command)', source)
        self.assertIn('--require-p0-signin', workflow)
        self.assertIn('timeout-minutes: 80', workflow)
        self.assertIn('timeout-minutes: 170', workflow)
        self.assertIn('timeout-minutes: 40', workflow)
        self.assertIn('Legacy layout build phase finished', source)
        self.assertIn('Required sign-in UI build phase finished', source)
        self.assertIn('Required sign-in UI {kind} phase finished', source)
        p0_source = (ROOT / "scripts/run_p0_signin_rendering.py").read_text()
        self.assertNotIn('"simctl", "boot"', p0_source)
        self.assertNotIn('"simctl", "create"', p0_source)
        self.assertIn('"-parallel-testing-enabled", "NO"', p0_source)
        self.assertIn('timeout=1080', p0_source)
        self.assertIn('"-default-test-execution-time-allowance", "240"', p0_source)
        self.assertIn('"-maximum-test-execution-time-allowance", "240"', p0_source)

    def test_ui_uses_real_taps_and_reports_limits(self):
        source = (ROOT / "tests/fixtures/p0_signin_ui_tests.swift").read_text()
        for marker in ('copy.tap()', 'proceed.tap()', 'cancel.tap()', 'app.terminate()', 'app.launch()',
                       'let captured = app.screenshot()', 'XCTAttachment(screenshot: captured)', 'spoken VoiceOver output is not asserted',
                       'p0-clipboard', 'p0-cancelled-submission', 'p0-synthetic-password', 'safeDiagnosticOnly',
                       '!cancel.isEnabled', '"Status"', '"Needs your input"'):
            self.assertIn(marker, source)
        self.assertEqual(source.count('    @MainActor func test'), 4)
        self.assertNotIn('screenshot("cancelled")', source)
        fixture = (ROOT / "tests/fixtures/p0_signin_app.swift").read_text()
        self.assertIn('UIPasteboard.general.string', fixture)
        self.assertNotIn('UIPasteboard', source)  # No cross-process pasteboard reads.

    def test_p0_build_or_execution_failure_preserves_all_fourteen_legacy_reports(self):
        spec = importlib.util.spec_from_file_location("legacy_renderer", ROOT / "scripts/run_issue25_rendering.py")
        legacy = importlib.util.module_from_spec(spec); spec.loader.exec_module(legacy)
        for stage in ("build", "execution"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); output = root / "evidence"
                p0 = Mock()
                def prepare(*args):
                    (output / "p0-signin").mkdir()
                    if stage == "build": raise RuntimeError("intentional UI build failure")
                    return {"sourceSHA256": {}}
                p0.prepare.side_effect = prepare
                p0.execute.side_effect = RuntimeError("intentional UI execution failure")
                devices = [("phone", "phone", "26.2"), ("tablet", "tablet", "26.2")]
                state = json.dumps({"devices": {"runtime": [{"udid": value[1], "state": "Booted"} for value in devices]}})
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(sys, "argv", ["runner", "--livecontainer", str(root),
                        "--v3-source", str(root / "source.swift"), "--output", str(output), "--require-p0-signin"]))
                    stack.enter_context(patch.object(legacy.platform, "system", return_value="Darwin"))
                    stack.enter_context(patch.object(legacy, "build_app", return_value=(root, "fixture", {})))
                    stack.enter_context(patch.object(legacy, "build_v3_app", return_value=(root, "native", {})))
                    stack.enter_context(patch.object(legacy, "available_devices", return_value=devices))
                    stack.enter_context(patch.object(legacy, "command", side_effect=lambda *args, **kwargs: state if "devices" in args else "fixture-commit"))
                    execute = stack.enter_context(patch.object(legacy, "execute", return_value={"passed": True}))
                    stack.enter_context(patch.object(legacy.importlib.util, "spec_from_file_location", return_value=SimpleNamespace(loader=Mock())))
                    stack.enter_context(patch.object(legacy.importlib.util, "module_from_spec", return_value=p0))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    with self.assertRaises(SystemExit): legacy.main()
                report = json.loads((output / "rendering-verification.json").read_text())
                self.assertEqual(execute.call_count, 14)
                self.assertEqual(report["reportCount"], 14)
                self.assertFalse(report["passed"])
                self.assertFalse(report["p0SignIn"]["passed"])
                legacy.COMMAND_LOG = None

    def test_missing_result_bundle_never_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "no result bundle"):
                renderer.execute({"xctestrun": "fixture", "sourceSHA256": {}}, "phone", "device", Path(temporary), Mock())

    def test_missing_runtime_proofs_wrong_width_and_clipping_fail_closed(self):
        for mutation in ("clipboardExactMatch", "safeDiagnosticOnly", "cancelInvoked", "oneCredentialsPanel",
                         "noRedundantStatusPanel", "width", "clipped", "control", "dynamic-type", "state"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); summary = self.make_export(root)
                path = root / "credentials-largest.json"; data = json.loads(path.read_text())
                if mutation in ("clipboardExactMatch", "safeDiagnosticOnly", "cancelInvoked", "oneCredentialsPanel", "noRedundantStatusPanel"):
                    data[mutation] = False
                elif mutation == "width": data["measurements"][0]["viewportBounds"][2] = 390
                elif mutation == "clipped": data["measurements"][0]["bounds"][0] = 300
                elif mutation == "control": data["measurements"].pop()
                elif mutation == "dynamic-type": data["largestDynamicType"] = False
                elif mutation == "state": data["submitting"] = True
                path.write_text(json.dumps(data))
                self.assertFalse(renderer.verify_export(root, summary)["passed"])

    def test_member_extraction_preserves_multiline_and_one_line_bodies(self):
        source = "struct X {\n    var body: some View {\n        Text(\"}\")\n    }\n    private var shown: Bool { true }\n}\n"
        self.assertEqual(renderer.member(source, "private var shown: Bool"), "    private var shown: Bool { true }\n")
        self.assertIn('Text("}")', renderer.member(source, "var body: some View"))
        for bad in ("", source + source, "    var body: some View {\n"):
            with self.assertRaises(ValueError): renderer.member(bad, "var body: some View")

    def test_optional_diagnostic_id_helper_is_extracted_exactly_when_present(self):
        templates = ROOT / "scripts/templates"
        shell = (templates / "v3_unified_shell.swift").read_text()
        needle = "    static func failureMessage(from failure: [String: Any]) -> String {"
        if "private static func failureMessageWithoutDiagnosticCode" not in shell:
            shell = shell.replace(needle, needle + '\n        failureMessageWithoutDiagnosticCode(from: failure)\n    }\n\n    private static func failureMessageWithoutDiagnosticCode(from failure: [String: Any]) -> String {', 1)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "generated.swift"
            source.write_text((templates / "v3_behavioral_primitives.swift").read_text() + "\n" + shell)
            sources, hashes = renderer.extract_sources(source, templates)
            self.assertIn("private static func failureMessageWithoutDiagnosticCode", sources["V3AuthStoreDiagnostics.swift"])
            self.assertIn("V3AuthStore.private static func failureMessageWithoutDiagnosticCode", hashes)

    def test_credentials_fixture_matches_real_producer_without_fabricated_options(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text()
        producer = renderer.member(runtime, "func credentials")
        fixture = (ROOT / "tests/fixtures/p0_signin_app.swift").read_text()
        prompt = fixture[fixture.index("@Published var prompt:"):fixture.index("@Published var previousFailure:")]
        password = '["key": "password", "label": "Password", "secure": "true"]'
        self.assertIn(password, producer)
        self.assertIn(password, prompt)
        apple_id = '["key": "appleID", "label": "Apple ID", "secure": "false", "value": '
        self.assertIn(apple_id + 'expectedOwner ?? ""]', producer)
        self.assertIn(apple_id + '""]', prompt)
        self.assertNotIn("options:", producer)
        self.assertNotIn('"options"', prompt)
        self.assertNotIn('"value": password', prompt)
        ui = (ROOT / "tests/fixtures/p0_signin_ui_tests.swift").read_text()
        self.assertIn('app.buttons["Submit"]', ui)
        self.assertIn('username.typeText("p0-user@example.invalid")', ui)
        self.assertIn('password.typeText("p0-synthetic-password\\n")', ui)
        self.assertIn('clearingAfterSubmission(', fixture)

    def test_prepare_preserves_source_identities_and_tags_actual_builder(self):
        templates = ROOT / "scripts/templates"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "generated.swift"
            source.write_text((templates / "v3_behavioral_primitives.swift").read_text() + "\n" +
                              (templates / "v3_unified_shell.swift").read_text())
            output = root / "evidence"; output.mkdir()
            commit = "0123456789abcdef0123456789abcdef01234567"
            def command(*args, **kwargs):
                if args[0] == "git": return commit
                self.assertEqual(args[:2], ("xcodebuild", "build-for-testing"))
                self.assertEqual(kwargs["timeout"], 300)
                products = output / "p0-build/DerivedData/Build/Products"
                products.mkdir(parents=True)
                (products / "fixture.xctestrun").write_text("fixture only, never native evidence")
                return ""
            prepared = renderer.prepare(output, source, command)
            identity = json.loads((output / "p0-signin/source-identity.json").read_text())
            self.assertEqual(identity["sourceSHA256"], prepared["sourceSHA256"])
            self.assertEqual(identity["sourceSHA256"]["generated-shell"], renderer.digest(source))
            project = output / "p0-signin/project"
            info = renderer.plistlib.loads((project / "Info.plist").read_bytes())
            self.assertEqual(info["LCBuilderCommit"], commit)
            for path in (output / "p0-signin/sources").iterdir():
                self.assertEqual(identity["sourceSHA256"][path.name], renderer.digest(path))
            self.assertEqual(identity["sourceSHA256"]["project/Info.plist"], renderer.digest(project / "Info.plist"))
