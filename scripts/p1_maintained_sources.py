"""Admit the exact reviewed P1 presentation delta above immutable P0 evidence.

The independent P1 native-review digest remains unset until a real host/UI run
has been downloaded and reviewed. Source tests and synthetic fixtures cannot
assign readiness. P0 approval data and code retain their original semantics.
"""
import copy
from pathlib import Path
import subprocess
import sys

import p0_maintained_sources as p0
from p0_maintained_sources import need, sha, digest, fullsha, read, parse, relative

BASIS = 'p1-ui-feedback-v1'
DIRECTORY = 'migration/p1-native-basis-v1'
P1_REVIEW_SHA256 = None
INTEGRATION_PARENT = '7e5062114f3ba8c1fe1990389ae9a008ce4d8c1f'
HOST_PARENT = '00546cec351adad1fa1cad5590da4f06e612db8f'
HOST_PARENT_TREE = 'c8b298d549ff5e72221533c5e4b946d67ec2cc97'
HOST_COMMIT = 'fbaafb939bfd3b7c08fdc8ff4b8d2aae43526ece'
HOST_TREE = '3d3efc14415db6d28ede216965f83fa5924e9e9c'
SHELL = 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift'
BEFORE = {'mode': '100644', 'type': 'blob', 'git_object': 'f6a16cf997048f93b27be1eba39382f448273f54',
          'sha256': '7f9efe5881898bad6e8e2182c0095166b62f8b251dbb36b313d6876067df56be'}
AFTER = {'mode': '100644', 'type': 'blob', 'git_object': '093a59955fff9252e18cc42a76c7f58055ef39d4',
         'sha256': '3c40aab6667431d0ecb502d49a38416b9e76250ddf6e66dc1037e60d4594e0be'}
TRANSITION = {'baseline_commit': HOST_PARENT, 'commit': HOST_COMMIT, 'tree': HOST_TREE,
              'changes': {SHELL: {'before': BEFORE, 'after': AFTER}}}
BEHAVIOR = {'passed': True, 'source_sha256': AFTER['sha256'],
            'device_classes': ['phone', 'tablet'], 'test_count': 10, 'cases_per_device': 5,
            'case_ids': ['feedback-timeout', 'prompt-replacement', 'dismiss-reopen',
                         'recovery-navigation-default', 'recovery-navigation-largest']}


def base_pins(integration_root):
    _, review, _ = p0.approval(integration_root)
    return {'schema_version': 1, 'integration_baseline': '141776ba6ba38fc04a5e77f68b0cfc4e6c8842ee',
            'source_basis': p0.BASIS, 'contract_registry_sha256': review['files']['compatibility-registry.json'],
            'owners': {name: {k: v for k, v in row.items() if k != 'tree'} for name, row in review['owners'].items()},
            'native_validation': {**review['native_validation'], 'review_sha256': p0.P0_REVIEW_SHA256}}


def expected_owners(prior):
    owners = copy.deepcopy(prior['owners'])
    need(owners['LiveContainer']['commit'] == HOST_PARENT and owners['LiveContainer']['tree'] == HOST_PARENT_TREE,
         'P1 parent is not the independently approved P0 host')
    owners['LiveContainer'].update(commit=HOST_COMMIT, tree=HOST_TREE)
    return owners


