"""Compile-only evidence recorder. Never grants production admission or runs app products."""
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys

ROOT = Path.cwd()
S = ROOT / 'sources'
E = ROOT / 'evidence'
TMP = Path(os.environ.get('RUNNER_TEMP', '/tmp'))
from verify_candidate_graph import load_candidate_graph
OWNERS = {k: tuple(v) for k, v in load_candidate_graph()['owners'].items()}

LOCKS = {
    'SideStore': 'AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved',
    'SideStore/Dependencies/SideSign': 'Package.resolved',
}

def require(ok, why):
    if not ok:
        raise RuntimeError(why)


def git(root, *args):
    return subprocess.check_output(['git', '--no-replace-objects', '-C', str(root), '-c',
                                   'core.hooksPath=/dev/null', *args]).decode().strip()


def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write(name, data):
    E.mkdir(exist_ok=True)
    (E / name).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def tracked(root):
    result = {}
    for row in git(root, 'ls-files', '--stage').splitlines():
        meta, name = row.split('\t', 1)
        mode, blob, stage = meta.split()
        require(stage == '0', 'Unmerged source: ' + name)
        p = root / name
        if mode == '160000':
            result[name] = {'gitlink': blob}
        elif p.is_symlink():
            result[name] = {'mode': mode, 'symlink': os.readlink(p)}
        else:
            require(p.is_file(), 'Missing compiler input: ' + str(p))
            result[name] = {'mode': mode, 'sha256': sha(p)}
    return result


def repos():
    roots = {name: S / name for name in OWNERS}
    for owner in ('LiveContainer', 'SideStore'):
        folder = TMP / ('native-' + owner) / 'SourcePackages/checkouts'
        if folder.exists():
            for p in sorted(folder.iterdir()):
                if p.is_dir():
                    roots['resolved/' + owner + '/' + p.name] = p
    return roots


def generated_installer(*, embedded=False):
    """Load only the source-owned installer and its exact sibling recipe module."""
    import importlib.util
    folder = Path(__file__).resolve().parents[1] / 'issue53-lts'
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    shared = load('_issue53_snapshot_build', folder / 'build.py')
    previous = sys.modules.get('build')
    sys.modules['build'] = shared
    previous_installer = sys.modules.get('install_ios')
    try:
        installer = load('_issue53_snapshot_installer', folder / 'install_ios.py')
        if not embedded: return installer
        sys.modules['install_ios'] = installer
        return load('_issue53_snapshot_embedded', folder / 'embedded.py')
    finally:
        if previous is None: sys.modules.pop('build', None)
        else: sys.modules['build'] = previous
        if previous_installer is None: sys.modules.pop('install_ios', None)
        else: sys.modules['install_ios'] = previous_installer


def generated_inventory(root):
    import stat
    require(root.is_dir() and not root.is_symlink(), 'Missing/linked generated dependency')
    rows = {}
    for path in [root, *sorted(root.rglob('*'))]:
        mode = path.lstat().st_mode
        require(not stat.S_ISLNK(mode), 'Linked generated compiler input')
        require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), 'Nonregular generated compiler input')
        row = {'mode': stat.S_IMODE(mode), 'kind': 'directory' if stat.S_ISDIR(mode) else 'file'}
        if stat.S_ISREG(mode): row['sha256'] = sha(path)
        rows[path.relative_to(root).as_posix()] = row
    return rows


def require_generated_stage(stage, observed, *, schema, consumer_commit, diagnostic, message):
    identity = {'schema': schema, 'workflow_commit': os.environ['GITHUB_SHA'],
                'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
                'consumer_commit': consumer_commit}
    identity_changes = {key: {'expected': value, 'observed': stage.get(key)}
                        for key, value in identity.items() if stage.get(key) != value}
    expected = stage.get('generated')
    if identity_changes or expected != observed:
        before = expected.get('files', {}) if isinstance(expected, dict) else {}
        after = observed.get('files', {})
        changes = {name: {'expected': before.get(name), 'observed': after.get(name)}
                   for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)}
        # Preserve the actual evidence before rejecting it. No normalization,
        # exclusion or resealing is permitted by this diagnostic path.
        write(diagnostic, {'schema': 'generated-stage-drift-diagnostic-v1',
              'status': 'REJECTED_NOT_RESEALED', 'identity_changes': identity_changes,
              'file_changes': changes, 'expected_generated': expected,
              'observed_generated': observed, 'accepted': False})
        require(False, message)


def check_sealed_generated_files_before_validation(root, *, filename, schema, consumer_commit, diagnostic, message):
    path = E / filename
    require(path.is_file() and not path.is_symlink(), 'Generated stage receipt missing or linked')
    stage = json.loads(path.read_text())
    expected = stage.get('generated')
    # This is diagnostic preflight only. The caller must still validate the
    # complete adapter/source policy and compare newly derived metadata below.
    # It makes a mode/content drift visible even when the adapter rejects first.
    observed = {**expected, 'files': generated_inventory(root)} if isinstance(expected, dict) else {}
    require_generated_stage(stage, observed, schema=schema, consumer_commit=consumer_commit,
                            diagnostic=diagnostic, message=message)


