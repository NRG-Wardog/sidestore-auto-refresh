"""Additive C++ allocation admission; old reviews remain historical and immutable.

No build eligibility exists until independent review binds exact published source,
real resolver output and fresh ordinary platform evidence. No guest is executed.
"""
import copy
import os
from pathlib import Path
import subprocess
import sys

import p0_elf_metadata_bounds as historical
from p0_maintained_sources import need, sha, digest, fullsha, relative, read, parse, run_identity, child_map, verify_children

BASIS = 'p0-cxx-allocation-v1'
DIRECTORY = 'migration/' + BASIS
BASELINE = historical.BASELINE
HELPER_SOURCES = historical.HELPER_SOURCES
METADATA_EXPECTED = historical.METADATA_EXPECTED
validate_helper = historical.validate_helper
INTEGRATION_PARENT = 'c477b44ad47f5e87e6a05b16dfb165c0065adc73'
REVIEW_SHA256 = 'd9612e716a535a12b5e2d9fcb0adeb3dfbc3b25f6d824efda4b50d426f5a3df4'  # Independent review of exact genuine successor evidence.
AK_COMMIT = '161435bcc0344e1a9853669455c1ca8d9dda2bf1'
AK_TREE = '0df50020ddd774a5b7ab941944501f1ecd591a4a'
MANIFEST_SHA256 = 'f64479b0daa40210864e5f9c7f5290e42b1ce03eee4fde50858714db2cb335df'
CHANGED_OWNERS = {'AnisetteKit', 'SideSign', 'SideStore'}
LOCK_FILES = historical.LOCK_FILES
RUN_FIELDS = historical.RUN_FIELDS
CONSUMER_SUITE_IDS = historical.CONSUMER_SUITE_IDS
REQUIRED_DIAGNOSTIC_LITERALS = historical.REQUIRED_DIAGNOSTIC_LITERALS
exact_counts = historical.exact_counts
PARENTS = {
    'AnisetteKit': historical.AK_COMMIT,
    'LiveContainer': '1df50257f6255522e6ff48db226508fe74d6b17d',
    'SideSign': '74a79cc4d1e6018696c39dfe975dfd13c449870b',
    'SideStore': '6e9f367d441dd48c3519f3b39d93acdeffd43c1e',
    'idevice': '803f9368e6b27a43d8a9f22dc0ec281c87883113',
    'jktcp': '32c0210f286b4fd16efe05be4f57167a9d94459b',
    'minimuxer': 'efbcab05d7d636aa37c6bf6c7f364d122c5610f6',
}
REVIEWED_RUNTIME = {
    'Native/Loader/elf_loader_emulator.cpp': '44a8b61dcf1d4d8d309f624c442c008278d33a68ccf14ec4dc9f4757d9cdce2a',
    'Native/Loader/cxx_allocation_runtime.h': 'b76bdc4043c497700f93aff5ad82968f7660ebb6720bb2c71ea5615000380a62',
}
ALLOCATION_HELPER_SOURCES = {'Native/Loader/elf_loader_emulator.cpp': '44a8b61dcf1d4d8d309f624c442c008278d33a68ccf14ec4dc9f4757d9cdce2a', '.ci/native-tests/fixtures/cxx_allocation_unit.cpp': 'cbbe243428144a0584593246a4d4b92d00ee9807cff457404d8d3a18d491680c', '.ci/native-tests/fixtures/cxx_allocation_hook_unit.cpp': '0614c634f0d8ca648bde35fde4d55ec07f657becb8b2a5cd1d78a92d7a0f1c62', 'Native/Loader/cxx_allocation_runtime.h': 'b76bdc4043c497700f93aff5ad82968f7660ebb6720bb2c71ea5615000380a62', 'Native/Loader/elf_loader_emulator.h': '16eb67e76dc4ae94350e55968e9313c2f4163817182c08af76a5c85b2ec09327', 'Native/Loader/elf_metadata_bounds.h': '7acd9287cc351b55141f40a0baecda9c3d3202a420cf4b7f0f2515572d877253', 'Native/Loader/android_tls_runtime.h': '51c6c123d78b2d53f70e1720c8bf8aa934cbdb149a66df0a6763b6614f4fda07', 'Native/Loader/vm_initialization_diagnostics.h': '023b34540c9fb31fcb6059fea3236eb411fc52d0438025dbe9d72e88c9337e5c', 'Native/Loader/adi_consumption_debug.h': '077b377c87b201893f83afbff5a4dd37e01354253f08045a3831adcb50022346', 'Native/anisette_base.h': '6c47c3fef881d99082d1eb7e687fc78de6339568118259884d2b8a0d6c6e8664', 'Native/include/anisette_core.h': 'ca1ea4005f77dcca17c68965ba2dd1f65596e282a57adb4277f1d90d8bd11f02', '.ci/native-tests/test_cxx_allocation.py': 'c0f2c97e40be67ac381792a9ebae60dbdb1363e0517741b1cbfa90f0d342d3d7'}
ALLOCATION_FILES = set(REVIEWED_RUNTIME) | {
    '.ci/native-tests/test_cxx_allocation.py',
    '.ci/native-tests/fixtures/cxx_allocation_unit.cpp',
    '.ci/native-tests/fixtures/cxx_allocation_hook_unit.cpp',
    '.ci/native-tests/CXX_ALLOCATION_REVIEW.md',
    '.ci/native-tests/test_constructor_diagnostics.py',
    '.ci/native-tests/test_android_tls_runtime.py',
}


