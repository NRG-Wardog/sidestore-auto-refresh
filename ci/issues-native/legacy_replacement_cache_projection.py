"""Exact opt-in replacement delta and strict-digest staging caller, no cache rewrite."""
import transport_observation_cache_projection as prior
from transport_observation_cache_projection import binding
PATH='SideStore/tests/cache_compatibility/reviewed-legacy-replacement-host-delta.json'
SHA='db49f4a5136dd8fd2d0dfbbdbb9fb56401fd1ea9ee338e6376fbe57d7caebc59'
STAGE='SideStore/SideStore/Core/Operations/PipelineOperations/StageAppOperation.swift'
STAGE_SHA='c5becaa90cfcfcab29ed5658bd1447dcbe5e3056658a9715f09821dc92807553'
KEY='reviewed_legacy_replacement_cache_host_change'
def require(ok,why):
 if not ok:raise ValueError(why)
def legacy_binding(contract):
 value=contract.get(KEY)
 if value is None:return False
 require(value=={'delta_path':PATH,'delta_sha256':SHA,'stage_path':STAGE,'stage_sha256':STAGE_SHA},'Unknown legacy replacement cache delta')
 require(contract['input_sha256'].get(PATH)==SHA and contract['input_sha256'].get(STAGE)==STAGE_SHA,'Unbound legacy replacement cache delta/caller')
 require(prior.observation_binding(contract),'Legacy replacement requires prior observation proof')
 return True
def inputs(contract,original):
 result=prior.inputs(contract,original)
 if legacy_binding(contract):
  require(PATH not in result['fixture'] and STAGE not in result['production'],'Duplicate legacy replacement cache input')
  result['fixture'].append(PATH);result['production'].append(STAGE)
 return result
def add(contract):
 result=prior.add(contract)
 if PATH in result['input_sha256']:
  require(KEY not in result,'Legacy replacement cache proof already declared')
  result[KEY]={'delta_path':PATH,'delta_sha256':SHA,'stage_path':STAGE,'stage_sha256':STAGE_SHA};legacy_binding(result)
  section=result['additive_suite_inputs']['cache-compatibility']
  require(PATH not in section['fixture'] and STAGE not in section['production'],'Duplicate legacy replacement cache input')
  section['fixture'].append(PATH);section['production'].append(STAGE)
 return result
def verify_proof(value,contract):
 result=prior.verify_proof(value,contract)
 if legacy_binding(contract):
  require(value.get('reviewed_legacy_replacement_delta_sha256')==SHA,'Legacy replacement cache proof differs')
 else:require('reviewed_legacy_replacement_delta_sha256' not in value,'Unexpected legacy replacement cache proof')
 return result