def openssl_size_admission_module():
    """Load the source-owned stdlib admission checker without ambient build modules."""
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'issue55-openssl-size/admission.py'
    require(path.is_file() and not path.is_symlink(), 'OpenSSL size admission helper missing or linked')
    spec = importlib.util.spec_from_file_location('_same_run_openssl_size_admission', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def openssl_size_qualification(expected_ios_receipt=None):
    """Revalidate same-run host qualification and exact measured iOS input, not device proof."""
    identity = {'workflow_commit': os.environ['GITHUB_SHA'], 'run_id': os.environ['GITHUB_RUN_ID'],
                'run_attempt': os.environ['GITHUB_RUN_ATTEMPT']}
    return openssl_size_admission_module().verify_measurement(
        E, ROOT / 'lts-host-build', ROOT / 'lts-ios-build', identity,
        expected_ios_receipt=expected_ios_receipt)


def generated_openssl_snapshot(*, required=False, sealed=False):
    consumer = S / 'LiveContainer'
    root = consumer / 'GeneratedDependencies/OpenSSL35'
    require(not root.is_symlink() and not root.parent.is_symlink(), 'Linked generated dependency root')
    if not root.exists():
        require(not required, 'Generated OpenSSL compiler inputs have not been staged')
        return {'present': False}
    if sealed:
        check_sealed_generated_files_before_validation(root, filename='generated-openssl-stage.json',
            schema='same-run-generated-openssl-input-v1', consumer_commit=OWNERS['LiveContainer'][1],
            diagnostic='generated-openssl-stage-drift.json', message='Generated OpenSSL stage identity/bytes drifted')
    generated, receipt = generated_installer().validate_installed(consumer)
    observed = {'present': True, 'relative_path': 'LiveContainer/GeneratedDependencies/OpenSSL35',
                'source': receipt['source'], 'recipe_sha256': receipt['recipe_sha256'],
                'shared_recipe_sha256': receipt['shared_recipe_sha256'],
                'size_qualification': openssl_size_qualification(receipt), 'files': generated_inventory(generated)}
    if sealed:
        path = E / 'generated-openssl-stage.json'
        require(path.is_file() and not path.is_symlink(), 'Generated OpenSSL stage receipt missing')
        stage = json.loads(path.read_text())
        require_generated_stage(stage, observed, schema='same-run-generated-openssl-input-v1',
                                consumer_commit=OWNERS['LiveContainer'][1],
                                diagnostic='generated-openssl-stage-drift.json',
                                message='Generated OpenSSL stage identity/bytes drifted')
        observed = {**observed, 'stage_receipt_sha256': sha(path)}
    return observed


def stage_openssl(built):
    openssl_size_qualification()  # Fail before any consumer mutation.
    require(Path(built).resolve(strict=True) == (ROOT / 'lts-ios-build').resolve(strict=True),
            'Staging requires the exact same-run qualified iOS build directory')
    consumer = S / 'LiveContainer'
    before = tracked(consumer)
    require(git(consumer, 'rev-parse', 'HEAD') == OWNERS['LiveContainer'][1], 'Consumer pin differs before staging')
    installer = generated_installer()
    installer.install(Path(built), consumer)
    require(tracked(consumer) == before, 'Installer modified tracked consumer source/gitlink')
    observed = generated_openssl_snapshot(required=True)
    write('generated-openssl-stage.json', {'schema': 'same-run-generated-openssl-input-v1',
          'workflow_commit': os.environ['GITHUB_SHA'], 'run_id': os.environ['GITHUB_RUN_ID'],
          'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'], 'consumer_commit': OWNERS['LiveContainer'][1],
          'generated': observed, 'old_gitlink_overwritten': False, 'target_executed': False})
    return generated_openssl_snapshot(required=True, sealed=True)


def generated_embedded_snapshot(*, required=False, sealed=False):
    owner = 'SideStore/Dependencies/minimuxer'
    consumer = S / owner
    overlay = consumer / 'GeneratedDependencies/RemotePairingKit'
    require(not overlay.is_symlink() and not overlay.parent.is_symlink(), 'Linked embedded generated dependency root')
    if not overlay.exists():
        require(not required, 'Embedded generated OpenSSL compiler inputs have not been staged')
        return {'present': False}
    if sealed:
        check_sealed_generated_files_before_validation(overlay, filename='generated-embedded-openssl-stage.json',
            schema='same-run-generated-embedded-openssl-input-v1', consumer_commit=OWNERS[owner][1],
            diagnostic='generated-embedded-openssl-stage-drift.json', message='Embedded generated stage identity/bytes drifted')
    generated, receipt = generated_installer(embedded=True).validate_embedded(consumer)
    nested = receipt['generated_build_receipt']
    observed = {'present': True, 'relative_path': owner+'/GeneratedDependencies/RemotePairingKit',
                'source_repository': receipt['source_repository'], 'source_commit': receipt['source_commit'],
                'source_tree': receipt['source_tree'], 'adapter_sha256': receipt['adapter_sha256'],
                'source': nested['source'], 'recipe_sha256': nested['recipe_sha256'],
                'shared_recipe_sha256': nested['shared_recipe_sha256'],
                'size_qualification': openssl_size_qualification(nested), 'files': generated_inventory(overlay)}
    if sealed:
        path = E / 'generated-embedded-openssl-stage.json'
        require(path.is_file() and not path.is_symlink(), 'Embedded generated stage receipt missing')
        stage = json.loads(path.read_text())
        require_generated_stage(stage, observed, schema='same-run-generated-embedded-openssl-input-v1',
                                consumer_commit=OWNERS[owner][1],
                                diagnostic='generated-embedded-openssl-stage-drift.json',
                                message='Embedded generated stage identity/bytes drifted')
        observed = {**observed, 'stage_receipt_sha256': sha(path)}
    return observed