def validate_allocation_helper(value, sources):
    need(isinstance(value, dict) and set(value) == {'results', 'guest_execution', 'leak_sanitizer_enabled', 'source_sha256'}, 'allocation helper schema differs')
    need(value['source_sha256'] == sources == ALLOCATION_HELPER_SOURCES, 'allocation helper source identity differs')
    need(all(sources[name] == expected for name, expected in REVIEWED_RUNTIME.items()), 'allocation production source differs')
    need(value['guest_execution'] is False and value['leak_sanitizer_enabled'] is False, 'allocation helper unsafe execution claim')
    need(isinstance(value['results'], dict) and set(value['results']) == {'normal', 'optimized', 'sanitized'}, 'three allocation variants required')
    for row in value['results'].values():
        need(isinstance(row, dict) and set(row) == {'allocator', 'hooks'}, 'allocator and actual-hook checks required')
        for name, count in [('allocator', 32), ('hooks', 51)]:
            expected = {'checks': count, 'failures': 0, 'guest_execution': False}
            need(row[name] == expected and all(type(row[name][k]) is type(v) for k, v in expected.items()), 'allocation check counts differ')
    return value


def verify_manifest_pin(before, after):
    old = PARENTS['AnisetteKit'].encode()
    new = fullsha(AK_COMMIT).encode()
    need(before.count(old) == 1 and before.replace(old, new) == after, 'C++ SideSign changed beyond exact AK revision pin')


def validate_delta(transitions, review, manifest):
    need(set(transitions) == set(PARENTS), 'C++ exact seven-owner transition required')
    ak = transitions['AnisetteKit']
    need(review['owners']['AnisetteKit']['commit'] == fullsha(AK_COMMIT) and review['owners']['AnisetteKit']['tree'] == fullsha(AK_TREE), 'C++ exact AK tuple required')
    need(manifest['base'] == PARENTS['AnisetteKit'] and manifest['commit'] == AK_COMMIT and manifest['tree'] == AK_TREE, 'C++ reviewed manifest identity differs')
    need(set(manifest['changes']) == ALLOCATION_FILES and ak['changes'] == manifest['changes'], 'C++ exact eight-file source transition required')
    for name, expected in REVIEWED_RUNTIME.items():
        need(ak['changes'][name]['after']['sha256'] == expected, 'C++ reviewed runtime bytes differ')
    need(set(transitions['SideSign']['changes']) == {'Package.swift', LOCK_FILES['SideSign']}, 'C++ SideSign must contain only exact manifest and real lock cascade')
    need(set(transitions['SideStore']['changes']) == {'Dependencies/SideSign', LOCK_FILES['SideStore']}, 'C++ SideStore must contain only exact gitlink and real lock cascade')
    need(transitions['SideStore']['changes']['Dependencies/SideSign'] == {
        'before': {'mode': '160000', 'type': 'commit', 'git_object': PARENTS['SideSign']},
        'after': {'mode': '160000', 'type': 'commit', 'git_object': review['owners']['SideSign']['commit']}}, 'C++ exact SideSign gitlink differs')
    for owner in set(PARENTS) - CHANGED_OWNERS:
        need(not transitions[owner]['changes'] and review['owners'][owner]['commit'] == PARENTS[owner], 'C++ unrelated owner changed')


