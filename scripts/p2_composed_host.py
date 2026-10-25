"""Additive, exact-source P2 composition admission atop frozen P0 C++ evidence.

The independent anchor stays unset until genuine capture replay and review.
Inherited P0 runs and parent P2 visual observations are never fresh e22 evidence.
"""
import copy
import os
from pathlib import Path
import re
import subprocess
import sys

import p0_cxx_allocation as historical
from p0_maintained_sources import need, sha, digest, fullsha, relative, read, parse, child_map, verify_children

BASIS = 'p2-composed-host-v1'
DIRECTORY = 'migration/' + BASIS
INTEGRATION_PARENT = '083b0f425549f987081005a97cacba814f456e11'
REVIEW_SHA256 = 'ceb365632e7e7baa8a8926f2606ea44eaa5caccd058257d4b71d98e4f73729a0'  # Pending genuine composed capture and independent review.
LC_COMMIT = 'e22de6df412a0977f7430b2218960ca3ee9585d5'
LC_TREE = '4b973e2bbf482953e298c41c7a7227cb60b30718'
LC_PARENT = 'f46a3e14c0752714144aa076355663c79a54f1a9'
LC_PARENT_TREE = 'f18b17b69ef3bdd061328c4bf4d14e8b0cc5f330'
SOURCE_DELTA_SHA256 = '09d2269ff28447e29a4228af6ab289e06211b19bd14521650b5f7b78ae8084e0'
COMPOSITION_SHA256 = '6677f3525a2af55d9873b570d39f3da473d1c3f3dc6fd87ef7d5941e4eacced8'
NATIVE_HOST_COMMIT = '1d09456b441912c12227052eb21a26389ac0a203'
NATIVE_HOST_TREE = '8e12c5938e30b761d1234506e2a6fdb6c4f66e1c'
NATIVE_INPUT_SHA256 = '5d1f466bae1fa20c707a8e164b05e33a1e8a92e99be91e73db592ad032d308a8'
UI_RUN_URL = 'https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/39169380159'
UI_ARTIFACT_ID = 12152372036
UI_ARTIFACT_SHA256 = 'e37521f34064bf1683779a99486f53a40c12f6b971b400ccc8fee5b936744903'
UI_SCOPE = 'Reviewed parent visible Settings picker and persisted preference in four languages; no composed UI rerun or all-app/error-string visual claim'
FRESH_SCOPE = 'Exact composed Release simulator compilation, compiled localization/plural/permission resources, and five native initializer consumer tests'
COMPOSITION_FILES = {'LiveContainerSwiftUI/Views/V3UnifiedShell.swift', 'SideStoreSupport/SideStore.swift',
    'tests/init_diagnostics/test_contract.py', 'tests/init_diagnostics/pipeline_harness.swift',
    'tests/init_diagnostics/native_fixture.cpp', 'tests/init_diagnostics/host_harness.swift', 'tests/init_diagnostics/README.md'}
CONTRACT_NAMES = {'compatibility-registry.json', 'provenance/source-evidence.json', 'validate_contracts.py',
    'anisette-generated/maintained-source-manifest.json'} | {'owners/' + owner + '/runtime-contract.json' for owner in historical.PARENTS}
SOURCE_NAMES = CONTRACT_NAMES | {'source-inventory.json', 'reviewed-source-delta.json', 'composition-parent-delta.json', 'submodules.json'}


def inherited_names(prior):
    return {'reviewed-approval.json', 'maintained-sources.json', *prior['files']}


def expected_owners(prior):
    result = copy.deepcopy(prior['owners'])
    result['LiveContainer'].update(commit=LC_COMMIT, tree=LC_TREE)
    return result


def compose_inventory(previous, transition):
    value = copy.deepcopy(previous)
    need(set(transition) == {'baseline_commit', 'commit', 'tree', 'changes'}, 'P2 transition schema differs')
    need(transition['baseline_commit'] == previous['commit'], 'P2 source baseline differs')
    value.update(commit=transition['commit'], tree=transition['tree'])
    for name, change in transition['changes'].items():
        relative(name)
        need(set(change) == {'before', 'after'} and change['before'] != change['after'], 'P2 malformed source change')
        for record in change.values():
            if record is None:
                continue
            need(set(record) == {'mode', 'type', 'git_object', 'sha256'} and record['type'] == 'blob' and
                 record['mode'] in {'100644', '100755'}, 'P2 changes must be regular source blobs')
            fullsha(record['git_object']); digest(record['sha256'])
        original = value['changes'].get(name)
        if original is not None:
            need(change['before'] == original['after'], 'P2 prior source record differs')
            change = {'before': original['before'], 'after': change['after']}
        if change['before'] == change['after']:
            value['changes'].pop(name, None)
        else:
            value['changes'][name] = change
    return value