def stage_embedded_openssl(built, upstream):
    openssl_size_qualification()  # Fail before any consumer mutation.
    require(Path(built).resolve(strict=True) == (ROOT / 'lts-ios-build').resolve(strict=True),
            'Staging requires the exact same-run qualified iOS build directory')
    owner = 'SideStore/Dependencies/minimuxer'
    consumer = S / owner
    before = tracked(consumer)
    require(git(consumer, 'rev-parse', 'HEAD') == OWNERS[owner][1], 'Embedded consumer pin differs before staging')
    generated_installer(embedded=True).stage(Path(built), Path(upstream), consumer)
    require(tracked(consumer) == before, 'Embedded installer modified tracked consumer source')
    observed = generated_embedded_snapshot(required=True)
    write('generated-embedded-openssl-stage.json', {'schema': 'same-run-generated-embedded-openssl-input-v1',
          'workflow_commit': os.environ['GITHUB_SHA'], 'run_id': os.environ['GITHUB_RUN_ID'],
          'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'], 'consumer_commit': OWNERS[owner][1],
          'generated': observed, 'upstream_source_modified_except_manifest': False,
          'host_library_deduplicated': False, 'target_executed': False})
    return generated_embedded_snapshot(required=True, sealed=True)


def generated_device_framework(root):
    import plistlib
    xc = root / 'OpenSSL.xcframework'
    info = plistlib.loads((xc / 'Info.plist').read_bytes())
    libraries = info.get('AvailableLibraries', [])
    require(len(libraries) == 2, 'Generated XCFramework slice count differs')
    closure = {(row.get('SupportedPlatform'), row.get('SupportedPlatformVariant'),
                tuple(sorted(row.get('SupportedArchitectures', [])))) for row in libraries}
    require(closure == {('ios', None, ('arm64',)), ('ios', 'simulator', ('arm64','x86_64'))},
            'Generated XCFramework platform/architecture closure differs')
    row = next(row for row in libraries if row.get('SupportedPlatformVariant') is None)
    name = row['LibraryIdentifier']
    require(re.fullmatch(r'[A-Za-z0-9_.-]+', name) and name not in ('.','..') and
            row['LibraryPath'] == 'OpenSSL.framework', 'Unsafe generated framework selector')
    framework = xc / name / 'OpenSSL.framework'
    require(framework.is_dir() and not framework.is_symlink(), 'Missing/linked generated device framework')
    return framework


def generated_ios_macho(path, *, require_minimum=True, allowed_kinds=(6,)):
    import struct
    data = path.read_bytes()
    require(len(data) >= 32, 'Generated payload is not Mach-O')
    magic, cpu, subtype, kind, count, size, flags, reserved = struct.unpack_from('<8I', data)
    require(magic == 0xfeedfacf and cpu == 0x100000c and kind in allowed_kinds and count <= 1024 and
            size <= len(data)-32, 'Generated payload architecture/type differs')
    end, position, platforms, identities = 32+size, 32, [], []
    install_names, loaded_dylibs = [], []
    for _ in range(count):
        require(position+8 <= end, 'Truncated generated Mach-O command')
        cmd, length = struct.unpack_from('<2I', data, position)
        require(length >= 8 and length % 8 == 0 and length <= end-position, 'Invalid generated Mach-O command')
        if cmd == 0x32:
            require(length >= 24, 'Truncated generated platform command')
            platform, minimum, sdk, tools = struct.unpack_from('<4I', data, position+8)
            require(length == 24+tools*8 and platform == 2 and (not require_minimum or minimum == 0x000f0000),
                    'Generated payload must target iOS device 15.0')
            platforms.append({'platform': platform, 'minimum': minimum, 'sdk': sdk})
        if cmd == 0x1b:
            require(length == 24, 'Invalid generated UUID command')
            identities.append(data[position+8:position+24].hex())
        if cmd in (0xd, 0xc, 0x80000018, 0x8000001f):
            require(length >= 24, 'Truncated generated dylib command')
            name_offset = struct.unpack_from('<I', data, position+8)[0]
            require(24 <= name_offset < length, 'Invalid generated dylib name offset')
            name_end = data.find(b'\0', position+name_offset, position+length)
            require(name_end >= position+name_offset, 'Unterminated generated dylib name')
            name = data[position+name_offset:name_end].decode('utf-8')
            (install_names if cmd == 0xd else loaded_dylibs).append(name)
        position += length
    require(position == end and len(platforms) == len(identities) == 1,
            'Generated platform/UUID command closure differs')
    return {'architecture': 'arm64', 'uuid': identities[0], 'install_names': install_names,
            'loaded_dylibs': loaded_dylibs, **platforms[0]}