def validate_transitions(root, review, prior_root, prior, manifest):
    transitions = parse(read(root, 'source-transition.json'))
    inventory = parse(read(root, 'source-inventory.json'))
    previous = parse(read(prior_root, 'source-inventory.json'))
    validate_delta(transitions, review, manifest)
    need(set(inventory) == set(PARENTS), 'C++ inventory incomplete')
    for owner, transition in transitions.items():
        row = review['owners'][owner]
        need(set(transition) == {'baseline_commit', 'commit', 'tree', 'changes'} and
             transition['baseline_commit'] == PARENTS[owner] and transition['commit'] == row['commit'] and transition['tree'] == row['tree'], 'C++ transition identity differs')
        composed = copy.deepcopy(previous[owner])
        composed.update(commit=row['commit'], tree=row['tree'])
        for name, change in transition['changes'].items():
            relative(name)
            need(isinstance(change, dict) and set(change) == {'before', 'after'} and change['before'] != change['after'], 'C++ source change malformed')
            for record in change.values():
                if record is None:
                    continue
                need(isinstance(record, dict) and set(record) == ({'mode', 'type', 'git_object', 'sha256'} if record.get('type') == 'blob' else {'mode', 'type', 'git_object'}), 'C++ source record malformed')
                fullsha(record['git_object'])
                need((record['type'] == 'blob' and record['mode'] in ('100644', '100755', '120000')) or
                     (record['type'] == 'commit' and record['mode'] == '160000'), 'C++ source mode/type malformed')
                if record['type'] == 'blob':
                    digest(record['sha256'])
            old = composed['changes'].get(name)
            if old is not None:
                need(change['before'] == old['after'], 'C++ before differs from delivered bounds source')
                change = {'before': old['before'], 'after': change['after']}
            if change['before'] == change['after']:
                composed['changes'].pop(name, None)
            else:
                composed['changes'][name] = change
        need(inventory[owner] == composed, 'C++ composed source inventory differs')


