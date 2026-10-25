"""Exercise real entrypoints, not just validators' importable run functions."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class ProjectedContractCLITests(unittest.TestCase):
    def test_both_entrypoints_advertise_contract(self):
        for script in ('verify_comprehensive_diagnostics.py', 'verify_setup_state_recovery.py'):
            with self.subTest(script=script):
                result = subprocess.run([sys.executable, '-B', str(HERE / script), '--help'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--contract', result.stdout)

    def test_both_real_cli_paths_load_selected_contract_before_sources_or_swift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = root / 'projected-contract-sentinel.json'
            selected.write_text('{}')
            for script, prefix, failure in (
                ('verify_comprehensive_diagnostics.py', [], 'contract schema'),
                ('verify_setup_state_recovery.py', ['run'], 'contract schema')):
                with self.subTest(script=script):
                    result = subprocess.run([sys.executable, '-B', str(HERE / script), *prefix,
                        '--sources', str(root / 'absent-sources'), '--swiftc', str(root / 'never-execute-swift'),
                        '--output', str(root / 'output'), '--contract', str(selected)], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotEqual(result.returncode, 2, result.stderr)
                    self.assertIn(failure, result.stderr.lower())
                    self.assertNotIn('unrecognized arguments', result.stderr)
                    self.assertFalse((root / 'output').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
