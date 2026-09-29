"""Run status-authority races against the production Swift helper declarations."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIMITIVES = ROOT / "scripts/templates/v3_behavioral_primitives.swift"
HARNESS = ROOT / "tests/fixtures/v3_status_authority_interleavings_harness.swift"


def extract_swift_declaration(source: str, declaration: str) -> str:
    """Return one balanced-brace declaration directly from production source."""
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
    raise AssertionError(f"production declaration has an unterminated body: {declaration}")


class StatusAuthorityInterleavingTests(unittest.TestCase):
    def test_status_authority_interleavings_execute_production_helpers(self):
        swiftc = shutil.which("swiftc")
        if sys.platform != "darwin" or not swiftc:
            self.skipTest("Swift behavioral harness requires macOS CI with swiftc")

        primitives = PRIMITIVES.read_text(encoding="utf-8")
        declarations = [
            "struct V3StatusWriteTicket:",
            "enum V3StatusAuthorityLeaseKind:",
            "struct V3StatusLeaseWaiterOrder:",
            "enum V3StatusWriteOutcome:",
            "struct V3StatusWriteAuthority:",
            "enum V3StatusReplyCommitPolicy {",
            "enum V3StatusAuthorityOperationPolicy {",
            "enum V3OperationReplyFieldPolicy {",
        ]
        production = "\n\n".join(extract_swift_declaration(primitives, name)
                                    for name in declarations)
        harness = HARNESS.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            main = directory / "main.swift"
            executable = directory / "status-authority-interleavings"
            main.write_text("import Foundation\nimport CoreFoundation\n" + production + "\n" + harness,
                            encoding="utf-8")
            compiled = subprocess.run([swiftc, "-parse-as-library", str(main), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_STATUS_AUTHORITY_INTERLEAVINGS_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