def approval(integration_root, metadata_root=None):
    need(P1_REVIEW_SHA256 is not None, 'P1 independent native/UI review is pending')
    digest(P1_REVIEW_SHA256)  # No caller metadata may self-authorize a pending P1.
    base_root, prior, _ = p0.approval(integration_root)
    root = Path(metadata_root) if metadata_root is not None else Path(integration_root)/DIRECTORY
    raw = read(root, 'reviewed-approval.json')
    need(sha(raw) == P1_REVIEW_SHA256, 'P1 independent review anchor differs')
    value = parse(raw)
    need(set(value) == {'schema_version', 'source_basis', 'integration_parent', 'inherited_p0_review_sha256',
                        'owners', 'transition', 'files', 'native_validation'}, 'P1 review schema differs')
    need(value['schema_version'] == 1 and value['source_basis'] == BASIS and
         value['integration_parent'] == INTEGRATION_PARENT and
         value['inherited_p0_review_sha256'] == p0.P0_REVIEW_SHA256, 'P1 review identity differs')
    need(value['owners'] == expected_owners(prior), 'P1 owner graph differs beyond exact host transition')
    need(value['transition'] == TRANSITION, 'P1 exact one-file transition differs')
    inherited = {'inherited/p0-native-basis-v1/reviewed-approval.json': p0.P0_REVIEW_SHA256,
                 **{'inherited/p0-native-basis-v1/'+name: expected for name, expected in prior['files'].items()}}
    required = set(prior['files']) | set(inherited) | {'p0-to-p1-transition.json', 'inherited/p0-pins.json'}
    need(isinstance(value['files'], dict) and set(value['files']) == required, 'P1 exact metadata inventory differs')
    for name, expected in value['files'].items():
        need(sha(read(root, name)) == digest(expected), 'P1 reviewed metadata differs: '+name)
    need(all(value['files'][name] == expected for name, expected in inherited.items()), 'P1 inherited P0 evidence changed')
    need(parse(read(root, 'inherited/p0-pins.json')) == base_pins(integration_root), 'P1 frozen P0 pins differ')
    need(parse(read(root, 'p0-to-p1-transition.json')) == TRANSITION, 'P1 transition file differs')
    # Every unaffected source/lock/child fact must remain exactly the P0 fact.
    adapted = {'source-inventory.json', 'native-receipt.json', 'compatibility-registry.json',
               'owners/LiveContainer/runtime-contract.json', 'provenance/source-evidence.json'}
    for name, expected in prior['files'].items():
        if name not in adapted:
            need(value['files'][name] == expected, 'P1 changed unrelated P0 metadata: '+name)
    expected_inventory = parse(read(base_root, 'source-inventory.json'))
    expected_inventory['LiveContainer'].update(commit=HOST_COMMIT, tree=HOST_TREE)
    expected_inventory['LiveContainer']['changes'][SHELL]['after'] = AFTER
    need(parse(read(root, 'source-inventory.json')) == expected_inventory, 'P1 source inventory exceeds exact host delta')
    native = value['native_validation']
    need(isinstance(native, dict) and set(native) == {
        'source_basis', 'readiness_scope', 'review_sha256', 'run_url', 'run_attempt', 'host_commit', 'host_tree',
        'artifact_id', 'artifact_sha256', 'approved_inputs_sha256', 'evidence_index_sha256',
        'tested_owners', 'inherited_p0_review_sha256'}, 'P1 native validation schema differs')
    need(native['source_basis'] == BASIS and native['readiness_scope'] == 'eligible_for_gated_full_build' and
         native['review_sha256'] is None and native['inherited_p0_review_sha256'] == p0.P0_REVIEW_SHA256,
         'P1 independent native readiness required')
    p0.run_identity(native)
    need(native['run_url'] != prior['native_validation']['run_url'], 'P1 native run cannot prove P1 host')
    need(native['tested_owners'] == {'LiveContainer': {'commit': HOST_COMMIT, 'tree': HOST_TREE}},
         'P1 fresh native host identity differs')
    receipt = parse(read(root, 'native-receipt.json'))
    need(receipt.get('status') == 'INDEPENDENTLY_REVIEWED_P1_NATIVE_PASS' and receipt.get('source_basis') == BASIS,
         'P1 native receipt is absent or unreviewed')
    need(receipt.get('native_validation') == native, 'P1 native receipt identity differs')
    need(receipt.get('native_builds') == {'LiveContainer': 'PASS_UNSIGNED_IPHONEOS'}, 'P1 fresh host build required')
    need(receipt.get('native_suites', {}).get('cleanup') == {'tests': 8, 'failures': 0, 'skipped': 0},
         'P1 source capability checks incomplete')
    ui = receipt.get('ui_validation', {})
    need(ui == {'status': 'PASS', 'source_sha256': AFTER['sha256'], 'legacy_reports': 14,
                'device_classes': 2, 'signin_cases': 8, 'validated_screenshots': 24},
         'P1 actual-source native UI matrix incomplete')
    need(receipt.get('behavior_validation') == BEHAVIOR,
         'P1 targeted native feedback/reset/recovery behavior proof incomplete')
    need(receipt.get('inherited_p0_review_sha256') == p0.P0_REVIEW_SHA256,
         'P1 receipt lost inherited dependency evidence')
    registry = parse(read(root, 'compatibility-registry.json'))
    need(set(registry.get('owners', {})) == set(p0.CHECKPOINTS), 'P1 registry owners incomplete')
    refs = {registry['evidence']['path']: registry['evidence']['sha256']}
    refs.update({row['manifest_path']: row['manifest_sha256'] for row in registry['owners'].values()})
    need(all(value['files'].get(name) == expected for name, expected in refs.items()), 'P1 contract references not sealed')
    return root, value, receipt


