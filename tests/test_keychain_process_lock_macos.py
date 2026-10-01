"""Run both production flock helpers from competing macOS processes."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_slices
import unittest

ROOT = Path(__file__).resolve().parents[1]


def extract_type(source: str, declaration: str) -> str:
    start = source.index(declaration)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unterminated production helper: " + declaration)


class ProductionProcessLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin":
            raise unittest.SkipTest("production Darwin flock harness requires macOS")
        compiler = shutil.which("swiftc")
        if not compiler:
            raise unittest.SkipTest("swiftc unavailable")
        keychain = (ROOT / "scripts/templates/embedded_shared_keychain.swift").read_text(encoding="utf-8")
        handoff = (ROOT / "scripts/templates/v3_secret_handoff.swift").read_text(encoding="utf-8")
        shared = (ROOT / "scripts/templates/v3_shared_app_group.swift").read_text(encoding="utf-8")
        embedded_lock = extract_type(keychain, "private enum LCSharedKeychainFileLock {")
        # Both production lock helpers resolve the same shared identity, so the
        # harness compiles the real resolver rather than a stand-in.
        shared_identity = extract_type(shared, "enum V3SharedAppGroup {")
        # The lock reports its failures through the real typed taxonomy, so the
        # harness compiles that instead of a stub: a stub would let the lock's
        # reporting drift without this test noticing.
        handoff_lock = handoff_slices.lock(handoff)
        typed_handoff = handoff_slices.without_policy(handoff)
        source = "\n".join([
            "import Foundation", "import Darwin",
            typed_handoff, shared_identity, embedded_lock, handoff_lock, fixture,
        ])
        cls.temp = tempfile.TemporaryDirectory(prefix="lc-process-lock-")
        cls.addClassCleanup(cls.temp.cleanup)
        swift = Path(cls.temp.name) / "ProcessLockTests.swift"
        swift.write_text(source, encoding="utf-8")
        cls.executable = Path(cls.temp.name) / "process-lock-tests"
        result = subprocess.run([compiler, "-swift-version", "5", "-parse-as-library", str(swift),
                                "-o", str(cls.executable)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def test_production_lock_helpers_serialize_across_processes(self):
        result = subprocess.run([str(self.executable)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("KEYCHAIN_PROCESS_LOCK_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
