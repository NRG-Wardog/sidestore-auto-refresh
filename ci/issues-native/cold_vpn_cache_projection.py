"""Exact additive cache preservation proof for the reviewed cold VPN delta."""
import copy
PATH='SideStore/tests/cache_compatibility/reviewed-cold-vpn-host-delta.json'
SHA='923df9acc16531a738f8ac9a3f5e6df6a4b29228d955f213f7a75c8c681fdc6f'
KEY='reviewed_cold_vpn_cache_host_change'
TRUTH_PATH='SideStore/tests/cache_compatibility/reviewed-refresh-truth-host-delta.json'
TRUTH_SHA='60400c47b35b9ebfd6e3ac8ab5c7d73ef3244c99f70dafeb8254c72cb9e58d75'
TRUTH_KEY='reviewed_refresh_truth_cache_host_change'
def require(ok,why):
 if not ok:raise ValueError(why)
def binding(contract):
 value=contract.get(KEY)
 if value is None:return None
 require(value=={'delta_path':PATH,'delta_sha256':SHA},'Unknown cold VPN cache preservation delta')
 require(contract['input_sha256'].get(PATH)==SHA,'Unbound cold VPN cache preservation delta')
 return value
def truth_binding(contract):
 value=contract.get(TRUTH_KEY)
 if value is None:return None
 require(binding(contract) is not None,'Refresh truth cache proof requires prior cold VPN proof')
 require(value=={'delta_path':TRUTH_PATH,'delta_sha256':TRUTH_SHA},'Unknown refresh truth cache preservation delta')
 require(contract['input_sha256'].get(TRUTH_PATH)==TRUTH_SHA,'Unbound refresh truth cache preservation delta')
 return value
def inputs(contract,original):
 result=copy.deepcopy(original)
 if binding(contract):
  require(PATH not in result['fixture'],'Duplicate cold VPN cache fixture')
  result['fixture'].append(PATH)
 if truth_binding(contract):
  require(TRUTH_PATH not in result['fixture'],'Duplicate refresh truth cache fixture')
  result['fixture'].append(TRUTH_PATH)
 return result
def add(contract):
 result=copy.deepcopy(contract);require(KEY not in result,'Cold VPN cache proof already declared')
 result[KEY]={'delta_path':PATH,'delta_sha256':SHA}
 binding(result)
 if TRUTH_PATH in result['input_sha256']:
  require(TRUTH_KEY not in result,'Refresh truth cache proof already declared')
  result[TRUTH_KEY]={'delta_path':TRUTH_PATH,'delta_sha256':TRUTH_SHA}
  truth_binding(result)
 result['additive_suite_inputs']['cache-compatibility']=inputs(result,result['additive_suite_inputs']['cache-compatibility'])
 return result

def verify_proof(value,contract):
 cold=binding(contract);truth=truth_binding(contract)
 if truth:
  require(value.get('host_pipeline_except_reviewed_refresh_truth_cold_vpn_and_reuse_main_byte_identical') is True and
          value.get('host_pipeline_except_reviewed_cold_vpn_and_reuse_main_byte_identical') is not True and
          value.get('reviewed_refresh_truth_delta_sha256')==TRUTH_SHA,
          'Cache refresh truth preservation proof differs')
 if cold:
  require(value.get('host_pipeline_byte_identical') is False and
          (truth is not None or value.get('host_pipeline_except_reviewed_cold_vpn_and_reuse_main_byte_identical') is True) and
          value.get('reviewed_cold_vpn_delta_sha256')==cold['delta_sha256'] and
          value.get('host_pipeline_except_reviewed_reuse_main_byte_identical') is not True,
          'Cache cold VPN preservation proof differs')
 return cold
