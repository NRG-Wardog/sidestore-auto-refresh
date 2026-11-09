
import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/packaging/zopfli'))
from pathlib import Path as _CIPath
import copy,json,pathlib,tempfile,unittest
from acquire import validate_metadata
from contract import REPO,WORKFLOW,manifest,verify_size_acceptance,CLOSEST_OUTPUT_BYTES,CLOSEST_OUTPUT_SHA256
class AcquisitionControls(unittest.TestCase):
    def setUp(self):
        self.m={'run_id':10,'run_attempt':1,'commit':'a'*40,'artifact_id':20,'artifact_bytes':30,'artifact_sha256':'b'*64}
        self.run={'id':10,'run_attempt':1,'head_sha':'a'*40,'status':'completed','conclusion':'success','path':WORKFLOW,'repository':{'full_name':REPO},'head_repository':{'full_name':REPO}}
        self.artifact={'id':20,'name':'experimental-issues-'+'a'*40+'-10-1','size_in_bytes':30,'expired':False,'workflow_run':{'id':10,'head_sha':'a'*40},'digest':'sha256:'+'b'*64}
    def test_pinned_artifact_retains_historical_workflow_identity(self):
        # Literal provenance prevents changing both a constant and its fixture
        # from silently rewriting the identity of an already completed run.
        pinned = manifest(_CIPath(__file__).resolve().parents[3]/'ci/packaging/zopfli/input-manifest.json')
        run = dict(self.run, id=pinned['run_id'], run_attempt=pinned['run_attempt'],
                   head_sha=pinned['commit'], path='.github/workflows/cold-vpn-native.yml')
        artifact = dict(self.artifact, id=pinned['artifact_id'],
                        name=f"experimental-issues-{pinned['commit']}-{pinned['run_id']}-{pinned['run_attempt']}",
                        size_in_bytes=pinned['artifact_bytes'], digest='sha256:'+pinned['artifact_sha256'],
                        workflow_run={'id':pinned['run_id'], 'head_sha':pinned['commit']})
        validate_metadata(pinned, run, artifact)
        for wrong in ('.github/workflows/native-build.yml', '.github/workflows/issues-native.yml'):
            with self.subTest(workflow=wrong), self.assertRaisesRegex(ValueError, 'workflow or ownership'):
                validate_metadata(pinned, dict(run, path=wrong), artifact)

    def test_historical_candidate_cannot_reuse_current_measured_bound(self):
        current=manifest((_CIPath(__file__).resolve().parents[3]/'ci/packaging/zopfli/input-manifest.json'))
        historical=dict(current,commit='c89d975a0c0abac2df32f9feb53493ed815f0c3d',run_id=41310888596)
        # Size admission alone is not payload admission: historical output bytes
        # are also rejected by the mandatory full source/payload equality audit.
        for size,digest in [(24206390,'77a161cb4376c0819ddfaa3623214e28b78e8c998ec350fa7b84e0c67a9064ca'),(24230651,'ca000b547a4ced3c3737cedd6abf77e1c8c68c19cddfbf6deeb88a114c075f86'),(24246915,'dbdc649aa059a3f4b577da330ff5fa3a4df98012df7f4e1472a3cc39a129d5fa'),(24292265,'7ea32a7d06a3775fce488fb9e4f6fca62047dedcd3a467b69083ac31d452ef11')]:
            with self.subTest(size=size),self.assertRaises(ValueError):verify_size_acceptance(historical,size,digest,False)
        with self.assertRaises(ValueError):
            verify_size_acceptance(dict(current,commit='80b34a7f04eaf52f2276d884163f699a66d63e48',run_id=41322807269),24302831,'eb5f71ea2f444c0524990bf9e0db38d1cdde8527c229ec39a648f3041e4179b8',False)
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
