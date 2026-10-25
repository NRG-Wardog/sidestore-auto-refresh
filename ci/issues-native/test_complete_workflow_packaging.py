"""Executable controls for complete-candidate workflow wiring, not native proof."""
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

from test_workflow_failure_collection import WORKFLOW, steps, simulate, FINAL


def block(label):
    text = WORKFLOW.read_text()
    start = text.index("<<'" + label + "'")
    start = text.index('\n', start) + 1
    end = text.index('          ' + label + '\n', start)
    return textwrap.dedent(text[start:end])


class CompletePackagingWorkflowTests(unittest.TestCase):
    def test_complete_branch_only_and_size_controls_required(self):
        text, rows = steps()
        self.assertIn("branches: ['integration/complete-issues-oct25']", text)
        self.assertIn("github.ref == 'refs/heads/integration/complete-issues-oct25'", text)
        self.assertNotIn("refs/heads/integration/issues-oct25'", text)
        size = next(row for row in rows if row['name'] == 'Verify same-run size optimizer controls without skips')
        self.assertEqual(size['condition'], 'success()')
        for name in ('test_macho_stab_guard.py','test_experiment_strip_stabs.py','test_native_size_optimization.py',
                     'test_combined_packaging.py','test_ipa_size_report.py',
                     'test_macho_local_symbol_guard.py','test_experiment_local_symbols.py',
                     'test_local_symbol_packaging.py','test_local_symbol_runtime_keep.py',
                     'test_sidebackup_stabs.py','test_sidebackup_same_run.py'):
            self.assertIn(name, size['text'])
        self.assertIn('tests.countTestCases() == 0', size['text'])
        self.assertIn('not result.wasSuccessful() or result.skipped', size['text'])
        self.assertLess(rows.index(size), next(i for i,row in enumerate(rows) if row['name'].startswith('Package same-run')))

    def test_same_run_packager_explicitly_enables_qualified_local_stage(self):
        tree = ast.parse(Path(__file__).with_name('package_candidate.py').read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'optimize_same_run_ipa']
        self.assertEqual(len(calls), 1)
        keywords = {entry.arg: entry.value for entry in calls[0].keywords}
        self.assertIsInstance(keywords['include_local_symbols'], ast.Constant)
        self.assertIs(keywords['include_local_symbols'].value, True)
        self.assertIn('generated_openssl', keywords)
        self.assertIn('run_identity', keywords)
        self.assertIsInstance(keywords['expected_transfer_manifests'], ast.Name)
        self.assertEqual(keywords['expected_transfer_manifests'].id, 'sealed_manifests')

    def test_three_stage_packaging_uses_earlier_trusted_manifest_seal(self):
        source = Path(__file__).with_name('package_candidate.py').read_text()
        tree = ast.parse(source)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        seals = [node for node in calls if node.func.id == 'verify_transfer_seal']
        self.assertEqual(len(seals), 2)
        for call in seals:
            values = {entry.arg: entry.value for entry in call.keywords}
            self.assertEqual(values['expected_seal_sha'].id, 'seal_sha')
        third = [node for node in calls if node.func.id == 'optimize_sidebackup_same_run']
        self.assertEqual(len(third), 1)
        values = {entry.arg: entry.value for entry in third[0].keywords}
        self.assertEqual(ast.unparse(values['expected_manifest_sha']), "sealed_manifests['SideStore']")
        self.assertIn("os.environ['NATIVE_TRANSFER_SEAL_SHA256']", source)
        self.assertLess(seals[0].lineno, third[0].lineno)
        self.assertGreater(seals[1].lineno, third[0].lineno)
        _, rows = steps()
        retained = next(row for row in rows if row['name'] == 'Retain exact unsigned app products for diagnostic packaging')
        self.assertIn('from native_transfer_seal import create as seal_native_transfers', retained['text'])
        self.assertIn("environment.write('NATIVE_TRANSFER_SEAL_SHA256=' + seal_sha", retained['text'])
        self.assertLess(retained['text'].index("(transfer / 'retained-files.json').write_text"),
                        retained['text'].index('seal_sha = seal_native_transfers'))

    def test_retained_diagnostics_only_success_and_failures_never_publish(self):
        _, rows = steps()
        row = next(row for row in rows if row['name'] == 'Retain original IPA and matching debug symbols separately')
        self.assertEqual(row['condition'], 'success()')
        self.assertIn('path: size-diagnostics/', row['text'])
        for failure in ('coverage_settings', 'Verify same-run size optimizer controls without skips',
                        'Package same-run reviewed candidate without public release'):
            states, failed = simulate(rows, (failure,))
            self.assertTrue(failed)
            self.assertEqual(states[row['name']], 'skipped')
            self.assertEqual(states['Upload experimental candidate IPA and provenance'], 'skipped')
            if failure == 'coverage_settings':
                self.assertEqual(states['livecontainer_build'], 'success')
                self.assertEqual(states['sidestore_build'], 'success')
        self.assertNotIn('cp -R package-evidence', WORKFLOW.read_text())

    def test_actual_failure_collector_only_regular_json_logs_from_alternate_alias_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            alias = root/'alias'; actual = root/'actual'; actual.mkdir(); alias.symlink_to(actual, target_is_directory=True)
            source = alias/'package-evidence'; source.mkdir()
            for name in ('partial.ipa','symbols.zip','tool.py','secret.txt','valid.json','tool.log'):
                (source/name).write_bytes(b'controlled fixture')
            (root/'outside.json').write_bytes(b'not allowed')
            (source/'linked.json').symlink_to(root/'outside.json')
            (source/'nested').mkdir(); (source/'nested/check.log').write_bytes(b'nested')
            result = subprocess.run([sys.executable, '-c', block('PY_PACKAGE_EVIDENCE')], cwd=alias,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            target = alias/'evidence/candidate-package'
            self.assertEqual({p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()},
                             {'valid.json','tool.log','nested/check.log'})
            self.assertTrue(target.samefile(actual/'evidence/candidate-package'))

    def test_actual_exit_trap_preserves_failed_exit_and_does_not_upload_binaries(self):
        text = WORKFLOW.read_text()
        start = text.index('          collect_package_evidence() {')
        end = text.index('          trap collect_package_evidence EXIT', start) + len('          trap collect_package_evidence EXIT')
        shell = textwrap.dedent(text[start:end])
        for status in (0,7):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); (root/'package-evidence').mkdir()
                (root/'package-evidence/partial.ipa').write_bytes(b'not publishable')
                result = subprocess.run(['bash','-c', 'set -euo pipefail\n'+shell+'\nexit '+str(status)],
                                        cwd=root,capture_output=True,text=True)
                self.assertEqual(result.returncode,status,result.stderr)
                target=root/'evidence/candidate-package'
                self.assertEqual(json.loads((target/'package.exit-code.json').read_text()),{'exit_code':status})
                self.assertFalse(list(target.rglob('*.ipa')))

    def test_effective_coverage_receipt_whitelists_and_requires_release_no(self):
        _, rows = steps()
        probe = next(row for row in rows if row.get('id') == 'coverage_settings')
        self.assertIn('-showBuildSettings -json', probe['text'])
        self.assertIn('release_coverage.py settings --require-support-size', probe['text'])
        self.assertIn('release_coverage.py logs --require-support-size', WORKFLOW.read_text())
        self.assertNotRegex(probe['text'], r'(?:COVERAGE|PROGRAM_FLOW_ARCS|TEST_COVERAGE_FILES)=(?:YES|NO)')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = {'CONFIGURATION': 'Release', 'ENABLE_CODE_COVERAGE': 'NO',
                        'OTHER_SECRET_SETTING': 'must not appear', 'ENABLE_TESTABILITY': 'NO'}
            files = [root/'LC.json', root/'SS.json']
            for path in files:
                path.write_text(json.dumps([{'target': 'App', 'buildSettings': settings}]))
            env = {**os.environ, 'GITHUB_SHA': 'test', 'GITHUB_RUN_ID': '17', 'GITHUB_RUN_ATTEMPT': '2'}
            command = [sys.executable, str(Path(__file__).with_name('release_coverage.py')), 'settings',
                       '--inputs', *map(str, files), '--output', str(root/'receipt.json')]
            result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root/'receipt.json').read_text())
            self.assertFalse(report['debug_test_configuration_changed'])
            self.assertNotIn('OTHER_SECRET_SETTING', json.dumps(report))
            self.assertEqual(set(report['projects']), {'LiveContainer', 'SideStore'})
            for rows in report['projects'].values():
                self.assertEqual(rows[0]['settings']['ENABLE_CODE_COVERAGE'], 'NO')
                self.assertEqual(len(rows[0]['settings']), 8)
            settings['ENABLE_CODE_COVERAGE'] = 'YES'
            files[1].write_text(json.dumps([{'target': 'App', 'buildSettings': settings}]))
            failed = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)


if __name__ == '__main__': unittest.main(verbosity=2)
