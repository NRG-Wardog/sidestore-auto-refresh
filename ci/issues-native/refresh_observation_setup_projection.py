"""Exact historical transport-copy replacement plus finite guard/recovery copy.
Normalizes only reviewed cases back to the unchanged prior validator.
"""
import copy, hashlib, json, re
from pathlib import Path
from refresh_truth_setup_projection import variant as prior_variant
HERE=Path(__file__).resolve().parent
KEY_MANIFEST='LiveContainer/.ci/refresh-observations/localization_keys.json'
REPLACEMENT_KEYS={'Device connection verified by the latest successful refresh. This is not a live VPN check.': 'Device connection was available during this refresh. This is not a live check; connection settings may have changed.', 'Refresh verified; CoreDevice transport was not independently recorded.': 'Device connection was observed during this refresh; CoreDevice transport was not independently recorded. This does not verify renewal.'}
ADDITIONAL_KEYS=('A required app extension has no selected provisioning profile.', 'A selected extension profile does not authorize that extension.', 'A selected extension profile does not contain the verified signing certificate.', 'A selected extension profile has already expired.', 'An app extension is outside the parent app identity.', 'An extension profile belongs to a different team than the main profile.', 'The installed app record required for this refresh is missing.', 'The main app provisioning profile could not be selected.', 'The older host app identity could not be safely verified for this refresh.', 'The older installed app identity could not be safely matched to the new profiles.', 'The profile selection contains an unexpected profile UUID.', 'The profile selection contains unexpected app identities.', 'The refresh has no provisioning profiles to install.', 'The same profile UUID refers to conflicting profile contents.', 'The selected main profile does not authorize the app identity.', 'The selected main profile does not contain the verified signing certificate.', 'The selected main profile has already expired.', 'The target app bundle required for this refresh is missing.', 'The verified signing certificate required to select profiles is missing.', 'This target stopped before profile installation. Copy Diagnostics to check its profile selection. Do not reset account or VPN settings based on this error alone.')
INPUTS=('LiveContainer/.ci/refresh-observations/localization_keys.json', 'LiveContainer/Resources/Localizable.xcstrings', 'LiveContainer/.ci/comprehensive-diagnostics/generate_setup_localization_fixture.py', 'LiveContainer/.ci/comprehensive-diagnostics/test_setup_localization_contract.py', 'LiveContainer/LiveContainerSwiftUI/Views/V3UnifiedShell.swift', 'LiveContainer/SideStoreSupport/SideStore.swift')
REPLACEMENTS={prefix+'-'+locale+'-'+old:prefix+'-'+locale+'-'+new for prefix in ('actual-source-copy','baseline-reproduces-missing-owned-copy') for locale in ('en','he','ru','zh-Hans','zh-Hant') for old,new in REPLACEMENT_KEYS.items()}
EXTRA=sorted(prefix+'-'+locale+'-'+key for prefix in ('actual-source-copy','baseline-reproduces-missing-owned-copy') for locale in ('en','he','ru','zh-Hans','zh-Hant') for key in ADDITIONAL_KEYS)
PRIOR={'cases':1779,'values':930,'baseline_missing':354,'keys':186}
CURRENT={'cases':1979,'values':1030,'baseline_missing':454,'keys':206}
TAG='refresh_observation_setup_extension'
def require(ok,message):
 if not ok:raise ValueError(message)
def pairs(rows):
 result={}
 for key,value in rows:
  require(key not in result,'duplicate observation manifest key');result[key]=value
 return result
def extension():
 data=(HERE/'refresh-observation-setup-extension.json').read_bytes();value=json.loads(data,object_pairs_hook=pairs)
 require(type(value) is dict and set(value)=={'schema','replacements','additional_cases','candidate_inputs','key_count','localized_values','baseline_missing','expected_case_count'},'observation extension closure differs')
 require(value['schema']=='reviewed-refresh-observation-setup-extension-v1' and value['replacements']==REPLACEMENTS and value['additional_cases']==EXTRA,'observation exact cases differ')
 require(all(type(value[k]) is int and value[k]==n for k,n in [('key_count',206),('localized_values',1030),('baseline_missing',454),('expected_case_count',1979)]),'observation counts differ')
 require(type(value['candidate_inputs']) is dict and set(value['candidate_inputs'])==set(INPUTS),'observation source closure differs')
 require(all(type(h) is str and re.fullmatch('[0-9a-f]{64}',h) for h in value['candidate_inputs'].values()),'observation source digest invalid')
 return value,hashlib.sha256(data).hexdigest()
def binding(contract,value):
 require(all(contract['input_sha256'].get(p)==h for p,h in value['candidate_inputs'].items()),'observation source hashes differ')
def add(contract):
 require(TAG not in contract and prior_variant(contract)==PRIOR,'observation requires exact prior setup coverage')
 value,digest=extension();binding(contract,value);result=copy.deepcopy(contract)
 cases=result['case_names']['setup-localization']
 require(len(cases)==len(set(cases))==1779 and set(REPLACEMENTS)<=set(cases),'observation reviewed originals missing')
 require(not (set(REPLACEMENTS.values())|set(EXTRA))&set(cases),'observation new cases overlap prior')
 result['case_names']['setup-localization']=sorted([REPLACEMENTS.get(c,c) for c in cases]+EXTRA)
 result[TAG]={'manifest_sha256':digest}
 require(variant(result)==CURRENT,'observation projected coverage differs')
 return result
def variant(contract):
 if TAG not in contract:return prior_variant(contract)
 value,digest=extension();binding(contract,value)
 require(contract[TAG]=={'manifest_sha256':digest},'observation identity differs')
 cases=contract['case_names']['setup-localization']
 require(type(cases) is list and all(type(c) is str for c in cases) and cases==sorted(cases) and len(cases)==len(set(cases))==1979,'observation ordered case count differs')
 require((set(REPLACEMENTS.values())|set(EXTRA))<=set(cases) and not set(REPLACEMENTS)&set(cases),'observation exact replacements/additions missing')
 inverse={new:old for old,new in REPLACEMENTS.items()}
 prior=copy.deepcopy(contract);prior.pop(TAG)
 prior['case_names']['setup-localization']=sorted(inverse.get(c,c) for c in cases if c not in EXTRA)
 require(prior_variant(prior)==PRIOR,'observation altered unreviewed historical coverage')
 return dict(CURRENT)
