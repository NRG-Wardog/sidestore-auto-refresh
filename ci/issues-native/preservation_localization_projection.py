"""Exact preservation copy successor; normalize cases through unchanged prior coverage."""
import copy
from refresh_observation_setup_projection import variant as prior_variant
TAG='preservation_localization_extension'
INVENTORY_TEST='LiveContainer/.ci/comprehensive-diagnostics/test_preservation_localization_inventory.py'
GENERATOR='LiveContainer/.ci/comprehensive-diagnostics/generate_setup_localization_fixture.py'
PINS={'LiveContainer/.ci/comprehensive-diagnostics/generate_setup_localization_fixture.py': 'ce85f7037cb28e7e8d46a4e385c829e91b5602adeffea178d10141c61d336fe9', 'LiveContainer/.ci/comprehensive-diagnostics/test_setup_localization_contract.py': 'cc9995c57a2456dbb444346a525724a7cb6c5c21ea99d5c18ec04cad2185253e', 'LiveContainer/.ci/comprehensive-diagnostics/test_preservation_localization_inventory.py': 'e87e14e3bfd4ea4f6e7e7fdf52b450a0454c5386b383b7962a940ea766ba3de5', 'LiveContainer/SideStoreSupport/SideStore.swift': 'aac699207539d16af3280a2cacffa6811cc36224ee8a098790e53c5e4d49941a', 'LiveContainer/Resources/Localizable.xcstrings': '4db80b72c3ddcc4ca12651caf0dda7c0de30bde7e03bd45a60e76c4349659f90', 'LiveContainer/.ci/refresh-truth/localization_keys.json': 'f43fd7dbcb7f20c8b4fb228d3a6ad611dceae18f7ebe38e95b3494696cdbb2c1', 'LiveContainer/.ci/refresh-observations/localization_keys.json': 'b28ff28aacfde5d5b75c01b586527bc8efa5da388d6f1b0923533db49c91c4ec'}
REPLACEMENT_KEYS={'The older installed app identity could not be safely matched to the new profiles.': 'The installed app could not be linked to sufficient refresh evidence. This does not mean its signature is invalid. The app has not been replaced.'}
ADDITIONAL_KEYS=('Refresh stopped because a profile contains unsupported or incomplete authorization data. The installed app was preserved.', 'Refresh stopped because existing profile authorizations are ambiguous or conflicting. The installed app was preserved.', 'Refresh stopped because one profile identifier refers to conflicting profile contents. The installed app was preserved.', 'Refresh stopped because the device profile inventory changed during verification. The installed app was preserved.', 'Refresh stopped because the existing developer account app identity could not be matched without changing it. The installed app was preserved.', 'Refresh stopped because the existing matching profile could not be verified on the device. The installed app was preserved.', 'Refresh stopped because the existing signing certificate is no longer valid. Profile-only renewal cannot extend the certificate. The installed app was preserved.', 'Refresh stopped because the historical app identity and signing certificate could not be linked. The installed app was preserved.', 'Refresh stopped because the new profile does not extend effective validity. The installed app was preserved.', 'Refresh stopped because the new profile would change existing authorization or signing certificates. The installed app was preserved.')
REPLACEMENTS={prefix+'-'+locale+'-'+old:prefix+'-'+locale+'-'+new for prefix in ('actual-source-copy','baseline-reproduces-missing-owned-copy') for locale in ('en','he','ru','zh-Hans','zh-Hant') for old,new in REPLACEMENT_KEYS.items()}
EXTRA=sorted(prefix+'-'+locale+'-'+key for prefix in ('actual-source-copy','baseline-reproduces-missing-owned-copy') for locale in ('en','he','ru','zh-Hans','zh-Hant') for key in ADDITIONAL_KEYS)
PRIOR={'cases':1979,'values':1030,'baseline_missing':454,'keys':206}
CURRENT={'cases':2079,'values':1080,'baseline_missing':504,'keys':216}
def require(ok,why):
 if not ok:raise ValueError(why)
def descriptor():
 return {'schema':'reviewed-preservation-localization-successor-v1','inputs':dict(PINS),'replacements':dict(REPLACEMENTS),'additional_cases':list(EXTRA),'counts':dict(CURRENT)}
def matches(contract):
 return contract['input_sha256'].get(GENERATOR)==PINS[GENERATOR]
def bind(contract):
 require(all(contract['input_sha256'].get(p)==h for p,h in PINS.items()),'Unbound preservation localization source')
def add(contract):
 require(TAG not in contract and prior_variant(contract)==PRIOR,'Preservation localization requires exact prior coverage')
 bind(contract);result=copy.deepcopy(contract);cases=result['case_names']['setup-localization']
 require(set(REPLACEMENTS)<=set(cases),'Missing historical local identity copy')
 require(not (set(REPLACEMENTS.values())|set(EXTRA))&set(cases),'Duplicate preservation localization additions')
 result['case_names']['setup-localization']=sorted([REPLACEMENTS.get(c,c) for c in cases]+EXTRA)
 result[TAG]=descriptor();require(variant(result)==CURRENT,'Preservation localization projection differs')
 return result
def variant(contract):
 if TAG not in contract:return prior_variant(contract)
 require(contract[TAG]==descriptor(),'Unknown preservation localization successor');bind(contract)
 cases=contract['case_names']['setup-localization']
 require(type(cases) is list and all(type(c) is str for c in cases) and cases==sorted(cases) and len(cases)==len(set(cases))==CURRENT['cases'],'Preservation localization ordered closure differs')
 require((set(REPLACEMENTS.values())|set(EXTRA))<=set(cases) and not set(REPLACEMENTS)&set(cases),'Preservation localization exact additions/replacements missing')
 inverse={v:k for k,v in REPLACEMENTS.items()};old=copy.deepcopy(contract);old.pop(TAG)
 old['case_names']['setup-localization']=sorted(inverse.get(c,c) for c in cases if c not in EXTRA)
 require(prior_variant(old)==PRIOR,'Preservation localization changed historical coverage')
 return dict(CURRENT)
