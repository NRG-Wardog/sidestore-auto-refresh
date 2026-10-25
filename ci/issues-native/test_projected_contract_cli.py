"""Exercise real entrypoints, not just validators' importable run functions."""
import subprocess
import contextlib
from unittest.mock import patch
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

    def test_real_nested_extractors_read_relative_contract_from_different_cwd(self):
        import test_comprehensive_diagnostics_contract as comp_controls
        import test_setup_state_recovery_contract as setup_controls
        real_run = subprocess.run
        for module, target in ((comp_controls, 'extract_deferred_baseline.py'),
                               (setup_controls, 'extract-baseline')):
            with self.subTest(target=target):
                control = module.Controls()
                control.setUp()
                self.addCleanup(control.doCleanups)
                selected = control.contract if module is comp_controls else control.contract_path
                observed = []
                def process(argv, **kwargs):
                    if any(str(arg).endswith(target) for arg in argv):
                        contract_arg = Path(argv[argv.index('--contract') + 1])
                        self.assertTrue(contract_arg.is_absolute())
                        self.assertEqual(contract_arg, selected.resolve())
                        self.assertEqual(Path(kwargs['cwd']), control.output)
                        result = real_run(argv, **kwargs)
                        observed.append(result)
                        return result
                    callback = control.control_process if module is comp_controls else control.subprocess
                    return callback(argv, **kwargs)
                with contextlib.chdir(control.root):
                    with patch.object(module.gate.subprocess, 'run', side_effect=process):
                        with self.assertRaisesRegex(ValueError, '[Cc]ommand failed'):
                            module.gate.run(control.sources, control.swiftc, control.output, Path(selected.name))
                self.assertEqual(len(observed), 1)
                # The real nested script reads the selected valid contract and
                # reaches the intentionally absent local-Git boundary, without
                # compiling or running any Swift or requiring external history.
                output = observed[0].stdout.decode()
                self.assertIn('Missing exact local Git checkout', output)
                self.assertNotIn('FileNotFoundError', output)


if __name__ == '__main__':
    unittest.main(verbosity=2)
