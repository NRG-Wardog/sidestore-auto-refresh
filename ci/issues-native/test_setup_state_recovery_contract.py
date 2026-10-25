"""Gate-control tests use inert local files and subprocess doubles, never Swift or native execution."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import verify_setup_state_recovery as gate


class Controls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sources, self.output = self.root / 'sources', self.root / 'output'
        self.sources.mkdir()
        self.swiftc = self.root / 'inert-compiler'
        self.swiftc.write_text('This is not executable and is never launched.')
        required = set(gate.LOCALIZATION_INPUTS.values()) | {gate.LOCALIZATION + '/' + n for n in gate.LOCALIZATION_FILES}
        required |= {gate.EARLY + '/test_contract.py', gate.PORTAL + '/test_generator.py'}
        for spec in (*gate.SUITE_INPUTS.values(), *gate.ADDITIVE_SUITE_INPUTS.values()):
            for names in spec.values():
                required.update(names)
        for path in required:
            target = self.sources / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('inert current source: ' + path)
        baseline = set(gate.LOCALIZATION_INPUTS.values()).union(*(set(v['production']) for v in gate.SUITE_INPUTS.values()))
        self.baseline_bytes = {p: ('inert delivered source: ' + p).encode() for p in baseline}
        self.contract = {'schema': 'setup-state-recovery-contract-v1', 'source_inputs_bound': True,
            'suites': list(gate.SUITES), 'suite_inputs': copy.deepcopy(gate.SUITE_INPUTS),
            'additive_suite_inputs': copy.deepcopy(gate.ADDITIVE_SUITE_INPUTS),
            'input_sha256': {p: gate.digest((self.sources / p).read_bytes()) for p in required},
            'delivered_baseline': {'owners': copy.deepcopy(gate.BASELINE_OWNERS),
                                  'input_sha256': {p: gate.digest(raw) for p, raw in self.baseline_bytes.items()}},
            'case_names': {suite: [suite + '-case-' + str(i) for i in range(count)] for suite, count in
                           [('setup-localization', gate.LOCALIZATION_CASE_COUNT), ('early-refresh-boundaries', 24),
                            ('certificate-portal-evidence', 19), ('remote-identity-reuse', gate.IDENTITY_CASE_COUNT),
                            *((suite, spec['case_count']) for suite, spec in gate.ADDITIVE_SPECS.items())]}}
        self.contract_path = self.root / 'contract.json'
        self.save_contract()
        self.mutation = None
        self.command_names = []

    def save_contract(self):
        gate.write_json(self.contract_path, self.contract)

    def baseline_output(self, root, receipt):
        for p, raw in self.baseline_bytes.items():
            target = root / p
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        gate.write_json(receipt, {'schema': 'setup-delivered-baseline-v1', 'origin': 'exact-local-git-objects',
            **self.contract['delivered_baseline'], 'network': False, 'target_execution': False})

    def variant_output(self, suite, root):
        for variant in ('baseline', 'candidate'):
            folder = root / variant
            folder.mkdir(parents=True)
            spec = gate.SUITE_INPUTS[suite]
            inventory = self.contract['delivered_baseline']['input_sha256'] if variant == 'baseline' else self.contract['input_sha256']
            value = {'variant': variant, 'status': 'GENERATED_NOT_COMPILED_NOT_RUN',
                'swift_executed': False, 'network_executed': False, 'native_provider_executed': False,
                'expected_case_names': self.contract['case_names'][suite],
                'expected_case_count': len(self.contract['case_names'][suite]),
                'inputs_sha256': {p: inventory[p] for p in spec['production']},
                'support_inputs_sha256': {p: self.contract['input_sha256'][p] for p in spec['support']}}
            if suite == 'early-refresh-boundaries':
                (folder / 'early-refresh.swift').write_text('inert early ' + variant)
                value.update(schema='early-refresh-source-extraction-v1',
                    admitted_route_body_extracted=variant == 'candidate',
                    generated_sha256=gate.digest((folder / 'early-refresh.swift').read_bytes()),
                    fixture_inputs_sha256={p: self.contract['input_sha256'][p] for p in spec['fixture']})
            else:
                for name in ['host.swift', 'service.swift']:
                    (folder / name).write_text('inert portal ' + variant + ' ' + name)
                value.update(schema='certificate-portal-evidence-sources-v2',
                    generated_sha256={n: gate.digest((folder / n).read_bytes()) for n in ['host.swift', 'service.swift']})
            gate.write_json(folder / 'sources.json', value)
        if suite == 'early-refresh-boundaries':
            gate.write_json(root / 'contract.json', {'schema': 'early-refresh-portable-contract-v1',
                'status': 'STATIC_PASS_RUNTIME_NOT_RUN', 'check_count': 102,
                'checks': ['control-' + str(i) for i in range(102)], 'expected_generic_baseline_cases': 12,
                'variants': ['baseline', 'candidate'], 'swift_compiled': False, 'swift_executed': False})
        else:
            gate.write_json(root / 'static-controls.json', {'schema': 'portal-portability-controls-v1',
                'status': 'pass', 'check_count': 6, 'checks': ['control-' + str(i) for i in range(6)],
                'swift_executed': False, 'network_executed': False})

    def subprocess(self, argv, **kwargs):
        argv = list(map(str, argv))
        if Path(argv[0]).is_relative_to(self.output.resolve()):
            # Identify runtimes by their actual executable, not a sequential index:
            # a blocked portal host is deliberately never launched.
            relative = Path(argv[0]).relative_to(self.output.resolve()).as_posix()
            runtime_names = {'setup-localization/fixture': 'localization-run',
                             'remote-identity-reuse/fixture': 'identity-run'}
            runtime_names.update({suite + '/fixture': spec['prefix'] + '-run'
                                  for suite, spec in gate.ADDITIVE_SPECS.items()})
            for variant in ('baseline', 'candidate'):
                runtime_names['early-refresh-boundaries/' + variant + '/fixture'] = 'early-' + variant + '-run'
                runtime_names['certificate-portal-evidence/' + variant + '/service'] = 'portal-' + variant + '-run'
                runtime_names['certificate-portal-evidence/' + variant + '/host'] = 'portal-' + variant + '-host-run'
            name = runtime_names[relative]
        else:
            name = gate.COMMANDS[len(self.command_names)]
        self.command_names.append(name)
        data = (gate.markers().get(name, 'inert generation success') + '\n').encode()
        if name == 'delivered-baseline-extract':
            self.assertIn('extract-baseline', argv)
            self.baseline_output(Path(argv[argv.index('--output') + 1]), Path(argv[argv.index('--receipt') + 1]))
        elif name == 'localization-source':
            folder = Path(argv[argv.index('--output') + 1]); folder.mkdir()
            (folder / 'fixture.swift').write_text('inert localization fixture')
            (folder / 'baseline_missing_keys.json').write_text('[]')
            gate.write_json(folder / 'sources.json', {'schema': 'setup-localization-sources-v1',
                'status': 'generated_not_compiled',
                'source_sha256': {k: self.contract['input_sha256'][p] for k, p in gate.LOCALIZATION_INPUTS.items()},
                'baseline_sha256': {k: self.contract['delivered_baseline']['input_sha256'][p] for k, p in gate.LOCALIZATION_INPUTS.items()},
                'fixture_inputs': {n: self.contract['input_sha256'][gate.LOCALIZATION + '/' + n] for n in gate.LOCALIZATION_FILES},
                'key_count': gate.LOCALIZATION_KEY_COUNT, 'localized_value_count': gate.LOCALIZATION_VALUE_COUNT, 'expected_native_case_count': gate.LOCALIZATION_CASE_COUNT,
                'expected_case_names': self.contract['case_names']['setup-localization'],
                'retained_baseline_copy': gate.retained_baseline_copy(self.contract),
                'output_sha256': {p.name: gate.digest(p.read_bytes()) for p in folder.iterdir()}})
            gate.write_json(folder / 'static-contract.json', {'schema': 'setup-localization-static-contract-v1',
                'status': 'pass', 'candidate_key_coverage': 'pass', 'baseline_key_coverage': 'fail_as_expected',
                'retained_baseline_copy': gate.retained_baseline_copy(self.contract),
                'exact_connection_scope_negative_control': 'unrelated_cancel_mutation_rejected',
                'baseline_missing': list(range(gate.LOCALIZATION_BASELINE_MISSING)), 'missing_translation_mutation_cases': gate.LOCALIZATION_VALUE_COUNT,
                'identity_diagnostic_keys': ['inert-finite-key-' + str(i) for i in range(6)],
                'source_sha256': {k: self.contract['input_sha256'][p] for k, p in gate.LOCALIZATION_INPUTS.items()},
                'baseline_sha256': {k: self.contract['delivered_baseline']['input_sha256'][p] for k, p in gate.LOCALIZATION_INPUTS.items()}})
        elif name == 'identity-controls':
            pass
        elif name == 'identity-source':
            folder = Path(argv[argv.index('--output') + 1]); folder.mkdir()
            for filename in gate.IDENTITY_SWIFT:
                (folder / filename).write_text('inert identity ' + filename)
            spec = gate.SUITE_INPUTS['remote-identity-reuse']
            gate.write_json(folder / 'source-evidence.json', {
                'schema': 'remote-identity-reuse-source-evidence-v1', 'status': 'GENERATED_NOT_COMPILED_NOT_RUN',
                'swift_executed': False, 'network_executed': False,
                'native_provider_executed': False, 'native_keychain_executed': False,
                'inputs_sha256': {p: self.contract['input_sha256'][p] for p in spec['production']},
                'baseline_inputs_sha256': {p: self.contract['delivered_baseline']['input_sha256'][p] for p in spec['production']},
                'support_inputs_sha256': {p: self.contract['input_sha256'][p] for p in spec['support']},
                'outputs_sha256': {p.name: gate.digest(p.read_bytes()) for p in folder.iterdir()},
                'expected_case_names': self.contract['case_names']['remote-identity-reuse'],
                'candidate_source_wiring': {name: True for name in gate.IDENTITY_SOURCE_WIRING},
                'baseline_source_gaps': {'protected_identity_record_absent': True,
                    'protected_identity_adapter_absent': True, 'attempt_identity_only': True},
                'production_extracts': [{'source': spec['production'][0], 'anchor': 'inert',
                                        'first_line': 1, 'sha256': '0' * 64}]})
        elif name == 'identity-run':
            gate.write_json(Path(argv[-1]), {'schema': 'remote-identity-reuse-cases-v1', 'status': 'pass',
                'case_count': gate.IDENTITY_CASE_COUNT, 'cases': self.contract['case_names']['remote-identity-reuse'],
                'fake_only': True, 'network_executed': False, 'native_provider_executed': False,
                'native_keychain_executed': False})
        elif name in ['early-source', 'portal-source']:
            suite = 'early-refresh-boundaries' if name == 'early-source' else 'certificate-portal-evidence'
            self.variant_output(suite, Path(argv[argv.index('--output') + 1]))
        elif name in [spec['prefix'] + '-source' for spec in gate.ADDITIVE_SPECS.values()]:
            suite, spec = next((suite, spec) for suite, spec in gate.ADDITIVE_SPECS.items()
                               if spec['prefix'] + '-source' == name)
            folder = Path(argv[argv.index('--output') + 1]); folder.mkdir()
            (folder / spec['swift']).write_text('inert additive ' + suite)
            inputs = gate.ADDITIVE_SUITE_INPUTS[suite]
            value = {'schema': spec['source_schema'], 'status': 'STATIC_PASS_GENERATED_NOT_COMPILED_NOT_RUN',
                'swift_compiled': False, 'swift_executed': False, 'network_executed': False,
                'native_provider_executed': False,
                'expected_case_names': self.contract['case_names'][suite], 'expected_case_count': spec['case_count'],
                'inputs_sha256': {p: self.contract['input_sha256'][p] for p in inputs['production']},
                'support_inputs_sha256': {p: self.contract['input_sha256'][p] for p in inputs['support']},
                'fixture_inputs_sha256': {Path(p).name: self.contract['input_sha256'][p] for p in inputs['fixture']},
                'generated_sha256': gate.digest((folder / spec['swift']).read_bytes())}
            if suite == 'vpn-refresh-recovery':
                prefix = 'SideStore/Dependencies/minimuxer/'
                value['support_inputs_sha256'] = {p[len(prefix):]: self.contract['input_sha256'][p]
                                                 for p in inputs['support']}
                value['transport_fixture_inputs_sha256'] = {
                    p: self.contract['input_sha256'][p] for p in inputs['transport_fixture']}
                value['fixture_observation_hook'] = gate.VPN_OBSERVATION_HOOK
                value['cold_guard_provenance'] = 'actual_unchanged_guard_blocks_and_historical_baseline'
                value['baseline_classifier_sha256'] = self.contract['input_sha256'][
                    gate.VPN_RECOVERY + '/OriginalRefreshReadinessFacts.swift']
            if suite == 'setup-connection-evidence':
                value.update(host_activation_message_contract='exact_finite_guard_both_mirrors_before_generic_timeout',
                             host_activation_negative_static_mutations=list(gate.ACTIVATION_NEGATIVE_MUTATIONS))
            if suite == 'profile-preconditions':
                value.update(check_count=25, checks=['profile-check-' + str(i) for i in range(25)])
            if suite == 'host-refresh-bundle':
                self.assertEqual(Path(argv[argv.index('--candidate-root') + 1]).resolve(strict=True),
                                 (self.sources / 'SideStore').resolve(strict=True))
                value = {'schema': spec['source_schema'], 'pass': True,
                    'swift_executed': False, 'network_executed': False, 'provider_executed': False,
                    'device_executed': False, 'source_assertion_count': 34,
                    'expected_case_names': self.contract['case_names'][suite], 'expected_case_count': 30,
                    'inputs': [{'path': p.removeprefix('SideStore/'), 'sha256': self.contract['input_sha256'][p]}
                               for p in inputs['production']],
                    'support': [{'path': p.removeprefix('SideStore/'), 'sha256': self.contract['input_sha256'][p]}
                                for p in inputs['fixture']],
                    'generated_source': {'path': spec['swift'],
                        'sha256': gate.digest((folder / spec['swift']).read_bytes())}}
            if suite == 'cache-compatibility':
                value.update({key: True for key in ('strict_compute_byte_identical', 'host_pipeline_byte_identical', 'execute_and_pruning_byte_identical', 'original_cache_key_byte_identical_except_symbol', 'original_cache_flow_except_retained_safety_boundaries_symbol_and_logs', 'copy_helper_overwrite_disabled', 'cache_components_lstat_no_follow', 'production_darwin_import_present', 'trusted_apps_root_canonicalized_once', 'alias_regression_uses_production_getter', 'alias_before_reference_byte_identical')})
            gate.write_json(folder / spec['receipt'], value)
        elif name in [spec['prefix'] + '-run' for spec in gate.ADDITIVE_SPECS.values()]:
            suite, spec = next((suite, spec) for suite, spec in gate.ADDITIVE_SPECS.items()
                               if spec['prefix'] + '-run' == name)
            if suite == 'host-refresh-bundle':
                self.assertEqual(len(argv), 1)
                data = json.dumps({'schema': spec['cases_schema'], 'pass': True,
                    'cases': self.contract['case_names'][suite], 'case_count': spec['case_count'],
                    'network_executed': False, 'provider_executed': False, 'device_executed': False}).encode() + b'\n'
            else:
                gate.write_json(Path(argv[-1]), {'schema': spec['cases_schema'], 'status': 'pass',
                'fake_only': True, 'foundation_crypto_real': True,
                    'cases': self.contract['case_names'][suite], 'case_count': spec['case_count'],
                    'network_executed': False, 'native_provider_executed': False})
        elif name.endswith('-compile'):
            if name.startswith('portal-') and '-host-' not in name:
                self.assertEqual(argv[argv.index('-module-name') + 1], 'SideSign')
            Path(argv[-1]).write_text('inert non-executable compiler output')
        elif name == 'localization-run':
            gate.write_json(Path(argv[-1]) / 'cases.json', {'schema': 'setup-localization-cases-v1', 'status': 'pass',
                'case_count': gate.LOCALIZATION_CASE_COUNT, 'cases': self.contract['case_names']['setup-localization']})
        elif name.startswith('early-') and name.endswith('-run'):
            variant = name.split('-')[1]
            gate.write_json(Path(argv[-1]), {'schema': 'early-refresh-cases-v1', 'status': 'pass',
                'variant': variant, 'case_count': 24, 'cases': self.contract['case_names']['early-refresh-boundaries'],
                'network_executed': False, 'native_provider_executed': False})
        elif name.startswith('portal-') and name.endswith('-host-run'):
            variant = name.split('-')[1]
            gate.write_json(Path(argv[-1]), {'schema': 'portal-evidence-runtime-v1', 'status': 'pass',
                'candidate': variant == 'candidate', 'case_count': 19,
                'cases': self.contract['case_names']['certificate-portal-evidence'],
                'fake_only': True, 'network_executed': False, 'native_provider_executed': False})
        elif name.startswith('portal-') and name.endswith('-run'):
            Path(argv[-1]).write_bytes(b'inert plist placeholder, never native data')
        else:
            raise AssertionError(name)
        if self.mutation:
            altered = self.mutation(name, argv, data)
            if altered is not None:
                return altered
        return subprocess.CompletedProcess(argv, 0, data)

    def run_gate(self):
        with mock.patch.object(gate.subprocess, 'run', side_effect=self.subprocess):
            return gate.run(self.sources, self.swiftc, self.output, self.contract_path)

    def rewrite_json(self, path, modify):
        value = json.loads(path.read_text()); modify(value); gate.write_json(path, value)

    def test_success_all_commands_once_with_no_real_processes(self):
        report = self.run_gate()
        self.assertEqual(self.command_names, list(gate.COMMANDS))
        self.assertEqual(len(set(self.command_names)), len(gate.COMMANDS))
        self.assertEqual(len(self.command_names), 37)
        self.assertEqual(self.command_names[:11], ['delivered-baseline-extract', 'localization-source',
            'early-source', 'portal-source', 'identity-controls', 'identity-source',
            'connection-source', 'profile-preconditions-source', 'vpn-recovery-source', 'host-refresh-bundle-source', 'cache-compatibility-source'])
        self.assertEqual(self.command_names[11:24], ['localization-compile', 'early-baseline-compile',
            'early-candidate-compile', 'portal-baseline-compile', 'portal-baseline-host-compile',
            'portal-candidate-compile', 'portal-candidate-host-compile', 'identity-compile',
            'connection-compile', 'profile-preconditions-compile', 'vpn-recovery-compile', 'host-refresh-bundle-compile', 'cache-compatibility-compile'])
        self.assertTrue(all(name.endswith('-run') for name in self.command_names[24:]))
        for variant in ('baseline', 'candidate'):
            self.assertLess(self.command_names.index('portal-' + variant + '-run'),
                            self.command_names.index('portal-' + variant + '-host-run'))
        self.assertEqual(report['status'], gate.STATUS)
        self.assertNotIn('runtime_failures', report)
        self.assertNotIn('blocked_runtime_dependencies', report)
        self.assertFalse(report['native_provider_executed'])
        gate.verify_receipt(self.output / 'report.json', self.contract_path)


    def test_host_bundle_accepts_canonical_parent_symlink_alias(self):
        # macOS temp roots can be spelled /var/... or /private/var/....
        # Only the parent is aliased; direct symlink source roots remain denied.
        alias = self.root / 'parent-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        self.sources = alias / 'sources'
        self.output = alias / 'output'
        self.swiftc = alias / 'inert-compiler'
        self.contract_path = alias / 'contract.json'
        self.assertNotEqual(self.sources, self.sources.resolve(strict=True))
        self.assertFalse(self.sources.is_symlink())
        report = self.run_gate()
        self.assertEqual(report['status'], gate.STATUS)
        self.assertEqual(self.command_names, list(gate.COMMANDS))
        source = next(r for r in report['commands'] if r['name'] == 'host-refresh-bundle-source')
        actual = Path(source['argv'][source['argv'].index('--candidate-root') + 1])
        self.assertEqual(actual, (self.sources / 'SideStore').resolve(strict=True))

    def test_host_bundle_exact_compile_and_stdout_interface(self):
        report = self.run_gate()
        row = next(r for r in report['commands'] if r['name'] == 'host-refresh-bundle-compile')
        self.assertEqual(row['argv'][1:5], ['-swift-version', '5', '-framework', 'CryptoKit'])
        self.assertNotIn('-parse-as-library', row['argv'])
        row = next(r for r in report['commands'] if r['name'] == 'host-refresh-bundle-run')
        self.assertEqual(len(row['argv']), 1)
        self.assertEqual(report['evidence']['cases']['host-refresh-bundle']['case_count'], 30)

    def test_host_bundle_rejects_absolute_source_override(self):
        def mutate(name, argv, data):
            if name == 'host-refresh-bundle-source':
                self.rewrite_json(self.output / 'host-refresh-bundle/sources.json',
                    lambda value: value['inputs'][1].update(path='/tmp/fingerprint.swift'))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'exact source path or hash closure'):
            self.run_gate()
        self.assertFalse(any(n.endswith('-compile') for n in self.command_names))

    def test_host_bundle_rejects_missing_static_assertion(self):
        def mutate(name, argv, data):
            if name == 'host-refresh-bundle-source':
                self.rewrite_json(self.output / 'host-refresh-bundle/sources.json',
                    lambda value: value.update(source_assertion_count=33))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'static assertion or case closure'):
            self.run_gate()

    def test_host_bundle_rejects_runtime_provider_execution(self):
        def mutate(name, argv, data):
            if name == 'host-refresh-bundle-run':
                value = json.loads(data); value['provider_executed'] = True
                return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode())
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'Forbidden host bundle runtime boundary'):
            self.run_gate()

    def test_host_bundle_rejects_runtime_case_omission(self):
        def mutate(name, argv, data):
            if name == 'host-refresh-bundle-run':
                value = json.loads(data); value['cases'].pop()
                return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode())
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'ordered case closure'):
            self.run_gate()

    def test_cache_exact_source_compile_and_runtime_interface(self):
        # Exercise the macOS /var versus /private/var parent-alias shape locally.
        # Direct source symlinks are still rejected by the unchanged gate.
        alias = self.root / 'cache-parent-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        self.sources = alias / 'sources'
        self.output = alias / 'output'
        self.swiftc = alias / 'inert-compiler'
        self.contract_path = alias / 'contract.json'
        self.assertNotEqual(self.output, self.output.resolve())
        self.assertFalse(self.sources.is_symlink())
        report = self.run_gate()
        source = next(r for r in report['commands'] if r['name'] == 'cache-compatibility-source')
        actual = Path(source['argv'][source['argv'].index('--candidate-root') + 1])
        self.assertEqual(actual, (self.sources / 'SideStore').resolve(strict=True))
        row = next(r for r in report['commands'] if r['name'] == 'cache-compatibility-compile')
        self.assertEqual(row['argv'][1:5], ['-swift-version', '5', '-framework', 'CryptoKit'])
        self.assertNotIn('-parse-as-library', row['argv'])
        row = next(r for r in report['commands'] if r['name'] == 'cache-compatibility-run')
        self.assertEqual(len(row['argv']), 2)
        self.assertEqual(Path(row['argv'][1]).resolve(strict=True),
                         (self.output / 'cache-compatibility/cases.json').resolve(strict=True))
        self.assertEqual(report['evidence']['cases']['cache-compatibility']['case_count'], gate.ADDITIVE_SPECS['cache-compatibility']['case_count'])

    def test_cache_rejects_changed_source_hash(self):
        def mutate(name, argv, data):
            if name == 'cache-compatibility-source':
                self.rewrite_json(self.output / 'cache-compatibility/sources.json',
                    lambda value: value['inputs_sha256'].update({gate.ADDITIVE_SUITE_INPUTS['cache-compatibility']['production'][0]: '0' * 64}))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'Additive production source hashes differ'):
            self.run_gate()
        self.assertFalse(any(n.endswith('-compile') for n in self.command_names))

    def test_cache_rejects_case_omission(self):
        def mutate(name, argv, data):
            if name == 'cache-compatibility-run':
                self.rewrite_json(self.output / 'cache-compatibility/cases.json', lambda value: value['cases'].pop())
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'Fixture case closure differs'):
            self.run_gate()

    def test_cache_rejects_real_provider_claim(self):
        def mutate(name, argv, data):
            if name == 'cache-compatibility-run':
                self.rewrite_json(self.output / 'cache-compatibility/cases.json', lambda value: value.update(native_provider_executed=True))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'Fixture runtime boundary differs'):
            self.run_gate()

    def test_cache_rejects_missing_success_marker(self):
        def mutate(name, argv, data):
            if name == 'cache-compatibility-run':
                return subprocess.CompletedProcess(argv, 0, b'inert no marker\n')
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'Setup runtime commands failed'):
            self.run_gate()

    def test_cache_rejects_weakened_host_static_proof(self):
        def mutate(name, argv, data):
            if name == 'cache-compatibility-source':
                self.rewrite_json(self.output / 'cache-compatibility/sources.json', lambda value: value.update(strict_compute_byte_identical=False))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'Cache compatibility static proof differs'):
            self.run_gate()
        self.assertFalse(any(n.endswith('-compile') for n in self.command_names))

    def test_unbound_contract_stops_before_subprocess(self):
        self.contract['source_inputs_bound'] = False; self.save_contract()
        with self.assertRaisesRegex(ValueError, 'remain unbound'):
            self.run_gate()
        self.assertFalse(self.command_names)

    def test_candidate_source_drift_rejected(self):
        (self.sources / next(iter(self.contract['input_sha256']))).write_text('drift')
        with self.assertRaisesRegex(ValueError, 'hashes differ'):
            self.run_gate()

    def test_baseline_owner_drift_rejected(self):
        self.contract['delivered_baseline']['owners']['LiveContainer']['commit'] = '0' * 40; self.save_contract()
        with self.assertRaisesRegex(ValueError, 'baseline commit/tree'):
            self.run_gate()

    def test_baseline_path_omission_rejected(self):
        self.contract['delivered_baseline']['input_sha256'].pop(next(iter(self.baseline_bytes)));self.save_contract()
        with self.assertRaisesRegex(ValueError, 'Incomplete delivered baseline'):
            self.run_gate()

    def test_extra_unpinned_fixture_rejected(self):
        (self.sources / gate.EARLY / 'unreviewed.py').write_text('inert')
        with self.assertRaisesRegex(ValueError, 'Unpinned setup fixture'):
            self.run_gate()

    def test_symlink_fixture_rejected(self):
        (self.sources / gate.PORTAL / 'alias.py').symlink_to(self.swiftc)
        with self.assertRaisesRegex(ValueError, 'Symlink setup fixture'):
            self.run_gate()

    def test_nonzero_command_persists_failure_receipt(self):
        self.mutation = lambda n, a, d: subprocess.CompletedProcess(a, 9, b'failed') if n == 'early-baseline-run' else None
        with self.assertRaisesRegex(ValueError, 'Setup command failed'):
            self.run_gate()
        report = json.loads((self.output / 'report.json').read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(next(row for row in report['commands'] if row['name'] == 'early-baseline-run')['exit_code'], 9)
        self.assertEqual([name for name in self.command_names if name.endswith('-compile')],
                         list(gate.COMPILE_COMMANDS))
        self.assertEqual(self.command_names, list(gate.COMMANDS))
        self.assertEqual([row['name'] for row in report['runtime_failures']], ['early-baseline-run'])
        self.assertEqual(report['blocked_runtime_dependencies'], [])
        with mock.patch.object(gate.subprocess, 'run') as process:
            for name in ('localization-source', 'localization-run'):
                with self.assertRaisesRegex(ValueError, 'Only setup compile commands'):
                    gate.Runner(self.root).command(name, ['inert'], collect_compile_failure=True)
            process.assert_not_called()

    def test_independent_runtime_failures_collected_and_failed_service_blocks_host(self):
        nonzero = {'localization-run': 7, 'portal-baseline-run': 9, 'identity-run': 17}
        def mutate(name, argv, data):
            if name in nonzero:
                return subprocess.CompletedProcess(argv, nonzero[name], ('failed ' + name).encode())
            if name == 'early-baseline-run':
                raise subprocess.TimeoutExpired(argv, 300, output=b'partial runtime diagnostics')
            if name == 'early-candidate-run':
                raise OSError('inert runtime launch failure')
            if name == 'portal-candidate-host-run':
                return subprocess.CompletedProcess(argv, 0, data + data)
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'Setup runtime commands failed'):
            self.run_gate()
        expected = [name for name in gate.COMMANDS if name != 'portal-baseline-host-run']
        self.assertEqual(self.command_names, expected)
        self.assertEqual(len(set(self.command_names)), len(expected))
        report = json.loads((self.output / 'report.json').read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertFalse(report['source_owned_fixtures_executed'])
        self.assertEqual([row['name'] for row in report['commands']], expected)
        self.assertEqual([row['name'] for row in report['runtime_failures']], ['localization-run',
            'early-baseline-run', 'early-candidate-run', 'portal-baseline-run',
            'portal-candidate-host-run', 'identity-run'])
        self.assertEqual(report['blocked_runtime_dependencies'], [{'name': 'portal-baseline-host-run',
            'dependency': 'portal-baseline-run', 'reason': 'Service command or required success marker failed'}])
        # Even an existing partial output does not permit this dependent host.
        self.assertTrue((self.output / 'certificate-portal-evidence/baseline/cases.plist').is_file())
        self.assertFalse((self.output / 'certificate-portal-evidence/baseline/results.json').exists())
        self.assertFalse((self.output / 'commands/portal-baseline-host-run.log').exists())
        rows = {row['name']: row for row in report['commands']}
        for name, code in nonzero.items():
            self.assertEqual(rows[name]['exit_code'], code)
        self.assertTrue(rows['early-baseline-run']['timed_out'])
        self.assertEqual(rows['early-candidate-run']['launch_error'], 'OSError')
        self.assertEqual(rows['portal-candidate-host-run']['exit_code'], 0)
        self.assertNotIn('success_marker', rows['portal-candidate-host-run'])
        for row in rows.values():
            self.assertEqual(row['stdout_sha256'], gate.digest((self.output / row['log']).read_bytes()))
        with self.assertRaisesRegex(ValueError, 'Setup-state fixtures did not pass'):
            gate.verify_receipt(self.output / 'report.json', self.contract_path)

    def test_failed_service_marker_blocks_only_its_own_host(self):
        self.mutation = lambda n, a, d: subprocess.CompletedProcess(a, 0, d + d) if n == 'portal-baseline-run' else None
        with self.assertRaisesRegex(ValueError, 'duplicate exact success marker'):
            self.run_gate()
        self.assertEqual(self.command_names, [name for name in gate.COMMANDS if name != 'portal-baseline-host-run'])
        report = json.loads((self.output / 'report.json').read_text())
        self.assertEqual([row['name'] for row in report['runtime_failures']], ['portal-baseline-run'])
        self.assertEqual([row['name'] for row in report['blocked_runtime_dependencies']], ['portal-baseline-host-run'])
        self.assertIn('portal-candidate-host-run', self.command_names)
        self.assertIn('identity-run', self.command_names)

    def test_all_compile_failures_collected_before_any_runtime_or_admission(self):
        nonzero = {'early-baseline-compile': 9, 'portal-candidate-host-compile': 17}
        def mutate(name, argv, data):
            if name in nonzero:
                return subprocess.CompletedProcess(argv, nonzero[name], ('failed ' + name).encode())
            if name == 'early-candidate-compile':
                raise subprocess.TimeoutExpired(argv, 300, output=b'partial compiler diagnostics')
            if name == 'identity-compile':
                raise OSError('inert compiler launch failure')
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'Setup compile commands failed'):
            self.run_gate()
        self.assertEqual(self.command_names, list(gate.COMMANDS[:24]))
        self.assertEqual(len(set(self.command_names)), 24)
        self.assertFalse(any(name.endswith('-run') for name in self.command_names))
        report = json.loads((self.output / 'report.json').read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertFalse(report['source_owned_fixtures_executed'])
        self.assertEqual(report['suites'], [])
        rows = {row['name']: row for row in report['commands']}
        for name in gate.COMPILE_COMMANDS:
            row = rows[name]
            self.assertEqual(row['stdout_sha256'], gate.digest((self.output / row['log']).read_bytes()))
            self.assertNotIn('success_marker', row)
            if name in nonzero:
                self.assertEqual(row['exit_code'], nonzero[name])
            elif name == 'early-candidate-compile':
                self.assertIsNone(row['exit_code'])
                self.assertTrue(row['timed_out'])
                self.assertIn(b'partial compiler diagnostics', (self.output / row['log']).read_bytes())
            elif name == 'identity-compile':
                self.assertIsNone(row['exit_code'])
                self.assertEqual(row['launch_error'], 'OSError')
            else:
                self.assertEqual(row['exit_code'], 0)
        for name in (*nonzero, 'early-candidate-compile', 'identity-compile'):
            self.assertIn(name, report['failure'])
        with self.assertRaisesRegex(ValueError, 'Setup-state fixtures did not pass'):
            gate.verify_receipt(self.output / 'report.json', self.contract_path)

    def test_duplicate_marker_rejected(self):
        # Exact line emitted by admitted focused and full native VPN89 fixtures.
        actual_vpn_line = 'VPN_REFRESH_RECOVERY_PASS cases=89\n'
        vpn_marker = gate.markers()['vpn-recovery-run']
        self.assertEqual(gate.ADDITIVE_SPECS['vpn-refresh-recovery']['case_count'], 89)
        self.assertEqual(vpn_marker, 'VPN_REFRESH_RECOVERY_PASS cases=89')
        gate.require_marker(actual_vpn_line, vpn_marker)
        for invalid in ('VPN_REFRESH_RECOVERY_PASS cases=49\n', 'VPN_REFRESH_RECOVERY_PASS cases=72\n', actual_vpn_line + actual_vpn_line):
            with self.assertRaisesRegex(ValueError, 'Missing or duplicate exact success marker'):
                gate.require_marker(invalid, vpn_marker)
        self.mutation = lambda n, a, d: subprocess.CompletedProcess(a, 0, d + d) if n == 'localization-run' else None
        with self.assertRaisesRegex(ValueError, 'duplicate exact success marker'):
            self.run_gate()

    def test_early_baseline_candidate_source_swap_rejected(self):
        def mutate(name, argv, data):
            if name == 'early-source':
                path = self.output / 'early-refresh-boundaries/baseline/sources.json'
                self.rewrite_json(path, lambda v: v.update(inputs_sha256={p: self.contract['input_sha256'][p]
                    for p in gate.SUITE_INPUTS['early-refresh-boundaries']['production']}))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'actual-source inputs differ'):
            self.run_gate()

    def test_missing_admitted_runner_helper_rejected(self):
        def mutate(name, argv, data):
            if name == 'early-source':
                self.rewrite_json(self.output / 'early-refresh-boundaries/candidate/sources.json',
                    lambda v: v.update(admitted_route_body_extracted=False))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'admitted-route helper extraction'):
            self.run_gate()
        self.assertNotIn('early-baseline-compile', self.command_names)

    def test_generated_swift_tampering_rejected_before_compile(self):
        def mutate(name, argv, data):
            if name == 'portal-source':
                (self.output / 'certificate-portal-evidence/baseline/service.swift').write_text('drift')
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'generated source bytes differ'):
            self.run_gate()
        self.assertNotIn('portal-baseline-compile', self.command_names)
        self.assertFalse(any(name.endswith('-compile') or name.endswith('-run') for name in self.command_names))

    def test_missing_finite_identity_translation_contract_rejected(self):
        def mutate(name, argv, data):
            if name == 'localization-source':
                self.rewrite_json(self.output / 'setup-localization/static-contract.json',
                                  lambda v: v['identity_diagnostic_keys'].pop())
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'static/negative-control receipt'):
            self.run_gate()
        self.assertNotIn('localization-compile', self.command_names)

    def test_identity_baseline_source_swap_rejected(self):
        def mutate(name, argv, data):
            if name == 'identity-source':
                path = self.output / 'remote-identity-reuse/source-evidence.json'
                self.rewrite_json(path, lambda v: v.update(baseline_inputs_sha256=v['inputs_sha256']))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'Identity actual-source input closure'):
            self.run_gate()
        self.assertNotIn('identity-compile', self.command_names)

    def test_identity_baseline_feature_gap_rejected(self):
        def mutate(name, argv, data):
            if name == 'identity-source':
                self.rewrite_json(self.output / 'remote-identity-reuse/source-evidence.json',
                    lambda v: v['baseline_source_gaps'].update(protected_identity_record_absent=False))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'baseline identity negative controls'):
            self.run_gate()
        self.assertNotIn('identity-compile', self.command_names)

    def test_identity_missing_source_wiring_rejected(self):
        def mutate(name, argv, data):
            if name == 'identity-source':
                self.rewrite_json(self.output / 'remote-identity-reuse/source-evidence.json',
                    lambda v: v['candidate_source_wiring'].pop(gate.IDENTITY_SOURCE_WIRING[0]))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'exact source-wiring controls'):
            self.run_gate()
        self.assertNotIn('identity-compile', self.command_names)

    def test_identity_extra_swift_rejected(self):
        def mutate(name, argv, data):
            if name == 'identity-source':
                (self.output / 'remote-identity-reuse/unreviewed.swift').write_text('inert')
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'Identity generated Swift closure'):
            self.run_gate()

    def test_identity_native_keychain_claim_rejected(self):
        def mutate(name, argv, data):
            if name == 'identity-run':
                self.rewrite_json(Path(argv[-1]), lambda v: v.update(native_keychain_executed=True))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'runtime boundary differs'):
            self.run_gate()

    def test_identity_required_framework_closure_rejected(self):
        report = self.run_gate()
        row = next(r for r in report['commands'] if r['name'] == 'identity-compile')
        row['argv'][row['argv'].index('Security')] = 'Network'
        gate.write_json(self.output / 'report.json', report)
        with self.assertRaisesRegex(ValueError, 'Identity compile-only argument closure'):
            gate.verify_receipt(self.output / 'report.json', self.contract_path)

    def test_missing_runtime_case_rejected(self):
        def mutate(name, argv, data):
            if name == 'early-candidate-run':
                self.rewrite_json(Path(argv[-1]), lambda v: v['cases'].pop())
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'case closure differs'):
            self.run_gate()

    def test_duplicate_runtime_case_rejected(self):
        def mutate(name, argv, data):
            if name == 'portal-candidate-host-run':
                self.rewrite_json(Path(argv[-1]), lambda v: v.update(cases=[v['cases'][0]] * 19))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'case closure differs'):
            self.run_gate()

    def test_native_provider_claim_rejected(self):
        def mutate(name, argv, data):
            if name == 'portal-baseline-host-run':
                self.rewrite_json(Path(argv[-1]), lambda v: v.update(native_provider_executed=True))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'runtime boundary differs'):
            self.run_gate()

    def test_source_mutation_after_fixture_rejected(self):
        def mutate(name, argv, data):
            if name == 'portal-candidate-host-run':
                (self.sources / next(iter(self.contract['input_sha256']))).write_text('drift')
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'source hashes differ'):
            self.run_gate()

    def test_retained_artifact_tampering_rejected(self):
        self.run_gate()
        (self.output / 'early-refresh-boundaries/candidate/fixture').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'retained artifact changed'):
            gate.verify_receipt(self.output / 'report.json', self.contract_path)

    def test_missing_command_receipt_rejected(self):
        report = self.run_gate()
        self.rewrite_json(self.output / 'report.json', lambda v: v['commands'].pop())
        with self.assertRaisesRegex(ValueError, 'Missing or repeated setup command'):
            gate.verify_receipt(self.output / 'report.json', self.contract_path)
        for key in ('runtime_failures', 'blocked_runtime_dependencies'):
            with self.subTest(key=key):
                forged = copy.deepcopy(report)
                forged[key] = [{'name': 'portal-baseline-host-run', 'failure': 'inert failure'}]
                gate.write_json(self.output / 'report.json', forged)
                with self.assertRaisesRegex(ValueError, 'runtime failures or blocked dependencies remain'):
                    gate.verify_receipt(self.output / 'report.json', self.contract_path)

    def test_portal_module_identity_receipt_drift_rejected(self):
        self.run_gate()
        def modify(value):
            command = next(r for r in value['commands'] if r['name'] == 'portal-candidate-compile')
            command['argv'][command['argv'].index('-module-name') + 1] = 'UnreviewedModule'
        self.rewrite_json(self.output / 'report.json', modify)
        with self.assertRaisesRegex(ValueError, 'argument closure differs'):
            gate.verify_receipt(self.output / 'report.json', self.contract_path)

    def test_additive_input_closure_must_be_explicit(self):
        self.contract['additive_suite_inputs']['profile-preconditions']['fixture'].pop()
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'Additive exact source path closure'):
            self.run_gate()
        self.assertFalse(self.command_names)

    def test_independent_source_failures_are_collected_before_compilation(self):
        failures = {'localization-source': 7, 'early-source': 9, 'connection-source': 17, 'vpn-recovery-source': 19}
        self.mutation = lambda n, a, d: subprocess.CompletedProcess(a, failures[n], b'failed source') if n in failures else None
        with self.assertRaisesRegex(ValueError, 'Setup source commands failed'):
            self.run_gate()
        self.assertEqual(self.command_names, list(gate.COMMANDS[:11]))
        report = json.loads((self.output / 'report.json').read_text())
        self.assertEqual([row['name'] for row in report['source_failures']], list(failures))
        self.assertEqual(report['status'], 'FAILED')
        self.assertFalse(any(n.endswith('-compile') or n.endswith('-run') for n in self.command_names))

    def test_additive_compile_failures_collected_with_inherited_failures(self):
        failures = {'localization-compile': 7, 'connection-compile': 9, 'profile-preconditions-compile': 17, 'vpn-recovery-compile': 19}
        self.mutation = lambda n, a, d: subprocess.CompletedProcess(a, failures[n], b'failed compile') if n in failures else None
        with self.assertRaisesRegex(ValueError, 'Setup compile commands failed'):
            self.run_gate()
        self.assertEqual(self.command_names, list(gate.COMMANDS[:24]))
        report = json.loads((self.output / 'report.json').read_text())
        self.assertTrue(all(n in report['failure'] for n in failures))
        self.assertEqual(report['status'], 'FAILED')

    def test_additive_runtime_failures_are_collected_with_inherited_failures(self):
        failures = {'early-candidate-run': 7, 'connection-run': 9, 'profile-preconditions-run': 17, 'vpn-recovery-run': 19}
        self.mutation = lambda n, a, d: subprocess.CompletedProcess(a, failures[n], b'failed runtime') if n in failures else None
        with self.assertRaisesRegex(ValueError, 'Setup runtime commands failed'):
            self.run_gate()
        self.assertEqual(self.command_names, list(gate.COMMANDS))
        report = json.loads((self.output / 'report.json').read_text())
        self.assertEqual([row['name'] for row in report['runtime_failures']], list(failures))
        self.assertEqual(report['status'], 'FAILED')

    def test_additive_cases_require_exact_order(self):
        def mutate(name, argv, data):
            if name == 'profile-preconditions-run':
                self.rewrite_json(Path(argv[-1]), lambda v: v['cases'].reverse())
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'ordered case closure differs'):
            self.run_gate()

    def test_additive_source_and_runtime_privacy_claims_rejected(self):
        for command, field, expected in [('connection-source', 'network_executed', 'Unbounded additive generation'),
                                        ('profile-preconditions-run', 'native_provider_executed', 'runtime boundary differs')]:
            with self.subTest(command=command):
                self.setUp()
                def mutate(name, argv, data):
                    if name == command:
                        path = (self.output / 'setup-connection-evidence/sources.json') if name.endswith('-source') else Path(argv[-1])
                        self.rewrite_json(path, lambda v: v.update({field: True}))
                self.mutation = mutate
                with self.assertRaisesRegex(ValueError, expected):
                    self.run_gate()

    def test_additive_fixture_hash_drift_rejected(self):
        # Preserve the existing fixture drift case and bind new VPN proof boundaries.
        for field, invalid, expected in (
                ('connection_fixture', None, 'Additive fixture source hashes'),
                ('support_inputs_sha256', {}, 'Additive support source hashes'),
                ('support_inputs_sha256', {'SideStore/Dependencies/minimuxer/Sources/MinimuxerImpl.swift': '0' * 64}, 'Additive support source hashes'),
                ('fixture_observation_hook', {}, 'VPN deterministic observation hook'),
                ('transport_fixture_inputs_sha256', {}, 'VPN transport fixture exact'),
                ('transport_fixture_inputs_sha256', {'SideStore/tests/vpn_transport_recovery/unknown.py': '0' * 64}, 'VPN transport fixture exact'),
                ('cold_guard_provenance', 'pinned_source_receipt_sparse_generation', 'VPN actual dependency'),
                ('baseline_classifier_sha256', '0' * 64, 'VPN actual dependency')):
            with self.subTest(field=field, invalid=invalid):
                self.setUp()
                def mutate(name, argv, data):
                    if field == 'connection_fixture' and name == 'connection-source':
                        self.rewrite_json(self.output / 'setup-connection-evidence/sources.json',
                                          lambda v: v['fixture_inputs_sha256'].update({'cases.swift': '0' * 64}))
                    elif field != 'connection_fixture' and name == 'vpn-recovery-source':
                        self.rewrite_json(self.output / 'vpn-refresh-recovery/sources.json',
                                          lambda v: v.update({field: invalid}))
                self.mutation = mutate
                with self.assertRaisesRegex(ValueError, expected):
                    self.run_gate()
                self.assertFalse(any(n.endswith('-compile') for n in self.command_names))

    def test_additive_generated_swift_tampering_rejected(self):
        def mutate(name, argv, data):
            if name == 'profile-preconditions-source':
                (self.output / 'profile-preconditions/profile-preconditions.swift').write_text('changed')
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'generated Swift closure or bytes differ'):
            self.run_gate()

    def test_additive_unpinned_package_file_rejected(self):
        (self.sources / gate.CONNECTION / 'unreviewed.py').write_text('inert')
        with self.assertRaisesRegex(ValueError, 'Unpinned setup fixture'):
            self.run_gate()
        self.assertFalse(self.command_names)

    def test_forged_success_with_source_failures_rejected(self):
        self.run_gate()
        self.rewrite_json(self.output / 'report.json', lambda v: v.update(source_failures=[{'name': 'connection-source'}]))
        with self.assertRaisesRegex(ValueError, 'source generation failures remain'):
            gate.verify_receipt(self.output / 'report.json', self.contract_path)

    def test_missing_activation_negative_control_rejected(self):
        def mutate(name, argv, data):
            if name == 'connection-source':
                self.rewrite_json(self.output / 'setup-connection-evidence/sources.json',
                                  lambda v: v['host_activation_negative_static_mutations'].pop())
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'finite activation negative controls differ'):
            self.run_gate()
        self.assertFalse(any(n.endswith('-compile') for n in self.command_names))

    def test_retained_baseline_copy_cannot_claim_current_ui_producer(self):
        def mutate(name, argv, data):
            if name == 'localization-source':
                self.rewrite_json(self.output / 'setup-localization/sources.json',
                    lambda v: next(iter(v['retained_baseline_copy'].values())).update(emitted_by_current_ui=True))
        self.mutation = mutate
        with self.assertRaisesRegex(ValueError, 'Inherited baseline copy provenance differs'):
            self.run_gate()

    def use_source_parent_alias(self):
        # Reproduce macOS /var -> /private/var spelling on every platform.
        alias = self.root / 'fixture-parent-alias'
        alias.symlink_to(self.root.resolve(), target_is_directory=True)
        self.sources = alias / 'sources'
        self.assertFalse(self.sources.is_symlink())
        self.assertNotEqual(self.sources, self.sources.resolve())

    def test_local_git_object_extractor_has_no_network_and_uses_nested_owner(self):
        self.use_source_parent_alias()
        for owner in gate.BASELINE_OWNERS:
            (self.sources / owner / '.git').mkdir(parents=True)
        calls = []
        def fake_git(argv, **kwargs):
            calls.append(argv)
            self.assertEqual(kwargs['env']['GIT_ALLOW_PROTOCOL'], '')
            self.assertEqual(kwargs['env']['GIT_NO_LAZY_FETCH'], '1')
            self.assertEqual(kwargs['env']['GIT_NO_REPLACE_OBJECTS'], '1')
            root = Path(argv[argv.index('-C') + 1])
            owner = root.relative_to(self.sources.resolve()).as_posix()
            action, argument = argv[-2:]
            self.assertIn(action, ['cat-file', 'rev-parse', 'show']) if action != '-t' else None
            if '-t' in argv:
                data = b'commit\n'
            elif 'rev-parse' in argv:
                data = (gate.BASELINE_OWNERS[owner]['tree'] + '\n').encode()
            else:
                commit, relative = argument.split(':', 1)
                self.assertEqual(commit, gate.BASELINE_OWNERS[owner]['commit'])
                data = self.baseline_bytes[owner + '/' + relative]
            return subprocess.CompletedProcess(argv, 0, data)
        with mock.patch.object(gate.subprocess, 'run', side_effect=fake_git):
            gate.extract_baseline(self.sources, self.contract, self.output / 'delivered-baseline',
                                  self.output / 'delivered-baseline.json')
        gate.baseline_receipt(self.output, self.contract)
        self.assertEqual(len(calls), 6 + len(self.baseline_bytes))
        self.assertTrue(any(str(self.sources.resolve() / 'SideStore/Dependencies/SideSign') in call for call in calls))

    def test_local_git_object_byte_mismatch_rejected_without_output(self):
        self.use_source_parent_alias()
        for owner in gate.BASELINE_OWNERS:
            (self.sources / owner / '.git').mkdir(parents=True)
        def fake_git(argv, **kwargs):
            owner = Path(argv[argv.index('-C') + 1]).relative_to(self.sources.resolve()).as_posix()
            data = b'commit\n' if '-t' in argv else ((gate.BASELINE_OWNERS[owner]['tree'] + '\n').encode()
                   if 'rev-parse' in argv else b'wrong delivered bytes')
            return subprocess.CompletedProcess(argv, 0, data)
        with mock.patch.object(gate.subprocess, 'run', side_effect=fake_git):
            with self.assertRaisesRegex(ValueError, 'baseline source bytes differ'):
                gate.extract_baseline(self.sources, self.contract, self.output / 'delivered-baseline',
                                      self.output / 'delivered-baseline.json')
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
