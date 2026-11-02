"""Exact preservation selection successor; historical selection pins stay immutable."""
import copy,hashlib
import refresh_observation_vpn_projection as prior
PATH=prior.PATH
SHA='745adc1bc1d04b887b77e31ed5790cca6ceeb9074cdeed51cf46125653154ebd'
KEY='reviewed_preservation_selection_dependency'
MAPPER='SideStore/SideStore/Core/DeviceApi/MinimuxerWrapper.swift'
MAPPER_SHA='72ca7e68613bf103004507460efc6628c9f168dd6042dbfa589d33d34c5a49ab'
def require(ok,why):
 if not ok:raise ValueError(why)
def descriptor():
 return {'path':PATH,'prior_sha256':prior.SHA,'sha256':SHA,'mapper_path':MAPPER,'mapper_sha256':MAPPER_SHA}
def binding(contract):
 if KEY not in contract:return False
 require(contract[KEY]==descriptor(),'Unknown preservation selection successor')
 require(contract['input_sha256'].get(PATH)==SHA and contract['input_sha256'].get(MAPPER)==MAPPER_SHA,'Unbound preservation selection successor')
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
 require(contract['input_sha256'].get(MAPPER)==MAPPER_SHA,'Unbound preservation mapper successor')
 old=copy.deepcopy(contract);old['input_sha256'][PATH]=prior.SHA
 result=prior.add(old)
 result['input_sha256'][PATH]=SHA;result[KEY]=descriptor()
 normalized(result)
 return result
def inputs(contract,original):
 return prior.inputs(normalized(contract),original)
def validate_upstream_dependencies(sources):
 # Keep the original validator unchanged for its original exact source. The
 # sole new alternative binds this exact source pair. The additional native
 # install helper leaves the extracted MinimuxerError mapper unchanged.
 import upstream_candidate_projection as upstream
 require(upstream.SELECTION_PATH==PATH and upstream.SELECTION_SHA==prior.SHA,'Historical selection identity changed')
 actual=hashlib.sha256((sources/PATH).read_bytes()).hexdigest()
 if actual==prior.SHA:
  upstream.validate_observation_dependencies(sources)
  return None
 require(actual==SHA,'Unreviewed preservation selection fixture dependency bytes')
 require(upstream.OBSERVATION_MAPPER==MAPPER,'Historical mapper path changed')
 require(hashlib.sha256((sources/MAPPER).read_bytes()).hexdigest()==MAPPER_SHA,'Unreviewed observation mapper successor bytes')
 return descriptor()