def verify_generated_openssl_payload(*, embedded=False):
    current = (generated_embedded_snapshot if embedded else generated_openssl_snapshot)(required=True, sealed=True)
    key = 'generated-embedded-openssl' if embedded else 'generated-openssl'
    owner = 'SideStore' if embedded else 'LiveContainer'
    for label in ('compiler-inputs', 'after-build'):
        recorded = json.loads((E / (label+'.json')).read_text())
        require(recorded.get(key) == current, 'Generated compiler input snapshot drift: '+label)
    root = S / current['relative_path']
    if embedded: root = root / 'GeneratedDependencies/OpenSSL35'
    framework = generated_device_framework(root)
    generated = framework / 'OpenSSL'
    app = TMP / ('native-'+owner) / 'Build/Products/Release-iphoneos' / (owner+'.app')
    product = app / 'Frameworks/OpenSSL.framework'
    require(app.is_dir() and not app.is_symlink() and product.is_dir() and not product.is_symlink() and
            product.resolve(strict=True).is_relative_to(app.resolve(strict=True)),
            'Generated framework missing/outside native product')
    payload = product / 'OpenSSL'
    require(payload.is_file() and not payload.is_symlink(), 'Generated native payload missing/linked')
    require(sha(payload) == sha(generated), 'Native OpenSSL payload differs from generated device input')
    identity = generated_ios_macho(generated)
    require(generated_ios_macho(payload) == identity, 'Native generated OpenSSL platform/UUID differs')
    expected_install = '@rpath/OpenSSL.framework/OpenSSL'
    require(identity['install_names'] == [expected_install], 'Generated OpenSSL install name differs')
    consumer = app / 'SideStore' if embedded else product.parent / 'ZSign.dylib'
    require(consumer.is_file() and not consumer.is_symlink() and
            consumer.resolve(strict=True).is_relative_to(app.resolve(strict=True)),
            'Native generated-library consumer absent/linked/outside product')
    linked = generated_ios_macho(consumer, require_minimum=False, allowed_kinds=(2,) if embedded else (6,))['loaded_dylibs']
    require([name for name in linked if '/OpenSSL.framework/' in name] == [expected_install],
            'Native consumer does not link the generated OpenSSL install name')
    require((product/'Info.plist').is_file() and not (product/'Info.plist').is_symlink() and
            sha(product/'Info.plist') == sha(framework/'Info.plist'), 'Native generated framework metadata differs')
    report = {'schema': 'same-run-generated-openssl-payload-v1', 'status': 'EXACT_GENERATED_DEVICE_BYTES_LINKED_NOT_RUNTIME_PROVEN',
              'consumer': owner, 'source': current['source'], 'generated_binary_sha256': sha(generated),
              'native_binary_sha256': sha(payload), 'identity': identity,
              'native_consumer_sha256': sha(consumer), 'native_consumer_dependency': expected_install,
              'generated_stage_receipt_sha256': current['stage_receipt_sha256'],
              'size_qualification': current['size_qualification'],
              'generated_inputs_unchanged': True, 'historical_gitlink_used_as_payload': False,
              'target_executed': False, 'production_admission': False}
    write(key+'-payload.json', report)
    return report


def snapshot(label):
    result = {}
    for name, root in repos().items():
        head = git(root, 'rev-parse', 'HEAD')
        if name in OWNERS:
            require(head == OWNERS[name][1], 'Owner pin drift: ' + name)
        result[name] = {'commit': head, 'tree': git(root, 'rev-parse', 'HEAD^{tree}'),
                        'origin': git(root, 'remote', 'get-url', 'origin'), 'files': tracked(root)}
    # Include the staged, generated local binary/header inputs, not only tracked source.
    local = S / 'SideStore/Dependencies/minimuxer/DeviceGateway/LocalBinary/IDevice.xcframework'
    result['local-framework'] = {p.relative_to(local).as_posix(): sha(p)
                                 for p in sorted(local.rglob('*')) if p.is_file()}
    for owner in ('LiveContainer', 'SideStore'):
        folder = TMP / ('native-' + owner) / 'SourcePackages/artifacts'
        result['binary-artifacts/' + owner] = {p.relative_to(folder).as_posix(): sha(p)
            for p in sorted(folder.rglob('*')) if p.is_file()}
    result['generated-openssl'] = generated_openssl_snapshot(
        required=label in ('compiler-inputs', 'after-build'), sealed=label in ('compiler-inputs', 'after-build'))
    result['generated-embedded-openssl'] = generated_embedded_snapshot(
        required=label in ('compiler-inputs', 'after-build'), sealed=label in ('compiler-inputs', 'after-build'))
    write(label + '.json', result)


