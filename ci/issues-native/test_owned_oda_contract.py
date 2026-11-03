"""Captured real producer admission and isolated runner controls; no downloads or emulation."""
import copy
import hashlib
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
         'reviewed_deltas': {'AnisetteKit': {'tree': 'b' * 40,
             'files': {name: row['git_blob'] for name, row in oda.INPUT_BOUNDARY_FILES.items()
                       if name != 'Sources/AnisetteKit.swift'}}}}
NATIVE = ('PASS: control, filesystem, pinned-header syntax, actual C ABI with mock engine\n'
          'PASS: pinned real Unicorn synthetic ARM64 procedures\n')
SWIFT = ''.join("Test Case '-[AnisetteKitTests.OwnedOnDeviceAnisetteAttemptTests " + test + "]' passed (0.01 seconds).\n" for test in oda.SWIFT_TESTS)
# Unmodified macOS producer output from AK 25fc5b5d634c74280ee714c1ed090e3358cde454,
# https://github.com/NRG-Wardog/AnisetteKit/actions/runs/41641666599 (attempt 1).
# This capture tests parser admission; only the fresh mandatory CI suite qualifies
# a candidate. Replaying this file is not evidence that this run executed Swift.
BOUNDARY_CAPTURE = Path(__file__).with_name('fixtures') / 'owned-input-boundary-macos-25fc5b5d.json'
BOUNDARY_CAPTURE_SHA256 = '2718f227d8647520bba7b284986fa92d6c6284c2c7d225bcd87f94b6f160c156'


