import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "required_test_runner", ROOT / "scripts" / "run_required_tests.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class RequiredSkipGateTests(unittest.TestCase):
    def test_exact_allowlisted_test_id_and_reason_is_accepted(self):
        record = ("test_module.Case.test_known_platform_skip", "requires macOS")
        self.assertEqual([], runner.unexpected_skips([record], {record}))

    def test_unlisted_skip_is_rejected_when_allowlist_is_empty(self):
        record = ("test_module.Case.test_swift_behavior", "swiftc unavailable")
        self.assertEqual([record], runner.unexpected_skips([record], set()))

    def test_allowlisting_test_id_does_not_allow_a_different_reason(self):
        actual = ("test_module.Case.test_source_behavior", "pinned source missing")
        allowed = {("test_module.Case.test_source_behavior", "requires macOS")}
        self.assertEqual([actual], runner.unexpected_skips([actual], allowed))

    def test_allowlisting_reason_does_not_allow_a_different_test_id(self):
        actual = ("test_module.Case.test_other_behavior", "requires macOS")
        allowed = {("test_module.Case.test_known_platform_skip", "requires macOS")}
        self.assertEqual([actual], runner.unexpected_skips([actual], allowed))

    def test_allowlist_schema_is_exact_and_starts_empty(self):
        self.assertEqual(set(), runner.load_allowlist(ROOT / "scripts" / "required_test_skip_allowlist.json"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "allowlist.json"
            path.write_text('[{"test_id":"x","reason":"y","wildcard":"*"}]', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must contain non-empty test_id and reason"):
                runner.load_allowlist(path)

    def test_runner_accepts_synthetic_skip_only_when_exactly_allowlisted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_synthetic.py").write_text(
                "import unittest\n"
                "class Synthetic(unittest.TestCase):\n"
                "    def test_platform_case(self):\n"
                "        self.skipTest('requires synthetic platform')\n",
                encoding="utf-8")
            allowlist = root / "allowlist.json"
            allowlist.write_text(json.dumps([{
                "test_id": "test_synthetic.Synthetic.test_platform_case",
                "reason": "requires synthetic platform",
            }]), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts" / "run_required_tests.py"),
                "--start-directory", directory, "--top-level-directory", directory,
                "--allowlist", str(allowlist), "--verbosity", "2",
            ], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("observed=1 allowlisted=1 unexpected=0", result.stdout)

    def test_runner_rejects_synthetic_skip_with_empty_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_synthetic.py").write_text(
                "import unittest\n"
                "class Synthetic(unittest.TestCase):\n"
                "    def test_swift_case(self):\n"
                "        self.skipTest('swiftc unavailable')\n",
                encoding="utf-8")
            allowlist = root / "allowlist.json"
            allowlist.write_text("[]", encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts" / "run_required_tests.py"),
                "--start-directory", directory, "--top-level-directory", directory,
                "--allowlist", str(allowlist), "--verbosity", "2",
            ], capture_output=True, text=True)
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertIn("UNEXPECTED SKIP: test_synthetic.Synthetic.test_swift_case :: swiftc unavailable",
                          result.stderr)

    def test_runner_rejects_empty_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowlist = root / "allowlist.json"
            allowlist.write_text("[]", encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts" / "run_required_tests.py"),
                "--start-directory", directory, "--top-level-directory", directory,
                "--allowlist", str(allowlist), "--verbosity", "2",
            ], capture_output=True, text=True)
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertIn("required test runner discovered zero tests", result.stderr)


if __name__ == "__main__":
    unittest.main()
