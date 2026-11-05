"""Exact transaction-lane Runner delta over unchanged cache preservation proofs."""
import copy
import preservation_cache_projection as prior
PATH='SideStore/tests/cache_compatibility/reviewed-profile-transaction-host-delta.json'
SHA='2a034d639266032185a06a9a48f976fe15f468121d958238513784c3162dc236'
RUNNER=prior.RUNNER
RUNNER_SHA='c9a92748bad7f96eeb49607a72b32dd1d199b247ec6e58f4631c135523dd6ada'
KEY='reviewed_profile_transaction_cache_host_change'
PROOF='reviewed_profile_transaction_delta_sha256'
require=prior.require
binding=prior.binding

def descriptor():
 return {'delta_path':PATH,'delta_sha256':SHA,'runner_path':RUNNER,'prior_runner_sha256':prior.RUNNER_SHA,'runner_sha256':RUNNER_SHA}
def current_binding(contract):
 if KEY not in contract:return False
 require(contract[KEY]==descriptor(),'Unknown profile transaction successor')
 require(contract['input_sha256'].get(PATH)==SHA and contract['input_sha256'].get(RUNNER)==RUNNER_SHA,'Unbound profile transaction successor')
 require(prior.KEY in contract,'Missing prior preservation cache proof')
 return True
def normalized(contract):
 result=copy.deepcopy(contract)
 if current_binding(result):
  result.pop(KEY);result['input_sha256'][RUNNER]=prior.RUNNER_SHA
  fixture=result['additive_suite_inputs']['cache-compatibility']['fixture']
  require(fixture.count(PATH)==1,'Missing or duplicate profile transaction delta input')
  fixture.remove(PATH);prior.preservation_binding(result)
 return result
def add(contract):
 if PATH not in contract['input_sha256']:return prior.add(contract)
 require(KEY not in contract,'Profile transaction successor already projected')
 require(contract['input_sha256'].get(PATH)==SHA and contract['input_sha256'].get(RUNNER)==RUNNER_SHA,'Unreviewed profile transaction source')
 old=copy.deepcopy(contract);old['input_sha256'][RUNNER]=prior.RUNNER_SHA
 result=prior.add(old);result['input_sha256'][RUNNER]=RUNNER_SHA;result[KEY]=descriptor()
 fixture=result['additive_suite_inputs']['cache-compatibility']['fixture'];require(PATH not in fixture,'Duplicate profile transaction delta');fixture.append(PATH)
 normalized(result);return result
def inputs(contract,original):
 result=prior.inputs(normalized(contract),original)
 if current_binding(contract):
  require(PATH not in result['fixture'],'Duplicate profile transaction input');result['fixture'].append(PATH)
 return result
def verify_proof(value,contract):
 evidence=copy.deepcopy(value)
 if current_binding(contract):
  require(evidence.pop(PROOF,None)==SHA,'Profile transaction preservation proof differs')
 else:require(PROOF not in evidence,'Unexpected profile transaction proof')
 return prior.verify_proof(evidence,normalized(contract))
