"""Exact preservation selection successor; historical selection pins stay immutable."""
import copy,hashlib
import refresh_observation_vpn_projection as prior
PATH=prior.PATH
SHA='bd640fc043058d1c922f8b4ee48847a8c30324b828fd9374665953d78fdab65c'
KEY='reviewed_preservation_selection_dependency'
def require(ok,why):
 if not ok:raise ValueError(why)
def descriptor():
 return {'path':PATH,'prior_sha256':prior.SHA,'sha256':SHA}
def binding(contract):
 if KEY not in contract:return False
 require(contract[KEY]==descriptor(),'Unknown preservation selection successor')
 require(contract['input_sha256'].get(PATH)==SHA,'Unbound preservation selection successor')
 require(prior.KEY in contract,'Preservation selection missing prior observation layer')
 return True
def normalized(contract):
 result=copy.deepcopy(contract)
 if binding(result):
  result.pop(KEY)
  result['input_sha256'][PATH]=prior.SHA
  prior.binding(result)
 return result
def add(contract):
 if contract['input_sha256'].get(PATH)!=SHA:return prior.add(contract)
 require(KEY not in contract,'Preservation selection already projected')
 old=copy.deepcopy(contract);old['input_sha256'][PATH]=prior.SHA
 result=prior.add(old)
 result['input_sha256'][PATH]=SHA;result[KEY]=descriptor()
 normalized(result)
 return result
def inputs(contract,original):
 return prior.inputs(normalized(contract),original)
def validate_upstream_dependencies(sources):
 # Keep the original validator unchanged for its original exact source. The
 # sole new alternative binds this exact successor and the unchanged mapper.
 import upstream_candidate_projection as upstream
 require(upstream.SELECTION_PATH==PATH and upstream.SELECTION_SHA==prior.SHA,'Historical selection identity changed')
 actual=hashlib.sha256((sources/PATH).read_bytes()).hexdigest()
 if actual==prior.SHA:
  upstream.validate_observation_dependencies(sources)
  return None
 require(actual==SHA,'Unreviewed preservation selection fixture dependency bytes')
 require(hashlib.sha256((sources/upstream.OBSERVATION_MAPPER).read_bytes()).hexdigest()==upstream.OBSERVATION_MAPPER_SHA,'Unreviewed observation mapper bytes')
 return descriptor()