def approval(integration_root, metadata_root=None):
    digest(REVIEW_SHA256)  # Deliberately fails before reading caller-controlled metadata.
    prior_root, prior, prior_receipt = historical.approval(integration_root)
    root = Path(metadata_root) if metadata_root is not None else Path(integration_root) / DIRECTORY
    raw = read(root, 'reviewed-approval.json')
    need(sha(raw) == REVIEW_SHA256, 'C++ independent review anchor differs')
    value = parse(raw)
    need(set(value) == {'schema_version', 'source_basis', 'integration_parent', 'inherited_bounds_review_sha256', 'owners', 'files', 'native_validation'}, 'C++ review schema differs')
    need(type(value['schema_version']) is int and value['schema_version'] == 1 and value['source_basis'] == BASIS and
         value['integration_parent'] == INTEGRATION_PARENT and value['inherited_bounds_review_sha256'] == historical.REVIEW_SHA256, 'C++ review basis differs')
    required = {'source-transition.json', 'source-inventory.json', 'submodules.json', 'compatibility-registry.json',
                'provenance/source-evidence.json', 'validate_contracts.py', 'anisette-generated/maintained-source-manifest.json',
                'reviewed-ak-manifest.json', 'historical-evidence.json', 'allocation-helper.json', 'allocation-helper-run.json',
                'metadata-helper.json', 'native-receipt.json', 'historical-bounds-approval.json', 'historical-bounds-pins.json'}
    required |= {'owners/' + owner + '/runtime-contract.json' for owner in PARENTS}
    required |= {owner + '-resolver.json' for owner in LOCK_FILES}
    required |= {'resolver-locks/' + owner + '.Package.resolved.' + suffix for owner in LOCK_FILES for suffix in ('before', 'after')}
    need(isinstance(value['files'], dict) and set(value['files']) == required, 'C++ exact metadata inventory differs')
    for name, expected in value['files'].items():
        need(sha(read(root, name)) == digest(expected), 'C++ sealed metadata drift: ' + name)
    need(read(root, 'historical-bounds-approval.json') == read(prior_root, 'reviewed-approval.json'), 'C++ historical bounds review changed')
    historical.validate_pins(parse(read(root, 'historical-bounds-pins.json')), prior)
    for name in ['validate_contracts.py', *('owners/' + owner + '/runtime-contract.json' for owner in set(PARENTS) - {'AnisetteKit'})]:
        need(read(root, name) == read(prior_root, name), 'C++ unrelated contract changed: ' + name)
    need(set(value['owners']) == set(PARENTS), 'C++ seven-owner graph required')
    for owner, row in value['owners'].items():
        previous = prior['owners'][owner]
        need(set(row) == set(previous) and all(row[k] == previous[k] for k in ('repository', 'source_checkpoint', 'path')) and previous['commit'] == PARENTS[owner], 'C++ owner origin/checkpoint differs')
        fullsha(row['commit']); fullsha(row['tree'])
        if owner not in CHANGED_OWNERS:
            need(row == previous, 'C++ unrelated owner changed')
    manifest_raw = read(root, 'reviewed-ak-manifest.json')
    need(sha(manifest_raw) == digest(MANIFEST_SHA256), 'C++ exact reviewed AK manifest differs')
    manifest = parse(manifest_raw)
    validate_transitions(root, value, prior_root, prior, manifest)
    native = value['native_validation']
    need(set(native) == (set(prior['native_validation']) - {'inherited_constructor_review_sha256'}) | {'inherited_bounds_review_sha256'}, 'C++ native schema differs')
    need(native['source_basis'] == BASIS and native['inherited_bounds_review_sha256'] == historical.REVIEW_SHA256 and
         native['readiness_scope'] == 'eligible_for_gated_full_build' and native['review_sha256'] is None, 'C++ native review identity differs')
    run_identity(native)
    need(native['run_url'] != prior['native_validation']['run_url'] and native['artifact_id'] != prior['native_validation']['artifact_id'], 'historical native evidence cannot prove C++ successor')
    need(set(native['tested_owners']) == set(PARENTS), 'C++ tested graph incomplete')
    for owner, tested in native['tested_owners'].items():
        need(set(tested) == {'commit', 'tree'}, 'C++ tested owner schema differs')
        fullsha(tested['commit']); fullsha(tested['tree'])
        if owner not in LOCK_FILES:
            need(tested == {k: value['owners'][owner][k] for k in ('commit', 'tree')}, 'C++ owner changed after capture')
    children = child_map(root)
    old_children = child_map(prior_root)
    need(set(children) == set(old_children), 'C++ child closure differs')
    for name, child in children.items():
        if name == 'SideStore/Dependencies/SideSign':
            row = value['owners']['SideSign']
            need(child == {'repository': row['repository'], 'declared_repository': row['repository'], 'commit': row['commit'], 'tree': row['tree']}, 'C++ SideSign child differs')
        else:
            need(child == old_children[name], 'C++ unrelated child changed')
    need(native['tested_children'] == {'SideSign': {}, 'SideStore': {'Dependencies/SideSign': value['owners']['SideSign']['commit'], 'Dependencies/minimuxer': value['owners']['minimuxer']['commit']}}, 'C++ tested children differ')
    receipt = parse(read(root, 'native-receipt.json'))
    need(set(receipt) == set(prior_receipt) | {'allocation_helper'}, 'C++ native receipt schema differs')
    need(receipt['status'] == 'INDEPENDENTLY_REVIEWED_P0_CXX_ALLOCATION_NATIVE_PASS' and receipt['source_basis'] == BASIS and
         receipt['native_validation'] == native and receipt['native_builds'] == prior_receipt['native_builds'] and receipt['tested_submodules'] == children and
         all(receipt[key] is False for key in ('guest_execution', 'ADI_executed', 'device_validation')), 'C++ native receipt unsupported claim')
    historical_evidence = {'role': 'historical-only', 'review_sha256': historical.REVIEW_SHA256,
                           'native_receipt_sha256': sha(read(prior_root, 'native-receipt.json')),
                           'fresh_guest_execution': False, 'full_loader_changed': True}
    need(parse(read(root, 'historical-evidence.json')) == receipt['historical_evidence'] == historical_evidence, 'C++ old native evidence relabeled')
    helper = parse(read(root, 'allocation-helper.json'))
    helper_run = parse(read(root, 'allocation-helper-run.json'))
    run_identity(helper_run)
    need(helper_run['source_commit'] == AK_COMMIT and helper_run['source_tree'] == AK_TREE and helper_run['platform'] == 'linux', 'C++ helper run source/platform differs')
    need(helper_run['run_url'] not in {native['run_url'], prior['native_validation']['run_url']}, 'C++ helper run identity reused')
    validate_allocation_helper(helper, helper_run['source_sha256'])
    need(receipt['allocation_helper'] == {'source_owner': AK_COMMIT, 'source_tree': AK_TREE,
        'receipt_sha256': sha(read(root, 'allocation-helper.json')), 'run_sha256': sha(read(root, 'allocation-helper-run.json')),
        'checks_per_variant': 83, 'variants': ['normal', 'optimized', 'sanitized'], 'skipped': 0}, 'C++ helper receipt binding differs')
    historical.validate_helper(parse(read(root, 'metadata-helper.json')))
    need(receipt['metadata_helper'] == {'source_owner': AK_COMMIT, 'source_tree': AK_TREE, 'receipt_sha256': sha(read(root, 'metadata-helper.json')),
        'checks_per_variant': 713, 'variants': ['normal', 'optimized', 'sanitized'], 'skipped': 0}, 'C++ metadata helper binding differs')
    need(receipt['ordinary_native_boundary'] == {'checks': 8, 'failures': 0, 'skipped': 0}, 'C++ native boundary counts differ')
    exact_counts(receipt['ordinary_native_boundary'], {'checks': 8, 'failures': 0, 'skipped': 0}, 'C++ native boundary')
    suites = receipt['consumer_suites']
    need(set(suites) == {'tested_owners', *CONSUMER_SUITE_IDS} and suites['tested_owners'] == {owner: native['tested_owners'][owner] for owner in ('AnisetteKit', 'SideStore', 'LiveContainer')}, 'C++ consumer source graph differs')
    for name, ids in CONSUMER_SUITE_IDS.items():
        row = suites[name]
        need(set(row) == {'tests', 'failures', 'errors', 'skipped', 'test_ids'} and row['test_ids'] == list(ids), 'C++ consumer IDs differ')
        exact_counts(row, {'tests': len(ids), 'failures': 0, 'errors': 0, 'skipped': 0}, 'C++ consumer ' + name)
    need(set(receipt['phase_evidence']) == set(LOCK_FILES), 'C++ resolver phases incomplete')
    for owner in LOCK_FILES:
        resolver = parse(read(root, owner + '-resolver.json'))
        run_identity(resolver)
        old_resolver = parse(read(prior_root, owner + '-resolver.json'))
        need(resolver['run_url'] != old_resolver['run_url'] and resolver['artifact_id'] != old_resolver['artifact_id'], 'historical lock capture reused')
        need(resolver.get('status') == 'CAPTURED_FOR_REVIEW' and resolver.get('purpose') == 'independently_reviewed_p0_cxx_allocation_swiftpm_resolution' and
             resolver.get('owner') == owner and resolver.get('source_basis') == BASIS and resolver.get('anisette_commit') == AK_COMMIT, 'C++ resolver identity differs')
        phase = receipt['phase_evidence'][owner]
        linked = (*RUN_FIELDS, 'resolver_tested_commit', 'resolver_tested_tree', 'lock_before_sha256', 'lock_sha256', 'origin_hash')
        need(set(phase) == {'status', *linked} and phase['status'] == 'CAPTURED_FOR_REVIEW' and all(phase[key] == resolver[key] for key in linked), 'C++ resolver/native linkage differs')
        for key in ('resolver_tested_commit', 'resolver_tested_tree'):
            fullsha(resolver[key])
        for suffix, key in [('before', 'lock_before_sha256'), ('after', 'lock_sha256')]:
            need(sha(read(root, 'resolver-locks/' + owner + '.Package.resolved.' + suffix)) == digest(resolver[key]), 'C++ genuine lock bytes differ')
        lock = parse(read(root, 'resolver-locks/' + owner + '.Package.resolved.after'))
        need(resolver['origin_hash'] == {'present': 'originHash' in lock, 'value': lock.get('originHash')}, 'C++ resolver originHash differs')
        if owner == 'SideStore':
            need(all(phase[key] == native[key] for key in RUN_FIELDS) and native['tested_owners'][owner] == {'commit': resolver['resolver_tested_commit'], 'tree': resolver['resolver_tested_tree']}, 'C++ final graph capture differs')
    registry = parse(read(root, 'compatibility-registry.json'))
    old_registry = parse(read(prior_root, 'compatibility-registry.json'))
    need(registry['contracts'] == old_registry['contracts'] and registry['edges'] == old_registry['edges'], 'C++ public contracts changed')
    refs = {registry['evidence']['path']: registry['evidence']['sha256'], **{row['manifest_path']: row['manifest_sha256'] for row in registry['owners'].values()}}
    need(set(registry['owners']) == set(PARENTS) and all(value['files'].get(name) == expected for name, expected in refs.items()), 'C++ contract hashes not sealed')
    return root, value, receipt


