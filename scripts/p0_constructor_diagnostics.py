"""Independent P0 constructor admission, reconstructed after workspace loss.

Historical P0 evidence is immutable. An external independent review digest is
required before any caller-supplied pins or packaged metadata can be accepted.
"""
import copy
import os
from pathlib import Path
import subprocess
import sys
import p0_maintained_sources as p0
from p0_maintained_sources import need, sha, digest, fullsha, relative, read, parse, run_identity, child_map, verify_children

BASIS = 'p0-constructor-diagnostics-v1'
DIRECTORY = 'migration/p0-constructor-diagnostics-v1'
INTEGRATION_PARENT = '7e5062114f3ba8c1fe1990389ae9a008ce4d8c1f'
DIAGNOSTIC_REVIEW_SHA256 = '940879e3a378019d27a8ceaa014b9b31584229ed3220772b94cd1e44f73fa8ec'
PARENTS = {
    'AnisetteKit': '0dfcc89aa93411d84f7c50beb3c75599af89e852',
    'SideSign': '081994b142c781f9a32bbddfa7277f305b20447b',
    'SideStore': '0e6a626ae97401fb671ee12ae88e4f364fc2a31c',
    'LiveContainer': '00546cec351adad1fa1cad5590da4f06e612db8f',
    'minimuxer': 'efbcab05d7d636aa37c6bf6c7f364d122c5610f6',
    'idevice': '803f9368e6b27a43d8a9f22dc0ec281c87883113',
    'jktcp': '32c0210f286b4fd16efe05be4f57167a9d94459b',
}
CHANGED_OWNERS = {'AnisetteKit', 'SideSign', 'SideStore', 'LiveContainer'}
LOCK_FILES = {'SideSign': 'Package.resolved', 'SideStore': 'AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved'}
RUN_FIELDS = ('run_url', 'run_attempt', 'host_commit', 'host_tree', 'artifact_id', 'artifact_sha256', 'approved_inputs_sha256', 'evidence_index_sha256')
REQUIRED_DIAGNOSTIC_CHECKS = 2105
REQUIRED_DIAGNOSTIC_CASES = ('normal_nonzero_w0', 'normal_error_shaped_w0', 'protected_access', 'unsupported_interrupt',
    'abnormal_stop', 'dt_init_failure', 'dt_init_array_first_failure', 'dt_init_array_second_failure',
    'array_entry_read_first', 'array_entry_read_second', 'invalid_vm', 'poisoned_vm',
    'caller_snapshot_independence', 'serialization_bounds', 'serialization_privacy')
REQUIRED_DIAGNOSTIC_LITERALS = ('Library constructor execution failed [v1;kind=',)
CONSUMER_SUITE_IDS = {
 'init_diagnostics': tuple('test_contract.InitializerDiagnosticContract.'+name for name in (
    'test_actual_swift_parser_service_wire_and_host_copy', 'test_bounded_primary_only_contract_and_no_recovery_mutation',
    'test_real_native_producer_fixture', 'test_shared_capture_validation_and_copy_contracts_match',
    'test_trace_disabled_preserves_constructor_evidence')),
 'adi_consumption': tuple('test_contract.ConsumerContractTests.'+name for name in (
    'test_disabled_trace_preserves_classification_and_drops_diagnostics',
    'test_legacy_comparison_is_read_only_snapshot_bound_and_failure_scoped',
    'test_native_pipeline_wire_copy_and_importing_host', 'test_real_cpp_producer_descriptor_and_pinned_schema',
    'test_shared_parser_and_schema_are_exact_across_owners', 'test_wire_and_render_use_real_finite_contract')),
}


def exact_counts(value, expected, label):
    need(isinstance(value, dict) and set(expected) <= set(value), label+' count fields missing')
    need(all(type(value[k]) is int and value[k] == n for k, n in expected.items()), label+' counts differ')


