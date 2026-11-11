import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
from pathlib import Path as _CIPath
import sys as _workflow_sys
from pathlib import Path as _WorkflowPath
_workflow_sys.path.insert(0, str((_CIPath(__file__).resolve().parents[3]/'ci')))
from workflow_jobs import workflow_job
import copy
import json
from pathlib import Path
import tempfile
import unittest
from ci.qualification.native.run_issue_tests import (SUITES, HARNESS_SUITES, verify_issue_receipt, OWNED_INPUTS,
    OWNED_SWIFT_SUITES, INITIAL_REJECTION_INPUTS, INITIAL_REJECTION_MARKERS, owned_expected_graph, verify_owned_receipt, run_owned_swift_suites)


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
        gate = patch('ci.qualification.native.run_issue_tests.owned_expected_graph', return_value=test_owned_graph())
        gate.start(); self.addCleanup(gate.stop)
        provenance = owned_receipt(); provenance.pop('swift_suites'); provenance.pop('target_device_execution')
        inputs = patch('ci.qualification.native.run_issue_tests.collect_owned_sources', return_value=provenance)
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
        gate = patch('ci.qualification.native.run_issue_tests.owned_expected_graph', return_value=test_owned_graph())
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

    def test_strict_portal_inverse_is_required_and_must_match_committed_source(self):
        owner = 'SideStore/Dependencies/SideSign'
        name = '.ci/owned-oda/strict-appid-portal-projection.json'
        self.assertIn((owner, '.ci/owned-oda', 'test_sources.py'), SUITES)
        self.assertIn(name, OWNED_INPUTS[owner])
        value = owned_receipt()
        provenance = {key: copy.deepcopy(value[key]) for key in ('source_graph', 'source_sha256')}
        missing = copy.deepcopy(value); missing['source_sha256'].pop(owner+'/'+name)
        with self.assertRaisesRegex(ValueError, 'closure'):
            verify_owned_receipt(missing, provenance)
        tampered = copy.deepcopy(value); tampered['source_sha256'][owner+'/'+name] = 'b'*64
        with self.assertRaisesRegex(ValueError, 'actual committed inputs'):
            verify_owned_receipt(tampered, provenance)

    def test_portal_inverse_missing_changed_linked_and_foreign_commit_files_fail_both_collectors(self):
        import subprocess
        from unittest.mock import patch
        from ci.qualification.native import run_issue_tests as issue
        from ci.qualification.native import refresh_truth_receipt as refresh
        owner = 'SideStore/Dependencies/SideSign'
        names = ('.ci/owned-oda/test_sources.py', '.ci/owned-oda/strict-appid-portal-projection.json')
        for name in names:
            self.assertIn(name, issue.OWNED_INPUTS[owner])
            self.assertIn(name, refresh.INPUTS[owner])
        with tempfile.TemporaryDirectory() as directory:
            sources = Path(directory).resolve(); root = sources/owner; root.mkdir(parents=True)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), '-c', 'core.hooksPath=/dev/null', *args], text=True).strip()
            git('init', '-q'); git('config', 'user.name', 'Synthetic fixture'); git('config', 'user.email', 'fixture@example.invalid')
            for name in names:
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{}\n')
            git('add', '.'); git('commit', '-qm', 'Synthetic committed inverse input')
            commit, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
            graph = {'owners': {owner: ['NRG-Wardog/SideSign', commit]}, 'reviewed_deltas': {owner: {'tree': tree}}}
            expected = {owner: {'commit': commit, 'tree': tree}}
            with patch.object(issue, 'OWNED_INPUTS', {owner: names}), \
                 patch.object(issue, 'owned_expected_graph', return_value=expected), \
                 patch.object(refresh, 'INPUTS', {owner: names}):
                before_issue = issue.collect_owned_sources(sources)
                before_refresh = refresh.collect(sources, graph)
                self.assertEqual(before_issue['source_sha256'], before_refresh['source_sha256'])
                for name in names:
                    path = root/name
                    for mutation in ('missing', 'changed', 'linked'):
                        if mutation == 'missing': path.unlink()
                        elif mutation == 'changed': path.write_text('{"unreviewed":true}\n')
                        else:
                            path.unlink(); path.symlink_to(root/names[1 if name == names[0] else 0])
                        for collector in (lambda: issue.collect_owned_sources(sources), lambda: refresh.collect(sources, graph)):
                            with self.subTest(name=name,mutation=mutation), self.assertRaises(ValueError): collector()
                        if path.is_symlink(): path.unlink()
                        path.write_text('{}\n')
                    self.assertEqual(issue.collect_owned_sources(sources), before_issue)
                    self.assertEqual(refresh.collect(sources, graph), before_refresh)
                (root/names[1]).write_text('{"unreviewed":true}\n')
                git('add', '.'); git('commit', '-qm', 'Foreign inverse fixture')
                for collector in (lambda: issue.collect_owned_sources(sources), lambda: refresh.collect(sources, graph)):
                    with self.assertRaisesRegex(ValueError, 'graph differs|HEAD differs'): collector()

    def test_receipt_must_match_current_committed_inputs(self):
        value = owned_receipt()
        provenance = {k: value[k] for k in ('source_graph', 'source_sha256')}
        self.assertEqual(verify_owned_receipt(value, provenance), value)
        changed = copy.deepcopy(provenance)
        changed['source_sha256'][next(iter(changed['source_sha256']))] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'actual committed inputs'):
            verify_owned_receipt(value, changed)

    def test_actual_prompt_suite_and_complete_source_closure_are_required(self):
        self.assertIn(('LiveContainer', 'tests', 'test_diagnostic_provider_sequence.py'), SUITES)
        required = {
            'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
            'LiveContainerSwiftUI/Models/V3DiagnosticProviderSequence.swift',
            'LiveContainerSwiftUI/Utilities/V3Localization.swift',
            'Resources/Localizable.xcstrings',
            'tests/test_diagnostic_provider_sequence.py',
            'tests/provider_sequence/main.swift',
            'tests/provider_sequence/prompt_main.swift',
        }
        self.assertTrue(required <= set(OWNED_INPUTS['LiveContainer']))
        value = owned_receipt()
        provenance = {k: value[k] for k in ('source_graph', 'source_sha256')}
        for name in sorted(required):
            path = 'LiveContainer/' + name
            with self.subTest(path=path):
                changed = copy.deepcopy(value); del changed['source_sha256'][path]
                with self.assertRaises(ValueError): verify_owned_receipt(changed, provenance)
                changed = copy.deepcopy(value); changed['source_sha256'][path] = 'b' * 64
                with self.assertRaisesRegex(ValueError, 'actual committed inputs'):
                    verify_owned_receipt(changed, provenance)

    def test_source_owned_wrapper_rejects_skipped_and_empty_suites(self):
        import subprocess
        import sys
        from ci.qualification.native.run_issue_tests import RUNNER
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'test_prompt.py'
            for body in ('', 'import unittest\nclass Prompt(unittest.TestCase):\n'
                         ' @unittest.skip("Swift unavailable")\n'
                         ' def test_native(self): pass\n'):
                path.write_text(body)
                result = subprocess.run([sys.executable, '-B', '-c', RUNNER, temp,
                                         path.name], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_prompt_gate_controls_precede_native_suite_in_each_workflow(self):
        root = (_CIPath(__file__).resolve().parents[3])
        control = 'python3 -B harness/tests/ci/native/test_issue_receipt.py'
        native = 'python3 -B harness/ci/qualification/native/run_issue_tests.py 2>&1'
        for name, job in (('native-build.yml', None), ('focused-diagnostics.yml', 'refresh_truth')):
            with self.subTest(workflow=name):
                text = workflow_job(root / '.github/workflows' / name, job)
                self.assertEqual(text.count(control), 1)
                self.assertEqual(text.count(native), 1)
                self.assertLess(text.index(control), text.index(native))

    def test_prompt_compile_failure_cannot_write_aggregate_success(self):
        import os
        import subprocess
        from unittest.mock import patch
        from ci.qualification.native.run_issue_tests import main
        old = Path.cwd()
        provenance = owned_receipt(); provenance.pop('swift_suites'); provenance.pop('target_device_execution')
        calls = []
        def execute(command, **kwargs):
            calls.append(command)
            if command[-1] == 'test_diagnostic_provider_sequence.py':
                raise subprocess.CalledProcessError(1, command, output='prompt Swift compilation failed')
            return subprocess.CompletedProcess(command, 0)
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)
                for owner, folder, pattern in SUITES:
                    path = Path('sources') / owner / folder / pattern
                    path.parent.mkdir(parents=True, exist_ok=True); path.write_text('synthetic fixture')
                Path('evidence').mkdir(); Path('evidence/issue-tests.json').write_text('stale success')
                with patch('ci.qualification.native.run_issue_tests.shutil.which', return_value='/synthetic/swiftc'), \
                     patch('ci.qualification.native.run_issue_tests.collect_owned_sources', return_value=provenance) as seal, \
                     patch('ci.qualification.native.run_issue_tests.subprocess.run', side_effect=execute), \
                     patch('ci.qualification.native.run_issue_tests.run_owned_swift_suites') as owned:
                    with self.assertRaises(subprocess.CalledProcessError): main()
                    seal.assert_called_once_with('sources')
                    owned.assert_not_called()
                self.assertTrue(calls)
                self.assertEqual(calls[-1][-1], 'test_diagnostic_provider_sequence.py')
                self.assertFalse(Path('evidence/issue-tests.json').exists())
            finally: os.chdir(old)

    def test_prompt_inputs_must_match_before_and_after_native_execution(self):
        import os
        from unittest.mock import patch
        from ci.qualification.native.run_issue_tests import main
        old = Path.cwd()
        value = owned_receipt()
        provenance = {k: copy.deepcopy(value[k]) for k in ('source_graph', 'source_sha256')}
        value['source_sha256']['LiveContainer/tests/provider_sequence/prompt_main.swift'] = 'b' * 64
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)
                for owner, folder, pattern in SUITES:
                    path = Path('sources') / owner / folder / pattern
                    path.parent.mkdir(parents=True, exist_ok=True); path.write_text('synthetic fixture')
                Path('evidence').mkdir(); Path('evidence/issue-tests.json').write_text('stale success')
                with patch('ci.qualification.native.run_issue_tests.shutil.which', return_value='/synthetic/swiftc'), \
                     patch('ci.qualification.native.run_issue_tests.collect_owned_sources', return_value=provenance) as seal, \
                     patch('ci.qualification.native.run_issue_tests.subprocess.run') as execute, \
                     patch('ci.qualification.native.run_issue_tests.run_owned_swift_suites', return_value=value):
                    with self.assertRaisesRegex(ValueError, 'actual committed inputs'): main()
                    seal.assert_called_once_with('sources')
                    self.assertTrue(execute.called)
                self.assertFalse(Path('evidence/issue-tests.json').exists())
            finally: os.chdir(old)

    def test_each_script_is_bounded_and_required_marker_count_is_exact(self):
        import subprocess
        from unittest.mock import patch
        provenance = owned_receipt(); provenance.pop('swift_suites'); provenance.pop('target_device_execution')
        results = [subprocess.CompletedProcess([], 0, stdout=('\n'.join(INITIAL_REJECTION_MARKERS) + '\n') if script == '.ci/initial-provider-rejection/run.sh' else (marker + ' synthetic test output\n') * count)
                   for _, script, marker, count, _ in OWNED_SWIFT_SUITES]
        with tempfile.TemporaryDirectory() as temp:
            with patch('ci.qualification.native.run_issue_tests.collect_owned_sources', return_value=provenance), \
                 patch('ci.qualification.native.run_issue_tests.subprocess.run', side_effect=results) as run:
                value = run_owned_swift_suites(temp, Path(temp) / 'evidence')
                verify_owned_receipt(value)
                self.assertEqual([c.kwargs['timeout'] for c in run.call_args_list], [300, 120, 300])
            for output in ['', OWNED_SWIFT_SUITES[0][2] + ' incomplete\n']:
                with patch('ci.qualification.native.run_issue_tests.collect_owned_sources', return_value=provenance), \
                     patch('ci.qualification.native.run_issue_tests.subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout=output)):
                    with self.assertRaises(ValueError): run_owned_swift_suites(temp, Path(temp) / 'evidence')
            with patch('ci.qualification.native.run_issue_tests.collect_owned_sources', return_value=provenance), \
                 patch('ci.qualification.native.run_issue_tests.subprocess.run', return_value=subprocess.CompletedProcess([], 1, stdout='compile failed')):
                with self.assertRaises(subprocess.CalledProcessError): run_owned_swift_suites(temp, Path(temp) / 'evidence')

    def test_initial_rejection_markers_and_separate_logs(self):
        import subprocess
        from unittest.mock import patch
        provenance = owned_receipt(); provenance.pop('swift_suites'); provenance.pop('target_device_execution')
        base = [(marker + ' synthetic test output\n') * count for _, _, marker, count, _ in OWNED_SWIFT_SUITES[:2]]
        valid = '\n'.join(INITIAL_REJECTION_MARKERS) + '\n'
        invalid = ['', valid.replace('cases=27', 'cases=26'), valid.replace('account_calls=false', 'account_calls=true'), valid + INITIAL_REJECTION_MARKERS[0].replace('cases=27', 'cases=999') + '\n']
        for marker in INITIAL_REJECTION_MARKERS:
            invalid += [valid.replace(marker + '\n', ''), valid + marker + '\n']
        with tempfile.TemporaryDirectory() as temp:
            for output in [valid] + invalid:
                results = [subprocess.CompletedProcess([], 0, stdout=text) for text in base + [output]]
                with patch('ci.qualification.native.run_issue_tests.collect_owned_sources', return_value=provenance), patch('ci.qualification.native.run_issue_tests.subprocess.run', side_effect=results):
                    if output == valid:
                        run_owned_swift_suites(temp, Path(temp) / 'evidence')
                        self.assertEqual((Path(temp)/'evidence/owned-SideStore.log').read_text(), base[1])
                        self.assertEqual((Path(temp)/'evidence/owned-initial-provider-rejection.log').read_text(), valid)
                    else:
                        with self.assertRaises(ValueError): run_owned_swift_suites(temp, Path(temp) / 'evidence')

    def test_generated_input_owner_classification_normalizes_sibling_checkout(self):
        import sys
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            harness_file = root/'harness/tests/ci/native/test_issue_receipt.py'
            harness_file.parent.mkdir(parents=True); harness_file.touch()
            side = root/'sources/SideStore'
            generator = side/'.ci/initial-provider-rejection/generate.py'
            generator.parent.mkdir(parents=True)
            inputs = []
            for owner, base in [('SideStore', side), ('SideStore/Dependencies/SideSign', side/'Dependencies/SideSign')]:
                path = base/next(iter(INITIAL_REJECTION_INPUTS[owner]))
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text('synthetic input')
                inputs.append(str(path.resolve()))
            generator.write_text('import argparse,json\nfrom pathlib import Path\n'
                'p=argparse.ArgumentParser();p.add_argument("--output");p.add_argument("--sidesign-root");a=p.parse_args()\n'
                + 'Path(a.output,"source-evidence.json").write_text(json.dumps({"inputs":'+repr(inputs)+'}))\n')
            with patch.object(sys.modules[__name__], '__file__', str(harness_file)):
                self.test_actual_generated_initial_rejection_inputs_are_all_sealed()

    def test_actual_generated_initial_rejection_inputs_are_all_sealed(self):
        import subprocess, sys
        root = (_CIPath(__file__).resolve().parents[3]/'..').resolve()
        side = root / 'SS'
        if not side.is_dir():
            side = root / 'sources/SideStore'
        script = side / '.ci/initial-provider-rejection/generate.py'
        self.assertTrue(script.is_file())
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run([sys.executable, '-B', str(script), '--output', temp, '--sidesign-root', str(side / 'Dependencies/SideSign')], check=True, timeout=60)
            actual = json.loads((Path(temp)/'source-evidence.json').read_text())
            for raw in actual['inputs']:
                path = Path(raw).resolve()
                prefix = side / 'Dependencies/SideSign'
                owner = 'SideStore/Dependencies/SideSign' if path.is_relative_to(prefix) else 'SideStore'
                base = prefix if owner.endswith('SideSign') else side
                self.assertIn(str(path.relative_to(base)), INITIAL_REJECTION_INPUTS[owner])
        self.assertIn(('SideStore', '.ci/initial-provider-rejection', 'test_source.py'), SUITES)
        value = owned_receipt()
        for owner, names in INITIAL_REJECTION_INPUTS.items():
            for name in names:
                broken = copy.deepcopy(value); del broken['source_sha256'][owner + '/' + name]
                with self.assertRaises(ValueError): verify_owned_receipt(broken)

    def test_actual_input_bytes_and_symlink_or_wrong_source_rejected(self):
        import hashlib
        from unittest.mock import patch
        from ci.qualification.native.run_issue_tests import collect_owned_sources
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for owner, names in OWNED_INPUTS.items():
                for name in names:
                    path = root / owner / name; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'synthetic input')
            with patch('ci.qualification.native.run_issue_tests.subprocess.check_output', side_effect=lambda command, **kw: 'b'*40+'\n'+'c'*40+'\n' if 'rev-parse' in command else b'synthetic input'):
                value = collect_owned_sources(root)
                self.assertEqual(set(value['source_sha256'].values()), {hashlib.sha256(b'synthetic input').hexdigest()})
                owner = next(iter(OWNED_INPUTS)); path = root / owner / OWNED_INPUTS[owner][0]
                path.write_bytes(b'dirty fixture before execution')
                with self.assertRaisesRegex(ValueError, 'committed blob'): collect_owned_sources(root)
                path.unlink(); path.symlink_to(root / 'not-present')
                with self.assertRaisesRegex(ValueError, 'symlink'): collect_owned_sources(root)
            with patch('ci.qualification.native.run_issue_tests.subprocess.check_output', return_value='d'*40+'\n'+'c'*40+'\n'):
                with self.assertRaisesRegex(ValueError, 'graph differs'): collect_owned_sources(root)

    def test_missing_compiler_clears_stale_aggregate_success(self):
        import os
        from unittest.mock import patch
        from ci.qualification.native.run_issue_tests import main
        old = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp); Path('evidence').mkdir()
                Path('evidence/issue-tests.json').write_text('stale success')
                with patch('ci.qualification.native.run_issue_tests.shutil.which', return_value=None):
                    with self.assertRaises(SystemExit): main()
                self.assertFalse(Path('evidence/issue-tests.json').exists())
            finally: os.chdir(old)


