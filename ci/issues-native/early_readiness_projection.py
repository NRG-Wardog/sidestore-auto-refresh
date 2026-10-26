"""Exact fixture-only readiness dependency closure; historical cases stay fixed."""
import copy
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