def validate_source_metadata(root, prior_root, prior):
    """Checks source bytes only. It deliberately grants no execution readiness."""
    delta_raw = read(root, 'reviewed-source-delta.json')
    composition_raw = read(root, 'composition-parent-delta.json')
    need(sha(delta_raw) == SOURCE_DELTA_SHA256 and sha(composition_raw) == COMPOSITION_SHA256, 'P2 exact reviewed source delta differs')
    delta, composition = parse(delta_raw), parse(composition_raw)
    need(delta['baseline_commit'] == prior['owners']['LiveContainer']['commit'] and
         delta['commit'] == LC_COMMIT and delta['tree'] == LC_TREE and len(delta['changes']) == 35, 'P2 exact P0-to-composed tuple differs')
    need(composition['baseline_commit'] == LC_PARENT and composition['commit'] == LC_COMMIT and
         composition['tree'] == LC_TREE and set(composition['changes']) == COMPOSITION_FILES, 'P2 exact parent-to-composed tuple differs')
    # All P0 native consumer/support files survive byte-for-byte. The shell has
    # reviewed localized changes and requires genuine composed native consumers.
    need(not (COMPOSITION_FILES - {'LiveContainerSwiftUI/Views/V3UnifiedShell.swift'}) & set(delta['changes']), 'P2 P0 support or initializer harness changed')
    before = parse(read(prior_root, 'source-inventory.json'))
    after = parse(read(root, 'source-inventory.json'))
    expected = copy.deepcopy(before)
    expected['LiveContainer'] = compose_inventory(before['LiveContainer'], delta)
    need(after == expected, 'P2 exhaustive composed source inventory differs')
    need(read(root, 'submodules.json') == read(prior_root, 'submodules.json'), 'P2 child gitlinks changed')
    for name in CONTRACT_NAMES - {'compatibility-registry.json', 'provenance/source-evidence.json', 'owners/LiveContainer/runtime-contract.json'}:
        need(read(root, name) == read(prior_root, name), 'P2 inherited contract changed: ' + name)
    old_contract = parse(read(prior_root, 'owners/LiveContainer/runtime-contract.json'))
    contract = copy.deepcopy(old_contract)
    for row in contract['sources']:
        change = delta['changes'].get(row['path'])
        if change is not None:
            need(change['before']['sha256'] == row['sha256'] and change['after']['mode'] == row['mode'], 'P2 contract before hash or mode differs')
            row['sha256'] = change['after']['sha256']
    need(parse(read(root, 'owners/LiveContainer/runtime-contract.json')) == contract, 'P2 contract semantics or source hashes changed')
    registry = parse(read(prior_root, 'compatibility-registry.json'))
    evidence = parse(read(prior_root, 'provenance/source-evidence.json'))
    evidence['owners']['LiveContainer'] = {key: contract[key] for key in ('sources', 'provenance')}
    need(parse(read(root, 'provenance/source-evidence.json')) == evidence, 'P2 contract provenance differs')
    registry['owners']['LiveContainer']['manifest_sha256'] = sha(read(root, 'owners/LiveContainer/runtime-contract.json'))
    registry['evidence']['sha256'] = sha(read(root, 'provenance/source-evidence.json'))
    need(parse(read(root, 'compatibility-registry.json')) == registry, 'P2 contracts, edges, or unrelated owner changed')
    return delta


def builder_run_identity(value):
    need(set(value) == {'run_url', 'run_attempt', 'artifact_id', 'artifact_sha256', 'evidence_index_sha256',
                       'host_commit', 'host_tree', 'approved_inputs_sha256'}, 'P2 capture identity schema differs')
    need(re.fullmatch(r'https://github\.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/[1-9][0-9]*', value['run_url']), 'P2 authenticated builder run required')
    for key in ('run_attempt', 'artifact_id'):
        need(type(value[key]) is int and value[key] > 0, 'P2 capture positive integer required')
    for key in ('host_commit', 'host_tree'):
        fullsha(value[key])
    for key in ('artifact_sha256', 'evidence_index_sha256', 'approved_inputs_sha256'):
        digest(value[key])


