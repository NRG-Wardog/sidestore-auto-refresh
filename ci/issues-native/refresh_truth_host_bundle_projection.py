"""Exact host cleanup ordering and three negative controls, no runtime-case rewrite."""
import copy
PATH='SideStore/tests/host_refresh_bundle/test_contract.py'
SHA='85dba185eedde70ec6c38a4698da385983b3e349950364971181b7f6a7f52fec'
KEY='reviewed_refresh_truth_host_bundle_source'
def require(ok,why):
 if not ok:raise ValueError(why)
def add(contract):
 result=copy.deepcopy(contract)
 require(KEY not in result and result['input_sha256'].get(PATH)==SHA,'Unreviewed refresh host fixture source')
 result[KEY]={'path':PATH,'sha256':SHA,'source_assertion_count':41}
 return result
def assertions(contract,previous):
 if KEY not in contract:return previous
 require(previous==38 and contract[KEY]=={'path':PATH,'sha256':SHA,'source_assertion_count':41},'Refresh host source assertion variant differs')
 require(contract['input_sha256'].get(PATH)==SHA,'Refresh host source assertions not bound')
 return 41
