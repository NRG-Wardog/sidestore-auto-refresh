"""Reconstructed P0 admission. Fresh review required; deliberately disabled.

This is newly reconstructed code, not the lost previously tested artifact.
The immutable review anchor can only be assigned after full real native review.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

BASIS = 'p0-abi-tls-certificate-lifecycle-v1'
P0_REVIEW_SHA256 = '34d12b14364d4db4ed24ee80bf206332d383c27e958b497d6f5cd1c9d28cc6dc'
DIRECTORY = 'migration/p0-native-basis-v1'
CHECKPOINTS = {
    'LiveContainer': '47db163356dc419d2b6a894fdc5dbeb9d011dc69',
    'SideStore': '9d8c71ed69684f805325ef440983e74d97113a71',
    'AnisetteKit': '62ce85c8798d8eab8e29752aba7dc9f1f6a5b80d',
    'SideSign': 'ed30d3989ea0f80bcb91466d6d5ca043f4366df0',
    'minimuxer': 'efbcab05d7d636aa37c6bf6c7f364d122c5610f6',
    'idevice': '803f9368e6b27a43d8a9f22dc0ec281c87883113',
    'jktcp': '32c0210f286b4fd16efe05be4f57167a9d94459b',
}
FORK_SHA256 = '4f61907db6aafc56fb3e336b524d742342312f498bb40739f1da55fb4a24614a'

def need(value, message):
    if not value:
        raise ValueError(message)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def digest(value):
    need(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) and value != '0'*64,
         'P0 independently reviewed digest is pending')
    return value

def fullsha(value):
    need(isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) and value != '0'*40,
         'P0 actual commit/tree is pending')
    return value

def relative(name):
    need(isinstance(name, str) and name and not name.startswith('/') and '\\' not in name and ':' not in name and
         all(p not in ('', '.', '..', '.git') for p in name.split('/')), 'unsafe P0 metadata/source path')
    return name

def read(root, name):
    relative(name)
    path = Path(root)
    need(not path.is_symlink(), 'linked P0 metadata root')
    for part in name.split('/'):
        path /= part
        need(not path.is_symlink(), 'linked P0 metadata')
    need(path.is_file() and path.stat().st_size <= 8*1024*1024, 'P0 metadata absent/oversized: '+name)
    return path.read_bytes()

def parse(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            need(key not in value, 'duplicate P0 JSON key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique)

def run_identity(value):
    need(re.fullmatch(r'https://github\.com/NRG-Wardog/LiveContainer/actions/runs/[1-9][0-9]*', value.get('run_url', '')),
         'P0 real native run URL required')
    for key in ('run_attempt', 'artifact_id'):
        need(type(value.get(key)) is int and value[key] > 0, 'P0 run/artifact ID required')
    for key in ('host_commit', 'host_tree'):
        fullsha(value.get(key))
    for key in ('approved_inputs_sha256', 'artifact_sha256', 'evidence_index_sha256'):
        digest(value.get(key))

def child_map(root):
    children = parse(read(root, 'submodules.json'))
    need(isinstance(children, dict), 'P0 child map required')
    for path, row in children.items():
        relative(path)
        need(path.split('/')[0] in CHECKPOINTS and len(path.split('/')) > 1, 'child lacks owner-relative path')
        need(set(row) == {'repository', 'declared_repository', 'commit', 'tree'}, 'P0 exact child schema required')
        for key in ('repository', 'declared_repository'):
            need(re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', row[key]), 'P0 child origin invalid')
        fullsha(row['commit']); fullsha(row['tree'])
    return children

def approval(integration_root, metadata_root=None):
    digest(P0_REVIEW_SHA256)  # Fail before reading caller-controlled metadata.
    root = Path(metadata_root) if metadata_root is not None else Path(integration_root)/DIRECTORY
    raw = read(root, 'reviewed-approval.json')
    need(sha(raw) == P0_REVIEW_SHA256, 'P0 review anchor differs')
    value = parse(raw)
    need(set(value) == {'schema_version', 'source_basis', 'integration_parent', 'owners', 'files', 'native_validation'}, 'P0 review schema differs')
    need(value['schema_version'] == 1 and value['source_basis'] == BASIS and
         value['integration_parent'] == '767234eb704745ce915c0750eee2c31061ac4f78', 'wrong P0 review identity')
    need(set(value['owners']) == set(CHECKPOINTS), 'exact seven-owner P0 review required')
    required = {'compatibility-registry.json', 'source-inventory.json', 'native-receipt.json', 'submodules.json',
                'anisette-generated/maintained-source-manifest.json', 'SideSign-resolver.json', 'SideStore-resolver.json'}
    need(isinstance(value['files'], dict) and required <= set(value['files']), 'P0 reviewed file inventory incomplete')
    for name, expected in value['files'].items():
        need(sha(read(root, name)) == digest(expected), 'P0 reviewed metadata differs: '+name)
    native = value['native_validation']
    need(isinstance(native, dict) and native.get('source_basis') == BASIS and
         native.get('readiness_scope') == 'eligible_for_gated_full_build' and native.get('review_sha256') is None,
         'P0 native readiness must originate in independently reviewed receipt')
    run_identity(native)
    receipt = parse(read(root, 'native-receipt.json'))
    need(receipt.get('status') == 'INDEPENDENTLY_REVIEWED_P0_NATIVE_PASS' and receipt.get('source_basis') == BASIS,
         'P0 fresh native evidence unreviewed')
    need(receipt.get('native_validation') == native, 'P0 native receipt identity differs')
    need(receipt.get('native_builds') == {'SideSign': 'PASS_MACOS_BUILD_AND_TEST', 'SideStore': 'PASS_UNSIGNED_IPHONEOS',
         'LiveContainer': 'PASS_UNSIGNED_IPHONEOS'}, 'P0 native builds incomplete')
    need(receipt.get('canonical_native_fork83') == {'checks': 83, 'failures': 0,
         'source_owner': value['owners']['AnisetteKit']['commit'], 'engine_archive_sha256': FORK_SHA256}, 'P0 canonical fork identity differs')
    need(set(native.get('tested_owners', {})) == set(CHECKPOINTS), 'P0 tested owner graph incomplete')
    for owner, row in value['owners'].items():
        need(set(row) == {'repository', 'commit', 'tree', 'source_checkpoint', 'path'}, 'P0 reviewed owner schema differs')
        need(row['repository'] == 'https://github.com/NRG-Wardog/'+owner+'.git' and
             row['source_checkpoint'] == CHECKPOINTS[owner], 'P0 owner origin/checkpoint drift')
        relative(row['path']); fullsha(row['commit']); fullsha(row['tree'])
        tested = native['tested_owners'][owner]
        need(set(tested) == {'commit', 'tree'}, 'P0 tested owner schema differs')
        fullsha(tested['commit']); fullsha(tested['tree'])
        if owner != 'SideStore':
            need(tested == {'commit': row['commit'], 'tree': row['tree']}, 'P0 owner changed after native capture')
    expected_children = {'SideSign': {}, 'SideStore': {'Dependencies/SideSign': value['owners']['SideSign']['commit'],
                         'Dependencies/minimuxer': value['owners']['minimuxer']['commit']}}
    need(native.get('tested_children') == expected_children, 'P0 native child graph differs')
    children = child_map(root)
    for owner in ('SideSign', 'minimuxer'):
        child = children.get('SideStore/Dependencies/'+owner)
        need(child == {'repository': value['owners'][owner]['repository'], 'declared_repository': value['owners'][owner]['repository'],
                      'commit': value['owners'][owner]['commit'], 'tree': value['owners'][owner]['tree']}, 'P0 named child graph differs')
    need(receipt.get('tested_submodules') == children, 'P0 complete child map lacks exact native receipt binding')
    phases = receipt.get('phase_evidence', {})
    need(set(phases) == {'SideSign', 'SideStore'}, 'P0 phase evidence incomplete')
    run_fields = ('run_url', 'run_attempt', 'host_commit', 'host_tree', 'artifact_id', 'artifact_sha256', 'approved_inputs_sha256', 'evidence_index_sha256')
    linked = (*run_fields, 'resolver_tested_commit', 'resolver_tested_tree', 'lock_before_sha256', 'lock_sha256', 'origin_hash')
    for owner in ('SideSign', 'SideStore'):
        resolver = parse(read(root, owner+'-resolver.json'))
        need(resolver.get('purpose') == 'independently_reviewed_p0_swiftpm_resolution' and resolver.get('owner') == owner and
             resolver.get('source_basis') == BASIS and resolver.get('anisette_commit') == value['owners']['AnisetteKit']['commit'], 'P0 resolver identity differs')
        phase = phases[owner]
        need(isinstance(phase, dict) and phase.get('status') == 'CAPTURED_FOR_REVIEW', 'P0 phase incomplete')
        need(all(key in resolver and resolver[key] == phase.get(key) for key in linked), 'P0 resolver/native phase linkage differs')
        run_identity(resolver)
        for key in ('lock_sha256', 'lock_before_sha256'):
            digest(resolver.get(key))
        for key in ('resolver_tested_commit', 'resolver_tested_tree'):
            fullsha(resolver.get(key))
        if owner == 'SideStore':
            need(all(phase.get(key) == native.get(key) for key in run_fields), 'P0 final graph run differs from app phase')
            need({'commit': resolver['resolver_tested_commit'], 'tree': resolver['resolver_tested_tree']} == native['tested_owners']['SideStore'],
                 'P0 app resolver not native-tested owner')
    registry = parse(read(root, 'compatibility-registry.json'))
    need(set(registry.get('owners', {})) == set(CHECKPOINTS), 'P0 registry owners incomplete')
    refs = {registry['evidence']['path']: registry['evidence']['sha256']}
    refs.update({row['manifest_path']: row['manifest_sha256'] for row in registry['owners'].values()})
    need(all(value['files'].get(name) == expected for name, expected in refs.items()), 'P0 contract references not sealed')
    return root, value, receipt

def load_pins(document, integration_root, legacy):
    _, review, _ = approval(integration_root)
    need(set(document) == {'schema_version', 'integration_baseline', 'source_basis', 'contract_registry_sha256', 'owners', 'native_validation'}, 'P0 pin schema differs')
    need(document['schema_version'] == 1 and document['integration_baseline'] == legacy.BASELINE and document['source_basis'] == BASIS, 'P0 pin basis differs')
    expected = {owner: {k: v for k, v in row.items() if k != 'tree'} for owner, row in review['owners'].items()}
    need(document['owners'] == expected and all(row['path'] == legacy.PATHS[owner] for owner, row in expected.items()), 'P0 pin graph/path differs')
    need(document['contract_registry_sha256'] == review['files']['compatibility-registry.json'], 'P0 registry pin differs')
    need(document['native_validation'] == {**review['native_validation'], 'review_sha256': P0_REVIEW_SHA256}, 'P0 native pin differs')
    return document

def contract_basis(pins, integration_root):
    root, review, _ = approval(integration_root)
    need(pins.get('contract_registry_sha256') == review['files']['compatibility-registry.json'], 'P0 registry differs')
    return root, review['files']['compatibility-registry.json']

def contract_files(pins, integration_root, root=None):
    directory, review, _ = approval(integration_root, root)
    need(pins.get('contract_registry_sha256') == review['files']['compatibility-registry.json'], 'P0 packaged registry differs')
    return {'reviewed-approval.json': read(directory, 'reviewed-approval.json'), **{name: read(directory, name) for name in review['files']}}

def anisette_manifest(pins, integration_root):
    root, _, _ = approval(integration_root)
    value = parse(read(root, 'anisette-generated/maintained-source-manifest.json'))
    need(value.get('source_basis') == BASIS and value.get('anisettekit_revision') == pins['owners']['AnisetteKit']['commit'], 'P0 Anisette manifest identity differs')
    rows = value.get('files')
    need(isinstance(rows, list) and rows and len({r['path'] for r in rows}) == len(rows), 'P0 Anisette file inventory invalid')
    required = {'Native/Loader/android_tls_runtime.h', 'Native/Loader/elf_loader_emulator.cpp', 'Native/Loader/elf_loader_emulator.h',
                'Native/anisette_core_uc.cpp', 'Native/Loader/adi_consumption_debug.h', 'Native/include/anisette_core.h', 'Sources/AnisetteDataProvider.swift'}
    need(required <= {r['path'] for r in rows}, 'P0 Anisette runtime source missing')
    for row in rows:
        relative(row['path']); digest(row['sha256'])
    literals = value.get('required_compiled_evidence', {}).get('literals')
    required_literals = {'V3_CHECKED_ANISETTE_STAGING_V1', 'Checked OTP staging failed', 'v2|%u|%u|%u', '|%d,%d,%d,%d,%d,%u,%u,%d', 'v3_native_consumption'}
    need(isinstance(literals, list) and required_literals <= {r.get('value') for r in literals}, 'P0 inherited compiled evidence missing')
    return value

def verify_children(roots, children, legacy):
    import configparser
    seen = set()
    def visit(source, prefix):
        modules = configparser.ConfigParser(interpolation=None)
        if (source/'.gitmodules').exists():
            modules.read(source/'.gitmodules')
        for path, (mode, kind, oid) in legacy.entries(source).items():
            if kind != 'commit':
                continue
            relative(path)
            key = prefix+'/'+path
            need(mode == '160000' and key in children, 'P0 source has unreviewed child')
            row = children[key]
            declarations = [dict(modules[section]) for section in modules.sections() if modules[section].get('path') == path]
            need(declarations == [{'path': path, 'url': row['declared_repository']}] and oid == row['commit'], 'P0 child URL/gitlink differs')
            child = source/path
            need(legacy.git(child, 'rev-parse', 'HEAD^{tree}').decode().strip() == row['tree'], 'P0 child tree differs')
            legacy.verify_origin(child, row['repository'], row['commit'])
            seen.add(key)
            visit(child, key)
    for owner in ('LiveContainer', 'SideStore', 'AnisetteKit', 'idevice', 'jktcp'):
        visit(roots[owner], owner)
    need(seen == set(children), 'P0 final child closure differs')

def verify_all(workspace, pins, anisette, after_build, legacy):
    root, review, _ = approval(legacy.ROOT)
    sys.path.insert(0, str(legacy.ROOT))
    from prepare_candidate_inventory import BASES, snapshot
    inventory = parse(read(root, 'source-inventory.json'))
    need(set(inventory) == set(CHECKPOINTS), 'P0 final source inventory incomplete')
    roots = {name: workspace/row['path'] for name, row in pins['owners'].items()}
    if anisette is not None:
        roots['AnisetteKit'] = Path(anisette)
    results = {}
    for name, source in roots.items():
        spec = pins['owners'][name]
        actual = snapshot(source, BASES[name], spec['commit'])
        need(actual == inventory[name] and actual['tree'] == review['owners'][name]['tree'], 'P0 final source inventory differs: '+name)
        results[name] = legacy.verify_checkout(source, spec['commit'], owner=name, after_build=after_build, require_full_history=True)
        results[name]['origin'] = legacy.verify_origin(source, spec['repository'], spec['commit'], Path(anisette).parent.parent if name == 'AnisetteKit' and anisette else None)
        tested = review['native_validation']['tested_owners'][name]
        need(legacy.git(source, 'rev-parse', tested['commit']+'^{tree}').decode().strip() == tested['tree'], 'P0 tested tree differs')
        if name == 'SideStore':
            need(set(snapshot(source, tested['commit'], spec['commit'])['changes']) <= {legacy.APP_LOCK}, 'P0 runtime changed after native capture')
    legacy.verify_graph(roots, pins)
    for name, path in (('SideSign', 'Package.resolved'), ('SideStore', legacy.APP_LOCK)):
        resolver = parse(read(root, name+'-resolver.json'))
        source = roots[name]
        need(legacy.git(source, 'rev-parse', resolver['resolver_tested_commit']+'^{tree}').decode().strip() == resolver['resolver_tested_tree'], 'P0 resolver tree differs')
        need(sha(legacy.git(source, 'show', resolver['resolver_tested_commit']+':'+path)) == resolver['lock_before_sha256'], 'P0 resolver starting lock differs')
        need(sha((source/path).read_bytes()) == resolver['lock_sha256'], 'P0 final lock differs from captured resolver bytes')
        need(set(snapshot(source, resolver['resolver_tested_commit'], pins['owners'][name]['commit'])['changes']) <= {path}, 'P0 resolver descendant changed runtime')
        lock = parse((source/path).read_bytes())
        need(resolver['origin_hash'] == {'present': 'originHash' in lock, 'value': lock.get('originHash')}, 'P0 originHash differs')
    verify_children(roots, child_map(root), legacy)
    command = [sys.executable, '-B', str(legacy.ROOT/'migration/contracts/validate_contracts.py'), '--registry', str(root/'compatibility-registry.json'), '--registry-sha256', review['files']['compatibility-registry.json']]
    for name, source in roots.items():
        command += ['--owner', name+'='+str(source.resolve())]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return {'schema_version': 1, 'integration_baseline': legacy.BASELINE, 'owners': results, 'contracts': parse(result.stdout),
            'runtime_rewrites': False, 'approved_native_validation': pins['native_validation'],
            'resolved_dependencies': legacy.verify_resolution(roots, anisette) if anisette else None,
            'built_idevice': legacy.verify_build_products(roots) if after_build else None}

def acquire(workspace, pins, legacy):
    import configparser
    root, review, _ = approval(legacy.ROOT)
    children = child_map(root)
    seen = set()
    def fetch(path, row, prefix):
        need(not path.is_symlink() and (not path.exists() or (path.is_dir() and not list(path.iterdir()))), 'P0 refuses nonfresh source target')
        for parent in path.parents:
            if parent == workspace:
                break
            need(not parent.is_symlink(), 'P0 linked source parent')
        path.parent.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL='https', GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
        subprocess.run(['git', '--no-replace-objects', '-c', 'core.hooksPath='+os.devnull, 'clone', '--no-checkout', row['repository'], str(path)], check=True, env=env)
        legacy.git(path, 'fetch', '--no-tags', '--no-recurse-submodules', 'origin', row['commit'])
        legacy.git(path, 'checkout', '--detach', row['commit'])
        legacy.verify_origin(path, row['repository'], row['commit'])
        need(legacy.git(path, 'rev-parse', 'HEAD^{tree}').decode().strip() == row['tree'], 'P0 acquired tree differs')
        modules = configparser.ConfigParser(interpolation=None)
        if (path/'.gitmodules').exists():
            modules.read(path/'.gitmodules')
        for relative_path, (mode, kind, oid) in legacy.entries(path).items():
            if kind != 'commit':
                continue
            relative(relative_path)
            key = prefix+'/'+relative_path
            need(mode == '160000' and key in children, 'P0 unreviewed child gitlink')
            child = children[key]
            declarations = [dict(modules[section]) for section in modules.sections() if modules[section].get('path') == relative_path]
            need(declarations == [{'path': relative_path, 'url': child['declared_repository']}] and oid == child['commit'], 'P0 child URL/gitlink differs before fetch')
            seen.add(key)
            fetch(path/relative_path, child, key)
    for owner in ('LiveContainer', 'SideStore', 'AnisetteKit', 'idevice', 'jktcp'):
        row = review['owners'][owner]
        path = workspace/pins['owners'][owner]['path']
        need(not path.exists(), 'P0 owner requires fresh target')
        fetch(path, row, owner)
    need(seen == set(children), 'P0 reviewed child closure differs')
