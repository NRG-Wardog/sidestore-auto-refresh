"""Crash-reason privacy at the prepared SideStore AppDelegate source."""
from pathlib import Path
import importlib.util
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = ROOT / "scripts/patch_v3_service.py"
PRIMITIVES = ROOT / "scripts/templates/v3_behavioral_primitives.swift"
HARNESS = ROOT / "tests/fixtures/v3_crash_log_privacy_harness.swift"
SWIFTC = shutil.which("swiftc")


def production_crash_log_privacy_declaration():
    primitives = PRIMITIVES.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^enum V3CrashLogPrivacy\s*\{.*?^\}", primitives)
    if match is None:
        raise AssertionError("V3CrashLogPrivacy declaration not found in production primitives")
    return match.group(0)


def load_patch_module():
    spec = importlib.util.spec_from_file_location("patch_v3_service_crash_privacy", PATCH_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PINNED_HANDLER = '''private extension AppDelegate {
    func setupCrashHandler() {
        NSSetUncaughtExceptionHandler { exception in
            // Clear handler immediately so execution can never recurse under any circumstance
            NSSetUncaughtExceptionHandler(nil)

            let stackTrace = exception.callStackSymbols.joined(separator: "\\n")
            let message = """
            | UNCAUGHT NSEXCEPTION CRASH
              Name: \\(exception.name.rawValue)
              Reason: \\(exception.reason ?? "Unknown")
              Call Stack: \\(stackTrace)
            """

            debugLog(message)
            fputs(message, stderr)
            fflush(stderr)
            NSLog("%@", message)
        }
    }
}
'''


class V3ExceptionLogPrivacyTests(unittest.TestCase):
    def test_prepared_handler_uses_only_safe_message_for_every_sink(self):
        module = load_patch_module()
        prepared = module.patch_crash_log_privacy(PINNED_HANDLER)
        self.assertEqual(module.patch_crash_log_privacy(prepared), prepared)

        handler = prepared[prepared.index("func setupCrashHandler()"):]
        self.assertIn("V3_CRASH_REASON_LOG_PRIVACY_V1", handler)
        self.assertIn("let message = V3CrashLogPrivacy.safeCrashMarker(reason: exception.reason)", handler)
        self.assertIn("debugLog(message)", handler)
        self.assertIn("fputs(message, stderr)", handler)
        self.assertIn('NSLog("%@", message)', handler)
        self.assertNotIn("exception.reason ??", handler)
        self.assertNotIn("exception.callStackSymbols", handler)
        self.assertNotIn("exception.name", handler)
        self.assertNotIn("\\(exception.reason", handler)

        integration = PATCH_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("text = patch_crash_log_privacy(text)", integration)

    def test_production_helper_does_not_emit_secret_reason_or_path(self):
        if SWIFTC is None:
            self.skipTest("swiftc unavailable; behavioral harness runs in macOS CI")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "crash_log_privacy.swift"
            executable = Path(directory) / "crash_log_privacy"
            source.write_text(
                production_crash_log_privacy_declaration() + "\n" +
                HARNESS.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            compiled = subprocess.run([SWIFTC, str(source), "-o", str(executable)],
                                      check=False, capture_output=True, text=True)
            self.assertEqual(
                compiled.returncode, 0,
                msg=f"swiftc failed with exit code {compiled.returncode}; stderr:\n{compiled.stderr}",
            )
            result = subprocess.run([str(executable)], check=False, capture_output=True, text=True)
            self.assertEqual(
                result.returncode, 0,
                msg=f"crash privacy harness failed with exit code {result.returncode}; stderr:\n{result.stderr}",
            )
            self.assertIn("V3_CRASH_LOG_PRIVACY_PASS", result.stdout)
            self.assertNotIn("CRASH_REASON_SECRET", result.stdout)


if __name__ == "__main__":
    unittest.main()
