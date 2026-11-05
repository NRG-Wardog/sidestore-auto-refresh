"""Exact reviewed additive Russian setup variant; frozen four-language bytes stay intact."""
import copy
import hashlib
import json
from pathlib import Path
HERE = Path(__file__).resolve().parent
REVIEWED_INPUT = 'LiveContainer/tests/fixtures/russian_reviewed_existing.json'

def require(ok, message):
    if not ok: raise ValueError(message)

def extension():
    path = HERE / 'russian-setup-extension.json'; data = path.read_bytes(); value = json.loads(data)
    frozen = HERE / 'setup-state-recovery-contract.json'
    original = json.loads(frozen.read_text())['case_names']['setup-localization']
    require(value.get('schema') == 'reviewed-russian-setup-extension-v1' and value.get('frozen_contract_sha256') == hashlib.sha256(frozen.read_bytes()).hexdigest(), 'Russian extension frozen contract differs')
    require(value.get('original_case_count') == len(original) == 1292, 'Russian extension original count differs')
    extra = value.get('additional_cases')
    require(isinstance(extra,list) and len(extra) == len(set(extra)) == 467 and extra == sorted(extra) and not set(extra)&set(original), 'Russian exact additions differ')
    require(all(isinstance(c,str) and ('-ru-' in c or c.endswith('-ru')) for c in extra), 'Russian additions contain unreviewed locale')
    require(value.get('expected_case_count') == 1759 and value.get('localized_values') == 920 and value.get('baseline_missing') == 344, 'Russian coverage extension differs')
    return value, original, hashlib.sha256(data).hexdigest()

def add(contract):
    value, original, digest = extension(); result=copy.deepcopy(contract)
    require(result['case_names']['setup-localization'] == original, 'Russian projection requires original ordered cases unchanged')
    require(all(result.get('input_sha256',{}).get(p)==sha for p,sha in value['candidate_inputs'].items()), 'Russian projection source inputs not exactly bound')
    result['case_names']['setup-localization'] = sorted(original + value['additional_cases'])
    require([c for c in result['case_names']['setup-localization'] if c in set(original)] == original, 'Russian projection reordered original cases')
    result['russian_setup_extension']={'manifest_sha256':digest}
    return result

def variant(contract):
    if 'russian_setup_extension' not in contract:
        return {'cases':1292,'values':736,'baseline_missing':160}
    value, original, digest=extension()
    require(contract['russian_setup_extension']=={'manifest_sha256':digest}, 'Russian extension identity differs')
    require(contract.get('case_names',{}).get('setup-localization') == sorted(original + value['additional_cases']), 'Russian exact ordered case extension differs')
    require(all(contract.get('input_sha256',{}).get(p)==sha for p,sha in value['candidate_inputs'].items()), 'Russian extension source hashes differ')
    return {'cases':1759,'values':920,'baseline_missing':344}