class NativeIssueReceiptTests(unittest.TestCase):
    def test_real_named_tests_and_zero_skips_required(self):
        from ci.qualification.native.run_issue_tests import REQUIRED_RUST_TESTS, validate_rust_output
        for package, names in REQUIRED_RUST_TESTS.items():
            output = '\n'.join('test module::' + name + ' ... ok' for name in names)
            output += f'\ntest result: ok. {len(names)} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0s\n'
            validate_rust_output(package, output)
            for bad in (output.replace('0 ignored', '1 ignored'), output.replace('0 filtered out', '1 filtered out'),
                        output.replace(names[0], 'not_the_test'), output + output, ''):
                with self.subTest(package=package, bad=bad):
                    with self.assertRaises(ValueError):
                        validate_rust_output(package, bad)

    def test_generated_header_provider_executions_are_exact(self):
        from ci.qualification.native.run_issue_tests import GENERATED_PROVIDER_MARKERS, validate_generated_provider_output
        valid = '\n'.join(GENERATED_PROVIDER_MARKERS) + '\n'
        validate_generated_provider_output(valid)
        for marker in GENERATED_PROVIDER_MARKERS:
            for invalid in (valid.replace(marker + '\n', ''), valid + marker + '\n',
                            valid.replace('paths=13', 'paths=12')):
                with self.subTest(marker=marker, invalid=invalid):
                    with self.assertRaises(ValueError):
                        validate_generated_provider_output(invalid)

    def test_native_receipt_exact_closure(self):
        from ci.qualification.native.run_issue_tests import RUST_FEATURES, REQUIRED_RUST_TESTS, verify_native_receipt
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
        from ci.qualification.native.run_issue_tests import run_idevice_native
        old = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                Path('evidence').mkdir()
                Path('evidence/idevice-issue-tests.json').write_text('stale success')
                failed = subprocess.CompletedProcess([], 1, stdout='compile error')
                with patch('ci.qualification.native.run_issue_tests.subprocess.run', return_value=failed):
                    with self.assertRaises(subprocess.CalledProcessError):
                        run_idevice_native()
                self.assertFalse(Path('evidence/idevice-issue-tests.json').exists())
                self.assertEqual(Path('evidence/idevice-tests.log').read_text(), 'compile error')
            finally:
                os.chdir(old)

    def test_custom_usb_heartbeat_is_explicit_without_enabling_defaults(self):
        from ci.qualification.native.run_issue_tests import RUST_FEATURES
        # Regression for E0432 in tunnel_provider::tunnel_create_usb: selecting
        # the custom tunnel stack also needs its live HeartbeatClient service.
        features = RUST_FEATURES['idevice-ffi'].split(',')
        self.assertIn('tunnel_tcp_stack', features)
        self.assertEqual(features.count('heartbeat'), 1)
        self.assertNotIn('full', features)
        self.assertNotIn('aws-lc', features)
        runner = ((_CIPath(__file__).resolve().parents[3]/'ci/qualification/native/run_issue_tests.py')).read_text()
        self.assertIn("'--no-default-features'", runner)

    def test_all_borrowed_provider_tests_are_mandatory(self):
        from ci.qualification.native.run_issue_tests import RUST_FEATURES, REQUIRED_RUST_TESTS, validate_rust_output
        features = set(RUST_FEATURES['idevice-ffi'].split(','))
        self.assertTrue({'afc', 'amfi', 'bt_packet_logger', 'companion_proxy',
            'heartbeat', 'location_simulation', 'misagent', 'mobileactivationd',
            'notification_proxy', 'pcapd', 'preboard_service', 'screenshotr'} <= features)
        expected = {
            'afc_client_connect_errors_preserve_provider',
            'afc2_client_connect_errors_preserve_provider',
            'amfi_connect_errors_preserve_provider',
            'bt_packet_logger_connect_errors_preserve_provider',
            'companion_proxy_connect_errors_preserve_provider',
            'heartbeat_connect_errors_preserve_provider',
            'lockdown_location_simulation_connect_errors_preserve_provider',
            'misagent_connect_errors_preserve_provider',
            'notification_proxy_connect_errors_preserve_provider',
            'pcapd_connect_errors_preserve_provider',
            'preboard_service_connect_errors_preserve_provider',
            'screenshotr_connect_errors_preserve_provider',
            'mobileactivationd_client_borrows_provider_until_freed',
        }
        names = REQUIRED_RUST_TESTS['idevice-ffi']
        self.assertTrue(expected <= set(names))
        self.assertEqual(len(names), len(set(names)))
        output = '\n'.join('test provider::ownership_tests::' + name + ' ... ok' for name in names)
        output += '\ntest result: ok. ' + str(len(names)) + ' passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;'
        validate_rust_output('idevice-ffi', output)
        for missing in expected:
            with self.subTest(missing=missing):
                incomplete = output.replace('test provider::ownership_tests::' + missing + ' ... ok', '')
                with self.assertRaises(ValueError):
                    validate_rust_output('idevice-ffi', incomplete)

    def test_feature_and_workflow_closure(self):
        from ci.qualification.native.run_issue_tests import RUST_FEATURES, SUITES
        self.assertIn('cryptexd', RUST_FEATURES['idevice'].split(','))
        self.assertTrue({'tss', 'tunnel_tcp_stack', 'mobile_image_mounter', 'heartbeat'} <= set(RUST_FEATURES['idevice-ffi'].split(',')))
        for name in ('ownership', 'listener', 'mss', 'adapter_clone_scope'):
            self.assertIn(('idevice', '.ci/issue53', 'test_' + name + '.py'), SUITES)
        root = (_CIPath(__file__).resolve().parents[3])
        workflow = (root / '.github/workflows/native-build.yml').read_text()
        self.assertIn('python3 -B harness/ci/qualification/native/run_issue_tests.py --idevice-native', workflow)
        self.assertIn("verify_native_receipt(E / 'idevice-issue-tests.json')", ((root/'ci/build/record.py')).read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
