"""Exact, additive full-login diagnostic admission. Historical reviews stay historical.

The anchor is unset during preparation. This gate grants only normal-build
eligibility; no device, guest, successful login, or initializer repair is claimed.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import p2_composed_host as historical
from p0_maintained_sources import need, sha, digest, fullsha, relative, read, parse, child_map, verify_children

BASIS = 'p0-full-login-diagnostics-v1'
DIRECTORY = 'migration/' + BASIS
INTEGRATION_PARENT = '828c4818b93e747125c979537bec29afda678ad5'
REVIEW_SHA256 = 'e42ff8620df373c539e9b118fa1ee338fc044b9287ba4ead5dcf9cec050ff960'
TRANSITION_SHA256 = 'dd9c2ea555c416914bb389c706fbb7a7767fa89601151b482feb9ab417be4e6b'
HANDOFF_SHA256 = '776b0813e84a6cd7202960e8ff92fb6e79b69167f4405250b01d8d678722ab08'
EVIDENCE_INVENTORY_SHA256 = '56d3b4cac73238a269c9469c8d7920a653a6081574afdca07a74a58acb61987b'  # Filled only with the frozen parser output, not activation.
CAPTURE_SUMMARY_SHA256 = 'cc94d8d6e989d6e76fbf869bc606c95d9be09b9859b301db077c3e1d675c2939'
CHANGED = {'AnisetteKit', 'SideSign', 'SideStore', 'LiveContainer'}
TUPLES = {
 'AnisetteKit': ('e6e9d4aac698de2a5219aaef02c9a417da45f4e9', '734664d759b9e99caa09089905e6a240b149548d'),
 'SideSign': ('c733332b0d64022cd603ab9ac5de054413729aab', '6559fa8c226ea7496e1fb1aa813c058bfee3e342'),
 'SideStore': ('d507f9e417f12c262b4d7e717c84d4a244c8e625', '829d5e86ae2365b2870d268e77735f11f16944fb'),
 'LiveContainer': ('545438e1f0a89fdef375f73273c5c9fc2735cbce', 'e8014dd771c25c4a05dc76ab0aa304a233642a83'),
 'idevice': ('803f9368e6b27a43d8a9f22dc0ec281c87883113', 'f5ac11668bae97e577e7c4fc098b5484df1c9af1'),
 'jktcp': ('32c0210f286b4fd16efe05be4f57167a9d94459b', 'e94eaefdcae5fe54a535186e36a0b2ebccc9bf13'),
 'minimuxer': ('efbcab05d7d636aa37c6bf6c7f364d122c5610f6', '30aa947645c5befb0c3210700095c8b039893d83'),
}
CHAINS = {
 'AnisetteKit': ['161435bcc0344e1a9853669455c1ca8d9dda2bf1', TUPLES['AnisetteKit'][0]],
 'SideSign': ['c55f53ee6d3a65ab9b49d1528eb6c668fdde6c0e', '770b991b1dbc269ad19dd18b8b2e8252b665af9b', TUPLES['SideSign'][0]],
 'SideStore': ['59fa3b5cd8c9ea4a88716cbaaa08bacd9f22e218', '85bf45f65a9c2835c18d2c8e2e9c618177c95b54', 'dc29ce0d79a299f32a340c460e1d61e8190e320c', TUPLES['SideStore'][0]],
 'LiveContainer': ['e22de6df412a0977f7430b2218960ca3ee9585d5', TUPLES['LiveContainer'][0]],
}
LOCKS = historical.historical.LOCK_FILES
CONTRACT_NAMES = historical.CONTRACT_NAMES
SOURCE_NAMES = CONTRACT_NAMES | {'source-transition.json', 'source-inventory.json', 'submodules.json'}
RUNTIME_ADDITIONS = {
 'AnisetteKit': {'Sources/LoginDiagnosticTrace.swift'},
 'SideSign': {'Sources/DeveloperPortal/Devices.swift', 'Sources/DeveloperPortal/Teams.swift'},
 'SideStore': {'AltStore/Authentication/AuthenticationViewController.swift', 'AltStore/Settings/SettingsViewController.swift'},
 'LiveContainer': set(),
}
SOURCE_NOTE = (' Additive p0-full-login-diagnostics-v1 binds the exact reviewed bounded numeric login trace and safe logging source transition. '
 'Historical P0/P2 evidence remains historical; fresh unsigned capture and final-lock equivalence are separately sealed. '
 'No real login, device execution, guest execution, or initializer-47 repair is established.')
SCOPE = 'Reviewed full-login diagnostic source graph, genuine native compile captures, and exact final SideStore app-lock equivalence; normal gated build eligibility only'


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, 'duplicate login JSON key')
            result[key] = value
        return result
    def forbidden(value):
        raise ValueError('login metadata floats/nonfinite numbers forbidden')
    return json.loads(raw, object_pairs_hook=unique, parse_float=forbidden, parse_constant=forbidden)


def dump(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n').encode()


def expected_owners(prior):
    result = copy.deepcopy(prior['owners'])
    need(set(result) == set(TUPLES), 'login owner closure differs')
    for owner, (commit, tree) in TUPLES.items():
        result[owner].update(commit=commit, tree=tree)
    return result


def inherited_names(prior):
    return {'reviewed-approval.json', 'maintained-sources.json', *prior['files']}


def compose(previous, transition):
    need(set(transition) == {'baseline_commit', 'commit', 'tree', 'changes'} and
         transition['baseline_commit'] == previous['commit'], 'login transition baseline/schema differs')
    result = copy.deepcopy(previous)
    result.update(commit=transition['commit'], tree=transition['tree'])
    for name, change in transition['changes'].items():
        relative(name)
        need(set(change) == {'before', 'after'} and change['before'] != change['after'], 'login source change invalid')
        for row in change.values():
            if row is None:
                continue
            need(isinstance(row, dict), 'login source row must be an object')
            fullsha(row.get('git_object'))
            if row.get('type') == 'commit':
                need(set(row) == {'mode', 'type', 'git_object'} and row['mode'] == '160000', 'login gitlink row differs')
            else:
                need(set(row) == {'mode', 'type', 'git_object', 'sha256'} and row['type'] == 'blob' and
                     row['mode'] in {'100644', '100755'}, 'login ordinary source row differs')
                digest(row['sha256'])
        old = result['changes'].get(name)
        if old is not None:
            need(old['after'] == change['before'], 'login previous source bytes differ')
            change = {'before': old['before'], 'after': change['after']}
        if change['before'] == change['after']:
            result['changes'].pop(name, None)
        else:
            result['changes'][name] = change
    return result


def derive_source_metadata(prior_root, prior, transitions):
    """Deterministic successor contracts; never establishes native readiness."""
    need(sha(dump(transitions)) == TRANSITION_SHA256 and set(transitions) == CHANGED, 'login exact source transition differs')
    owners = expected_owners(prior)
    inventory = parse(read(prior_root, 'source-inventory.json'))
    for owner in CHANGED:
        item = transitions[owner]
        need(item['baseline_commit'] == prior['owners'][owner]['commit'] and
             (item['commit'], item['tree']) == TUPLES[owner], 'login source target differs')
        inventory[owner] = compose(inventory[owner], item)
    children = parse(read(prior_root, 'submodules.json'))
    children['SideStore/Dependencies/SideSign'].update(commit=TUPLES['SideSign'][0], tree=TUPLES['SideSign'][1])
    output = {'source-transition.json': dump(transitions), 'source-inventory.json': dump(inventory),
              'submodules.json': dump(children), 'validate_contracts.py': read(prior_root, 'validate_contracts.py')}
    registry = parse(read(prior_root, 'compatibility-registry.json'))
    evidence = parse(read(prior_root, 'provenance/source-evidence.json'))
    evidence['inventory_digests']['source-transition.json'] = TRANSITION_SHA256
    for owner in TUPLES:
        path = 'owners/'+owner+'/runtime-contract.json'
        raw = read(prior_root, path)
        if owner in CHANGED:
            contract = parse(raw)
            rows = {row['path']: row for row in contract['sources']}
            delta = transitions[owner]['changes']
            for name in set(rows) & set(delta):
                change = delta[name]
                need(change['before']['sha256'] == rows[name]['sha256'] and change['after'] is not None and
                     change['after']['mode'] == rows[name]['mode'], 'login contract source/mode differs')
                rows[name]['sha256'] = change['after']['sha256']
                rows[name]['basis'] = 'source-transition.json:'+owner+':changes'
            for name in RUNTIME_ADDITIONS[owner]:
                need(name not in rows and delta[name]['after']['type'] == 'blob', 'login added runtime row differs')
                rows[name] = {'path': name, 'mode': delta[name]['after']['mode'], 'sha256': delta[name]['after']['sha256'],
                              'basis': 'source-transition.json:'+owner+':changes'}
            contract['sources'] = [rows[name] for name in sorted(rows)]
            # Include newly covered runtime implementation files under the existing
            # diagnostics contract. No provider, version, edge or public API is removed.
            for item in contract['provides']:
                if item['contract'] == 'diagnostics' or (owner == 'AnisetteKit' and item['contract'] == 'anisette-provider'):
                    item['source_paths'] = sorted(set(item['source_paths']) | RUNTIME_ADDITIONS[owner])
            contract['provenance']['source_basis'] += SOURCE_NOTE
            raw = dump(contract)
            evidence['owners'][owner] = {key: contract[key] for key in ('sources', 'provenance')}
        output[path] = raw
        registry['owners'][owner]['manifest_sha256'] = sha(raw)
    output['provenance/source-evidence.json'] = dump(evidence)
    registry['evidence']['sha256'] = sha(output['provenance/source-evidence.json'])
    output['compatibility-registry.json'] = dump(registry)
    manifest = parse(read(prior_root, 'anisette-generated/maintained-source-manifest.json'))
    manifest.update(source_basis=BASIS, anisettekit_revision=TUPLES['AnisetteKit'][0], anisettekit_tree=TUPLES['AnisetteKit'][1])
    rows = {row['path']: row for row in manifest['files']}
    for name, change in transitions['AnisetteKit']['changes'].items():
        need(change['after'] is not None and change['after']['type'] == 'blob', 'login AK removal/nonfile forbidden')
        if name in rows:
            need(rows[name]['sha256'] == change['before']['sha256'], 'login prior AK manifest differs')
        rows[name] = {'path': name, 'mode': change['after']['mode'], 'sha256': change['after']['sha256'],
                      'basis': 'source-transition.json:AnisetteKit:changes'}
    manifest['files'] = [rows[name] for name in sorted(rows)]
    manifest['required_compiled_evidence']['literals'] += [
        {'purpose': 'new native bounded timing payload key', 'value': 'v3_login_native_trace'},
        {'purpose': 'actual bounded Swift login trace version marker', 'value': 'login_trace_v1'}]
    manifest['scope'] += SOURCE_NOTE
    output['anisette-generated/maintained-source-manifest.json'] = dump(manifest)
    need(set(output) == SOURCE_NAMES, 'login source metadata closure differs')
    return output


def evidence_inventory(root):
    raw = read(root, 'evidence-inventory.json')
    need(sha(raw) == digest(EVIDENCE_INVENTORY_SHA256), 'login original evidence inventory differs')
    values = parse(raw)
    need(isinstance(values, dict) and values, 'login evidence inventory missing')
    for name, expected in values.items():
        relative(name)
        need(sha(read(root, name)) == digest(expected), 'login original evidence changed: '+name)
    need(sha(read(root, 'evidence/final-source-handoff.json')) == HANDOFF_SHA256, 'login original source handoff differs')
    need(sha(read(root, 'capture-summary.json')) == digest(CAPTURE_SUMMARY_SHA256), 'login independently replayed summary differs')
    return values


def native_receipt(root, accepted=False):
    handoff = parse(read(root, 'evidence/final-source-handoff.json'))
    return {'schema_version': 1, 'source_basis': BASIS,
        'status': 'INDEPENDENTLY_REVIEWED_FULL_LOGIN_DIAGNOSTICS_PASS' if accepted else 'CANDIDATE_AWAITING_INDEPENDENT_REVIEW',
        'scope': SCOPE, 'original_handoff_sha256': HANDOFF_SHA256,
        'capture_summary_sha256': CAPTURE_SUMMARY_SHA256,
        'captured_source_owners': handoff['captured_source_owners'], 'final_source_owners': handoff['final_source_owners'],
        'final_sidestore_equivalence': handoff['final_sidestore_equivalence'], 'scope_notes': handoff['capture_scope_notes'],
        'guest_execution': False, 'device_login_proven': False, 'physical_device_execution': False,
        'initializer47_containment_integrated': False, 'final_sidestore_commit_directly_compiled': False,
        'fresh_all_language_ui_execution': False, 'normal_release_completed': False}


def native_validation(receipt_hash, accepted=False):
    return {'source_basis': BASIS, 'readiness_scope': 'eligible_for_gated_full_build' if accepted else 'candidate_for_gated_full_build_review',
        'review_sha256': None, 'inherited_p2_review_sha256': historical.REVIEW_SHA256,
        'native_receipt_sha256': receipt_hash, 'capture_summary_sha256': CAPTURE_SUMMARY_SHA256, 'scope': SCOPE}


def approval(integration_root, metadata_root=None):
    digest(REVIEW_SHA256)  # Anchor-first, before caller-controlled filesystem reads.
    prior_root, prior, _ = historical.approval(integration_root)
    root = Path(metadata_root) if metadata_root is not None else Path(integration_root)/DIRECTORY
    raw = read(root, 'reviewed-approval.json')
    need(sha(raw) == REVIEW_SHA256, 'login independent approval anchor differs')
    review = parse(raw)
    need(set(review) == {'schema_version', 'source_basis', 'integration_parent', 'inherited_p2_review_sha256', 'owners', 'files', 'native_validation'}, 'login approval schema differs')
    need(type(review['schema_version']) is int and review['schema_version'] == 1 and review['source_basis'] == BASIS and
         review['integration_parent'] == INTEGRATION_PARENT and review['inherited_p2_review_sha256'] == historical.REVIEW_SHA256,
         'login approval identity differs')
    evidence = evidence_inventory(root)
    required = SOURCE_NAMES | set(evidence) | {'evidence-inventory.json', 'capture-summary.json', 'native-receipt.json'} | {'historical-p2/'+name for name in inherited_names(prior)}
    need(set(review['files']) == required, 'login exact metadata closure differs')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    need(actual == required | {'reviewed-approval.json'}, 'login extra or missing metadata file')
    for name, expected in review['files'].items():
        need(sha(read(root, name)) == digest(expected), 'login sealed metadata drift: '+name)
    for name in inherited_names(prior) - {'maintained-sources.json'}:
        need(read(root, 'historical-p2/'+name) == read(prior_root, name), 'login historical P2 evidence changed')
    historical.validate_pins(parse(read(root, 'historical-p2/maintained-sources.json')), prior)
    need(review['owners'] == expected_owners(prior), 'login seven-owner graph differs')
    expected = derive_source_metadata(prior_root, prior, parse(read(root, 'source-transition.json')))
    need(all(read(root, name) == raw for name, raw in expected.items()), 'login source contracts/inventory differ')
    receipt = parse(read(root, 'native-receipt.json'))
    need(read(root, 'native-receipt.json') == dump(native_receipt(root, accepted=True)), 'login native receipt scope/identity differs')
    need(review['native_validation'] == native_validation(sha(read(root, 'native-receipt.json')), accepted=True), 'login readiness differs')
    return root, review, receipt


def validate_pins(pins, review):
    need(set(pins) == {'schema_version', 'integration_baseline', 'source_basis', 'contract_registry_sha256', 'owners', 'native_validation'}, 'login pins schema differs')
    need(type(pins['schema_version']) is int and pins['schema_version'] == 1 and pins['integration_baseline'] == historical.historical.BASELINE and
         pins['source_basis'] == BASIS and pins['owners'] == {owner: {k: v for k, v in row.items() if k != 'tree'} for owner, row in review['owners'].items()} and
         pins['contract_registry_sha256'] == review['files']['compatibility-registry.json'] and
         pins['native_validation'] == {**review['native_validation'], 'review_sha256': REVIEW_SHA256}, 'login exact caller pins differ')
    return pins


def load_pins(document, integration_root, legacy):
    _, review, _ = approval(integration_root)
    validate_pins(document, review)
    need(all(row['path'] == legacy.PATHS[owner] for owner, row in document['owners'].items()), 'login owner path differs')
    return document


def contract_basis(pins, integration_root):
    root, review, _ = approval(integration_root); validate_pins(pins, review)
    return root, review['files']['compatibility-registry.json']


def contract_files(pins, integration_root, root=None):
    directory, review, _ = approval(integration_root, root); validate_pins(pins, review)
    return {'reviewed-approval.json': read(directory, 'reviewed-approval.json'), **{name: read(directory, name) for name in review['files']}}


def anisette_manifest(pins, integration_root):
    root, review, _ = approval(integration_root); validate_pins(pins, review)
    return parse(read(root, 'anisette-generated/maintained-source-manifest.json'))


def verify_source_objects(roots, prior, transitions, legacy):
    from prepare_candidate_inventory import snapshot
    for owner in CHANGED:
        source = roots[owner]
        need(snapshot(source, prior['owners'][owner]['commit'], TUPLES[owner][0]) == transitions[owner], 'login actual transition differs: '+owner)
        for parent, child in zip(CHAINS[owner], CHAINS[owner][1:]):
            need(legacy.git(source, 'rev-list', '--parents', '-n1', child).decode().split() == [child, parent], 'login source parent chain differs')


def verify_all(workspace, pins, anisette, after_build, legacy):
    root, review, _ = approval(legacy.ROOT); validate_pins(pins, review)
    _, prior, _ = historical.approval(legacy.ROOT)
    sys.path.insert(0, str(legacy.ROOT))
    from prepare_candidate_inventory import BASES, snapshot
    roots = {owner: workspace/row['path'] for owner, row in pins['owners'].items()}
    if anisette is not None:
        roots['AnisetteKit'] = Path(anisette)
    verify_source_objects(roots, prior, parse(read(root, 'source-transition.json')), legacy)
    inventory = parse(read(root, 'source-inventory.json')); results = {}
    for owner, source in roots.items():
        spec = pins['owners'][owner]
        need(snapshot(source, BASES[owner], spec['commit']) == inventory[owner] and
             inventory[owner]['tree'] == review['owners'][owner]['tree'], 'login complete source inventory differs: '+owner)
        results[owner] = legacy.verify_checkout(source, spec['commit'], owner=owner, after_build=after_build, require_full_history=True)
        results[owner]['origin'] = legacy.verify_origin(source, spec['repository'], spec['commit'], Path(anisette).parent.parent if owner == 'AnisetteKit' and anisette else None)
    legacy.verify_graph(roots, pins)
    for owner, path in LOCKS.items():
        need(read(roots[owner], path) == read(root, 'resolver-locks/'+owner+'.Package.resolved.after'), 'login final genuine lock bytes differ')
        parent, child = CHAINS[owner][-2:]
        change = snapshot(roots[owner], parent, child)['changes']
        need(set(change) == {path} and all(row['mode'] == '100644' and row['type'] == 'blob' for row in change[path].values()), 'login lock child changed runtime/mode')
        need(legacy.git(roots[owner], 'show', parent+':'+path) == read(root, 'resolver-locks/'+owner+'.Package.resolved.before'), 'login resolver parent lock differs')
    verify_children(roots, child_map(root), legacy)
    command = [sys.executable, '-B', str(root/'validate_contracts.py'), '--registry', str(root/'compatibility-registry.json'), '--registry-sha256', review['files']['compatibility-registry.json']]
    for owner, source in roots.items(): command += ['--owner', owner+'='+str(source.resolve())]
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
        need(not path.is_symlink() and (not path.exists() or (path.is_dir() and not list(path.iterdir()))), 'login refuses nonfresh source target')
        for parent in path.parents:
            if parent == workspace:
                break
            need(not parent.is_symlink(), 'login linked source parent')
        path.parent.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL='https', GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
        subprocess.run(['git', '--no-replace-objects', '-c', 'core.hooksPath='+os.devnull, 'clone', '--no-checkout', row['repository'], str(path)], check=True, env=env)
        legacy.git(path, 'fetch', '--no-tags', '--no-recurse-submodules', 'origin', row['commit'])
        legacy.git(path, 'checkout', '--detach', row['commit'])
        legacy.verify_origin(path, row['repository'], row['commit'])
        need(legacy.git(path, 'rev-parse', 'HEAD^{tree}').decode().strip() == row['tree'], 'login acquired tree differs')
        modules = configparser.ConfigParser(interpolation=None)
        if (path/'.gitmodules').exists():
            modules.read(path/'.gitmodules')
        for relative_path, (mode, kind, oid) in legacy.entries(path).items():
            if kind != 'commit':
                continue
            relative(relative_path)
            key = prefix+'/'+relative_path
            need(mode == '160000' and key in children, 'login unreviewed child gitlink')
            child = children[key]
            declarations = [dict(modules[section]) for section in modules.sections() if modules[section].get('path') == relative_path]
            need(declarations == [{'path': relative_path, 'url': child['declared_repository']}] and oid == child['commit'], 'login child URL/gitlink differs before fetch')
            seen.add(key)
            fetch(path/relative_path, child, key)
    for owner in ('LiveContainer', 'SideStore', 'AnisetteKit', 'idevice', 'jktcp'):
        row = review['owners'][owner]
        path = workspace/pins['owners'][owner]['path']
        need(not path.exists(), 'login owner requires fresh target')
        fetch(path, row, owner)
    need(seen == set(children), 'login reviewed child closure differs')