def base_pins(integration_root):
    _, review, _ = p0.approval(integration_root)
    return {'schema_version': 1, 'integration_baseline': '141776ba6ba38fc04a5e77f68b0cfc4e6c8842ee',
        'source_basis': p0.BASIS, 'contract_registry_sha256': review['files']['compatibility-registry.json'],
        'owners': {owner: {k: v for k, v in row.items() if k != 'tree'} for owner, row in review['owners'].items()},
        'native_validation': {**review['native_validation'], 'review_sha256': p0.P0_REVIEW_SHA256}}


def validate_transitions(root, review, prior_root, prior):
    transitions = parse(read(root, 'source-transition.json'))
    inventory = parse(read(root, 'source-inventory.json'))
    previous = parse(read(prior_root, 'source-inventory.json'))
    need(set(transitions) == set(inventory) == set(PARENTS), 'diagnostic exact source inventory required')
    for owner, transition in transitions.items():
        row = review['owners'][owner]
        need(set(transition) == {'baseline_commit', 'commit', 'tree', 'changes'} and
             transition['baseline_commit'] == PARENTS[owner] and transition['commit'] == row['commit'] and
             transition['tree'] == row['tree'] and isinstance(transition['changes'], dict), 'diagnostic source transition identity differs')
        composed = copy.deepcopy(previous[owner])
        composed.update(commit=row['commit'], tree=row['tree'])
        if owner not in CHANGED_OWNERS:
            need(not transition['changes'], 'unrelated owner transition forbidden')
        else:
            need(bool(transition['changes']), 'diagnostic successor has no source delta')
        for path, change in transition['changes'].items():
            relative(path)
            need(isinstance(change, dict) and set(change) == {'before', 'after'} and
                 change['before'] != change['after'], 'diagnostic source change malformed')
            for record in change.values():
                if record is not None:
                    need(isinstance(record, dict) and set(record) == ({'mode', 'type', 'git_object', 'sha256'} if record.get('type') == 'blob' else {'mode', 'type', 'git_object'}), 'diagnostic source record malformed')
                    fullsha(record['git_object'])
                    need((record['type'] == 'blob' and record['mode'] in ('100644', '100755', '120000')) or
                         (record['type'] == 'commit' and record['mode'] == '160000'), 'diagnostic source mode/type malformed')
                    if record['type'] == 'blob': digest(record['sha256'])
            old = composed['changes'].get(path)
            if old is not None:
                need(change['before'] == old['after'], 'transition before differs from delivered P0')
                change = {'before': old['before'], 'after': change['after']}
            if change['before'] == change['after']:
                composed['changes'].pop(path, None)
            else:
                composed['changes'][path] = change
        need(inventory[owner] == composed, 'source inventory differs from exact P0 transition')


