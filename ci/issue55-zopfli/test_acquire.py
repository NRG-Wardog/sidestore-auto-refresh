import copy,os,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import acquire as gate
class Tests(unittest.TestCase):
 def fixtures(self):
  run={'id':gate.RUN,'run_attempt':1,'head_sha':gate.HEAD,'status':'completed','conclusion':'success','path':'.github/workflows/recover-native-package.yml','repository':{'full_name':gate.REPO},'head_repository':{'full_name':gate.REPO}}
  artifact={'name':'recovered-experimental-issues-'+gate.HEAD+'-'+str(gate.RUN)+'-1','id':gate.ARTIFACT,'size_in_bytes':25471679,'expired':False,'workflow_run':{'id':gate.RUN,'head_sha':gate.HEAD},'digest':'sha256:'+gate.OUTER_SHA}
  return run,artifact
 def test_exact_metadata(self):gate.metadata(*self.fixtures())
 def test_failed_or_wrong_run_rejected(self):
  run,artifact=self.fixtures()
  for key,value in [('conclusion','failure'),('head_sha','0'*40),('run_attempt',2),('path','other')]:
   with self.subTest(key=key),self.assertRaises(ValueError):gate.metadata({**run,key:value},artifact)
 def test_artifact_mutations_rejected(self):
  run,artifact=self.fixtures()
  for key,value in [('id',1),('name','other'),('expired',True),('size_in_bytes',1),('digest','sha256:'+'0'*64),('workflow_run',{'id':1,'head_sha':gate.HEAD})]:
   with self.subTest(key=key),self.assertRaises(ValueError):gate.metadata(run,{**artifact,key:value})
 def test_failure_never_downloads_or_creates_output(self):
  run,artifact=self.fixtures();run['conclusion']='failure';api=Mock();api.json.side_effect=[run,artifact]
  with tempfile.TemporaryDirectory() as temp:
   output=Path(temp).resolve()/'new'
   with patch.object(gate,'API',return_value=api),patch.dict(os.environ,GH_TOKEN='fixture-not-a-token'),self.assertRaises(ValueError):gate.acquire(output)
   api.download.assert_not_called();self.assertFalse(output.exists())
 def test_denied_route_not_retried(self):
  api=Mock();api.json.side_effect=ValueError('denied')
  with tempfile.TemporaryDirectory() as temp:
   with patch.object(gate,'API',return_value=api),patch.dict(os.environ,GH_TOKEN='fixture-not-a-token'),self.assertRaises(ValueError):gate.acquire(Path(temp).resolve()/'new')
  self.assertEqual(api.json.call_count,1);api.download.assert_not_called()
 def test_symlink_output_rejected_before_api(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp).resolve();(root/'real').mkdir();(root/'link').symlink_to(root/'real',target_is_directory=True)
   with patch.object(gate,'API') as api,self.assertRaises(ValueError):gate.acquire(root/'link')
   api.assert_not_called()
 def test_redirects_disabled(self):self.assertIsNone(gate.NoRedirect().redirect_request(None,None,None,None,None,None))
if __name__=='__main__':unittest.main(verbosity=2)
