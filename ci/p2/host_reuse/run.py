#!/usr/bin/env python3
"""One bounded Settings probe using an authenticated historical built app.

No genuine-host compilation, product changes, signing repair or account actions.
Only requested XCUIApplication launch inputs are cleared; diagnostic instrumentation remains.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
import types

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE_COMMIT = 'bcd6d06b94853537cc6c66474d0b2b65a2712eb1'
APPROVED_INPUTS = 'e321ff3eb7f86b1f725a6f551994a9a13ec68abb700e2fe88011a13a1fc956b8'
EXPERIMENT_SECONDS = 18 * 60
FINALIZATION_SECONDS = 6 * 60


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_pinned(path, digest, name):
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), 'Linked/missing dependency')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == digest, 'Unrestored/changed dependency: ' + str(path))
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


INSTALL_PATH = ROOT / 'ci/p2/host_probe/install_contract.py'
require(INSTALL_PATH.is_file() and not any(p.is_symlink() for p in (INSTALL_PATH, *INSTALL_PATH.parents)) and
        sha(INSTALL_PATH) == 'b2fcf4494444eb908f7a58a3b25a55dfc8d0c73becb9d1be6a121fd342b210ac', 'Transitive install contract changed before import')
require(not (INSTALL_PATH.parent / '__pycache__').exists() and not INSTALL_PATH.with_suffix('.pyc').exists(), 'Unreviewed transitive bytecode cache before import')
CAP = load_pinned(ROOT / 'ci/p2/runner_capability/run.py', '71813f04ea31e177bc110de5dc6e537667c550fecd888b4c809a389c25050e1a', 'p2_reuse_command_runtime')
HP = load_pinned(ROOT / 'ci/p2/host_probe/run.py', '72dd4074ea9e03c09ec967f2b8e5841af521402af884c37d5fe5aca3eded547e', 'p2_reuse_unchanged_host_checks')
RC = AC = None


def normalized_source(raw, filename):
    pattern = rb"APPROVED_INPUTS = '[a-f0-9]{64}'" if filename.endswith('run.py') else rb'APPROVED_HOST_REUSE_INPUTS_SHA256: [a-f0-9]{64}'
    replacement = b"APPROVED_INPUTS = 'TO_BE_SEALED'" if filename.endswith('run.py') else b'APPROVED_HOST_REUSE_INPUTS_SHA256: TO_BE_SEALED'
    value, count = re.subn(pattern, replacement, raw)
    require(count == 1, 'Seal placeholder must occur exactly once')
    return value


def verify_observations_with_policy(attachments, identity, builder, expected):
    observations = HP.verify_observations(attachments, identity, builder)
    return RC.verify_launch_policy(observations['report'], expected)


def finish_owned_child(process):
    """Signal only a retained, unreaped child identity. Never a reaped PGID."""
    state = {'pgid': process.pid, 'quiescent': False, 'pipesJoined': False,
             'signals': [], 'identityRetainedForSignals': False, 'reapedBeforeSignals': False,
             'postReapPolicy': 'read-only-group-disappearance', 'errors': []}
    result = {'ownedProcessGroup': state}
    try:
        # SIG_IGN/custom handlers could reap asynchronously and invalidate PID
        # reservation. This driver is single-threaded and owns all child waits.
        require(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL, 'SIGCHLD identity reservation unavailable')
        if process.returncode is None:
            process.poll()  # Last possible reap before the nonzero-signal phase.
        state['reapedBeforeSignals'] = process.returncode is not None
        if process.returncode is None:
            require(os.getpgid(process.pid) == process.pid, 'Owned child process group differs')
            state['identityRetainedForSignals'] = True
            # No poll/wait/communicate is allowed between this reservation and
            # the last signal. An exited child remains an unreaped zombie, so
            # its PID/PGID cannot be reused by an unrelated process group.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                require(process.returncode is None and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL,
                        'Owned child identity lost before signal')
                try:
                    os.killpg(process.pid, sig)
                    state['signals'].append(int(sig))
                except ProcessLookupError:
                    break
                if sig == signal.SIGTERM:
                    time.sleep(0.5)  # Bounded grace, deliberately without reaping.
    except Exception as error:
        state['errors'].append(type(error).__name__ + ': ' + str(error))
    # The signal phase has irrevocably ended. No nonzero signal below here,
    # even if a descendant retains pipes or a numeric group is reused.
    try:
        stdout, stderr = process.communicate(timeout=3)
        result.update(stdout=stdout, stderr=stderr)
        state['pipesJoined'] = True
    except Exception as error:
        state['errors'].append('Pipe join: ' + type(error).__name__)
        try:
            process.wait(timeout=1)
        except Exception as wait_error:
            state['errors'].append('Owned child reap: ' + type(wait_error).__name__)
    result['terminationReturncode'] = process.returncode
    state['leaderReaped'] = process.returncode is not None
    until = time.monotonic() + 1
    while True:
        try:
            os.killpg(process.pid, 0)  # Read-only observation, never ownership proof.
        except ProcessLookupError:
            state['quiescent'] = state['leaderReaped']
            break
        except OSError as error:
            state['errors'].append('Group observation: ' + type(error).__name__)
            break
        if time.monotonic() >= until:
            state['errors'].append('Surviving or reused group; no post-reap signal permitted')
            break
        time.sleep(0.05)
    if state['errors'] or not state['quiescent'] or not state['pipesJoined']:
        result['quiescenceError'] = 'Owned child cleanup/observation incomplete; fail closed'
    return result


class IdentityAdapter(HP.Probe):
    def __init__(self, owner):
        self.owner, self.app, self.installed = owner, owner.app, owner.installed
        self.evidence, self.identity = owner.evidence, owner.identity
        self.installed_baseline = None
        self.number = 0

    def command(self, *argv, timeout=300):
        value = self.owner.checked_command(list(argv), 'installed-signature', timeout)
        self.number = self.owner.number
        return value


class ReuseProbe(CAP.Capability):
    def __init__(self, artifact, work, evidence, approved):
        super().__init__(work, evidence, approved)
        self.artifact = Path(artifact)
        self.deadline = time.monotonic() + EXPERIMENT_SECONDS
        self.device = self.app = self.installed = self.identity = self.adapter = None
        self.runner_snapshot = self.source_snapshot = self.expected_environment = None
        self.generated = self.wiring = None
        self.native_result = self.work / 'host-probe.xcresult'
        self.attachments = self.work / 'attachments'
        self.device_name = 'P2-Reused-Host-' + os.environ.get('GITHUB_RUN_ID', 'local') + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '1')
        self.finalizing = False
        self.report = {'schemaVersion': 1, 'status': 'BLOCKED', 'scope': 'One unchanged reused app EN→HE→EN Settings probe',
                       'sourceCommit': HP.SOURCE, 'sourceTree': HP.TREE,
                       'productBuildRunId': 39064168648, 'productBuilderCommit': '4cb67e4d252ba1b1f7a6fa6431560f75264f337c',
                       'hostBuiltThisRun': False, 'hostInstalled': False, 'hostLaunchAttempted': False, 'hostLaunched': False,
                       'testExecutionRequested': False, 'nativeTestExecuted': False,
                       'performanceCheckerEnabled': True, 'effectiveEnvironmentEmptyProven': False,
                       'fullAppAcceptance': False, 'finalCompositionAcceptance': False,
                       'experimentBudgetSeconds': EXPERIMENT_SECONDS, 'finalizationBudgetSeconds': FINALIZATION_SECONDS,
                       'errors': [], 'retentionErrors': [], 'outcomes': {}}

    def command(self, argv, label, timeout=300):
        # Deliberate lane-local replacement: the inherited command runtime can
        # signal a numeric group after communicate has reaped its leader.
        self.number += 1
        argv = [str(value) for value in argv]
        stem = self.evidence / 'commands' / f'{self.number:03d}'
        stem.parent.mkdir(parents=True, exist_ok=True)
        receipt = {'argv': argv, 'label': label, 'stage': self.stage, 'timeoutSeconds': timeout,
                   'requestedTimeoutSeconds': timeout, 'returncode': None, 'stdout': '', 'stderr': '',
                   'developerDir': os.environ.get('DEVELOPER_DIR'), 'ambientOverrideKeys': CAP.override_keys(os.environ)}
        started, process = time.monotonic(), None
        try:
            remaining = self.deadline - time.monotonic()
            receipt.update(remainingExperimentSeconds=max(0, remaining), timeoutSeconds=min(timeout, max(0, remaining)), deadlineExhausted=remaining <= 0)
            require(remaining > 0, 'Shared experiment deadline exhausted')
            require(self.allowed(argv), 'Command outside reused-host allowlist')
            require(not receipt['ambientOverrideKeys'], 'Ambient build/launch overrides forbidden')
            require(os.environ.get('DEVELOPER_DIR') == CAP.DEVELOPER, 'Selected developer directory differs')
            require(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL, 'SIGCHLD identity reservation unavailable')
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            stdout, stderr = process.communicate(timeout=receipt['timeoutSeconds'])
            receipt.update(returncode=process.returncode, stdout=stdout, stderr=stderr)
        except subprocess.TimeoutExpired as error:
            def decode(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
            receipt.update(stdout=decode(error.stdout), stderr=decode(error.stderr), error='TimeoutExpired')
        except Exception as error:
            receipt['error'] = type(error).__name__ + ': ' + str(error)
        finally:
            if process is not None:
                receipt.update(finish_owned_child(process))
                self.command_quiescence.append({'label': label, **receipt['ownedProcessGroup']})
            receipt['elapsedSeconds'] = time.monotonic() - started
            stem.with_suffix('.stdout').write_text(receipt['stdout'])
            stem.with_suffix('.stderr').write_text(receipt['stderr'])
            CAP.write(stem.with_suffix('.json'), {k: v for k, v in receipt.items() if k not in ('stdout', 'stderr')})
        return receipt

    def allowed(self, argv):
        reads = [['git', '-C', str(ROOT), 'rev-parse', 'HEAD', 'HEAD^{tree}'],
                 ['git', '-C', str(ROOT), 'status', '--porcelain=v1', '--untracked-files=all'],
                 ['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', BASE_COMMIT, 'HEAD'],
                 ['xcodebuild', '-version'], ['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'],
                 ['xcrun', 'simctl', 'list', 'runtimes', '--json'], ['xcrun', 'simctl', 'list', 'devicetypes', '--json'],
                 ['xcrun', 'simctl', 'list', 'devices', '--json']]
        if argv in reads:
            return True
        if argv == ['xcrun', 'simctl', 'create', self.device_name, CAP.DEVICE_TYPE, CAP.RUNTIME]:
            return not self.finalizing and self.device is None
        if self.device:
            permitted = [['xcrun', 'simctl', 'shutdown', self.device]]
            if not self.finalizing:
                permitted += [['xcrun', 'simctl', 'boot', self.device], ['xcrun', 'simctl', 'bootstatus', self.device, '-b'],
                              ['xcrun', 'simctl', 'listapps', self.device],
                              ['xcrun', 'simctl', 'get_app_container', self.device, HP.BUNDLE_ID, 'app']]
                if self.app:
                    permitted += [['xcrun', 'simctl', 'install', self.device, str(self.app)]]
            if argv in permitted:
                return True
        for root in (self.app, self.installed):
            if root and argv in [['codesign', '--verify', '--deep', '--strict', str(root if part is None else root / part)]
                                 for part in (None, *sorted(HP.INSTALL_CONTRACT.ALLOWED_DYLIB_PATHS))]:
                return not self.finalizing
        if self.runner_base and not self.finalizing:
            if argv in [self.runner_base + ['build-for-testing', '-showBuildSettings', '-json'],
                        self.runner_base + ['-resultBundlePath', str(self.result_bundle), 'build-for-testing']]:
                return True
        if self.generated and self.device and not self.finalizing:
            if argv == self.native_command():
                return True
        return argv in [['xcrun', 'xcresulttool', 'get', 'test-results', kind, '--path', str(self.native_result)] for kind in ('summary', 'tests')] or \
            argv == ['xcrun', 'xcresulttool', 'export', 'attachments', '--path', str(self.native_result), '--output-path', str(self.attachments)]

    def checked_command(self, argv, label, timeout=300):
        receipt = self.command(argv, label, timeout)
        require(receipt['returncode'] == 0 and not receipt.get('error') and not receipt.get('quiescenceError'), 'Command failed: ' + label)
        return receipt['stdout']

    def native_command(self):
        return ['xcodebuild', 'test-without-building', '-xctestrun', str(self.generated),
                '-destination', 'platform=iOS Simulator,id=' + self.device, '-parallel-testing-enabled', 'NO',
                '-maximum-concurrent-test-simulator-destinations', '1', '-resultBundlePath', str(self.native_result)]

    def verify_inputs(self):
        global RC, AC
        require(self.approved == APPROVED_INPUTS and sha(HERE / 'binding.json') == APPROVED_INPUTS, 'Approved reuse input seal differs')
        self.binding = CAP.strict_json((HERE / 'binding.json').read_text())
        require({p.relative_to(HERE).as_posix() for p in HERE.rglob('*') if not p.is_dir()} ==
                set(self.binding['files']) | {'binding.json', 'run.py'}, 'Unexpected/missing reuse input file set')
        for name, digest in self.binding['dependencies'].items():
            path = ROOT / name
            CAP.unlinked_within(path, ROOT)
            require(sha(path) == digest, 'Immutable dependency changed: ' + name)
            if path.suffix == '.py':
                require(not (path.parent / '__pycache__').exists() and not path.with_suffix('.pyc').exists(), 'Unreviewed dependency bytecode cache')
        for name, digest in self.binding['files'].items():
            CAP.unlinked_within(HERE / name, HERE)
            require(sha(HERE / name) == digest, 'Reuse input source changed: ' + name)
        for name, digest in self.binding['normalizedSources'].items():
            require(hashlib.sha256(normalized_source((ROOT / name).read_bytes(), name)).hexdigest() == digest, 'Normalized source changed: ' + name)
        RC = load_pinned(HERE / 'runner_contract.py', self.binding['files']['runner_contract.py'], 'p2_reuse_runner_contract')
        AC = load_pinned(HERE / 'artifact_contract.py', self.binding['files']['artifact_contract.py'], 'p2_reuse_artifact_contract')
        self.source_snapshot = CAP.snapshot(HERE)
        CAP.write(self.evidence / 'input-binding.json', self.binding)

    def guard_inputs(self):
        require(CAP.snapshot(HERE) == self.source_snapshot, 'Reuse diagnostic sources changed')
        for name, digest in self.binding['dependencies'].items():
            require(sha(ROOT / name) == digest, 'Pinned dependency changed: ' + name)
        for name, digest in self.binding['normalizedSources'].items():
            require(hashlib.sha256(normalized_source((ROOT / name).read_bytes(), name)).hexdigest() == digest, 'Normalized source changed: ' + name)
        if self.runner_snapshot is not None:
            require(CAP.snapshot(self.work / 'runner') == self.runner_snapshot, 'Generated runner source changed')

    def prepare_runner(self, source):
        project = HP.prepare_runner(self.work / 'runner', self.identity, self.report['probeBuilderCommit'],
                                    CAP.strict_json((source / 'Resources/Localizable.xcstrings').read_text()))
        catalog = CAP.strict_json((source / 'Resources/Localizable.xcstrings').read_text())
        replacements = {'__HOST_BUNDLE_ID__': HP.swift_literal(self.identity['bundleIdentifier']),
                        '__HOST_EXECUTABLE_SHA256__': HP.swift_literal(self.identity['executableSHA256']),
                        '__SOURCE_COMMIT__': HP.swift_literal(HP.SOURCE), '__SOURCE_TREE__': HP.swift_literal(HP.TREE),
                        '__BUILDER_COMMIT__': HP.swift_literal(self.report['probeBuilderCommit'])}
        for code, name in [('en', 'EN'), ('he', 'HE')]:
            replacements['__SETTINGS_' + name + '__'] = HP.swift_literal(catalog['strings']['Settings']['localizations'][code]['stringUnit']['value'])
        notice = {'TITLE': 'Stay Informed About Refreshes', 'LATER': 'Later', 'ALLOW': 'Allow Notifications',
                  'BODY': 'LiveContainer can notify you when a refresh starts, completes, or needs attention. Nothing runs differently if you skip this.'}
        for name, key in notice.items():
            require(catalog['strings'][key]['localizations']['en']['stringUnit']['value'] == key, 'Known notice changed')
            replacements['__NOTICE_' + name + '__'] = HP.swift_literal(key)
        template = (HERE / 'HostSettingsProbeTests.swift.in').read_text()
        for token, value in replacements.items():
            require(token in template, 'Missing reviewed Swift token')
            template = template.replace(token, value)
        testroot = self.work / 'runner-derived/Build/Products'
        binding = RC.roots(testroot, Path(CAP.DEVELOPER), Path(self.runtime['runtimeRoot']))
        self.expected_environment = RC.requested_environment(binding)
        literal = '[' + ', '.join(HP.swift_literal(k) + ': ' + HP.swift_literal(v) for k, v in sorted(self.expected_environment.items())) + ']'
        template = template.replace('__EXPECTED_INITIAL_ENVIRONMENT__', literal)
        require(not re.search(r'__[_A-Z0-9]+__', template), 'Unresolved Swift token')
        (self.work / 'runner/Sources/HostSettingsProbeTests.swift').write_text(template)
        self.runner_snapshot = CAP.snapshot(self.work / 'runner')
        CAP.write(self.evidence / 'expected-requested-launch-policy.json', {'initialEnvironment': self.expected_environment,
                  'initialArguments': [], 'requestedEnvironmentAfterClearing': {}, 'requestedArgumentsAfterClearing': [],
                  'getterEqualityObserved': False, 'performanceCheckerEnabled': True, 'effectiveEnvironmentEmptyProven': False})
        return project

    def execute(self):
        self.verify_inputs()
        self.stage = 'historical-build-authentication'
        reused = AC.authenticate_artifact(self.artifact, self.work, self.evidence)
        self.app, self.identity, self.settings = reused['app'], reused['identity'], reused['settings']
        self.report['reuseProvenance'] = reused['provenance']
        require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Pinned macOS arm64 required')
        require(not CAP.override_keys(os.environ), 'Ambient build/launch overrides forbidden')
        identity = self.checked_command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD', 'HEAD^{tree}'], 'probe-identity').splitlines()
        require(len(identity) == 2 and all(re.fullmatch('[a-f0-9]{40}', value) for value in identity), 'Malformed probe identity')
        self.report.update(probeBuilderCommit=identity[0], probeBuilderTree=identity[1])
        require(not self.checked_command(['git', '-C', str(ROOT), 'status', '--porcelain=v1', '--untracked-files=all'], 'probe-cleanliness').strip(), 'Probe checkout changed')
        self.checked_command(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', BASE_COMMIT, 'HEAD'], 'probe-ancestry')
        require(self.checked_command(['xcodebuild', '-version'], 'toolchain').strip() == 'Xcode 26.4.1\nBuild version 17E202', 'Toolchain changed')
        require(self.checked_command(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'], 'sdk').strip() == '26.4', 'SDK changed')
        runtimes = CAP.strict_json(self.checked_command(['xcrun', 'simctl', 'list', 'runtimes', '--json'], 'runtimes'))
        matches = [r for r in runtimes['runtimes'] if r.get('identifier') == CAP.RUNTIME and r.get('isAvailable') is True]
        require(len(matches) == 1 and matches[0].get('buildversion') == '23F77' and matches[0].get('version') == '26.5', 'Pinned runtime unavailable')
        self.runtime = matches[0]
        types_ = CAP.strict_json(self.checked_command(['xcrun', 'simctl', 'list', 'devicetypes', '--json'], 'device-types'))
        require(sum(d.get('identifier') == CAP.DEVICE_TYPE for d in types_['devicetypes']) == 1, 'Pinned device type unavailable')
        CAP.write(self.evidence / 'environment.json', {'developerDir': CAP.DEVELOPER, 'runtime': self.runtime,
                  'architecture': platform.machine(), 'xcodeVersion': 'Xcode 26.4.1\nBuild version 17E202'})
        self.stage = 'restored-built-app-resources-and-signatures'
        self.guard_inputs()
        actual = HP.verify_product(self.app, self.settings)
        require(actual == {k: v for k, v in self.identity.items() if k != 'machOPlatforms'}, 'Restored real product identity differs')
        CAP.write(self.evidence / 'provenance/product-identity.json', {'path': str(self.app), **self.identity})
        resources = HP.compiled_resource_contracts(self.app, reused['sourceRoot'])
        CAP.write(self.evidence / 'actual-host-compiled-resources.json', resources)
        require(all(resources[k]['status'] == 'passed' for k in ('localization', 'exactPluralValues', 'permissions')), 'Fresh compiled-resource check failed')
        for part in (None, *sorted(HP.INSTALL_CONTRACT.ALLOWED_DYLIB_PATHS)):
            self.checked_command(['codesign', '--verify', '--deep', '--strict', str(self.app if part is None else self.app / part)], 'restored-signature')
        require(HP.verify_product(self.app, self.settings) == actual, 'Restored app changed during strict signature checks')
        self.stage = 'fresh-simulator-install'
        self.device = self.checked_command(['xcrun', 'simctl', 'create', self.device_name, CAP.DEVICE_TYPE, CAP.RUNTIME], 'create-simulator').strip()
        require(re.fullmatch(r'[A-Fa-f0-9-]{36}', self.device), 'Invalid created simulator identifier')
        CAP.write(self.evidence / 'created-simulator.json', {'udid': self.device, 'name': self.device_name, 'runtime': CAP.RUNTIME, 'fresh': True})
        self.checked_command(['xcrun', 'simctl', 'boot', self.device], 'boot-simulator')
        self.checked_command(['xcrun', 'simctl', 'bootstatus', self.device, '-b'], 'bootstatus', 180)
        listapps = self.checked_command(['xcrun', 'simctl', 'listapps', self.device], 'installed-before')
        require(HP.BUNDLE_ID not in listapps, 'Genuine app unexpectedly exists before fresh installation')
        self.checked_command(['xcrun', 'simctl', 'install', self.device, str(self.app)], 'install-authenticated-app')
        self.report['hostInstalled'] = True
        self.installed = Path(self.checked_command(['xcrun', 'simctl', 'get_app_container', self.device, HP.BUNDLE_ID, 'app'], 'installed-container').strip())
        require(self.installed.is_absolute() and self.installed.name == 'LiveContainer.app' and self.device in self.installed.parts and
                not any(p.is_symlink() for p in (self.installed, *self.installed.parents)), 'Unexpected installed container path')
        self.adapter = IdentityAdapter(self)
        self.adapter.installed_identity_gate(self.settings, 'initial', 'Fresh installed identity failed')
        self.adapter.installed_signature_gate(self.settings, 'initial')
        self.stage = 'fresh-runner-build'
        project = self.prepare_runner(reused['sourceRoot'])
        self.runner_base = ['xcodebuild', '-project', str(project), '-scheme', 'HostSettingsProbe', '-configuration', 'Debug',
                            '-sdk', 'iphonesimulator', '-destination', 'platform=iOS Simulator,id=' + self.device,
                            '-derivedDataPath', str(self.work / 'runner-derived')]
        self.guard_inputs()
        raw = self.checked_command(self.runner_base + ['build-for-testing', '-showBuildSettings', '-json'], 'runner-settings')
        (self.evidence / 'runner-build-settings.json').write_text(raw)
        CAP.settings_contract(raw)
        self.checked_command(self.runner_base + ['-resultBundlePath', str(self.result_bundle), 'build-for-testing'], 'runner-build', 600)
        self.guard_inputs()
        generated = list((self.work / 'runner-derived/Build/Products').glob('*.xctestrun'))
        require(len(generated) == 1, 'Ambiguous generated xctestrun')
        self.generated = generated[0]
        self.generated_hash = sha(self.generated)
        shutil.copyfile(self.generated, self.evidence / 'generated.xctestrun')
        self.wiring = RC.validate_generated(self.generated.read_bytes(), self.generated.parent, self.app, Path(CAP.DEVELOPER), Path(self.runtime['runtimeRoot']))
        require(self.wiring['expectedInitialEnvironment'] == self.expected_environment, 'Compiled getter expectation differs from generated configuration')
        CAP.write(self.evidence / 'runner-wiring.json', self.wiring)
        self.runner_products = RC.inspect_products(self.wiring, HP.INSTALL_CONTRACT)
        CAP.write(self.evidence / 'runner-products.json', self.runner_products)
        self.adapter.installed_identity_gate(self.settings, 'prelaunch', 'Installed identity changed before launch')
        self.adapter.installed_signature_gate(self.settings, 'prelaunch')
        self.guard_inputs()
        self.stage = 'single-native-settings-route'
        self.report['testExecutionRequested'] = True
        receipt = self.command(self.native_command(), 'native-settings-test', 240)
        self.retain_try('unbuffered launch policy events', lambda: CAP.write(self.evidence / 'requested-launch-policy-events.json',
            RC.policy_events(receipt['stdout'] + '\n' + receipt['stderr'], self.expected_environment)))
        self.harvest_results()
        require(receipt['returncode'] == 0 and not receipt.get('error') and not receipt.get('quiescenceError'), 'Native test did not pass')
        require(sha(self.generated) == self.generated_hash, 'Generated xctestrun changed during execution')
        HP.verify_summary(CAP.strict_json((self.evidence / 'xctest-summary.json').read_text()), CAP.strict_json((self.evidence / 'xctest-tests.json').read_text()))
        policy = verify_observations_with_policy(self.attachments, self.identity, self.report['probeBuilderCommit'], self.expected_environment)
        RC.verify_policy_events(CAP.strict_json((self.evidence / 'requested-launch-policy-events.json').read_text()), self.expected_environment)
        CAP.write(self.evidence / 'verified-requested-launch-policy.json', policy)
        self.report.update(nativeTestExecuted=True, hostLaunchAttempted=True, hostLaunched=True)
        self.stage = 'postnavigation-identity'
        self.adapter.installed_identity_gate(self.settings, 'postnavigation', 'Installed identity changed after navigation')
        self.adapter.installed_signature_gate(self.settings, 'postnavigation')
        require(RC.inspect_products(self.wiring, HP.INSTALL_CONTRACT) == self.runner_products, 'Runner products changed during execution')
        self.guard_inputs()
        self.report['status'] = 'BOUNDED_SETTINGS_ROUTE_OBSERVED'

    def harvest_results(self):
        if not self.native_result.is_dir():
            return
        for kind in ('summary', 'tests'):
            if not (self.evidence / ('xctest-' + kind + '.json')).exists():
                self.retain_try('XCTest ' + kind, lambda kind=kind: CAP.write(self.evidence / ('xctest-' + kind + '.json'),
                    CAP.strict_json(self.checked_command(['xcrun', 'xcresulttool', 'get', 'test-results', kind, '--path', str(self.native_result)], 'export-' + kind))))
        if not self.attachments.exists():
            self.retain_try('XCTest attachments', lambda: self.checked_command(['xcrun', 'xcresulttool', 'export', 'attachments',
                            '--path', str(self.native_result), '--output-path', str(self.attachments)], 'export-attachments'))
        if self.attachments.is_dir():
            try:
                rows = HP.attachment_map(self.attachments)
                observation = CAP.strict_json(rows['host-probe-observations.json'].read_text())
                CAP.write(self.evidence / 'native-observation.json', observation)
                self.report['nativeTestExecuted'] = True
                self.report['hostLaunchAttempted'] = type(observation.get('launchCount')) is int and observation['launchCount'] > 0
            except Exception as error:
                self.report['retentionErrors'].append('Native observation extraction: ' + type(error).__name__ + ': ' + str(error))

    def retain(self):
        self.finalizing = True
        self.deadline = time.monotonic() + FINALIZATION_SECONDS
        self.harvest_results()
        if self.generated is not None and self.generated.exists():
            self.retain_try('generated xctestrun stability', lambda: CAP.write(self.evidence / 'xctestrun-hashes.json',
                {'before': self.generated_hash, 'after': sha(self.generated)}))
            if sha(self.generated) != self.generated_hash:
                self.report['retentionErrors'].append('Generated xctestrun changed')
        if self.device:
            self.retain_try('shutdown owned simulator', lambda: self.checked_command(['xcrun', 'simctl', 'shutdown', self.device], 'shutdown-owned-simulator', 60))
        CAP.write(self.evidence / 'owned-command-groups.json', self.command_quiescence)
        if any(not row.get('quiescent') or not row.get('pipesJoined') for row in self.command_quiescence):
            self.report['retentionErrors'].append('Unresolved owned command writers')
        for name, source in [('generated-runner', self.work / 'runner'), ('runner-derived', self.work / 'runner-derived'),
                             ('runner-build.xcresult', self.result_bundle), ('host-probe.xcresult', self.native_result),
                             ('attachments', self.attachments), ('restored-built-app', self.app), ('installed-app', self.installed)]:
            if source and source.exists():
                self.retain_try('archive ' + name, lambda name=name, source=source: self.archive_bounded(source, self.evidence / (name + '.zip')))
        self.retain_try('diagnostic sources', lambda: self.archive_bounded(HERE, self.evidence / 'diagnostic-sources.zip'))
        if self.binding:
            self.retain_try('exact workflow source', self.retain_workflow)
            for name in self.binding['dependencies']:
                def retain_dependency(name=name):
                    target = self.evidence / 'immutable-dependencies' / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / name, target)
                self.retain_try('dependency ' + name, retain_dependency)
        if self.source_snapshot is not None:
            self.retain_try('final source immutability', self.guard_inputs)
        if self.report['errors'] or self.report['retentionErrors']:
            self.report['status'] = 'BLOCKED'
        self.report['stage'] = self.stage
        CAP.write(self.evidence / 'reuse-probe.json', self.report)
        index = {}
        for path in sorted(self.evidence.rglob('*')):
            require(not path.is_symlink(), 'Unexpected raw evidence symlink')
            if path.is_file():
                index[path.relative_to(self.evidence).as_posix()] = {'type': 'regular', 'bytes': path.stat().st_size, 'sha256': sha(path)}
        CAP.write(self.evidence / 'evidence-sha256.json', index)

    def archive_bounded(self, source, destination):
        require(time.monotonic() < self.deadline, 'Finalization budget exhausted before archive')
        CAP.archive_tree(source, destination)
        require(time.monotonic() < self.deadline, 'Finalization budget exceeded during archive; partial diagnostics retained')

    def retain_workflow(self):
        name = '.github/workflows/p2-host-reuse.yml'
        path = ROOT / name
        CAP.unlinked_within(path, ROOT)
        raw = path.read_bytes()
        normalized = hashlib.sha256(normalized_source(raw, name)).hexdigest()
        require(normalized == self.binding['normalizedSources'][name], 'Workflow normalized source differs')
        require(raw.count(('APPROVED_HOST_REUSE_INPUTS_SHA256: ' + self.approved).encode()) == 1, 'Workflow current seal differs')
        retained = self.evidence / 'workflow.yml'
        retained.write_bytes(raw)
        require(retained.read_bytes() == raw, 'Retained workflow bytes differ')
        CAP.write(self.evidence / 'workflow-source.json', {'sourcePath': name, 'retainedPath': 'workflow.yml',
                  'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'normalizedSHA256': normalized,
                  'approvedInputSHA256': self.approved})


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--artifact', type=Path, required=True)
    cli.add_argument('--work', type=Path, required=True)
    cli.add_argument('--evidence', type=Path, required=True)
    cli.add_argument('--approved-inputs-sha256', required=True)
    args = cli.parse_args()
    paths = [p.absolute() for p in (args.work, args.evidence)]
    for path in paths:
        require(not path.exists() and not path.is_symlink() and not any(p.is_symlink() for p in path.parents), 'Fresh unlinked output paths required')
        require(not path.is_relative_to(ROOT) and not ROOT.is_relative_to(path), 'Output paths must be outside repository')
    work, evidence = paths
    require(not work.is_relative_to(evidence) and not evidence.is_relative_to(work), 'Outputs must be disjoint')
    work.mkdir(parents=True)
    evidence.mkdir(parents=True)
    probe = ReuseProbe(args.artifact, work, evidence, args.approved_inputs_sha256)
    try:
        probe.execute()
    except Exception as error:
        probe.report['errors'].append(type(error).__name__ + ': ' + str(error))
    finally:
        probe.retain()
    print(json.dumps(probe.report, indent=2, sort_keys=True))
    return 0 if probe.report['status'] == 'BOUNDED_SETTINGS_ROUTE_OBSERVED' else 1


if __name__ == '__main__':
    sys.exit(main())