def load_pins(document, integration_root, legacy):
    _, review, _ = approval(integration_root)
    need(set(document) == {'schema_version', 'integration_baseline', 'source_basis', 'contract_registry_sha256',
                           'owners', 'native_validation'}, 'P1 pin schema differs')
    need(document['schema_version'] == 1 and document['integration_baseline'] == legacy.BASELINE and
         document['source_basis'] == BASIS, 'P1 pin basis differs')
    expected = {owner: {k: v for k, v in row.items() if k != 'tree'} for owner, row in review['owners'].items()}
    need(document['owners'] == expected and all(row['path'] == legacy.PATHS[owner] for owner, row in expected.items()),
         'P1 pin graph/path differs')
    need(document['contract_registry_sha256'] == review['files']['compatibility-registry.json'], 'P1 registry pin differs')
    need(document['native_validation'] == {**review['native_validation'], 'review_sha256': P1_REVIEW_SHA256},
         'P1 native pin differs')
    return document


def contract_basis(pins, integration_root):
    import maintained_sources as legacy
    load_pins(pins, integration_root, legacy)
    root, review, _ = approval(integration_root)
    need(pins.get('contract_registry_sha256') == review['files']['compatibility-registry.json'], 'P1 registry differs')
    return root, review['files']['compatibility-registry.json']


def contract_files(pins, integration_root, root=None):
    directory, review, _ = approval(integration_root, root)
    # Validate the complete caller pin map, not only a caller-supplied registry.
    import maintained_sources as legacy
    load_pins(pins, integration_root, legacy)
    return {'reviewed-approval.json': read(directory, 'reviewed-approval.json'),
            **{name: read(directory, name) for name in review['files']}}


def anisette_manifest(pins, integration_root):
    import maintained_sources as legacy
    load_pins(pins, integration_root, legacy)
    return p0.anisette_manifest(base_pins(integration_root), integration_root)


def verify_transition(source, legacy):
    parents = legacy.git(source, 'rev-list', '--parents', '-n', '1', HOST_COMMIT).decode().strip().split()
    need(parents == [HOST_COMMIT, HOST_PARENT], 'P1 must directly descend from reviewed P0 host')
    need(legacy.git(source, 'rev-parse', HOST_PARENT+'^{tree}').decode().strip() == HOST_PARENT_TREE,
         'P1 parent tree differs')
    need(legacy.git(source, 'rev-parse', HOST_COMMIT+'^{tree}').decode().strip() == HOST_TREE, 'P1 host tree differs')
    sys.path.insert(0, str(legacy.ROOT))
    from prepare_candidate_inventory import snapshot
    need(snapshot(source, HOST_PARENT, HOST_COMMIT) == TRANSITION, 'P1 actual Git transition differs')
    raw = legacy.git(source, 'show', HOST_COMMIT+':'+SHELL)
    need(sha(raw) == AFTER['sha256'] and b'@State private var copiedDiagnostic: String?' in raw and
         b'.safeAreaInset(edge: .top, spacing: 0)' in raw, 'P1 feedback capability absent')