def validate_pins(document, review):
    need(set(document) == {'schema_version', 'integration_baseline', 'source_basis', 'contract_registry_sha256', 'owners', 'native_validation'}, 'C++ pin schema differs')
    need(type(document['schema_version']) is int and document['schema_version'] == 1 and document['integration_baseline'] == historical.BASELINE and document['source_basis'] == BASIS, 'C++ pin basis differs')
    need(document['owners'] == {owner: {k: v for k, v in row.items() if k != 'tree'} for owner, row in review['owners'].items()} and
         document['contract_registry_sha256'] == review['files']['compatibility-registry.json'] and
         document['native_validation'] == {**review['native_validation'], 'review_sha256': REVIEW_SHA256}, 'C++ caller pins differ')
    return document


def load_pins(document, integration_root, legacy):
    _, review, _ = approval(integration_root)
    validate_pins(document, review)
    need(all(row['path'] == legacy.PATHS[owner] for owner, row in document['owners'].items()), 'C++ owner paths differ')
    return document


def contract_basis(pins, integration_root):
    root, review, _ = approval(integration_root)
    validate_pins(pins, review)
    return root, review['files']['compatibility-registry.json']


def contract_files(pins, integration_root, root=None):
    directory, review, _ = approval(integration_root, root)
    validate_pins(pins, review)
    return {'reviewed-approval.json': read(directory, 'reviewed-approval.json'), **{name: read(directory, name) for name in review['files']}}


