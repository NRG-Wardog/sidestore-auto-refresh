"""Current native-size orchestration controls, not Apple runtime qualification."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ci/issues-native'))
sys.path.insert(0, str(ROOT / 'scripts'))
import package_candidate as packaging
import optimize_openssl_local_candidate as adapter

spec = importlib.util.spec_from_file_location('same_run_qualification', Path(__file__).with_name('qualify_host.py'))
qualify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualify)

class SameRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(strict=True)
        self.current = {'workflow_commit': 'a'*40, 'run_id': '123', 'run_attempt': '1'}

    def test_recovery_and_same_run_are_mutually_exclusive_before_io(self):
        with patch.object(packaging.subprocess, 'run') as execute:
            with self.assertRaisesRegex(ValueError, 'cannot use recovery'):
                packaging.main(recover_fixed_native=True, same_run_qualified=True)
            execute.assert_not_called()

    def test_same_run_requires_complete_reviewed_pipeline(self):
        with self.assertRaisesRegex(ValueError, 'complete reviewed size pipeline'):
            packaging.main(same_run_qualified=True)

    def test_unknown_and_unbound_receipts_fail_before_loading_qualifier(self):
        path = self.root / 'receipt.json'
        path.write_text('{"schema":"unknown"}')
        with patch.object(adapter.importlib.util, 'spec_from_file_location') as loader:
            with self.assertRaisesRegex(ValueError, 'SHA differs'):
                adapter.verify_host_qualification(path, '0'*64, self.current, self.current)
            with self.assertRaisesRegex(ValueError, 'unknown host qualification schema'):
                adapter.verify_host_qualification(path, adapter.digest(path), self.current, self.current)
            loader.assert_not_called()

    def test_same_run_verifier_rejects_other_run_before_support_reads(self):
        path = self.root / 'receipt.json'
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'same-run native/packaging'):
            qualify.verify_host_local_qualification(path, qualify.sha(path), self.current,
                {**self.current, 'run_id': '456'})

    def test_same_run_json_duplicate_keys_fail_closed(self):
        path = self.root / 'receipt.json'
        path.write_text('{"schema":"good","schema":"other"}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            qualify.read(path)

    def test_current_source_closure_includes_new_workflow_and_adapters(self):
        paths = set(qualify.sources())
        self.assertTrue({'ci/issue56-startup/qualify_host.py',
                         '.github/workflows/issue56-native.yml',
                         'ci/issues-native/package_candidate.py',
                         'scripts/optimize_openssl_local_candidate.py'} <= paths)
        self.assertNotIn('ci/issue55-openssl-local/qualify_host.py', paths)
        self.assertNotIn('.github/workflows/openssl-local-host-diagnostic.yml', paths)

    def test_actual_cli_from_unrelated_working_directory(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT/'ci/issue56-startup/qualify_host.py'), '--help'],
            cwd=self.root, text=True, capture_output=True, check=True)
        for option in ('--built', '--inputs', '--evidence', '--work'):
            self.assertIn(option, result.stdout)

    def test_workflow_fresh_native_qualification_precedes_success_only_package(self):
        workflow = (ROOT/'.github/workflows/issue56-native.yml').read_text()
        self.assertIn("branches: ['validation/issue56-startup']", workflow)
        self.assertIn("github.ref == 'refs/heads/validation/issue56-startup'", workflow)
        self.assertIn('python3 harness/ci/issues-native/record.py acquire', workflow)
        self.assertNotIn('--recover-fixed-native', workflow)
        self.assertNotIn('recover_native_artifacts.py', workflow)
        self.assertNotIn('continue-on-error:', workflow)
        self.assertEqual(workflow.count('package_candidate.py --same-run-qualified-size'), 1)
        self.assertLess(workflow.index('ci/issue56-startup/qualify_host.py --built'),
                        workflow.index('package_candidate.py --same-run-qualified-size'))
        self.assertIn('OPENSSL_LOCAL_HOST_QUALIFICATION_SHA256=', workflow)
        self.assertIn('not result.wasSuccessful() or result.skipped', workflow)
        self.assertIn("sys.platform != 'darwin'", workflow)
        self.assertNotIn('zopfli', workflow.lower())  # Compression requires a completed green native run.
        for name in ('GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
            self.assertNotIn('export '+name+'=', workflow)
        block = workflow.split('      - name: Retain original widget and matching debug symbols separately\n')[1].split('      - name:')[0]
        self.assertIn('if: success()', block)
        self.assertIn('path: widget-diagnostics/', block)

    def test_all_literal_shell_blocks_parse_without_execution(self):
        # Extract exact YAML block indentation, not reconstructed command strings.
        lines = (ROOT/'.github/workflows/issue56-native.yml').read_text().splitlines()
        scripts = []
        for index, line in enumerate(lines):
            if line == '        run: |':
                body=[]
                for candidate in lines[index+1:]:
                    if candidate.strip() and not candidate.startswith('          '): break
                    body.append(candidate[10:] if candidate.startswith('          ') else '')
                scripts.append('\n'.join(body)+'\n')
        self.assertGreater(len(scripts), 20)
        for index, script in enumerate(scripts):
            result = subprocess.run(['bash', '-n'], input=script, text=True, capture_output=True, cwd=self.root)
            self.assertEqual(result.returncode, 0, f'block {index}: {result.stderr}')

if __name__ == '__main__': unittest.main(verbosity=2)
