"""Read-only exact #56 admission. Execute trusted source, never artifact code."""
import argparse
import hashlib
import json
from pathlib import Path
import types
import initial_rejection_package_admission as projection

safe = projection.safe
HERE = Path(__file__).resolve().parent
REPORT = 'database-recovery-package-admission.json'

def trusted():
    contract = safe.decode(safe.read(HERE/'database-recovery-source-contract.json'))
    safe.require(set(contract) == {'schema', 'native_builder', 'trusted_helper_sha256', 'expected_source_sha256'}, 'Recovery contract closure')
    safe.require(contract['schema'] == 'reviewed-database-recovery-package-contract-v1', 'Recovery contract schema')
    path = HERE/'reviewed-native/run_database_recovery.py'
    raw = safe.read(path)
    safe.require(safe.sha(raw) == contract['trusted_helper_sha256'], 'Recovery reviewed helper changed')
    helper = types.ModuleType('_reviewed_database_recovery'); helper.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), helper.__dict__)
    return contract, helper

def verify(root, manifest, projector):
    root = Path(root).resolve(strict=True)
    contract, helper = trusted()
    source_contract, _, _, _, graph = projection.trusted()
    safe.require(contract['native_builder'] == source_contract['native_builder'], 'Recovery builder differs')
    files = ('database-recovery-tests.json', 'schema-acquisition.json', 'database-recovery-settings.json') + tuple(helper.flat(name) for name in helper.FILES)
    raw, binding, native = projection.projected_evidence(root, manifest, projector, source_contract, graph, files)
    value = safe.decode(raw['database-recovery-tests.json'])
    helper.verify_portable(value, graph['owners'], binding['native_identity'])
    safe.require(value['source_sha256'] == contract['expected_source_sha256'], 'Recovery exact reviewed source closure differs')
    helper.verify_effective(value, safe.sha(raw['database-recovery-tests.json']), binding['native_identity'], safe.decode(raw['database-recovery-settings.json']))
    safe.require(value['acquisition_sha256'] == safe.sha(raw['schema-acquisition.json']), 'Recovery acquisition differs')
    acquisition = safe.decode(raw['schema-acquisition.json'])
    safe.require(acquisition.get('identity') == binding['native_identity'], 'Recovery acquisition run differs')
    for owner in ('SideStore', 'LiveContainer'):
        expected = {'repository': graph['owners'][owner][0], 'commit': graph['owners'][owner][1], 'tree': graph['reviewed_deltas'][owner]['tree']}
        safe.require(acquisition['acquired_owners'][owner] == expected, 'Recovery acquired source differs')
    inventory = acquisition['source_inventory']['SideStore']
    safe.require({name: inventory[name]['sha256'] for name in contract['expected_source_sha256']} == contract['expected_source_sha256'], 'Recovery acquired bytes differ from reviewed closure')
    for name in helper.FILES:
        safe.require(value['evidence_sha256'][name] == safe.sha(raw[helper.flat(name)]), 'Recovery raw evidence differs: '+name)
    for name in ('source-before.json', 'source-after.json'):
        safe.require(safe.decode(raw[helper.flat(name)]) == value['source_sha256'], 'Recovery source seals differ')
    safe.require(safe.decode(raw[helper.flat('engine/report.json')]) == value['engine'], 'Recovery engine report differs')
    safe.require(safe.decode(raw[helper.flat('coredata/receipt.json')]) == value['coredata'], 'Recovery Core Data report differs')
    for name in ('database-recovery-tests.json','database-recovery-settings.json'):
        safe.require(safe.read(root/name) == raw[name], 'Recovery packaged/projected receipt differs')
    provenance = safe.decode(safe.read(root/'provenance.json'))
    safe.require(provenance.get('database_recovery_regression_sha256') == safe.sha(raw['database-recovery-tests.json']), 'Recovery package provenance differs')
    safe.require(provenance.get('database_recovery_settings_sha256') == safe.sha(raw['database-recovery-settings.json']), 'Recovery settings provenance differs')
    original = safe.regular(root/'original.ipa', 550*1024*1024)
    digest = hashlib.sha256()
    with original.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): digest.update(chunk)
    safe.require(type(manifest.get('ipa_bytes')) is int and original.stat().st_size == manifest['ipa_bytes'] and digest.hexdigest() == manifest.get('ipa_sha256'), 'Recovery input IPA differs')
    return {'schema': 'database-recovery-package-admission-v1', 'status': 'PASS_EXACT_SQLITE_AND_COREDATA_NOT_DEVICE',
            **binding, 'trusted_helper_sha256': contract['trusted_helper_sha256'],
            'input_ipa_sha256': digest.hexdigest(), 'input_ipa_bytes': original.stat().st_size,
            'device_executed': False, 'adapter_native_execution': False}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--root', type=Path, required=True); parser.add_argument('--projector', type=Path, required=True)
    args = parser.parse_args(); result = verify(args.root, safe.decode(safe.read(args.root/'input-manifest.json')), args.projector)
    (args.root/REPORT).write_text(json.dumps(result, sort_keys=True, indent=2)+'\n')
