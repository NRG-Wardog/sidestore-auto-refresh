"""Control/receipt guards only: subprocess doubles, no Swift/native/provider execution."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('comprehensive', HERE / 'verify_comprehensive_diagnostics.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class Controls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS may expose the same temporary directory via /var and /private/var.
        # Match the runner's canonical paths without relaxing evidence comparisons.
        self.root = Path(self.temp.name).resolve()
        self.sources = self.root / 'sources'
        self.sources.mkdir()
        self.output = self.root / 'evidence'
        self.swiftc = self.root / 'swiftc-control-double'
        self.swiftc.write_text('Never executed\n')
        self.contract = self.root / 'contract.json'
        self.markers = {k: 'CONTROL_PASS_' + k for k in ('legacy','auth','iloader','onboarding','profile','wire_service','wire_host','pairing','deferred')}
        for name in gate.REQUIRED_INPUTS:
            path = self.sources / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('source control double: ' + name)
        self.config = {'schema':'comprehensive-diagnostics-source-contract-v1',
            'source_inputs_bound':True, 'schemas':{'request':'ar1','profile':'hp2','provider':'ap1'},
            'input_sha256':{name:gate.digest((self.sources/name).read_bytes()) for name in sorted(gate.REQUIRED_INPUTS)},
            'success_markers':self.markers, 'onboarding_case_names':['control-case-a'],
            'registration_case_names':['registration-case-a'],
            **{suite + '_case_names': ['control-case-a'] for suite in gate.SETUP_FIXTURES}}
        self.config['extension_selection_case_names'] = ['extension-control-' + str(n) for n in range(17)]
        self.config['refresh_failure_case_names'] = ['refresh-control-case']
        self.config['pairing_recovery_case_names'] = ['recovery-control-case']
        self.config['deferred_baseline'] = {'owners': copy.deepcopy(gate.DEFERRED_BASELINE_OWNERS),
            'input_sha256': {name: self.config['input_sha256'][name]
                            for name in gate.SETUP_FIXTURES['deferred-device-setup']['sources']}}
        self.save_contract()
        self.calls = []

    def save_contract(self):
        gate.write_json(self.contract, self.config)

    def control_process(self, argv, **kwargs):
        name = gate.COMMANDS[len(self.calls)]
        self.calls.append((name, argv))
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(kwargs['env']['PYTHONDONTWRITEBYTECODE'], '1')
        data = b'control double\n'
        if name == 'legacy-generate':
            Path(argv[-1]).write_text('// generated control double')
            mappings = {'errors': gate.SIGN+'/Sources/Models/Errors.swift', 'auth': gate.SIGN+'/Sources/DeveloperPortal/Authentication.swift',
                        'store':'SideStore/AltStore/AppDelegate.swift', 'host':'LiveContainer/SideStoreSupport/SideStore.swift',
                        'shell':'LiveContainer/LiveContainerSwiftUI/Views/V3UnifiedShell.swift'}
            gate.write_json(Path(argv[-1]).with_suffix('.sources.json'), {'input_sha256':{k:self.config['input_sha256'][v] for k,v in mappings.items()}})
        elif name == 'auth-generate':
            folder = Path(argv[-1])
            for file in gate.AUTH_FILES:
                (folder / file).write_text('// generated control double')
            mappings = ['Sources/DeveloperPortal/Authentication.swift','Sources/Models/AuthenticationDiagnosticTrace.swift','Sources/Models/Errors.swift']
            gate.write_json(folder/'source-evidence.json', {'schema':'auth-request-source-evidence-v1','baseline_commit':'9' * 40,
                'inputs':{k:self.config['input_sha256'][gate.SIGN+'/'+k] for k in mappings},
                'outputs':{k:gate.digest((folder/k).read_bytes()) for k in gate.AUTH_FILES}})
        elif name == 'iloader-generate':
            folder = Path(argv[argv.index('--output-dir') + 1])
            self.assertEqual(Path(argv[argv.index('--sidestore-root') + 1]), self.sources / 'SideStore')
            for file in gate.ILOADER_FILES:
                (folder / file).write_text('// generated control double')
            mappings = ['Sources/DeveloperPortal/Authentication.swift','Sources/Models/AuthenticationDiagnosticTrace.swift',
                        'Sources/Models/Errors.swift','Sources/Anisette/ILoaderRemoteAnisette.swift',
                        'Sources/DeveloperPortal/ILoaderAuthenticationPolicy.swift',
                        '.ci/iloader-auth-parity/generate.py','.ci/iloader-auth-parity/test_source.py']
            inputs = {k:self.config['input_sha256'][gate.SIGN + '/' + k] for k in mappings}
            factory = 'SideStore/SideStore/Core/Anisette/AnisetteProvider.swift'
            inputs[factory] = self.config['input_sha256'][factory]
            gate.write_json(folder / 'source-evidence.json', {
                'schema':'iloader-auth-parity-source-evidence-v1', 'official_reference':gate.ILOADER_OFFICIAL_REFERENCE,
                'baseline_commit':'9' * 40, 'inputs':inputs,
                'outputs':{k:gate.digest((folder / k).read_bytes()) for k in gate.ILOADER_FILES},
                'output_closure':list(gate.ILOADER_FILES), 'boundary_replacements':['control double boundary'],
                'network':False, 'native_provider':False, 'swift_executed':False})
        elif name == 'onboarding-generate':
            folder = Path(argv[argv.index('--output-dir') + 1])
            self.assertEqual(Path(argv[argv.index('--sidesign-root') + 1]), self.sources / gate.SIGN)
            self.assertEqual(Path(argv[argv.index('--sidestore-root') + 1]), self.sources / 'SideStore')
            for file in gate.ONBOARDING_FILES:
                (folder / file).write_text('// generated control double')
            used = {}
            for relative in gate.ONBOARDING_REQUIRED_SOURCES:
                key = 'SideStore/Dependencies/' + relative if relative.startswith('SideSign/') else relative
                used[relative] = self.config['input_sha256'][key]
            used.update({k:v for k,v in self.config['input_sha256'].items() if k.startswith(gate.ONBOARDING + '/')})
            gate.write_json(folder / 'source-evidence.json', {
                'schema':'iloader-onboarding-scope-source-evidence-v1', 'inputs':used,
                'outputs':{k:gate.digest((folder / k).read_bytes()) for k in gate.ONBOARDING_FILES},
                'output_closure':list(gate.ONBOARDING_FILES), 'boundary_replacements':['control double boundary'],
                'network':False, 'native_provider':False, 'swift_executed':False})
        elif name == 'profile-generate':
            Path(argv[argv.index('--output')+1]).write_text('// generated control double')
            mappings = ['Sources/Models/AuthenticationHeaderProvenance.swift','Sources/Anisette/AnisetteDataManager.swift']
            gate.write_json(Path(argv[-1]), {'sources':{'SideSign/'+k:{'sha256':self.config['input_sha256'][gate.SIGN+'/'+k]} for k in mappings},
                'generated_sha256':gate.digest(Path(argv[argv.index('--output')+1]).read_bytes())})
        elif name == 'wire-generate':
            for file in ('service.swift','host.swift'):
                (Path(argv[-1])/file).write_text('// generated control double')
            mappings = gate.WIRE_INPUT_PATHS
            gate.write_json(Path(argv[-1])/'sources.json', {'input_sha256':{k:self.config['input_sha256'][v] for k,v in mappings.items()},
                'generated_sha256':{k:gate.digest((Path(argv[-1])/k).read_bytes()) for k in ('service.swift','host.swift')}})
        elif name == 'refresh-generate':
            root = Path(argv[argv.index('--output') + 1])
            (root / 'refresh-failure.swift').write_text('// inert refresh generator control double')
            inputs = {key: self.config['input_sha256'][path] for key, path in gate.REFRESH_INPUT_PATHS.items()}
            inputs.update({name: self.config['input_sha256'][gate.WIRE + '/' + name] for name in gate.REFRESH_FIXTURE_FILES})
            gate.write_json(root / 'sources.json', {
                'schema': 'refresh-failure-sources-v1', 'status': 'generated_not_compiled',
                'case_names': self.config['refresh_failure_case_names'], 'inputs_sha256': inputs,
                'generated_sha256': gate.digest((root / 'refresh-failure.swift').read_bytes()),
                'boundaries': ['actual-chain control double, never runtime evidence'],
                'native_provider_executed': False, 'network_executed': False, 'swift_executed': False})
        elif name == 'refresh-run':
            gate.write_json(Path(argv[-1]), {'schema': 'refresh-failure-cases-v1', 'status': 'pass',
                'fake_only': True, 'native_provider_executed': False,
                'cases': self.config['refresh_failure_case_names'],
                'case_count': len(self.config['refresh_failure_case_names'])})
            data = (gate.refresh_failure_marker(self.config) + '\n').encode()
        elif name == 'extension-generate':
            folder = Path(argv[-1])
            positive = '// inert control targetExtensions: appBundle.appExtensions,'
            (folder / 'Fixture.swift').write_text(positive)
            (folder / 'NegativeControl.swift').write_text(positive.replace('targetExtensions: appBundle.appExtensions,', 'targetExtensions: excessExtensions,'))
            gate.write_json(folder / 'source-receipt.json', {
                'schema': 2, 'case_names': self.config['extension_selection_case_names'],
                'static_wiring_checks': 'passed', 'swift_execution': 'not-run-by-generator',
                'native_signing_device_install': 'not-run',
                'source_sha256': {k: self.config['input_sha256'][v] for k, v in gate.EXTENSION_INPUT_PATHS.items()},
                'fixture_files_sha256': {n: self.config['input_sha256'][gate.EXTENSION + '/' + n] for n in gate.EXTENSION_FIXTURE_FILES},
                'generated_sha256': {n: gate.digest((folder / n).read_bytes()) for n in gate.EXTENSION_OUTPUTS},
                'fixture_sha256': gate.digest((folder / 'Fixture.swift').read_bytes())})
        elif name == 'extension-run':
            gate.write_json(Path(argv[-1]), {'schema': 1, 'count': 17,
                'native_signing_device_install': 'not-run', 'passed_cases': self.config['extension_selection_case_names']})
            data = (gate.EXTENSION_MARKER + '\n').encode()
        elif name == 'extension-negative-run':
            return subprocess.CompletedProcess(argv, -6, (gate.EXTENSION_NEGATIVE_MARKER + '\n').encode())
        elif name == 'recovery-generate':
            root = Path(argv[argv.index('--output') + 1])
            for relative in gate.PAIRING_RECOVERY_OUTPUTS:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('generated control-only recovery data')
            gate.write_json(root / 'sources.json', {
                'status': 'generated_not_compiled', 'schema': 'pairing-recovery-sources-v1',
                'case_names': self.config['pairing_recovery_case_names'],
                'input_sha256': {key: self.config['input_sha256'][path]
                                for key, path in gate.PAIRING_RECOVERY_INPUT_PATHS.items()},
                'fixture_inputs': {Path(name).name: self.config['input_sha256'][name]
                                   for name in gate.PAIRING_RECOVERY_FIXTURE_PATHS},
                'fixture_sha256': gate.digest((root / 'fixture.swift').read_bytes()),
                'baseline_store_sha256': gate.PAIRING_RECOVERY_BASELINE_STORE_SHA256,
                'output_sha256': {name: gate.digest((root / name).read_bytes()) for name in gate.PAIRING_RECOVERY_OUTPUTS},
                'owned_paths': ['actual path control double'], 'doubles': ['inert double']})
        elif name == 'recovery-run':
            gate.write_json(Path(argv[-1]), {'schema': 'pairing-recovery-cases-v1', 'status': 'pass',
                'fake_only': True, 'native_pairing_executed': False,
                'cases': self.config['pairing_recovery_case_names']})
            data = (gate.PAIRING_RECOVERY_MARKER + '\n').encode()
        elif name == 'deferred-prior-extract':
            root = Path(argv[argv.index('--output') + 1])
            for path in gate.SETUP_FIXTURES['deferred-device-setup']['sources']:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((self.sources / path).read_bytes())
            gate.write_json(Path(argv[argv.index('--receipt') + 1]), {
                'schema': 'deferred-prior-pem-sources-v1', 'origin': 'exact-local-git-objects',
                'owners': self.config['deferred_baseline']['owners'],
                'input_sha256': self.config['deferred_baseline']['input_sha256'],
                'network': False, 'target_execution': False})
        elif name == 'deferred-prior-run':
            self.assertEqual(argv[-1], '--expect-prior-pem-rejection')
            data = (gate.DEFERRED_PRIOR_MARKER + '\n').encode()
        elif name in ('pairing-generate', 'deferred-generate', 'deferred-prior-generate'):
            suite, spec = next((suite, spec) for suite, spec in gate.SETUP_FIXTURES.items()
                               if name == spec['command'] + '-generate' or
                               (name == 'deferred-prior-generate' and suite == 'deferred-device-setup'))
            folder = Path(argv[argv.index(spec['output_arg']) + 1])
            self.assertEqual(Path(argv[argv.index('--sources') + 1]),
                self.output / 'deferred-prior-pem/sources' if name == 'deferred-prior-generate' else self.sources)
            for file in spec['files']:
                (folder / file).write_text('// generated control double')
            used = {key: value for key, value in self.config['input_sha256'].items()
                    if key in spec['sources'] or key.startswith(spec['root'] + '/')}
            gate.write_json(folder / 'source-evidence.json', {
                'schema': suite + '-source-evidence-v1', 'inputs': used,
                'outputs': {key: gate.digest((folder / key).read_bytes()) for key in spec['files']},
                'output_closure': list(spec['files']), 'boundary_replacements': ['control double boundary'],
                'network': False, 'native_provider': False, 'swift_executed': False})
        elif name in ('pairing-run', 'deferred-run'):
            suite, spec = next((suite, spec) for suite, spec in gate.SETUP_FIXTURES.items()
                               if name == spec['command'] + '-run')
            gate.write_json(Path(argv[-1]), {'schema': suite + '-cases-v1', 'fake_only': True,
                                           'cases': ['control-case-a']})
            data = (self.markers[spec['command']] + '\n').encode()
        elif name.endswith('-compile'):
            Path(argv[-1]).write_bytes(b'not an executable; control double only')
        elif name == 'auth-run':
            gate.write_json(Path(argv[-1]), {'schema':'auth-request-cases-v1','producer':'control-only',
                                          'cases':[{'name':'case-a'}]})
            data = (self.markers['auth'] + '\n').encode()
        elif name == 'iloader-run':
            gate.write_json(Path(argv[-1]), {'schema':'iloader-auth-parity-cases-v1','fake_only':True,
                                          'cases':['control-case-a']})
            data = (self.markers['iloader'] + '\n').encode()
        elif name == 'onboarding-run':
            gate.write_json(Path(argv[-1]), {'schema':'iloader-onboarding-scope-cases-v1','fake_only':True,
                                          'cases':['control-case-a']})
            gate.write_json(Path(argv[-1]).with_name('registration-cases.json'), {
                'schema':gate.REGISTRATION_SCHEMA, 'fake_only':True, 'producer':'control-only',
                'cases':[{'name':'registration-case-a'}]})
            data = (self.markers['onboarding'] + '\n').encode()
        elif name == 'profile-run':
            gate.write_json(Path(argv[-1]), {'schema':'profile-provenance-cases-v1','producer':'control-only',
                                          'cases':[{'name':str(n)} for n in range(12)]})
            data = (self.markers['profile'] + '\n').encode()
        elif name == 'wire-service-run':
            self.assertEqual(Path(argv[2]), self.output/'auth-request/cases.json')
            self.assertEqual(Path(argv[3]), self.output/'profile-provenance/cases.json')
            self.assertEqual(Path(argv[4]), self.output/'iloader-onboarding-scope/registration-cases.json')
            Path(argv[1]).write_bytes(b'control-only wire output')
            data = ('WIRE_IMPORTED_CASES schema=auth-request-cases-v1 producer_cases=1 wire_cases=2\n'
                    'WIRE_IMPORTED_CASES schema=profile-provenance-cases-v1 producer_cases=12 wire_cases=24\n'
                    + f'WIRE_IMPORTED_CASES schema={gate.REGISTRATION_SCHEMA} producer_cases=1 wire_cases=2\n'
                    + self.markers['wire_service'] + '\n').encode()
        elif name == 'wire-host-run':
            self.assertEqual(Path(argv[1]), self.output/'wire-copy/wire-cases.plist')
            data = (self.markers['wire_host'] + '\n').encode()
        elif name == 'legacy-run':
            data = (self.markers['legacy'] + '\n').encode()
        return subprocess.CompletedProcess(argv, 0, data)

    def run_control(self, process=None, platform='darwin'):
        with patch.object(gate.sys, 'platform', platform), patch.object(gate.subprocess, 'run', side_effect=process or self.control_process):
            return gate.run(self.sources, self.swiftc, self.output, self.contract)

    def test_unbound_contract_rejected(self):
        self.config['source_inputs_bound'] = False
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'unbound'):
            gate.load_contract(self.contract)

    def test_missing_required_input_rejected(self):
        self.config['input_sha256'].pop(next(iter(self.config['input_sha256'])))
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            gate.load_contract(self.contract)

    def test_bad_sha_rejected(self):
        self.config['input_sha256'][next(iter(self.config['input_sha256']))] = 'PENDING'
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'Malformed'):
            gate.load_contract(self.contract)

    def test_unbound_success_marker_rejected(self):
        self.config['success_markers']['profile'] = 'PENDING'
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'Unbound'):
            gate.load_contract(self.contract)

    def test_wrong_schema_rejected(self):
        self.config['schemas']['profile'] = 'hp1'
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'schema'):
            gate.load_contract(self.contract)

    def test_path_traversal_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            gate.regular(self.sources, '../contract.json')

    def test_symlink_rejected(self):
        target = self.sources/'link'
        target.symlink_to(self.contract)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            gate.regular(self.sources, 'link')

    def test_missing_source_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Missing'):
            gate.regular(self.sources, 'missing')

    def test_source_byte_drift_rejected(self):
        (self.sources / next(iter(gate.REQUIRED_INPUTS))).write_text('drift')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            gate.source_inventory(self.sources, self.config)

    def test_extra_fixture_file_rejected(self):
        # Scratch bytecode must not be admitted by weakening the executable inventory.
        for relative in ('extra.py', 'extra.pyc', '__pycache__/extra.cpython-312.pyc'):
            with self.subTest(relative=relative):
                path = self.sources / gate.AUTH / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('unreviewed')
                try:
                    with self.assertRaisesRegex(ValueError, 'Unpinned'):
                        gate.source_inventory(self.sources, self.config)
                finally:
                    path.unlink()

    def test_missing_marker_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Missing or duplicate'):
            gate.require_marker('anything\n', 'PASS')

    def test_duplicate_marker_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Missing or duplicate'):
            gate.require_marker('PASS\nPASS\n', 'PASS')

    def test_marker_requires_exact_line(self):
        with self.assertRaises(ValueError):
            gate.require_marker('prefix PASS\n', 'PASS')
        gate.require_marker('PASS\n', 'PASS')

    def test_case_count_and_duplicate_name_rejected(self):
        path = self.root/'cases.json'
        gate.write_json(path, {'schema':'x','cases':[{'name':'a'},{'name':'a'}]})
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            gate.exported_cases(path,'x')
        gate.write_json(path, {'schema':'x','cases':[{'name':'a'}]})
        with self.assertRaisesRegex(ValueError, 'count'):
            gate.exported_cases(path,'x',12)

    def test_case_schema_rejected(self):
        path = self.root/'cases.json'
        gate.write_json(path, {'schema':'wrong','cases':[{'name':'a'}]})
        with self.assertRaisesRegex(ValueError, 'schema'):
            gate.exported_cases(path,'expected')

    def test_fresh_output_required(self):
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, 'fresh'):
            self.run_control()
        self.assertFalse(self.calls)

    def test_complete_control_order_and_case_interchange(self):
        report = self.run_control()
        self.assertEqual([name for name,_ in self.calls], list(gate.COMMANDS))
        self.assertEqual(report['status'], gate.STATUS)
        self.assertEqual([name for name,_ in self.calls].count('iloader-run'), 1)
        self.assertEqual(set(report['producer_exports']), {'auth-request', 'profile-provenance', 'device-registration'})
        self.assertEqual(set(report['fixture_exports']), {'iloader-auth-parity', 'iloader-onboarding-scope', *gate.SETUP_FIXTURES})
        self.assertEqual([name for name,_ in self.calls].count('onboarding-run'), 1)
        self.assertTrue(report['source_owned_fake_authentication_executed'])
        self.assertFalse(report['live_authentication_executed'])
        self.assertEqual(gate.verify_receipt(self.output/'report.json',self.contract), report)
        for name, argv in self.calls:
            if name.endswith('-compile'):
                self.assertNotIn('-L',argv)
                self.assertNotIn('-lAnisetteKit',argv)
                if name in ('deferred-prior-compile', 'deferred-compile'):
                    self.assertEqual([argv[i + 1] for i, word in enumerate(argv[:-1]) if word == '-framework'],
                                     ['Security', 'CryptoKit'])
                else:
                    self.assertNotIn('-framework',argv)

    def test_symlink_backed_temp_root_preserves_exact_case_paths(self):
        alias = self.root / 'temp-root-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        # Force the macOS-style alias on every platform, then exercise the same
        # setup and all command/receipt assertions rather than weakening equality.
        with patch.object(tempfile, 'tempdir', str(alias)):
            probe = Controls('test_complete_control_order_and_case_interchange')
            try:
                probe.setUp()
                self.assertNotEqual(Path(probe.temp.name), Path(probe.temp.name).resolve())
                probe.test_complete_control_order_and_case_interchange()
                self.assertEqual(probe.root, Path(probe.temp.name).resolve())
                self.assertEqual(probe.output, probe.root / 'evidence')
            finally:
                probe.doCleanups()

    def test_actual_failure_exit_code_retained(self):
        def fail(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 7, b'actual failure\n')
        with self.assertRaisesRegex(ValueError, 'exit=7'):
            self.run_control(fail)
        report = json.loads((self.output/'report.json').read_text())
        self.assertEqual(report['commands'][0]['exit_code'],7)
        self.assertEqual(report['status'],'FAILED')

    def test_missing_generated_swift_stops_before_compile(self):
        def missing(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'auth-generate':
                (self.output/'auth-request/RuntimeTests.swift').unlink()
            return result
        with self.assertRaisesRegex(ValueError, 'seven-file'):
            self.run_control(missing)
        self.assertNotIn('auth-compile',[name for name,_ in self.calls])

    def test_mutated_source_fails_final_immutability(self):
        def mutate(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'wire-host-run':
                (self.sources/gate.AUTH/'generate.py').write_text('changed')
            return result
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.run_control(mutate)
        self.assertEqual(json.loads((self.output/'report.json').read_text())['status'],'FAILED')

    def test_tampered_artifact_receipt_rejected(self):
        self.run_control()
        (self.output/'wire-copy/host.swift').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'hashes|receipt|evidence'):
            gate.verify_receipt(self.output/'report.json',self.contract)

    def test_missing_command_receipt_rejected(self):
        report = self.run_control()
        report['commands'].pop()
        gate.write_json(self.output/'report.json',report)
        with self.assertRaisesRegex(ValueError, 'command receipt'):
            gate.verify_receipt(self.output/'report.json',self.contract)

    def test_false_native_execution_receipt_rejected(self):
        report = self.run_control()
        report['guest_binary_executed'] = True
        gate.write_json(self.output/'report.json',report)
        with self.assertRaisesRegex(ValueError, 'Forbidden'):
            gate.verify_receipt(self.output/'report.json',self.contract)


    def test_missing_extraction_manifest_rejected(self):
        self.run_control()
        (self.output/'auth-request/source-evidence.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            gate.verify_receipt(self.output/'report.json',self.contract)

    def test_extraction_source_hash_mismatch_rejected(self):
        self.run_control()
        path = self.output/'wire-copy/sources.json'
        value = json.loads(path.read_text())
        value['input_sha256']['collector'] = '0' * 64
        gate.write_json(path,value)
        with self.assertRaisesRegex(ValueError, 'Wire extraction input evidence'):
            gate.extraction_receipt(self.output,self.config)

    def test_generated_source_hash_mismatch_rejected(self):
        self.run_control()
        (self.output/'auth-request/Errors.swift').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Auth generated-source hashes'):
            gate.extraction_receipt(self.output,self.config)



    def test_iloader_source_arguments_are_explicit(self):
        self.run_control()
        argv = dict(self.calls)['iloader-source']
        self.assertEqual(Path(argv[argv.index('--sidestore-root') + 1]), self.sources / 'SideStore')

    def test_iloader_missing_generated_swift_stops_before_compile(self):
        def missing(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'iloader-generate':
                (self.output/'iloader-auth-parity/ILoaderRemoteAnisette.swift').unlink()
            return result
        with self.assertRaisesRegex(ValueError, 'iLoader seven-file'):
            self.run_control(missing)
        self.assertNotIn('iloader-compile', [name for name,_ in self.calls])

    def test_iloader_extra_generated_swift_stops_before_compile(self):
        def extra(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'iloader-generate':
                (self.output/'iloader-auth-parity/Unexpected.swift').write_text('// unapproved')
            return result
        with self.assertRaisesRegex(ValueError, 'iLoader seven-file'):
            self.run_control(extra)

    def test_iloader_missing_or_duplicate_marker_rejected(self):
        for text in ('wrong', 'CONTROL_PASS_iloader\nCONTROL_PASS_iloader'):
            with self.subTest(text=text):
                probe = Controls('test_complete_control_order_and_case_interchange')
                try:
                    probe.setUp()
                    def bad(argv, **kwargs):
                        result = probe.control_process(argv, **kwargs)
                        if probe.calls[-1][0] == 'iloader-run':
                            return subprocess.CompletedProcess(argv, 0, (text + '\n').encode())
                        return result
                    with self.assertRaisesRegex(ValueError, 'Missing or duplicate'):
                        probe.run_control(bad)
                finally:
                    probe.doCleanups()

    def test_iloader_nonzero_exit_is_retained(self):
        def fail(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'iloader-run':
                return subprocess.CompletedProcess(argv, 9, b'fake failure\n')
            return result
        with self.assertRaisesRegex(ValueError, 'exit=9'):
            self.run_control(fail)
        report = json.loads((self.output/'report.json').read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(report['commands'][-1]['name'], 'iloader-run')
        self.assertEqual(report['commands'][-1]['exit_code'], 9)

    def test_iloader_unpinned_fixture_rejected(self):
        (self.sources / gate.ILOADER / 'extra.py').write_text('unexpected')
        with self.assertRaisesRegex(ValueError, 'Unpinned'):
            self.run_control()
        self.assertFalse(self.calls)

    def test_iloader_case_export_guards(self):
        path = self.root/'iloader-cases.json'
        valid = {'schema':'iloader-auth-parity-cases-v1','fake_only':True,'cases':['a']}
        for key,value in [('schema','wrong'),('fake_only',False),('cases',[]),('cases',['a','a']),('cases',[{'name':'a'}])]:
            bad = {**valid,key:value}
            gate.write_json(path,bad)
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                gate.iloader_exported_cases(path)

    def test_iloader_missing_manifest_rejected(self):
        self.run_control()
        (self.output/'iloader-auth-parity/source-evidence.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            gate.verify_receipt(self.output/'report.json',self.contract)

    def test_iloader_wrong_extracted_source_rejected(self):
        self.run_control()
        path = self.output/'iloader-auth-parity/source-evidence.json'
        value = json.loads(path.read_text())
        value['inputs']['Sources/Anisette/ILoaderRemoteAnisette.swift'] = '0'*64
        gate.write_json(path,value)
        with self.assertRaisesRegex(ValueError, 'iLoader extraction input hash'):
            gate.verify_receipt(self.output/'report.json',self.contract)

    def test_iloader_generator_execution_claim_rejected(self):
        self.run_control()
        path = self.output/'iloader-auth-parity/source-evidence.json'
        value = json.loads(path.read_text()); value['network'] = True; gate.write_json(path,value)
        with self.assertRaisesRegex(ValueError, 'Forbidden iLoader'):
            gate.verify_receipt(self.output/'report.json',self.contract)

    def test_iloader_missing_or_repeated_command_receipt_rejected(self):
        original = self.run_control()
        for duplicate in (False,True):
            report = copy.deepcopy(original)
            i = next(i for i,row in enumerate(report['commands']) if row['name']=='iloader-run')
            if duplicate: report['commands'].insert(i,copy.deepcopy(report['commands'][i]))
            else: report['commands'].pop(i)
            gate.write_json(self.output/'report.json',report)
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, 'command receipt'):
                gate.verify_receipt(self.output/'report.json',self.contract)

    def test_iloader_export_receipt_tamper_rejected(self):
        report = self.run_control()
        report['fixture_exports']['iloader-auth-parity']['count'] += 1
        gate.write_json(self.output/'report.json',report)
        with self.assertRaisesRegex(ValueError, 'iLoader fixture case receipt'):
            gate.verify_receipt(self.output/'report.json',self.contract)


    def test_onboarding_source_arguments_are_explicit(self):
        self.run_control()
        argv = dict(self.calls)['onboarding-source']
        self.assertEqual(Path(argv[argv.index('--sidestore-root') + 1]), self.sources / 'SideStore')
        self.assertEqual(Path(argv[argv.index('--sidesign-root') + 1]), self.sources / gate.SIGN)

    def test_onboarding_missing_generated_swift_stops_before_compile(self):
        def missing(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'onboarding-generate':
                (self.output/'iloader-onboarding-scope/CandidateOnboarding.swift').unlink()
            return result
        with self.assertRaisesRegex(ValueError, 'Onboarding three-file'):
            self.run_control(missing)
        self.assertNotIn('onboarding-compile', [name for name,_ in self.calls])

    def test_onboarding_extra_generated_swift_stops_before_compile(self):
        def extra(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'onboarding-generate':
                (self.output/'iloader-onboarding-scope/Unexpected.swift').write_text('// unapproved')
            return result
        with self.assertRaisesRegex(ValueError, 'Onboarding three-file'):
            self.run_control(extra)

    def test_onboarding_missing_or_duplicate_marker_rejected(self):
        for text in ('wrong', 'CONTROL_PASS_onboarding\nCONTROL_PASS_onboarding'):
            with self.subTest(text=text):
                probe = Controls('test_complete_control_order_and_case_interchange')
                try:
                    probe.setUp()
                    def bad(argv, **kwargs):
                        result = probe.control_process(argv, **kwargs)
                        if probe.calls[-1][0] == 'onboarding-run':
                            return subprocess.CompletedProcess(argv, 0, (text + '\n').encode())
                        return result
                    with self.assertRaisesRegex(ValueError, 'Missing or duplicate'):
                        probe.run_control(bad)
                finally:
                    probe.doCleanups()

    def test_onboarding_nonzero_exit_is_retained(self):
        def fail(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'onboarding-run':
                return subprocess.CompletedProcess(argv, 9, b'fake failure\n')
            return result
        with self.assertRaisesRegex(ValueError, 'exit=9'):
            self.run_control(fail)
        report = json.loads((self.output/'report.json').read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(report['commands'][-1]['name'], 'onboarding-run')
        self.assertEqual(report['commands'][-1]['exit_code'], 9)

    def test_onboarding_unpinned_fixture_rejected(self):
        (self.sources / gate.ONBOARDING / 'extra.py').write_text('unexpected')
        with self.assertRaisesRegex(ValueError, 'Unpinned'):
            self.run_control()
        self.assertFalse(self.calls)

    def test_onboarding_case_export_guards(self):
        path = self.root/'onboarding-cases.json'
        valid = {'schema':'iloader-onboarding-scope-cases-v1','fake_only':True,'cases':['a']}
        for key,value in [('schema','wrong'),('fake_only',False),('cases',[]),('cases',['a','a']),('cases',[{'name':'a'}])]:
            bad = {**valid,key:value}
            gate.write_json(path,bad)
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                gate.onboarding_exported_cases(path, self.config['onboarding_case_names'])

    def test_onboarding_missing_manifest_rejected(self):
        self.run_control()
        (self.output/'iloader-onboarding-scope/source-evidence.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            gate.verify_receipt(self.output/'report.json',self.contract)

    def test_onboarding_wrong_extracted_source_rejected(self):
        self.run_control()
        path = self.output/'iloader-onboarding-scope/source-evidence.json'
        value = json.loads(path.read_text())
        value['inputs']['SideSign/Sources/Anisette/ILoaderRemoteAnisette.swift'] = '0'*64
        gate.write_json(path,value)
        with self.assertRaisesRegex(ValueError, 'Onboarding extraction input hash'):
            gate.verify_receipt(self.output/'report.json',self.contract)

    def test_onboarding_generator_execution_claim_rejected(self):
        self.run_control()
        path = self.output/'iloader-onboarding-scope/source-evidence.json'
        value = json.loads(path.read_text()); value['network'] = True; gate.write_json(path,value)
        with self.assertRaisesRegex(ValueError, 'Forbidden Onboarding'):
            gate.verify_receipt(self.output/'report.json',self.contract)

    def test_onboarding_missing_or_repeated_command_receipt_rejected(self):
        original = self.run_control()
        for duplicate in (False,True):
            report = copy.deepcopy(original)
            i = next(i for i,row in enumerate(report['commands']) if row['name']=='onboarding-run')
            if duplicate: report['commands'].insert(i,copy.deepcopy(report['commands'][i]))
            else: report['commands'].pop(i)
            gate.write_json(self.output/'report.json',report)
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, 'command receipt'):
                gate.verify_receipt(self.output/'report.json',self.contract)

    def test_onboarding_export_receipt_tamper_rejected(self):
        report = self.run_control()
        report['fixture_exports']['iloader-onboarding-scope']['count'] += 1
        gate.write_json(self.output/'report.json',report)
        with self.assertRaisesRegex(ValueError, 'Onboarding fixture case receipt'):
            gate.verify_receipt(self.output/'report.json',self.contract)


    def test_onboarding_unbound_case_contract_stops_before_execution(self):
        for names in (None, [], ['a', 'a'], ['__PENDING_CASES__']):
            with self.subTest(names=names):
                self.config['onboarding_case_names'] = names
                self.save_contract()
                with self.assertRaisesRegex(ValueError, 'Onboarding case contract'):
                    self.run_control()
                self.assertFalse(self.calls)

    def test_onboarding_missing_expected_case_stops_before_following_suite(self):
        base = self.control_process
        def bad(argv, **kwargs):
            result = base(argv, **kwargs)
            if self.calls[-1][0] == 'onboarding-run':
                gate.write_json(Path(argv[-1]), {'schema':'iloader-onboarding-scope-cases-v1',
                                                'fake_only':True, 'cases':['unreviewed-replacement']})
            return result
        with self.assertRaisesRegex(ValueError, 'Onboarding case coverage'):
            self.run_control(bad)
        self.assertEqual(self.calls[-1][0], 'onboarding-run')
        self.assertEqual(json.loads((self.output/'report.json').read_text())['status'], 'FAILED')

    def test_onboarding_missing_proxy_or_fixture_input_rejected(self):
        self.run_control()
        path = self.output/'iloader-onboarding-scope/source-evidence.json'
        original = json.loads(path.read_text())
        for key, error in [('SideStore/SideStore/Core/Auth/DeveloperPortalProxy.swift', 'queued onboarding'),
                           (gate.ONBOARDING + '/generate.py', 'Unrecorded Onboarding')]:
            with self.subTest(key=key):
                value = copy.deepcopy(original); del value['inputs'][key]; gate.write_json(path,value)
                with self.assertRaisesRegex(ValueError,error):
                    gate.extraction_receipt(self.output,self.config)

    def test_onboarding_output_hash_and_closure_mismatch_rejected(self):
        self.run_control()
        path = self.output/'iloader-onboarding-scope/source-evidence.json'
        original = json.loads(path.read_text())
        value = copy.deepcopy(original); value['outputs']['CandidateOnboarding.swift'] = '0' * 64
        gate.write_json(path,value)
        with self.assertRaisesRegex(ValueError,'Onboarding generated-source hashes'):
            gate.extraction_receipt(self.output,self.config)
        value = copy.deepcopy(original); value['output_closure'].pop(); gate.write_json(path,value)
        with self.assertRaisesRegex(ValueError,'Onboarding generated-source closure'):
            gate.extraction_receipt(self.output,self.config)

    def test_onboarding_boundary_receipt_empty_or_duplicated_rejected(self):
        self.run_control()
        path = self.output/'iloader-onboarding-scope/source-evidence.json'
        original = json.loads(path.read_text())
        for boundaries in ([], ['same', 'same'], [None]):
            with self.subTest(boundaries=boundaries):
                value = copy.deepcopy(original); value['boundary_replacements'] = boundaries
                gate.write_json(path,value)
                with self.assertRaisesRegex(ValueError,'Onboarding boundary receipt'):
                    gate.extraction_receipt(self.output,self.config)

    def test_onboarding_unqualified_source_input_rejected(self):
        self.run_control()
        path = self.output/'iloader-onboarding-scope/source-evidence.json'
        value = json.loads(path.read_text()); value['inputs']['unqualified.swift'] = '0' * 64
        gate.write_json(path,value)
        with self.assertRaisesRegex(ValueError,'repository prefix'):
            gate.extraction_receipt(self.output,self.config)
        del value['inputs']['unqualified.swift']
        value['inputs']['SideStore/unpinned.swift'] = None
        gate.write_json(path,value)
        with self.assertRaisesRegex(ValueError,'Onboarding extraction input hash'):
            gate.extraction_receipt(self.output,self.config)


    def test_registration_case_contract_rejects_missing_or_duplicates(self):
        for names in (None, [], ['same', 'same'], ['PENDING'], [None]):
            with self.subTest(names=names):
                self.config['registration_case_names'] = names
                self.save_contract()
                with self.assertRaisesRegex(ValueError, 'Registration case contract'):
                    gate.load_contract(self.contract)

    def test_registration_export_schema_and_exact_names(self):
        path = self.root/'registration-cases.json'
        valid = {'schema':gate.REGISTRATION_SCHEMA, 'fake_only':True,
                 'producer':'control-only', 'cases':[{'name':'registration-case-a'}]}
        for patch_value in ({'schema':'wrong'}, {'fake_only':False}, {'cases':[]},
                            {'cases':[{'name':'registration-case-a'}, {'name':'registration-case-a'}]},
                            {'cases':[{'name':'wrong'}]}, {'cases':[{'name':None}]},
                            {'cases':[{'name':'registration-case-a'}, {'name':'extra'}]}):
            with self.subTest(patch_value=patch_value):
                gate.write_json(path, {**valid, **patch_value})
                with self.assertRaises(ValueError):
                    gate.registration_exported_cases(path, self.config['registration_case_names'])
        gate.write_json(path, valid)
        self.assertEqual(gate.registration_exported_cases(path, self.config['registration_case_names'])['count'], 1)

    def test_registration_missing_export_stops_before_profile(self):
        def missing(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'onboarding-run':
                (self.output/'iloader-onboarding-scope/registration-cases.json').unlink()
            return result
        with self.assertRaises(FileNotFoundError):
            self.run_control(missing)
        self.assertEqual(self.calls[-1][0], 'onboarding-run')

    def test_registration_wrong_export_stops_before_profile(self):
        def wrong(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'onboarding-run':
                gate.write_json(self.output/'iloader-onboarding-scope/registration-cases.json', {
                    'schema':gate.REGISTRATION_SCHEMA, 'fake_only':True, 'cases':[{'name':'wrong'}]})
            return result
        with self.assertRaisesRegex(ValueError, 'Registration case coverage'):
            self.run_control(wrong)
        self.assertEqual(self.calls[-1][0], 'onboarding-run')

    def test_registration_missing_wire_import_marker_stops_before_host(self):
        def missing(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'wire-service-run':
                result.stdout = result.stdout.replace(
                    f'WIRE_IMPORTED_CASES schema={gate.REGISTRATION_SCHEMA} producer_cases=1 wire_cases=2\n'.encode(), b'')
            return result
        with self.assertRaisesRegex(ValueError, 'Missing or duplicate exact success marker'):
            self.run_control(missing)
        self.assertEqual(self.calls[-1][0], 'wire-service-run')

    def test_registration_wrong_wire_multiplicity_stops_before_host(self):
        def wrong(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'wire-service-run':
                result.stdout = result.stdout.replace(
                    f'WIRE_IMPORTED_CASES schema={gate.REGISTRATION_SCHEMA} producer_cases=1 wire_cases=2'.encode(),
                    f'WIRE_IMPORTED_CASES schema={gate.REGISTRATION_SCHEMA} producer_cases=1 wire_cases=1'.encode())
            return result
        with self.assertRaisesRegex(ValueError, 'Missing or duplicate exact success marker'):
            self.run_control(wrong)
        self.assertEqual(self.calls[-1][0], 'wire-service-run')

    def test_registration_missing_retained_producer_receipt_rejected(self):
        report = self.run_control()
        del report['producer_exports']['device-registration']
        gate.write_json(self.output/'report.json', report)
        with self.assertRaisesRegex(ValueError, 'Registration producer case receipt'):
            gate.verify_receipt(self.output/'report.json', self.contract)

    def test_registration_mutated_export_is_rejected_even_with_updated_inventory(self):
        report = self.run_control()
        path = self.output/'iloader-onboarding-scope/registration-cases.json'
        value = json.loads(path.read_text()); value['producer'] = 'changed'
        gate.write_json(path, value)
        report['artifact_sha256'] = gate.artifact_inventory(self.output)
        gate.write_json(self.output/'report.json', report)
        with self.assertRaisesRegex(ValueError, 'Registration producer case receipt'):
            gate.verify_receipt(self.output/'report.json', self.contract)


    def test_registration_mapper_source_evidence_is_exact(self):
        self.run_control()
        path = self.output/'wire-copy/sources.json'
        original = json.loads(path.read_text())
        for key in ('operation_errors', 'portal_errors', 'portal_observers', 'anisette_errors',
                    'archive_errors', 'remote_errors', 'minimuxer_mapping',
                    'registration_doubles', 'registration_cases'):
            for action in ('remove', 'change'):
                with self.subTest(key=key, action=action):
                    value = copy.deepcopy(original)
                    if action == 'remove': del value['input_sha256'][key]
                    else: value['input_sha256'][key] = '0' * 64
                    gate.write_json(path, value)
                    with self.assertRaisesRegex(ValueError, 'Wire extraction input evidence'):
                        gate.extraction_receipt(self.output, self.config)


class SetupControls(Controls):
    # Inherit the control process, not the inherited test matrix: unittest's
    # loader below runs old Controls once, then this class's new checks once.
    def fresh(self):
        probe = Controls('test_complete_control_order_and_case_interchange')
        probe.setUp()
        self.addCleanup(probe.doCleanups)
        return probe

    def test_setup_new_suites_and_commands_once(self):
        report = self.run_control()
        self.assertEqual(len(gate.COMMANDS), len(set(gate.COMMANDS)))
        self.assertEqual(len(gate.COMMANDS), 49)
        for suite, spec in gate.SETUP_FIXTURES.items():
            self.assertEqual(report['suites'].count(suite), 1)
            for step in ('source', 'generate', 'compile', 'run'):
                name = spec['command'] + '-' + step
                self.assertEqual([name for name, _ in self.calls].count(name), 1)
            self.assertEqual(report['fixture_exports'][suite]['names'], ['control-case-a'])

    def test_setup_source_and_generator_root_arguments(self):
        self.run_control()
        for suite, spec in gate.SETUP_FIXTURES.items():
            for step in ('source', 'generate'):
                argv = dict(self.calls)[spec['command'] + '-' + step]
                self.assertEqual(Path(argv[argv.index('--sources') + 1]), self.sources)

    def test_setup_unbound_case_contract_stops_before_execution(self):
        for suite in gate.SETUP_FIXTURES:
            for names in (None, [], ['a', 'a'], ['__PENDING_CASES__']):
                probe = self.fresh()
                probe.config[suite + '_case_names'] = names
                probe.save_contract()
                with self.subTest(suite=suite, names=names), self.assertRaisesRegex(ValueError, 'setup case contract'):
                    probe.run_control()
                self.assertFalse(probe.calls)

    def test_setup_unpinned_fixture_stops_before_execution(self):
        for suite, spec in gate.SETUP_FIXTURES.items():
            probe = self.fresh()
            (probe.sources / spec['root'] / 'unapproved.py').write_text('not admitted')
            with self.subTest(suite=suite), self.assertRaisesRegex(ValueError, 'Unpinned'):
                probe.run_control()
            self.assertFalse(probe.calls)

    def test_setup_missing_extra_or_symlink_swift_stops_before_compile(self):
        for suite, spec in gate.SETUP_FIXTURES.items():
            for action in ('missing', 'extra', 'symlink'):
                probe = self.fresh()
                def bad(argv, **kwargs):
                    result = probe.control_process(argv, **kwargs)
                    if probe.calls[-1][0] == spec['command'] + '-generate':
                        path = probe.output / suite / spec['files'][0]
                        if action == 'extra':
                            (path.parent / 'Unapproved.swift').write_text('// not admitted')
                        else:
                            path.unlink()
                            if action == 'symlink':
                                path.symlink_to(probe.contract)
                    return result
                with self.subTest(suite=suite, action=action), self.assertRaisesRegex(ValueError, 'closure|Symlink'):
                    probe.run_control(bad)
                self.assertNotIn(spec['command'] + '-compile', [name for name, _ in probe.calls])

    def test_setup_manifest_guards_stop_before_compile(self):
        for suite, spec in gate.SETUP_FIXTURES.items():
            for action in ('schema', 'missing-input', 'wrong-input', 'output', 'closure', 'network',
                           'native_provider', 'swift_executed', 'boundaries'):
                probe = self.fresh()
                def bad(argv, **kwargs):
                    result = probe.control_process(argv, **kwargs)
                    if probe.calls[-1][0] == spec['command'] + '-generate':
                        path = probe.output / suite / 'source-evidence.json'
                        value = json.loads(path.read_text())
                        if action == 'schema': value['schema'] = 'wrong'
                        elif action == 'missing-input': value['inputs'].pop(spec['root'] + '/' + spec['generator'])
                        elif action == 'wrong-input': value['inputs'][spec['sources'][0]] = '0' * 64
                        elif action == 'output': value['outputs'][spec['files'][0]] = '0' * 64
                        elif action == 'closure': value['output_closure'].reverse()
                        elif action == 'boundaries': value['boundary_replacements'] = ['same', 'same']
                        else: value[action] = True
                        gate.write_json(path, value)
                    return result
                with self.subTest(suite=suite, action=action), self.assertRaisesRegex(ValueError, 'Setup|setup'):
                    probe.run_control(bad)
                self.assertNotIn(spec['command'] + '-compile', [name for name, _ in probe.calls])

    def test_setup_missing_or_duplicate_success_marker(self):
        for suite, spec in gate.SETUP_FIXTURES.items():
            for duplicate in (False, True):
                probe = self.fresh()
                def bad(argv, **kwargs):
                    result = probe.control_process(argv, **kwargs)
                    if probe.calls[-1][0] == spec['command'] + '-run':
                        data = result.stdout * 2 if duplicate else b'not a success marker'
                        return subprocess.CompletedProcess(argv, 0, data)
                    return result
                with self.subTest(suite=suite, duplicate=duplicate), self.assertRaisesRegex(ValueError, 'Missing or duplicate'):
                    probe.run_control(bad)

    def test_setup_failed_exit_retained(self):
        for suite, spec in gate.SETUP_FIXTURES.items():
            probe = self.fresh()
            def bad(argv, **kwargs):
                result = probe.control_process(argv, **kwargs)
                return subprocess.CompletedProcess(argv, 17, b'fixture rejected') if probe.calls[-1][0] == spec['command'] + '-run' else result
            with self.subTest(suite=suite), self.assertRaisesRegex(ValueError, 'exit=17'):
                probe.run_control(bad)
            value = json.loads((probe.output / 'report.json').read_text())
            self.assertEqual(value['status'], 'FAILED')
            self.assertEqual(value['commands'][-1]['exit_code'], 17)

    def test_setup_case_export_guards(self):
        path = self.root / 'setup-cases.json'
        for suite in gate.SETUP_FIXTURES:
            valid = {'schema': suite + '-cases-v1', 'fake_only': True, 'cases': ['control-case-a']}
            for key, value in [('schema', 'wrong'), ('fake_only', False), ('cases', []),
                               ('cases', ['a', 'a']), ('cases', [{'name': 'control-case-a'}]),
                               ('cases', ['unreviewed-case'])]:
                gate.write_json(path, {**valid, key: value})
                with self.subTest(suite=suite, key=key, value=value), self.assertRaises(ValueError):
                    gate.setup_exported_cases(path, suite, ['control-case-a'])

    def test_setup_export_change_rejected_after_artifact_rehash(self):
        for suite in gate.SETUP_FIXTURES:
            probe = self.fresh()
            report = probe.run_control()
            path = probe.output / suite / 'cases.json'
            value = json.loads(path.read_text()); value['metadata'] = 'changed'; gate.write_json(path, value)
            report['artifact_sha256'] = gate.artifact_inventory(probe.output)
            gate.write_json(probe.output / 'report.json', report)
            with self.subTest(suite=suite), self.assertRaisesRegex(ValueError, 'Setup fixture case receipt'):
                gate.verify_receipt(probe.output / 'report.json', probe.contract)

    def test_setup_no_missing_or_duplicate_commands_admitted(self):
        original = self.run_control()
        for suite, spec in gate.SETUP_FIXTURES.items():
            for duplicate in (False, True):
                report = copy.deepcopy(original)
                index = next(i for i, row in enumerate(report['commands']) if row['name'] == spec['command'] + '-run')
                if duplicate: report['commands'].insert(index, copy.deepcopy(report['commands'][index]))
                else: report['commands'].pop(index)
                gate.write_json(self.output / 'report.json', report)
                with self.subTest(suite=suite, duplicate=duplicate), self.assertRaisesRegex(ValueError, 'command receipt'):
                    gate.verify_receipt(self.output / 'report.json', self.contract)



    def test_crypto_frameworks_are_exact_and_baseline_is_once(self):
        report = self.run_control()
        for name in ('deferred-prior-compile', 'deferred-compile'):
            argv = dict(self.calls)[name]
            self.assertEqual([argv[i + 1] for i, word in enumerate(argv[:-1]) if word == '-framework'],
                             ['Security', 'CryptoKit'])
        self.assertEqual([name for name, _ in self.calls].count('deferred-prior-run'), 1)
        self.assertTrue(report['system_crypto_synthetic_fixture_executed'])
        self.assertFalse(report['system_crypto_boundary']['real_keychain_access'])

    def test_crypto_platform_guard_prevents_non_macos_compilation(self):
        with self.assertRaisesRegex(ValueError, 'requires macOS'):
            self.run_control(platform='linux')
        self.assertNotIn('deferred-prior-compile', dict(self.calls))
        self.assertNotIn('deferred-source', dict(self.calls))

    def test_crypto_prior_missing_marker_stops_before_candidate(self):
        def bad(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'deferred-prior-run':
                result.stdout = b'no prior failure reproduced'
            return result
        with self.assertRaisesRegex(ValueError, 'Missing or duplicate'):
            self.run_control(bad)
        self.assertNotIn('deferred-source', dict(self.calls))

    def test_crypto_prior_bad_source_hash_stops_before_compile(self):
        def bad(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'deferred-prior-generate':
                root = self.output / 'deferred-prior-pem/sources'
                (root / next(iter(self.config['deferred_baseline']['input_sha256']))).write_text('drift')
            return result
        with self.assertRaisesRegex(ValueError, 'Prior deferred source bytes differ'):
            self.run_control(bad)
        self.assertNotIn('deferred-prior-compile', dict(self.calls))

    def test_crypto_prior_contract_missing_input_or_wrong_owner_rejected(self):
        self.config['deferred_baseline']['input_sha256'].pop(next(iter(self.config['deferred_baseline']['input_sha256'])))
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'baseline source hashes'):
            self.run_control()
        self.assertFalse(self.calls)
        self.config['deferred_baseline']['owners']['SideStore']['commit'] = '0' * 40
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'baseline owners'):
            self.run_control()
        self.assertFalse(self.calls)

    def test_crypto_prior_generated_hash_change_rejected(self):
        def bad(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'deferred-prior-generate':
                (self.output / 'deferred-prior-pem/deferred-device-setup/CandidateDeferredSetup.swift').write_text('changed')
            return result
        with self.assertRaisesRegex(ValueError, 'generated-source hashes'):
            self.run_control(bad)
        self.assertNotIn('deferred-prior-compile', dict(self.calls))



    def test_pairing_recovery_case_contract_is_required(self):
        del self.config['pairing_recovery_case_names']
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'pairing recovery case contract'):
            self.run_control()
        self.assertFalse(self.calls)

    def test_pairing_recovery_bundle_hash_drift_stops_before_compile(self):
        def bad(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'recovery-generate':
                (self.output / 'pairing-recovery/TestLocalization.bundle/Info.plist').write_text('changed')
            return result
        with self.assertRaisesRegex(ValueError, 'Swift/bundle hashes'):
            self.run_control(bad)
        self.assertNotIn('recovery-compile', dict(self.calls))

    def test_pairing_recovery_fixture_hash_drift_stops_before_compile(self):
        def bad(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'recovery-generate':
                path = self.output / 'pairing-recovery/sources.json'
                value = json.loads(path.read_text())
                value['fixture_inputs']['generate_wire_fixture.py'] = '0' * 64
                gate.write_json(path, value)
            return result
        with self.assertRaisesRegex(ValueError, 'fixture input hashes'):
            self.run_control(bad)
        self.assertNotIn('recovery-compile', dict(self.calls))

    def test_pairing_recovery_real_pairing_claim_is_rejected(self):
        def bad(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'recovery-run':
                path = Path(argv[-1]); value = json.loads(path.read_text())
                value['native_pairing_executed'] = True; gate.write_json(path, value)
            return result
        with self.assertRaisesRegex(ValueError, 'runtime boundary'):
            self.run_control(bad)
        self.assertNotIn('deferred-prior-extract', dict(self.calls))

    def test_pairing_recovery_changed_case_export_cannot_be_rehashed_away(self):
        report = self.run_control()
        path = self.output / 'pairing-recovery/cases.json'
        value = json.loads(path.read_text()); value['cases'] = ['unreviewed']; gate.write_json(path, value)
        report['artifact_sha256'] = gate.artifact_inventory(self.output)
        gate.write_json(self.output / 'report.json', report)
        with self.assertRaisesRegex(ValueError, 'coverage differs'):
            gate.verify_receipt(self.output / 'report.json', self.contract)


class RefreshControls(Controls):
    # Run the inherited control matrix once, never once per extension subclass.
    def fresh(self):
        probe = Controls('test_complete_control_order_and_case_interchange')
        probe.setUp()
        self.addCleanup(probe.doCleanups)
        return probe

    def test_refresh_command_and_suite_uniqueness(self):
        report = self.run_control()
        self.assertEqual(len(report['suites']), 8)
        self.assertEqual(report['suites'].count('wire-copy'), 1)
        self.assertEqual(len(gate.COMMANDS), 49)
        self.assertEqual(len(set(gate.COMMANDS)), 49)
        for name in ('refresh-generate', 'refresh-compile', 'refresh-run'):
            self.assertEqual([n for n, _ in self.calls].count(name), 1)
        self.assertEqual(report['refresh_failure_cases']['names'], ['refresh-control-case'])

    def test_refresh_case_contract_rejected_before_execution(self):
        for names in (None, [], ['same', 'same'], ['__PENDING__']):
            probe = self.fresh()
            probe.config['refresh_failure_case_names'] = names
            probe.save_contract()
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, 'refresh failure case contract'):
                probe.run_control()
            self.assertFalse(probe.calls)

    def test_refresh_source_or_fixture_drift_stops_before_compile(self):
        for key in list(gate.REFRESH_INPUT_PATHS) + list(gate.REFRESH_FIXTURE_FILES):
            probe = self.fresh()
            def bad(argv, **kwargs):
                result = probe.control_process(argv, **kwargs)
                if probe.calls[-1][0] == 'refresh-generate':
                    path = probe.output / 'refresh-failure/sources.json'
                    value = json.loads(path.read_text())
                    value['inputs_sha256'][key] = '0' * 64
                    gate.write_json(path, value)
                return result
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'source/fixture hashes'):
                probe.run_control(bad)
            self.assertNotIn('refresh-compile', dict(probe.calls))

    def test_refresh_output_schema_and_boundary_guards(self):
        for change in ('schema', 'status', 'generated_sha256', 'case_names', 'boundaries',
                       'native_provider_executed', 'network_executed', 'swift_executed', 'extra'):
            probe = self.fresh()
            def bad(argv, **kwargs):
                result = probe.control_process(argv, **kwargs)
                if probe.calls[-1][0] == 'refresh-generate':
                    root = probe.output / 'refresh-failure'
                    path = root / 'sources.json'
                    value = json.loads(path.read_text())
                    if change == 'extra': (root / 'unreviewed.swift').write_text('// reject')
                    elif change == 'case_names': value[change] = ['unreviewed']
                    elif change == 'boundaries': value[change] = ['same', 'same']
                    elif change in ('native_provider_executed', 'network_executed', 'swift_executed'): value[change] = True
                    else: value[change] = 'wrong'
                    gate.write_json(path, value)
                return result
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'Refresh|refresh'):
                probe.run_control(bad)
            self.assertNotIn('refresh-compile', dict(probe.calls))

    def test_refresh_missing_or_duplicate_marker_rejected(self):
        for duplicate in (False, True):
            probe = self.fresh()
            def bad(argv, **kwargs):
                result = probe.control_process(argv, **kwargs)
                if probe.calls[-1][0] == 'refresh-run':
                    result.stdout = result.stdout * 2 if duplicate else b'no runtime success marker'
                return result
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, 'Missing or duplicate'):
                probe.run_control(bad)

    def test_refresh_runtime_export_rejected(self):
        for key, value in [('schema', 'wrong'), ('status', 'failed'), ('fake_only', False),
                           ('native_provider_executed', True), ('case_count', -1), ('cases', ['unreviewed'])]:
            probe = self.fresh()
            def bad(argv, **kwargs):
                result = probe.control_process(argv, **kwargs)
                if probe.calls[-1][0] == 'refresh-run':
                    path = Path(argv[-1]); data = json.loads(path.read_text())
                    data[key] = value; gate.write_json(path, data)
                return result
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Refresh|refresh|Setup'):
                probe.run_control(bad)
            self.assertNotIn('pairing-source', dict(probe.calls))

    def test_refresh_receipt_rehash_cannot_mask_case_changes(self):
        report = self.run_control()
        path = self.output / 'refresh-failure/cases.json'
        value = json.loads(path.read_text()); value['cases'] = ['unreviewed']; gate.write_json(path, value)
        report['artifact_sha256'] = gate.artifact_inventory(self.output)
        gate.write_json(self.output / 'report.json', report)
        with self.assertRaisesRegex(ValueError, 'coverage differs'):
            gate.verify_receipt(self.output / 'report.json', self.contract)

    def test_refresh_unpinned_native_boundary_fixture_rejected(self):
        (self.sources / gate.REFRESH_TEST_ROOT / 'unapproved.py').write_text('not admitted')
        with self.assertRaisesRegex(ValueError, 'Unpinned'):
            self.run_control()
        self.assertFalse(self.calls)


class ExtensionControls(RefreshControls):
    def test_extension_command_uniqueness(self):
        report = self.run_control()
        self.assertEqual(len(report['suites']), 8)
        self.assertEqual(len(gate.COMMANDS), 49)
        self.assertEqual(len(set(gate.COMMANDS)), 49)
        self.assertEqual(report['extension_selection_cases']['count'], 17)
        self.assertEqual(next(row['exit_code'] for row in report['commands'] if row['name'] == 'extension-negative-run'), -6)

    def test_extension_contract_required(self):
        for names in (None, [], ['same'] * 17):
            probe = self.fresh(); probe.config['extension_selection_case_names'] = names; probe.save_contract()
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, 'extension selection case contract'):
                probe.run_control()
            self.assertFalse(probe.calls)

    def test_extension_provenance_and_output_guard(self):
        for key in ('source_sha256', 'fixture_files_sha256', 'generated_sha256', 'case_names', 'schema', 'swift_execution'):
            probe = self.fresh()
            def bad(argv, **kwargs):
                result = probe.control_process(argv, **kwargs)
                if probe.calls[-1][0] == 'extension-generate':
                    path = Path(argv[-1]) / 'source-receipt.json'; value = json.loads(path.read_text())
                    value[key] = [] if key == 'case_names' else 'wrong'; gate.write_json(path, value)
                return result
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Extension'):
                probe.run_control(bad)
            self.assertNotIn('extension-compile', dict(probe.calls))

    def test_extension_negative_must_fail_exact_assertion(self):
        for mode in ('zero', 'wrong', 'timeout'):
            probe = self.fresh()
            def bad(argv, **kwargs):
                result = probe.control_process(argv, **kwargs)
                if probe.calls[-1][0] == 'extension-negative-run':
                    if mode == 'zero': result.returncode = 0
                    elif mode == 'wrong': result.stdout = b'unrelated crash'
                    else: raise subprocess.TimeoutExpired(argv, 300)
                return result
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'Command failed|Extension negative'):
                probe.run_control(bad)

    def test_extension_positive_marker_is_exact(self):
        for duplicate in (False, True):
            probe = self.fresh()
            def bad(argv, **kwargs):
                result = probe.control_process(argv, **kwargs)
                if probe.calls[-1][0] == 'extension-run': result.stdout = result.stdout * 2 if duplicate else b'no success'
                return result
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, 'Missing or duplicate'):
                probe.run_control(bad)

    def test_extension_runtime_case_export_guards(self):
        for key, value in [('schema', 3), ('count', 0), ('passed_cases', []), ('native_signing_device_install', 'run')]:
            probe = self.fresh()
            def bad(argv, **kwargs):
                result = probe.control_process(argv, **kwargs)
                if probe.calls[-1][0] == 'extension-run':
                    path = Path(argv[-1]); data = json.loads(path.read_text()); data[key] = value; gate.write_json(path, data)
                return result
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Extension runtime'):
                probe.run_control(bad)

    def test_extension_mutation_is_one_actual_handler_argument(self):
        def bad(argv, **kwargs):
            result = self.control_process(argv, **kwargs)
            if self.calls[-1][0] == 'extension-generate':
                root = Path(argv[-1]); (root / 'NegativeControl.swift').write_text('// wrong control')
                path = root / 'source-receipt.json'; value = json.loads(path.read_text())
                value['generated_sha256']['NegativeControl.swift'] = gate.digest((root / 'NegativeControl.swift').read_bytes())
                gate.write_json(path, value)
            return result
        with self.assertRaisesRegex(ValueError, 'Extension negative mutation'):
            self.run_control(bad)

    def test_extension_expected_failure_cannot_mask_other_failure(self):
        self.assertFalse(gate.command_passed({'name': 'refresh-run', 'exit_code': -6,
            'expected_failure_marker': gate.EXTENSION_NEGATIVE_MARKER}))
        report = self.run_control()
        row = next(row for row in report['commands'] if row['name'] == 'extension-negative-run')
        row['exit_code'] = 0
        gate.write_json(self.output / 'report.json', report)
        with self.assertRaisesRegex(ValueError, 'Incomplete successful command'):
            gate.verify_receipt(self.output / 'report.json', self.contract)


def load_tests(loader, tests, pattern):
    suite = loader.loadTestsFromTestCase(Controls)
    suite.addTests(SetupControls(name) for name in sorted(SetupControls.__dict__) if name.startswith('test_'))
    suite.addTests(RefreshControls(name) for name in sorted(RefreshControls.__dict__) if name.startswith('test_'))
    suite.addTests(ExtensionControls(name) for name in sorted(ExtensionControls.__dict__) if name.startswith('test_'))
    return suite


if __name__ == '__main__':
    unittest.main(verbosity=2)
