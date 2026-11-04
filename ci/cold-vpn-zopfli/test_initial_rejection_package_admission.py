"""Synthetic package adapter controls only; no native/device qualification."""
import copy,io,json,socket,subprocess,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import initial_rejection_package_admission as a
class AdmissionTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.contract,_,_,helper,graph=a.trusted()
  self.m={'commit':self.contract['native_builder'],'run_id':123,'run_attempt':1}
  self.projector=self.root/'projector.py';self.projector.write_text("EVIDENCE_ID = 42\nEVIDENCE_BYTES = 100\nEVIDENCE_SHA = '"+'a'*64+"'\n")
  self.issue={'suites':[{'owner':o,'suite':f+'/'+p,'status':'PASS_NO_SKIPS'} for o,f,p in helper.SUITES],'owned_authentication':dict(copy.deepcopy(self.contract['source_provenance']),swift_suites=[{'owner':o,'suite':s,'status':'PASS','executions':n} for o,s,_,n,_ in helper.OWNED_SWIFT_SUITES],target_device_execution=False),'harness_suites':list(helper.HARNESS_SUITES),'registry_runtime_double':'PASS','signer_policy_and_production_body_double':'PASS','all_suites_passed_no_skips':True,'target_device_execution':False}
  self.log='\n'.join(helper.INITIAL_REJECTION_MARKERS)+'\n'
  self.native={'workflow_commit':self.m['commit'],'run_id':'123','run_attempt':'1','status':'COMPILE_ONLY_PASS_REQUIRES_REVIEW','requested_graph':graph['owners']}
  self.build()
 def build(self, extra=False):
  self.data={'issue-tests.json':json.dumps(self.issue).encode(),'owned-initial-provider-rejection.log':self.log.encode()}
  self.native['evidence_sha256']={k:a.safe.sha(v) for k,v in self.data.items()};raw=json.dumps(self.native).encode();(self.root/'native-receipt.json').write_bytes(raw)
  buf=io.BytesIO()
  with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
   z.writestr('native-receipt.json',raw)
   for k,v in self.data.items():z.writestr(k,v)
   if extra:z.writestr('extra',b'not referenced')
  archive=buf.getvalue();(self.root/'native-evidence-projection.zip').write_bytes(archive)
  self.report={'schema':'exact-native-evidence-projection-v1','execution':False,'native_run':self.m.copy(),'source_artifact_id':42,'source_archive_bytes':100,'source_archive_sha256':'a'*64,'native_receipt_sha256':a.safe.sha(raw),'projection_sha256':a.safe.sha(archive),'projection_bytes':len(archive),'verified_reference_sha256':self.native['evidence_sha256'].copy(),'verified_reference_count':len(self.data)};self.save_report()
 def save_report(self):(self.root/'native-evidence-projection.json').write_text(json.dumps(self.report))
 def verify(self):return a.verify(self.root,self.m,self.projector)
 def test_positive_no_network_process_or_native_execution(self):
  with patch.object(subprocess,'run',side_effect=AssertionError('process')),patch.object(subprocess,'check_output',side_effect=AssertionError('process')),patch.object(subprocess,'Popen',side_effect=AssertionError('process')),patch.object(socket,'socket',side_effect=AssertionError('network')):
   result=self.verify()
  self.assertFalse(result['live_authentication_executed']);self.assertFalse(result['device_executed']);self.assertFalse(result['adapter_native_execution'])
 def test_missing_duplicate_unknown_and_false_scope_markers(self):
  original=self.log
  for marker in self.contract['required_markers']:
   for value in [original.replace(marker+'\n',''),original+marker+'\n',original+marker.split(' ',1)[0]+' unknown\n',original.replace(marker,marker+' altered')]:
    self.log=value;self.build()
    with self.assertRaises(ValueError):self.verify()
  self.log=original
 def test_native_identity_and_archive_binding(self):
  for key,value in [('run_id','456'),('run_attempt','2'),('workflow_commit','0'*40)]:
   old=self.native[key];self.native[key]=value;self.build()
   with self.assertRaises(ValueError):self.verify()
   self.native[key]=old
  self.build()
  for key,value in [('source_artifact_id',99),('source_archive_bytes',101),('source_archive_sha256','b'*64),('verified_reference_count',1),('execution',True),('projection_sha256','c'*64),('native_run',dict(self.m,run_attempt=True)),('source_archive_bytes',100.0)]:
   old=self.report[key];self.report[key]=value;self.save_report()
   with self.assertRaises(ValueError):self.verify()
   self.report[key]=old
 def test_source_suite_and_privacy_claim_drift(self):
  original=copy.deepcopy(self.issue)
  mutations=[lambda v:v.update(target_device_execution=True),lambda v:v.update(all_suites_passed_no_skips=False),lambda v:v['suites'].pop(),lambda v:v['owned_authentication']['swift_suites'].pop(),lambda v:v['owned_authentication'].update(target_device_execution=True),lambda v:v['owned_authentication']['source_sha256'].pop(next(iter(v['owned_authentication']['source_sha256']))),lambda v:v['owned_authentication']['source_sha256'].update({next(iter(v['owned_authentication']['source_sha256'])):'0'*64})]
  for mutate in mutations:
   self.issue=copy.deepcopy(original);mutate(self.issue);self.build()
   with self.assertRaises(ValueError):self.verify()
 def test_extra_member_and_reference_drift(self):
  self.build(extra=True)
  with self.assertRaises(ValueError):self.verify()
  self.build();self.report['verified_reference_sha256']['issue-tests.json']='0'*64;self.save_report()
  with self.assertRaises(ValueError):self.verify()
 def test_trusted_helper_checked_before_execution(self):
  original=a.safe.read
  def corrupt(path,*args):
   result=original(path,*args)
   return result+b'\nraise RuntimeError("untrusted executed")\n' if str(path).endswith('/run_issue_tests.py') else result
  with patch.object(a.safe,'read',side_effect=corrupt):
   with self.assertRaisesRegex(ValueError,'trusted source differs'):self.verify()
if __name__=='__main__':unittest.main(verbosity=2)