def validate_capture(capture, owners):
    need(set(capture) == {'schema_version', 'status', 'scope', 'identity', 'tested_owners', 'consumer_tests',
                         'product', 'claims', 'evidence_files'}, 'P2 native capture schema differs')
    need(type(capture['schema_version']) is int and capture['schema_version'] == 1 and
         capture['status'] == 'INDEPENDENTLY_REVIEWED_COMPOSED_HOST_PASS' and capture['scope'] == FRESH_SCOPE, 'P2 fresh capture scope differs')
    identity = capture['identity']; builder_run_identity(identity)
    need(identity['host_commit'] == NATIVE_HOST_COMMIT and identity['host_tree'] == NATIVE_HOST_TREE and
         identity['approved_inputs_sha256'] == NATIVE_INPUT_SHA256 and identity['run_url'] != UI_RUN_URL, 'P2 native capture runner differs')
    need(capture['tested_owners'] == {owner: {key: owners[owner][key] for key in ('commit', 'tree')}
         for owner in ('LiveContainer', 'SideStore', 'AnisetteKit')}, 'P2 native consumers did not test exact composed sources')
    tests = capture['consumer_tests']
    need(set(tests) == {'tests', 'failures', 'errors', 'skipped', 'test_ids'} and
         tests['test_ids'] == list(historical.CONSUMER_SUITE_IDS['init_diagnostics']), 'P2 exact native consumer IDs required')
    historical.exact_counts(tests, {'tests': 5, 'failures': 0, 'errors': 0, 'skipped': 0}, 'P2 native consumers')
    need(capture['product'] == {'configuration': 'Release', 'sdk': 'iphonesimulator', 'compile': 'passed',
         'localization': 'passed', 'exact_plural_values': 'passed', 'permissions': 'passed'}, 'P2 simulator product checks differ')
    claims = capture['claims']
    need(set(claims) == {'fresh_ui_execution', 'guest_execution', 'ADI_executed', 'device_validation', 'ipa_acceptance'} and
         all(value is False for value in claims.values()), 'P2 native capture overclaims validation scope')
    names = {'result.json', 'inputs.json', 'source-before.json', 'source-after.json', 'consumer-sources-before.json',
             'consumer-sources-after.json', 'consumer-tests.json', 'product-identity.json', 'compiled-resources.json'}
    need(isinstance(capture['evidence_files'], dict) and set(capture['evidence_files']) == names, 'P2 exact native evidence references required')
    for value in capture['evidence_files'].values():
        digest(value)


def validate_ui_basis(value):
    need(set(value) == {'status', 'scope', 'source_commit', 'source_tree', 'run_url', 'run_attempt', 'artifact_id',
                       'artifact_sha256', 'evidence_index_sha256', 'languages', 'composed_ui_reexecuted',
                       'service_failure_unresolved', 'full_app_acceptance', 'final_composition_acceptance'}, 'P2 parent UI schema differs')
    need(value['status'] == 'INDEPENDENTLY_REVIEWED_PARENT_UI' and value['scope'] == UI_SCOPE and
         value['source_commit'] == LC_PARENT and value['source_tree'] == LC_PARENT_TREE and
         value['run_url'] == UI_RUN_URL and type(value['run_attempt']) is int and value['run_attempt'] == 1 and
         type(value['artifact_id']) is int and value['artifact_id'] == UI_ARTIFACT_ID and
         value['artifact_sha256'] == UI_ARTIFACT_SHA256 and value['languages'] == ['en', 'zh-Hans', 'zh-Hant', 'he'] and
         value['composed_ui_reexecuted'] is False and value['service_failure_unresolved'] is True and
         value['full_app_acceptance'] is False and value['final_composition_acceptance'] is False, 'P2 parent UI identity or scope differs')
    digest(value['evidence_index_sha256'])


