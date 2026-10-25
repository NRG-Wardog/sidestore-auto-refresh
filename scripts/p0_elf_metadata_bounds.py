"""Independent defensive ELF metadata admission. No guest execution is authorized.

A pending review digest fails before any caller-controlled evidence is read.
Historical constructor/native gates and their evidence remain immutable.
"""
import copy
import os
from pathlib import Path
import subprocess
import sys
import p0_maintained_sources as p0
import p0_constructor_diagnostics as historical
from p0_maintained_sources import need, sha, digest, fullsha, relative, read, parse, run_identity, child_map, verify_children

BASIS = 'p0-elf-metadata-bounds-v1'
DIRECTORY = 'migration/p0-elf-metadata-bounds-v1'
INTEGRATION_PARENT = 'b66dc6f8452847921261b65b608e355a9301a5a2'
BASELINE = '141776ba6ba38fc04a5e77f68b0cfc4e6c8842ee'
REVIEW_SHA256 = 'f736d82fcdce1be06b92d1581f6509c1db2375a0a9620eeed52e0b3766f7d0bb'  # Assigned only after independent review of genuine CI.
REPAIR_COMMIT = '8e195b0e5c90f30f5ab49f2a9315dfff8aeacfb2'
REPAIR_TREE = '35f0d2669a20700cd2105f207897ee08d72ad5f7'
AK_COMMIT = '9c876820d102affe2f55ee4b7f735fff6abd990a'
AK_TREE = '1c54f163acfb8e5d18605aaa301a3d5ebd576505'
FIXTURE_MANIFEST_SHA256 = '9d47d20550102c54665f7c7a7e960f308ff27618e54eedd009f255e203b0b1f1'
FIXTURE_FILE = {'before_git_blob': '4381043502adbea1b4d8d3860f2ba5f5f83e7fa7', 'before_sha256': '053efa1197760643af52976e868287ad83508195ddf75629add99b83ca7c0347', 'bytes': 9066, 'git_blob': '580a9f5c6e82cfaa2237ee15a18b9c3e555881e7', 'mode': '100644', 'path': '.ci/native-tests/fixtures/isolated_anisette_loader_harness.cpp', 'sha256': 'b15722a758ffc80324476823c854763ff3c98e3bce51a7c74e77fa453ea6b2e8'}
MANIFEST_SHA256 = '36196da122bbe616094916bdb259c6d82ce892b458df722174600ae75c6088f8'
CHANGED_OWNERS = {'AnisetteKit', 'SideSign', 'SideStore'}
LOCK_FILES = historical.LOCK_FILES
RUN_FIELDS = historical.RUN_FIELDS
REQUIRED_DIAGNOSTIC_LITERALS = historical.REQUIRED_DIAGNOSTIC_LITERALS
CONSUMER_SUITE_IDS = historical.CONSUMER_SUITE_IDS
exact_counts = historical.exact_counts
PARENTS = {'AnisetteKit': '94659b4d54eed5729b34f4103b3f70579aa8d8e9', 'LiveContainer': '1df50257f6255522e6ff48db226508fe74d6b17d', 'SideSign': 'b829a21c3955b4084fc3a6f613bad9162a8d43ef', 'SideStore': 'aebf23d8129f831c1c706619a71c936ab73fb6e5', 'idevice': '803f9368e6b27a43d8a9f22dc0ec281c87883113', 'jktcp': '32c0210f286b4fd16efe05be4f57167a9d94459b', 'minimuxer': 'efbcab05d7d636aa37c6bf6c7f364d122c5610f6'}
AK_FILES = {'.ci/native-tests/README.md': {'path': '.ci/native-tests/README.md', 'bytes': 4997, 'sha256': '7380f730896817cd6913a59b8f411b815ea4bf06560237fd1bdeedc2cdba72aa', 'git_blob': '2682bbd0caf64c818a28379bc78d99be86498ebb', 'mode': '100644'}, '.ci/native-tests/fixtures/elf_metadata_bounds_unit.cpp': {'path': '.ci/native-tests/fixtures/elf_metadata_bounds_unit.cpp', 'bytes': 13973, 'sha256': 'bb4e7ec60202f6576f8013d0925c69c6ca4d8fca6061d93b233587efdb35487a', 'git_blob': 'd0a38ca27cf61770010803ca86c44620051ae182', 'mode': '100644'}, '.ci/native-tests/run_tests.py': {'path': '.ci/native-tests/run_tests.py', 'bytes': 23749, 'sha256': 'e750b294770ee864e36b94d9a7fa936c5cb76b2a16633ce8d199f439829f6811', 'git_blob': '24ab5e38ab0952afe2182810de0e29197838022e', 'mode': '100644'}, '.ci/native-tests/test_android_tls_runtime.py': {'path': '.ci/native-tests/test_android_tls_runtime.py', 'bytes': 3184, 'sha256': '6867ebfa2ef821692bc7c8091e1072996139c791cb61f942146bc6205e28f755', 'git_blob': '9163e7858c9469795b3aff1c0d450c0fa1004729', 'mode': '100644'}, '.ci/native-tests/test_constructor_diagnostics.py': {'path': '.ci/native-tests/test_constructor_diagnostics.py', 'bytes': 6509, 'sha256': '9d27c7b9206246d5e908555e7f2412162ccf5d3b0862270fb79913a4a8ec1ddc', 'git_blob': 'b7fd809bc8a5d5d1e8b1a0c18db28b074294b0ff', 'mode': '100644'}, '.ci/native-tests/test_elf_metadata_bounds.py': {'path': '.ci/native-tests/test_elf_metadata_bounds.py', 'bytes': 4478, 'sha256': '6a601417c24df2a267e3a782affcd1fefcd41b18301ed6073299be33e2b643ac', 'git_blob': 'f74dd05d0605e59298e42338527b6866d1aacf41', 'mode': '100644'}, 'Native/Loader/elf_loader_emulator.cpp': {'path': 'Native/Loader/elf_loader_emulator.cpp', 'bytes': 57538, 'sha256': '2caeb8f2d5526c35e6128a288ec74229e25d23f1d2f84b22ca226ba3c89405d3', 'git_blob': '8cb45ed2bc6e4d7f03aff1b4b678044d038bc048', 'mode': '100644'}, 'Native/Loader/elf_loader_emulator.h': {'path': 'Native/Loader/elf_loader_emulator.h', 'bytes': 4307, 'sha256': '16eb67e76dc4ae94350e55968e9313c2f4163817182c08af76a5c85b2ec09327', 'git_blob': 'aea3514b0ac438926d4edd354d1731b44549c7f9', 'mode': '100644'}, 'Native/Loader/elf_metadata_bounds.h': {'path': 'Native/Loader/elf_metadata_bounds.h', 'bytes': 14829, 'sha256': '7acd9287cc351b55141f40a0baecda9c3d3202a420cf4b7f0f2515572d877253', 'git_blob': '4dbeeb6949fbc0fa87f050fcc8c571e6fef06b40', 'mode': '100644'}, 'Native/anisette_core_uc.cpp': {'path': 'Native/anisette_core_uc.cpp', 'bytes': 47278, 'sha256': 'd75cc5f9534f94d8871da89ca84c885148d3f813e93eee790aa698cec6ee855a', 'git_blob': '782ff3329c9dad86b2aa010526fb9ab65313c952', 'mode': '100644'}}
HELPER_SOURCES = {'Native/Loader/elf_metadata_bounds.h': '7acd9287cc351b55141f40a0baecda9c3d3202a420cf4b7f0f2515572d877253', 'Native/anisette_base.h': '6c47c3fef881d99082d1eb7e687fc78de6339568118259884d2b8a0d6c6e8664', 'Native/include/anisette_core.h': 'ca1ea4005f77dcca17c68965ba2dd1f65596e282a57adb4277f1d90d8bd11f02', '.ci/native-tests/fixtures/elf_metadata_bounds_unit.cpp': 'bb4e7ec60202f6576f8013d0925c69c6ca4d8fca6061d93b233587efdb35487a'}
METADATA_EXPECTED = {'schema': 1, 'checks': 713, 'failures': 0, 'guest_execution': False, 'proprietary_input': False}


