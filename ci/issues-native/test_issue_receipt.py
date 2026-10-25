import copy
import json
from pathlib import Path
import tempfile
import unittest
from run_issue_tests import (SUITES, HARNESS_SUITES, verify_issue_receipt, OWNED_INPUTS,
    OWNED_SWIFT_SUITES, owned_expected_graph, verify_owned_receipt, run_owned_swift_suites)


def test_owned_graph():
    return {o: {'commit': 'b' * 40, 'tree': 'c' * 40} for o in OWNED_INPUTS}


def owned_receipt():
    return {'source_graph': test_owned_graph(),
            'source_sha256': {o + '/' + n: 'a' * 64 for o, names in OWNED_INPUTS.items() for n in names},
            'swift_suites': [{'owner': o, 'suite': s, 'status': 'PASS', 'executions': count}
                for o, s, _, count, _ in OWNED_SWIFT_SUITES], 'target_device_execution': False}


class IssueReceiptTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        gate = patch('run_issue_tests.owned_expected_graph', return_value=test_owned_graph())
        gate.start(); self.addCleanup(gate.stop)
        provenance = owned_receipt(); provenance.pop('swift_suites'); provenance.pop('target_device_execution')
        inputs = patch('run_issue_tests.collect_owned_sources', return_value=provenance)
        inputs.start(); self.addCleanup(inputs.stop)

    def test_exact_pass_and_negative_controls(self):
        value = {'owned_authentication': owned_receipt(), 'suites': [{'owner': o, 'suite': d + '/' + p, 'status': 'PASS_NO_SKIPS'} for o, d, p in SUITES],
                 'harness_suites': list(HARNESS_SUITES), 'registry_runtime_double': 'PASS',
                 'signer_policy_and_production_body_double': 'PASS', 'target_device_execution': False,
                 'all_suites_passed_no_skips': True}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'receipt.json'
            path.write_text(json.dumps(value))
            self.assertEqual(verify_issue_receipt(path), value)
            for key, bad in [('suites', value['suites'][:-1]), ('harness_suites', []),
                             ('owned_authentication', {}), ('registry_runtime_double', 'SKIP'), ('signer_policy_and_production_body_double', 'FAIL'),
                             ('target_device_execution', True), ('all_suites_passed_no_skips', 1)]:
                with self.subTest(key=key):
                    changed = copy.deepcopy(value)
                    changed[key] = bad
                    path.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        verify_issue_receipt(path)

class OwnedAuthenticationReceiptTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        gate = patch('run_issue_tests.owned_expected_graph', return_value=test_owned_graph())
        gate.start(); self.addCleanup(gate.stop)

    def test_exact_final_source_graph_and_all_inputs_required(self):
        value = owned_receipt()
        self.assertEqual(verify_owned_receipt(value), value)
        invalid = []
        for key in value:
            changed = copy.deepcopy(value); del changed[key]; invalid.append(changed)
        changed = copy.deepcopy(value); changed['source_graph']['SideStore']['commit'] = 'f' * 40; invalid.append(changed)
        changed = copy.deepcopy(value); changed['swift_suites'][0]['executions'] = 9; invalid.append(changed)
        changed = copy.deepcopy(value); changed['swift_suites'][1]['status'] = 'SKIP'; invalid.append(changed)
        changed = copy.deepcopy(value); changed['swift_suites'][1]['executions'] = True; invalid.append(changed)
        changed = copy.deepcopy(value); changed['target_device_execution'] = True; invalid.append(changed)
        changed = copy.deepcopy(value); changed['source_sha256'].pop(next(iter(changed['source_sha256']))); invalid.append(changed)
        changed = copy.deepcopy(value); changed['source_sha256'][next(iter(changed['source_sha256']))] = 'invalid'; invalid.append(changed)
        for changed in invalid:
            with self.assertRaises(ValueError): verify_owned_receipt(changed)

    def test_receipt_must_match_current_committed_inputs(self):
        value = owned_receipt()
        provenance = {k: value[k] for k in ('source_graph', 'source_sha256')}
        self.assertEqual(verify_owned_receipt(value, provenance), value)
        changed = copy.deepcopy(provenance)
        changed['source_sha256'][next(iter(changed['source_sha256']))] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'actual committed inputs'):
            verify_owned_receipt(value, changed)

    def test_each_script_is_bounded_and_required_marker_count_is_exact(self):
        import subprocess
        from unittest.mock import patch
        provenance = owned_receipt(); provenance.pop('swift_suites'); provenance.pop('target_device_execution')
        results = [subprocess.CompletedProcess([], 0, stdout=(marker + ' synthetic test output\n') * count)
                   for _, _, marker, count, _ in OWNED_SWIFT_SUITES]
        with tempfile.TemporaryDirectory() as temp:
            with patch('run_issue_tests.collect_owned_sources', return_value=provenance), \
                 patch('run_issue_tests.subprocess.run', side_effect=results) as run:
                value = run_owned_swift_suites(temp, Path(temp) / 'evidence')
                verify_owned_receipt(value)
                self.assertEqual([c.kwargs['timeout'] for c in run.call_args_list], [300, 120])
            for output in ['', OWNED_SWIFT_SUITES[0][2] + ' incomplete\n']:
                with patch('run_issue_tests.collect_owned_sources', return_value=provenance), \
                     patch('run_issue_tests.subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout=output)):
                    with self.assertRaises(ValueError): run_owned_swift_suites(temp, Path(temp) / 'evidence')
            with patch('run_issue_tests.collect_owned_sources', return_value=provenance), \
                 patch('run_issue_tests.subprocess.run', return_value=subprocess.CompletedProcess([], 1, stdout='compile failed')):
                with self.assertRaises(subprocess.CalledProcessError): run_owned_swift_suites(temp, Path(temp) / 'evidence')

    def test_actual_input_bytes_and_symlink_or_wrong_source_rejected(self):
        import hashlib
        from unittest.mock import patch
        from run_issue_tests import collect_owned_sources
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for owner, names in OWNED_INPUTS.items():
                for name in names:
                    path = root / owner / name; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'synthetic input')
            with patch('run_issue_tests.subprocess.check_output', side_effect=lambda command, **kw: 'b'*40+'\n'+'c'*40+'\n' if 'rev-parse' in command else b'synthetic input'):
                value = collect_owned_sources(root)
                self.assertEqual(set(value['source_sha256'].values()), {hashlib.sha256(b'synthetic input').hexdigest()})
                owner = next(iter(OWNED_INPUTS)); path = root / owner / OWNED_INPUTS[owner][0]
                path.write_bytes(b'dirty fixture before execution')
                with self.assertRaisesRegex(ValueError, 'committed blob'): collect_owned_sources(root)
                path.unlink(); path.symlink_to(root / 'not-present')
                with self.assertRaisesRegex(ValueError, 'symlink'): collect_owned_sources(root)
            with patch('run_issue_tests.subprocess.check_output', return_value='d'*40+'\n'+'c'*40+'\n'):
                with self.assertRaisesRegex(ValueError, 'graph differs'): collect_owned_sources(root)

    def test_missing_compiler_clears_stale_aggregate_success(self):
        import os
        from unittest.mock import patch
        from run_issue_tests import main
        old = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp); Path('evidence').mkdir()
                Path('evidence/issue-tests.json').write_text('stale success')
                with patch('run_issue_tests.shutil.which', return_value=None):
                    with self.assertRaises(SystemExit): main()
                self.assertFalse(Path('evidence/issue-tests.json').exists())
            finally: os.chdir(old)


