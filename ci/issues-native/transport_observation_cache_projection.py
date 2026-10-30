"""Exact observation-only host delta layered before unchanged historical proofs."""
import copy
import cold_vpn_cache_projection as prior
from cold_vpn_cache_projection import binding
PATH='SideStore/tests/cache_compatibility/reviewed-transport-observation-host-delta.json'
SHA='14b1bb801ca53db5d1c9237aef1bf308fc6650ae1d5086582f22ccb52c392676'
KEY='reviewed_transport_observation_cache_host_change'
def require(ok,why):
 if not ok:raise ValueError(why)
def observation_binding(contract):
 value=contract.get(KEY)
 if value is None:return False
 require(value=={'delta_path':PATH,'delta_sha256':SHA},'Unknown transport observation cache delta')
 require(contract['input_sha256'].get(PATH)==SHA,'Unbound transport observation cache delta')
 require(prior.truth_binding(contract) is not None,'Observation proof requires historical refresh truth proof')
 return True
def inputs(contract,original):
 result=prior.inputs(contract,original)
 if observation_binding(contract):
  require(PATH not in result['fixture'],'Duplicate transport observation cache input')
  result['fixture'].append(PATH)
 return result
def add(contract):
 result=prior.add(contract)
 if PATH in result['input_sha256']:
  require(KEY not in result,'Transport observation cache proof already declared')
  result[KEY]={'delta_path':PATH,'delta_sha256':SHA};observation_binding(result)
  fixture=result['additive_suite_inputs']['cache-compatibility']['fixture']
  require(PATH not in fixture,'Duplicate transport observation cache input');fixture.append(PATH)
 return result
def verify_proof(value,contract):
 result=prior.verify_proof(value,contract)
 if observation_binding(contract):
  require(value.get('reviewed_transport_observation_delta_sha256')==SHA,'Transport observation cache proof differs')
 else:
  require('reviewed_transport_observation_delta_sha256' not in value,'Unexpected transport observation cache proof')
 return result
