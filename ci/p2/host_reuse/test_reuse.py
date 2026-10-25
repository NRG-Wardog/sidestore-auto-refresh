"""Portable hostile-contract tests. Synthetic Mach-O bytes are never executable."""
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import struct
import tempfile
import unittest
from unittest.mock import patch

import runner_contract as RC

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SPEC = importlib.util.spec_from_file_location('host_reuse_orchestrator_under_test', HERE / 'run.py')
RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN)
RUN.RC = RC


def thin(cpu, subtype, filetype, platform=7):
    command = struct.pack('<8I', 0x32, 32, platform, 0x0f0000, 0x1a0400, 1, 3, 0x04f20000)
    return struct.pack('<8I', 0xfeedfacf, cpu, subtype, filetype, 1, len(command), 0, 0) + command


def fat(filetype, platform=7, cpus=((0x1000007, 3), (0x100000c, 0))):
    offset = (8 + len(cpus) * 20 + 7) & ~7
    head = struct.pack('>II', 0xcafebabe, len(cpus))
    payload = b''
    for cpu, subtype in cpus:
        raw = thin(cpu, subtype, filetype, platform)
        head += struct.pack('>IIIII', cpu, subtype, offset + len(payload), len(raw), 3)
        payload += raw
    return head + b'\0' * (offset - len(head)) + payload


def success_policy(expected):
    return {'status': 'cleared-before-single-launch', 'initialEnvironment': {}, 'initialArguments': [],
            'expectedInitialEnvironment': {}, 'generatedTargetEnvironment': expected, 'initialEnvironmentMatched': True, 'initialEnvironmentKeyCount': 0,
            'initialArgumentCount': 0, 'missingExpectedKeys': [], 'mismatchedExpectedKeys': [], 'unknownKeyCount': 0,
            'requestedEnvironmentEmpty': True, 'requestedArgumentsEmpty': True, 'performanceCheckerEnabled': True,
            'effectiveEnvironmentEmptyProven': False, 'requestedEnvironmentAfterClearing': {}, 'requestedArgumentsAfterClearing': []}


def events(expected):
    final = success_policy(expected)
    initial = {k: v for k, v in final.items() if not k.startswith('requested')}
    initial['status'] = 'initial-observed'
    cleared = {**final, 'status': 'initial-observed'}
    return [{'schemaVersion': 2, 'stage': stage, 'launchCount': count, 'policy': policy}
            for stage, count, policy in zip(('requested-target-launch-policy', 'cleared-requested-target-launch-inputs', 'genuine-host-launch'),
                                           (0, 0, 1), (initial, cleared, final))]


class ContractFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve(strict=True)
        self.products = self.base / 'Products'
        self.products.mkdir()
        self.layout = json.loads((HERE / 'products-layout.json').read_text())
        for name, row in self.layout.items():
            path = self.products / name
            if row['type'] == 'directory':
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(plistlib.dumps({}) if path.name == 'Info.plist' else b'synthetic')
            path.chmod(row['mode'])
        self.runner = self.products / 'Debug-iphonesimulator/HostSettingsProbeUITests-Runner.app'
        self.bundle = self.runner / 'PlugIns/HostSettingsProbeUITests.xctest'
        for path, identifier, executable, package, filetype in [(self.runner, RC.RUNNER_ID, 'HostSettingsProbeUITests-Runner', 'APPL', 2),
                                                              (self.bundle, RC.TEST_ID, 'HostSettingsProbeUITests', 'BNDL', 8)]:
            (path / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': identifier, 'CFBundleExecutable': executable,
                'CFBundlePackageType': package, 'DTPlatformName': 'iphonesimulator'}))
            (path / executable).write_bytes(fat(filetype))
        self.reference = RC.reference()
        self.raw = plistlib.dumps(self.reference)
        self.xctest = self.products / 'HostSettingsProbe_iphonesimulator26.4-arm64-x86_64.xctestrun'
        self.xctest.write_bytes(self.raw)
        self.app = self.base / 'LiveContainer.app'
        self.app.mkdir()
        self.bindings = RC.roots(self.products, Path('/Applications/Xcode.app/Contents/Developer'), Path('/Library/Developer/RuntimeRoot'))
        self.expected = RC.generated_target_environment(self.bindings)

    def validate(self, value=None):
        return RC.validate_generated(plistlib.dumps(self.reference if value is None else value), self.products, self.app,
                                     Path('/Applications/Xcode.app/Contents/Developer'), Path('/Library/Developer/RuntimeRoot'))

    def inspect(self):
        return RC.inspect_products(self.validate(), RUN.HP.INSTALL_CONTRACT)


