"""Mirror transport must never relax source identity or cleanliness."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pinned", ROOT / "scripts/verify_pinned_sources.py")
pinned = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pinned)


class PinnedSourceIdentityTests(unittest.TestCase):
    def test_exact_commit_tree_and_clean_source(self):
        with patch.object(pinned, "git", side_effect=["a" * 40, "b" * 40, "", ""]):
            result = pinned.verify_checkout("source", "a" * 40, "b" * 40)
        self.assertEqual(result["commit"], "a" * 40)

    def test_wrong_commit_is_not_accepted(self):
        with patch.object(pinned, "git", return_value="c" * 40):
            with self.assertRaisesRegex(ValueError, "unexpected commit"):
                pinned.verify_checkout("source", "a" * 40)

    def test_wrong_tree_is_not_accepted(self):
        with patch.object(pinned, "git", side_effect=["a" * 40, "c" * 40]):
            with self.assertRaisesRegex(ValueError, "unexpected tree"):
                pinned.verify_checkout("source", "a" * 40, "b" * 40)

    def test_changed_or_extra_source_is_not_accepted(self):
        for dirty in [" M source.swift", "?? injected.swift", "M  staged.swift"]:
            with self.subTest(dirty=dirty), patch.object(pinned, "git", side_effect=["a" * 40, "b" * 40, dirty]):
                with self.assertRaisesRegex(ValueError, "not pristine"):
                    pinned.verify_checkout("source", "a" * 40)

    def test_missing_submodule_is_not_resolved_as_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            values = ["a" * 40, "b" * 40, "", "160000 commit " + "c" * 40 + "\tchild"]
            with patch.object(pinned, "git", side_effect=values):
                with self.assertRaisesRegex(ValueError, "submodule is missing"):
                    pinned.verify_checkout(directory, "a" * 40)

    def test_recursive_child_commit_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            child = Path(directory) / "child"
            child.mkdir()
            (child / ".git").write_text("gitdir: test")
            values = ["a" * 40, "b" * 40, "", "160000 commit " + "c" * 40 + "\tchild", "d" * 40]
            with patch.object(pinned, "git", side_effect=values):
                with self.assertRaisesRegex(ValueError, "unexpected commit"):
                    pinned.verify_checkout(directory, "a" * 40)

    def test_current_entry_uses_explicit_committed_graph_and_receipt_gate(self):
        workflow = (ROOT / '.github/workflows/cold-vpn-native.yml').read_text()
        self.assertIn('permissions:\n  contents: read', workflow)
        self.assertIn('ci/current-build/source_selection.py --graph harness/ci/issues-native/candidate-graph.json', workflow)
        self.assertIn('verify_candidate_graph.py', workflow)
        self.assertIn('record.py receipt-current', workflow)
        self.assertIn('ref: ${{ github.sha }}', workflow)
        self.assertNotIn('contents: write', workflow)



if __name__ == "__main__":
    unittest.main()
