"""Exact fixture-only readiness dependency closure; historical cases stay fixed."""
import copy,hashlib,json
from pathlib import Path
API='SideStore/Dependencies/minimuxer/Sources/MinimuxerApi.swift'
GENERATOR='LiveContainer/.ci/early-refresh-boundaries/generate_regression.py'
GENERATOR_SHA='777454bb47cb3d674688cfb44739f905d0a8749daec713d4e50f50c75592146a'
TRUTH_SUPPORT=('SideStore/SideStore/Core/Operations/PipelineOperations/RefreshAppOperation.swift', 'SideStore/SideStore/Core/Certificates/OCSPValidator.swift', 'SideStore/SideStore/Core/Operations/RefreshGroup.swift')
KEY='reviewed_early_readiness_dependency'
def require(ok,why):
 if not ok:raise ValueError(why)
def binding(contract):
 value=contract.get(KEY)
 if value is None:
  require(contract['input_sha256'].get(GENERATOR)!=GENERATOR_SHA,'Current early fixture dependency must be bound')
  return None
 expected={'path':API,'source_sha256':contract['input_sha256'][API],'generator_sha256':GENERATOR_SHA,'refresh_truth_support_sha256':{p:contract['input_sha256'][p] for p in TRUTH_SUPPORT}}
 require(value==expected and contract['input_sha256'].get(GENERATOR)==GENERATOR_SHA,'Unknown or unbound early readiness dependency')
 return value
def inputs(contract,original,profile=False):
 result=copy.deepcopy(original)
 if binding(contract):
  require(all(p not in result['support'] for p in (API,*TRUTH_SUPPORT)),'Duplicate early readiness dependency')
  if profile:
   index=result['support'].index(GENERATOR);result['support'][index:index]=[API,*TRUTH_SUPPORT]
  else:result['support'].extend([API,*TRUTH_SUPPORT])
 return result
def add(contract):
 result=copy.deepcopy(contract);require(KEY not in result,'Early dependency already declared')
 result[KEY]={'path':API,'source_sha256':result['input_sha256'][API],'generator_sha256':GENERATOR_SHA,'refresh_truth_support_sha256':{p:contract['input_sha256'][p] for p in TRUTH_SUPPORT}};binding(result)
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
 result=list(new)
 for variant in ('baseline','candidate'):
  anchor=variant+': candidate-only support '+API
  require(result.count(anchor)==1,'Early support anchor differs')
  index=result.index(anchor)+1
  result[index:index]=[variant+': candidate-only support '+path for path in TRUTH_SUPPORT]
 require(len(result)==len(set(result))==121 and [name for name in result if name in new]==new,'Early truth support checks altered prior checks')
 return result

def verify_static(value,contract):
 count=102
 if binding(contract):
  require(contract['input_sha256'].get(TEST_SOURCE)==TEST_SOURCE_SHA,'Unbound early static-control source')
  require(value.get('checks')==static_names(),'Early-refresh exact current static controls differ')
  count=121
 require(value.get('schema')=='early-refresh-portable-contract-v1' and
         value.get('status')=='STATIC_PASS_RUNTIME_NOT_RUN' and
         type(value.get('check_count')) is int and value['check_count']==len(value.get('checks',[]))==count and
         value.get('expected_generic_baseline_cases')==12 and
         value.get('variants')==['baseline','candidate'] and
         value.get('swift_compiled') is False and value.get('swift_executed') is False,
         'Early-refresh static controls differ')