def validate_helper(value):
    need(isinstance(value, dict) and set(value) == {'schema', 'source_sha256', 'results', 'sanitizers', 'leak_sanitizer_enabled', 'guest_execution', 'device_validation', 'proprietary_input'}, 'bounds helper schema differs')
    need(type(value['schema']) is int and value['schema'] == 1 and value['source_sha256'] == HELPER_SOURCES, 'bounds actual helper source identity differs')
    need(isinstance(value['results'], dict) and set(value['results']) == {'normal', 'optimized', 'sanitized'}, 'three bounds helper variants required')
    for row in value['results'].values():
        need(isinstance(row, dict) and row == METADATA_EXPECTED and all(type(row[k]) is type(v) for k,v in METADATA_EXPECTED.items()), 'bounds helper checks or execution claims differ')
    need(value['sanitizers'] == ['address', 'undefined'] and value['leak_sanitizer_enabled'] is False, 'bounds sanitizer configuration differs')
    need(all(value[k] is False for k in ('guest_execution', 'device_validation', 'proprietary_input')), 'bounds helper unsafe execution claim')
    return value


def base_pins(integration_root):
    _, review, _ = historical.approval(integration_root)
    return {'schema_version': 1, 'integration_baseline': BASELINE,
        'source_basis': historical.BASIS, 'contract_registry_sha256': review['files']['compatibility-registry.json'],
        'owners': {owner: {k: v for k, v in row.items() if k != 'tree'} for owner, row in review['owners'].items()},
        'native_validation': {**review['native_validation'], 'review_sha256': historical.DIAGNOSTIC_REVIEW_SHA256}}


