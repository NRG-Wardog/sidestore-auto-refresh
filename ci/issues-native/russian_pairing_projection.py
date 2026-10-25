"""Finite Russian pairing case/resource extension, never a frozen baseline rewrite."""
import copy
import hashlib
import json
from pathlib import Path
HERE = Path(__file__).resolve().parent
CASE = 'localized-command-recovery-deferred-ru'
RESOURCE = 'TestLocalization.bundle/ru.lproj/Localizable.strings'
def require(ok, message):
    if not ok: raise ValueError(message)
def extension():
    data = (HERE/'russian-pairing-extension.json').read_bytes(); value = json.loads(data)
    frozen = (HERE/'comprehensive-diagnostics-contract.json').read_bytes()
    original = json.loads(frozen)['pairing_recovery_case_names']
    require(value.get('schema') == 'reviewed-russian-pairing-extension-v1' and value.get('frozen_contract_sha256') == hashlib.sha256(frozen).hexdigest(), 'Russian pairing frozen contract differs')
    require(len(original) == 31 and CASE not in original and original == sorted(original), 'Russian pairing original closure differs')
    return value, original, hashlib.sha256(data).hexdigest()
def add(contract):
    value, original, digest = extension(); result = copy.deepcopy(contract)
    require(result['pairing_recovery_case_names'] == original, 'Russian pairing original ordered cases differ')
    require(all(result['input_sha256'].get(p) == h for p,h in value['candidate_inputs'].items()), 'Russian pairing source inputs differ')
    result['pairing_recovery_case_names'] = sorted(original + [CASE])
    result['russian_pairing_extension'] = {'manifest_sha256':digest}
    return result
def resources(contract):
    if 'russian_pairing_extension' not in contract: return ()
    value, original, digest = extension()
    require(contract['russian_pairing_extension'] == {'manifest_sha256':digest}, 'Russian pairing extension identity differs')
    require(contract['pairing_recovery_case_names'] == sorted(original + [CASE]), 'Russian pairing ordered case closure differs')
    require(all(contract['input_sha256'].get(p) == h for p,h in value['candidate_inputs'].items()), 'Russian pairing source inputs differ')
    return (RESOURCE,)
