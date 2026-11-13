"""Exact preservation selection successor; historical selection pins stay immutable."""
import sys as _native_sys
from pathlib import Path as _NativePath
_native_root = next((p for p in _NativePath(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file()), None)
if _native_root is not None:
    _native_sys.path.insert(0, str(_native_root))
import copy,hashlib
from ci.qualification.native import refresh_observation_vpn_projection as prior
from ci.qualification.native import portal_identity_projection as portal_identity
from ci.qualification.native import preservation_evidence_projection as preservation_evidence
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
 contract=portal_identity.selection_normalized(preservation_evidence.selection_normalized(contract))
 if KEY not in contract:return False
 require(contract[KEY]==descriptor(),'Unknown preservation selection successor')
 require(contract['input_sha256'].get(PATH)==SHA and contract['input_sha256'].get(MAPPER)==MAPPER_SHA,'Unbound preservation selection successor')
 require(prior.KEY in contract,'Preservation selection missing prior observation layer')
 return True
def normalized(contract):
 result=copy.deepcopy(portal_identity.selection_normalized(preservation_evidence.selection_normalized(contract)))
 if binding(result):
  result.pop(KEY)
  result['input_sha256'][PATH]=prior.SHA
  prior.binding(result)
 return result
def add(contract):
 evidence=preservation_evidence.specifications()['files'][PATH]
 require(preservation_evidence.SELECTION_KEY not in contract,'Preservation-evidence selection already projected')
 if contract['input_sha256'].get(PATH)==evidence['after_sha256']:
  result=add(preservation_evidence.normalize(contract,PATH))
  result['input_sha256'][PATH]=evidence['after_sha256']
  result[preservation_evidence.SELECTION_KEY]=preservation_evidence.selection_descriptor()
  normalized(result)
  return result
 spec=portal_identity.specifications()[PATH]
 require(portal_identity.SELECTION_KEY not in contract,'Portal-identity selection already projected')
 if contract['input_sha256'].get(PATH)==spec['after_sha256']:
  require(spec['before_sha256']==SHA,'Portal-identity selection predecessor differs')
  result=add(portal_identity.normalize(contract,PATH))
  result['input_sha256'][PATH]=spec['after_sha256']
  result[portal_identity.SELECTION_KEY]=portal_identity.selection_descriptor()
  normalized(result)
  return result
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
 from ci.qualification.native import upstream_candidate_projection as upstream
 require(upstream.SELECTION_PATH==PATH and upstream.SELECTION_SHA==prior.SHA,'Historical selection identity changed')
 data=portal_identity.read_source(sources,PATH)
 restored=preservation_evidence.restore_if_successor(PATH,data)
 evidence=preservation_evidence.selection_descriptor() if restored!=data else None
 data=restored
 actual=hashlib.sha256(data).hexdigest()
 portal=None
 if actual==portal_identity.specifications()[PATH]['after_sha256']:
  data=portal_identity.restore_bytes(PATH,data)
  actual=hashlib.sha256(data).hexdigest()
  require(actual==SHA,'Portal-identity selection inverse changed preservation source')
  portal=portal_identity.selection_descriptor()
 if actual==prior.SHA:
  upstream.validate_observation_dependencies(sources)
  return None
 require(actual==SHA,'Unreviewed preservation selection fixture dependency bytes')
 require(upstream.OBSERVATION_MAPPER==MAPPER,'Historical mapper path changed')
 require(hashlib.sha256((sources/MAPPER).read_bytes()).hexdigest()==MAPPER_SHA,'Unreviewed observation mapper successor bytes')
 result={'preservation':descriptor(),'portal_identity':portal} if portal is not None else descriptor()
 if evidence is not None:result['preservation_evidence']=evidence
 return result