def approval(integration_root, metadata_root=None):
    digest(DIAGNOSTIC_REVIEW_SHA256)
    prior_root, prior, historical = p0.approval(integration_root)
    root = Path(metadata_root) if metadata_root is not None else Path(integration_root)/DIRECTORY
    raw = read(root, 'reviewed-approval.json')
    need(sha(raw) == DIAGNOSTIC_REVIEW_SHA256, 'diagnostic independent review anchor differs')
    value = parse(raw)
    need(set(value) == {'schema_version', 'source_basis', 'integration_parent', 'inherited_p0_review_sha256', 'owners', 'files', 'native_validation'}, 'diagnostic review schema differs')
    need(type(value['schema_version']) is int and value['schema_version'] == 1 and value['source_basis'] == BASIS and
         value['integration_parent'] == INTEGRATION_PARENT and value['inherited_p0_review_sha256'] == p0.P0_REVIEW_SHA256,
         'diagnostic review basis differs')
    inherited = {'inherited/p0-native-basis-v1/'+name for name in ['reviewed-approval.json', *prior['files']]}
    required = set(prior['files']) | inherited | {'inherited/p0-pins.json', 'source-transition.json'}
    need(isinstance(value['files'], dict) and set(value['files']) == required, 'diagnostic exact metadata inventory required')
    for name, expected in value['files'].items():
        need(sha(read(root, name)) == digest(expected), 'diagnostic metadata drift: '+name)
    for name in ['reviewed-approval.json', *prior['files']]:
        need(read(root, 'inherited/p0-native-basis-v1/'+name) == read(prior_root, name), 'inherited P0 evidence changed')
    need(parse(read(root, 'inherited/p0-pins.json')) == base_pins(integration_root), 'inherited P0 pins differ')
    for name in ('historical-inventory-references.json', 'p0-frozen-payload-manifest.json', 'p0-native-owner-identities.json', 'validate_contracts.py', 'owners/SideSign/runtime-contract.json',
                 *('owners/'+owner+'/runtime-contract.json' for owner in set(PARENTS)-CHANGED_OWNERS)):
        need(read(root, name) == read(prior_root, name), 'unrelated P0 metadata changed: '+name)
    need(set(value['owners']) == set(PARENTS), 'diagnostic seven-owner graph required')
    for owner, row in value['owners'].items():
        previous = prior['owners'][owner]
        need(set(row) == set(previous) and all(row[k] == previous[k] for k in ('repository', 'source_checkpoint', 'path')),
             'diagnostic owner origin/path/checkpoint differs')
        fullsha(row['commit']); fullsha(row['tree'])
        need(previous['commit'] == PARENTS[owner], 'wrong inherited P0 owner')
        if owner in CHANGED_OWNERS:
            need(row['commit'] != previous['commit'] and row['tree'] != previous['tree'], 'diagnostic successor pending')
        else:
            need(row == previous, 'unrelated transport owner changed')
    validate_transitions(root, value, prior_root, prior)
    native = value['native_validation']
    need(isinstance(native, dict) and set(native) == set(prior['native_validation']) | {'inherited_p0_review_sha256'}, 'diagnostic native schema differs')
    need(native['source_basis'] == BASIS and native['inherited_p0_review_sha256'] == p0.P0_REVIEW_SHA256 and
         native['readiness_scope'] == 'eligible_for_gated_full_build' and native['review_sha256'] is None, 'diagnostic native review identity differs')
    run_identity(native)
    old_runs = [prior['native_validation'], *[parse(read(prior_root, owner+'-resolver.json')) for owner in LOCK_FILES]]
    def fresh_run(run):
        run_identity(run)
        need(all(run['run_url'] != old['run_url'] and run['artifact_id'] != old['artifact_id'] for old in old_runs), 'historical P0 run cannot prove successor')
    fresh_run(native)
    need(set(native['tested_owners']) == set(PARENTS), 'diagnostic tested graph incomplete')
    for owner, tested in native['tested_owners'].items():
        need(set(tested) == {'commit', 'tree'}, 'tested owner schema differs')
        fullsha(tested['commit']); fullsha(tested['tree'])
        if owner not in LOCK_FILES:
            need(tested == {k: value['owners'][owner][k] for k in ('commit', 'tree')}, 'owner changed after native capture')
    children = child_map(root)
    for owner in ('SideSign', 'minimuxer'):
        row = value['owners'][owner]
        need(children.get('SideStore/Dependencies/'+owner) == {'repository': row['repository'], 'declared_repository': row['repository'],
             'commit': row['commit'], 'tree': row['tree']}, 'diagnostic child graph differs')
    need(native['tested_children'] == {'SideSign': {}, 'SideStore': {'Dependencies/SideSign': value['owners']['SideSign']['commit'],
         'Dependencies/minimuxer': value['owners']['minimuxer']['commit']}}, 'diagnostic native child graph differs')
    receipt = parse(read(root, 'native-receipt.json'))
    need(set(receipt) == {'status', 'source_basis', 'native_validation', 'native_builds', 'canonical_native_fork83', 'constructor_diagnostics',
        'constructor_regression', 'consumer_suites', 'phase_evidence', 'tested_submodules', 'ADI_executed', 'device_validation'}, 'diagnostic native receipt schema differs')
    need(receipt['status'] == 'INDEPENDENTLY_REVIEWED_P0_CONSTRUCTOR_DIAGNOSTICS_NATIVE_PASS' and receipt['source_basis'] == BASIS and
         receipt['native_validation'] == native and receipt['native_builds'] == historical['native_builds'] and
         receipt['tested_submodules'] == children and receipt['ADI_executed'] is False and receipt['device_validation'] is False,
         'diagnostic native receipt claims differ')
    canonical = receipt['canonical_native_fork83']
    need(canonical == {'checks':83, 'failures':0, 'source_owner':value['owners']['AnisetteKit']['commit'], 'engine_archive_sha256':p0.FORK_SHA256}, 'canonical fork receipt differs')
    exact_counts(canonical, {'checks':83, 'failures':0}, 'canonical')
    focused = receipt['constructor_diagnostics']
    need(set(focused) == {'checks', 'failures', 'skipped', 'source_owner', 'case_ids'} and
         focused['source_owner'] == value['owners']['AnisetteKit']['commit'] and focused['case_ids'] == list(REQUIRED_DIAGNOSTIC_CASES), 'constructor cases/source differ')
    exact_counts(focused, {'checks':REQUIRED_DIAGNOSTIC_CHECKS, 'failures':0, 'skipped':0}, 'constructor')
    regression = receipt['constructor_regression']
    need(set(regression) == {'tests', 'failures', 'skipped'}, 'constructor aggregate schema differs')
    exact_counts(regression, {'tests':8, 'failures':0, 'skipped':0}, 'constructor aggregate')
    suites = receipt['consumer_suites']
    need(set(suites) == {'tested_owners', *CONSUMER_SUITE_IDS} and suites['tested_owners'] ==
         {owner:native['tested_owners'][owner] for owner in ('AnisetteKit', 'SideStore', 'LiveContainer')}, 'consumer source graph differs')
    for name, ids in CONSUMER_SUITE_IDS.items():
        suite = suites[name]
        need(set(suite) == {'tests', 'failures', 'errors', 'skipped', 'test_ids'} and suite['test_ids'] == list(ids), 'consumer exact test IDs required')
        exact_counts(suite, {'tests':len(ids), 'failures':0, 'errors':0, 'skipped':0}, 'consumer '+name)
    phases = receipt['phase_evidence']
    need(set(phases) == set(LOCK_FILES), 'fresh phase evidence incomplete')
    linked = (*RUN_FIELDS, 'resolver_tested_commit', 'resolver_tested_tree', 'lock_before_sha256', 'lock_sha256', 'origin_hash')
    for owner, lock_path in LOCK_FILES.items():
        resolver = parse(read(root, owner+'-resolver.json')); phase = phases[owner]
        need(resolver.get('status') == 'CAPTURED_FOR_REVIEW' and resolver.get('purpose') == 'independently_reviewed_p0_constructor_diagnostics_swiftpm_resolution' and
             resolver.get('owner') == owner and resolver.get('source_basis') == BASIS and
             resolver.get('anisette_commit') == value['owners']['AnisetteKit']['commit'], 'diagnostic resolver identity differs')
        need(set(phase) == {'status', *linked} and phase['status'] == 'CAPTURED_FOR_REVIEW' and
             all(key in resolver and resolver[key] == phase[key] for key in linked), 'diagnostic resolver/native phase linkage differs')
        fresh_run(resolver)
        for key in ('resolver_tested_commit', 'resolver_tested_tree'): fullsha(resolver[key])
        for suffix, key in (('before', 'lock_before_sha256'), ('after', 'lock_sha256')):
            need(sha(read(root, 'resolver-locks/'+owner+'.Package.resolved.'+suffix)) == digest(resolver[key]), 'retained resolver bytes differ')
        lock = parse(read(root, 'resolver-locks/'+owner+'.Package.resolved.after'))
        need(resolver['origin_hash'] == {'present':'originHash' in lock, 'value':lock.get('originHash')}, 'resolver originHash differs')
        if owner == 'SideStore':
            need(all(phase[key] == native[key] for key in RUN_FIELDS) and
                 native['tested_owners'][owner] == {'commit':resolver['resolver_tested_commit'], 'tree':resolver['resolver_tested_tree']}, 'phase2 final graph identity differs')
    registry = parse(read(root, 'compatibility-registry.json'))
    need(set(registry['owners']) == set(PARENTS), 'diagnostic contract owners differ')
    refs = {registry['evidence']['path']:registry['evidence']['sha256'], **{pin['manifest_path']:pin['manifest_sha256'] for pin in registry['owners'].values()}}
    need(all(value['files'].get(name) == expected for name, expected in refs.items()), 'diagnostic contract hashes not sealed')
    return root, value, receipt