def verify_manifest_pin(before, after):
    old=PARENTS['AnisetteKit'].encode();new=AK_COMMIT.encode()
    need(before.count(old)==1 and before.replace(old,new)==after, 'bounds SideSign manifest changed beyond exact AK revision pin')


def verify_ak_lineage(repo, git, snapshot):
    repair=snapshot(repo,PARENTS['AnisetteKit'],REPAIR_COMMIT)
    need(repair['tree']==REPAIR_TREE and set(repair['changes'])==set(AK_FILES), 'bounds immutable original repair transition differs')
    for path,row in AK_FILES.items():
        need(repair['changes'][path]['after']=={'mode':row['mode'],'type':'blob','git_object':row['git_blob'],'sha256':row['sha256']}, 'bounds original repair source differs')
    fixture=snapshot(repo,REPAIR_COMMIT,AK_COMMIT)
    need(fixture['tree']==AK_TREE and set(fixture['changes'])=={FIXTURE_FILE['path']}, 'bounds fixture successor changed production source')
    need(fixture['changes'][FIXTURE_FILE['path']]=={'before':{'mode':FIXTURE_FILE['mode'],'type':'blob','git_object':FIXTURE_FILE['before_git_blob'],'sha256':FIXTURE_FILE['before_sha256']},
        'after':{'mode':FIXTURE_FILE['mode'],'type':'blob','git_object':FIXTURE_FILE['git_blob'],'sha256':FIXTURE_FILE['sha256']}}, 'bounds fixture successor bytes differ')
    need(git(repo,'rev-list','--parents','--max-count=1',AK_COMMIT).decode().split()==[AK_COMMIT,REPAIR_COMMIT], 'bounds fixture must be direct child of exact repair')


