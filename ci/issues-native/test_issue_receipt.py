import copy
import json
from pathlib import Path
import tempfile
import unittest
from run_issue_tests import SUITES, HARNESS_SUITES, verify_issue_receipt


class IssueReceiptTests(unittest.TestCase):
    def test_exact_pass_and_negative_controls(self):
        value = {'suites': [{'owner': o, 'suite': d + '/' + p, 'status': 'PASS_NO_SKIPS'} for o, d, p in SUITES],
                 'harness_suites': list(HARNESS_SUITES), 'registry_runtime_double': 'PASS',
                 'signer_policy_and_production_body_double': 'PASS', 'target_device_execution': False,
                 'all_suites_passed_no_skips': True}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'receipt.json'
            path.write_text(json.dumps(value))
            self.assertEqual(verify_issue_receipt(path), value)
            for key, bad in [('suites', value['suites'][:-1]), ('harness_suites', []),
                             ('registry_runtime_double', 'SKIP'), ('signer_policy_and_production_body_double', 'FAIL'),
                             ('target_device_execution', True), ('all_suites_passed_no_skips', 1)]:
                with self.subTest(key=key):
                    changed = copy.deepcopy(value)
                    changed[key] = bad
                    path.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        verify_issue_receipt(path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