def validate_pins(document, review):
    need(set(document) == {'schema_version', 'integration_baseline', 'source_basis', 'contract_registry_sha256', 'owners', 'native_validation'}, 'diagnostic pin schema differs')
    need(type(document['schema_version']) is int and document['schema_version'] == 1 and document['integration_baseline'] ==
         '141776ba6ba38fc04a5e77f68b0cfc4e6c8842ee' and document['source_basis'] == BASIS, 'diagnostic pin basis differs')
    need(document['owners'] == {owner:{k:v for k,v in row.items() if k!='tree'} for owner,row in review['owners'].items()} and
         document['contract_registry_sha256'] == review['files']['compatibility-registry.json'] and
         document['native_validation'] == {**review['native_validation'], 'review_sha256':DIAGNOSTIC_REVIEW_SHA256}, 'diagnostic caller pins differ')
    return document


def load_pins(document, integration_root, legacy):
    _, review, _ = approval(integration_root)
    validate_pins(document, review)
    need(all(row['path'] == legacy.PATHS[owner] for owner,row in document['owners'].items()), 'diagnostic owner paths differ')
    return document


def contract_basis(pins, integration_root):
    root, review, _ = approval(integration_root); validate_pins(pins, review)
    return root, review['files']['compatibility-registry.json']


