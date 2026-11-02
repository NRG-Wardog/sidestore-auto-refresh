"""Exact progress-owner Runner delta over unchanged cache preservation proofs."""
import copy
import profile_transaction_cache_projection as prior
PATH='SideStore/tests/cache_compatibility/reviewed-progress-owner-host-delta.json'
SHA='f3dbe3109c02e709ce4b37b04f0f2fdbf6ed4b20298396a7e288157af052a55b'
RUNNER=prior.RUNNER
RUNNER_SHA='ca520c43ab89775f602ffe676405bb5df5aa65b14383cc736929342a6c5fa453'
KEY='reviewed_progress_owner_cache_host_change'
PROOF='reviewed_progress_owner_delta_sha256'
require=prior.require
binding=prior.binding

def descriptor():
 return {'delta_path':PATH,'delta_sha256':SHA,'runner_path':RUNNER,'prior_runner_sha256':prior.RUNNER_SHA,'runner_sha256':RUNNER_SHA}
def current_binding(contract):
 if KEY not in contract:return False
 require(contract[KEY]==descriptor(),'Unknown progress owner successor')
 require(contract['input_sha256'].get(PATH)==SHA and contract['input_sha256'].get(RUNNER)==RUNNER_SHA,'Unbound progress owner successor')
 require(prior.KEY in contract,'Missing prior preservation cache proof')
 return True
def normalized(contract):
 result=copy.deepcopy(contract)
 if current_binding(result):
  result.pop(KEY);result['input_sha256'][RUNNER]=prior.RUNNER_SHA
  fixture=result['additive_suite_inputs']['cache-compatibility']['fixture']
  require(fixture.count(PATH)==1,'Missing or duplicate progress owner delta input')
  fixture.remove(PATH);prior.current_binding(result)
 return result
def add(contract):
 if PATH not in contract['input_sha256']:return prior.add(contract)
 require(KEY not in contract,'Progress owner successor already projected')
 require(contract['input_sha256'].get(PATH)==SHA and contract['input_sha256'].get(RUNNER)==RUNNER_SHA,'Unreviewed progress owner source')
 old=copy.deepcopy(contract);old['input_sha256'][RUNNER]=prior.RUNNER_SHA
 result=prior.add(old);result['input_sha256'][RUNNER]=RUNNER_SHA;result[KEY]=descriptor()
 fixture=result['additive_suite_inputs']['cache-compatibility']['fixture'];require(PATH not in fixture,'Duplicate progress owner delta');fixture.append(PATH)
 normalized(result);return result
def inputs(contract,original):
 result=prior.inputs(normalized(contract),original)
 if current_binding(contract):
  require(PATH not in result['fixture'],'Duplicate progress owner input');result['fixture'].append(PATH)
 return result
def verify_proof(value,contract):
 evidence=copy.deepcopy(value)
 if current_binding(contract):
  require(evidence.pop(PROOF,None)==SHA,'Progress owner preservation proof differs')
 else:require(PROOF not in evidence,'Unexpected progress owner proof')
 return prior.verify_proof(evidence,normalized(contract))
