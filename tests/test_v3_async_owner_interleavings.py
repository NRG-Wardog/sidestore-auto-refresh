"""Execute async request ownership scenarios against the production Swift helper."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIMITIVES = ROOT / "scripts/templates/v3_behavioral_primitives.swift"
HARNESS = ROOT / "tests/fixtures/v3_async_owner_interleavings_harness.swift"


def extract_swift_declaration(source: str, declaration: str) -> str:
    """Return one balanced-brace Swift declaration from its production source."""
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


class AsyncRequestOwnerInterleavingTests(unittest.TestCase):
    def test_request_ownership_interleavings_execute_production_helper(self):
        swiftc = shutil.which("swiftc")
        if sys.platform != "darwin" or not swiftc:
            self.skipTest("Swift behavioral harness requires macOS CI with swiftc")

        primitives = PRIMITIVES.read_text(encoding="utf-8")
        owner = extract_swift_declaration(primitives, "struct V3AsyncRequestOwner:")
        owner_state = extract_swift_declaration(primitives, "struct V3AsyncRequestOwnerState:")
        harness = HARNESS.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            main = directory / "main.swift"
            executable = directory / "async-owner-interleavings"
            main.write_text("import Foundation\n" + owner + "\n" + owner_state + "\n" + harness,
                            encoding="utf-8")
            compiled = subprocess.run([swiftc, "-parse-as-library", str(main), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_ASYNC_OWNER_INTERLEAVINGS_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