def acquire():
    # Retain requested identity even when cloning or closure validation fails.
    write('requested-graph.json', OWNERS)
    # Refuse incomplete reviewed pins before any acquisition/network operation.
    require(all(re.fullmatch(r'[0-9a-f]{40}', commit) for _, commit in OWNERS.values()),
            'Reviewed graph has missing or invalid exact commit pins')
    for name, (repo, commit) in OWNERS.items():
        root = S / name
        require(not root.exists() or (root.is_dir() and not list(root.iterdir())), 'Nonfresh checkout: ' + name)
        root.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'clone', '--no-checkout',
                        'https://github.com/' + repo + '.git', str(root)], check=True)
        git(root, 'fetch', '--no-tags', '--no-recurse-submodules', 'origin', commit)
        git(root, 'checkout', '--detach', commit)
        require(git(root, 'rev-parse', 'HEAD') == commit, 'Acquisition identity mismatch')
    # No recursive submodule discovery or branch substitution. Enforce complete gitlink closure.
    for name in OWNERS:
        for row in git(S / name, 'ls-files', '--stage').splitlines():
            meta, child = row.split('\t', 1)
            mode, oid, stage = meta.split()
            if mode == '160000':
                key = name + '/' + child
                require(key in OWNERS and OWNERS[key][1] == oid, 'Unexpected child gitlink: ' + key)
    project = (S / 'LiveContainer/LiveContainer.xcodeproj/project.pbxproj').read_text()
    require('XCRemoteSwiftPackageReference' not in project and
            re.search(r'packageReferences\s*=\s*\(\s*\);', project),
            'LiveContainer unexpectedly has remote package references')
    for owner, path in LOCKS.items():
        data = (S / owner / path).read_bytes()
        (E / (owner.replace('/', '_') + '.Package.resolved.before')).write_bytes(data)
    write('requested-graph.json', OWNERS)


def seal_resolution():
    before_raw = (E / 'SideStore.Package.resolved.before').read_bytes()
    path = S / 'SideStore' / LOCKS['SideStore']
    after_raw = path.read_bytes()
    (E / 'SideStore.Package.resolved.observed').write_bytes(after_raw)
    before, after = json.loads(before_raw), json.loads(after_raw)
    require(set(before) == set(after), 'Resolver changed top-level app-lock schema')
    for key in set(before) - {'pins', 'originHash'}:
        require(before[key] == after[key], 'Resolver changed app-lock field: ' + key)
    index = lambda value: {pin['identity']: pin for pin in value['pins']}
    old, new = index(before), index(after)
    require(len(old) == len(before['pins']) and len(new) == len(after['pins']), 'Duplicate package identities')
    require(set(old) == set(new), 'Resolver changed package identity closure')
    require(old['anisettekit']['state'] == {'revision': OWNERS['AnisetteKit'][1]},
            'Unexpected starting AK lock: source must match the exact candidate graph')
    require(new == old, 'Resolver changed pins beyond the exact source lock')
    if 'originHash' in after:
        require(isinstance(after['originHash'], str) and re.fullmatch('[0-9a-f]{64}', after['originHash']),
                'Invalid observed resolver originHash')
    (E / 'SideStore.Package.resolved.after').write_bytes(after_raw)
    write('app-lock-transition.json', {'status': 'OBSERVED_RESOLVER_METADATA_ONLY' if before_raw != after_raw else 'UNCHANGED_PINNED_APP_LOCK',
          'source_commit': OWNERS['SideStore'][1],
          'working_checkout_has_uncommitted_app_lock': before_raw != after_raw,
          'before_sha256': hashlib.sha256(before_raw).hexdigest(),
          'after_sha256': hashlib.sha256(after_raw).hexdigest(),
          'before_originHash': before.get('originHash'), 'after_originHash': after.get('originHash'),
          'pins_changed': [], 'only_allowed_metadata_change': 'originHash',
          'new_source_commit_published': False})


def https_origin(value):
    from urllib.parse import urlsplit
    parsed = urlsplit(value)
    require(parsed.scheme == 'https' and parsed.netloc.lower() == 'github.com' and
            not parsed.query and not parsed.fragment and
            re.fullmatch(r'/[^/]+/[^/]+/?', parsed.path), 'Unexpected upstream origin format')
    return 'https://github.com' + parsed.path.rstrip('/').removesuffix('.git').lower()


def contained_directory(path, root):
    path, root = Path(path), Path(root).resolve(strict=True)
    require(path.is_absolute() and '..' not in path.parts, 'Noncanonical local repository path')
    # Reject symlink routing below the trusted canonical root before invoking Git.
    require(path.is_relative_to(root), 'Local repository path escapes resolver root')
    current = root
    for component in path.relative_to(root).parts:
        current = current / component
        require(not current.is_symlink(), 'Symlink in local repository path')
    require(path.is_dir() and path.resolve(strict=True).is_relative_to(root), 'Missing/outside repository')
    return path.resolve(strict=True)


