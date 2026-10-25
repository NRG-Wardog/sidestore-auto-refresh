#!/usr/bin/env python3
"""Offline authentication of one immutable, genuine P2 host-build artifact.

No subprocesses, network, imported artifact code, native execution, installation,
or signing. Historical build evidence never becomes fresh native acceptance.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import stat
import tarfile
import unicodedata
import zipfile

ARTIFACT_ID = 12112643772
ORIGINAL_RUN_ID = 39064168648
ARTIFACT_SHA256 = 'c2c4166674996460cc9c173871b456bae1fc0bc975b7538c011df350103c2f94'
APP_ZIP_SHA256 = 'ffa6122ad883ceb35e18f43c8a33e59f1855683e16fcfef6938d783ea2999cb6'
SOURCE_TAR_SHA256 = '9b5d02229d0b3a7ab599458a292d8cf36a7a8fc2f567d618636648fc5341fed4'
INPUTS_SHA256 = 'd90ebf4e6729b0c87498b1fc5c6037120753b4d40fb9c2abeedac6a9055c3bb2'
BUILD_SETTINGS_SHA256 = 'dfe47fe08b218d59acfa9d92c710615081d3b98e11615bfe07a39b8ad3de66d7'
PRODUCT_IDENTITY_SHA256 = 'b659019566e6b70b787993504d9d1c87b9ce1630109c33e49fe2aa7e777e3ed9'
SOURCE = 'eb10cedb772bc0496d67edd7a269679e24826b9f'
TREE = 'd6960a99a2f022a333c364fa4d28e264568a28ca'
BUILDER = '4cb67e4d252ba1b1f7a6fa6431560f75264f337c'
BUILDER_TREE = '2971e719ec0b3771d5c95e5c1776dd58e555c183'
BUILDER_PARENT = '4fe6738d3223fe632e179642d52d429866b052d1'
SOURCE_URL = 'https://github.com/NRG-Wardog/LiveContainer.git'
BUNDLE_ID = 'com.kdt.livecontainer'
ORIGINAL_CWD = '/Users/runner/work/sidestore-auto-refresh/sidestore-auto-refresh'
HOST_ROOT = ORIGINAL_CWD + '/maintained-source'
DERIVED = ORIGINAL_CWD + '/p2-host-probe-work/product-derived'
ORIGINAL_EVIDENCE = ORIGINAL_CWD + '/p2-host-probe-evidence'
DEVELOPER_DIR = '/Applications/Xcode_26.4.1.app/Contents/Developer'
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
MAX_BYTES = 256 * 1024 * 1024
PREFIX = 'p2-host-probe-evidence/'
CATALOG = 'Resources/Localizable.xcstrings'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git_object(kind, raw):
    return hashlib.sha1(kind.encode('ascii') + b' ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()


def strict_json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def number(raw):
        result = float(raw)
        require(math.isfinite(result), 'Nonfinite JSON number')
        return result
    def constant(raw):
        raise ValueError('Nonfinite JSON constant: ' + raw)
    return json.loads(raw, object_pairs_hook=pairs, parse_float=number, parse_constant=constant)


def safe_relative(name):
    require(isinstance(name, str) and name, 'Empty or nonstring path')
    require(not any(ord(c) < 32 or ord(c) == 127 for c in name), 'Control character in path')
    require('\\' not in name and ':' not in name, 'Platform-ambiguous path: ' + name)
    require(not PurePosixPath(name).is_absolute() and
            all(x not in ('', '.', '..') for x in name.split('/')), 'Unsafe relative path: ' + name)
    require(unicodedata.normalize('NFC', name) == name, 'Noncanonical Unicode path: ' + name)
    return name


def path_inventory(rows):
    """Reject aliases, duplicate entries, and file/directory prefix conflicts."""
    explicit = {}
    aliases = {}
    for name, is_directory in rows:
        safe_relative(name)
        require(name not in explicit, 'Duplicate path: ' + name)
        explicit[name] = is_directory
        parts = name.split('/')
        for index in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:index])
            alias = unicodedata.normalize('NFD', prefix).casefold()
            require(alias not in aliases or aliases[alias] == prefix, 'Case or Unicode path ambiguity: ' + prefix)
            aliases[alias] = prefix
    for name in explicit:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.':
                require(explicit.get(str(parent), True), 'File used as directory: ' + str(parent))
    return explicit


def read_stable(path):
    """Pin a regular file descriptor and compare two reads, stat, and pathname."""
    path = Path(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode), 'Input is not a regular file: ' + str(path))
    require(before.st_size <= MAX_BYTES and before.st_nlink == 1, 'Oversized or hard-linked input')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(fd)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
        require(signature(before) == signature(opened), 'Input changed before read')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, 'Input exceeds byte limit')
            stream.seek(0)
            second = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                second.update(chunk)
        require(second.hexdigest() == digest(raw), 'Input changed between snapshot reads')
        require(signature(opened) == signature(os.fstat(fd)) == signature(path.lstat()), 'Input changed during read')
        return raw
    finally:
        os.close(fd)


def zip_payload(raw, *, allow_directories):
    """Read bounded immutable bytes, never using extract/extractall."""
    result = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        require(0 < len(members) <= 20000, 'Invalid ZIP member count')
        require(sum(m.file_size for m in members) <= MAX_BYTES, 'ZIP expands beyond byte limit')
        paths = []
        for member in members:
            require(member.orig_filename == member.filename, 'ZIP filename was truncated')
            directory = member.is_dir()
            name = member.filename[:-1] if directory else member.filename
            paths.append((name, directory))
            require(member.create_system == 3, 'ZIP lacks Unix mode provenance')
            mode = member.external_attr >> 16
            require(stat.S_IFMT(mode) == (stat.S_IFDIR if directory else stat.S_IFREG), 'Unsupported ZIP entry type: ' + name)
            require(not directory or allow_directories, 'Unexpected ZIP directory: ' + name)
            require(not member.flag_bits & 1, 'Encrypted ZIP entry')
            require(not mode & 0o7000, 'Special permission bits in ZIP')
            require(not directory or member.file_size == 0, 'Directory contains bytes')
        path_inventory(paths)
        for member, (name, directory) in zip(members, paths):
            data = archive.read(member)
            require(len(data) == member.file_size, 'ZIP member length differs: ' + name)
            result[name] = {'data': data, 'mode': member.external_attr >> 16, 'directory': directory}
    return result


def verify_index(payload):
    index_name = PREFIX + 'evidence-sha256.json'
    require(index_name in payload, 'Missing artifact index')
    index = strict_json(payload[index_name]['data'])
    require(isinstance(index, dict) and len(index) == 326, 'Unexpected evidence index count')
    path_inventory((name, False) for name in index)
    expected = {PREFIX + name for name in index} | {index_name, 'p2-host-probe-portable.log'}
    require(set(payload) == expected, 'Unindexed, missing, or unexpected outer payload')
    for name, row in index.items():
        require(isinstance(row, dict) and set(row) == {'bytes', 'sha256'}, 'Invalid index row: ' + name)
        require(type(row['bytes']) is int and row['bytes'] >= 0 and
                re.fullmatch('[0-9a-f]{64}', row['sha256'] or ''), 'Invalid indexed identity')
        raw = payload[PREFIX + name]['data']
        require(len(raw) == row['bytes'] and digest(raw) == row['sha256'], 'Indexed payload differs: ' + name)
    # The portable log is not in the producer index; it is authenticated only
    # by the hard-coded outer ZIP digest, and is never used as a success claim.
    return {name: payload[PREFIX + name]['data'] for name in index}, index


def reconstruct_tree(rows):
    require(isinstance(rows, dict) and rows, 'Empty or invalid Git manifest')
    path_inventory((name, False) for name in rows)
    root = {}
    for name, row in rows.items():
        require(isinstance(row, dict), 'Invalid Git manifest row')
        require((row.get('mode'), row.get('type')) in {
            ('100644', 'blob'), ('100755', 'blob'), ('120000', 'blob'), ('160000', 'commit')}, 'Unsupported Git mode/type')
        fields = {'mode', 'type', 'gitObject'}
        if row['type'] == 'blob':
            fields.add('sha256')
        if row['mode'] == '120000':
            fields |= {'linkTarget', 'resolvedTarget'}
        require(set(row) == fields, 'Unexpected Git manifest fields')
        require(re.fullmatch('[0-9a-f]{40}', row.get('gitObject', '')), 'Invalid Git object ID')
        if row['type'] == 'blob':
            require(re.fullmatch('[0-9a-f]{64}', row.get('sha256', '')), 'Missing blob SHA-256')
        if row['mode'] == '120000':
            target = row.get('linkTarget')
            require(isinstance(target, str) and target and '\\' not in target and '\0' not in target and
                    not PurePosixPath(target).is_absolute(), 'Unsafe historical source link')
            require(git_object('blob', target.encode()) == row['gitObject'] and digest(target.encode()) == row['sha256'], 'Historical link bytes differ')
        node = root
        parts = name.split('/')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = (row['mode'], row['gitObject'])
    def tree(node):
        encoded = []
        for name, value in node.items():
            directory = isinstance(value, dict)
            mode, oid = ('40000', tree(value)) if directory else value
            encoded.append((name.encode() + (b'/' if directory else b''),
                            mode.encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(oid)))
        return git_object('tree', b''.join(value for _, value in sorted(encoded)))
    verify_manifest_links(rows)
    return tree(root)


def verify_manifest_links(rows):
    directories = {str(p) for name in rows for p in PurePosixPath(name).parents if str(p) != '.'}
    for name, row in rows.items():
        if row['mode'] != '120000':
            continue
        pending = name.split('/')
        resolved, seen = [], set()
        while pending:
            part = pending.pop(0)
            if part in ('', '.'):
                continue
            if part == '..':
                require(resolved, 'Historical link escapes tree')
                resolved.pop()
                continue
            candidate = '/'.join([*resolved, part])
            target = rows.get(candidate)
            if target and target['mode'] == '120000':
                require(candidate not in seen and len(seen) < 128, 'Cyclic historical source link')
                seen.add(candidate)
                pending = target['linkTarget'].split('/') + pending
                continue
            require(candidate in directories or (target and target['type'] == 'blob' and not pending), 'Missing historical link destination')
            resolved.append(part)
        require(resolved and '/'.join(resolved) == row['resolvedTarget'], 'Historical resolved link differs')


def verify_provenance(files):
    load = lambda name: strict_json(files[name])
    source = load('provenance/source-initial.json')
    dependencies = {name: load('provenance/' + name + '.json') for name in DEPENDENCIES}
    expected = {'host': source, 'dependencies': dependencies}
    for phase in ('before-build', 'after-build', 'final'):
        require(load('provenance/source-' + phase + '.json') == expected, 'Source/dependency provenance changed: ' + phase)
    for name, manifest, commit, tree, origin, root in [
        ('source', source, SOURCE, TREE, SOURCE_URL, HOST_ROOT),
        *[(name, dependencies[name], pin['commit'], pin['tree'], pin['fetch_url'], HOST_ROOT + '/' + name)
          for name, pin in DEPENDENCIES.items()],
    ]:
        require(set(manifest) == {'commit', 'tree', 'origin', 'root', 'files'}, 'Unexpected provenance schema')
        require((manifest['commit'], manifest['tree'], manifest['root']) == (commit, tree, root), 'Provenance identity differs: ' + name)
        require(manifest['origin'].removesuffix('.git') == origin.removesuffix('.git'), 'Provenance origin differs: ' + name)
        require(reconstruct_tree(manifest['files']) == tree, 'Reconstructed Git tree differs: ' + name)
        for suffix in ('observed-before-verification-expected', 'final-observed-expected'):
            observation = load('provenance/' + name + '-' + suffix + '.json')
            require(observation == {'commit': commit, 'tree': tree, 'origin': origin}, 'Historical observation differs: ' + name)
    gitlinks = {name: row['gitObject'] for name, row in source['files'].items() if row['type'] == 'commit'}
    require(gitlinks == {name: pin['commit'] for name, pin in DEPENDENCIES.items()}, 'Host gitlinks differ')
    require(all(row['type'] != 'commit' for m in dependencies.values() for row in m['files'].values()), 'Nested dependency gitlink')
    return source, dependencies


def verify_source_tar(raw, manifest):
    require(digest(raw) == SOURCE_TAR_SHA256, 'Source tar digest differs')
    expected = manifest['files']
    blobs = {name: row for name, row in expected.items() if row['type'] == 'blob'}
    require(len(blobs) == 300 and all(r['mode'] == '100644' for r in blobs.values()), 'Unexpected host source blob inventory')
    directories = {str(p) for name in expected for p in PurePosixPath(name).parents if str(p) != '.'}
    directories |= {name for name, row in expected.items() if row['type'] == 'commit'}
    captured = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        require(archive.pax_headers == {'comment': SOURCE}, 'Source tar commit provenance differs')
        members = archive.getmembers()
        require(len(members) == len(blobs) + len(directories), 'Unexpected source tar member count')
        path_inventory((m.name, m.isdir()) for m in members)
        require({m.name for m in members if m.isdir()} == directories, 'Source tar directories differ')
        for member in members:
            require(member.pax_headers == {'comment': SOURCE}, 'Unexpected tar extended metadata')
            require(not member.linkname and not member.issparse(), 'Linked or sparse source entry')
            if member.isdir():
                require(member.mode == 0o775 and member.size == 0, 'Source directory mode/size differs')
                continue
            require(member.isfile() and member.name in blobs, 'Unexpected source tar entry')
            # git archive adds group-write; authenticate the full archive mode,
            # then the Git file mode (100644). Do not confuse the two formats.
            require(member.mode == 0o664, 'Source archive mode differs')
            data = archive.extractfile(member).read()
            row = blobs[member.name]
            require(len(data) == member.size and digest(data) == row['sha256'], 'Source blob SHA-256 differs: ' + member.name)
            require(git_object('blob', data) == row['gitObject'], 'Source Git blob differs: ' + member.name)
            captured[member.name] = data
    require(set(captured) == set(blobs), 'Source tar blob set differs')
    require(CATALOG in captured, 'Authenticated catalog missing')
    return captured


def verify_inputs(files):
    require(digest(files['inputs.json']) == INPUTS_SHA256, 'Original approved inputs digest differs')
    data = strict_json(files['inputs.json'])
    identity = {'schemaVersion': 1, 'sourceCommit': SOURCE, 'sourceTree': TREE, 'sourceRepository': SOURCE_URL,
                'builderParent': BUILDER_PARENT, 'project': 'LiveContainer.xcodeproj', 'scheme': 'LiveContainer',
                'configuration': 'Release', 'expectedTest': 'HostSettingsProbeTests/testUnchangedHostSettingsLanguageRoundTrip()'}
    require(set(data) == set(identity) | {'dependencies', 'adapterFiles', 'contractFiles'}, 'Unexpected approved input schema')
    require(all(data.get(k) == v for k, v in identity.items()) and data['dependencies'] == DEPENDENCIES, 'Original input identity differs')
    expected_adapters = {'HostSettingsProbeTests.swift.in', 'README.md', 'install_contract.py', 'project/HostSettingsProbe.xcscheme',
                         'project/project.pbxproj', 'run.py', 'test_install_contract.py', 'test_probe.py'}
    expected_contracts = {'ci/p2/preflight.py', 'scripts/p2_localization_contract.py', 'scripts/p2_permission_contract.py',
                          'tests/fixtures/p2_localization_contracts/compiled-translations.json'}
    require(set(data['adapterFiles']) == expected_adapters and set(data['contractFiles']) == expected_contracts, 'Original input file set differs')
    for name, value in data['adapterFiles'].items():
        require(digest(files['probe-inputs/' + name]) == value and
                digest(files['preflight-inputs/ci/p2/host_probe/' + name]) == value, 'Original adapter bytes differ: ' + name)
    for name, value in data['contractFiles'].items():
        require(digest(files['resource-contract-inputs/' + name]) == value and
                digest(files['preflight-inputs/' + name]) == value, 'Original contract bytes differ: ' + name)
    require(files['preflight-inputs/ci/p2/host_probe/inputs.json'] == files['inputs.json'], 'Preflight inputs differ')
    require(strict_json(files['preflight-expected.json']) == {
        'approvedInputsSHA256': INPUTS_SHA256, 'builderAncestor': BUILDER_PARENT,
        'sourceCommit': SOURCE, 'sourceOrigin': SOURCE_URL, 'sourceTree': TREE}, 'Original preflight identity differs')
    # Parse only literal identity assignments; never import or execute old code.
    literals = {}
    wanted = {'SOURCE': SOURCE, 'TREE': TREE, 'BUILDER_PARENT': BUILDER_PARENT, 'SOURCE_URL': SOURCE_URL,
              'PROJECT': 'LiveContainer.xcodeproj', 'SCHEME': 'LiveContainer', 'CONFIGURATION': 'Release',
              'BUNDLE_ID': BUNDLE_ID, 'DEPENDENCIES': DEPENDENCIES}
    for node in ast.parse(files['probe-inputs/run.py']).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    require(target.id not in literals, 'Duplicate original identity assignment')
                    literals[target.id] = ast.literal_eval(node.value)
    require(literals == wanted, 'Original probe source identity differs')
    return data


def verify_build(files, identity):
    require(digest(files['product-build-settings.json']) == BUILD_SETTINGS_SHA256, 'Original complete build-settings digest differs')
    require(digest(files['provenance/product-identity.json']) == PRODUCT_IDENTITY_SHA256, 'Original complete product-identity digest differs')
    load = lambda name: strict_json(files[name])
    feasibility = load('feasibility.json')
    require((feasibility['builderCommit'], feasibility['builderTree'], feasibility['sourceCommit'], feasibility['sourceTree']) ==
            (BUILDER, BUILDER_TREE, SOURCE, TREE), 'Original builder identity differs')
    require(feasibility['builderWorktreeStatus'] == '' and feasibility['retentionErrors'] == [], 'Unclean or incomplete historical build')
    require(feasibility['status'] == 'BLOCKED' and feasibility['stage'] == 'external-runner-generation' and
            all(feasibility[k] is False for k in ('nativeExecution', 'fullAppAcceptance', 'finalCompositionAcceptance')), 'Historical native scope differs')
    require(digest(files['workflow.yml']) == feasibility['workflowSHA256'] and
            files['workflow.yml'] == files['preflight-inputs/workflow/p2-host-settings-probe.yml'], 'Original workflow differs')
    simulator = load('simulator.json')
    require(simulator['newlyCreated'] is True and re.fullmatch('[0-9A-F-]{36}', simulator['udid']), 'Original simulator identity differs')
    base = ['xcodebuild', '-project', HOST_ROOT + '/LiveContainer.xcodeproj', '-scheme', 'LiveContainer',
            '-configuration', 'Release', '-sdk', 'iphonesimulator', '-destination', 'platform=iOS Simulator,id=' + simulator['udid'],
            '-derivedDataPath', DERIVED, '-disableAutomaticPackageResolution', '-onlyUsePackageVersionsFromResolvedFile']
    for number, suffix, stage in [('038', ['-showBuildSettings', '-json'], 'genuine-build-settings'),
                                  ('039', ['-resultBundlePath', ORIGINAL_EVIDENCE + '/product-build.xcresult', 'build'], 'genuine-simulator-build')]:
        command = load('commands/' + number + '.json')
        require(command['argv'] == base + suffix and command['returncode'] == 0 and
                command['ambientOverrideKeys'] == [] and command['cwd'] == ORIGINAL_CWD and
                command['developerDir'] == DEVELOPER_DIR and command['stage'] == stage, 'Historical build command/override differs: ' + number)
    require(files['commands/038.stdout'] == files['product-build-settings.json'], 'Retained build settings differ from command output')
    require(b'** BUILD SUCCEEDED **' in files['commands/039.stdout'], 'Historical build success output missing')
    settings = load('product-build-settings.json')
    require(isinstance(settings, list) and len(settings) == 1 and settings[0]['target'] == 'LiveContainer', 'Ambiguous original build target')
    settings = settings[0]['buildSettings']
    expected = {'TARGET_NAME': 'LiveContainer', 'PRODUCT_NAME': 'LiveContainer', 'CONFIGURATION': 'Release',
                'PRODUCT_BUNDLE_IDENTIFIER': BUNDLE_ID, 'WRAPPER_EXTENSION': 'app', 'MACH_O_TYPE': 'mh_execute',
                'EFFECTIVE_PLATFORM_NAME': '-iphonesimulator', 'FULL_PRODUCT_NAME': 'LiveContainer.app',
                'EXECUTABLE_NAME': 'LiveContainer', 'PROJECT_FILE_PATH': HOST_ROOT + '/LiveContainer.xcodeproj',
                'SRCROOT': HOST_ROOT, 'TARGET_BUILD_DIR': DERIVED + '/Build/Products/Release-iphonesimulator',
                'SDK_NAME': 'iphonesimulator26.4', 'CODE_SIGNING_ALLOWED': 'YES', 'CODE_SIGNING_REQUIRED': 'NO'}
    require(all(settings.get(k) == v for k, v in expected.items()), 'Historical product build settings differ')
    compiled = load('actual-host-compiled-resources.json')
    require(compiled['bundleIdentifier'] == BUNDLE_ID and compiled['productInventory'] == identity['files'], 'Historical compiled-resource product differs')
    for key in ('localization', 'permissions', 'exactPluralValues'):
        require(compiled[key]['status'] == 'passed' and compiled[key]['failures'] == [], 'Prior resource contract failed: ' + key)
        require(feasibility['actualHostCompiledResources'][key] == compiled[key], 'Historical resource summary differs')
    require(compiled['exactPluralValues']['checkedLanguageRows'] == 24 and
            compiled['exactPluralValues']['runtimePluralSelection'] is False, 'Historical plural proof scope differs')
    raw = load('provenance/product-raw-inventory.json')
    require(raw['files'] == identity['files'] and raw['path'] == expected['TARGET_BUILD_DIR'] + '/LiveContainer.app', 'Historical raw product inventory differs')
    return settings


def verify_app_zip(raw, identity):
    require(digest(raw) == APP_ZIP_SHA256, 'Original built-app ZIP digest differs')
    require(set(identity) == {'bundleIdentifier', 'executable', 'executableSHA256', 'files', 'infoPlistSHA256', 'machOPlatforms'}, 'Invalid product identity schema')
    require(identity['bundleIdentifier'] == BUNDLE_ID and identity['executable'] == 'LiveContainer' and
            identity['machOPlatforms'] == ['IOSSIMULATOR'], 'Original product identity differs')
    expected = identity['files']
    require(isinstance(expected, dict) and len(expected) == 123, 'Original product file count differs')
    payload = zip_payload(raw, allow_directories=True)
    prefix = 'LiveContainer.app/'
    rows = {}
    directories = {'LiveContainer.app'}
    for name, row in expected.items():
        safe_relative(name)
        require(set(row) == {'type', 'mode', 'sha256', 'bytes', 'executable'} and row['type'] == 'regular', 'Unexpected original product file type')
        require(type(row['mode']) is int and row['mode'] in (0o644, 0o755) and type(row['bytes']) is int and row['bytes'] >= 0 and
                type(row['executable']) is bool and row['executable'] == bool(row['mode'] & 0o111), 'Invalid original product mode/size')
        directories.update(prefix.rstrip('/') + '/' + str(parent) for parent in PurePosixPath(name).parents if str(parent) != '.')
    require(set(payload) == {prefix + name for name in expected} | directories, 'App ZIP contains extra or missing paths')
    for name, member in payload.items():
        if member['directory']:
            require(name in directories and member['mode'] == stat.S_IFDIR | 0o755, 'App ZIP directory mode/type differs')
            continue
        require(name.startswith(prefix), 'App ZIP escapes expected bundle')
        data, mode = member['data'], stat.S_IMODE(member['mode'])
        rows[name[len(prefix):]] = {'type': 'regular', 'mode': mode, 'sha256': digest(data),
                                    'bytes': len(data), 'executable': bool(mode & 0o111)}
    require(rows == expected, 'App ZIP bytes/types/full modes differ from original inventory')
    require(digest(payload[prefix + 'Info.plist']['data']) == identity['infoPlistSHA256'] and
            digest(payload[prefix + 'LiveContainer']['data']) == identity['executableSHA256'], 'Original Info.plist/executable hashes differ')
    info = plistlib.loads(payload[prefix + 'Info.plist']['data'])
    require(info.get('CFBundleIdentifier') == BUNDLE_ID and info.get('CFBundleExecutable') == 'LiveContainer' and
            info.get('CFBundlePackageType') == 'APPL' and info.get('CFBundleSupportedPlatforms') == ['iPhoneSimulator'], 'Original bundle plist identity differs')
    return payload


def safe_directory(path):
    path = Path(os.path.abspath(path))
    for candidate in [*reversed(path.parents), path]:
        if candidate.exists() or candidate.is_symlink():
            require(stat.S_ISDIR(candidate.lstat().st_mode), 'Output ancestor is not a real directory: ' + str(candidate))
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_new(path, raw, mode=0o644):
    safe_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    require(read_stable(path) == raw and stat.S_IMODE(path.lstat().st_mode) == mode, 'Retained bytes/mode differ')


def verify_restored_app(app, expected):
    require(stat.S_ISDIR(app.lstat().st_mode) and stat.S_IMODE(app.lstat().st_mode) == 0o755, 'Restored app root differs')
    actual = {}
    directories = set()
    for path in app.rglob('*'):
        name = path.relative_to(app).as_posix()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            require(stat.S_IMODE(metadata.st_mode) == 0o755, 'Restored directory mode differs')
            directories.add(name)
            continue
        require(stat.S_ISREG(metadata.st_mode), 'Restored app contains nonregular entry')
        raw = read_stable(path)
        mode = stat.S_IMODE(metadata.st_mode)
        actual[name] = {'type': 'regular', 'mode': mode, 'sha256': digest(raw), 'bytes': len(raw), 'executable': bool(mode & 0o111)}
    require(actual == expected, 'Restored app inventory differs')
    required_directories = {str(p) for name in expected for p in PurePosixPath(name).parents if str(p) != '.'}
    require(directories == required_directories, 'Restored app directory set differs')
    return actual


def authenticate_artifact(artifact: Path, work: Path, evidence: Path) -> dict:
    """Authenticate one pinned historical build, then safely restore its app.

    Creates fresh work/reused-host and evidence/reused-host-original. A receipt
    is created before input processing and records failures. Never restores the
    former installed app. The caller must perform fresh signature, installation,
    resource, native launch, and UI checks independently.
    """
    evidence = safe_directory(evidence)
    receipt_path = evidence / 'reused-host-authentication.json'
    receipt = {'schemaVersion': 1, 'status': 'IN_PROGRESS', 'stage': 'outer-artifact',
               'artifactID': ARTIFACT_ID, 'originalRunID': ORIGINAL_RUN_ID,
               'expectedArtifactSHA256': ARTIFACT_SHA256, 'nativeExecution': False,
               'freshSignatureVerification': False, 'freshInstallVerification': False,
               'fullAppAcceptance': False, 'finalCompositionAcceptance': False}
    # A fresh exclusively created receipt prevents silently overwriting an old
    # run. Updates use the same inode; symlink and identity changes are rejected.
    write_new(receipt_path, (json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode())
    receipt_stat = receipt_path.stat()
    def save():
        now = receipt_path.lstat()
        require(stat.S_ISREG(now.st_mode) and (now.st_dev, now.st_ino) == (receipt_stat.st_dev, receipt_stat.st_ino), 'Receipt path changed')
        fd = os.open(receipt_path, os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            opened = os.fstat(fd)
            require((opened.st_dev, opened.st_ino) == (receipt_stat.st_dev, receipt_stat.st_ino), 'Receipt changed before write')
            os.ftruncate(fd, 0)
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write((json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode())
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
    try:
        raw = read_stable(artifact)
        receipt['observedArtifactSHA256'] = digest(raw)
        require(digest(raw) == ARTIFACT_SHA256, 'Outer artifact SHA-256 differs')
        outer = zip_payload(raw, allow_directories=False)
        files, index = verify_index(outer)
        receipt.update(stage='historical-inputs', indexedPayloadCount=len(index), outerMemberCount=len(outer),
                       unindexedPortableLogSHA256=digest(outer['p2-host-probe-portable.log']['data']))
        save()
        verify_inputs(files)
        source, dependencies = verify_provenance(files)
        source_bytes = verify_source_tar(files['unchanged-source.tar'], source)
        identity = strict_json(files['provenance/product-identity.json'])
        settings = verify_build(files, identity)
        app_payload = verify_app_zip(files['actual-LiveContainer.app.zip'], identity)
        receipt.update(stage='restore-authenticated-product', originalBuild={
            'builderCommit': BUILDER, 'builderTree': BUILDER_TREE, 'builderAncestor': BUILDER_PARENT,
            'sourceCommit': SOURCE, 'sourceTree': TREE, 'sourceOrigin': SOURCE_URL,
            'sourceBlobCountVerified': len(source_bytes), 'sourceTarSHA256': SOURCE_TAR_SHA256,
            'appZIPSHA256': APP_ZIP_SHA256, 'productFileCount': len(identity['files']),
            'productSymlinkCount': 0, 'productInventorySHA256': digest(files['provenance/product-identity.json']),
            'buildSettingsSHA256': digest(files['product-build-settings.json']),
            'approvedInputsSHA256': INPUTS_SHA256,
            'dependencies': {name: {'commit': manifest['commit'], 'reconstructedTree': manifest['tree'],
                                   'manifestRows': len(manifest['files']), 'sourceBytesRechecked': False}
                             for name, manifest in dependencies.items()},
            'dependencyEvidenceScope': 'Authenticated historical build manifests and reconstructed Git trees; dependency checkout bytes were not retained and are not freshly reverified.',
            'historicalNativeStatus': 'BLOCKED at external-runner-generation; no historical native acceptance.'})
        save()
        # All provenance and payload validation above precedes app restoration.
        destination = safe_directory(work) / 'reused-host'
        destination.mkdir(mode=0o700, exist_ok=False)
        retained = evidence / 'reused-host-original'
        retained.mkdir(mode=0o700, exist_ok=False)
        selected = {'inputs.json', 'preflight-expected.json', 'feasibility.json', 'workflow.yml',
                    'actual-host-compiled-resources.json', 'product-build-settings.json',
                    'actual-LiveContainer.app.zip', 'unchanged-source.tar',
                    *[name for name in files if name.startswith('provenance/source-')],
                    *['provenance/' + name + '.json' for name in DEPENDENCIES],
                    'provenance/product-identity.json', 'provenance/product-raw-inventory.json',
                    *['commands/' + n + suffix for n in ('038', '039', '040', '041') for suffix in ('.json', '.stdout', '.stderr')]}
        for name in sorted(selected):
            write_new(retained / name, files[name])
        write_new(retained / 'evidence-sha256.json', outer[PREFIX + 'evidence-sha256.json']['data'])
        source_root = destination / 'authenticated-source'
        write_new(source_root / CATALOG, source_bytes[CATALOG])
        for name, member in sorted(app_payload.items(), key=lambda x: (len(PurePosixPath(x[0]).parts), x[0])):
            path = destination / name
            if member['directory']:
                path.mkdir(mode=0o755, exist_ok=False)
                path.chmod(0o755)
            else:
                write_new(path, member['data'], stat.S_IMODE(member['mode']))
        app = destination / 'LiveContainer.app'
        verify_restored_app(app, identity['files'])
        require(read_stable(source_root / CATALOG) == source_bytes[CATALOG], 'Restored catalog changed')
        require(digest(read_stable(artifact)) == ARTIFACT_SHA256, 'Outer artifact changed after authentication')
        receipt.update(status='AUTHENTICATED', stage='complete', restoredSourceFiles=[CATALOG],
                       appZIPSHA256=APP_ZIP_SHA256, retainedEvidence='reused-host-original',
                       scope='Offline artifact/source/build/product authentication only. Fresh native checks belong to the current probe.')
        save()
        return {'app': app, 'sourceRoot': source_root, 'identity': identity, 'settings': settings, 'provenance': receipt}
    except Exception as exc:
        receipt.update(status='FAILED', error=type(exc).__name__ + ': ' + str(exc))
        save()
        raise