def verify_all(workspace, pins, anisette, after_build, legacy):
    root, review, _ = approval(legacy.ROOT)
    _, prior, _ = p0.approval(legacy.ROOT)
    load_pins(pins, legacy.ROOT, legacy)
    sys.path.insert(0, str(legacy.ROOT))
    from prepare_candidate_inventory import BASES, snapshot
    inventory = parse(read(root, 'source-inventory.json'))
    need(set(inventory) == set(p0.CHECKPOINTS), 'P1 final source inventory incomplete')
    roots = {name: workspace/row['path'] for name, row in pins['owners'].items()}
    if anisette is not None:
        roots['AnisetteKit'] = Path(anisette)
    results = {}
    for name, source in roots.items():
        spec = pins['owners'][name]
        actual = snapshot(source, BASES[name], spec['commit'])
        need(actual == inventory[name] and actual['tree'] == review['owners'][name]['tree'], 'P1 final source inventory differs: '+name)
        results[name] = legacy.verify_checkout(source, spec['commit'], owner=name, after_build=after_build, require_full_history=True)
        results[name]['origin'] = legacy.verify_origin(source, spec['repository'], spec['commit'], Path(anisette).parent.parent if name == 'AnisetteKit' and anisette else None)
        tested = (review['native_validation']['tested_owners']['LiveContainer'] if name == 'LiveContainer'
                  else prior['native_validation']['tested_owners'][name])
        need(legacy.git(source, 'rev-parse', tested['commit']+'^{tree}').decode().strip() == tested['tree'], 'P1 tested tree differs')
        if name == 'SideStore':
            need(set(snapshot(source, tested['commit'], spec['commit'])['changes']) <= {legacy.APP_LOCK}, 'P1 runtime changed after native capture')
    verify_transition(roots['LiveContainer'], legacy)
    legacy.verify_graph(roots, pins)
    for name, path in (('SideSign', 'Package.resolved'), ('SideStore', legacy.APP_LOCK)):
        resolver = parse(read(root, name+'-resolver.json'))
        source = roots[name]
        need(legacy.git(source, 'rev-parse', resolver['resolver_tested_commit']+'^{tree}').decode().strip() == resolver['resolver_tested_tree'], 'P1 resolver tree differs')
        need(sha(legacy.git(source, 'show', resolver['resolver_tested_commit']+':'+path)) == resolver['lock_before_sha256'], 'P1 resolver starting lock differs')
        need(sha((source/path).read_bytes()) == resolver['lock_sha256'], 'P1 final lock differs from captured resolver bytes')
        need(set(snapshot(source, resolver['resolver_tested_commit'], pins['owners'][name]['commit'])['changes']) <= {path}, 'P1 resolver descendant changed runtime')
        lock = parse((source/path).read_bytes())
        need(resolver['origin_hash'] == {'present': 'originHash' in lock, 'value': lock.get('originHash')}, 'P1 originHash differs')
    p0.verify_children(roots, p0.child_map(root), legacy)
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
    import os
    root, review, _ = approval(legacy.ROOT)
    load_pins(pins, legacy.ROOT, legacy)
    children = p0.child_map(root)
    seen = set()
    def fetch(path, row, prefix):
        need(not path.is_symlink() and (not path.exists() or (path.is_dir() and not list(path.iterdir()))), 'P1 refuses nonfresh source target')
        for parent in path.parents:
            if parent == workspace:
                break
            need(not parent.is_symlink(), 'P1 linked source parent')
        path.parent.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL='https', GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
        subprocess.run(['git', '--no-replace-objects', '-c', 'core.hooksPath='+os.devnull, 'clone', '--no-checkout', row['repository'], str(path)], check=True, env=env)
        legacy.git(path, 'fetch', '--no-tags', '--no-recurse-submodules', 'origin', row['commit'])
        legacy.git(path, 'checkout', '--detach', row['commit'])
        legacy.verify_origin(path, row['repository'], row['commit'])
        need(legacy.git(path, 'rev-parse', 'HEAD^{tree}').decode().strip() == row['tree'], 'P1 acquired tree differs')
        modules = configparser.ConfigParser(interpolation=None)
        if (path/'.gitmodules').exists():
            modules.read(path/'.gitmodules')
        for relative_path, (mode, kind, oid) in legacy.entries(path).items():
            if kind != 'commit':
                continue
            relative(relative_path)
            key = prefix+'/'+relative_path
            need(mode == '160000' and key in children, 'P1 unreviewed child gitlink')
            child = children[key]
            declarations = [dict(modules[section]) for section in modules.sections() if modules[section].get('path') == relative_path]
            need(declarations == [{'path': relative_path, 'url': child['declared_repository']}] and oid == child['commit'], 'P1 child URL/gitlink differs before fetch')
            seen.add(key)
            fetch(path/relative_path, child, key)
    for owner in ('LiveContainer', 'SideStore', 'AnisetteKit', 'idevice', 'jktcp'):
        row = review['owners'][owner]
        path = workspace/pins['owners'][owner]['path']
        need(not path.exists(), 'P1 owner requires fresh target')
        fetch(path, row, owner)
    need(seen == set(children), 'P1 reviewed child closure differs')