def verify_origin_chain(checkout, cache_root, expected_url, revision, rows=None):
    from urllib.parse import urlsplit, unquote
    expected = https_origin(expected_url)
    checkout = Path(checkout)
    # Caller has checked checkout ancestry against canonical RUNNER_TEMP. Validate
    # its sibling cache before resolving it, so a cache symlink cannot redefine trust.
    require(Path(cache_root) == checkout.parent.parent / 'repositories', 'Unexpected cache root')
    cache_root = contained_directory(cache_root, checkout.parent.parent)
    current = contained_directory(checkout, checkout.parent)
    checkout_tree = git(current, 'rev-parse', revision + '^{tree}')
    visited = set()
    rows = [] if rows is None else rows
    for _ in range(4):
        require(current not in visited, 'Origin chain cycle')
        visited.add(current)
        gitdir = Path(git(current, 'rev-parse', '--absolute-git-dir'))
        contained_directory(gitdir, current)
        require(git(current, 'cat-file', '-t', revision) == 'commit' and
                git(current, 'rev-parse', revision + '^{tree}') == checkout_tree,
                'Origin cache commit/tree differs')
        origins = git(current, 'remote', 'get-url', '--all', 'origin').splitlines()
        require(len(origins) == 1 and 0 < len(origins[0]) < 4096, 'Ambiguous/missing origin')
        origin = origins[0]
        parsed = urlsplit(origin)
        require(not parsed.username and not parsed.password, 'Credential-bearing origin rejected')
        rows.append({'repository': str(current), 'origin': origin, 'tree': checkout_tree})
        if parsed.scheme == 'https':
            require(https_origin(origin) == expected, 'Resolved upstream differs from exact lock')
            return rows
        if parsed.scheme == 'file':
            require(not parsed.netloc and not parsed.query and not parsed.fragment,
                    'Nonlocal file URL rejected')
            target = Path(unquote(parsed.path))
        else:
            require(not parsed.scheme and not parsed.netloc and not parsed.query and not parsed.fragment,
                    'Unsupported local origin')
            target = Path(origin)
        current = contained_directory(target, cache_root)
        require(current != cache_root, 'Cache root is not a package repository')
    raise RuntimeError('Origin chain exceeds four repositories')


def verify_workspace_row(document, identity, checkout, pin):
    require(document.get('version') in (6, 7), 'Unsupported resolver state version')
    matches = [row for row in document['object']['dependencies']
               if row.get('packageRef', {}).get('identity') == identity]
    require(len(matches) == 1, 'Missing/duplicate resolver package row')
    row = matches[0]
    ref, state = row['packageRef'], row['state']
    require(ref.get('kind') == 'remoteSourceControl' and
            https_origin(ref['location']) == https_origin(pin['location']) and
            state.get('name') == 'sourceControlCheckout' and
            state.get('checkoutState', {}).get('revision') == pin['state']['revision'] and
            row.get('subpath') == checkout.name and row.get('basedOn') is None,
            'Resolver state does not bind exact lock and checkout')


def capture_resolution_inputs(folder):
    # Retain observations before any origin assertion; failure is not success evidence.
    from urllib.parse import urlsplit
    state = folder.parent / 'workspace-state.json'
    raw = state.read_bytes() if state.is_file() and not state.is_symlink() else None
    if raw is not None:
        (E / 'SideStore-workspace-state.json').write_bytes(raw)
    observations = {}
    for checkout in sorted(folder.iterdir()):
        if not checkout.is_dir():
            continue
        try:
            contained_directory(checkout, folder)
            origin = git(checkout, 'remote', 'get-url', '--all', 'origin')
            parsed = urlsplit(origin)
            observations[checkout.name] = {'origin': '[credential-bearing origin rejected]' if
                parsed.username or parsed.password else origin,
                'commit': git(checkout, 'rev-parse', 'HEAD')}
        except (RuntimeError, subprocess.CalledProcessError) as error:
            observations[checkout.name] = {'observation_error': type(error).__name__}
    write('resolved-origin-observations.json', observations)
    require(raw is not None, 'Missing/linked resolver state')
    return json.loads(raw)


