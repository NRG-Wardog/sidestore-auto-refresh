"""One owner for each CI lane; native and portable coverage remain distinct."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / '.github/workflows'


class CIOwnershipTests(unittest.TestCase):
    def test_source_integrity_runs_once_before_native_lane(self):
        text = (WORKFLOWS/'source-integrity-contract.yml').read_text()
        self.assertEqual(text.count('run: python3 -B ci/source-integrity/verify.py'), 1)
        self.assertEqual(text.count('unittest discover -s ci/source-integrity'), 1)
        self.assertIn('needs: source-integrity', text)
        self.assertIn('runs-on: macos-26', text)
        self.assertIn('xcrun --find clang', text)

    def test_current_entry_and_failure_controls_have_one_owner(self):
        text = (WORKFLOWS/'source-integrity-contract.yml').read_text()
        for suite in ("-s ci/current-build -p 'test_*.py'", 'test_workflow_failure_collection.py',
                      "-s ci/cold-vpn -p 'test_*.py'", 'test_same_run_size.py'):
            self.assertEqual(text.count(suite), 1, suite)
        for retired in ('current-build-contract.yml', 'maintained-packaging-contract.yml'):
            self.assertFalse((WORKFLOWS/retired).exists())

    def test_packaging_keeps_both_platforms_without_duplicate_workflows(self):
        text = (WORKFLOWS/'source-integrity-contract.yml').read_text()
        linux, macos = text.split('  bounded-contracts:', 1)
        for suite in ('test_packaging_adapters.py', 'test_combined_packaging.py'):
            self.assertEqual(linux.count(suite), 1)
            self.assertEqual(macos.count(suite), 1)
        for suite in ('test_dylibify_source_build.py', 'test_ipa_size_report.py'):
            self.assertEqual(macos.count(suite), 1)
        self.assertIn('scripts/run_required_tests.py', linux)
        custom = (WORKFLOWS/'custom-integration-contract.yml').read_text()
        command = next(line for line in custom.splitlines() if 'for suite in' in line)
        self.assertNotIn('test_packaging_adapters.py', command)
        self.assertNotIn('test_combined_packaging.py', command)
        self.assertIn('test_integration_contracts.py', command)
        self.assertIn('test_custom_integration_boundaries.py', command)

    def test_feature_branch_uses_pr_not_duplicate_push(self):
        text = (WORKFLOWS/'custom-integration-contract.yml').read_text()
        self.assertIn('  pull_request:', text)
        self.assertIn('  push:\n    branches: [main]\n', text)
        self.assertIn('  workflow_dispatch:', text)


if __name__ == '__main__':
    unittest.main()
