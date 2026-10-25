import shutil
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AuthLeaseRetirementTests(unittest.TestCase):
    def test_auth_unavailable_policy_stays_in_host_bridge_layer(self):
        policy_name = "V3AuthSessionUnavailableReplyPolicy"
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        self.assertNotIn(policy_name, primitives)
        self.assertNotIn("V3WireContract", re.sub(r"//[^\n]*", "", primitives))
        self.assertNotIn("CombinedFailure", wire)
        self.assertIn(policy_name, bridge)
        self.assertNotIn("replacesAuthSession", bridge)
        self.assertIn("CombinedFailure.decode(rawFailure, expectedID: requestID)", bridge)
        self.assertIn("V3WireContract.strictInt(envelope[\"version\"]) == 1", bridge)

        # The pinned generator appends the bridge only after its two shared
        # dependencies, matching the executable fixture assembly below.
        generator = (ROOT / "scripts/patch_v3_service.py").read_text(encoding="utf-8")
        start = generator.rindex("def host(s):")
        generated_host = generator[start:generator.index("edit(live,", start)]
        wire_at = generated_host.index('"v3_wire_contract.swift"')
        primitives_at = generated_host.index('"v3_behavioral_primitives.swift"')
        bridge_at = generated_host.index('"v3_service_bridge.swift"')
        self.assertLess(wire_at, primitives_at)
        self.assertLess(primitives_at, bridge_at)

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
                + (ROOT / "scripts/templates/v3_wire_contract.swift").read_text()
                + (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text()
                + (ROOT / "scripts/templates/combined_service_connection.swift").read_text()
                + (ROOT / "scripts/templates/v3_service_bridge.swift").read_text()
            )
            executable = directory / "auth-lease-retirement-tests"
            compiled = subprocess.run(
                [compiler, "-parse-as-library", str(program), "-o", str(executable)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            try:
                result = subprocess.run(
                    [str(executable)], capture_output=True, text=True, timeout=10
                )
            except subprocess.TimeoutExpired as error:
                stderr = error.stderr or ""
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                stdout = error.stdout or ""
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", errors="replace")
                self.fail(f"auth lease harness timed out; stdout={stdout}\nstderr={stderr}")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 auth lease retirement PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
