import copy,json,pathlib,tempfile,unittest
from acquire import validate_metadata
from contract import REPO,WORKFLOW,manifest,verify_size_acceptance,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256
class AcquisitionControls(unittest.TestCase):
    def setUp(self):
        self.m={'run_id':10,'run_attempt':1,'commit':'a'*40,'artifact_id':20,'artifact_bytes':30,'artifact_sha256':'b'*64}
        self.run={'id':10,'run_attempt':1,'head_sha':'a'*40,'status':'completed','conclusion':'success','path':WORKFLOW,'repository':{'full_name':REPO},'head_repository':{'full_name':REPO}}
        self.artifact={'id':20,'name':'experimental-issues-'+'a'*40+'-10-1','size_in_bytes':30,'expired':False,'workflow_run':{'id':10,'head_sha':'a'*40},'digest':'sha256:'+'b'*64}
    def test_current_manifest_cannot_reuse_historical_size_exception(self):
        current=manifest(pathlib.Path(__file__).with_name('input-manifest.json'))
        self.assertEqual(current['run_id'],41160811783)
        self.assertEqual(current['run_attempt'],1)
        with self.assertRaises(ValueError):
            verify_size_acceptance(current,24206390,'77a161cb4376c0819ddfaa3623214e28b78e8c998ec350fa7b84e0c67a9064ca',False)
    def test_exact_completed_green(self):validate_metadata(self.m,self.run,self.artifact)
    def test_wrong_run_or_pending_failed_forbidden(self):
        for key,value in [('conclusion','failure'),('status','in_progress'),('head_sha','c'*40),('run_attempt',2),('path','.github/workflows/recover-native-package.yml')]:
            run=copy.deepcopy(self.run);run[key]=value
            with self.assertRaises(ValueError):validate_metadata(self.m,run,self.artifact)
    def test_wrong_artifact_or_digest_forbidden(self):
        for key,value in [('id',99),('expired',True),('digest','sha256:'+'d'*64),('size_in_bytes',31),('name','untrusted')]:
            artifact=copy.deepcopy(self.artifact);artifact[key]=value
            with self.assertRaises(ValueError):validate_metadata(self.m,self.run,artifact)
    def test_foreign_owner_forbidden(self):
        run=copy.deepcopy(self.run);run['head_repository']['full_name']='someone/else'
        with self.assertRaises(ValueError):validate_metadata(self.m,run,self.artifact)
if __name__=='__main__':unittest.main()
