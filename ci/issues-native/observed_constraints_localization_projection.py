"""Exact observed-constraints copy successor; preserve the prior case validator."""
import copy
import preservation_localization_projection as prior
PINS=prior.PINS
INVENTORY_TEST=prior.INVENTORY_TEST
GENERATOR=prior.GENERATOR
HARNESS=prior.HARNESS
CURRENT=prior.CURRENT
TAG='observed_constraints_localization_extension'
COPY_REPLACEMENTS={'Profile-store expiration advanced. Preservation-mode targets were not re-signed or replaced. Selection of these renewed profiles by the app and its extensions has not been independently verified.': 'Profile-store expiration advanced while observed profile constraints were kept unchanged. Preservation-mode targets were not re-signed or replaced. This does not verify which profiles the app or its extensions use, future launch, or Keychain access.', 'Refresh stopped because the existing matching profile could not be verified on the device. The installed app was preserved.': 'Refresh stopped because the profiles could not be read and matched safely. The installed app was preserved.', 'Refresh stopped because existing profile authorizations are ambiguous or conflicting. The installed app was preserved.': 'Refresh stopped because observed profile constraints are ambiguous or conflicting. The installed app was preserved.'}
CASE_REPLACEMENTS={prefix+'-'+locale+'-'+old:prefix+'-'+locale+'-'+new
 for prefix in ('actual-source-copy','baseline-reproduces-missing-owned-copy')
 for locale in ('en','he','ru','zh-Hans','zh-Hant')
 for old,new in COPY_REPLACEMENTS.items() if old in prior.ADDITIONAL_KEYS}
require=prior.require
matches=prior.matches
def descriptor():
 return {'schema':'reviewed-observed-constraints-localization-v1','inputs':dict(PINS),
         'copy_replacements':dict(COPY_REPLACEMENTS),'case_replacements':dict(CASE_REPLACEMENTS),'counts':dict(CURRENT)}
def add(contract):
 require(TAG not in contract,'Observed constraints localization already projected')
 result=prior.add(contract)
 names=result['case_names']['setup-localization']
 require(len(CASE_REPLACEMENTS)==20 and set(CASE_REPLACEMENTS)<=set(names),'Missing exact prior constraint copy')
 require(not set(CASE_REPLACEMENTS.values())&set(names),'Duplicate observed constraint copy')
 result['case_names']['setup-localization']=sorted(CASE_REPLACEMENTS.get(n,n) for n in names)
 result[TAG]=descriptor();variant(result)
 return result
def normalized(contract):
 result=copy.deepcopy(contract)
 if TAG not in result:return result
 require(result[TAG]==descriptor(),'Unknown observed constraints localization successor')
 prior.bind(result)
 names=result['case_names']['setup-localization']
 require(type(names) is list and all(type(n) is str for n in names) and names==sorted(names) and len(names)==len(set(names))==CURRENT['cases'],'Observed constraints ordered case closure differs')
 require(set(CASE_REPLACEMENTS.values())<=set(names) and not set(CASE_REPLACEMENTS)&set(names),'Observed constraints exact replacements differ')
 inverse={v:k for k,v in CASE_REPLACEMENTS.items()}
 result['case_names']['setup-localization']=sorted(inverse.get(n,n) for n in names)
 result.pop(TAG)
 return result
def variant(contract):
 return prior.variant(normalized(contract))
