"""Exact fixture-only readiness dependency closure; historical cases stay fixed."""
import copy,hashlib,json
from pathlib import Path
API='SideStore/Dependencies/minimuxer/Sources/MinimuxerApi.swift'
GENERATOR='LiveContainer/.ci/early-refresh-boundaries/generate_regression.py'
GENERATOR_SHA='b1aaedca5377dec52905e80d467c9a7ddfabf1a31eda747848aadd8db21f056e'
KEY='reviewed_early_readiness_dependency'
def require(ok,why):
 if not ok:raise ValueError(why)
def binding(contract):
 value=contract.get(KEY)
 if value is None:
  require(contract['input_sha256'].get(GENERATOR)!=GENERATOR_SHA,'Current early fixture dependency must be bound')
  return None
 expected={'path':API,'source_sha256':contract['input_sha256'][API],'generator_sha256':GENERATOR_SHA}
 require(value==expected and contract['input_sha256'].get(GENERATOR)==GENERATOR_SHA,'Unknown or unbound early readiness dependency')
 return value
def inputs(contract,original,profile=False):
 result=copy.deepcopy(original)
 if binding(contract):
  require(API not in result['support'],'Duplicate early readiness dependency')
  if profile:
   index=result['support'].index(GENERATOR);result['support'].insert(index,API)
  else:result['support'].append(API)
 return result
def add(contract):
 result=copy.deepcopy(contract);require(KEY not in result,'Early dependency already declared')
 result[KEY]={'path':API,'source_sha256':result['input_sha256'][API],'generator_sha256':GENERATOR_SHA};binding(result)
 result['suite_inputs']['early-refresh-boundaries']=inputs(result,result['suite_inputs']['early-refresh-boundaries'])
 result['additive_suite_inputs']['profile-preconditions']=inputs(result,result['additive_suite_inputs']['profile-preconditions'],profile=True)
 return result

TEST_SOURCE='LiveContainer/.ci/early-refresh-boundaries/test_contract.py'
TEST_SOURCE_SHA='df84d6d696f83409955e65fdef04a370c74c700ff4f70a5949bec0e8564f5135'
def static_names():
 data=json.loads(Path(__file__).with_name('early_static_controls.json').read_text())
 require(data['schema']=='reviewed-early-static-control-names-v1','Early static name schema differs')
 old,new=data['historical_checks'],data['current_checks']
 def digest(names):return hashlib.sha256(json.dumps(names,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 require(len(old)==len(set(old))==102 and digest(old)=='e0203be3b51171f4bd10207ecc1a3aa3eec70f79bb2a9e0daebbc092d675e597','Historical static names changed')
 require(len(new)==len(set(new))==115 and digest(new)=='54e2328039c4702299cbd2faafcf711cb68d8da54a086d9901c265312b9c1a3d','Reviewed current static names changed')
 require([name for name in new if name in old]==old,'Historical static checks removed or reordered')
 return new

def verify_static(value,contract):
 count=102
 if binding(contract):
  require(contract['input_sha256'].get(TEST_SOURCE)==TEST_SOURCE_SHA,'Unbound early static-control source')
  require(value.get('checks')==static_names(),'Early-refresh exact current static controls differ')
  count=115
 require(value.get('schema')=='early-refresh-portable-contract-v1' and
         value.get('status')=='STATIC_PASS_RUNTIME_NOT_RUN' and
         type(value.get('check_count')) is int and value['check_count']==len(value.get('checks',[]))==count and
         value.get('expected_generic_baseline_cases')==12 and
         value.get('variants')==['baseline','candidate'] and
         value.get('swift_compiled') is False and value.get('swift_executed') is False,
         'Early-refresh static controls differ')