def approval(integration_root, metadata_root=None):
    digest(REVIEW_SHA256)  # Fail before any caller-controlled path is read.
    prior_root, prior, _ = historical.approval(integration_root)
    root = Path(metadata_root) if metadata_root is not None else Path(integration_root) / DIRECTORY
    raw = read(root, 'reviewed-approval.json')
    need(sha(raw) == REVIEW_SHA256, 'P2 independent review anchor differs')
    review = parse(raw)
    need(set(review) == {'schema_version', 'source_basis', 'integration_parent', 'inherited_p0_review_sha256', 'owners', 'files', 'native_validation'}, 'P2 approval schema differs')
    need(type(review['schema_version']) is int and review['schema_version'] == 1 and review['source_basis'] == BASIS and
         review['integration_parent'] == INTEGRATION_PARENT and review['inherited_p0_review_sha256'] == historical.REVIEW_SHA256, 'P2 inherited approval identity differs')
    required = SOURCE_NAMES | {'native-capture.json', 'parent-ui-basis.json'} | {'historical-p0/' + name for name in inherited_names(prior)}
    need(isinstance(review['files'], dict) and set(review['files']) == required, 'P2 exact metadata inventory differs')
    for name, expected in review['files'].items():
        need(sha(read(root, name)) == digest(expected), 'P2 sealed metadata drift: ' + name)
    for name in inherited_names(prior) - {'maintained-sources.json'}:
        need(read(root, 'historical-p0/' + name) == read(prior_root, name), 'P2 inherited P0 evidence changed')
    historical.validate_pins(parse(read(root, 'historical-p0/maintained-sources.json')), prior)
    need(review['owners'] == expected_owners(prior), 'P2 owner graph differs beyond exact LiveContainer successor')
    validate_source_metadata(root, prior_root, prior)
    capture = parse(read(root, 'native-capture.json')); validate_capture(capture, review['owners'])
    ui = parse(read(root, 'parent-ui-basis.json')); validate_ui_basis(ui)
    need(review['native_validation'] == {'source_basis': BASIS, 'readiness_scope': 'eligible_for_gated_full_build',
        'review_sha256': None, 'inherited_p0_review_sha256': historical.REVIEW_SHA256,
        'fresh_composed_capture_sha256': sha(read(root, 'native-capture.json')),
        'parent_ui_basis_sha256': sha(read(root, 'parent-ui-basis.json')),
        'tested_owners': capture['tested_owners'], 'scope': FRESH_SCOPE}, 'P2 readiness receipt differs')
    return root, review, capture


def validate_pins(pins, review):
    need(set(pins) == {'schema_version', 'integration_baseline', 'source_basis', 'contract_registry_sha256', 'owners', 'native_validation'}, 'P2 pin schema differs')
    need(type(pins['schema_version']) is int and pins['schema_version'] == 1 and pins['integration_baseline'] == historical.BASELINE and
         pins['source_basis'] == BASIS and pins['owners'] == {owner: {k: v for k, v in row.items() if k != 'tree'} for owner, row in review['owners'].items()} and
         pins['contract_registry_sha256'] == review['files']['compatibility-registry.json'] and
         pins['native_validation'] == {**review['native_validation'], 'review_sha256': REVIEW_SHA256}, 'P2 caller pins differ')
    return pins


def load_pins(document, integration_root, legacy):
    _, review, _ = approval(integration_root); validate_pins(document, review)
    need(all(row['path'] == legacy.PATHS[owner] for owner, row in document['owners'].items()), 'P2 source paths differ')
    return document


def contract_basis(pins, integration_root):
    root, review, _ = approval(integration_root); validate_pins(pins, review)
    return root, review['files']['compatibility-registry.json']


def contract_files(pins, integration_root, root=None):
    directory, review, _ = approval(integration_root, root); validate_pins(pins, review)
    return {'reviewed-approval.json': read(directory, 'reviewed-approval.json'), **{name: read(directory, name) for name in review['files']}}


def anisette_manifest(pins, integration_root):
    root, review, _ = approval(integration_root); validate_pins(pins, review)
    prior_pins = parse(read(root, 'historical-p0/maintained-sources.json'))
    manifest = historical.anisette_manifest(prior_pins, integration_root)
    need(parse(read(root, 'anisette-generated/maintained-source-manifest.json')) == manifest, 'P2 inherited AK evidence changed')
    return manifest  # Keep the original C++ basis. No AK source changed.


def git_delta(source, before, after):
    from prepare_candidate_inventory import entries, git
    old, new = entries(source, before), entries(source, after)
    return {'baseline_commit': before, 'commit': after, 'tree': git(source, 'rev-parse', after+'^{tree}').decode().strip(),
        'changes': {name: {'before': old.get(name), 'after': new.get(name)} for name in sorted(old.keys() | new.keys()) if old.get(name) != new.get(name)}}