def contract_files(pins, integration_root, root=None):
    directory, review, _ = approval(integration_root, root); validate_pins(pins, review)
    return {'reviewed-approval.json':read(directory, 'reviewed-approval.json'), **{name:read(directory,name) for name in review['files']}}


def anisette_manifest(pins, integration_root):
    root, review, _ = approval(integration_root); validate_pins(pins, review)
    manifest = parse(read(root, 'anisette-generated/maintained-source-manifest.json'))
    prior = parse(read(root, 'inherited/p0-native-basis-v1/anisette-generated/maintained-source-manifest.json'))
    need(manifest.get('source_basis') == BASIS and manifest.get('anisettekit_revision') == review['owners']['AnisetteKit']['commit'] and
         manifest.get('anisettekit_tree') == review['owners']['AnisetteKit']['tree'], 'diagnostic Anisette identity differs')
    rows = manifest.get('files'); need(isinstance(rows,list) and rows and len({row['path'] for row in rows}) == len(rows), 'diagnostic Anisette inventory malformed')
    need({row['path'] for row in prior['files']} | {'Native/Loader/vm_initialization_diagnostics.h'} <= {row['path'] for row in rows}, 'diagnostic Anisette sources missing')
    for row in rows: relative(row['path']); digest(row['sha256'])
    literals = manifest.get('required_compiled_evidence', {}).get('literals')
    need(isinstance(literals,list) and all(item in literals for item in prior['required_compiled_evidence']['literals']) and
         set(REQUIRED_DIAGNOSTIC_LITERALS) <= {row.get('value') for row in literals}, 'diagnostic/inherited compiled literals missing')
    return manifest


def verify_transitions(roots, review, root, legacy):
    sys.path.insert(0,str(legacy.ROOT))
    from prepare_candidate_inventory import snapshot
    transitions = parse(read(root,'source-transition.json'))
    for owner, source in roots.items():
        need(snapshot(source,PARENTS[owner],review['owners'][owner]['commit']) == transitions[owner], 'actual diagnostic transition differs')
        ancestry = legacy.git(source,'rev-list','--parents',PARENTS[owner]+'..'+review['owners'][owner]['commit']).decode().splitlines()
        need(all(len(line.split()) == 2 for line in ancestry), 'diagnostic transition merged another lane')
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
