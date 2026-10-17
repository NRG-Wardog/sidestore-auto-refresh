#!/usr/bin/env python3
"""Compile the exact historical P2 external runner; never install or execute it."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import types
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
APPROVED_INPUTS = '1710da2788a5f45b592035bfe99f9c5cefdfcb111f170e3f68914b884f672438'
BUILDER = '4cb67e4d252ba1b1f7a6fa6431560f75264f337c'
DEVELOPER = '/Applications/Xcode_26.4.1.app/Contents/Developer'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-26-5'
DEVICE_TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro'
EXPERIMENT_SECONDS = 12 * 60
VALIDATOR_FILES = {
    'ci/p2/host_probe/run.py': '72dd4074ea9e03c09ec967f2b8e5841af521402af884c37d5fe5aca3eded547e',
    'ci/p2/host_probe/install_contract.py': 'b2fcf4494444eb908f7a58a3b25a55dfc8d0c73becb9d1be6a121fd342b210ac',
}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def number(raw):
        value = float(raw)
        require(math.isfinite(value), 'Nonfinite JSON number')
        return value
    return json.loads(raw, object_pairs_hook=pairs, parse_float=number,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + value)))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def snapshot(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'Missing or linked input root')
    result = {}
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        require(not stat.S_ISLNK(mode), 'Input symlink: ' + str(path))
        if stat.S_ISDIR(mode):
            continue
        require(stat.S_ISREG(mode), 'Special input file: ' + str(path))
        name = path.relative_to(root).as_posix()
        require('\\' not in name and not any(ord(c) < 32 for c in name) and
                all(part not in ('', '.', '..') for part in name.split('/')), 'Unsafe input path')
        result[name] = {'sha256': sha(path), 'bytes': path.stat().st_size}
    return result


def unlinked_within(path, boundary):
    path, boundary = Path(path), Path(boundary)
    require(path.is_relative_to(boundary), 'Path outside trusted root')
    while True:
        require(not path.is_symlink(), 'Linked path within trusted root')
        if path == boundary:
            return
        path = path.parent


def load_validator(root=ROOT):
    root = Path(root)
    for name, digest in VALIDATOR_FILES.items():
        path = root / name
        unlinked_within(path, root)
        require(path.is_file() and not path.is_symlink() and sha(path) == digest,
                'Immutable validator source differs: ' + name)
    # Compile only the exact pinned validation functions and parser source,
    # bypassing cached bytecode and never importing the host orchestration.
    parser_path = root / 'ci/p2/host_probe/install_contract.py'
    parser = types.ModuleType('p2_capability_exact_macho_parser')
    parser.__file__ = str(parser_path)
    exec(compile(parser_path.read_bytes(), str(parser_path), 'exec'), parser.__dict__)
    path = root / 'ci/p2/host_probe/run.py'
    tree = ast.parse(path.read_bytes(), filename=str(path))
    names = {'require', 'safe_relative', 'expand_test_path', 'validate_xctestrun'}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    require(len(functions) == len(names) and {node.name for node in functions} == names, 'Pinned validation functions missing')
    module = types.ModuleType('p2_capability_exact_host_validator')
    module.__dict__.update(Path=Path, PurePosixPath=PurePosixPath, plistlib=plistlib, INSTALL_CONTRACT=parser)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), module.__dict__)
    return module


def choose_destination(devices, runtimes):
    require(isinstance(devices, dict) and isinstance(runtimes, dict), 'Malformed simulator inventory')
    rows = runtimes.get('runtimes')
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows), 'Missing or malformed simulator runtimes')
    selected = [row for row in rows if row.get('identifier') == RUNTIME]
    require(len(selected) == 1, 'Missing or duplicate pinned runtime')
    runtime = selected[0]
    require(runtime.get('isAvailable') is True and runtime.get('version') == '26.5' and
            runtime.get('buildversion') == '23F77', 'Pinned runtime unavailable or differs')
    groups = devices.get('devices')
    require(isinstance(groups, dict) and isinstance(groups.get(RUNTIME), list), 'Missing devices for pinned runtime')
    require(all(isinstance(row, dict) for row in groups[RUNTIME]), 'Malformed simulator device')
    candidates = []
    for row in groups[RUNTIME]:
        if row.get('isAvailable') is True and row.get('deviceTypeIdentifier') == DEVICE_TYPE:
            udid = row.get('udid')
            require(isinstance(udid, str) and re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', udid), 'Invalid destination UUID')
            candidates.append(row)
    require(candidates and len({row['udid'].lower() for row in candidates}) == len(candidates), 'Missing or duplicate available destination')
    selected = sorted(candidates, key=lambda row: (row.get('state') != 'Shutdown', row['udid']))[0]
    return {'udid': selected['udid'], 'runtime': runtime, 'deviceTypeIdentifier': DEVICE_TYPE,
            'state': selected.get('state'), 'created': False, 'bootedByDiagnostic': False}


def settings_contract(raw):
    rows = strict_json(raw)
    require(isinstance(rows, list) and len(rows) == 1, 'Unexpected runner build-settings target count')
    row = rows[0]
    require(isinstance(row, dict), 'Malformed runner build-settings target')
    require(row.get('target') == 'HostSettingsProbeUITests', 'Unexpected runner build-settings target')
    settings = row.get('buildSettings')
    require(isinstance(settings, dict), 'Missing runner build settings')
    for key, value in {'PRODUCT_BUNDLE_IDENTIFIER': 'org.sidestore.audit.hostsettings.uitests',
                       'PRODUCT_TYPE': 'com.apple.product-type.bundle.ui-testing',
                       'PLATFORM_NAME': 'iphonesimulator', 'SDK_VERSION': '26.4',
                       'EFFECTIVE_PLATFORM_NAME': '-iphonesimulator', 'SDK_NAME': 'iphonesimulator26.4',
                       'CONFIGURATION': 'Debug'}.items():
        require(settings.get(key) == value, 'Unexpected runner setting: ' + key)
    for key in ('TEST_TARGET_NAME', 'TEST_HOST', 'BUNDLE_LOADER', 'TEST_TARGET_PATH', 'UITEST_TARGET_APP_PATH'):
        require(not settings.get(key), 'Unexpected target-application binding: ' + key)
    return {'target': row['target'], 'platform': settings['PLATFORM_NAME'],
            'sdkVersion': settings['SDK_VERSION'], 'targetApplication': None}


def override_keys(env):
    exact = {'XCODE_XCCONFIG_FILE', 'SDKROOT', 'TOOLCHAINS', 'ARCHS', 'EXCLUDED_ARCHS',
             'ONLY_ACTIVE_ARCH', 'SUPPORTED_PLATFORMS', 'TEST_TARGET_NAME', 'TEST_HOST',
             'BUNDLE_LOADER', 'SYMROOT', 'OBJROOT', 'CONFIGURATION_BUILD_DIR'}
    prefixes = ('DYLD_', 'SIMCTL_CHILD_', 'OTHER_', 'CODE_SIGN', 'SWIFT_', 'CLANG_')
    return sorted(key for key, value in env.items() if value and (key in exact or key.startswith(prefixes)))


def output_inventory(source):
    source = Path(source)
    require(source.is_dir() and not source.is_symlink(), 'Unsafe archive source root')
    result = {}
    flags = os.O_RDONLY | os.O_NOFOLLOW
    def fingerprint(value):
        return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    def visit(directory_fd, prefix):
        names = sorted(os.listdir(directory_fd))
        for name in names:
            relative = prefix + name
            seen = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            mode = seen.st_mode
            if stat.S_ISLNK(mode):
                raw = os.readlink(os.fsencode(name), dir_fd=directory_fd)
                result[relative] = {'type': 'symlink', 'target': os.fsdecode(raw), 'mode': mode,
                                    'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
            elif stat.S_ISDIR(mode):
                result[relative] = {'type': 'directory', 'mode': mode}
                child = os.open(name, flags | os.O_DIRECTORY, dir_fd=directory_fd)
                try:
                    require(os.fstat(child).st_ino == seen.st_ino, 'Output directory replaced')
                    visit(child, relative + '/')
                finally:
                    os.close(child)
            else:
                require(stat.S_ISREG(mode), 'Special build output: ' + relative)
                fd = os.open(name, flags, dir_fd=directory_fd)
                try:
                    before = os.fstat(fd)
                    require(before.st_ino == seen.st_ino and before.st_mode == mode, 'Output replaced before inventory')
                    digest, length = hashlib.sha256(), 0
                    while True:
                        chunk = os.read(fd, 1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk); length += len(chunk)
                    after = os.fstat(fd)
                    require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_mode) ==
                            (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode), 'Output changed during inventory')
                    require(length == before.st_size, 'Output size changed during inventory')
                finally:
                    os.close(fd)
                result[relative] = {'type': 'regular', 'sha256': digest.hexdigest(), 'bytes': length, 'mode': mode}
            require(fingerprint(seen) == fingerprint(os.stat(name, dir_fd=directory_fd, follow_symlinks=False)), 'Output entry changed during inventory')
        require(names == sorted(os.listdir(directory_fd)), 'Output directory entries changed')
    fd = os.open(source, flags | os.O_DIRECTORY)
    try:
        visit(fd, '')
    finally:
        os.close(fd)
    return result


def open_output(source, relative):
    parts = relative.split('/')
    require(parts and all(part not in ('', '.', '..') for part in parts), 'Unsafe output path')
    flags = os.O_RDONLY | os.O_NOFOLLOW
    fd = os.open(source, flags | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd); fd = child
        return os.open(parts[-1], flags, dir_fd=fd)
    finally:
        os.close(fd)


def archive_tree(source, target):
    """Verify before/after source and ZIP payloads; never dereference links."""
    source, target = Path(source), Path(target)
    inventory = output_inventory(source)
    write(target.with_suffix('.before-inventory.json'), inventory)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for relative, row in inventory.items():
            info = zipfile.ZipInfo(relative + ('/' if row['type'] == 'directory' else ''))
            info.create_system = 3
            info.external_attr = row['mode'] << 16
            if row['type'] == 'symlink':
                archive.writestr(info, os.fsencode(row['target']))
            elif row['type'] == 'directory':
                archive.writestr(info, b'')
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                fd = open_output(source, relative)
                with os.fdopen(fd, 'rb') as stream, archive.open(info, 'w', force_zip64=True) as output:
                    shutil.copyfileobj(stream, output)
    after = output_inventory(source)
    write(target.with_suffix('.after-inventory.json'), after)
    require(inventory == after, 'Output changed while archiving')
    observed = {}
    with zipfile.ZipFile(target) as archive:
        for info in archive.infolist():
            name = info.filename.rstrip('/')
            require(name in inventory and name not in observed, 'Archive has unexpected/duplicate entries')
            row = inventory[name]
            require(info.external_attr >> 16 == row['mode'], 'Archived type/mode differs')
            if row['type'] == 'directory':
                require(info.is_dir() and info.file_size == 0, 'Archived directory differs')
            else:
                digest, length = hashlib.sha256(), 0
                with archive.open(info) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk); length += len(chunk)
                require(digest.hexdigest() == row['sha256'] and length == row['bytes'], 'Archived payload differs')
            observed[name] = row
    require(observed == inventory, 'Archive omitted output entries')
    write(target.with_suffix('.verification.json'), {'status': 'PASSED', 'archiveSHA256': sha(target),
          'entries': len(inventory), 'sourceStableBeforeAfter': True, 'archivedPayloadsTypesModesMatch': True})


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_owned_group(process):
    """Only this invocation's start_new_session group; never shared processes."""
    result = {'processGroup': process.pid, 'signals': [], 'quiescent': False}
    for signum, duration in ((signal.SIGTERM, 3), (signal.SIGKILL, 3)):
        if not group_alive(process.pid):
            result['quiescent'] = True
            break
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            result['quiescent'] = True
            break
        result['signals'].append(signal.Signals(signum).name)
        end = time.monotonic() + duration
        while time.monotonic() < end:
            process.poll()  # reap our own child, including after timeout
            if not group_alive(process.pid):
                result['quiescent'] = True
                break
            time.sleep(0.05)
        if result['quiescent']:
            break
    result['quiescent'] = not group_alive(process.pid)
    return result