def validate_delta(transitions, review):
    ak = transitions['AnisetteKit']
    need(review['owners']['AnisetteKit']['commit'] == AK_COMMIT and review['owners']['AnisetteKit']['tree'] == AK_TREE, 'bounds exact reviewed AK tuple required')
    need(set(ak['changes']) == set(AK_FILES) | {FIXTURE_FILE['path']}, 'bounds exact ten-file repair plus separate one-fixture successor required')
    expected_fixture={'before':{'mode':FIXTURE_FILE['mode'],'type':'blob','git_object':FIXTURE_FILE['before_git_blob'],'sha256':FIXTURE_FILE['before_sha256']},
        'after':{'mode':FIXTURE_FILE['mode'],'type':'blob','git_object':FIXTURE_FILE['git_blob'],'sha256':FIXTURE_FILE['sha256']}}
    need(ak['changes'][FIXTURE_FILE['path']]==expected_fixture, 'bounds separately reviewed host fixture differs')
    for path, row in AK_FILES.items():
        after = ak['changes'][path]['after']
        need(after == {'mode': row['mode'], 'type': 'blob', 'git_object': row['git_blob'], 'sha256': row['sha256']}, 'bounds reviewed AK file differs: '+path)
    need(set(transitions['SideSign']['changes']) == {'Package.swift', LOCK_FILES['SideSign']}, 'bounds SideSign must contain only manifest and real lock cascade')
    need(set(transitions['SideStore']['changes']) == {'Dependencies/SideSign', LOCK_FILES['SideStore']}, 'bounds SideStore must contain only gitlink and real lock cascade')
    link=transitions['SideStore']['changes']['Dependencies/SideSign']
    need(link=={'before':{'mode':'160000','type':'commit','git_object':PARENTS['SideSign']}, 'after':{'mode':'160000','type':'commit','git_object':review['owners']['SideSign']['commit']}}, 'bounds exact SideSign dependency link differs')
    for owner in set(PARENTS)-CHANGED_OWNERS:
        need(not transitions[owner]['changes'] and review['owners'][owner]['commit'] == PARENTS[owner], 'bounds unrelated owner changed')