class GeneratedContractTests(ContractFixture):
    def test_exact_reference_and_universal_products(self):
        result = self.inspect()
        self.assertEqual(len(result['productClosure']['layout']), 75)
        self.assertEqual([p['machO']['container'] for p in result['products']], ['fat32', 'fat32'])
        self.assertFalse(result['executed'])

    def test_every_added_environment_key_fails(self):
        for role in ('EnvironmentVariables', 'TestingEnvironmentVariables', 'UITargetAppEnvironmentVariables'):
            data = copy.deepcopy(self.reference)
            data[RC.TARGET][role]['AppleLanguages'] = '(he)'
            with self.subTest(role=role), self.assertRaises(ValueError): self.validate(data)

    def test_every_changed_environment_value_fails(self):
        for role in ('EnvironmentVariables', 'TestingEnvironmentVariables', 'UITargetAppEnvironmentVariables'):
            for key in self.reference[RC.TARGET][role]:
                data = copy.deepcopy(self.reference)
                data[RC.TARGET][role][key] += 'changed'
                with self.subTest(role=role, key=key), self.assertRaises(ValueError): self.validate(data)

    def test_role_swap_fails(self):
        data = copy.deepcopy(self.reference)
        data[RC.TARGET]['EnvironmentVariables'], data[RC.TARGET]['UITargetAppEnvironmentVariables'] = data[RC.TARGET]['UITargetAppEnvironmentVariables'], data[RC.TARGET]['EnvironmentVariables']
        with self.assertRaises(ValueError): self.validate(data)

    def test_argument_language_region_and_target_overrides_fail(self):
        for key, value in [('CommandLineArguments', ['-AppleLanguages', '(he)']), ('UITargetAppCommandLineArguments', ['x']), ('TestLanguage', 'he'),
                           ('TestRegion', 'IL'), ('UITargetAppPath', '/tmp/evil.app'), ('UITargetAppBundleIdentifier', 'com.kdt.livecontainer')]:
            data = copy.deepcopy(self.reference); data[RC.TARGET][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.validate(data)

    def test_external_flags_exact_boolean_types(self):
        for key in ('UseUITargetAppProvidedByTests', 'UITargetAppPerformanceAntipatternCheckerEnabled', 'IsUITestBundle', 'IsXCTRunnerHostedTestBundle'):
            for value in (False, 1, 'true'):
                data = copy.deepcopy(self.reference); data[RC.TARGET][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.validate(data)

    def test_duplicate_plist_keys_and_entity_fails(self):
        raw = self.raw.replace(b'<key>RunOrder</key>', b'<key>RunOrder</key><integer>0</integer><key>RunOrder</key>')
        with self.assertRaises(ValueError): RC.strict_plist(raw)
        with self.assertRaises(ValueError): RC.strict_plist(b'<!ENTITY x "bad">' + self.raw)

    def test_binary_plist_not_admitted(self):
        with self.assertRaises(ValueError): RC.strict_plist(plistlib.dumps(self.reference, fmt=plistlib.FMT_BINARY))

    def test_bad_roots(self):
        for root in ('relative', '//double/root', '/a/../b', '/a/./b', '/a//b', '/a:b', '/a\\b', '/a\x00b', '/a/'):
            with self.subTest(root=root), self.assertRaises(ValueError): RC.canonical(root)

    def test_bad_path_expansion(self):
        for value in ('', '__TESTROOT__/../escape', '__TESTROOT__//bad', '__TESTROOT__/a:', ':__TESTROOT__/a', '__BAD__/a',
                      '__TESTROOT__/__TESTHOST__/a', '__TESTROOT__/a:__TESTROOT__/a', '/tmp/unreviewed', '__TESTROOT__/a\\b'):
            with self.subTest(value=value), self.assertRaises(ValueError): RC.expand(value, self.bindings)

    def test_linked_runner_fails(self):
        real = self.runner.with_name('elsewhere'); self.runner.rename(real); self.runner.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError): self.validate()

    def test_linked_info_fails(self):
        p = self.runner / 'Info.plist'; raw = p.read_bytes(); p.unlink(); other = self.base / 'Info.plist'; other.write_bytes(raw); p.symlink_to(other)
        with self.assertRaises(ValueError): self.validate()

    def test_genuine_host_nested_in_runner_fails(self):
        with self.assertRaises(ValueError): RC.validate_generated(self.raw, self.products, self.bundle, Path('/Applications/Xcode.app'), Path('/Runtime'))

    def test_extra_root_product_fails(self):
        (self.products / 'LiveContainer.app').mkdir()
        with self.assertRaises(ValueError): self.inspect()

    def test_extra_search_root_framework_fails(self):
        (self.products / 'Debug-iphonesimulator/Evil.framework').mkdir()
        with self.assertRaises(ValueError): self.inspect()

    def test_extra_swiftmodule_payload_fails(self):
        (self.products / 'Debug-iphonesimulator/HostSettingsProbeUITests.swiftmodule/Evil').write_bytes(b'bad')
        with self.assertRaises(ValueError): self.inspect()

    def test_bundle_id_collision_in_framework_fails(self):
        p = self.runner / 'Frameworks/XCTest.framework/Info.plist'; p.write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.kdt.livecontainer'}))
        with self.assertRaises(ValueError): self.inspect()

    def test_product_symlink_fails(self):
        p = self.runner / 'PkgInfo'; p.unlink(); p.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): self.inspect()

    def test_wrong_bundle_identity_type_platform_fails(self):
        p = self.bundle / 'Info.plist'; original = plistlib.loads(p.read_bytes())
        for key, value in [('CFBundleIdentifier', RC.RUNNER_ID), ('CFBundlePackageType', 'APPL'), ('DTPlatformName', 'iphoneos'), ('CFBundleExecutable', '../evil')]:
            p.write_bytes(plistlib.dumps({**original, key: value}))
            with self.subTest(key=key), self.assertRaises(ValueError): self.inspect()

    def test_macho_wrong_platform_fails(self):
        (self.bundle / 'HostSettingsProbeUITests').write_bytes(fat(8, 2))
        with self.assertRaises(ValueError): self.inspect()

    def test_macho_wrong_type_fails(self):
        (self.bundle / 'HostSettingsProbeUITests').write_bytes(fat(2))
        with self.assertRaises(ValueError): self.inspect()

    def test_macho_thin_or_wrong_architecture_fails(self):
        p = self.bundle / 'HostSettingsProbeUITests'
        for raw in (thin(0x100000c, 0, 8), fat(8, cpus=((0x100000c, 0), (0x1000007, 3))), fat(8, cpus=((0x1000007, 3),))):
            p.write_bytes(raw)
            with self.assertRaises(ValueError): self.inspect()

    def test_macho_byte_mutation_fails(self):
        p = self.bundle / 'HostSettingsProbeUITests'; raw = bytearray(p.read_bytes()); raw[0] ^= 1; p.write_bytes(raw)
        with self.assertRaises(ValueError): self.inspect()

    def test_wrong_executable_mode_fails(self):
        (self.bundle / 'HostSettingsProbeUITests').chmod(0o644)
        with self.assertRaises(ValueError): self.inspect()

    def test_mutation_between_product_snapshots_fails(self):
        original = RUN.HP.INSTALL_CONTRACT.bundle_inventory
        counter = [0]
        def mutate(path):
            result = original(path); counter[0] += 1
            if counter[0] == 2: result['PkgInfo']['sha256'] = '0' * 64
            return result
        with patch.object(RUN.HP.INSTALL_CONTRACT, 'bundle_inventory', mutate), self.assertRaises(ValueError): self.inspect()