def anisette_manifest(pins, integration_root):
    root, review, _ = approval(integration_root)
    validate_pins(pins, review)
    manifest = parse(read(root, 'anisette-generated/maintained-source-manifest.json'))
    prior_root, _, _ = historical.approval(integration_root)
    prior = parse(read(prior_root, 'anisette-generated/maintained-source-manifest.json'))
    need(manifest.get('source_basis') == BASIS and manifest.get('anisettekit_revision') == AK_COMMIT and manifest.get('anisettekit_tree') == AK_TREE, 'C++ manifest identity differs')
    rows = manifest.get('files')
    need(isinstance(rows, list) and len({row['path'] for row in rows}) == len(rows), 'C++ source inventory malformed')
    by_name = {row['path']: row for row in rows}
    need({row['path'] for row in prior['files']} | ALLOCATION_FILES <= set(by_name), 'C++ inherited/new sources missing')
    for row in rows:
        relative(row['path']); digest(row['sha256'])
    for name, expected in REVIEWED_RUNTIME.items():
        need(by_name[name]['sha256'] == expected, 'C++ source manifest differs from reviewed source')
    need(manifest.get('required_compiled_evidence') == prior['required_compiled_evidence'], 'C++ inherited compiled literals changed')
    return manifest

def verify_transitions(roots, review, root, legacy):
    sys.path.insert(0,str(legacy.ROOT))
    from prepare_candidate_inventory import snapshot
    transitions = parse(read(root,'source-transition.json'))
    for owner, source in roots.items():
        need(snapshot(source,PARENTS[owner],review['owners'][owner]['commit']) == transitions[owner], 'actual C++ transition differs')
        ancestry = legacy.git(source,'rev-list','--parents',PARENTS[owner]+'..'+review['owners'][owner]['commit']).decode().splitlines()
        need(all(len(line.split()) == 2 for line in ancestry), 'C++ transition merged another lane')
        legacy.git(source,'merge-base','--is-ancestor',PARENTS[owner],review['owners'][owner]['commit'])


