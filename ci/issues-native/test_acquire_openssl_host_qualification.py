"""Acquisition admission/closure controls. Full host qualification has its own tests."""
import copy
import json
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch,Mock
import zipfile
import acquire_openssl_host_qualification as gate

class HostAcquisitionTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve(strict=True)
  self.pin=json.loads(gate.MANIFEST.read_text());self.pin.update(status='VERIFIED_GREEN_IMMUTABLE_ARTIFACT',artifact_id=12,
      artifact_size=100,artifact_sha256='a'*64,receipt_sha256='b'*64)
  self.identity={'workflow_commit':'e'*40,'run_id':'333','run_attempt':'1'}
  self.run={'id':self.pin['run_id'],'run_attempt':1,'head_sha':self.pin['head_sha'],'head_branch':self.pin['branch'],
      'path':self.pin['workflow'],'status':'completed','conclusion':'success','repository':{'full_name':gate.REPOSITORY},
      'head_repository':{'full_name':gate.REPOSITORY}}
  self.jobs={'total_count':1,'jobs':[{'run_id':self.pin['run_id'],'run_attempt':1,'head_sha':self.pin['head_sha'],
      'name':'qualify','status':'completed','conclusion':'success','steps':[{'number':i,'name':name,'status':'completed','conclusion':'success'}
       for i,name in enumerate(sorted(gate.REQUIRED_STEPS),1)]}]}
  self.artifact={'id':12,'name':self.pin['artifact_name'],'expired':False,'size_in_bytes':100,
      'digest':'sha256:'+'a'*64,'workflow_run':{'id':self.pin['run_id'],'head_sha':self.pin['head_sha']}}
 def test_incomplete_pin_rejects_before_network_or_directory_creation(self):
  api=Mock()
  with patch.object(gate,'manifest',side_effect=lambda:gate.validate_manifest({**self.pin,'status':'PENDING_VERIFIED_ARTIFACT'})):
   with self.assertRaises(ValueError):gate.acquire(self.root/'output',self.identity,api=api)
  api.json.assert_not_called();api.download.assert_not_called();self.assertFalse((self.root/'output').exists())
 def test_exact_green_run_and_all_steps_required(self):
  gate.validate_remote(self.run,self.jobs,self.artifact,self.pin)
  for key,value in [('conclusion','failure'),('head_sha','c'*40),('run_attempt',2),('head_branch','main')]:
   with self.subTest(key=key),self.assertRaises(ValueError):gate.validate_remote({**self.run,key:value},self.jobs,self.artifact,self.pin)
  for value in ('failure','skipped','cancelled',None):
   changed=copy.deepcopy(self.jobs);changed['jobs'][0]['steps'][0]['conclusion']=value
   with self.assertRaises(ValueError):gate.validate_remote(self.run,changed,self.artifact,self.pin)
 def test_failed_host_run_with_otherwise_valid_artifact_never_downloads(self):
  api=Mock();api.json.side_effect=[{**self.run,'conclusion':'failure'},self.jobs,self.artifact]
  with patch.object(gate,'manifest',return_value=self.pin),self.assertRaisesRegex(ValueError,'exact green'):
   gate.acquire(self.root/'output',self.identity,api=api)
  api.download.assert_not_called()
 def test_wrong_job_step_closure_and_artifact_rejected(self):
  for mutate in (lambda row:row.update(total_count=2),lambda row:row['jobs'][0]['steps'].pop(),
                 lambda row:row['jobs'][0].update(run_attempt=2)):
   jobs=copy.deepcopy(self.jobs);mutate(jobs)
   with self.assertRaises(ValueError):gate.validate_remote(self.run,jobs,self.artifact,self.pin)
  for key,value in [('id',13),('expired',True),('name','other'),('size_in_bytes',99),('digest','sha256:'+'c'*64)]:
   with self.assertRaises(ValueError):gate.validate_remote(self.run,self.jobs,{**self.artifact,key:value},self.pin)
 def fixture(self,**changes):
  module=gate.qualification_module();source=module.sources()
  receipt={'qualification_identity':{'workflow_commit':self.pin['head_sha'],'run_id':str(self.pin['run_id']),'run_attempt':'1'},
           'source_sha256':source};receipt.update(changes)
  data=json.dumps(receipt).encode();archive=self.root/'input.zip'
  with zipfile.ZipFile(archive,'w') as z:
   info=zipfile.ZipInfo(self.pin['receipt_path']);info.create_system=3;info.external_attr=(stat.S_IFREG|0o644)<<16
   z.writestr(info,data)
  self.pin.update(artifact_sha256=gate.sha(archive),artifact_size=archive.stat().st_size,
                  receipt_sha256=__import__('hashlib').sha256(data).hexdigest())
  verifier=Mock(return_value={'test_fixture_only':True})
  fake=types.SimpleNamespace(SOURCES=module.SOURCES,sources=module.sources,NATIVE=module.NATIVE,
                             verify_host_local_qualification=verifier)
  return archive,fake,verifier
 def test_archive_hash_receipt_and_full_verifier_are_mandatory(self):
  archive,module,verifier=self.fixture()
  with patch.object(gate,'qualification_module',return_value=module):
   path,result=gate.verify_local(archive,self.root/'extract',self.pin,self.identity)
  verifier.assert_called_once_with(path,self.pin['receipt_sha256'],self.identity,module.NATIVE)
  self.assertEqual(result,{'test_fixture_only':True})
  with self.assertRaises(ValueError):gate.verify_local(archive,self.root/'other',{**self.pin,'artifact_sha256':'0'*64},self.identity)
 def test_source_or_receipt_identity_drift_rejected_before_verifier(self):
  archive,module,verifier=self.fixture(source_sha256={})
  with patch.object(gate,'qualification_module',return_value=module),self.assertRaisesRegex(ValueError,'29-source'):
   gate.verify_local(archive,self.root/'extract',self.pin,self.identity)
  verifier.assert_not_called()
 def test_framework_seal_is_required_even_with_matching_replacement_inventory(self):
  original=gate.qualification_module()
  self.assertEqual(len(original.SOURCES),29)
  self.assertIn('ci/issue55-openssl-local/framework_seal.py',original.SOURCES)
  source=original.sources()
  source['ci/issue55-openssl-local/unreviewed.py']=source.pop('ci/issue55-openssl-local/framework_seal.py')
  archive,module,verifier=self.fixture(source_sha256=source)
  module.SOURCES=tuple(source)
  module.sources=lambda:source
  with patch.object(gate,'qualification_module',return_value=module),self.assertRaisesRegex(ValueError,'29-source'):
   gate.verify_local(archive,self.root/'extract',self.pin,self.identity)
  verifier.assert_not_called()
 def test_full_qualifier_failure_is_not_masked(self):
  archive,module,verifier=self.fixture();verifier.side_effect=ValueError('cryptographic qualification failed')
  with patch.object(gate,'qualification_module',return_value=module),self.assertRaisesRegex(ValueError,'cryptographic'):
   gate.verify_local(archive,self.root/'extract',self.pin,self.identity)
 def test_explicit_symlink_root_rejected_no_network(self):
  real=self.root/'real';real.mkdir();linked=self.root/'linked';linked.symlink_to(real,target_is_directory=True)
  api=Mock()
  with patch.object(gate,'manifest',return_value=self.pin),self.assertRaises(ValueError):gate.acquire(linked,self.identity,api=api)
  api.json.assert_not_called()
 def test_owned_downloader_denial_stops_without_fallback(self):
  api=Mock();api.json.side_effect=ValueError('HTTP403')
  with patch.object(gate,'manifest',return_value=self.pin),self.assertRaisesRegex(ValueError,'403'):
   gate.acquire(self.root/'output',self.identity,api=api)
  self.assertEqual(api.json.call_count,1);api.download.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
