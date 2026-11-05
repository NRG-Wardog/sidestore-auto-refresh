"""Exact two-message/five-locale addition; prior Russian and frozen cases unchanged."""
import copy
import hashlib
import json
from pathlib import Path
from russian_setup_projection import variant as prior_variant
HERE=Path(__file__).resolve().parent
KEY_MANIFEST='LiveContainer/.ci/refresh-truth/localization_keys.json'
KEYS=(
 'Certificate verification is unavailable. This does not mean the certificate was revoked. Check the connection and review this app’s verification status.',
 'Review the observed profile validity and signing setup before another action. No automatic retry will occur.',
)
EXTRA=sorted(prefix+'-'+locale+'-'+key for prefix in ('actual-source-copy','baseline-reproduces-missing-owned-copy') for locale in ('en','he','ru','zh-Hans','zh-Hant') for key in KEYS)
def require(ok,message):
 if not ok:raise ValueError(message)
def extension():
 data=(HERE/'refresh-truth-setup-extension.json').read_bytes();value=json.loads(data)
 require(set(value)=={'schema','additional_cases','candidate_inputs','key_count','localized_values','baseline_missing','expected_case_count'},'refresh setup extension closure differs')
 require(value['schema']=='reviewed-refresh-truth-setup-extension-v1' and value['additional_cases']==EXTRA,'refresh setup exact additions differ')
 require((value['key_count'],value['localized_values'],value['baseline_missing'],value['expected_case_count'])==(186,930,354,1779),'refresh setup exact counts differ')
 require(set(value['candidate_inputs'])=={KEY_MANIFEST,'LiveContainer/Resources/Localizable.xcstrings','LiveContainer/.ci/comprehensive-diagnostics/generate_setup_localization_fixture.py','LiveContainer/.ci/comprehensive-diagnostics/test_setup_localization_contract.py'},'refresh setup input closure differs')
 return value,hashlib.sha256(data).hexdigest()
def binding(contract,value):
 require(all(contract['input_sha256'].get(p)==sha for p,sha in value['candidate_inputs'].items()),'refresh setup source hashes differ')
def add(contract):
 require(prior_variant(contract)=={'cases':1759,'values':920,'baseline_missing':344},'refresh setup requires exact prior Russian coverage')
 value,digest=extension();binding(contract,value);result=copy.deepcopy(contract)
 old=result['case_names']['setup-localization'];require(not set(old)&set(EXTRA),'refresh setup duplicates prior cases')
 result['case_names']['setup-localization']=sorted(old+EXTRA)
 result['refresh_truth_setup_extension']={'manifest_sha256':digest}
 return result
def variant(contract):
 if 'refresh_truth_setup_extension' not in contract:return prior_variant(contract)
 value,digest=extension();binding(contract,value)
 require(contract['refresh_truth_setup_extension']=={'manifest_sha256':digest},'refresh setup identity differs')
 cases=contract['case_names']['setup-localization']
 require(cases==sorted(cases) and len(cases)==len(set(cases))==1779 and set(EXTRA)<=set(cases),'refresh setup ordered cases differ')
 prior=copy.deepcopy(contract);prior.pop('refresh_truth_setup_extension');prior['case_names']['setup-localization']=[c for c in cases if c not in EXTRA]
 require(prior_variant(prior)=={'cases':1759,'values':920,'baseline_missing':344},'refresh setup altered prior coverage')
 return {'cases':1779,'values':930,'baseline_missing':354,'keys':186}
