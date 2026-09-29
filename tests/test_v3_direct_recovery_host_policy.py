"""Execute the production host parser and ACK policy for direct recovery."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIMITIVES = ROOT / "scripts/templates/v3_behavioral_primitives.swift"
HARNESS = ROOT / "tests/fixtures/v3_direct_recovery_host_policy_harness.swift"


def extract_swift_declaration(source: str, declaration: str) -> str:
    """Extract one balanced production Swift declaration."""
    start = source.find(declaration)
    if start < 0:
        raise AssertionError(f"production declaration not found: {declaration}")
    opening = source.find("{", start)
    if opening < 0:
        raise AssertionError(f"production declaration has no body: {declaration}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated production declaration: {declaration}")


class DirectRecoveryHostPolicyTests(unittest.TestCase):
    def test_production_record_and_ack_policy_execute(self):
        swiftc = shutil.which("swiftc")
        if sys.platform != "darwin" or not swiftc:
            self.skipTest("Swift behavioral harness requires macOS CI with swiftc")

        primitives = PRIMITIVES.read_text(encoding="utf-8")
        production = "\n\n".join(
            extract_swift_declaration(primitives, declaration)
            for declaration in (
                "struct V3HostDirectRecoveryRecord:",
                "enum V3DirectRecoveryPostcondition:",
                "enum V3DirectRecoveryHostPolicy {",
            )
        )
        harness = HARNESS.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            main = directory / "main.swift"
            executable = directory / "direct-recovery-host-policy"
            main.write_text("import Foundation\n" + production + "\n" + harness,
                            encoding="utf-8")
            compiled = subprocess.run(
                [swiftc, "-parse-as-library", str(main), "-o", str(executable)],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_DIRECT_RECOVERY_HOST_POLICY_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