def verify_resolved():
    lc_checkouts = TMP / 'native-LiveContainer/SourcePackages/checkouts'
    require(not lc_checkouts.exists() or not any(lc_checkouts.iterdir()),
            'Unexpected LiveContainer resolved checkout')
    for owner, path in LOCKS.items():
        current = (S / owner / path).read_bytes()
        require(current == (E / (owner.replace('/', '_') + '.Package.resolved.' + ('after' if owner == 'SideStore' else 'before'))).read_bytes(),
                'Frozen resolver lock changed: ' + owner)
    app_lock = json.loads((S / 'SideStore' / LOCKS['SideStore']).read_text())
    expected = {pin['identity'].lower(): pin for pin in app_lock['pins']}
    observed = {}
    folder = TMP / 'native-SideStore/SourcePackages/checkouts'
    contained_directory(folder, TMP.resolve(strict=True))
    state_document = capture_resolution_inputs(folder)
    origin_chains = {}
    for checkout in sorted(folder.iterdir()):
        if not checkout.is_dir():
            continue
        contained_directory(checkout, folder)
        identity = checkout.name.lower()
        require(identity in expected, 'Unexpected resolved checkout: ' + identity)
        pin = expected[identity]
        require(git(checkout, 'rev-parse', 'HEAD') == pin['state']['revision'], 'Resolved revision differs: ' + identity)
        verify_workspace_row(state_document, identity, checkout, pin)
        chain_observations = []
        try:
            origin_chains[identity] = verify_origin_chain(checkout, folder.parent / 'repositories',
                                                        pin['location'], pin['state']['revision'], chain_observations)
        except Exception as error:
            write('resolved-origin-chain-failure.json', {'identity': identity, 'error': str(error),
                                                       'observed_chain': chain_observations, 'completed_chains': origin_chains})
            raise
        write('resolved-origin-chains.json', origin_chains)
        require(not git(checkout, 'status', '--porcelain', '--untracked-files=no'), 'Dirty resolved source: ' + identity)
        observed[identity] = pin['state']['revision']
    require(set(observed) == set(expected), 'App lock/checkouts closure differs')
    write('actual-resolved-pins.json', observed)
    # Verify the actually compiled AK checkout, not merely a separately acquired matching repository.
    ak = TMP / 'native-SideStore/SourcePackages/checkouts/AnisetteKit'
    require(ak.is_dir(), 'Missing actual app-resolved AnisetteKit')
    require(git(ak, 'rev-parse', 'HEAD') == OWNERS['AnisetteKit'][1], 'Resolved AK revision mismatch')
    require(tracked(ak) == tracked(S / 'AnisetteKit'), 'Resolved AK input bytes mismatch')
    for owner in ('LiveContainer', 'SideStore'):
        state = TMP / ('native-' + owner) / 'SourcePackages/workspace-state.json'
        if state.exists():
            # Retain actual resolver provenance; review remains required, never infer native readiness.
            (E / (owner + '-workspace-state.json')).write_bytes(state.read_bytes())
    write('resolution-check.json', {'frozen_app_lock_unchanged': True, 'sidesign_committed_lock_unchanged': True,
          'actual_anisette_commit': git(ak, 'rev-parse', 'HEAD'),
          'actual_anisette_tree': git(ak, 'rev-parse', 'HEAD^{tree}')})


def verify_vpn_transport_evidence(expected=None):
    if expected is None:
        from upstream_candidate_projection import current_spec
        current, projection = current_spec(S)
        expected = current['vpn_transport_regression']
        write('upstream-runtime-projection.json', projection)
    root = E / 'vpn-transport-recovery'
    def regular(path):
        require(path.is_file() and not path.is_symlink(), 'Missing/linked VPN transport evidence: ' + str(path))
        return path
    require(regular(E / 'vpn-transport-recovery.exit-code.txt').read_text().strip() == '0',
            'VPN transport regression command did not pass')
    source_hashes = {name: sha(regular(S / name)) for name in expected['source_inputs']}
    require(source_hashes == expected['source_inputs'], 'VPN transport source hashes differ')
    rows = json.loads(regular(root / 'inputs.json').read_text())
    require(rows == {name: {'path': str((S / row['path']).resolve()), 'sha256': row['sha256']}
                     for name, row in expected['input_rows'].items()}, 'VPN transport exact input roles differ')
    require(sha(regular(root / 'fixture.swift')) == expected['generated_sha256'], 'VPN transport generated Swift differs')
    runtime = regular(root / 'runtime.log').read_text()
    marker = expected['runtime_marker']
    require(runtime.splitlines().count(marker) == 1, 'VPN transport exact runtime marker missing or duplicate')
    log = regular(E / 'vpn-transport-recovery.log').read_text()
    require(len(re.findall(r'^Ran 4 tests in [0-9.]+s$', log, re.M)) == 1 and
            log.splitlines().count('OK') == 1 and 'skipped=' not in log and
            log.splitlines().count(marker) == 1, 'VPN transport tests failed, skipped, or had wrong totals')
    report = {'schema': 'vpn-transport-regression-evidence-v1',
              'status': 'SOURCE_OWNED_FAKE_SWIFT_PASS_NOT_DEVICE_VALIDATION',
              'test_count': 4, 'transport_case_count': 144, 'source_inputs_sha256': source_hashes,
              'generated_sha256': expected['generated_sha256'], 'runtime_marker': marker,
              'evidence_sha256': {name: sha(regular(root / name)) for name in
                                 ('fixture.swift', 'inputs.json', 'compiler.log', 'runtime.log')},
              'target_native_executed': False, 'network_executed': False, 'authentication_executed': False}
    write('vpn-transport-recovery.json', report)
    return report


def working_app_lock_differs():
    transition = json.loads((E / 'app-lock-transition.json').read_text())
    observed = transition.get('working_checkout_has_uncommitted_app_lock')
    require(type(observed) is bool, 'App lock transition must report a strict boolean difference')
    return observed