def verify_all(workspace, pins, anisette, after_build, legacy):
    root, review, _ = approval(legacy.ROOT); validate_pins(pins, review)
    prior_root, prior, _ = historical.approval(legacy.ROOT)
    sys.path.insert(0, str(legacy.ROOT))
    from prepare_candidate_inventory import BASES, snapshot
    roots = {name: workspace / row['path'] for name, row in pins['owners'].items()}
    if anisette is not None:
        roots['AnisetteKit'] = Path(anisette)
    historical.verify_transitions({name: source for name, source in roots.items() if name != 'LiveContainer'}, prior, prior_root, legacy)
    host = roots['LiveContainer']
    need(git_delta(host, prior['owners']['LiveContainer']['commit'], LC_COMMIT) == parse(read(root, 'reviewed-source-delta.json')), 'P2 actual full source delta differs')
    need(git_delta(host, LC_PARENT, LC_COMMIT) == parse(read(root, 'composition-parent-delta.json')), 'P2 composition delta differs')
    need(legacy.git(host, 'rev-list', '--parents', '-n1', LC_COMMIT).decode().split() == [LC_COMMIT, LC_PARENT], 'P2 composition must be exact direct child')
    need(legacy.git(host, 'rev-parse', LC_PARENT+'^{tree}').decode().strip() == LC_PARENT_TREE, 'P2 UI parent tree differs')
    inventory = parse(read(root, 'source-inventory.json')); results = {}
    for name, source in roots.items():
        spec = pins['owners'][name]
        need(snapshot(source, BASES[name], spec['commit']) == inventory[name], 'P2 source inventory differs: ' + name)
        need(inventory[name]['tree'] == review['owners'][name]['tree'], 'P2 owner tree differs')
        results[name] = legacy.verify_checkout(source, spec['commit'], owner=name, after_build=after_build, require_full_history=True)
        results[name]['origin'] = legacy.verify_origin(source, spec['repository'], spec['commit'], Path(anisette).parent.parent if name == 'AnisetteKit' and anisette else None)
    legacy.verify_graph(roots, pins)
    for name, path in historical.LOCK_FILES.items():
        resolver = parse(read(prior_root, name+'-resolver.json'))
        source = roots[name]
        need(legacy.git(source, 'rev-parse', resolver['resolver_tested_commit']+'^{tree}').decode().strip() == resolver['resolver_tested_tree'], 'P2 inherited resolver source tree differs')
        need(sha(legacy.git(source, 'show', resolver['resolver_tested_commit']+':'+path)) == resolver['lock_before_sha256'], 'P2 inherited lock before differs')
        need(sha(read(source, path)) == resolver['lock_sha256'], 'P2 unchanged genuine lock bytes differ')
        need(set(snapshot(source, resolver['resolver_tested_commit'], pins['owners'][name]['commit'])['changes']) <= {path}, 'P2 resolver descendant runtime differs')
        lock = parse(read(source, path))
        need(resolver['origin_hash'] == {'present': 'originHash' in lock, 'value': lock.get('originHash')}, 'P2 inherited originHash differs')
    verify_children(roots, child_map(root), legacy)
    command = [sys.executable, '-B', str(root/'validate_contracts.py'), '--registry', str(root/'compatibility-registry.json'), '--registry-sha256', review['files']['compatibility-registry.json']]
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
    validate_pins(pins, review)
    children = child_map(root)
    seen = set()
    def fetch(path, row, prefix):
        need(not path.is_symlink() and (not path.exists() or (path.is_dir() and not list(path.iterdir()))), 'P2 refuses nonfresh source target')
        for parent in path.parents:
            if parent == workspace:
                break
            need(not parent.is_symlink(), 'P2 linked source parent')
        path.parent.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL='https', GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
        subprocess.run(['git', '--no-replace-objects', '-c', 'core.hooksPath='+os.devnull, 'clone', '--no-checkout', row['repository'], str(path)], check=True, env=env)
        legacy.git(path, 'fetch', '--no-tags', '--no-recurse-submodules', 'origin', row['commit'])
        legacy.git(path, 'checkout', '--detach', row['commit'])
        legacy.verify_origin(path, row['repository'], row['commit'])
        need(legacy.git(path, 'rev-parse', 'HEAD^{tree}').decode().strip() == row['tree'], 'P2 acquired tree differs')
        modules = configparser.ConfigParser(interpolation=None)
        if (path/'.gitmodules').exists():
            modules.read(path/'.gitmodules')
        for relative_path, (mode, kind, oid) in legacy.entries(path).items():
            if kind != 'commit':
                continue
            relative(relative_path)
            key = prefix+'/'+relative_path
            need(mode == '160000' and key in children, 'P2 unreviewed child gitlink')
            child = children[key]
            declarations = [dict(modules[section]) for section in modules.sections() if modules[section].get('path') == relative_path]
            need(declarations == [{'path': relative_path, 'url': child['declared_repository']}] and oid == child['commit'], 'P2 child URL/gitlink differs before fetch')
            seen.add(key)
            fetch(path/relative_path, child, key)
    for owner in ('LiveContainer', 'SideStore', 'AnisetteKit', 'idevice', 'jktcp'):
        row = review['owners'][owner]
        path = workspace/pins['owners'][owner]['path']
        need(not path.exists(), 'P2 owner requires fresh target')
        fetch(path, row, owner)
    need(seen == set(children), 'P2 reviewed child closure differs')
