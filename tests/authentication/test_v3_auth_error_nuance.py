from tests.support.auth_diagnostic_owner import diagnostic_error_types, operation_error_cases, provisioning_diagnostic_helpers
"""Executable regression for conservative typed authentication error mapping."""
from tests.support.product_sources import ProductSource
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SWIFTC = shutil.which("swiftc")


class AuthErrorNuanceTests(unittest.TestCase):
    def test_untyped_response_shape_does_not_claim_apple_service_outage(self):
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; executable auth harness runs in macOS CI")

        runtime = (ProductSource('v3_headless_runtime.swift')).read_text(encoding="utf-8")
        classifier_start = runtime.index("enum V3AuthFailureKind:")
        classifier_end = runtime.index("// MARK: - Provisioning failure guidance", classifier_start)
        classifier = runtime[classifier_start:classifier_end]

        shell = (ProductSource('v3_unified_shell.swift')).read_text(encoding="utf-8")
        message_start = shell.index("    static func failureMessage(from failure: [String: Any]) -> String {")
        message_end = shell.index("\n    static func failureDetails(", message_start)
        host_message = shell[message_start:message_end].replace(
            "static func failureMessage", "func v3HostAuthFailureMessage")

        source = "\n".join((
            "import Foundation",
            (ProductSource('v3_wire_contract.swift')).read_text(encoding="utf-8"),
            (ProductSource('combined_failure.swift')).read_text(encoding="utf-8"),
            classifier,
            host_message,
            (ProductSource('v3_behavioral_primitives.swift')).read_text(encoding="utf-8"),
            diagnostic_error_types(), operation_error_cases(), provisioning_diagnostic_helpers(),
            "@main" + (ROOT / "tests/fixtures/v3_auth_error_nuance_harness.swift").read_text(encoding="utf-8").split("@main", 1)[1],
        ))

        with tempfile.TemporaryDirectory() as temporary:
            main = Path(temporary) / "main.swift"
            executable = Path(temporary) / "auth-error-nuance"
            main.write_text(source, encoding="utf-8")
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(main), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_AUTH_ERROR_NUANCE_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