def receipt():
    if os.environ.get('GITHUB_REF') == 'refs/heads/validation/cold-vpn' or (E / 'multitask-launch-tests.json').exists():
        from startup_upgrade_receipt import verify_local as verify_startup_upgrade
        verify_startup_upgrade(E / 'startup-upgrade-tests.json', S, ROOT / 'schema-sources', E,
            {k:list(v) for k,v in OWNERS.items()},
            {k: os.environ[v] for k,v in [('workflow_commit','GITHUB_SHA'),('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT')]})
        from multitask_receipt import verify_local as verify_multitask
        verify_multitask(E / 'multitask-launch-tests.json', S, E / 'multitask-launch', {k:list(v) for k,v in OWNERS.items()},
                        {k: os.environ[v] for k,v in [('workflow_commit','GITHUB_SHA'),('run_id','GITHUB_RUN_ID'),('run_attempt','GITHUB_RUN_ATTEMPT')]})
    from verify_russian_localization import verify_receipt as verify_russian_receipt
    russian_report = E / 'russian-localization/receipt.json'
    verify_russian_receipt(russian_report, S / 'LiveContainer')
    from run_owned_oda import verify_receipt as verify_owned_oda_receipt
    verify_owned_oda_receipt(E / 'owned-oda/receipt.json')
    from run_issue_tests import verify_issue_receipt, verify_native_receipt
    verify_issue_receipt(E / 'issue-tests.json')
    verify_native_receipt(E / 'idevice-issue-tests.json')
    verify_vpn_transport_evidence()
    from verify_comprehensive_diagnostics import verify_receipt
    diagnostic_report = E / 'comprehensive-diagnostics/report.json'
    from project_candidate_contracts import contracts
    contracts(S, E / 'candidate-contracts', verify=True)
    verify_receipt(diagnostic_report, E / 'candidate-contracts/comprehensive-diagnostics-contract.json')
    from verify_setup_state_recovery import verify_receipt as verify_setup_receipt
    setup_report = E / 'setup-state-recovery/report.json'
    verify_setup_receipt(setup_report, E / 'candidate-contracts/setup-state-recovery-contract.json')
    outputs = {}
    for owner in ('LiveContainer', 'SideStore'):
        product = TMP / ('native-' + owner) / 'Build/Products/Release-iphoneos' / (owner + '.app')
        require((product / owner).is_file(), 'Expected native executable absent: ' + owner)
        require((E / (owner + '-build.exit-code.txt')).read_text().strip() == '0', 'Build did not pass')
        outputs[owner] = {p.relative_to(product).as_posix(): sha(p)
                          for p in sorted(product.rglob('*')) if p.is_file()}
    verify_generated_openssl_payload()
    verify_generated_openssl_payload(embedded=True)
    write('product-hashes.json', outputs)
    write('receipt.json', {'schema_version': 1, 'status': 'COMPILE_ONLY_PASS_REQUIRES_REVIEW',
          'workflow_commit': os.environ['GITHUB_SHA'], 'run_id': os.environ['GITHUB_RUN_ID'],
          'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
          'run_url': 'https://github.com/' + os.environ['GITHUB_REPOSITORY'] + '/actions/runs/' + os.environ['GITHUB_RUN_ID'],
          'requested_graph': OWNERS, 'native_builds': {'LiveContainer': 'PASS_UNSIGNED_RELEASE_IPHONEOS',
          'SideStore': 'PASS_UNSIGNED_RELEASE_IPHONEOS'}, 'compiler_input_bytes_unchanged': True,
          'generated_openssl_inputs_unchanged': True,
          'generated_openssl_payload_receipt_sha256': sha(E / 'generated-openssl-payload.json'),
          'generated_embedded_openssl_payload_receipt_sha256': sha(E / 'generated-embedded-openssl-payload.json'),
          'working_sidestore_app_lock_differs_from_git': working_app_lock_differs(), 'final_lock_only_child_compiled': False,
          'production_admission': False, 'combined_ipa_packaged': False, 'physical_device_execution': False,
          'authentication_executed': False, 'guest_binary_executed': False,
          'live_authentication_executed': False, 'source_owned_fake_authentication_executed': True,
          'source_owned_fake_diagnostics_executed': True,
          'comprehensive_diagnostics_report_sha256': sha(diagnostic_report),
          'setup_state_recovery_report_sha256': sha(setup_report),
          'russian_localization_report_sha256': sha(russian_report),
          'source_owned_oda_qualification_executed': True,
          'owned_oda_qualification_receipt_sha256': sha(E / 'owned-oda/receipt.json'),
          'issue_regression_tests_executed': True, 'target_native_tests_executed': False,
          'issue_regression_receipt_sha256': sha(E / 'issue-tests.json'), 'independent_review': 'pending',
          'evidence_sha256': {p.name: sha(p) for p in sorted(E.iterdir()) if p.is_file() and p.name != 'receipt.json'}})


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'snapshot':
        snapshot(sys.argv[2])
    elif action == 'stage-openssl':
        require(len(sys.argv) == 3, 'stage-openssl requires exactly one build directory')
        stage_openssl(Path(sys.argv[2]))
    elif action == 'stage-embedded-openssl':
        require(len(sys.argv) == 4, 'stage-embedded-openssl requires build directory and pinned upstream checkout')
        stage_embedded_openssl(Path(sys.argv[2]), Path(sys.argv[3]))
    elif action == 'acquire':
        acquire()
    elif action == 'seal-resolution':
        seal_resolution()
    elif action == 'verify-resolved':
        verify_resolved()
    elif action == 'receipt':
        receipt()
    else:
        raise ValueError('Unknown action')
