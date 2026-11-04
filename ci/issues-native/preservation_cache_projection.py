"""Exact preserved-cache successor over the immutable legacy proof chain."""
import legacy_replacement_cache_projection as prior
from legacy_replacement_cache_projection import binding
PATH='SideStore/tests/cache_compatibility/reviewed-preservation-registry-host-delta.json'
SHA='23faaa6ba49ed9236c6f4c6377242bdb06afee47f4b799400f7f79c390b04ba7'
INSTALL='SideStore/SideStore/Core/Operations/PipelineOperations/InstallAppOperation.swift'
INSTALL_SHA='32d0e29015eb452c2206a488eae761fd2cd91d541ec1958844ba5d8cf02146d2'
RUNNER='SideStore/SideStore/Core/Operations/PipelineRunner.swift'
RUNNER_SHA='af4766d57bfec9c72ff16d93127bfd1ac2a42a7efc2b67acfc648e17ff2d7348'
KEY='reviewed_preservation_registry_cache_host_change'
def require(ok,why):
 if not ok:raise ValueError(why)
def descriptor():
 return {'delta_path':PATH,'delta_sha256':SHA,'install_path':INSTALL,'install_sha256':INSTALL_SHA,'runner_path':RUNNER,'runner_sha256':RUNNER_SHA}
def preservation_binding(contract):
 if KEY not in contract:return False
 require(contract[KEY]==descriptor(),'Unknown preservation cache successor')
 require(all(contract['input_sha256'].get(p)==h for p,h in [(PATH,SHA),(INSTALL,INSTALL_SHA),(RUNNER,RUNNER_SHA)]),'Unbound preservation cache inputs')
 require(prior.legacy_binding(contract),'Missing legacy cache proof layer')
 return True
def inputs(contract,original):
 result=prior.inputs(contract,original)
 if preservation_binding(contract):
  require(PATH not in result['fixture'] and INSTALL not in result['production'],'Duplicate preservation cache input')
  result['fixture'].append(PATH);result['production'].append(INSTALL)
 return result
def add(contract):
 result=prior.add(contract)
 if PATH in result['input_sha256']:
  require(KEY not in result,'Preservation cache already projected')
  result[KEY]=descriptor();preservation_binding(result)
  section=result['additive_suite_inputs']['cache-compatibility']
  require(PATH not in section['fixture'] and INSTALL not in section['production'],'Duplicate preservation cache input')
  section['fixture'].append(PATH);section['production'].append(INSTALL)
 return result
def verify_proof(value,contract):
 result=prior.verify_proof(value,contract)
 if preservation_binding(contract):
  require(value.get('reviewed_preservation_registry_delta_sha256')==SHA,'Preservation cache proof differs')
 else:require('reviewed_preservation_registry_delta_sha256' not in value,'Unexpected preservation cache proof')
 return result
