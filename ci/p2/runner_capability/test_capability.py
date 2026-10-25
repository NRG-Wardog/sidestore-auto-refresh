"""Portable adversarial checks; these do not establish native runner support."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, call, patch
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SPEC = importlib.util.spec_from_file_location('bounded_runner_capability', HERE / 'run.py')
capability = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capability)

RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-26-5'
DEVICE_TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro'
UDID = '12345678-1234-4321-ABCD-1234567890AB'


def runtime_fixture():
    return {'runtimes': [{'identifier': RUNTIME, 'version': '26.5',
                          'buildversion': '23F77', 'isAvailable': True}]}


def devices_fixture():
    return {'devices': {RUNTIME: [{'name': 'iPhone 17 Pro', 'udid': UDID,
                                  'deviceTypeIdentifier': DEVICE_TYPE,
                                  'isAvailable': True, 'state': 'Shutdown'}]}}


def settings_fixture():
    return [{'target': 'HostSettingsProbeUITests', 'buildSettings': {
        'TARGET_NAME': 'HostSettingsProbeUITests',
        'PRODUCT_NAME': 'HostSettingsProbeUITests',
        'PRODUCT_BUNDLE_IDENTIFIER': 'org.sidestore.audit.hostsettings.uitests',
        'PRODUCT_TYPE': 'com.apple.product-type.bundle.ui-testing',
        'CONFIGURATION': 'Debug',
        'SDK_NAME': 'iphonesimulator26.4',
        'SDK_VERSION': '26.4',
        'PLATFORM_NAME': 'iphonesimulator',
        'EFFECTIVE_PLATFORM_NAME': '-iphonesimulator',
        'TEST_HOST': '', 'BUNDLE_LOADER': '', 'TEST_TARGET_NAME': '',
    }}]


def synthetic_macho(filetype, cpu=0x0100000C, platform=7):
    """Parser fixture only; no machine code or executable subprocess is used."""
    return (struct.pack('<8I', 0xFEEDFACF, cpu, 0, filetype, 1, 24, 0, 0) +
            struct.pack('<6I', 0x32, 24, platform, 0x1A0400, 0x1A0400, 0))


class ParserTests(unittest.TestCase):
    def test_strict_json_rejects_duplicate_and_nonfinite_values(self):
        for raw in ['{"x":1,"x":2}', '{"a":{"x":1,"x":2}}', 'NaN',
                    'Infinity', '-Infinity', '1e400', '-1e400', '{"a":[1e400]}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                capability.strict_json(raw)
        self.assertEqual(capability.strict_json('{"x":1.5,"y":[2,null,true]}'),
                         {'x': 1.5, 'y': [2, None, True]})

    def test_require_fails_closed(self):
        capability.require(True, 'unused')
        with self.assertRaisesRegex(ValueError, 'synthetic rejection'):
            capability.require(False, 'synthetic rejection')


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'inputs'
        self.root.mkdir()
        (self.root / 'nested').mkdir()
        (self.root / 'nested/source.swift').write_bytes(b'unchanged source\n')
        (self.root / 'empty').write_bytes(b'')

    def tearDown(self):
        self.tmp.cleanup()

    def test_inventory_records_exact_bytes_and_hashes(self):
        rows = capability.snapshot(self.root)
        self.assertEqual(set(rows), {'empty', 'nested/source.swift'})
        for name, row in rows.items():
            raw = (self.root / name).read_bytes()
            self.assertEqual(row['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(row['bytes'], len(raw))
            self.assertEqual(capability.sha(self.root / name), row['sha256'])

    def test_mutation_and_extra_file_change_snapshot(self):
        before = capability.snapshot(self.root)
        (self.root / 'nested/source.swift').write_bytes(b'tampered source\n')
        self.assertNotEqual(capability.snapshot(self.root), before)
        (self.root / 'nested/source.swift').write_bytes(b'unchanged source\n')
        (self.root / 'injected.swift').write_text('unexpected input')
        self.assertNotEqual(capability.snapshot(self.root), before)

    def test_all_symlinks_are_rejected_including_contained_and_dangling(self):
        for target in ['empty', 'missing', '/etc/hosts', 'nested']:
            with self.subTest(target=target):
                link = self.root / 'link'
                link.symlink_to(target)
                try:
                    with self.assertRaises(ValueError):
                        capability.snapshot(self.root)
                finally:
                    link.unlink()

    def test_symlink_root_is_rejected(self):
        alias = Path(self.tmp.name) / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            capability.snapshot(alias)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX special-file test')
    def test_special_file_is_rejected_without_opening(self):
        os.mkfifo(self.root / 'fifo')
        with self.assertRaises(ValueError):
            capability.snapshot(self.root)

    def test_ambiguous_path_names_are_rejected(self):
        for name in ['back\\slash', 'line\nbreak', 'tab\tname']:
            with self.subTest(name=name):
                path = self.root / name
                path.write_text('unsafe name')
                try:
                    with self.assertRaises(ValueError):
                        capability.snapshot(self.root)
                finally:
                    path.unlink()


class DestinationTests(unittest.TestCase):
    def choose(self, devices=None, runtimes=None):
        return capability.choose_destination(
            devices_fixture() if devices is None else devices,
            runtime_fixture() if runtimes is None else runtimes)

    def test_exact_available_destination_is_accepted(self):
        result = self.choose()
        self.assertIn(UDID, str(result))

    def test_runtime_identity_build_and_availability_are_exact(self):
        for key, value in [('identifier', RUNTIME + '-other'), ('version', '26.4'),
                           ('buildversion', '23F76'), ('isAvailable', False),
                           ('isAvailable', 1)]:
            bad = runtime_fixture()
            bad['runtimes'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.choose(runtimes=bad)

    def test_absent_duplicate_and_malformed_runtime_fail(self):
        cases = [{}, {'runtimes': []}, {'runtimes': {}}, {'runtimes': [None]}]
        duplicate = runtime_fixture()
        duplicate['runtimes'].append(copy.deepcopy(duplicate['runtimes'][0]))
        cases.append(duplicate)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.choose(runtimes=bad)

    def test_device_type_availability_and_uuid_are_required(self):
        for key, value in [('deviceTypeIdentifier', DEVICE_TYPE + '-Max'),
                           ('isAvailable', False), ('isAvailable', 1),
                           ('udid', ''), ('udid', '../escape'), ('udid', 'not-a-uuid')]:
            bad = devices_fixture()
            bad['devices'][RUNTIME][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.choose(devices=bad)

    def test_device_from_another_runtime_cannot_substitute(self):
        bad = devices_fixture()
        bad['devices'][RUNTIME.replace('26-5', '26-4')] = bad['devices'].pop(RUNTIME)
        with self.assertRaises(ValueError):
            self.choose(devices=bad)

    def test_duplicate_destination_uuid_is_rejected_case_insensitively(self):
        bad = devices_fixture()
        duplicate = copy.deepcopy(bad['devices'][RUNTIME][0])
        duplicate['udid'] = duplicate['udid'].lower()
        bad['devices'][RUNTIME].append(duplicate)
        with self.assertRaises(ValueError):
            self.choose(devices=bad)

    def test_malformed_device_inventory_is_rejected(self):
        for bad in [{}, {'devices': []}, {'devices': {RUNTIME: {}}},
                    {'devices': {RUNTIME: [None]}}, {'devices': {RUNTIME: []}}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.choose(devices=bad)


class SettingsTests(unittest.TestCase):
    def test_minimal_runner_only_simulator_settings_are_accepted(self):
        capability.settings_contract(json.dumps(settings_fixture()))

    def test_missing_duplicate_or_additional_target_is_rejected(self):
        row = settings_fixture()[0]
        for rows in [[], [row, row], [row, dict(row, target='LiveContainer')],
                     [dict(row, target='LiveContainer')]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                capability.settings_contract(json.dumps(rows))

    def test_target_application_and_host_linking_settings_are_rejected(self):
        for key in ['TEST_HOST', 'BUNDLE_LOADER', 'TEST_TARGET_NAME',
                    'TEST_TARGET_PATH', 'UITEST_TARGET_APP_PATH']:
            rows = settings_fixture()
            rows[0]['buildSettings'][key] = 'LiveContainer.app'
            with self.subTest(key=key), self.assertRaises(ValueError):
                capability.settings_contract(json.dumps(rows))

    def test_device_sdk_and_platform_are_rejected(self):
        for key, value in [('SDK_NAME', 'iphoneos26.4'), ('SDK_NAME', ''),
                           ('PLATFORM_NAME', 'iphoneos'),
                           ('EFFECTIVE_PLATFORM_NAME', '-iphoneos')]:
            rows = settings_fixture()
            rows[0]['buildSettings'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                capability.settings_contract(json.dumps(rows))

    def test_malformed_settings_schema_and_identity_are_rejected(self):
        for rows in [{}, [None], [{'target': 'HostSettingsProbeUITests'}],
                     [{'target': 'HostSettingsProbeUITests', 'buildSettings': []}]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                capability.settings_contract(json.dumps(rows))
        for key, value in [('PRODUCT_TYPE', 'com.apple.product-type.application'),
                           ('PRODUCT_BUNDLE_IDENTIFIER', 'com.kdt.livecontainer'),
                           ('SDK_VERSION', '26.5'), ('CONFIGURATION', 'Release')]:
            rows = settings_fixture()
            rows[0]['buildSettings'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                capability.settings_contract(json.dumps(rows))


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.testroot = self.root / 'products'
        self.runner = self.testroot / 'Debug-iphonesimulator/HostSettingsProbeUITests-Runner.app'
        self.bundle = self.runner / 'PlugIns/HostSettingsProbeUITests.xctest'
        self.bundle.mkdir(parents=True)
        (self.runner / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'org.sidestore.audit.hostsettings.uitests.xctrunner'}))
        self.app = self.root / 'unused-host.app'
        self.validator = capability.load_validator(ROOT)

    def tearDown(self):
        self.tmp.cleanup()

    def document(self):
        return {'__xctestrun_metadata__': {'FormatVersion': 1},
                'HostSettingsProbeUITests': {
                    'BlueprintName': 'HostSettingsProbeUITests', 'IsUITestBundle': True,
                    'TestHostBundleIdentifier': 'org.sidestore.audit.hostsettings.uitests.xctrunner',
                    'TestHostPath': '__TESTROOT__/Debug-iphonesimulator/HostSettingsProbeUITests-Runner.app',
                    'TestBundlePath': '__TESTHOST__/PlugIns/HostSettingsProbeUITests.xctest'}}

    def validate(self, document):
        return self.validator.validate_xctestrun(document, self.testroot, self.app)

    def test_unchanged_validator_accepts_supported_schema_versions(self):
        v1 = self.document()
        self.assertEqual(self.validate(v1)['formatVersion'], 1)
        v2 = {'__xctestrun_metadata__': {'FormatVersion': 2},
              'TestConfigurations': [{'IsEnabled': True,
                                      'TestTargets': [v1['HostSettingsProbeUITests']]}]}
        self.assertEqual(self.validate(v2)['formatVersion'], 2)

    def test_boolean_unknown_schema_and_multiple_targets_are_rejected(self):
        for version in [True, '1', 0, 3]:
            bad = self.document()
            bad['__xctestrun_metadata__']['FormatVersion'] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.validate(bad)
        bad = self.document()
        bad['AdditionalTests'] = copy.deepcopy(bad['HostSettingsProbeUITests'])
        with self.assertRaises(ValueError):
            self.validate(bad)

    def test_target_app_and_launch_overrides_fail(self):
        for key in ['UITargetAppPath', 'UITargetAppBundleIdentifier',
                    'CommandLineArguments', 'UITargetAppCommandLineArguments',
                    'UITargetAppEnvironmentVariables', 'TestLanguage', 'TestRegion',
                    'SkipTestIdentifiers', 'OnlyTestIdentifiers']:
            bad = self.document()
            bad['HostSettingsProbeUITests'][key] = 'injected'
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(bad)

    def test_escaping_and_missing_runner_products_fail(self):
        for key, value in [('TestHostPath', '__TESTROOT__/../HostSettingsProbeUITests-Runner.app'),
                           ('TestHostPath', '/tmp/HostSettingsProbeUITests-Runner.app'),
                           ('TestBundlePath', '__TESTHOST__/../HostSettingsProbeUITests.xctest')]:
            bad = self.document()
            bad['HostSettingsProbeUITests'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.validate(bad)
        self.bundle.rmdir()
        with self.assertRaises(ValueError):
            self.validate(self.document())

    def test_immutable_validator_files_are_verified_before_import(self):
        fixture = self.root / 'builder'
        directory = fixture / 'ci/p2/host_probe'
        directory.mkdir(parents=True)
        for name in ['run.py', 'install_contract.py']:
            shutil.copyfile(ROOT / 'ci/p2/host_probe' / name, directory / name)
        for name in ['run.py', 'install_contract.py']:
            path = directory / name
            original = path.read_bytes()
            path.write_bytes(original + b'\nraise RuntimeError("must not import changed validator")\n')
            with self.subTest(name=name), self.assertRaises(ValueError):
                capability.load_validator(fixture)
            path.write_bytes(original)

    def test_symlink_validator_is_rejected(self):
        fixture = self.root / 'linked-builder'
        directory = fixture / 'ci/p2/host_probe'
        directory.mkdir(parents=True)
        for name in ['run.py', 'install_contract.py']:
            (directory / name).symlink_to(ROOT / 'ci/p2/host_probe' / name)
        with self.assertRaises(ValueError):
            capability.load_validator(fixture)


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'
        self.evidence = self.root / 'evidence'
        self.work.mkdir()
        self.evidence.mkdir()
        self.instance = capability.Capability(self.work, self.evidence, '0' * 64)
        self.instance.binding = json.loads((HERE / 'binding.json').read_text())
        self.instance.input_snapshot = capability.snapshot(HERE / 'inputs')
        shutil.copytree(HERE / 'inputs/generated-runner', self.work / 'runner')
        self.instance.runner_snapshot = capability.snapshot(self.work / 'runner')
        self.instance.validator = capability.load_validator(ROOT)
        self.destination = capability.choose_destination(devices_fixture(), runtime_fixture())
        self.instance.destination = self.destination
        self.calls = []
        self.returncodes = {}
        self.settings_raw = json.dumps(settings_fixture())

    def tearDown(self):
        self.tmp.cleanup()

    def command(self, argv, label, timeout=300):
        self.calls.append((list(map(str, argv)), label, timeout))
        return {'returncode': self.returncodes.get(label, 0),
                'stdout': self.settings_raw if label == 'testing-settings' else '',
                'stderr': 'synthetic diagnostic' if self.returncodes.get(label) else ''}

    def run_fake(self):
        with patch.object(self.instance, 'command', side_effect=self.command), \
                patch.object(self.instance, 'guard_inputs'), \
                patch.object(self.instance, 'validate_generated', return_value={
                    'formatVersion': 1, 'unmodifiedGeneratedFile': True}):
            self.instance.run_experiment(self.destination)

    def test_control_explicit_query_and_real_build_are_separate_ordered_attempts(self):
        self.run_fake()
        self.assertEqual([label for _, label, _ in self.calls],
                         ['actionless-control', 'testing-settings', 'runner-build'])
        control, query, build = [argv for argv, _, _ in self.calls]
        self.assertIn('-showBuildSettings', control)
        self.assertNotIn('build-for-testing', control)
        self.assertIn('build-for-testing', query)
        self.assertIn('-showBuildSettings', query)
        self.assertIn('build-for-testing', build)
        self.assertNotIn('-showBuildSettings', build)
        for argv, _, timeout in self.calls:
            self.assertEqual(argv[0], 'xcodebuild')
            self.assertEqual(argv[argv.index('-scheme') + 1], 'HostSettingsProbe')
            self.assertIn('runner/HostSettingsProbe.xcodeproj',
                          argv[argv.index('-project') + 1])
            self.assertIn(UDID, argv[argv.index('-destination') + 1])
            self.assertGreater(timeout, 0)
            self.assertNotIn('test', argv)
            self.assertNotIn('test-without-building', argv)
            self.assertNotIn('install', argv)
            self.assertNotIn('launch', argv)
            self.assertFalse(any(arg.startswith(('TEST_HOST=', 'TEST_TARGET_NAME=',
                                                 'BUNDLE_LOADER=', 'UITEST_TARGET_APP_PATH='))
                                 for arg in argv))

    def test_nonzero_control_does_not_skip_query_or_build(self):
        self.returncodes['actionless-control'] = 65
        self.run_fake()
        self.assertEqual([label for _, label, _ in self.calls],
                         ['actionless-control', 'testing-settings', 'runner-build'])

    def test_nonzero_query_still_attempts_actual_build_and_cannot_pass(self):
        self.returncodes['testing-settings'] = 65
        with self.assertRaisesRegex(ValueError, 'settings query failed'):
            self.run_fake()
        self.assertEqual([label for _, label, _ in self.calls],
                         ['actionless-control', 'testing-settings', 'runner-build'])
        self.assertFalse(self.instance.report['capabilityPass'])

    def test_malformed_successful_query_still_attempts_actual_build(self):
        self.settings_raw = '{"invalid":'
        with self.assertRaisesRegex(ValueError, 'settings query failed'):
            self.run_fake()
        self.assertEqual([label for _, label, _ in self.calls],
                         ['actionless-control', 'testing-settings', 'runner-build'])
        self.assertFalse(self.instance.report['capabilityPass'])

    def test_build_failure_cannot_claim_capability(self):
        self.returncodes['runner-build'] = 65
        with self.assertRaisesRegex(ValueError, 'build failed'):
            self.run_fake()
        self.assertFalse(self.instance.report['capabilityPass'])

    def test_input_mutation_between_stages_blocks_all_later_commands(self):
        for mutate_after in ['actionless-control', 'testing-settings', 'runner-build']:
            self.calls.clear()
            mutated = False
            def fake(argv, label, timeout=300):
                nonlocal mutated
                result = self.command(argv, label, timeout)
                if label == mutate_after:
                    mutated = True
                return result
            def guard():
                if mutated:
                    raise ValueError('synthetic input mutation')
            with self.subTest(mutate_after=mutate_after), \
                    patch.object(self.instance, 'command', side_effect=fake), \
                    patch.object(self.instance, 'guard_inputs', side_effect=guard), \
                    patch.object(self.instance, 'validate_generated') as validate, \
                    self.assertRaisesRegex(ValueError, 'synthetic input mutation'):
                self.instance.run_experiment(self.destination)
            labels = [label for _, label, _ in self.calls]
            self.assertEqual(labels[-1], mutate_after)
            if mutate_after != 'runner-build':
                self.assertNotIn('runner-build', labels)
            self.assertIn(mutate_after, self.instance.report['outcomes'])
            validate.assert_not_called()

    def test_generated_validation_failure_cannot_claim_capability(self):
        with patch.object(self.instance, 'command', side_effect=self.command), \
                patch.object(self.instance, 'guard_inputs'), \
                patch.object(self.instance, 'validate_generated', side_effect=ValueError('invalid generated runner')), \
                self.assertRaisesRegex(ValueError, 'invalid generated runner'):
            self.instance.run_experiment(self.destination)
        self.assertEqual([label for _, label, _ in self.calls],
                         ['actionless-control', 'testing-settings', 'runner-build'])
        self.assertFalse(self.instance.report['capabilityPass'])

    def test_only_valid_query_build_and_unchanged_validator_can_pass(self):
        self.run_fake()
        self.assertTrue(self.instance.report['capabilityPass'])
        self.assertFalse(self.instance.report.get('nativeExecution', False))
        self.assertFalse(self.instance.report.get('fullAppAcceptance', False))
        self.assertFalse(self.instance.report.get('finalCompositionAcceptance', False))


class InputGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.here = self.root / 'builder/ci/p2/runner_capability'
        self.here.mkdir(parents=True)
        shutil.copyfile(HERE / 'binding.json', self.here / 'binding.json')
        shutil.copytree(HERE / 'inputs', self.here / 'inputs')
        validator_dir = self.root / 'builder/ci/p2/host_probe'
        validator_dir.mkdir()
        for name in ['run.py', 'install_contract.py']:
            shutil.copyfile(ROOT / 'ci/p2/host_probe' / name, validator_dir / name)
        self.work = self.root / 'work'
        self.evidence = self.root / 'evidence'
        self.work.mkdir()
        self.evidence.mkdir()
        self.instance = capability.Capability(self.work, self.evidence, capability.APPROVED_INPUTS)
        self.here_patch = patch.object(capability, 'HERE', self.here)
        self.root_patch = patch.object(capability, 'ROOT', self.root / 'builder')
        self.here_patch.start()
        self.root_patch.start()
        self.addCleanup(self.here_patch.stop)
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_pinned_pack_is_copied_without_source_changes(self):
        self.instance.verify_inputs()
        self.instance.guard_inputs()
        self.assertEqual(capability.snapshot(self.work / 'runner'),
                         capability.snapshot(self.here / 'inputs/generated-runner'))
        self.assertEqual(json.loads((self.evidence / 'verified-inputs.json').read_text()),
                         json.loads((HERE / 'binding.json').read_text()))

    def test_unapproved_digest_and_modified_binding_fail_before_copy(self):
        self.instance.approved = '0' * 64
        with self.assertRaises(ValueError):
            self.instance.verify_inputs()
        self.instance.approved = capability.APPROVED_INPUTS
        with (self.here / 'binding.json').open('a') as output:
            output.write(' ')
        with self.assertRaises(ValueError):
            self.instance.verify_inputs()
        self.assertFalse((self.work / 'runner').exists())

    def test_added_input_file_fails_before_copy(self):
        (self.here / 'inputs/unapproved.swift').write_text('not in historical input')
        with self.assertRaises(ValueError):
            self.instance.verify_inputs()
        self.assertFalse((self.work / 'runner').exists())

    def test_changed_missing_and_linked_source_fail_before_copy(self):
        path = self.here / 'inputs/generated-runner/Sources/HostSettingsProbeTests.swift'
        original = path.read_bytes()
        path.write_bytes(original + b'\n// altered\n')
        with self.assertRaises(ValueError):
            self.instance.verify_inputs()
        path.unlink()
        with self.assertRaises(ValueError):
            self.instance.verify_inputs()
        path.symlink_to(HERE / 'inputs/generated-runner/Sources/HostSettingsProbeTests.swift')
        with self.assertRaises(ValueError):
            self.instance.verify_inputs()
        self.assertFalse((self.work / 'runner').exists())

    def test_guard_catches_mutated_copied_runner(self):
        self.instance.verify_inputs()
        path = self.work / 'runner/Sources/HostSettingsProbeTests.swift'
        path.write_text('substituted source')
        with self.assertRaises(ValueError):
            self.instance.guard_inputs()

    def test_guard_catches_new_historical_file(self):
        self.instance.verify_inputs()
        (self.here / 'inputs/extra').write_text('injected')
        with self.assertRaises(ValueError):
            self.instance.guard_inputs()

    def test_guard_catches_validator_mutation(self):
        self.instance.verify_inputs()
        path = self.root / 'builder/ci/p2/host_probe/run.py'
        with path.open('a') as output:
            output.write('\n# mutated\n')
        with self.assertRaises(ValueError):
            self.instance.guard_inputs()

    def test_guard_rejects_same_bytes_validator_symlink(self):
        self.instance.verify_inputs()
        path = self.root / 'builder/ci/p2/host_probe/run.py'
        path.unlink()
        path.symlink_to(ROOT / 'ci/p2/host_probe/run.py')
        with self.assertRaises(ValueError):
            self.instance.guard_inputs()


class GeneratedFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.products = self.work / 'runner-derived/Build/Products'
        self.runner = self.products / 'Debug-iphonesimulator/HostSettingsProbeUITests-Runner.app'
        self.bundle = self.runner / 'PlugIns/HostSettingsProbeUITests.xctest'
        self.bundle.mkdir(parents=True)
        (self.runner / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'org.sidestore.audit.hostsettings.uitests.xctrunner',
            'CFBundleExecutable': 'HostSettingsProbeUITests-Runner', 'CFBundlePackageType': 'APPL'}))
        (self.bundle / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'org.sidestore.audit.hostsettings.uitests',
            'CFBundleExecutable': 'HostSettingsProbeUITests', 'CFBundlePackageType': 'BNDL'}))
        for path, filetype in [(self.runner / 'HostSettingsProbeUITests-Runner', 2),
                               (self.bundle / 'HostSettingsProbeUITests', 8)]:
            path.write_bytes(synthetic_macho(filetype))
            path.chmod(0o755)
        self.document = {'__xctestrun_metadata__': {'FormatVersion': 1},
                         'HostSettingsProbeUITests': {
                             'BlueprintName': 'HostSettingsProbeUITests',
                             'IsUITestBundle': True,
                             'TestHostBundleIdentifier': 'org.sidestore.audit.hostsettings.uitests.xctrunner',
                             'TestHostPath': '__TESTROOT__/Debug-iphonesimulator/HostSettingsProbeUITests-Runner.app',
                             'TestBundlePath': '__TESTHOST__/PlugIns/HostSettingsProbeUITests.xctest'}}
        self.path = self.products / 'HostSettingsProbe.xctestrun'
        self.path.write_bytes(plistlib.dumps(self.document))
        self.instance = capability.Capability(self.work, self.evidence, capability.APPROVED_INPUTS)
        self.instance.validator = capability.load_validator(ROOT)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_file_is_retained_and_original_hashes_match(self):
        original = self.path.read_bytes()
        self.instance.validate_generated()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual((self.evidence / 'generated.xctestrun').read_bytes(), original)
        hashes = json.loads((self.evidence / 'xctestrun-hashes.json').read_text())
        self.assertEqual(hashes['before'], hashes['after'])
        outcome = self.instance.report['outcomes']['xctestrun-validation']
        self.assertEqual(outcome['status'], 'PASSED')
        self.assertFalse(outcome['hostIdentityVerified'])
        metadata = json.loads((self.evidence / 'runner-product-metadata.json').read_text())
        self.assertTrue(metadata['readOnly'])
        self.assertFalse(metadata['executed'])
        self.assertEqual([row['machO']['slices'][0]['fileType'] for row in metadata['products']], [2, 8])

    def test_missing_and_multiple_generated_files_fail(self):
        self.path.unlink()
        with self.assertRaises(ValueError):
            self.instance.validate_generated()
        self.path.write_bytes(plistlib.dumps(self.document))
        (self.products / 'Other.xctestrun').write_bytes(self.path.read_bytes())
        with self.assertRaises(ValueError):
            self.instance.validate_generated()

    def test_invalid_plist_is_retained_without_becoming_success(self):
        self.path.write_bytes(b'not an xctestrun property list')
        with self.assertRaises(plistlib.InvalidFileException):
            self.instance.validate_generated()
        self.assertEqual((self.evidence / 'generated.xctestrun').read_bytes(), self.path.read_bytes())
        self.assertNotIn('xctestrun-validation', self.instance.report['outcomes'])

    def test_generated_file_symlink_is_rejected(self):
        outside = self.root / 'outside.xctestrun'
        self.path.rename(outside)
        self.path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.instance.validate_generated()

    def test_generated_products_directory_symlink_is_rejected(self):
        outside = self.root / 'outside-products'
        self.products.rename(outside)
        self.products.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.instance.validate_generated()

    def test_validator_cannot_mutate_generated_file_silently(self):
        original = self.instance.validator.validate_xctestrun
        def mutate(data, root, app):
            result = original(data, root, app)
            changed = copy.deepcopy(data)
            changed['HostSettingsProbeUITests']['TestLanguage'] = 'he'
            self.path.write_bytes(plistlib.dumps(changed))
            return result
        with patch.object(self.instance.validator, 'validate_xctestrun', side_effect=mutate), \
                self.assertRaisesRegex(ValueError, 'mutated'):
            self.instance.validate_generated()
        hashes = json.loads((self.evidence / 'xctestrun-hashes.json').read_text())
        self.assertNotEqual(hashes['before'], hashes['after'])
        self.assertNotIn('xctestrun-validation', self.instance.report['outcomes'])

    def test_target_application_mutation_is_rejected_without_repair(self):
        self.document['HostSettingsProbeUITests']['UITargetAppPath'] = '/forbidden/LiveContainer.app'
        raw = plistlib.dumps(self.document)
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.instance.validate_generated()
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual((self.evidence / 'generated.xctestrun').read_bytes(), raw)

    def test_real_product_architecture_type_platform_and_execute_mode_are_required(self):
        executable = self.runner / 'HostSettingsProbeUITests-Runner'
        for raw in [synthetic_macho(8), synthetic_macho(2, cpu=0x01000007),
                    synthetic_macho(2, platform=2), b'not a Mach-O']:
            executable.write_bytes(raw)
            with self.subTest(raw=raw.hex()), self.assertRaises(ValueError):
                self.instance.validate_generated()
        executable.write_bytes(synthetic_macho(2))
        executable.chmod(0o644)
        with self.assertRaises(ValueError):
            self.instance.validate_generated()

    def test_real_test_bundle_identity_and_executable_are_required(self):
        info = self.bundle / 'Info.plist'
        original = plistlib.loads(info.read_bytes())
        for key, value in [('CFBundleIdentifier', 'com.kdt.livecontainer'),
                           ('CFBundleExecutable', 'other'), ('CFBundlePackageType', 'APPL')]:
            info.write_bytes(plistlib.dumps(dict(original, **{key: value})))
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.instance.validate_generated()
        info.write_bytes(plistlib.dumps(original))
        (self.bundle / 'HostSettingsProbeUITests').unlink()
        with self.assertRaises(ValueError):
            self.instance.validate_generated()


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.instance = capability.Capability(self.root / 'work', self.evidence, capability.APPROVED_INPUTS)
        self.environment = patch.dict(os.environ, {'DEVELOPER_DIR': capability.DEVELOPER}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_nonzero_command_retains_raw_outputs_and_receipt(self):
        process = Mock(pid=1234567, returncode=65)
        process.communicate.return_value = ('raw stdout', 'raw stderr')
        stopped = {'processGroup': process.pid, 'signals': [], 'quiescent': True}
        with patch.object(capability.subprocess, 'Popen', return_value=process) as run, \
                patch.object(capability, 'stop_owned_group', return_value=stopped):
            result = self.instance.command(['xcodebuild', '-version'], 'synthetic-failure')
        self.assertEqual(result['returncode'], 65)
        self.assertEqual((self.evidence / 'commands/001.stdout').read_text(), 'raw stdout')
        self.assertEqual((self.evidence / 'commands/001.stderr').read_text(), 'raw stderr')
        receipt = json.loads((self.evidence / 'commands/001.json').read_text())
        self.assertEqual(receipt['label'], 'synthetic-failure')
        self.assertEqual(receipt['returncode'], 65)
        run.assert_called_once()
        self.assertTrue(run.call_args.kwargs['start_new_session'])
        self.assertTrue(receipt['ownedProcessGroup']['pipesJoined'])
        self.assertEqual(process.communicate.call_args_list, [call(timeout=300), call(timeout=3)])

    def test_timeout_retains_partial_outputs_and_failure(self):
        failure = subprocess.TimeoutExpired(['xcodebuild', '-version'], 5,
                                            output=b'partial stdout', stderr=b'partial stderr')
        process = Mock(pid=1234567, returncode=-15)
        process.communicate.side_effect = [failure, ('partial stdout', 'partial stderr')]
        stopped = {'processGroup': process.pid, 'signals': ['SIGTERM'], 'quiescent': True}
        with patch.object(capability.subprocess, 'Popen', return_value=process), \
                patch.object(capability, 'stop_owned_group', return_value=stopped) as stop:
            result = self.instance.command(['xcodebuild', '-version'], 'synthetic-timeout', timeout=5)
        self.assertIsNone(result['returncode'])
        self.assertEqual(result['error'], 'TimeoutExpired')
        self.assertEqual((self.evidence / 'commands/001.stdout').read_text(), 'partial stdout')
        self.assertEqual((self.evidence / 'commands/001.stderr').read_text(), 'partial stderr')
        stop.assert_called_once_with(process)
        self.assertEqual(result['ownedProcessGroup']['signals'], ['SIGTERM'])

    def test_missing_command_is_recorded(self):
        with patch.object(capability.subprocess, 'Popen', side_effect=FileNotFoundError('synthetic missing tool')):
            result = self.instance.command(['xcodebuild', '-version'], 'missing-tool')
        self.assertIn('FileNotFoundError', result['error'])
        self.assertTrue((self.evidence / 'commands/001.json').is_file())

    def test_install_launch_execution_and_unknown_commands_never_reach_subprocess(self):
        forbidden = [['xcrun', 'simctl', action, UDID, 'com.kdt.livecontainer']
                     for action in ['install', 'launch', 'boot', 'create']]
        forbidden += [['xcodebuild', 'test'], ['xcodebuild', 'test-without-building'],
                      ['sh', '-c', 'true'], ['xcodebuild', '-version', 'TEST_HOST=other']]
        with patch.object(capability.subprocess, 'Popen') as run:
            for argv in forbidden:
                with self.subTest(argv=argv):
                    result = self.instance.command(argv, 'forbidden')
                    self.assertIsNone(result['returncode'])
                    self.assertIn('allowlist', result['error'])
            run.assert_not_called()

    def test_ambient_build_launch_overrides_block_before_subprocess(self):
        for key in ['XCODE_XCCONFIG_FILE', 'SDKROOT', 'TOOLCHAINS', 'TEST_HOST',
                    'OTHER_SWIFT_FLAGS', 'DYLD_INSERT_LIBRARIES', 'SIMCTL_CHILD_TEST_LANGUAGE']:
            with self.subTest(key=key), patch.dict(os.environ, {key: 'injected'}), \
                    patch.object(capability.subprocess, 'Popen') as run:
                result = self.instance.command(['xcodebuild', '-version'], 'ambient-override')
                self.assertIn('overrides', result['error'])
                run.assert_not_called()

    def test_unjoined_timeout_pipe_records_unresolved_writers(self):
        process = Mock(pid=1234567, returncode=0)
        process.communicate.side_effect = [('initial stdout', ''),
                                          subprocess.TimeoutExpired(['synthetic'], 3)]
        stopped = {'processGroup': process.pid, 'signals': ['SIGTERM', 'SIGKILL'], 'quiescent': True}
        with patch.object(capability.subprocess, 'Popen', return_value=process), \
                patch.object(capability, 'stop_owned_group', return_value=stopped):
            result = self.instance.command(['xcodebuild', '-version'], 'unjoined-pipe')
        self.assertIn('TimeoutExpired', result['quiescenceError'])
        self.assertFalse(result['ownedProcessGroup']['quiescent'])
        self.assertEqual(len(self.instance.command_quiescence), 1)
        self.assertEqual((self.evidence / 'commands/001.stdout').read_text(), 'initial stdout')


class OwnedProcessGroupTests(unittest.TestCase):
    def test_termination_targets_only_the_owned_group(self):
        process = Mock(pid=1234567)
        with patch.object(capability, 'group_alive', side_effect=[True, False, False]), \
                patch.object(capability.os, 'killpg') as kill, \
                patch.object(capability.time, 'monotonic', return_value=0), \
                patch.object(capability.time, 'sleep'):
            result = capability.stop_owned_group(process)
        kill.assert_called_once_with(process.pid, signal.SIGTERM)
        self.assertTrue(result['quiescent'])
        self.assertEqual(result['signals'], ['SIGTERM'])

    def test_unresponsive_owned_group_escalates_to_kill(self):
        process = Mock(pid=1234567)
        with patch.object(capability, 'group_alive', side_effect=[True, True, False, False]), \
                patch.object(capability.os, 'killpg') as kill, \
                patch.object(capability.time, 'monotonic', side_effect=[0, 4, 4, 4]), \
                patch.object(capability.time, 'sleep'):
            result = capability.stop_owned_group(process)
        self.assertEqual(kill.call_args_list, [call(process.pid, signal.SIGTERM),
                                              call(process.pid, signal.SIGKILL)])
        self.assertTrue(result['quiescent'])

    def test_group_still_alive_after_bounded_signals_is_not_success(self):
        process = Mock(pid=1234567)
        with patch.object(capability, 'group_alive', return_value=True), \
                patch.object(capability.os, 'killpg') as kill, \
                patch.object(capability.time, 'monotonic', side_effect=[0, 4, 4, 8]), \
                patch.object(capability.time, 'sleep'):
            result = capability.stop_owned_group(process)
        self.assertFalse(result['quiescent'])
        self.assertEqual(kill.call_count, 2)


class SharedDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.instance = capability.Capability(self.root / 'work', self.evidence, capability.APPROVED_INPUTS)
        self.addCleanup(self.tmp.cleanup)

    def test_exhausted_shared_budget_blocks_before_any_process_and_keeps_receipt(self):
        self.instance.deadline = 5
        with patch.object(capability.time, 'monotonic', return_value=10), \
                patch.object(capability.subprocess, 'Popen') as start:
            result = self.instance.command(['xcodebuild', '-version'], 'preflight-after-budget')
        start.assert_not_called()
        self.assertTrue(result['deadlineExhausted'])
        self.assertEqual(result['timeoutSeconds'], 0)
        self.assertIn('deadline exhausted', result['error'])
        self.assertTrue((self.evidence / 'commands/001.json').exists())

    def test_command_timeout_is_capped_to_remaining_shared_budget(self):
        self.instance.deadline = 17
        process = Mock(pid=1234567, returncode=0)
        process.communicate.return_value = ('version', '')
        with patch.dict(os.environ, {'DEVELOPER_DIR': capability.DEVELOPER}, clear=True), \
                patch.object(capability.time, 'monotonic', return_value=10), \
                patch.object(capability.subprocess, 'Popen', return_value=process), \
                patch.object(capability, 'stop_owned_group', return_value={
                    'processGroup': process.pid, 'signals': [], 'quiescent': True}):
            result = self.instance.command(['xcodebuild', '-version'], 'bounded-preflight', timeout=300)
        self.assertEqual(process.communicate.call_args_list, [call(timeout=7), call(timeout=3)])
        self.assertEqual(result['requestedTimeoutSeconds'], 300)
        self.assertEqual(result['timeoutSeconds'], 7)

    def test_budget_is_shared_not_reset_between_commands(self):
        self.instance.deadline = 17
        process = Mock(pid=1234567, returncode=0)
        process.communicate.return_value = ('version', '')
        with patch.dict(os.environ, {'DEVELOPER_DIR': capability.DEVELOPER}, clear=True), \
                patch.object(capability.subprocess, 'Popen', return_value=process), \
                patch.object(capability, 'stop_owned_group', return_value={
                    'processGroup': process.pid, 'signals': [], 'quiescent': True}):
            with patch.object(capability.time, 'monotonic', return_value=10):
                first = self.instance.command(['xcodebuild', '-version'], 'first')
            with patch.object(capability.time, 'monotonic', return_value=15):
                second = self.instance.command(['xcodebuild', '-version'], 'second')
        self.assertEqual((first['timeoutSeconds'], second['timeoutSeconds']), (7, 2))
        self.assertEqual(self.instance.deadline, 17)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        (self.work / 'runner/Sources').mkdir(parents=True)
        (self.work / 'runner/Sources/probe.swift').write_text('partial synthetic source')
        (self.work / 'runner-derived/Build/Products').mkdir(parents=True)
        (self.work / 'runner-derived/Build/Products/partial-output').write_bytes(b'partial build bytes')
        (self.work / 'runner-build.xcresult').mkdir()
        (self.work / 'runner-build.xcresult/Info.plist').write_bytes(b'partial result evidence')
        self.instance = capability.Capability(self.work, self.evidence, capability.APPROVED_INPUTS)
        self.instance.report['errors'].append('primary synthetic build failure')

    def tearDown(self):
        self.tmp.cleanup()

    def test_failure_retains_inputs_sources_partial_build_and_result_without_commands(self):
        with patch.object(self.instance, 'command', side_effect=AssertionError('native command forbidden')), \
                patch.object(capability.subprocess, 'Popen', side_effect=AssertionError('subprocess forbidden')):
            self.instance.retain()
        self.assertEqual(self.instance.report['status'], 'BLOCKED')
        self.assertFalse(self.instance.report['capabilityPass'])
        self.assertEqual(self.instance.report['errors'], ['primary synthetic build failure'])
        with zipfile.ZipFile(self.evidence / 'generated-runner.zip') as archive:
            self.assertEqual(archive.read('Sources/probe.swift'), b'partial synthetic source')
        with zipfile.ZipFile(self.evidence / 'runner-derived.zip') as archive:
            self.assertEqual(archive.read('Build/Products/partial-output'), b'partial build bytes')
        with zipfile.ZipFile(self.evidence / 'inputs.zip') as archive:
            self.assertIn('generated-runner/Sources/HostSettingsProbeTests.swift', archive.namelist())
        with zipfile.ZipFile(self.evidence / 'runner-build.xcresult.zip') as archive:
            self.assertEqual(archive.read('Info.plist'), b'partial result evidence')
        self.assertFalse((self.evidence / 'runner-build.xcresult').exists())
        report = json.loads((self.evidence / 'capability.json').read_text())
        self.assertEqual(report['errors'], ['primary synthetic build failure'])
        inventory = json.loads((self.evidence / 'evidence-sha256.json').read_text())
        for name in ['capability.json', 'generated-runner.zip', 'runner-derived.zip',
                     'inputs.zip', 'runner-build.xcresult.zip']:
            self.assertEqual(inventory[name]['sha256'], capability.sha(self.evidence / name))

    def test_archive_failure_does_not_mask_original_failure_or_skip_report(self):
        with patch.object(capability, 'archive_tree', side_effect=OSError('synthetic archive failure')):
            self.instance.retain()
        self.assertEqual(self.instance.report['errors'], ['primary synthetic build failure'])
        self.assertEqual(len(self.instance.report['retentionErrors']), 4)
        self.assertTrue(all('synthetic archive failure' in error for error in self.instance.report['retentionErrors']))
        self.assertEqual(json.loads((self.evidence / 'capability.json').read_text())['status'], 'BLOCKED')
        self.assertTrue((self.evidence / 'evidence-sha256.json').is_file())

    def test_retention_failure_revokes_prior_capability_claim(self):
        self.instance.report.update(errors=[], status='RUNNER_GENERATION_CAPABLE', capabilityPass=True)
        self.instance.runner_snapshot = {}
        with patch.object(self.instance, 'guard_inputs', side_effect=ValueError('late source mutation')):
            self.instance.retain()
        self.assertEqual(self.instance.report['status'], 'BLOCKED')
        self.assertFalse(self.instance.report['capabilityPass'])
        self.assertTrue(any('late source mutation' in error for error in self.instance.report['retentionErrors']))

    def test_output_symlink_is_archived_as_link_without_reading_external_bytes(self):
        outside = self.root / 'private-outside'
        outside.write_bytes(b'external bytes must not be retained')
        (self.work / 'runner-derived/escape').symlink_to(outside)
        self.instance.retain()
        with zipfile.ZipFile(self.evidence / 'runner-derived.zip') as archive:
            info = archive.getinfo('escape')
            self.assertEqual(info.external_attr >> 16 & 0o170000, 0o120000)
            self.assertEqual(archive.read(info), os.fsencode(outside))
            self.assertNotIn(b'external bytes must not be retained',
                             [archive.read(name) for name in archive.namelist()])
        inventory = json.loads((self.evidence / 'runner-derived.before-inventory.json').read_text())
        self.assertEqual(inventory['escape']['type'], 'symlink')
        self.assertEqual(inventory['escape']['target'], str(outside))
        self.assertEqual(inventory, json.loads((self.evidence / 'runner-derived.after-inventory.json').read_text()))
        verification = json.loads((self.evidence / 'runner-derived.verification.json').read_text())
        self.assertEqual(verification['status'], 'PASSED')
        self.assertTrue(verification['archivedPayloadsTypesModesMatch'])

    def test_output_mutation_during_archive_is_reported_and_preserves_operands(self):
        source = self.work / 'runner-derived'
        path = source / 'Build/Products/partial-output'
        original = capability.shutil.copyfileobj
        def mutate(input_stream, output_stream, *args, **kwargs):
            result = original(input_stream, output_stream, *args, **kwargs)
            path.write_bytes(b'mutated during archive')
            return result
        with patch.object(capability.shutil, 'copyfileobj', side_effect=mutate), \
                self.assertRaisesRegex(ValueError, 'changed while archiving'):
            capability.archive_tree(source, self.evidence / 'changed.zip')
        before = json.loads((self.evidence / 'changed.before-inventory.json').read_text())
        after = json.loads((self.evidence / 'changed.after-inventory.json').read_text())
        self.assertNotEqual(before, after)
        self.assertFalse((self.evidence / 'changed.verification.json').exists())

    def test_archive_root_symlink_fails(self):
        source = self.root / 'linked-source'
        source.symlink_to(self.work / 'runner', target_is_directory=True)
        with self.assertRaises(ValueError):
            capability.archive_tree(source, self.evidence / 'linked.zip')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX special-file test')
    def test_special_output_fails_retention_without_hanging_or_losing_report(self):
        os.mkfifo(self.work / 'runner-derived/fifo')
        self.instance.retain()
        self.assertEqual(self.instance.report['status'], 'BLOCKED')
        self.assertTrue(any('Special build output' in error for error in self.instance.report['retentionErrors']))
        self.assertTrue((self.evidence / 'capability.json').exists())


if __name__ == '__main__':
    unittest.main()