def verify_all(workspace, pins, anisette, after_build, legacy):
    root, review, _ = approval(legacy.ROOT)
    validate_pins(pins, review)
    sys.path.insert(0, str(legacy.ROOT))
    from prepare_candidate_inventory import BASES, snapshot
    inventory = parse(read(root, 'source-inventory.json'))
    need(set(inventory) == set(PARENTS), 'P0 final source inventory incomplete')
    roots = {name: workspace/row['path'] for name, row in pins['owners'].items()}
    if anisette is not None:
        roots['AnisetteKit'] = Path(anisette)
    verify_transitions(roots, review, root, legacy)
    need(legacy.git(roots['AnisetteKit'], 'rev-list', '--parents', '--max-count=1', AK_COMMIT).decode().split() == [AK_COMMIT, PARENTS['AnisetteKit']], 'C++ AK must be a direct reviewed successor')
    verify_manifest_pin(legacy.git(roots['SideSign'],'show',PARENTS['SideSign']+':Package.swift'), legacy.git(roots['SideSign'],'show',review['owners']['SideSign']['commit']+':Package.swift'))
    results = {}
    for name, source in roots.items():
        spec = pins['owners'][name]
        actual = snapshot(source, BASES[name], spec['commit'])
        need(actual == inventory[name] and actual['tree'] == review['owners'][name]['tree'], 'P0 final source inventory differs: '+name)
        results[name] = legacy.verify_checkout(source, spec['commit'], owner=name, after_build=after_build, require_full_history=True)
        results[name]['origin'] = legacy.verify_origin(source, spec['repository'], spec['commit'], Path(anisette).parent.parent if name == 'AnisetteKit' and anisette else None)
        tested = review['native_validation']['tested_owners'][name]
        need(legacy.git(source, 'rev-parse', tested['commit']+'^{tree}').decode().strip() == tested['tree'], 'P0 tested tree differs')
        if name in LOCK_FILES:
            need(set(snapshot(source, tested['commit'], spec['commit'])['changes']) <= {LOCK_FILES[name]}, 'P0 runtime changed after native capture')
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