def input_boundary():
    return json.loads(BOUNDARY_CAPTURE.read_text())


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
    def test_actual_captured_macos_producer_json_is_admitted(self):
        self.assertTrue(BOUNDARY_CAPTURE.is_file() and not BOUNDARY_CAPTURE.is_symlink())
        self.assertEqual(oda.digest(BOUNDARY_CAPTURE), BOUNDARY_CAPTURE_SHA256)
        output = BOUNDARY_CAPTURE.read_text()
        oda.validate_log('input_boundary', output)
        with tempfile.TemporaryDirectory() as temp, patch('builtins.print'):
            root = Path(temp); rows = {}
            result = types.SimpleNamespace(stdout=output, returncode=0)
            self.assertTrue(oda.execute('input_boundary', ['captured-producer-control'], root, root, 1, rows,
                                        runner=lambda *a, **k: result))
            self.assertEqual(rows['input_boundary'], {'status': 'PASS', 'returncode': 0,
                                                       'log_sha256': BOUNDARY_CAPTURE_SHA256})

    def test_input_boundary_exact_schema_and_all_finite_cases_are_required(self):
        good = input_boundary()
        oda.validate_log('input_boundary', json.dumps(good))
        for key in good:
            bad = copy.deepcopy(good); bad.pop(key)
            with self.subTest(missing=key), self.assertRaises(ValueError):
                oda.validate_log('input_boundary', json.dumps(bad))
        bad = copy.deepcopy(good); bad['extra'] = True
        with self.assertRaises(ValueError): oda.validate_log('input_boundary', json.dumps(bad))
        for key, expected in good['results'].items():
            invalid_values = (int(expected), str(expected), None)
            if key in oda.INPUT_BOUNDARY_RESULTS: invalid_values += (not expected,)
            for invalid in invalid_values:
                bad = copy.deepcopy(good); bad['results'][key] = invalid
                with self.subTest(case=key, invalid=invalid), self.assertRaises(ValueError):
                    oda.validate_log('input_boundary', json.dumps(bad))
            bad = copy.deepcopy(good); bad['results'].pop(key)
            with self.subTest(missing_case=key), self.assertRaises(ValueError):
                oda.validate_log('input_boundary', json.dumps(bad))
        bad = copy.deepcopy(good); bad['results']['unreviewed_case'] = True
        with self.assertRaises(ValueError): oda.validate_log('input_boundary', json.dumps(bad))
        for invalid in ([], None, 1, 'all passed'):
            bad = copy.deepcopy(good); bad['results'] = invalid
            with self.subTest(results=invalid), self.assertRaises(ValueError):
                oda.validate_log('input_boundary', json.dumps(bad))
        for invalid in ([], None, 1, 'all passed'):
            with self.subTest(root=invalid), self.assertRaises(ValueError):
                oda.validate_log('input_boundary', json.dumps(invalid))

    def test_alias_observations_remain_typed_observations_not_admission_conditions(self):
        for name in oda.INPUT_BOUNDARY_OBSERVATIONS:
            for observed in (False, True):
                value = input_boundary(); value['results'][name] = observed
                with self.subTest(name=name, observed=observed):
                    oda.validate_log('input_boundary', json.dumps(value))

    def test_input_boundary_rejects_old_diagnosis_and_unbound_digest_claims(self):
        good = input_boundary()
        for key, current in oda.INPUT_BOUNDARY_METADATA.items():
            bad = copy.deepcopy(good)
            bad[key] = (False if type(current) is bool else
                        '0' * 64 if key.endswith('_sha256') else 'old')
            with self.subTest(field=key), self.assertRaises(ValueError):
                oda.validate_log('input_boundary', json.dumps(bad))
        for field, invalid in [('mode', 'diagnose'), ('mode', 'legacy-reject'),
                               ('legacy_url_mismatch_reproduced', 1),
                               ('classification', 'matching_directories_admitted'),
                               ('source_sha256', None), ('runtime_returncode', False),
                               ('runtime_returncode', 1), ('runtime_returncode', None),
                               ('qualification_passed', False), ('qualification_passed', 1)]:
            bad = copy.deepcopy(good); bad[field] = invalid
            with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                oda.validate_log('input_boundary', json.dumps(bad))
        # No duplicate keys, ignored suffix text, or concatenated success objects.
        text = json.dumps(good)
        for invalid in (text + text, text + 'PASS', text.replace('"mode": "pass"', '"mode": "diagnose", "mode": "pass"'),
                        text.replace('"different_library_rejected": true', '"different_library_rejected": false, "different_library_rejected": true')):
            with self.assertRaises(ValueError): oda.validate_log('input_boundary', invalid)

    def test_input_boundary_graph_requires_sealed_source_runner_and_test_blobs(self):
        oda.verify_input_boundary_graph(GRAPH)
        for name in GRAPH['reviewed_deltas']['AnisetteKit']['files']:
            for mutation in ('missing', 'other_blob'):
                bad = copy.deepcopy(GRAPH); files = bad['reviewed_deltas']['AnisetteKit']['files']
                if mutation == 'missing': files.pop(name)
                else: files[name] = '0' * 40
                with self.subTest(name=name, mutation=mutation), self.assertRaises(ValueError):
                    oda.verify_input_boundary_graph(bad)
        bad = copy.deepcopy(GRAPH)
        bad['reviewed_deltas']['AnisetteKit']['files']['Sources/AnisetteKit.swift'] = '0' * 40
        with self.assertRaises(ValueError): oda.verify_input_boundary_graph(bad)

    def test_input_boundary_checks_real_bytes_and_git_blob_not_only_digest_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = b'synthetic source bytes for isolated source checker control\n'
            seal = {'sha256': hashlib.sha256(data).hexdigest(),
                    'git_blob': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}
            files = {name: dict(seal) for name in oda.INPUT_BOUNDARY_FILES}
            for name in files:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            with patch.object(oda, 'INPUT_BOUNDARY_FILES', files), patch.object(oda, 'git', return_value=seal['git_blob']):
                oda.verify_input_boundary_sources(root)
                for name in files:
                    path = root / name; path.write_bytes(data + b'changed')
                    with self.subTest(changed=name), self.assertRaises(ValueError): oda.verify_input_boundary_sources(root)
                    path.write_bytes(data); path.unlink()
                    with self.subTest(missing=name), self.assertRaises(ValueError): oda.verify_input_boundary_sources(root)
                    other = root / 'identical-untracked-input'; other.write_bytes(data); path.symlink_to(other)
                    with self.subTest(symlink=name), self.assertRaises(ValueError): oda.verify_input_boundary_sources(root)
                    path.unlink(); path.write_bytes(data)
            with patch.object(oda, 'INPUT_BOUNDARY_FILES', files), patch.object(oda, 'git', return_value='0' * 40):
                with self.assertRaises(ValueError): oda.verify_input_boundary_sources(root)

    def test_requires_real_native_marker_and_rejects_skips(self):
        oda.validate_log('native', NATIVE)
        for log in (NATIVE.splitlines()[0], NATIVE + 'NOT RUN: real engine', NATIVE + NATIVE):
            with self.assertRaises(ValueError): oda.validate_log('native', log)

    def test_exact_nine_swift_names_no_zero_or_duplicate_green(self):
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

    def test_input_boundary_success_exit_cannot_override_failed_admission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for mutation in ('bad_json', 'security', 'positive', 'source'):
                value = input_boundary()
                if mutation == 'security': value['results']['different_state_rejected'] = False
                if mutation == 'positive': value['results']['matching_realpath_directories_admitted'] = False
                if mutation == 'source': value['source_sha256'] = '0' * 64
                output = 'invalid JSON' if mutation == 'bad_json' else json.dumps(value)
                result = types.SimpleNamespace(stdout=output, returncode=0)
                rows = {}
                with self.subTest(mutation=mutation), patch('builtins.print'):
                    self.assertFalse(oda.execute('input_boundary', ['synthetic'], root, root, 1, rows,
                                                 runner=lambda *a, **k: result))
                self.assertEqual(rows['input_boundary']['status'], 'FAIL')
                self.assertEqual(rows['input_boundary']['returncode'], 0)
                self.assertIn('validation_error', rows['input_boundary'])

    def test_timeout_is_failure_with_bounded_receipt(self):
        def timeout(*args, **kwargs): raise subprocess.TimeoutExpired('synthetic', 1, output=b'entered fixture\n')
        with tempfile.TemporaryDirectory() as temp:
            rows = {}; root = Path(temp)
            self.assertFalse(oda.execute('native', ['synthetic'], root, root, 1, rows, runner=timeout))
            self.assertEqual(rows['native']['returncode'], 124)
            self.assertIn('BOUNDED_SUBPROCESS_TIMEOUT', (root / 'native.log').read_text())

    def make_receipt(self, root):
        rows = {}
        logs = {'input_boundary': json.dumps(input_boundary()), 'native': NATIVE,
                'historical_engine': json.dumps(historical()), 'legacy': 'Ran 9 tests in 0.1s\nOK\n',
                'logging': 'Ran 13 tests in 0.1s\nOK\n', 'swift': SWIFT, 'startup': 'synthetic fixture only\n'}
        for name, text in logs.items():
            path = root / (name + '.log'); path.write_text(text)
            rows[name] = {'status': 'PASS', 'returncode': 0, 'log_sha256': oda.digest(path)}
        (root / 'trusted-startup.json').write_text(json.dumps(startup()))
        value = {'schema': 'owned-oda-candidate-v1', 'source': oda.source_identity(GRAPH), 'all_passed': True,
                 'device_or_authentication_proven': False, 'unicorn_sha256': oda.UNICORN_SHA, 'suites': rows,
                 'input_boundary_source_inputs': copy.deepcopy(oda.INPUT_BOUNDARY_FILES),
                 'startup_receipt_sha256': oda.digest(root / 'trusted-startup.json')}
        receipt = root / 'receipt.json'; receipt.write_text(json.dumps(value))
        return receipt, value

    def test_receipt_binds_source_logs_and_startup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); receipt, value = self.make_receipt(root)
            with patch.object(oda, 'load_candidate_graph', return_value=GRAPH):
                oda.verify_receipt(receipt)
                (root / 'native.log').write_text(NATIVE + 'changed')
                with self.assertRaises(ValueError): oda.verify_receipt(receipt)
                (root / 'native.log').write_text(NATIVE)
                value['source']['commit'] = 'd' * 40; receipt.write_text(json.dumps(value))
                with self.assertRaises(ValueError): oda.verify_receipt(receipt)

    def test_receipt_requires_new_suite_descriptor_and_bound_untampered_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); receipt, good = self.make_receipt(root)
            with patch.object(oda, 'load_candidate_graph', return_value=GRAPH):
                oda.verify_receipt(receipt)
                for mutation in ('old_six_suites', 'missing_descriptor', 'descriptor_source', 'descriptor_runner',
                                 'descriptor_uuid', 'descriptor_test', 'descriptor_extra', 'suite_false_pass', 'suite_missing_code', 'suite_bool_code', 'suite_extra'):
                    bad = copy.deepcopy(good)
                    if mutation == 'old_six_suites': bad['suites'].pop('input_boundary')
                    elif mutation == 'missing_descriptor': bad.pop('input_boundary_source_inputs')
                    elif mutation == 'descriptor_extra': bad['input_boundary_source_inputs']['extra.swift'] = {}
                    elif mutation == 'suite_false_pass': bad['suites']['input_boundary']['status'] = 'SKIP'
                    elif mutation == 'suite_missing_code': bad['suites']['input_boundary'].pop('returncode')
                    elif mutation == 'suite_bool_code': bad['suites']['input_boundary']['returncode'] = False
                    elif mutation == 'suite_extra': bad['suites']['input_boundary']['skip_reason'] = 'ignored'
                    else:
                        selected = {'descriptor_source': 'Sources/OwnedOnDeviceAnisetteAttempt.swift',
                                    'descriptor_runner': '.ci/owned-attempt/run_input_boundary_probe.py',
                                    'descriptor_uuid': 'Sources/AnisetteKit.swift',
                                    'descriptor_test': 'Tests/AnisetteKitTests/OwnedOnDeviceAnisetteAttemptTests.swift'}[mutation]
                        bad['input_boundary_source_inputs'][selected]['sha256'] = '0' * 64
                    receipt.write_text(json.dumps(bad))
                    with self.subTest(mutation=mutation), self.assertRaises(ValueError): oda.verify_receipt(receipt)
                receipt.write_text(json.dumps(good))
                log = root / 'input_boundary.log'; original = log.read_text()
                log.write_text(original + '\n')
                with self.assertRaisesRegex(ValueError, 'log drift'): oda.verify_receipt(receipt)
                # Even updating the outer checksum cannot admit false or unbound producer output.
                for field in ('source_sha256', 'fixture_sha256', 'results'):
                    changed = input_boundary()
                    if field == 'results': changed[field]['different_library_rejected'] = False
                    else: changed[field] = '0' * 64
                    log.write_text(json.dumps(changed))
                    bad = copy.deepcopy(good); bad['suites']['input_boundary']['log_sha256'] = oda.digest(log)
                    receipt.write_text(json.dumps(bad))
                    with self.subTest(resealed_tamper=field), self.assertRaises(ValueError): oda.verify_receipt(receipt)
                receipt.write_text(json.dumps(good)); log.unlink()
                with self.assertRaises(ValueError): oda.verify_receipt(receipt)
                alternate = root / 'alternate.log'; alternate.write_text(original); log.symlink_to(alternate)
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
                     patch.object(oda, 'verify_input_boundary_sources'), \
                     patch.object(oda, 'verify_receipt', side_effect=ValueError('expected fail-closed final gate')):
                    with self.assertRaises(ValueError): oda.main()
                self.assertEqual(calls, ['input_boundary', 'legacy', 'logging'] if dependency_failure else ['input_boundary', 'native', 'historical_engine', 'legacy', 'logging', 'swift'])
                receipt = json.loads((evidence / 'receipt.json').read_text())
                self.assertFalse(receipt['all_passed'])
                self.assertEqual(receipt['suites']['startup']['status'], 'NOT_RUN_NATIVE_FAILURE')
                self.assertEqual(list((root / 'qualification-inputs').iterdir()), [])
                self.assertFalse(any(p.suffix in ('.zip', '.apk', '.so') for p in evidence.rglob('*')))

    def exercise_boundary_failure(self, root):
        source = root / 'sources/AnisetteKit'
        tests = source / 'Tests/AnisetteKitTests'; tests.mkdir(parents=True)
        (tests / 'OwnedOnDeviceAnisetteAttemptTests.swift').write_text('\n'.join('    func ' + name + '() {}' for name in oda.SWIFT_TESTS))
        evidence = root / 'evidence/owned-oda'; evidence.mkdir(parents=True); calls = []
        stale = ('receipt.json', 'trusted-startup.json', *(name + '.log' for name in oda.SUITES))
        for name in stale: (evidence / name).write_text('stale success')
        def fake_git(path, *args):
            self.assertEqual(path, source.resolve())
            return 'a' * 40 if args == ('rev-parse', 'HEAD') else 'b' * 40 if args == ('rev-parse', 'HEAD^{tree}') else ''
        def fake_execute(name, command, cwd, out, timeout, rows):
            self.assertEqual(cwd, source.resolve())
            self.assertEqual(out, evidence.resolve())
            calls.append(name); rows[name] = {'status': 'FAIL' if name == 'input_boundary' else 'PASS'}
            if name == 'input_boundary':
                self.assertTrue(all(not (out / previous).exists() for previous in stale))
                self.assertEqual(command[-2:], ['--expect', 'pass'])
                self.assertEqual(Path(command[-3]), source.resolve() / '.ci/owned-attempt/run_input_boundary_probe.py')
                self.assertEqual(timeout, 180)
            if name == 'startup': (out / 'trusted-startup.json').write_text(json.dumps(startup()))
            return name != 'input_boundary'
        args = ['runner', '--sources', str(root / 'sources'), '--evidence', str(evidence), '--inputs', str(root / 'qualification-inputs')]
        with patch.object(oda.sys, 'argv', args), patch.object(oda, 'load_candidate_graph', return_value=GRAPH), \
             patch.object(oda, 'git', side_effect=fake_git), patch.object(oda, 'download'), \
             patch.object(oda, 'digest', return_value=oda.UNICORN_SHA), patch.object(oda, 'execute', side_effect=fake_execute), \
             patch.object(oda, 'verify_input_boundary_sources'), \
             patch.object(oda, 'verify_receipt', side_effect=ValueError('expected fail-closed final gate')):
            with self.assertRaises(ValueError): oda.main()
        self.assertEqual(calls, ['input_boundary', 'native', 'historical_engine', 'legacy', 'logging', 'swift', 'startup'])
        receipt = json.loads((evidence / 'receipt.json').read_text())
        self.assertFalse(receipt['all_passed'])
        self.assertEqual(receipt['input_boundary_source_inputs'], oda.INPUT_BOUNDARY_FILES)
        self.assertTrue(all(row['status'] == 'PASS' for name, row in receipt['suites'].items() if name != 'input_boundary'))
        self.assertEqual(list((root / 'qualification-inputs').iterdir()), [])
        self.assertFalse(any((evidence / (name + '.log')).exists() for name in oda.SUITES))

    def test_input_boundary_failure_still_collects_six_suites_but_blocks_success(self):
        with tempfile.TemporaryDirectory() as temp:
            self.exercise_boundary_failure(Path(temp))

    def test_main_canonicalizes_symlink_ancestor_and_clears_stale_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            physical = Path(temp) / 'physical'; physical.mkdir()
            alias = Path(temp) / 'alias'; alias.symlink_to(physical, target_is_directory=True)
            self.assertNotEqual(alias, alias.resolve())
            self.exercise_boundary_failure(alias)
            for relative in ('sources/AnisetteKit', 'evidence/owned-oda', 'qualification-inputs'):
                self.assertTrue((alias / relative).is_dir())
                self.assertFalse((alias / relative).is_symlink())
            self.assertFalse(json.loads((physical / 'evidence/owned-oda/receipt.json').read_text())['all_passed'])

    def test_main_still_rejects_symlink_qualification_root_leaves(self):
        for leaf in ('evidence', 'inputs'):
            with self.subTest(leaf=leaf), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); real = root / 'real'; real.mkdir()
                linked = root / 'linked'; linked.symlink_to(real, target_is_directory=True)
                evidence = linked if leaf == 'evidence' else root / 'evidence'
                inputs = linked if leaf == 'inputs' else root / 'inputs'
                args = ['runner', '--sources', str(root / 'sources'), '--evidence', str(evidence), '--inputs', str(inputs)]
                with patch.object(oda.sys, 'argv', args), patch.object(oda, 'load_candidate_graph') as graph, \
                     patch.object(oda, 'execute') as execute:
                    with self.assertRaisesRegex(ValueError, 'No symlink qualification roots'): oda.main()
                graph.assert_not_called(); execute.assert_not_called()

    def test_invalid_preflight_removes_stale_success_before_any_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); evidence = root / 'evidence/owned-oda'; evidence.mkdir(parents=True)
            (evidence / 'receipt.json').write_text('{"all_passed":true}')
            (evidence / 'trusted-startup.json').write_text('{"passed":true}')
            (evidence / 'native.log').write_text('stale success')
            (evidence / 'input_boundary.log').write_text('stale success')
            args = ['runner', '--sources', str(root / 'sources'), '--evidence', str(evidence), '--inputs', str(root / 'qualification-inputs')]
            with patch.object(oda.sys, 'argv', args), patch.object(oda, 'load_candidate_graph', side_effect=ValueError('invalid graph')), \
                 patch.object(oda, 'download') as download, patch.object(oda, 'execute') as execute:
                with self.assertRaises(ValueError): oda.main()
                download.assert_not_called(); execute.assert_not_called()
            self.assertFalse((evidence / 'receipt.json').exists())
            self.assertFalse((evidence / 'trusted-startup.json').exists())
            self.assertFalse((evidence / 'native.log').exists())
            self.assertFalse((evidence / 'input_boundary.log').exists())

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