class Capability:
    def __init__(self, work, evidence, approved):
        self.work, self.evidence, self.approved = Path(work), Path(evidence), approved
        self.stage, self.number = 'preflight', 0
        self.binding = self.input_snapshot = self.runner_snapshot = self.validator = None
        self.destination, self.runner_base = None, None
        self.result_bundle = self.work / 'runner-build.xcresult'
        self.command_quiescence = []
        self.deadline = time.monotonic() + EXPERIMENT_SECONDS
        self.report = {'schemaVersion': 1, 'status': 'BLOCKED', 'capabilityPass': False,
                       'historicalBuilderCommit': BUILDER, 'historicalRunId': 39064168648,
                       'scope': 'Exact historical external-runner generation only',
                       'hostBuilt': False, 'hostInstalled': False, 'hostLaunched': False,
                       'nativeTestExecuted': False, 'hostIdentityVerifiedThisRun': False,
                       'historicalRunnerReusableForNewHost': False, 'fullAppAcceptance': False,
                       'experimentBudgetSeconds': EXPERIMENT_SECONDS,
                       'finalCompositionAcceptance': False, 'outcomes': {}, 'errors': [], 'retentionErrors': []}

    def allowed(self, argv):
        reads = [
            ['git', '-C', str(ROOT), 'rev-parse', 'HEAD', 'HEAD^{tree}'],
            ['git', '-C', str(ROOT), 'status', '--porcelain=v1', '--untracked-files=all'],
            ['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', BUILDER, 'HEAD'],
            ['xcodebuild', '-version'],
            ['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'],
            ['xcrun', 'simctl', 'list', 'runtimes', '--json'],
            ['xcrun', 'simctl', 'list', 'devices', '--json'],
        ]
        if argv in reads:
            return True
        if self.runner_base:
            tails = [['-showBuildSettings', '-json'],
                     ['build-for-testing', '-showBuildSettings', '-json'],
                     ['-resultBundlePath', str(self.result_bundle), 'build-for-testing']]
            return any(argv == self.runner_base + tail for tail in tails)
        return False

    def command(self, argv, label, timeout=300):
        self.number += 1
        argv = [str(value) for value in argv]
        stem = self.evidence / 'commands' / f'{self.number:03d}'
        stem.parent.mkdir(parents=True, exist_ok=True)
        receipt = {'argv': argv, 'label': label, 'stage': self.stage, 'timeoutSeconds': timeout,
                   'requestedTimeoutSeconds': timeout,
                   'returncode': None, 'stdout': '', 'stderr': '', 'developerDir': os.environ.get('DEVELOPER_DIR'),
                   'ambientOverrideKeys': override_keys(os.environ)}
        started = time.monotonic()
        process = None
        try:
            remaining = self.deadline - time.monotonic()
            receipt['remainingExperimentSeconds'] = max(0, remaining)
            receipt['timeoutSeconds'] = min(timeout, max(0, remaining))
            receipt['deadlineExhausted'] = remaining <= 0
            require(remaining > 0, 'Shared experiment deadline exhausted')
            require(self.allowed(argv), 'Command outside runner-only allowlist')
            require(not receipt['ambientOverrideKeys'], 'Ambient build/launch overrides are forbidden')
            require(os.environ.get('DEVELOPER_DIR') == DEVELOPER, 'Selected developer directory differs')
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
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
                try:
                    receipt['ownedProcessGroup'] = stop_owned_group(process)
                    # Bound the join even if a detached process inherited a pipe.
                    stdout, stderr = process.communicate(timeout=3)
                    receipt.update(stdout=stdout, stderr=stderr)
                    receipt['terminationReturncode'] = process.returncode
                    receipt['ownedProcessGroup']['pipesJoined'] = True
                    require(receipt['ownedProcessGroup']['quiescent'], 'Owned process group still has writers')
                except Exception as error:
                    receipt['quiescenceError'] = type(error).__name__ + ': ' + str(error)
                    receipt.setdefault('ownedProcessGroup', {})['quiescent'] = False
                self.command_quiescence.append({'label': label, **receipt['ownedProcessGroup']})
            receipt['elapsedSeconds'] = time.monotonic() - started
            stem.with_suffix('.stdout').write_text(receipt['stdout'])
            stem.with_suffix('.stderr').write_text(receipt['stderr'])
            write(stem.with_suffix('.json'), {k: v for k, v in receipt.items() if k not in ('stdout', 'stderr')})
        return receipt

    def checked(self, argv, label):
        receipt = self.command(argv, label)
        require(receipt['returncode'] == 0 and not receipt.get('error') and not receipt.get('quiescenceError'), 'Command failed: ' + label)
        return receipt['stdout']

    def verify_inputs(self):
        require(self.approved == APPROVED_INPUTS and sha(HERE / 'binding.json') == APPROVED_INPUTS,
                'Capability input approval digest differs')
        self.binding = strict_json((HERE / 'binding.json').read_text())
        require(self.binding['validatorFiles'] == VALIDATOR_FILES, 'Validator binding differs')
        self.input_snapshot = snapshot(HERE / 'inputs')
        require(self.input_snapshot == self.binding['files'], 'Historical input bytes or file set differ')
        self.validator = load_validator()
        shutil.copytree(HERE / 'inputs/generated-runner', self.work / 'runner')
        self.runner_snapshot = snapshot(self.work / 'runner')
        expected = {key[len('generated-runner/'):]: value for key, value in self.input_snapshot.items()
                    if key.startswith('generated-runner/')}
        require(self.runner_snapshot == expected, 'Copied runner differs')
        write(self.evidence / 'verified-inputs.json', self.binding)

    def guard_inputs(self):
        require(sha(HERE / 'binding.json') == APPROVED_INPUTS, 'Input binding mutated')
        require(snapshot(HERE / 'inputs') == self.input_snapshot, 'Historical inputs mutated')
        require(snapshot(self.work / 'runner') == self.runner_snapshot, 'Generated project/source mutated')
        for name, digest in VALIDATOR_FILES.items():
            path = ROOT / name
            unlinked_within(path, ROOT)
            require(sha(path) == digest, 'Validator source mutated: ' + name)

    def preflight(self):
        self.verify_inputs()
        require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Requires pinned macOS arm64 toolchain')
        require(os.environ.get('DEVELOPER_DIR') == DEVELOPER, 'Developer directory differs')
        require(not override_keys(os.environ), 'Ambient overrides forbidden')
        raw = self.checked(['git', '-C', str(ROOT), 'rev-parse', 'HEAD', 'HEAD^{tree}'], 'diagnostic-identity')
        lines = raw.strip().splitlines()
        require(len(lines) == 2 and all(re.fullmatch('[0-9a-f]{40}', value) for value in lines), 'Malformed diagnostic identity')
        self.report.update(diagnosticCommit=lines[0], diagnosticTree=lines[1])
        require(not self.checked(['git', '-C', str(ROOT), 'status', '--porcelain=v1', '--untracked-files=all'], 'diagnostic-cleanliness').strip(), 'Diagnostic checkout must be clean')
        self.checked(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', BUILDER, 'HEAD'], 'diagnostic-ancestry')
        version = self.checked(['xcodebuild', '-version'], 'toolchain-version').strip()
        require(version == self.binding['environment']['xcodeVersion'], 'Xcode version differs')
        sdk = self.checked(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'], 'sdk-version').strip()
        require(sdk == '26.4', 'Simulator SDK differs')
        runtimes = strict_json(self.checked(['xcrun', 'simctl', 'list', 'runtimes', '--json'], 'available-runtimes'))
        devices = strict_json(self.checked(['xcrun', 'simctl', 'list', 'devices', '--json'], 'available-devices'))
        self.destination = choose_destination(devices, runtimes)
        write(self.evidence / 'environment.json', {'xcodeVersion': version, 'sdkVersion': sdk,
              'architecture': platform.machine(), 'developerDir': DEVELOPER, 'destination': self.destination})
        return self.destination

    def validate_generated(self):
        root = self.work / 'runner-derived/Build/Products'
        unlinked_within(root, self.work)
        require(root.is_dir(), 'Missing generated products root')
        generated = list(root.glob('*.xctestrun'))
        require(len(generated) == 1, 'Expected exactly one generated external-runner xctestrun')
        path = generated[0]
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()), 'Unsafe generated xctestrun')
        before = sha(path)
        shutil.copyfile(path, self.evidence / 'generated.xctestrun')
        # This unchanged validator argument checks path separation only. There
        # is deliberately no app in this capability job and no host identity claim.
        reference = self.work / 'absent-historical-product-reference/LiveContainer.app'
        require(not reference.exists(), 'Capability job unexpectedly contains a host product')
        try:
            wiring = self.validator.validate_xctestrun(plistlib.loads(path.read_bytes()), root, reference)
        finally:
            after = sha(path)
            write(self.evidence / 'xctestrun-hashes.json', {'before': before, 'after': after})
            require(before == after, 'Generated xctestrun mutated')
        wiring.update(xctestrunSHA256=before, hostPathSeparationOnly=True, hostIdentityVerified=False)
        write(self.evidence / 'runner-wiring.json', wiring)
        self.inspect_products(wiring)
        self.report['outcomes']['xctestrun-validation'] = {'status': 'PASSED', **wiring}

    def inspect_products(self, wiring):
        parser = self.validator.INSTALL_CONTRACT
        runner = Path(wiring['runnerPath'])
        bundle = Path(wiring['testBundlePath'])
        before = parser.bundle_inventory(runner)
        write(self.evidence / 'runner-product-inventory-before.json', before)
        observations = []
        for label, package, identifier, executable, filetype, package_type in [
            ('runner', runner, 'org.sidestore.audit.hostsettings.uitests.xctrunner', 'HostSettingsProbeUITests-Runner', 2, 'APPL'),
            ('test-bundle', bundle, 'org.sidestore.audit.hostsettings.uitests', 'HostSettingsProbeUITests', 8, 'BNDL')]:
            prefix = '' if package == runner else package.relative_to(runner).as_posix() + '/'
            info_path = package / 'Info.plist'
            require(before.get(prefix + 'Info.plist', {}).get('type') == 'regular', 'Missing real product Info.plist')
            fd = open_output(runner, prefix + 'Info.plist')
            with os.fdopen(fd, 'rb') as stream:
                info_raw = stream.read()
            require(hashlib.sha256(info_raw).hexdigest() == before[prefix + 'Info.plist']['sha256'], 'Product Info.plist changed')
            info = plistlib.loads(info_raw)
            require(info.get('CFBundleIdentifier') == identifier and info.get('CFBundleExecutable') == executable and
                    info.get('CFBundlePackageType') == package_type, 'Actual generated product identity differs')
            row = before.get(prefix + executable, {})
            require(row.get('type') == 'regular' and row.get('executable') is True, 'Missing real generated executable')
            path = package / executable
            fd = open_output(runner, prefix + executable)
            with os.fdopen(fd, 'rb') as stream:
                raw = stream.read()
            require(hashlib.sha256(raw).hexdigest() == row['sha256'], 'Generated executable changed')
            macho = parser.parse_macho(raw)
            require(macho['slices'] and all(item['cpuType'] == 0x0100000C and item['bits'] == 64 and
                    item['fileType'] == filetype and item['buildVersion']['platform'] == 7 for item in macho['slices']),
                    'Generated executable architecture/type/platform differs')
            observations.append({'label': label, 'bundleIdentifier': identifier, 'executable': executable,
                'infoPlistSHA256': hashlib.sha256(info_raw).hexdigest(), 'executableSHA256': row['sha256'],
                'executableMode': row['mode'], 'machO': macho})
        after = parser.bundle_inventory(runner)
        write(self.evidence / 'runner-product-inventory-after.json', after)
        require(before == after, 'Generated product changed during metadata inspection')
        write(self.evidence / 'runner-product-metadata.json', {'status': 'PASSED', 'products': observations,
              'readOnly': True, 'executed': False, 'inventoryStable': True})

    def run_experiment(self, destination):
        self.runner_base = ['xcodebuild', '-project', str(self.work / 'runner/HostSettingsProbe.xcodeproj'),
                            '-scheme', 'HostSettingsProbe', '-configuration', 'Debug', '-sdk', 'iphonesimulator',
                            '-destination', 'platform=iOS Simulator,id=' + destination['udid'],
                            '-derivedDataPath', str(self.work / 'runner-derived')]
        attempts = [('actionless-control', ['-showBuildSettings', '-json'], 300),
                    ('testing-settings', ['build-for-testing', '-showBuildSettings', '-json'], 300),
                    ('runner-build', ['-resultBundlePath', str(self.result_bundle), 'build-for-testing'], 600)]
        for label, tail, timeout in attempts:
            self.stage = label
            self.guard_inputs()
            receipt = self.command(self.runner_base + tail, label, timeout=timeout)
            # Persist the result before checking immutable inputs; mutations must
            # block subsequent commands without losing the offending outcome.
            outcome = {'status': 'PASSED' if receipt['returncode'] == 0 and not receipt.get('error') else 'FAILED',
                       'returncode': receipt['returncode'], 'error': receipt.get('error')}
            self.report['outcomes'][label] = outcome
            require(not receipt.get('deadlineExhausted'), 'Shared experiment deadline exhausted before ' + label)
            require(not receipt.get('quiescenceError'), 'Command writers did not quiesce: ' + label)
            self.guard_inputs()
            if label == 'testing-settings' and outcome['status'] == 'PASSED':
                (self.evidence / 'runner-build-settings.json').write_text(receipt['stdout'])
                try:
                    outcome['validatedSettings'] = settings_contract(receipt['stdout'])
                except Exception as error:
                    outcome.update(status='FAILED', error=type(error).__name__ + ': ' + str(error))
        # Query failures do not hide independent compilation/wiring evidence.
        build = self.report['outcomes']['runner-build']
        require(build['status'] == 'PASSED', 'Runner-only build failed')
        self.stage = 'generated-xctestrun-validation'
        self.validate_generated()
        self.guard_inputs()
        require(self.report['outcomes']['testing-settings']['status'] == 'PASSED', 'Testing-action settings query failed')
        self.report.update(status='RUNNER_GENERATION_CAPABLE', capabilityPass=True)

    def execute(self):
        self.run_experiment(self.preflight())

    def retain_try(self, label, fn):
        try:
            fn()
        except Exception as error:
            self.report['retentionErrors'].append(label + ': ' + type(error).__name__ + ': ' + str(error))

    def retain(self):
        # Only filesystem reads/copies here, including for partial builds. Never
        # invoke the old host probe's finalizer or any simulator/test command.
        write(self.evidence / 'owned-command-groups.json', self.command_quiescence)
        if any(not row.get('quiescent') or not row.get('pipesJoined') for row in self.command_quiescence):
            self.report['retentionErrors'].append('Unresolved owned command writers; outputs cannot be declared stable')
        for label, source, target in [
            ('historical inputs', HERE / 'inputs', self.evidence / 'inputs.zip'),
            ('generated sources', self.work / 'runner', self.evidence / 'generated-runner.zip'),
            ('partial derived data', self.work / 'runner-derived', self.evidence / 'runner-derived.zip'),
            ('build result bundle', self.result_bundle, self.evidence / 'runner-build.xcresult.zip')]:
            if source.exists():
                self.retain_try(label, lambda source=source, target=target: archive_tree(source, target))
        for name in ('run.py', 'binding.json', 'test_capability.py', 'README.md'):
            path = HERE / name
            if path.is_file():
                self.retain_try('diagnostic ' + name, lambda path=path, name=name: shutil.copyfile(path, self.evidence / ('diagnostic-' + name)))
        workflow = ROOT / '.github/workflows/p2-runner-capability.yml'
        if workflow.is_file():
            self.retain_try('workflow', lambda: shutil.copyfile(workflow, self.evidence / 'workflow.yml'))
        for name in VALIDATOR_FILES:
            def retain_validator(name=name):
                source = ROOT / name
                unlinked_within(source, ROOT)
                target = self.evidence / 'validator-source' / source.name
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(source, target)
            self.retain_try('immutable validator ' + name, retain_validator)
        if self.runner_snapshot is not None:
            self.retain_try('final input immutability', self.guard_inputs)
        if self.report['retentionErrors'] or self.report['errors']:
            self.report.update(status='BLOCKED', capabilityPass=False)
        self.report['stage'] = self.stage
        write(self.evidence / 'capability.json', self.report)
        # Preserve links as links. Never follow a build output link to hash or
        # copy outside the evidence; record it so review can identify it.
        inventory = {}
        for path in sorted(self.evidence.rglob('*')):
            if path.is_symlink():
                inventory[path.relative_to(self.evidence).as_posix()] = {'type': 'symlink', 'target': os.readlink(path)}
            elif path.is_file():
                inventory[path.relative_to(self.evidence).as_posix()] = {'type': 'regular', 'sha256': sha(path), 'bytes': path.stat().st_size}
        write(self.evidence / 'evidence-sha256.json', inventory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--approved-inputs-sha256', required=True)
    args = parser.parse_args()
    paths = (args.work.absolute(), args.evidence.absolute())
    for path in paths:
        require(not path.exists() and not path.is_symlink(), 'Use fresh output paths; never erase old state')
        require(not any(parent.is_symlink() for parent in path.parents), 'Output parent symlink forbidden')
        require(not path.resolve().is_relative_to(ROOT) and not ROOT.is_relative_to(path.resolve()), 'Output must be outside repository')
    work, evidence = [path.resolve() for path in paths]
    require(not work.is_relative_to(evidence) and not evidence.is_relative_to(work), 'Output paths must be disjoint')
    evidence.mkdir(parents=True)
    probe = Capability(work, evidence, args.approved_inputs_sha256)
    try:
        work.mkdir(parents=True)
        probe.execute()
    except Exception as error:
        probe.report['errors'].append(type(error).__name__ + ': ' + str(error))
    finally:
        probe.retain()
    print(json.dumps(probe.report, indent=2))
    return 0 if probe.report['capabilityPass'] else 1


if __name__ == '__main__':
    sys.exit(main())
