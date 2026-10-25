"""Source-owned runner controls with synthetic logs/data only; never download or emulate."""
import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch
import run_owned_oda as oda

GRAPH = {'owners': {'AnisetteKit': ['NRG-Wardog/AnisetteKit', 'a' * 40]},
         'reviewed_deltas': {'AnisetteKit': {'tree': 'b' * 40}}}
NATIVE = ('PASS: control, filesystem, pinned-header syntax, actual C ABI with mock engine\n'
          'PASS: pinned real Unicorn synthetic ARM64 procedures\n')
SWIFT = ''.join("Test Case '-[AnisetteKitTests.OwnedOnDeviceAnisetteAttemptTests " + test + "]' passed (0.01 seconds).\n" for test in oda.SWIFT_TESTS)


def historical():
    return {'schema': 'owned-oda-historical-engine-v1', 'source': oda.source_identity(GRAPH), 'unicorn_archive_sha256': oda.UNICORN_SHA,
            'engine_library_sha256_observed': 'd' * 64, 'fixture_sha256': {'android_tls': 'e' * 64, 'constructor_diagnostics': 'f' * 64},
            'checks': {'android_tls': 83, 'constructor_diagnostics': 2105}, 'ADI_executed': False, 'device_validation': False, 'all_passed': True}


def startup():
    return {'passed': True, 'unicorn_sha256': oda.UNICORN_SHA,
            'verified_inputs': {'recognized_libraries': copy.deepcopy(oda.LIBRARIES), 'archive_sha256_observed': 'c' * 64},
            'commands': [{'returncode': 0, 'stdout': '', 'stderr': ''}, {'returncode': 0, 'stdout': '', 'stderr': ''},
                         {'returncode': 0, 'stdout': json.dumps(dict.fromkeys(oda.STARTUP_FLAGS, 1)), 'stderr': ''}]}


