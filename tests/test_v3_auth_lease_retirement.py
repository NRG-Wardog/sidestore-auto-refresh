import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AuthLeaseRetirementTests(unittest.TestCase):
    def test_shipped_bridge_reconciles_retired_auth_status_owner(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text(
                (ROOT / "tests/fixtures/v3_auth_lease_retirement_harness.swift").read_text()
                + (ROOT / "scripts/templates/combined_failure.swift").read_text()
                + (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text()
                + (ROOT / "scripts/templates/combined_service_connection.swift").read_text()
                + (ROOT / "scripts/templates/v3_wire_contract.swift").read_text()
                + (ROOT / "scripts/templates/v3_service_bridge.swift").read_text()
            )
            executable = directory / "auth-lease-retirement-tests"
            compiled = subprocess.run(
                [compiler, "-parse-as-library", str(program), "-o", str(executable)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=10
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 auth lease retirement PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
