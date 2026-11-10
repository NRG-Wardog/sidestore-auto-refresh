"""One owner for each CI lane; native and portable coverage remain distinct."""

import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/build/contracts'))
from pathlib import Path as _CIPath
from pathlib import Path
import unittest
import json

ROOT = (_CIPath(__file__).resolve().parents[3])
WORKFLOWS = ROOT / '.github/workflows'


class CIOwnershipTests(unittest.TestCase):
    def test_source_integrity_runs_once_before_native_lane(self):
        text = (WORKFLOWS/'source-integrity-contract.yml').read_text()
        self.assertEqual(text.count('run: python3 -B ci/contracts/integrity/verify.py'), 1)
        self.assertEqual(text.count('unittest discover -s tests/ci/integrity'), 1)
        self.assertIn('needs: source-integrity', text)
        self.assertIn('runs-on: macos-26', text)
        self.assertIn('xcrun --find clang', text)

    def test_current_entry_and_failure_controls_have_one_owner(self):
        text = (WORKFLOWS/'source-integrity-contract.yml').read_text()
        for suite in ("-s tests/ci/build -p 'test_*.py'", 'test_workflow_failure_collection.py',
                      "-s tests/ci/vpn -p 'test_*.py'", 'test_same_run_size.py'):
            self.assertEqual(text.count(suite), 1, suite)
        for retired in ('current-build-contract.yml', 'maintained-packaging-contract.yml'):
            self.assertFalse((WORKFLOWS/retired).exists())

    def test_packaging_keeps_both_platforms_without_duplicate_workflows(self):
        text = (WORKFLOWS/'source-integrity-contract.yml').read_text()
        linux, macos = text.split('  bounded-contracts:', 1)
        inventory = json.loads((ROOT/'ci/contracts/product-tests.json').read_text())['files']
        for suite in ('test_packaging_adapters.py', 'test_combined_packaging.py'):
            self.assertEqual(linux.count(suite), 1)
            self.assertEqual(inventory.count('tests/'+suite), 1)
        for suite in ('test_dylibify_source_build.py', 'test_ipa_size_report.py'):
            self.assertEqual(inventory.count('tests/'+suite), 1)
        self.assertEqual(macos.count("--start-directory tests --pattern 'test_*.py'"), 1)
        self.assertIn('scripts/run_required_tests.py', linux)
        self.assertIn('ci/contracts/verify_product_tests.py', macos)
        custom = (WORKFLOWS/'custom-integration-contract.yml').read_text()
        command = next(line for line in custom.splitlines() if 'for suite in' in line)
        self.assertNotIn('test_packaging_adapters.py', command)
        self.assertNotIn('test_combined_packaging.py', command)
        self.assertIn('test_integration_contracts.py', command)
        self.assertIn('test_custom_integration_boundaries.py', command)

    def test_product_regressions_acquire_all_current_owners_without_skips(self):
        text = (WORKFLOWS/'source-integrity-contract.yml').read_text()
        inventory = json.loads((ROOT/'ci/contracts/product-tests.json').read_text())['files']
        self.assertIn('tests/authentication/test_lc_certificate_observation.py', inventory)
        self.assertLess(text.index('ci/build/record.py acquire'), text.index('ci/contracts/verify_product_tests.py'))
        self.assertLess(text.index('ci/build/verify_candidate_graph.py'), text.index('ci/contracts/verify_product_tests.py'))
        for owner in ('LIVE_CONTAINER', 'SIDESTORE', 'SIDESIGN', 'MINIMUXER', 'IDEVICE', 'ANISETTEKIT'):
            self.assertIn('MAINTAINED_'+owner+'_TEST_SOURCE=', text)
        self.assertIn('REQUIRE_LC_CERTIFICATE_NATIVE_TESTS=1', text)
        self.assertNotIn('LC_CERTIFICATE_OBSERVATION_PATCHER', text)
        self.assertIn('--allowlist scripts/required_test_skip_allowlist.json', text)
        self.assertEqual(json.loads((ROOT/'scripts/required_test_skip_allowlist.json').read_text()), [])
        self.assertIn('product-test-timings.json', text)
        self.assertIn("if: ${{ !cancelled() }}", text)

    def test_historical_exclusion_inventory_never_replaces_current_owner(self):
        text = (WORKFLOWS/'source-integrity-contract.yml').read_text()
        block = text.split('      - name: Acquire immutable removed-UI inventory', 1)[1].split('      - name:', 1)[0]
        self.assertIn('historical_pin=ff25922e5c13ccfafd83bda5092910d848ebd409', block)
        self.assertIn('fetch --no-tags origin "$historical_pin"', block)
        self.assertNotIn('checkout', block)
        self.assertEqual(block.count('test "$(git -C sources/SideStore rev-parse HEAD)" = "$current_pin"'), 2)
        self.assertIn('removed-ui-inventory-identity.txt', block)
        self.assertNotIn('EMBEDDED_SIDESTORE_TEST_SOURCE', text)

    def test_feature_branch_uses_pr_not_duplicate_push(self):
        text = (WORKFLOWS/'custom-integration-contract.yml').read_text()
        self.assertIn('  pull_request:', text)
        self.assertIn('  push:\n    branches: [main]\n', text)
        self.assertIn('  workflow_dispatch:', text)


if __name__ == '__main__':
    unittest.main()