def validate_transitions(root, review, prior_root, prior):
    transitions = parse(read(root, 'source-transition.json'))
    inventory = parse(read(root, 'source-inventory.json'))
    previous = parse(read(prior_root, 'source-inventory.json'))
    validate_delta(transitions, review)
    need(set(transitions) == set(inventory) == set(PARENTS), 'bounds exact source inventory required')
    for owner, transition in transitions.items():
        row = review['owners'][owner]
        need(set(transition) == {'baseline_commit', 'commit', 'tree', 'changes'} and
             transition['baseline_commit'] == PARENTS[owner] and transition['commit'] == row['commit'] and
             transition['tree'] == row['tree'] and isinstance(transition['changes'], dict), 'bounds source transition identity differs')
        composed = copy.deepcopy(previous[owner])
        composed.update(commit=row['commit'], tree=row['tree'])
        if owner not in CHANGED_OWNERS:
            need(not transition['changes'], 'unrelated owner transition forbidden')
        else:
            need(bool(transition['changes']), 'bounds successor has no source delta')
        for path, change in transition['changes'].items():
            relative(path)
            need(isinstance(change, dict) and set(change) == {'before', 'after'} and
                 change['before'] != change['after'], 'bounds source change malformed')
            for record in change.values():
                if record is not None:
                    need(isinstance(record, dict) and set(record) == ({'mode', 'type', 'git_object', 'sha256'} if record.get('type') == 'blob' else {'mode', 'type', 'git_object'}), 'bounds source record malformed')
                    fullsha(record['git_object'])
                    need((record['type'] == 'blob' and record['mode'] in ('100644', '100755', '120000')) or
                         (record['type'] == 'commit' and record['mode'] == '160000'), 'bounds source mode/type malformed')
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
    digest(REVIEW_SHA256)  # Must fail before reading caller-supplied metadata.
    prior_root, prior, prior_receipt = historical.approval(integration_root)
    root = Path(metadata_root) if metadata_root is not None else Path(integration_root)/DIRECTORY
    raw = read(root, 'reviewed-approval.json')
    need(sha(raw) == REVIEW_SHA256, 'bounds independent review anchor differs')
    value = parse(raw)
    need(set(value) == {'schema_version', 'source_basis', 'integration_parent', 'inherited_constructor_review_sha256', 'owners', 'files', 'native_validation'}, 'bounds review schema differs')
    need(type(value['schema_version']) is int and value['schema_version'] == 1 and value['source_basis'] == BASIS and
         value['integration_parent'] == INTEGRATION_PARENT and value['inherited_constructor_review_sha256'] == historical.DIAGNOSTIC_REVIEW_SHA256, 'bounds review basis differs')
    inherited = {'inherited/p0-constructor-diagnostics-v1/'+name for name in ['reviewed-approval.json', *prior['files']]}
    required = set(prior['files']) | inherited | {'inherited/constructor-pins.json', 'reviewed-ak-manifest.json', 'historical-source-fingerprints.json', 'historical-evidence.json', 'metadata-helper.json', 'reviewed-host-fixture-manifest.json'}
    need(isinstance(value['files'], dict) and set(value['files']) == required, 'bounds exact metadata inventory required')
    for name, expected in value['files'].items():
        need(sha(read(root, name)) == digest(expected), 'bounds metadata drift: '+name)
    for name in ['reviewed-approval.json', *prior['files']]:
        need(read(root, 'inherited/p0-constructor-diagnostics-v1/'+name) == read(prior_root, name), 'historical constructor evidence changed')
    need(parse(read(root, 'inherited/constructor-pins.json')) == base_pins(integration_root), 'historical constructor pins differ')
    need(sha(read(root, 'reviewed-ak-manifest.json')) == MANIFEST_SHA256, 'exact reviewed repair manifest differs')
    need(sha(read(root, 'reviewed-host-fixture-manifest.json')) == FIXTURE_MANIFEST_SHA256, 'exact reviewed host-only fixture manifest differs')
    for name in ('historical-inventory-references.json', 'p0-frozen-payload-manifest.json', 'p0-native-owner-identities.json', 'validate_contracts.py',
                 *('owners/'+owner+'/runtime-contract.json' for owner in set(PARENTS)-{'AnisetteKit'}),
                 *(name for name in prior['files'] if name.startswith('inherited/'))):
        need(read(root, name) == read(prior_root, name), 'unrelated historical metadata changed: '+name)
    need(set(value['owners']) == set(PARENTS), 'bounds seven-owner graph required')
    for owner, row in value['owners'].items():
        previous = prior['owners'][owner]
        need(set(row) == set(previous) and all(row[k] == previous[k] for k in ('repository', 'source_checkpoint', 'path')), 'bounds owner origin/checkpoint differs')
        fullsha(row['commit']); fullsha(row['tree'])
        need(previous['commit'] == PARENTS[owner], 'wrong historical source owner')
        if owner in CHANGED_OWNERS:
            need(row['commit'] != previous['commit'] and row['tree'] != previous['tree'], 'bounds successor pending')
        else:
            need(row == previous, 'unrelated source owner changed')
    validate_transitions(root, value, prior_root, prior)
    native = value['native_validation']
    need(isinstance(native, dict) and set(native) == (set(prior['native_validation'])-{'inherited_p0_review_sha256'}) | {'inherited_constructor_review_sha256'}, 'bounds native schema differs')
    need(native['source_basis'] == BASIS and native['inherited_constructor_review_sha256'] == historical.DIAGNOSTIC_REVIEW_SHA256 and
         native['readiness_scope'] == 'eligible_for_gated_full_build' and native['review_sha256'] is None, 'bounds native review identity differs')
    old_runs = [prior['native_validation'], *[parse(read(prior_root, owner+'-resolver.json')) for owner in LOCK_FILES]]
    _, older, _ = p0.approval(integration_root)
    old_runs += [older['native_validation']]
    def fresh_run(run):
        run_identity(run)
        need(all(run['run_url'] != old['run_url'] and run['artifact_id'] != old['artifact_id'] for old in old_runs), 'historical execution cannot prove bounds successor')
    fresh_run(native)
    need(set(native['tested_owners']) == set(PARENTS), 'bounds tested graph incomplete')
    for owner, tested in native['tested_owners'].items():
        need(set(tested) == {'commit', 'tree'}, 'bounds tested owner schema differs')
        fullsha(tested['commit']); fullsha(tested['tree'])
        if owner not in LOCK_FILES:
            need(tested == {k:value['owners'][owner][k] for k in ('commit','tree')}, 'bounds owner changed after native capture')
    children = child_map(root)
    prior_children = child_map(prior_root)
    need(set(children) == set(prior_children), 'bounds child closure differs')
    for name in children:
        if name == 'SideStore/Dependencies/SideSign':
            row = value['owners']['SideSign']
            need(children[name] == {'repository':row['repository'], 'declared_repository':row['repository'], 'commit':row['commit'], 'tree':row['tree']}, 'bounds SideSign child differs')
        else:
            need(children[name] == prior_children[name], 'bounds unrelated child changed')
    need(native['tested_children'] == {'SideSign':{}, 'SideStore':{'Dependencies/SideSign':value['owners']['SideSign']['commit'], 'Dependencies/minimuxer':value['owners']['minimuxer']['commit']}}, 'bounds tested child graph differs')
    receipt = parse(read(root, 'native-receipt.json'))
    need(set(receipt) == {'status','source_basis','native_validation','native_builds','metadata_helper','historical_evidence','ordinary_native_boundary','consumer_suites','phase_evidence','tested_submodules','guest_execution','ADI_executed','device_validation'}, 'bounds receipt schema differs')
    need(receipt['status'] == 'INDEPENDENTLY_REVIEWED_P0_ELF_METADATA_BOUNDS_NATIVE_PASS' and receipt['source_basis'] == BASIS and receipt['native_validation'] == native and
         receipt['native_builds'] == prior_receipt['native_builds'] and receipt['tested_submodules'] == children and
         all(receipt[k] is False for k in ('guest_execution','ADI_executed','device_validation')), 'bounds receipt unsupported claim')
    helper = validate_helper(parse(read(root, 'metadata-helper.json')))
    need(receipt['metadata_helper'] == {'source_owner':AK_COMMIT, 'source_tree':AK_TREE, 'receipt_sha256':sha(read(root,'metadata-helper.json')), 'checks_per_variant':713, 'variants':['normal','optimized','sanitized'], 'skipped':0}, 'bounds helper binding differs')
    exact_counts(receipt['metadata_helper'], {'checks_per_variant':713,'skipped':0}, 'bounds metadata helper')
    historical_evidence = parse(read(root, 'historical-evidence.json'))
    need(historical_evidence == {'role':'historical-only','review_sha256':historical.DIAGNOSTIC_REVIEW_SHA256,
        'canonical_native_fork83':prior_receipt['canonical_native_fork83'], 'constructor_diagnostics':prior_receipt['constructor_diagnostics'],
        'fresh_guest_execution':False,'full_loader_changed':True,'fingerprints_sha256':sha(read(root,'historical-source-fingerprints.json'))}, 'historical guest evidence relabeled or altered')
    need(receipt['historical_evidence'] == historical_evidence, 'bounds historical reference differs')
    boundary = receipt['ordinary_native_boundary']
    need(set(boundary) == {'checks','failures','skipped'} , 'ordinary native boundary schema differs')
    exact_counts(boundary, {'checks':8,'failures':0,'skipped':0}, 'ordinary native boundary')
    suites=receipt['consumer_suites']
    need(set(suites) == {'tested_owners',*CONSUMER_SUITE_IDS} and suites['tested_owners'] == {owner:native['tested_owners'][owner] for owner in ('AnisetteKit','SideStore','LiveContainer')}, 'bounds consumer source graph differs')
    for name, ids in CONSUMER_SUITE_IDS.items():
        suite=suites[name]
        need(set(suite)=={'tests','failures','errors','skipped','test_ids'} and suite['test_ids']==list(ids), 'bounds consumer IDs differ')
        exact_counts(suite, {'tests':len(ids),'failures':0,'errors':0,'skipped':0}, 'bounds consumer '+name)
    phases=receipt['phase_evidence']
    need(set(phases)==set(LOCK_FILES), 'bounds fresh phase evidence incomplete')
    linked=(*RUN_FIELDS,'resolver_tested_commit','resolver_tested_tree','lock_before_sha256','lock_sha256','origin_hash')
    for owner in LOCK_FILES:
        resolver=parse(read(root,owner+'-resolver.json'));phase=phases[owner]
        need(resolver.get('status')=='CAPTURED_FOR_REVIEW' and resolver.get('purpose')=='independently_reviewed_p0_elf_metadata_bounds_swiftpm_resolution' and
             resolver.get('owner')==owner and resolver.get('source_basis')==BASIS and resolver.get('anisette_commit')==AK_COMMIT, 'bounds resolver identity differs')
        need(set(phase)=={'status',*linked} and phase['status']=='CAPTURED_FOR_REVIEW' and all(key in resolver and resolver[key]==phase[key] for key in linked), 'bounds resolver/native linkage differs')
        fresh_run(resolver)
        for key in ('resolver_tested_commit','resolver_tested_tree'):fullsha(resolver[key])
        for suffix,key in (('before','lock_before_sha256'),('after','lock_sha256')):
            need(sha(read(root,'resolver-locks/'+owner+'.Package.resolved.'+suffix))==digest(resolver[key]), 'bounds actual lock bytes differ')
        lock=parse(read(root,'resolver-locks/'+owner+'.Package.resolved.after'))
        need(resolver['origin_hash']=={'present':'originHash' in lock,'value':lock.get('originHash')}, 'bounds resolver originHash differs')
        if owner=='SideStore':
            need(all(phase[key]==native[key] for key in RUN_FIELDS) and native['tested_owners'][owner]=={'commit':resolver['resolver_tested_commit'],'tree':resolver['resolver_tested_tree']}, 'bounds final graph phase differs')
    registry=parse(read(root,'compatibility-registry.json'))
    need(set(registry['owners'])==set(PARENTS), 'bounds contract owners differ')
    refs={registry['evidence']['path']:registry['evidence']['sha256'], **{pin['manifest_path']:pin['manifest_sha256'] for pin in registry['owners'].values()}}
    need(all(value['files'].get(name)==expected for name,expected in refs.items()), 'bounds contract hashes not sealed')
    return root,value,receipt


