#!/usr/bin/env python3
"""Fail-closed feasibility probe for the unchanged, isolated P2 simulator app.

This module owns no product source patches and never turns feasibility into
full-app, final-composition, physical-device, or language-geometry acceptance.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import importlib.util
import math
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INSTALL_SPEC = importlib.util.spec_from_file_location('p2_install_contract', HERE / 'install_contract.py')
INSTALL_CONTRACT = importlib.util.module_from_spec(INSTALL_SPEC)
INSTALL_SPEC.loader.exec_module(INSTALL_CONTRACT)
SOURCE = 'eb10cedb772bc0496d67edd7a269679e24826b9f'
TREE = 'd6960a99a2f022a333c364fa4d28e264568a28ca'
BUILDER_PARENT = '4fe6738d3223fe632e179642d52d429866b052d1'
SOURCE_URL = 'https://github.com/NRG-Wardog/LiveContainer.git'
PROJECT = 'LiveContainer.xcodeproj'
SCHEME = 'LiveContainer'
CONFIGURATION = 'Release'
BUNDLE_ID = 'com.kdt.livecontainer'
METHOD = 'testUnchangedHostSettingsLanguageRoundTrip'
TEST_ID = 'HostSettingsProbeTests/' + METHOD + '()'
DEPENDENCIES = {
    'OpenSSL': {'commit': '623c84da314e85363236507ca38a4bde65df21c3',
                'tree': '21bc09387e7f91a46eb8bd26587ce1d052277801',
                'declared_url': 'https://github.com/krzyzanowskim/OpenSSL',
                'fetch_url': 'https://github.com/krzyzanowskim/OpenSSL'},
    'litehook': {'commit': '8025e0c8ebdf5cdd1d2a4f45025813234bf9dc55',
                 'tree': '62bc06a17a1c12d805aec1ada25910bfa9f89781',
                 'declared_url': 'https://github.com/LiveContainer/litehook',
                 'fetch_url': 'https://github.com/LiveContainerMirror/litehook.git'},
}
LIMITS = [
    'Feasibility only: isolated P2 source, never final composed P0/P1/P2 acceptance.',
    'No product/project/source edits, injected fixtures, launch language overrides, preference seeding, auth stubs, or root/transform changes.',
    'The genuine Release scheme is built for a new iOS Simulator with no command-line product build-setting overrides.',
    'Separate no-target-application XCUITest runner is unproven on the selected Xcode. Unsupported generation or wiring is a blocker, never repaired by editing the product project or xctestrun.',
    'Only the exact known app-owned notification explanation can be cancelled with its empty-closure Later action; OS permission, authentication, entitlement and unknown prompts block.',
    'Only the exact known Later cancellation, Settings-tab and genuine settings.language picker taps are permitted; Hebrew then English in one launch attempt.',
    'Screenshots, AX and selected-value evidence require independent review; passing feasibility does not prove glyph geometry, transforms, root/model lifetime, persistence across relaunch, or whole-app acceptance.',
    'One fresh simulator; no physical device, signed IPA, guest execution, account or backend acceptance.',
]


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def strict_json(raw):
    def finite_float(value):
        result = float(value)
        require(math.isfinite(result), 'Nonfinite JSON number')
        return result
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_float=finite_float,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + value)))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')


def safe_relative(name):
    require(isinstance(name, str) and name and '\\' not in name, 'Invalid relative path')
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(p not in ('', '.', '..') for p in name.split('/')), 'Unsafe relative path: ' + name)
    return path


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def tree_entries(raw):
    rows = {}
    for record in raw.split('\0'):
        if not record:
            continue
        header, name = record.split('\t', 1)
        mode, kind, object_id = header.split()
        safe_relative(name)
        require(name not in rows and re.fullmatch('[0-9a-f]{40}', object_id), 'Invalid or duplicate tree entry')
        require((mode, kind) in {('100644', 'blob'), ('100755', 'blob'), ('120000', 'blob'), ('160000', 'commit')}, 'Unsupported source entry: ' + name)
        rows[name] = {'mode': mode, 'type': kind, 'gitObject': object_id}
    require(rows, 'Empty source tree')
    return rows


def source_link_target(root, name, row):
    path = root / name
    require(path.is_symlink() and row['mode'] == '120000', 'Source link mode differs: ' + name)
    raw = os.readlink(os.fsencode(path))
    object_id = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
    require(object_id == row['gitObject'], 'Source link bytes differ: ' + name)
    target = os.fsdecode(raw)
    require(target and not PurePosixPath(target).is_absolute(), 'Absolute or empty source link: ' + name)
    return target, raw


def source_link_destination(root, name, entries):
    # Resolve through the authenticated tree, never following an untracked or
    # escaping filesystem link. Framework directory links are legitimate inputs.
    directories = {''}
    for entry in entries:
        directories.update(str(p) for p in PurePosixPath(entry).parents if str(p) != '.')
    pending = name.split('/')
    resolved = []
    traversed = set()
    while pending:
        part = pending.pop(0)
        if part in ('', '.'):
            continue
        if part == '..':
            require(resolved, 'Source link escapes checkout: ' + name)
            resolved.pop()
            continue
        candidate = '/'.join([*resolved, part])
        row = entries.get(candidate)
        if row and row['mode'] == '120000':
            require(candidate not in traversed and len(traversed) < 128, 'Cyclic or excessive source link: ' + name)
            traversed.add(candidate)
            target, _ = source_link_target(root, candidate, row)
            pending = target.split('/') + pending
            continue
        require(candidate in directories or (row and row['type'] == 'blob'), 'Untracked or missing source link target: ' + name)
        path = root / candidate
        require(not path.is_symlink(), 'Untracked intermediate source link: ' + candidate)
        if candidate in directories:
            require(path.is_dir(), 'Source link directory missing: ' + candidate)
        else:
            require(path.is_file() and not pending, 'Source link file missing or used as directory: ' + candidate)
        resolved.append(part)
    require(resolved, 'Source link resolves to checkout root: ' + name)
    return '/'.join(resolved)


def checkout_proof(root, commit, tree, origin, *, dependencies=False):
    root = Path(root).resolve()
    require(git(root, 'rev-parse', 'HEAD') == commit, 'Checkout commit differs')
    require(git(root, 'rev-parse', 'HEAD^{tree}') == tree, 'Checkout tree differs')
    actual_origin = git(root, 'remote', 'get-url', 'origin')
    equivalent_origin = origin[:-4] if origin.endswith('.git') else origin + '.git'
    require(actual_origin in (origin, equivalent_origin), 'Checkout origin differs')
    require(not git(root, 'status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none'), 'Checkout is dirty')
    entries = tree_entries(git(root, 'ls-tree', '-rz', '--full-tree', 'HEAD'))
    children = {name: row['gitObject'] for name, row in entries.items() if row['type'] == 'commit'}
    if dependencies:
        require(children == {name: row['commit'] for name, row in DEPENDENCIES.items()}, 'Source gitlink set/pins differ')
    else:
        require(not children, 'Unreviewed nested gitlink')
    for name, row in entries.items():
        if row['type'] == 'commit':
            continue
        path = root / name
        if row['mode'] == '120000':
            target, raw = source_link_target(root, name, row)
            row['sha256'] = hashlib.sha256(raw).hexdigest()
            row['linkTarget'] = target
            row['resolvedTarget'] = source_link_destination(root, name, entries)
            continue
        require(all(not parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents), 'Source parent directory linked: ' + name)
        require(path.is_file() and not path.is_symlink(), 'Source file missing or linked: ' + name)
        require(git(root, 'hash-object', '--', str(path)) == row['gitObject'], 'Source bytes differ: ' + name)
        require(bool(path.stat().st_mode & 0o111) == (row['mode'] == '100755'), 'Source executable mode differs: ' + name)
        row['sha256'] = sha(path)
    return {'root': str(root), 'commit': commit, 'tree': tree, 'origin': actual_origin, 'files': entries}


def verify_declared_dependencies(root):
    modules = configparser.ConfigParser(interpolation=None, strict=True)
    modules.read(Path(root) / '.gitmodules')
    declared = {modules[s]['path']: modules[s]['url'] for s in modules.sections()}
    require(declared == {'fishhook': 'https://github.com/LiveContainer/fishhook',
                        **{name: row['declared_url'] for name, row in DEPENDENCIES.items()}}, 'Declared submodule URLs differ')
    # fishhook remains a stale declaration, not a gitlink. Never initialize it.
    return declared


def verify_inputs(host, approved):
    require(re.fullmatch('[0-9a-f]{64}', approved or ''), 'Explicit reviewed input SHA-256 required')
    require(sha(HERE / 'inputs.json') == approved, 'Unreviewed host-probe input bytes')
    data = strict_json((HERE / 'inputs.json').read_text())
    require(set(data) == {'schemaVersion', 'sourceCommit', 'sourceTree', 'sourceRepository', 'builderParent',
                          'project', 'scheme', 'configuration', 'dependencies', 'adapterFiles', 'contractFiles', 'expectedTest'}, 'Unexpected input schema')
    require((data['schemaVersion'], data['sourceCommit'], data['sourceTree'], data['sourceRepository'],
             data['builderParent'], data['project'], data['scheme'], data['configuration'], data['expectedTest']) ==
            (1, SOURCE, TREE, SOURCE_URL, BUILDER_PARENT, PROJECT, SCHEME, CONFIGURATION, TEST_ID), 'Probe identity drift')
    require(data['dependencies'] == DEPENDENCIES, 'Unreviewed dependency pins')
    expected = {'run.py', 'test_probe.py', 'HostSettingsProbeTests.swift.in',
                'project/project.pbxproj', 'project/HostSettingsProbe.xcscheme', 'README.md',
                'install_contract.py', 'test_install_contract.py'}
    require(set(data['adapterFiles']) == expected, 'Unexpected probe adapter inventory')
    for name, digest in data['adapterFiles'].items():
        safe_relative(name)
        require(sha(HERE / name) == digest, 'Changed host-probe adapter: ' + name)
    contracts = {'scripts/p2_localization_contract.py', 'scripts/p2_permission_contract.py', 'ci/p2/preflight.py',
                 'tests/fixtures/p2_localization_contracts/compiled-translations.json'}
    require(set(data['contractFiles']) == contracts, 'Unexpected resource contract inventory')
    for name, digest in data['contractFiles'].items():
        require(sha(ROOT / name) == digest, 'Changed resource contract: ' + name)
    proof = checkout_proof(host, SOURCE, TREE, SOURCE_URL, dependencies=True)
    verify_declared_dependencies(host)
    return data, proof


def product_build_command(host, derived, udid):
    # No CODE_SIGN*, DEVELOPMENT_TEAM, architecture, resource or linker overrides.
    return ['xcodebuild', '-project', str(Path(host) / PROJECT), '-scheme', SCHEME,
            '-configuration', CONFIGURATION, '-sdk', 'iphonesimulator',
            '-destination', 'platform=iOS Simulator,id=' + udid, '-derivedDataPath', str(derived),
            '-disableAutomaticPackageResolution', '-onlyUsePackageVersionsFromResolvedFile']


def host_build_settings(raw, host, derived):
    rows = strict_json(raw)
    require(isinstance(rows, list), 'Build settings must be an array')
    matches = [r for r in rows if r.get('target') == SCHEME]
    require(len(matches) == 1, 'Exactly one genuine LiveContainer target required')
    value = matches[0]['buildSettings']
    for key, expected in {'TARGET_NAME': SCHEME, 'PRODUCT_NAME': 'LiveContainer', 'CONFIGURATION': CONFIGURATION,
                          'PRODUCT_BUNDLE_IDENTIFIER': BUNDLE_ID, 'WRAPPER_EXTENSION': 'app',
                          'MACH_O_TYPE': 'mh_execute', 'EFFECTIVE_PLATFORM_NAME': '-iphonesimulator',
                          'FULL_PRODUCT_NAME': 'LiveContainer.app'}.items():
        require(value.get(key) == expected, 'Unexpected product setting: ' + key)
    require(value.get('SDK_NAME', '').startswith('iphonesimulator'), 'Wrong SDK')
    require(Path(value['PROJECT_FILE_PATH']).resolve() == (Path(host) / PROJECT).resolve(), 'Wrong product project')
    require(Path(value['SRCROOT']).resolve() == Path(host).resolve(), 'Wrong product source root')
    app = Path(value['TARGET_BUILD_DIR']) / value['FULL_PRODUCT_NAME']
    require(app.resolve().is_relative_to(Path(derived).resolve()), 'Product escapes dedicated derived data')
    require(value.get('EXECUTABLE_NAME') == 'LiveContainer', 'Unexpected executable name')
    return value, app


def bundle_inventory(root, *, strict=True):
    root = Path(root).resolve()
    require(root.is_dir(), 'Missing bundle')
    rows = {}
    for path in sorted(root.rglob('*')):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            if strict:
                require(path.resolve().is_relative_to(root), 'Bundle link escapes root: ' + relative)
            raw = os.readlink(os.fsencode(path))
            rows[relative] = {'type': 'symlink', 'mode': stat.S_IMODE(path.lstat().st_mode),
                              'link': os.fsdecode(raw), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        elif path.is_file():
            rows[relative] = {'type': 'regular', 'mode': stat.S_IMODE(path.lstat().st_mode),
                              'sha256': sha(path), 'bytes': path.stat().st_size,
                              'executable': bool(path.stat().st_mode & 0o111)}
        else:
            if not path.is_dir():
                require(not strict, 'Unsupported product filesystem entry')
                rows[relative] = {'unsupportedMode': path.lstat().st_mode}
    if strict:
        require('Info.plist' in rows and 'LiveContainer' in rows, 'Incomplete actual host bundle')
    return rows


def archive_bundle(root, destination):
    # Preserve links as link bytes, including invalid links being diagnosed.
    # Never dereference them or archive anything outside this installed app.
    root, destination = Path(root), Path(destination)
    require(root.is_dir() and not root.is_symlink(), 'Missing or linked installed app root')
    captured = {}
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob('*')):
            relative = str(path.relative_to(root))
            safe_relative(relative)
            info = zipfile.ZipInfo(root.name + '/' + relative)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = path.lstat().st_mode << 16
            if path.is_symlink():
                raw = os.readlink(os.fsencode(path))
                archive.writestr(info, raw)
                captured[relative] = {'type': 'symlink', 'mode': stat.S_IMODE(info.external_attr >> 16),
                                      'link': os.fsdecode(raw), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            elif path.is_file():
                digest = hashlib.sha256(); size = 0
                with path.open('rb') as source, archive.open(info, 'w') as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        target.write(chunk); digest.update(chunk); size += len(chunk)
                captured[relative] = {'type': 'regular', 'mode': stat.S_IMODE(info.external_attr >> 16),
                                      'sha256': digest.hexdigest(), 'bytes': size,
                                      'executable': bool((info.external_attr >> 16) & 0o111)}
            else:
                require(path.is_dir(), 'Unsupported installed entry during archive: ' + relative)
                info.filename += '/'
                archive.writestr(info, b'')
    with zipfile.ZipFile(destination) as archive:
        require(archive.testzip() is None, 'Installed archive CRC failure')
    return captured


def inventory_difference(expected, actual):
    added = {key: actual[key] for key in sorted(actual.keys() - expected.keys())}
    removed = {key: expected[key] for key in sorted(expected.keys() - actual.keys())}
    changed = {}
    for key in sorted(expected.keys() & actual.keys()):
        if expected[key] != actual[key]:
            fields = {}
            for field in sorted(expected[key].keys() | actual[key].keys()):
                before, after = expected[key].get(field), actual[key].get(field)
                if before != after or (field in expected[key]) != (field in actual[key]):
                    fields[field] = {'expectedPresent': field in expected[key], 'actualPresent': field in actual[key],
                                     'expected': before, 'actual': after}
            changed[key] = fields
    return {'equal': expected == actual, 'added': added, 'removed': removed, 'changed': changed}


def verify_product(app, settings):
    info = plistlib.loads((Path(app) / 'Info.plist').read_bytes())
    require(info.get('CFBundleIdentifier') == settings['PRODUCT_BUNDLE_IDENTIFIER'] == BUNDLE_ID, 'Built bundle identifier differs')
    require(info.get('CFBundleExecutable') == settings['EXECUTABLE_NAME'] == 'LiveContainer', 'Built executable identity differs')
    require(info.get('CFBundlePackageType') == 'APPL', 'Built package is not an application')
    require(info.get('CFBundleSupportedPlatforms') == ['iPhoneSimulator'], 'Built package is not simulator-specific')
    return {'bundleIdentifier': BUNDLE_ID, 'executable': 'LiveContainer', 'infoPlistSHA256': sha(Path(app) / 'Info.plist'),
            'executableSHA256': sha(Path(app) / 'LiveContainer'), 'files': bundle_inventory(app)}


def simulator_platform(raw):
    platforms = re.findall(r'^\s*platform\s+(\S+)\s*$', raw, re.M)
    require(platforms and all(x in ('IOSSIMULATOR', '7') for x in platforms), 'Executable is not exclusively iOS Simulator Mach-O')
    return platforms


def choose_simulator(runtimes, types):
    available = [r for r in runtimes.get('runtimes', []) if r.get('isAvailable') is True
                 and r.get('identifier', '').startswith('com.apple.CoreSimulator.SimRuntime.iOS-')]
    require(available, 'No available iOS Simulator runtime')
    def version(r):
        require(re.fullmatch(r'\d+(\.\d+)*', r.get('version', '')), 'Malformed runtime version')
        return tuple(int(v) for v in r['version'].split('.'))
    runtime = max(available, key=version)
    require(version(runtime) >= (17,), 'iOS 17 or newer required for probe runner')
    devices = {d['identifier']: d for d in types.get('devicetypes', [])}
    for name in ('iPhone-17-Pro', 'iPhone-17', 'iPhone-16-Pro', 'iPhone-16', 'iPhone-15-Pro'):
        identifier = 'com.apple.CoreSimulator.SimDeviceType.' + name
        if identifier in devices:
            return runtime, devices[identifier]
    raise ValueError('No reviewed iPhone device type; no arbitrary device fallback')


def swift_literal(value):
    return json.dumps(value, ensure_ascii=False).replace('\\/', '/')


def prepare_runner(destination, identity, builder_commit, catalog):
    destination = Path(destination)
    sources = destination / 'Sources'
    project = destination / 'HostSettingsProbe.xcodeproj'
    sources.mkdir(parents=True)
    (project / 'xcshareddata/xcschemes').mkdir(parents=True)
    replacements = {'__HOST_BUNDLE_ID__': swift_literal(identity['bundleIdentifier']),
                    '__HOST_EXECUTABLE_SHA256__': swift_literal(identity['executableSHA256']),
                    '__SOURCE_COMMIT__': swift_literal(SOURCE), '__SOURCE_TREE__': swift_literal(TREE),
                    '__BUILDER_COMMIT__': swift_literal(builder_commit)}
    for code, name in [('en', 'EN'), ('he', 'HE')]:
        value = catalog['strings']['Settings']['localizations'][code]['stringUnit']['value']
        require(isinstance(value, str) and value, 'Missing Settings tab translation')
        replacements['__SETTINGS_' + name + '__'] = swift_literal(value)
    notice = {'TITLE': 'Stay Informed About Refreshes', 'LATER': 'Later', 'ALLOW': 'Allow Notifications',
              'BODY': 'LiveContainer can notify you when a refresh starts, completes, or needs attention. Nothing runs differently if you skip this.'}
    for name, key in notice.items():
        value = catalog['strings'][key]['localizations']['en']['stringUnit']['value']
        require(value == key, 'Known notification explanation differs')
        replacements['__NOTICE_' + name + '__'] = swift_literal(value)
    text = (HERE / 'HostSettingsProbeTests.swift.in').read_text()
    for token, value in replacements.items():
        require(token in text, 'Missing runner replacement token')
        text = text.replace(token, value)
    require(not re.search('__[_A-Z]+__', text), 'Unresolved runner token')
    (sources / 'HostSettingsProbeTests.swift').write_text(text)
    shutil.copyfile(HERE / 'project/project.pbxproj', project / 'project.pbxproj')
    shutil.copyfile(HERE / 'project/HostSettingsProbe.xcscheme', project / 'xcshareddata/xcschemes/HostSettingsProbe.xcscheme')
    return project


def expand_test_path(value, testroot):
    require(isinstance(value, str) and value.startswith('__TESTROOT__/'), 'Unsupported generated xctestrun path')
    tail = value[len('__TESTROOT__/'):]
    safe_relative(tail)
    path = (Path(testroot) / tail).resolve()
    require(path.is_relative_to(Path(testroot).resolve()), 'Runner path escapes TESTROOT')
    return path


def validate_xctestrun(data, testroot, app):
    # Both generated formats are read-only. v1 was observed in the retained
    # Xcode 26.4 fixture artifacts; that does not prove external-runner support.
    version = data.get('__xctestrun_metadata__', {}).get('FormatVersion')
    require(type(version) is int and version in (1, 2), 'Unsupported xctestrun schema')
    if version == 1:
        require(set(data) == {'__xctestrun_metadata__', 'HostSettingsProbeUITests'}, 'Unexpected generated test targets')
        target = data['HostSettingsProbeUITests']
    else:
        configurations = data.get('TestConfigurations')
        require(isinstance(configurations, list) and len(configurations) == 1, 'Unexpected xctestrun configuration count')
        config = configurations[0]
        require(config.get('IsEnabled', True) is True, 'Disabled generated test configuration')
        targets = config.get('TestTargets')
        require(isinstance(targets, list) and len(targets) == 1, 'Unexpected generated test targets')
        target = targets[0]
    require(target.get('BlueprintName') == 'HostSettingsProbeUITests' and target.get('IsUITestBundle') is True,
            'Unexpected external runner target')
    require(target.get('TestHostBundleIdentifier') == 'org.sidestore.audit.hostsettings.uitests.xctrunner', 'Wrong test runner bundle')
    runner = expand_test_path(target.get('TestHostPath'), testroot)
    raw_bundle = target.get('TestBundlePath')
    if isinstance(raw_bundle, str) and raw_bundle.startswith('__TESTHOST__/'):
        safe_relative(raw_bundle[len('__TESTHOST__/'):])
        bundle = (runner / raw_bundle[len('__TESTHOST__/'):]).resolve()
    else:
        bundle = expand_test_path(raw_bundle, testroot)
    require(runner.name == 'HostSettingsProbeUITests-Runner.app' and bundle.name == 'HostSettingsProbeUITests.xctest', 'Wrong generated runner package names')
    require(bundle.is_relative_to(runner) and '/PlugIns/' in str(bundle), 'Tests not embedded in external runner')
    require(runner.is_dir() and bundle.is_dir(), 'Generated runner products missing')
    info = plistlib.loads((runner / 'Info.plist').read_bytes())
    require(info.get('CFBundleIdentifier') == target['TestHostBundleIdentifier'], 'Actual runner bundle identifier differs')
    require(Path(app).resolve() != runner, 'Runner cannot substitute for the product')
    # This project has Target Application=None. Any generated target application
    # or target-app launch settings are unsupported; never invent or patch them.
    for key in ('UITargetAppPath', 'UITargetAppBundleIdentifier', 'CommandLineArguments',
                'UITargetAppCommandLineArguments', 'UITargetAppEnvironmentVariables',
                'TestLanguage', 'TestRegion', 'SkipTestIdentifiers', 'OnlyTestIdentifiers'):
        require(not target.get(key), 'Unexpected generated target/launch override: ' + key)
    for name in target.get('DependentProductPaths', []):
        path = expand_test_path(name, testroot)
        require(path == runner or path.is_relative_to(runner), 'External runner depends on an unexpected product')
    return {'formatVersion': version, 'runnerPath': str(runner), 'testBundlePath': str(bundle),
            'targetApplicationMode': 'external-XCUIApplication-bundleIdentifier', 'unmodifiedGeneratedFile': True}


def verify_summary(summary, tests):
    require(summary.get('result') == 'Passed', 'Native test result did not pass')
    for key, count in [('totalTestCount', 1), ('passedTests', 1), ('failedTests', 0), ('skippedTests', 0)]:
        require(type(summary.get(key)) is int and summary[key] == count, 'Wrong exact native test count: ' + key)
    identifiers = []
    def visit(node):
        if isinstance(node, dict):
            if node.get('nodeType') == 'Test Case':
                require(node.get('result') == 'Passed', 'Native test case did not pass')
                identifier = node.get('nodeIdentifier')
                require(identifier in (TEST_ID, 'HostSettingsProbeUITests/' + TEST_ID) and node.get('name') == METHOD + '()', 'Native test name/identity differs')
                identifiers.append(TEST_ID)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(tests)
    require(identifiers == [TEST_ID], 'Native test identity/count differs')


def attachment_map(directory):
    directory = Path(directory)
    manifest = strict_json((directory / 'manifest.json').read_text())
    require(isinstance(manifest, list), 'Unexpected attachment export schema')
    rows = {}
    for group in manifest:
        require(isinstance(group.get('attachments'), list), 'Malformed attachment group')
        for entry in group['attachments']:
            name = entry.get('suggestedHumanReadableName', '')
            require(isinstance(name, str), 'Invalid attachment display name')
            name = re.sub(r'_0_[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?=\.[a-z]+$)', '', name)
            if not name.startswith('host-probe-'):
                continue
            require(group.get('testIdentifier') in (TEST_ID, 'HostSettingsProbeUITests/' + TEST_ID), 'Probe attachment belongs to another test')
            require(name not in rows, 'Duplicate probe attachment name')
            relative = entry['exportedFileName']
            safe_relative(relative)
            path = directory / relative
            require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(directory.resolve()), 'Attachment missing or escapes export')
            require(path.stat().st_size <= 16 * 1024 * 1024, 'Oversized probe attachment')
            rows[name] = path
    require(len(rows) <= 16, 'Too many probe attachments')
    require(len({str(v) for v in rows.values()}) == len(rows), 'Aliased probe attachments')
    return rows


def verify_observations(directory, identity, builder_commit):
    rows = attachment_map(directory)
    report = strict_json(rows['host-probe-observations.json'].read_text())
    expected = {'sourceCommit': SOURCE, 'sourceTree': TREE, 'builderCommit': builder_commit,
                'bundleIdentifier': BUNDLE_ID, 'executableSHA256': identity['executableSHA256']}
    require(report.get('identity') == expected, 'Observation identity differs from built/installed product')
    require(report.get('status') == 'observed' and type(report.get('launchCount')) is int and report['launchCount'] == 1,
            'Incomplete or modified launch path')
    observations = report.get('observations')
    require(isinstance(observations, list) and len(observations) == 3, 'Missing picker observations')
    expected_names = {'host-probe-observations.json', 'host-probe-launch.png', 'host-probe-launch.txt'}
    navigation = report.get('navigation')
    require(isinstance(navigation, list) and len(navigation) <= 1, 'Unexpected startup/navigation actions')
    if navigation:
        actual_labels = navigation[0].get('actualStaticTextLabels')
        require(isinstance(actual_labels, list) and len(actual_labels) <= 64 and all(isinstance(x, str) and len(x.encode('utf-8')) <= 16384 for x in actual_labels), 'Missing bounded actual startup labels')
        expected_navigation = [{'action': 'known-notification-later', 'title': 'Stay Informed About Refreshes',
                                'body': 'LiveContainer can notify you when a refresh starts, completes, or needs attention. Nothing runs differently if you skip this.',
                                'buttons': ['Allow Notifications', 'Later'], 'tapped': 'Later',
                                'hittableBeforeTap': True, 'noAlertsAfterTap': True, 'actualStaticTextLabels': actual_labels}]
        require(navigation == expected_navigation, 'Unreviewed startup action')
        require(actual_labels.count(navigation[0]['title']) == 1 and actual_labels.count(navigation[0]['body']) == 1, 'Startup labels do not match exact known explanation')
        require(navigation[0]['hittableBeforeTap'] is True and navigation[0]['noAlertsAfterTap'] is True, 'Invalid navigation Boolean')
        expected_names |= {'host-probe-notice-before.png', 'host-probe-notice-before.txt',
                           'host-probe-notice-after.png', 'host-probe-notice-after.txt'}
    for row, code, native, stage in zip(observations, ['en', 'he', 'en'], ['English', 'עברית', 'English'],
                                      ['en-before', 'he-selected', 'en-returned']):
        require(row.get('stage') == stage and row.get('language') == code and row.get('selectedNativeName') == native and
                row.get('selectedValueVisible') is True and row.get('settingsTabVisible') is True and
                row.get('applicationForeground') is True, 'Invalid selected-language observation')
        label, value, children = row.get('pickerLabel'), row.get('pickerValue'), row.get('pickerChildLabels')
        require(isinstance(label, str) and isinstance(value, str) and isinstance(children, list) and all(isinstance(x, str) for x in children), 'Missing raw selected-value evidence')
        require(value == native or children.count(native) == 1 or native in label.split(', '), 'Raw picker evidence does not show selected language')
        require(row.get('settingsTabLabel') == ('הגדרות' if code == 'he' else 'Settings'), 'Raw Settings tab label differs')
        frame = row.get('pickerFrame')
        require(isinstance(frame, dict) and set(frame) == {'x', 'y', 'width', 'height'} and all(type(v) in (int, float) and math.isfinite(v) for v in frame.values()) and frame['width'] > 0 and frame['height'] > 0, 'Invalid picker rectangle')
        for suffix in ('.png', '.txt'):
            name = 'host-probe-' + stage + suffix
            expected_names.add(name)
            require(name in rows, 'Missing screenshot/AX: ' + name)
    require(set(rows) == expected_names, 'Missing or unexpected successful probe attachments')
    for name, path in rows.items():
        if name.endswith('.png'):
            require(path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'), 'Invalid screenshot')
        elif name.endswith('.txt'):
            require(0 < path.stat().st_size <= 512 * 1024 and path.read_text().strip(), 'Missing/oversized AX snapshot')
    return {'report': report, 'attachments': {name: {'sha256': sha(path), 'bytes': path.stat().st_size} for name, path in rows.items()}}


def compiled_resource_contracts(app, host):
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    localization = load('p2_localization_contract', ROOT / 'scripts/p2_localization_contract.py')
    permissions = load('host_probe_permissions', ROOT / 'scripts/p2_permission_contract.py')
    preflight = load('host_probe_preflight', ROOT / 'ci/p2/preflight.py')
    catalog = strict_json((Path(host) / 'Resources/Localizable.xcstrings').read_text())
    require(len(catalog['strings']) == 2088, 'Actual source catalog coverage drift')
    names = {str(p.relative_to(app)) for p in Path(app).rglob('*') if p.is_file()}
    return {'scope': 'Actual unchanged LiveContainer.app packaging only; no runtime plural/OS permission acceptance',
            'bundleIdentifier': BUNDLE_ID, 'productInventory': bundle_inventory(app),
            'localization': localization.verify_app(app),
            'exactPluralValues': preflight.verify_exact_plural_values(app, catalog),
            'permissions': permissions.verify_compiled_permissions(lambda name: (Path(app) / name).read_bytes(), names)}


class Probe:
    def __init__(self, host, work, evidence, approved):
        self.host, self.work, self.evidence = host, work, evidence
        self.approved = approved
        self.stage = 'preflight'
        self.number = 0
        self.device = None
        self.app = None
        self.installed = None
        self.identity = None
        self.installed_baseline = None
        self.before = None
        self.dependencies_ready = False
        self.report = {'status': 'BLOCKED', 'stage': self.stage, 'sourceCommit': SOURCE, 'sourceTree': TREE,
                       'scope': LIMITS, 'fullAppAcceptance': False, 'finalCompositionAcceptance': False,
                       'nativeExecution': False, 'errors': [], 'retentionErrors': []}

    def command(self, *argv, timeout=300):
        self.number += 1
        stem = self.evidence / 'commands' / f'{self.number:03d}'
        stem.parent.mkdir(exist_ok=True)
        argv = [str(a) for a in argv]
        start = time.monotonic()
        env = dict(os.environ)
        # Keep the blocked command receipt too; never export the full environment.
        override_keys = [key for key in ('XCODE_XCCONFIG_FILE', 'SDKROOT', 'TOOLCHAINS') if env.get(key)]
        meta = {'argv': argv, 'stage': self.stage, 'cwd': str(Path.cwd()),
                'developerDir': env.get('DEVELOPER_DIR'), 'timeoutSeconds': timeout,
                'ambientOverrideKeys': override_keys}
        try:
            require(not override_keys, 'Ambient build override forbidden: ' + ', '.join(override_keys))
            result = subprocess.run(argv, capture_output=True, timeout=timeout, env=env)
            stem.with_suffix('.stdout').write_bytes(result.stdout)
            stem.with_suffix('.stderr').write_bytes(result.stderr)
            meta['returncode'] = result.returncode
            require(result.returncode == 0, 'Command failed; see commands/' + stem.name)
            return result.stdout.decode('utf-8', errors='strict').strip()
        except subprocess.TimeoutExpired as error:
            stem.with_suffix('.stdout').write_bytes(error.stdout or b'')
            stem.with_suffix('.stderr').write_bytes(error.stderr or b'')
            meta['timedOut'] = True
            raise ValueError('Command timed out; see commands/' + stem.name) from error
        except Exception as error:
            meta['error'] = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            meta['elapsedSeconds'] = time.monotonic() - start
            write(stem.with_suffix('.json'), meta)

    def retain_try(self, name, operation):
        try:
            return operation()
        except Exception as error:
            self.report['retentionErrors'].append(name + ': ' + str(error))
            return None

    def proof(self):
        proof = {'host': checkout_proof(self.host, SOURCE, TREE, SOURCE_URL, dependencies=True)}
        if self.dependencies_ready:
            proof['dependencies'] = {name: checkout_proof(self.host / name, row['commit'], row['tree'], row['fetch_url'])
                                     for name, row in DEPENDENCIES.items()}
        return proof

    def checkout_diagnostics(self, root, label, expected):
        write(self.evidence / ('provenance/' + label + '-expected.json'), expected)
        # These are raw operands, not accepted provenance. Keep them even when
        # checkout_proof subsequently rejects a tree, origin, mode or worktree.
        for arguments in [('rev-parse', 'HEAD'), ('rev-parse', 'HEAD^{tree}'),
                          ('remote', 'get-url', 'origin'),
                          ('status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none'),
                          ('ls-tree', '-rz', '--full-tree', 'HEAD'),
                          ('diff', '--no-ext-diff', '--binary', 'HEAD', '--')]:
            self.retain_try(label + ' ' + arguments[0],
                            lambda arguments=arguments: self.command('git', '-C', root, *arguments))

    def retain_preflight_inputs(self):
        expected = {'approvedInputsSHA256': self.approved, 'sourceCommit': SOURCE,
                    'sourceTree': TREE, 'sourceOrigin': SOURCE_URL, 'builderAncestor': BUILDER_PARENT}
        write(self.evidence / 'preflight-expected.json', expected)
        paths = ['ci/p2/host_probe/inputs.json', '.github/workflows/p2-host-settings-probe.yml',
                 *['ci/p2/host_probe/' + name for name in ['run.py', 'test_probe.py', 'README.md',
                    'HostSettingsProbeTests.swift.in', 'project/project.pbxproj', 'project/HostSettingsProbe.xcscheme',
                    'install_contract.py', 'test_install_contract.py']],
                 'scripts/p2_localization_contract.py', 'scripts/p2_permission_contract.py',
                 'ci/p2/preflight.py', 'tests/fixtures/p2_localization_contracts/compiled-translations.json']
        for name in paths:
            # Retain the fixed workflow under a visible name: artifact upload omits dot directories.
            retained_name = 'workflow/p2-host-settings-probe.yml' if name == '.github/workflows/p2-host-settings-probe.yml' else name
            target = self.evidence / 'preflight-inputs' / retained_name
            target.parent.mkdir(parents=True, exist_ok=True)
            self.retain_try('preflight input ' + name, lambda name=name, target=target: shutil.copyfile(ROOT / name, target))
        self.checkout_diagnostics(self.host, 'source-observed-before-verification',
                                  {'commit': SOURCE, 'tree': TREE, 'origin': SOURCE_URL})
        self.retain_try('builder identity', lambda: self.command('git', '-C', ROOT, 'rev-parse', 'HEAD', 'HEAD^{tree}'))
        self.retain_try('builder status', lambda: self.command('git', '-C', ROOT, 'status', '--porcelain=v1', '--untracked-files=all'))

    def acquire_dependencies(self):
        for name, row in DEPENDENCIES.items():
            root = self.host / name
            require(root.is_dir() and not root.is_symlink() and not any(root.iterdir()), 'Dependency directory must start empty: ' + name)
            self.command('git', '-C', root, 'init')
            self.command('git', '-C', root, 'remote', 'add', 'origin', row['fetch_url'])
            self.command('git', '-C', root, 'fetch', '--depth=1', '--no-tags', '--no-recurse-submodules', 'origin', row['commit'], timeout=600)
            self.command('git', '-C', root, 'checkout', '--detach', row['commit'])
            self.checkout_diagnostics(root, name + '-observed-before-verification',
                                      {'commit': row['commit'], 'tree': row['tree'], 'origin': row['fetch_url']})
            require(not self.report['retentionErrors'], 'Incomplete dependency diagnostic capture')
            proof = checkout_proof(root, row['commit'], row['tree'], row['fetch_url'])
            write(self.evidence / 'provenance' / (name + '.json'), proof)
        self.dependencies_ready = True

    def installed_identity_gate(self, settings, label, message):
        # Persist raw operands before schema, containment or equality assertions.
        # A diagnostic inventory never authorizes launch; verify_product and
        # the parsed, exact-content installation contract remain mandatory below.
        inventory = bundle_inventory(self.installed, strict=False)
        write(self.evidence / ('provenance/installed-' + label + '-inventory.json'),
              {'path': str(self.installed), 'files': inventory})
        write(self.evidence / ('provenance/installed-' + label + '-diff.json'),
              {'expectedInventory': 'provenance/product-identity.json',
               'actualInventory': 'provenance/installed-' + label + '-inventory.json',
               **inventory_difference(self.identity['files'], inventory)})
        actual = verify_product(self.installed, settings)
        write(self.evidence / ('provenance/installed-' + label + '-identity.json'),
              {'path': str(self.installed), **actual})
        write(self.evidence / ('provenance/installed-' + label + '-observation-stability.json'),
              inventory_difference(inventory, actual['files']))
        require(inventory == actual['files'], 'Installed bundle changed during identity observation')
        try:
            contract = INSTALL_CONTRACT.verify_installation_identity(
                self.app, self.installed, self.identity['files'], actual['files'])
        except Exception as error:
            write(self.evidence / ('provenance/installed-' + label + '-contract.json'),
                  {'status': 'rejected', 'error': type(error).__name__ + ': ' + str(error)})
            raise ValueError(message + ': ' + str(error)) from error
        write(self.evidence / ('provenance/installed-' + label + '-contract.json'), contract)
        if self.installed_baseline is None:
            self.installed_baseline = actual['files']
        else:
            write(self.evidence / ('provenance/installed-' + label + '-baseline-diff.json'),
                  inventory_difference(self.installed_baseline, actual['files']))
            require(actual['files'] == self.installed_baseline, 'Installed identity changed after initial accepted observation')
        return actual

    def installed_signature_gate(self, settings, label):
        # Verify the real installed bundle and each exact embedded library. No
        # chmod, re-signing, source substitution, or availability fallback.
        receipt = {'status': 'blocked', 'scope': 'installed bundle and seven exact embedded libraries',
                   'objects': [], 'label': label}
        try:
            before = self.installed_identity_gate(settings, label + '-presignature', 'Installed identity differs before signature verification')
            for relative in (None, *sorted(INSTALL_CONTRACT.ALLOWED_DYLIB_PATHS)):
                target = self.installed if relative is None else self.installed / relative
                self.command('codesign', '--verify', '--deep', '--strict', target)
                receipt['objects'].append({'path': '.' if relative is None else relative, 'command': self.number, 'verified': True})
            after = self.installed_identity_gate(settings, label + '-postsignature', 'Installed identity differs after signature verification')
            write(self.evidence / ('provenance/installed-' + label + '-signature-stability.json'),
                  inventory_difference(before['files'], after['files']))
            require(before == after, 'Installed identity changed during signature verification')
            receipt['status'] = 'verified'
        except Exception as error:
            receipt['error'] = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            write(self.evidence / ('provenance/installed-' + label + '-signatures.json'), receipt)

    def retain_installed_bundle(self):
        # Only the exact app path returned by this run's simctl acquisition.
        # Do not rediscover, normalize, repair, launch, or accept a different app.
        if self.installed is None:
            return
        before = bundle_inventory(self.installed, strict=False)
        write(self.evidence / 'provenance/installed-finalization-inventory.json',
              {'path': str(self.installed), 'files': before})
        captured = archive_bundle(self.installed, self.evidence / 'installed-LiveContainer.app.zip')
        write(self.evidence / 'provenance/installed-archive-inventory.json', {'files': captured})
        write(self.evidence / 'provenance/installed-archive-diff.json', inventory_difference(before, captured))
        after = bundle_inventory(self.installed, strict=False)
        write(self.evidence / 'provenance/installed-finalization-after-inventory.json',
              {'path': str(self.installed), 'files': after})
        write(self.evidence / 'provenance/installed-retention-diff.json', inventory_difference(before, after))
        require(before == captured == after, 'Installed bundle changed during evidence retention')

    def main(self):
        self.retain_preflight_inputs()
        data, proof = verify_inputs(self.host, self.approved)
        require(not self.report['retentionErrors'], 'Incomplete preflight diagnostic capture')
        self.before = proof
        write(self.evidence / 'inputs.json', data)
        write(self.evidence / 'provenance/source-initial.json', proof)
        self.report['builderCommit'] = git(ROOT, 'rev-parse', 'HEAD')
        self.report['builderTree'] = git(ROOT, 'rev-parse', 'HEAD^{tree}')
        self.report['builderWorktreeStatus'] = git(ROOT, 'status', '--porcelain=v1', '--untracked-files=all')
        for name in data['contractFiles']:
            target = self.evidence / 'resource-contract-inputs' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        for name in data['adapterFiles']:
            target = self.evidence / 'probe-inputs' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(HERE / name, target)
        require(not self.report['builderWorktreeStatus'], 'Builder checkout must be clean for native execution')
        require(not os.environ.get('GITHUB_SHA') or os.environ['GITHUB_SHA'] == self.report['builderCommit'], 'CI builder commit mismatch')
        workflow = ROOT / '.github/workflows/p2-host-settings-probe.yml'
        shutil.copyfile(workflow, self.evidence / 'workflow.yml')
        self.report['workflowSHA256'] = sha(workflow)
        self.command('git', '-C', ROOT, 'merge-base', '--is-ancestor', BUILDER_PARENT, 'HEAD')
        require(platform.system() == 'Darwin', 'Darwin and Xcode required; no native execution or feasibility claimed')
        self.command('xcodebuild', '-version')
        self.command('xcrun', '--find', 'simctl')
        self.command('git', '-C', self.host, 'archive', '--format=tar', '--output=' + str(self.evidence / 'unchanged-source.tar'), 'HEAD')
        self.stage = 'dependency-acquisition'
        self.acquire_dependencies()
        write(self.evidence / 'provenance/source-before-build.json', self.proof())
        self.stage = 'fresh-simulator'
        runtimes = strict_json(self.command('xcrun', 'simctl', 'list', 'runtimes', '--json'))
        types = strict_json(self.command('xcrun', 'simctl', 'list', 'devicetypes', '--json'))
        runtime, device_type = choose_simulator(runtimes, types)
        self.device = self.command('xcrun', 'simctl', 'create', 'P2-Unchanged-Host-Settings-' + os.environ.get('GITHUB_RUN_ID', str(os.getpid())), device_type['identifier'], runtime['identifier'])
        require(re.fullmatch('[0-9A-Fa-f-]{36}', self.device), 'Malformed created simulator identifier')
        write(self.evidence / 'simulator.json', {'udid': self.device, 'runtime': runtime, 'deviceType': device_type, 'newlyCreated': True})
        self.command('xcrun', 'simctl', 'boot', self.device)
        self.command('xcrun', 'simctl', 'bootstatus', self.device, '-b', timeout=180)
        derived = self.work / 'product-derived'
        base = product_build_command(self.host, derived, self.device)
        self.stage = 'genuine-build-settings'
        raw = self.command(*base, '-showBuildSettings', '-json', timeout=180)
        (self.evidence / 'product-build-settings.json').write_text(raw + '\n')
        settings, self.app = host_build_settings(raw, self.host, derived)
        self.stage = 'genuine-simulator-build'
        self.command(*base, '-resultBundlePath', self.evidence / 'product-build.xcresult', 'build', timeout=1500)
        write(self.evidence / 'provenance/product-raw-inventory.json',
              {'path': str(self.app), 'files': bundle_inventory(self.app, strict=False)})
        self.command('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', self.app, self.evidence / 'actual-LiveContainer.app.zip')
        self.identity = verify_product(self.app, settings)
        self.identity['machOPlatforms'] = simulator_platform(self.command('xcrun', 'vtool', '-show-build', self.app / 'LiveContainer'))
        self.command('xcrun', 'lipo', '-archs', self.app / 'LiveContainer')
        write(self.evidence / 'provenance/product-identity.json', self.identity)
        self.stage = 'actual-host-compiled-resources'
        resources = compiled_resource_contracts(self.app, self.host)
        write(self.evidence / 'actual-host-compiled-resources.json', resources)
        self.report['actualHostCompiledResources'] = {name: resources[name] for name in ('localization', 'exactPluralValues', 'permissions')}
        require(all(resources[name]['status'] == 'passed' for name in ('localization', 'exactPluralValues', 'permissions')), 'Actual host compiled-resource contracts failed')
        write(self.evidence / 'provenance/source-after-build.json', self.proof())
        require(self.before == self.proof()['host'], 'Main source changed during genuine build')
        self.stage = 'product-signature-feasibility'
        self.command('codesign', '--verify', '--deep', '--strict', self.app)
        self.command('codesign', '-d', '--entitlements', ':-', self.app)
        self.stage = 'install-identity'
        listapps = self.command('xcrun', 'simctl', 'listapps', self.device)
        (self.evidence / 'installed-before.openstep.plist').write_text(listapps + '\n')
        converted = self.command('plutil', '-convert', 'json', '-o', '-', self.evidence / 'installed-before.openstep.plist')
        installed_before = strict_json(converted)
        require(isinstance(installed_before, dict), 'Unexpected simctl installed-app inventory schema')
        write(self.evidence / 'installed-before.json', installed_before)
        require(BUNDLE_ID not in installed_before, 'App already exists on supposedly fresh simulator')
        self.command('xcrun', 'simctl', 'install', self.device, self.app)
        self.installed = Path(self.command('xcrun', 'simctl', 'get_app_container', self.device, BUNDLE_ID, 'app'))
        self.installed_identity_gate(settings, 'initial', 'Installed product violates exact-content installation contract')
        self.installed_signature_gate(settings, 'initial')
        self.stage = 'external-runner-generation'
        catalog = strict_json((self.host / 'Resources/Localizable.xcstrings').read_text())
        runner = prepare_runner(self.work / 'runner', self.identity, self.report['builderCommit'], catalog)
        shutil.copytree(self.work / 'runner', self.evidence / 'generated-runner')
        runner_derived = self.work / 'runner-derived'
        runner_base = ['xcodebuild', '-project', runner, '-scheme', 'HostSettingsProbe', '-configuration', 'Debug',
                       '-sdk', 'iphonesimulator', '-destination', 'platform=iOS Simulator,id=' + self.device,
                       '-derivedDataPath', runner_derived]
        raw = self.command(*runner_base, '-showBuildSettings', '-json')
        (self.evidence / 'runner-build-settings.json').write_text(raw + '\n')
        self.command(*runner_base, '-resultBundlePath', self.evidence / 'runner-build.xcresult', 'build-for-testing', timeout=600)
        generated = list((runner_derived / 'Build/Products').glob('*.xctestrun'))
        require(len(generated) == 1, 'Expected exactly one generated external-runner xctestrun')
        xctestrun = generated[0]
        shutil.copyfile(xctestrun, self.evidence / 'generated.xctestrun')
        before_xctestrun = sha(xctestrun)
        wiring = validate_xctestrun(plistlib.loads(xctestrun.read_bytes()), xctestrun.parent, self.app)
        write(self.evidence / 'runner-wiring.json', {**wiring, 'xctestrunSHA256': before_xctestrun})
        self.command('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', wiring['runnerPath'], self.evidence / 'external-runner.app.zip')
        self.installed_identity_gate(settings, 'prelaunch', 'Installed identity changed before launch')
        self.installed_signature_gate(settings, 'prelaunch')
        self.stage = 'actual-host-settings-navigation'
        self.report['nativeExecution'] = True
        self.command('xcodebuild', 'test-without-building', '-xctestrun', xctestrun,
                     '-destination', 'platform=iOS Simulator,id=' + self.device,
                     '-parallel-testing-enabled', 'NO', '-maximum-concurrent-test-simulator-destinations', '1',
                     '-resultBundlePath', self.evidence / 'host-probe.xcresult', timeout=240)
        after_xctestrun = sha(xctestrun)
        shutil.copyfile(xctestrun, self.evidence / 'generated-after-test.xctestrun')
        write(self.evidence / 'xctestrun-after-test-comparison.json', {'beforeSHA256': before_xctestrun, 'afterSHA256': after_xctestrun})
        require(after_xctestrun == before_xctestrun, 'Generated xctestrun mutated during test')
        self.stage = 'native-evidence-verification'
        self.export_results()
        summary = strict_json((self.evidence / 'xctest-summary.json').read_text())
        tests = strict_json((self.evidence / 'xctest-tests.json').read_text())
        verify_summary(summary, tests)
        observations = verify_observations(self.evidence / 'attachments', self.identity, self.report['builderCommit'])
        write(self.evidence / 'selected-language-evidence.json', observations)
        self.stage = 'final-installed-identity'
        self.installed_identity_gate(settings, 'postnavigation', 'Installed identity changed after navigation')
        self.installed_signature_gate(settings, 'postnavigation')
        self.report['status'] = 'FEASIBLE_SETTINGS_PICKER'
        self.report['stage'] = 'bounded-settings-round-trip-observed'

    def export_results(self):
        bundle = self.evidence / 'host-probe.xcresult'
        if not bundle.is_dir():
            return
        for kind in ('summary', 'tests'):
            def export(kind=kind):
                raw = self.command('xcrun', 'xcresulttool', 'get', 'test-results', kind, '--path', bundle)
                write(self.evidence / ('xctest-' + kind + '.json'), strict_json(raw))
            if not (self.evidence / ('xctest-' + kind + '.json')).exists():
                self.retain_try('XCTest ' + kind, export)
        if not (self.evidence / 'attachments').exists():
            self.retain_try('XCTest attachments', lambda: self.command('xcrun', 'xcresulttool', 'export', 'attachments', '--path', bundle,
                                                                     '--output-path', self.evidence / 'attachments'))

    def finish(self):
        self.retain_try('installed app inventory and archive', self.retain_installed_bundle)
        self.export_results()
        for label, root in [('product', self.work / 'product-derived/Build/Products'), ('runner', self.work / 'runner-derived/Build/Products')]:
            if root.is_dir():
                self.retain_try(label + ' partial products', lambda root=root, label=label: self.command(
                    'ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', root, self.evidence / (label + '-all-products.zip')))
        if (self.work / 'runner').is_dir() and not (self.evidence / 'generated-runner').exists():
            self.retain_try('generated runner sources', lambda: shutil.copytree(self.work / 'runner', self.evidence / 'generated-runner'))
        if self.device:
            self.retain_try('final simulator screenshot', lambda: self.command('xcrun', 'simctl', 'io', self.device, 'screenshot', self.evidence / 'final-simulator.png'))
            self.retain_try('bounded app process log', lambda: (self.evidence / 'host-process-log.json').write_text(self.command(
                'xcrun', 'simctl', 'spawn', self.device, 'log', 'show', '--last', '3m', '--style', 'json',
                '--predicate', 'process == "LiveContainer"', timeout=60) + '\n'))
            self.retain_try('shutdown dedicated simulator', lambda: self.command('xcrun', 'simctl', 'shutdown', self.device))
        if self.before:
            self.retain_try('final source raw checkout operands', lambda: self.checkout_diagnostics(
                self.host, 'source-final-observed', {'commit': SOURCE, 'tree': TREE, 'origin': SOURCE_URL}))
            if self.dependencies_ready:
                for name, row in DEPENDENCIES.items():
                    self.retain_try(name + ' final raw checkout operands', lambda name=name, row=row: self.checkout_diagnostics(
                        self.host / name, name + '-final-observed',
                        {'commit': row['commit'], 'tree': row['tree'], 'origin': row['fetch_url']}))
            final = self.retain_try('final source proof', self.proof)
            if final:
                write(self.evidence / 'provenance/source-final.json', final)
                if self.before != final['host']:
                    self.report['errors'].append('Main source changed during probe')
        if self.report['retentionErrors'] or self.report['errors']:
            self.report['status'] = 'BLOCKED'
        if self.report['status'] != 'FEASIBLE_SETTINGS_PICKER':
            self.report['stage'] = self.stage
        write(self.evidence / 'feasibility.json', self.report)
        inventory = {str(p.relative_to(self.evidence)): {'sha256': sha(p), 'bytes': p.stat().st_size}
                     for p in sorted(self.evidence.rglob('*')) if p.is_file() and not p.is_symlink()}
        write(self.evidence / 'evidence-sha256.json', inventory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--approved-inputs-sha256', required=True)
    args = parser.parse_args()
    host, work, evidence = [p.resolve() for p in (args.host, args.work, args.evidence)]
    require(not work.exists() and not evidence.exists(), 'Use new work and evidence directories; never erase/reset old state')
    require(not work.is_relative_to(host) and not evidence.is_relative_to(host) and
            not work.is_relative_to(evidence) and not evidence.is_relative_to(work), 'Work/evidence/source paths must be disjoint')
    work.mkdir(parents=True)
    evidence.mkdir(parents=True)
    probe = Probe(host, work, evidence, args.approved_inputs_sha256)
    try:
        probe.main()
    except Exception as error:
        probe.report['errors'].append(type(error).__name__ + ': ' + str(error))
    finally:
        probe.finish()
    print(json.dumps(probe.report, indent=2))
    return 0 if probe.report['status'] == 'FEASIBLE_SETTINGS_PICKER' else 1


if __name__ == '__main__':
    sys.exit(main())