class LaunchPolicyTests(ContractFixture):
    def test_exact_policy_and_durable_sequence(self):
        rows = events(self.expected)
        output = '\n'.join('timestamp P2_REQUESTED_LAUNCH_POLICY_V2:' + json.dumps(row) for row in rows)
        receipt = RC.policy_events(output, self.expected)
        self.assertTrue(RC.verify_policy_events(receipt, self.expected))
        self.assertEqual(RC.verify_launch_policy({'launchPolicy': rows[-1]['policy'], 'launchCount': 1}, self.expected)['status'], 'cleared-before-single-launch')

    def test_nonempty_initial_getter_is_retained_redacted_but_blocks(self):
        row = events(self.expected)[0]
        row['policy'].pop('initialEnvironment'); row['policy']['initialEnvironmentMatched'] = False
        row['policy']['initialEnvironmentKeyCount'] = 1; row['policy']['unknownKeyCount'] = 1
        receipt = RC.policy_events('P2_REQUESTED_LAUNCH_POLICY_V2:' + json.dumps(row), self.expected)
        self.assertEqual(len(receipt['events']), 1)
        with self.assertRaises(ValueError): RC.verify_policy_events(receipt, self.expected)

    def test_unknown_getter_values_are_not_retained(self):
        row = events(self.expected)[0]; row['policy']['initialEnvironment']['SECRET'] = 'sensitive'
        with self.assertRaises(ValueError): RC.policy_events('P2_REQUESTED_LAUNCH_POLICY_V2:' + json.dumps(row), self.expected)

    def test_generated_map_never_qualifies_as_initial_getter(self):
        for environment in (self.expected, {'AppleLanguages': '(he)'}, {'APP_DISTRIBUTOR_ID_OVERRIDE': 'com.apple.AppStore'}):
            policy = success_policy(self.expected)
            policy['initialEnvironment'] = environment
            policy['initialEnvironmentKeyCount'] = len(environment)
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                RC.verify_launch_policy({'launchPolicy': policy, 'launchCount': 1}, self.expected)

    def test_empty_getter_does_not_relax_generated_map_binding(self):
        for value in ({}, dict(self.expected, AppleLanguages='(he)'), {**self.expected, 'XCODE_SCHEME_NAME': 'other'}):
            policy = success_policy(self.expected); policy['generatedTargetEnvironment'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                RC.verify_launch_policy({'launchPolicy': policy, 'launchCount': 1}, self.expected)
        policy = success_policy(self.expected); policy['expectedInitialEnvironment'] = self.expected
        with self.assertRaises(ValueError): RC.verify_launch_policy({'launchPolicy': policy, 'launchCount': 1}, self.expected)

    def test_original_version_cannot_satisfy_new_policy(self):
        rows = events(self.expected)
        output = '\n'.join('P2_REQUESTED_LAUNCH_POLICY_V1:' + json.dumps(row) for row in rows)
        receipt = RC.policy_events(output, self.expected)
        self.assertEqual(receipt['events'], [])
        with self.assertRaises(ValueError): RC.verify_policy_events(receipt, self.expected)
        rows[0]['schemaVersion'] = 1
        with self.assertRaises(ValueError): RC.policy_events('P2_REQUESTED_LAUNCH_POLICY_V2:' + json.dumps(rows[0]), self.expected)

    def test_empty_initial_observation_alone_is_not_launch_acceptance(self):
        receipt = RC.policy_events('P2_REQUESTED_LAUNCH_POLICY_V2:' + json.dumps(events(self.expected)[0]), self.expected)
        self.assertEqual(len(receipt['events']), 1)
        with self.assertRaises(ValueError): RC.verify_policy_events(receipt, self.expected)

    def test_nonempty_requested_fields_and_language_arguments_fail(self):
        for key, value in [('requestedEnvironmentAfterClearing', {'AppleLanguages': '(he)'}), ('requestedArgumentsAfterClearing', ['-AppleLanguages']),
                           ('requestedEnvironmentEmpty', False), ('initialArguments', ['secret']), ('effectiveEnvironmentEmptyProven', True), ('performanceCheckerEnabled', False)]:
            policy = success_policy(self.expected); policy[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): RC.verify_launch_policy({'launchPolicy': policy, 'launchCount': 1}, self.expected)

    def test_launch_count_bool_zero_and_twice_fail(self):
        for count in (True, 0, 2):
            with self.subTest(count=count), self.assertRaises(ValueError): RC.verify_launch_policy({'launchPolicy': success_policy(self.expected), 'launchCount': count}, self.expected)

    def test_out_of_order_missing_or_duplicate_events_fail(self):
        for rows in (events(self.expected)[::-1], events(self.expected)[:2], [events(self.expected)[0]] * 3):
            receipt = {'schemaVersion': 2, 'forwardedByXcode': True, 'effectiveEnvironmentEmptyProven': False, 'events': rows}
            with self.assertRaises(ValueError): RC.verify_policy_events(receipt, self.expected)

    def test_duplicate_json_key_and_unknown_metadata_fail(self):
        row = json.dumps(events(self.expected)[0]).replace('"schemaVersion": 2', '"schemaVersion": 2, "schemaVersion": 2')
        with self.assertRaises(ValueError): RC.policy_events('P2_REQUESTED_LAUNCH_POLICY_V2:' + row, self.expected)
        row = events(self.expected)[0]; row['policy']['missingExpectedKeys'] = ['SECRET_NAME']
        with self.assertRaises(ValueError): RC.policy_events('P2_REQUESTED_LAUNCH_POLICY_V2:' + json.dumps(row), self.expected)

    def test_wrapper_integration_uses_report_not_attachments(self):
        observation = {'launchCount': 1, 'launchPolicy': success_policy(self.expected)}
        with patch.object(RUN, 'SC') as startup:
            startup.verify_observations.return_value = {'report': observation, 'attachments': {'proof': 'bytes'}}
            result = RUN.verify_observations_with_policy(self.base, {}, 'builder', self.expected)
        self.assertFalse(result['effectiveEnvironmentEmptyProven'])



class OwnedCleanupTests(unittest.TestCase):
    def test_lane_overrides_inherited_unsafe_command_cleanup(self):
        self.assertIsNot(RUN.ReuseProbe.command, RUN.CAP.Capability.command)

    def test_real_owned_python_timeout_signals_only_before_reap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(strict=True); (root / 'evidence').mkdir()
            probe = RUN.ReuseProbe(root / 'artifact', root / 'work', root / 'evidence', 'seal')
            argv = [sys.executable, '-B', '-c', 'import time; time.sleep(30)']
            children, signals = [], []
            real_popen, real_killpg = subprocess.Popen, os.killpg
            def start(*args, **kwargs):
                child = real_popen(*args, **kwargs); children.append(child); return child
            def kill(pgid, sig):
                signals.append(sig)
                if sig:
                    self.assertIsNone(children[0].returncode)
                    self.assertEqual(pgid, children[0].pid)
                return real_killpg(pgid, sig)
            with patch.object(probe, 'allowed', side_effect=lambda value: value == argv), \
                 patch.object(RUN.subprocess, 'Popen', side_effect=start), patch.object(RUN.os, 'killpg', side_effect=kill), \
                 patch.dict(os.environ, {'DEVELOPER_DIR': RUN.CAP.DEVELOPER}):
                result = probe.command(argv, 'owned-synthetic-python-timeout', 0.05)
            self.assertEqual(result['error'], 'TimeoutExpired')
            self.assertIsNone(result['returncode'])
            self.assertTrue(result['ownedProcessGroup']['leaderReaped'])
            self.assertTrue(result['ownedProcessGroup']['quiescent'])
            self.assertTrue(result['ownedProcessGroup']['pipesJoined'])
            self.assertEqual([sig for sig in signals if sig], [signal.SIGTERM, signal.SIGKILL])

    class Child:
        pid = 424242
        def __init__(self, code=None, poll_exit=False):
            self.returncode = code; self.poll_exit = poll_exit; self.events = []
        def poll(self):
            self.events.append('poll')
            if self.poll_exit: self.returncode = 0
            return self.returncode
        def communicate(self, timeout):
            self.events.append('communicate'); self.returncode = 0
            return ('retained stdout', 'retained stderr')
        def wait(self, timeout):
            self.events.append('wait'); self.returncode = 0
            return 0

    def run_cleanup(self, child, surviving=False, denied=False, pgid=424242):
        calls = []
        def kill(group, sig):
            calls.append((group, sig, child.returncode))
            if sig:
                self.assertIsNone(child.returncode, 'Nonzero signal after actual child reap')
                child.events.append('signal-' + str(int(sig)))
            elif denied: raise PermissionError('denied')
            elif not surviving: raise ProcessLookupError('gone')
        ticks = iter(range(100))
        with patch.object(RUN.os, 'killpg', side_effect=kill), patch.object(RUN.os, 'getpgid', return_value=pgid), \
             patch.object(RUN.signal, 'getsignal', return_value=signal.SIG_DFL), \
             patch.object(RUN.time, 'sleep', side_effect=lambda seconds: child.events.append('sleep')), \
             patch.object(RUN.time, 'monotonic', side_effect=lambda: next(ticks)):
            result = RUN.finish_owned_child(child)
        return result, calls

    def test_already_reaped_child_never_receives_nonzero_signal(self):
        child = self.Child(code=0)
        result, calls = self.run_cleanup(child)
        self.assertFalse([call for call in calls if call[1]])
        self.assertTrue(result['ownedProcessGroup']['quiescent'])
        self.assertNotIn('poll', child.events)

    def test_exit_after_poll_retains_identity_until_all_signals_complete(self):
        # poll(None) followed by child exit without reap still reserves its PID.
        child = self.Child()
        result, calls = self.run_cleanup(child)
        self.assertEqual(child.events, ['poll', 'signal-15', 'sleep', 'signal-9', 'communicate'])
        self.assertEqual([sig for _, sig, _ in calls if sig], [signal.SIGTERM, signal.SIGKILL])
        self.assertTrue(result['ownedProcessGroup']['identityRetainedForSignals'])
        self.assertTrue(result['ownedProcessGroup']['leaderReaped'])

    def test_poll_reaps_then_reused_group_is_read_only_failure(self):
        child = self.Child(poll_exit=True)
        result, calls = self.run_cleanup(child, surviving=True)
        self.assertFalse([call for call in calls if call[1]])
        self.assertIn('quiescenceError', result)
        self.assertTrue(result['ownedProcessGroup']['reapedBeforeSignals'])

    def test_surviving_descendant_after_reap_is_never_killed(self):
        child = self.Child(code=0)
        result, calls = self.run_cleanup(child, surviving=True)
        self.assertFalse([call for call in calls if call[1]])
        self.assertFalse(result['ownedProcessGroup']['quiescent'])
        self.assertIn('quiescenceError', result)

    def test_unknown_group_permission_is_not_disappearance(self):
        result, calls = self.run_cleanup(self.Child(code=0), denied=True)
        self.assertIn('quiescenceError', result)
        self.assertFalse(result['ownedProcessGroup']['quiescent'])
        self.assertFalse([call for call in calls if call[1]])

    def test_wrong_owned_group_blocks_all_nonzero_signals(self):
        result, calls = self.run_cleanup(self.Child(), pgid=999)
        self.assertFalse([call for call in calls if call[1]])
        self.assertIn('quiescenceError', result)

    def test_timeout_preserved_after_cleanup_reaps_zero_exit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(strict=True); (root / 'evidence').mkdir()
            probe = RUN.ReuseProbe(root / 'artifact', root / 'work', root / 'evidence', 'seal')
            child = self.Child(poll_exit=True)
            child.communicate = unittest.mock.Mock(side_effect=[subprocess.TimeoutExpired('synthetic', 1, output=b'partial'), ('final', '')])
            def disappeared(*args): raise ProcessLookupError('gone')
            with patch.object(RUN.subprocess, 'Popen', return_value=child), patch.object(RUN.os, 'killpg', side_effect=disappeared) as kill, \
                 patch.dict(os.environ, {'DEVELOPER_DIR': RUN.CAP.DEVELOPER}), patch.object(RUN.signal, 'getsignal', return_value=signal.SIG_DFL):
                result = probe.command(['xcodebuild', '-version'], 'synthetic-timeout', 1)
            self.assertEqual(result['error'], 'TimeoutExpired')
            self.assertIsNone(result['returncode'])
            self.assertEqual(result['terminationReturncode'], 0)
            self.assertTrue(all(call.args[1] == 0 for call in kill.call_args_list))

    def test_custom_sigchld_handler_blocks_process_start(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(strict=True); (root / 'evidence').mkdir()
            probe = RUN.ReuseProbe(root / 'artifact', root / 'work', root / 'evidence', 'seal')
            with patch.object(RUN.subprocess, 'Popen') as popen, patch.dict(os.environ, {'DEVELOPER_DIR': RUN.CAP.DEVELOPER}), \
                 patch.object(RUN.signal, 'getsignal', return_value=signal.SIG_IGN):
                result = probe.command(['xcodebuild', '-version'], 'unsafe-handler')
            popen.assert_not_called(); self.assertIn('SIGCHLD', result['error'])

    def test_exact_workflow_retained_visible_and_bound_to_current_seal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(strict=True); evidence = root / 'evidence'; evidence.mkdir()
            probe = RUN.ReuseProbe(root / 'artifact', root / 'work', evidence, RUN.APPROVED_INPUTS)
            raw = (ROOT / '.github/workflows/p2-host-reuse.yml').read_bytes()
            probe.binding = {'normalizedSources': {'.github/workflows/p2-host-reuse.yml': hashlib.sha256(RUN.normalized_source(raw, '.github/workflows/p2-host-reuse.yml')).hexdigest()}}
            probe.retain_workflow()
            self.assertEqual((evidence / 'workflow.yml').read_bytes(), raw)
            receipt = json.loads((evidence / 'workflow-source.json').read_text())
            self.assertEqual(receipt['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(receipt['approvedInputSHA256'], RUN.APPROVED_INPUTS)
            probe.approved = '0' * 64
            with self.assertRaisesRegex(ValueError, 'current seal differs'): probe.retain_workflow()

class OrchestrationTests(unittest.TestCase):
    def test_unreviewed_bytecode_cache_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(strict=True); target = root / 'ci/p2/host_reuse/run.py'; target.parent.mkdir(parents=True)
            target.write_bytes((HERE / 'run.py').read_bytes())
            parser = root / 'ci/p2/host_probe/install_contract.py'; parser.parent.mkdir(parents=True)
            parser.write_bytes((ROOT / 'ci/p2/host_probe/install_contract.py').read_bytes())
            (parser.parent / '__pycache__').mkdir()
            spec = importlib.util.spec_from_file_location('cached_reuse', target); module = importlib.util.module_from_spec(spec)
            with self.assertRaisesRegex(ValueError, 'bytecode cache before import'): spec.loader.exec_module(module)

    def test_finalization_deadline_refuses_new_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(strict=True); probe = RUN.ReuseProbe(root / 'artifact', root / 'work', root / 'evidence', 'seal')
            probe.deadline = 0
            with patch.object(RUN.CAP, 'archive_tree') as archive, self.assertRaisesRegex(ValueError, 'budget exhausted'):
                probe.archive_bounded(root, root / 'output.zip')
            archive.assert_not_called()

    def test_transitive_import_tamper_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(strict=True); target = root / 'ci/p2/host_reuse/run.py'; target.parent.mkdir(parents=True)
            target.write_bytes((HERE / 'run.py').read_bytes())
            parser = root / 'ci/p2/host_probe/install_contract.py'; parser.parent.mkdir(parents=True)
            marker = root / 'executed'; parser.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n')
            spec = importlib.util.spec_from_file_location('tampered_reuse', target); module = importlib.util.module_from_spec(spec)
            with self.assertRaisesRegex(ValueError, 'before import'): spec.loader.exec_module(module)
            self.assertFalse(marker.exists())

    def test_load_pinned_hashes_same_bytes_before_execution(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td).resolve(strict=True) / 'module.py'; p.write_text('raise AssertionError("must not run")\n')
            with self.assertRaisesRegex(ValueError, 'changed dependency'): RUN.load_pinned(p, '0' * 64, 'no_execute')

    def test_seal_normalization_is_unique_and_non_circular(self):
        digest = 'a' * 64
        raw = ("APPROVED_INPUTS = '" + digest + "'\nprint('payload')\n").encode()
        self.assertEqual(RUN.normalized_source(raw, 'run.py'), b"APPROVED_INPUTS = 'TO_BE_SEALED'\nprint('payload')\n")
        with self.assertRaises(ValueError): RUN.normalized_source(raw + raw, 'run.py')
        with self.assertRaises(ValueError): RUN.normalized_source(b'no seal', 'run.py')

    def test_native_command_excludes_overrides_and_has_one_destination(self):
        with tempfile.TemporaryDirectory() as td:
            probe = RUN.ReuseProbe(Path(td).resolve(strict=True) / 'artifact', Path(td).resolve(strict=True) / 'work', Path(td).resolve(strict=True) / 'evidence', 'seal')
            probe.device = '12345678-1234-1234-1234-123456789012'; probe.generated = Path(td).resolve(strict=True) / 'generated.xctestrun'
            argv = probe.native_command(); self.assertTrue(probe.allowed(argv)); self.assertEqual(argv.count('-destination'), 1)
            for value in ('-AppleLanguages', 'DYLD_INSERT_LIBRARIES=x', 'CODE_SIGNING_ALLOWED=NO'):
                self.assertFalse(probe.allowed(argv + [value]))
            self.assertFalse(probe.allowed(['xcrun', 'simctl', 'launch', probe.device, RUN.HP.BUNDLE_ID]))
            self.assertFalse(probe.allowed(['codesign', '--force', '--sign', '-', '/tmp/app']))

    def test_exhausted_shared_deadline_starts_no_process(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(strict=True); (root / 'evidence').mkdir()
            probe = RUN.ReuseProbe(root / 'artifact', root / 'work', root / 'evidence', 'seal'); probe.deadline = 0
            with patch.object(RUN.CAP.subprocess, 'Popen') as popen:
                result = probe.command(['xcodebuild', '-version'], 'expired')
            popen.assert_not_called(); self.assertTrue(result['deadlineExhausted']); self.assertIsNone(result['returncode'])
            self.assertTrue((root / 'evidence/commands/001.json').is_file())

    def test_render_repeated_tokens_and_project_scheme_parity(self):
        catalog = {'strings': {}}
        for key in ('Settings', 'Stay Informed About Refreshes', 'Later', 'Allow Notifications', 'LiveContainer can notify you when a refresh starts, completes, or needs attention. Nothing runs differently if you skip this.'):
            catalog['strings'][key] = {'localizations': {code: {'stringUnit': {'value': 'הגדרות' if code == 'he' and key == 'Settings' else key}} for code in ('en', 'he')}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve(strict=True); source = root / 'source'; (source / 'Resources').mkdir(parents=True)
            (source / 'Resources/Localizable.xcstrings').write_text(json.dumps(catalog)); (root / 'work').mkdir(); (root / 'evidence').mkdir()
            probe = RUN.ReuseProbe(root / 'artifact', root / 'work', root / 'evidence', 'seal')
            probe.identity = {'bundleIdentifier': RUN.HP.BUNDLE_ID, 'executableSHA256': 'a' * 64}
            probe.report['probeBuilderCommit'] = 'b' * 40; probe.runtime = {'runtimeRoot': '/Library/Developer/RuntimeRoot'}
            project = probe.prepare_runner(source)
            rendered = (root / 'work/runner/Sources/HostSettingsProbeTests.swift').read_text()
            self.assertNotRegex(rendered, r'__[_A-Z0-9]+__'); self.assertGreaterEqual(rendered.count('settingsTitle: "Settings"'), 2)
            self.assertEqual((project / 'project.pbxproj').read_bytes(), (ROOT / 'ci/p2/host_probe/project/project.pbxproj').read_bytes())
            self.assertEqual((project / 'xcshareddata/xcschemes/HostSettingsProbe.xcscheme').read_bytes(), (ROOT / 'ci/p2/host_probe/project/HostSettingsProbe.xcscheme').read_bytes())
            self.assertEqual(len(probe.generated_environment), 7)
            self.assertIn('expectedInitialEnvironment: [String: String] = [:]', rendered)
            policy = json.loads((root / 'evidence/expected-requested-launch-policy.json').read_text())
            self.assertEqual(policy['initialEnvironment'], {})
            self.assertEqual(policy['generatedTargetEnvironment'], probe.generated_environment)

    def test_swift_policy_is_durable_redacted_and_before_single_launch(self):
        raw = (HERE / 'HostSettingsProbeTests.swift.in').read_text()
        self.assertEqual(raw.count('app.launch()'), 1)
        self.assertIn('FileHandle.standardOutput.write(line)', raw)
        self.assertIn('private func publishLaunchPolicy() -> Bool', raw)
        self.assertEqual(raw.count('guard publishLaunchPolicy() else { return }'), 3)
        self.assertIn('return false', raw)
        self.assertNotIn('XCTAssertEqual(initialEnvironment', raw); self.assertNotIn('XCTAssertEqual(initialArguments', raw)
        self.assertIn('if initialMatches { launchPolicy["initialEnvironment"] = initialEnvironment }', raw)
        self.assertIn('if initialArguments.isEmpty { launchPolicy["initialArguments"] = initialArguments }', raw)
        self.assertLess(raw.index('publishLaunchPolicy()', raw.index('let initialEnvironment =')), raw.index('guard initialEnvironment =='))
        self.assertLess(raw.index('app.launchEnvironment = [:]'), raw.index('app.launch()'))
        self.assertLess(raw.index('app.launchArguments = []'), raw.index('app.launch()'))
        self.assertNotIn('AppleLanguages', raw); self.assertNotIn('AppleLocale', raw)

    def test_postbuild_checks_precede_launch_and_postchecks_follow(self):
        raw = (HERE / 'run.py').read_text()
        start = raw.index('def execute(self):'); end = raw.index('def harvest_results(self):')
        body = raw[start:end]
        self.assertLess(body.index('RC.inspect_products('), body.index("self.stage = 'single-native-settings-route'"))
        self.assertLess(body.index("'prelaunch'"), body.index('self.command(self.native_command()'))
        self.assertLess(body.index('verify_observations_with_policy('), body.index("'postnavigation'"))
        self.assertGreater(body.index("self.report['status'] = 'BOUNDED_SETTINGS_ROUTE_OBSERVED'"), body.index("'postnavigation'"))


class TemporaryRootAliasTests(unittest.TestCase):
    def run_under_owned_temp_alias(self, cases):
        # Canonicalize only directories created and owned by these fixtures.
        # Runtime paths and attacker-created symlinks retain strict rejection.
        original = tempfile.TemporaryDirectory
        with original() as td:
            parent = Path(td).resolve(strict=True)
            real = parent / 'real'; real.mkdir()
            alias = parent / 'temp-alias'; alias.symlink_to(real, target_is_directory=True)
            created = []
            def aliased_directory(*args, **kwargs):
                self.assertNotIn('dir', kwargs)
                temporary = original(*args, dir=str(alias), **kwargs)
                created.append(Path(temporary.name))
                self.assertIn(alias, Path(temporary.name).parents)
                return temporary
            with patch.object(tempfile, 'TemporaryDirectory', side_effect=aliased_directory):
                for case in cases:
                    with self.subTest(case=case.id()):
                        result = unittest.TestResult()
                        case.run(result)
                        self.assertEqual(result.testsRun, 1)
                        self.assertEqual(result.skipped, [])
                        self.assertEqual(result.errors, [])
                        self.assertEqual(result.failures, [])
            self.assertEqual(len(created), len(cases))
            self.assertTrue(alias.is_symlink())
            self.assertEqual(list(real.iterdir()), [])

    def test_actual_fixtures_use_canonical_owned_temp_roots(self):
        from test_artifact_contract import ArtifactContractTests
        from test_fetch_artifact import FetchArtifactTests
        self.run_under_owned_temp_alias([
            ArtifactContractTests('test_complete_authentication_and_restore'),
            FetchArtifactTests('test_success_exact_bytes_and_api_only_credentials'),
            GeneratedContractTests('test_exact_reference_and_universal_products'),
            OrchestrationTests('test_load_pinned_hashes_same_bytes_before_execution'),
        ])

    def test_actual_guards_still_reject_attacker_linked_paths(self):
        from test_artifact_contract import ArtifactContractTests
        from test_fetch_artifact import FetchArtifactTests
        self.run_under_owned_temp_alias([
            ArtifactContractTests('test_restore_cannot_write_through_symlink'),
            FetchArtifactTests('test_symlinked_output_parent_is_rejected'),
            GeneratedContractTests('test_linked_runner_fails'),
        ])


if __name__ == '__main__': unittest.main()