class OwnedODAControls(unittest.TestCase):
    def test_requires_real_native_marker_and_rejects_skips(self):
        oda.validate_log('native', NATIVE)
        for log in (NATIVE.splitlines()[0], NATIVE + 'NOT RUN: real engine', NATIVE + NATIVE):
            with self.assertRaises(ValueError): oda.validate_log('native', log)

    def test_exact_eight_swift_names_no_zero_or_duplicate_green(self):
        oda.validate_log('swift', SWIFT)
        for log in ('Executed 0 tests', SWIFT + SWIFT.splitlines()[0] + '\n', '\n'.join(SWIFT.splitlines()[1:]), SWIFT.replace(oda.SWIFT_TESTS[0], oda.SWIFT_TESTS[0] + 'Suffix')):
            with self.assertRaises(ValueError): oda.validate_log('swift', log)

    def test_historical_real_engine_requires_both_finite_fixtures(self):
        oda.validate_log('historical_engine', json.dumps(historical()))
        for mutation in ('empty', 'tls', 'constructor', 'skipped'):
            value = historical()
            if mutation == 'empty': value['checks'] = {}
            if mutation == 'tls': value['checks']['android_tls'] = 82
            if mutation == 'constructor': value['checks']['constructor_diagnostics'] = 2104
            if mutation == 'skipped': value['all_passed'] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): oda.validate_log('historical_engine', json.dumps(value))

    def test_historical_count_and_no_skip_are_required(self):
        for name, count in [('legacy', 9), ('logging', 13)]:
            oda.validate_log(name, f'Ran {count} tests in 0.1s\n\nOK\n')
            for text in (f'Ran {count-1} tests in 0.1s\nOK\n', f'Ran {count} tests in 0.1s\nOK (skipped=1)\n'):
                with self.assertRaises(ValueError): oda.validate_log(name, text)

    def test_startup_requires_all_flags_known_hashes_and_executed_commands(self):
        oda.validate_startup(startup())
        for mutation in ('flag', 'hash', 'status', 'stderr', 'missing'):
            value = startup()
            if mutation == 'flag': value['commands'][-1]['stdout'] = json.dumps(dict.fromkeys(oda.STARTUP_FLAGS, 0))
            if mutation == 'hash': value['verified_inputs']['recognized_libraries']['libCoreADI.so']['sha256'] = '0' * 64
            if mutation == 'status': value['commands'][1]['returncode'] = 1
            if mutation == 'stderr': value['commands'][-1]['stderr'] = 'unexpected output'
            if mutation == 'missing': value['commands'].pop()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): oda.validate_startup(value)

    def test_subprocess_failure_does_not_hide_subsequent_suite(self):
        with tempfile.TemporaryDirectory() as temp:
            evidence = Path(temp); rows = {}
            result = types.SimpleNamespace(stdout='fixture failure', returncode=7)
            self.assertFalse(oda.execute('native', ['synthetic'], evidence, evidence, 1, rows, runner=lambda *a, **k: result))
            result = types.SimpleNamespace(stdout='Ran 13 tests in 0.1s\nOK\n', returncode=0)
            self.assertTrue(oda.execute('logging', ['synthetic'], evidence, evidence, 1, rows, runner=lambda *a, **k: result))
            self.assertEqual(rows['native']['returncode'], 7)
            self.assertEqual(rows['logging']['status'], 'PASS')

    def test_timeout_is_failure_with_bounded_receipt(self):
        def timeout(*args, **kwargs): raise subprocess.TimeoutExpired('synthetic', 1, output=b'entered fixture\n')
        with tempfile.TemporaryDirectory() as temp:
            rows = {}; root = Path(temp)
            self.assertFalse(oda.execute('native', ['synthetic'], root, root, 1, rows, runner=timeout))
            self.assertEqual(rows['native']['returncode'], 124)
            self.assertIn('BOUNDED_SUBPROCESS_TIMEOUT', (root / 'native.log').read_text())

    def test_receipt_binds_source_logs_and_startup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); rows = {}
            logs = {'native': NATIVE, 'historical_engine': json.dumps(historical()), 'legacy': 'Ran 9 tests in 0.1s\nOK\n', 'logging': 'Ran 13 tests in 0.1s\nOK\n', 'swift': SWIFT, 'startup': 'synthetic fixture only\n'}
            for name, text in logs.items():
                path = root / (name + '.log'); path.write_text(text)
                rows[name] = {'status': 'PASS', 'returncode': 0, 'log_sha256': oda.digest(path)}
            (root / 'trusted-startup.json').write_text(json.dumps(startup()))
            value = {'schema': 'owned-oda-candidate-v1', 'source': oda.source_identity(GRAPH), 'all_passed': True,
                     'device_or_authentication_proven': False, 'unicorn_sha256': oda.UNICORN_SHA, 'suites': rows,
                     'startup_receipt_sha256': oda.digest(root / 'trusted-startup.json')}
            receipt = root / 'receipt.json'; receipt.write_text(json.dumps(value))
            with patch.object(oda, 'load_candidate_graph', return_value=GRAPH):
                oda.verify_receipt(receipt)
                (root / 'native.log').write_text(NATIVE + 'changed')
                with self.assertRaises(ValueError): oda.verify_receipt(receipt)
                (root / 'native.log').write_text(NATIVE)
                value['source']['commit'] = 'd' * 40; receipt.write_text(json.dumps(value))
                with self.assertRaises(ValueError): oda.verify_receipt(receipt)

    def test_main_collects_independent_suites_and_blocks_startup_after_native_failure(self):
        for dependency_failure in (False, True):
            with self.subTest(dependency_failure=dependency_failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); source = root / 'sources/AnisetteKit'
                tests = source / 'Tests/AnisetteKitTests'; tests.mkdir(parents=True)
                (tests / 'OwnedOnDeviceAnisetteAttemptTests.swift').write_text('\n'.join('    func ' + name + '() {}' for name in oda.SWIFT_TESTS))
                evidence = root / 'evidence/owned-oda'; calls = []
                def fake_git(path, *args):
                    if args == ('rev-parse', 'HEAD'): return 'a' * 40
                    if args == ('rev-parse', 'HEAD^{tree}'): return 'b' * 40
                    return ''
                def fake_download(url, target, *args, **kwargs):
                    if dependency_failure: raise ValueError('synthetic digest failure')
                    target.write_bytes(b'non-executable synthetic download')
                def fake_execute(name, command, cwd, out, timeout, rows):
                    calls.append(name); rows[name] = {'status': 'FAIL' if name == 'native' else 'PASS'}
                    return name != 'native'
                args = ['runner', '--sources', str(root / 'sources'), '--evidence', str(evidence), '--inputs', str(root / 'qualification-inputs')]
                with patch.object(oda.sys, 'argv', args), patch.object(oda, 'load_candidate_graph', return_value=GRAPH), \
                     patch.object(oda, 'git', side_effect=fake_git), patch.object(oda, 'download', side_effect=fake_download), \
                     patch.object(oda, 'digest', return_value=oda.UNICORN_SHA), patch.object(oda, 'execute', side_effect=fake_execute), \
                     patch.object(oda, 'verify_receipt', side_effect=ValueError('expected fail-closed final gate')):
                    with self.assertRaises(ValueError): oda.main()
                self.assertEqual(calls, ['legacy', 'logging'] if dependency_failure else ['native', 'historical_engine', 'legacy', 'logging', 'swift'])
                receipt = json.loads((evidence / 'receipt.json').read_text())
                self.assertFalse(receipt['all_passed'])
                self.assertEqual(receipt['suites']['startup']['status'], 'NOT_RUN_NATIVE_FAILURE')
                self.assertEqual(list((root / 'qualification-inputs').iterdir()), [])
                self.assertFalse(any(p.suffix in ('.zip', '.apk', '.so') for p in evidence.rglob('*')))

    def test_invalid_preflight_removes_stale_success_before_any_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); evidence = root / 'evidence/owned-oda'; evidence.mkdir(parents=True)
            (evidence / 'receipt.json').write_text('{"all_passed":true}')
            (evidence / 'trusted-startup.json').write_text('{"passed":true}')
            (evidence / 'native.log').write_text('stale success')
            args = ['runner', '--sources', str(root / 'sources'), '--evidence', str(evidence), '--inputs', str(root / 'qualification-inputs')]
            with patch.object(oda.sys, 'argv', args), patch.object(oda, 'load_candidate_graph', side_effect=ValueError('invalid graph')), \
                 patch.object(oda, 'download') as download, patch.object(oda, 'execute') as execute:
                with self.assertRaises(ValueError): oda.main()
                download.assert_not_called(); execute.assert_not_called()
            self.assertFalse((evidence / 'receipt.json').exists())
            self.assertFalse((evidence / 'trusted-startup.json').exists())
            self.assertFalse((evidence / 'native.log').exists())

    def test_workflow_is_independent_and_final_package_keeps_success_gate(self):
        path = Path(__file__).resolve().parents[2] / '.github/workflows/issues-native.yml'
        workflow = path.read_text()
        block = workflow.split('        id: owned_oda_tests\n', 1)[1].split('      - name:', 1)[0]
        self.assertIn("if: ${{ !cancelled() && steps.source_gate.outcome == 'success' }}", block)
        self.assertIn('run_owned_oda.py', block)
        self.assertIn('--inputs qualification-inputs', block)
        self.assertNotIn('continue-on-error', workflow)
        package = workflow.split('      - name: Package same-run reviewed candidate without public release', 1)[1].split('      - name:', 1)[0]
        conditions = re.findall(r'^        if: (.+)$', package, re.M)
        self.assertIn(conditions, ([], ['success()'])) # default or explicit all-success, never failure admission
        self.assertNotIn('qualification-inputs', workflow.split('      - name: Preserve evidence, including failures (not app products)', 1)[1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