class NativeIssueReceiptTests(unittest.TestCase):
    def test_real_named_tests_and_zero_skips_required(self):
        from run_issue_tests import REQUIRED_RUST_TESTS, validate_rust_output
        for package, names in REQUIRED_RUST_TESTS.items():
            output = '\n'.join('test module::' + name + ' ... ok' for name in names)
            output += f'\ntest result: ok. {len(names)} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0s\n'
            validate_rust_output(package, output)
            for bad in (output.replace('0 ignored', '1 ignored'), output.replace('0 filtered out', '1 filtered out'),
                        output.replace(names[0], 'not_the_test'), output + output, ''):
                with self.subTest(package=package, bad=bad):
                    with self.assertRaises(ValueError):
                        validate_rust_output(package, bad)

    def test_native_receipt_exact_closure(self):
        from run_issue_tests import RUST_FEATURES, REQUIRED_RUST_TESTS, verify_native_receipt
        value = {'features': RUST_FEATURES, 'required_tests': {k: list(v) for k, v in REQUIRED_RUST_TESTS.items()},
                 'rust_suites': 'PASS_NO_SKIPS', 'generated_header_c_and_cpp': 'PASS',
                 'toolchain': '1.98.1', 'target_device_execution': False}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'native.json'
            path.write_text(json.dumps(value))
            self.assertEqual(verify_native_receipt(path), value)
            for key in value:
                bad = copy.deepcopy(value)
                del bad[key]
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    verify_native_receipt(path)

    def test_native_failed_compile_never_writes_success_receipt(self):
        import os
        import subprocess
        from unittest.mock import patch
        from run_issue_tests import run_idevice_native
        old = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                Path('evidence').mkdir()
                Path('evidence/idevice-issue-tests.json').write_text('stale success')
                failed = subprocess.CompletedProcess([], 1, stdout='compile error')
                with patch('run_issue_tests.subprocess.run', return_value=failed):
                    with self.assertRaises(subprocess.CalledProcessError):
                        run_idevice_native()
                self.assertFalse(Path('evidence/idevice-issue-tests.json').exists())
                self.assertEqual(Path('evidence/idevice-tests.log').read_text(), 'compile error')
            finally:
                os.chdir(old)

    def test_feature_and_workflow_closure(self):
        from run_issue_tests import RUST_FEATURES, SUITES
        self.assertIn('cryptexd', RUST_FEATURES['idevice'].split(','))
        self.assertTrue({'tss', 'tunnel_tcp_stack', 'mobile_image_mounter'} <= set(RUST_FEATURES['idevice-ffi'].split(',')))
        for name in ('ownership', 'listener', 'mss', 'adapter_clone_scope'):
            self.assertIn(('idevice', '.ci/issue53', 'test_' + name + '.py'), SUITES)
        root = Path(__file__).resolve().parents[2]
        workflow = (root / '.github/workflows/issues-native.yml').read_text()
        self.assertIn('python3 -B harness/ci/issues-native/run_issue_tests.py --idevice-native', workflow)
        self.assertIn("verify_native_receipt(E / 'idevice-issue-tests.json')", (root / 'ci/issues-native/record.py').read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