def validate_pins(document, review):
    need(set(document) == {'schema_version', 'integration_baseline', 'source_basis', 'contract_registry_sha256', 'owners', 'native_validation'}, 'bounds pin schema differs')
    need(type(document['schema_version']) is int and document['schema_version'] == 1 and document['integration_baseline'] ==
         '141776ba6ba38fc04a5e77f68b0cfc4e6c8842ee' and document['source_basis'] == BASIS, 'bounds pin basis differs')
    need(document['owners'] == {owner:{k:v for k,v in row.items() if k!='tree'} for owner,row in review['owners'].items()} and
         document['contract_registry_sha256'] == review['files']['compatibility-registry.json'] and
         document['native_validation'] == {**review['native_validation'], 'review_sha256':REVIEW_SHA256}, 'bounds caller pins differ')
    return document


def load_pins(document, integration_root, legacy):
    _, review, _ = approval(integration_root)
    validate_pins(document, review)
    need(all(row['path'] == legacy.PATHS[owner] for owner,row in document['owners'].items()), 'bounds owner paths differ')
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
    prior = parse(read(root, 'inherited/p0-constructor-diagnostics-v1/anisette-generated/maintained-source-manifest.json'))
    need(manifest.get('source_basis') == BASIS and manifest.get('anisettekit_revision') == review['owners']['AnisetteKit']['commit'] and
         manifest.get('anisettekit_tree') == review['owners']['AnisetteKit']['tree'], 'bounds Anisette identity differs')
    rows = manifest.get('files'); need(isinstance(rows,list) and rows and len({row['path'] for row in rows}) == len(rows), 'bounds Anisette inventory malformed')
    need({row['path'] for row in prior['files']} | {'Native/Loader/elf_metadata_bounds.h'} <= {row['path'] for row in rows}, 'bounds Anisette sources missing')
    for row in rows: relative(row['path']); digest(row['sha256'])
    literals = manifest.get('required_compiled_evidence', {}).get('literals')
    need(isinstance(literals,list) and all(item in literals for item in prior['required_compiled_evidence']['literals']) and
         set(REQUIRED_DIAGNOSTIC_LITERALS) <= {row.get('value') for row in literals}, 'bounds/inherited compiled literals missing')
    return manifest


def verify_transitions(roots, review, root, legacy):
    sys.path.insert(0,str(legacy.ROOT))
    from prepare_candidate_inventory import snapshot
    transitions = parse(read(root,'source-transition.json'))
    for owner, source in roots.items():
        need(snapshot(source,PARENTS[owner],review['owners'][owner]['commit']) == transitions[owner], 'actual bounds transition differs')
        ancestry = legacy.git(source,'rev-list','--parents',PARENTS[owner]+'..'+review['owners'][owner]['commit']).decode().splitlines()
        need(all(len(line.split()) == 2 for line in ancestry), 'bounds transition merged another lane')
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
    verify_ak_lineage(roots['AnisetteKit'],legacy.git,snapshot)
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
