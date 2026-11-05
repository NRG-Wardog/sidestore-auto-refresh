"""Synthetic projection integration controls; never native qualification."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import cms_package_admission as a
import preservation_cms_portable as p
import test_preservation_cms_portable as fixtures

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PortableTests('test_positive_closed_scope');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root
        self.sources=self.root/'reviewed';self.sources.mkdir()
        contract=copy.deepcopy(self.fixture.contract)
        helper=b'reviewed synthetic native helper snapshot\n'
        contract['native_cms_helper_sha256']=p.sha(helper)
        (self.sources/'cms-native-helper.snapshot').write_bytes(helper)
        (self.sources/'cms-source-contract.json').write_text(json.dumps(contract))
        self.graph={'owners':{key:['reviewed/repo',value['commit']] for key,value in contract['owners'].items()},
                    'reviewed_deltas':{key:{'tree':value['tree']} for key,value in contract['owners'].items()}}
        (self.sources/'cms-native-graph.json').write_text(json.dumps(self.graph))
        self.identity=self.fixture.identity
        self.manifest={'commit':self.identity['workflow_commit'],'run_id':123,'run_attempt':2}
        self.native=dict(self.identity,status='COMPILE_ONLY_PASS_REQUIRES_REVIEW',requested_graph=self.graph['owners'],
                         evidence_sha256={key:p.sha((self.root/key).read_bytes()) for key in p.FILES})
        self.build()
        for mod in (a,p):
            context=patch.object(mod,'HERE',self.sources);context.start();self.addCleanup(context.stop)
    def build(self):
        raw=(json.dumps(self.native)+'\n').encode();(self.root/'native-receipt.json').write_bytes(raw)
        with zipfile.ZipFile(self.root/'native-evidence-projection.zip','w') as z:
            z.writestr('native-receipt.json',raw)
            for name in p.FILES:z.writestr(name,(self.root/name).read_bytes())
        archive=(self.root/'native-evidence-projection.zip').read_bytes()
        self.projection={'schema':'exact-native-evidence-projection-v1','execution':False,'native_run':self.manifest,
                         'native_receipt_sha256':p.sha(raw),'projection_sha256':p.sha(archive),'projection_bytes':len(archive),
                         'verified_reference_sha256':self.native['evidence_sha256'], 'verified_reference_count':len(p.FILES)}
        self.save_projection()
    def save_projection(self):
        (self.root/'native-evidence-projection.json').write_text(json.dumps(self.projection))
    def verify(self):return a.verify(self.root,self.manifest)
    def test_positive(self):
        result=self.verify();self.assertEqual(result['cms']['evidence_sha256'],self.native['evidence_sha256'])
        self.assertEqual(result['cms']['source_count'],20)
    def test_wrong_native_identity(self):
        self.native['run_id']='999';self.build()
        with self.assertRaises(ValueError):self.verify()
    def test_wrong_requested_graph(self):
        self.native['requested_graph']={};self.build()
        with self.assertRaises(ValueError):self.verify()
    def test_wrong_owner_tree(self):
        self.graph['reviewed_deltas']['SideStore']['tree']='a'*40
        (self.sources/'cms-native-graph.json').write_text(json.dumps(self.graph))
        with self.assertRaises(ValueError):self.verify()
    def test_wrong_helper(self):
        (self.sources/'cms-native-helper.snapshot').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.verify()
    def test_wrong_four_file_reference(self):
        self.native['evidence_sha256'][p.FILES[0]]='a'*64;self.build()
        with self.assertRaises(ValueError):self.verify()
    def test_replaced_archive(self):
        (self.root/'native-evidence-projection.zip').write_bytes(b'wrong')
        with self.assertRaises(ValueError):self.verify()
    def test_wrong_projection_reference_count(self):
        self.projection['verified_reference_count']+=1;self.save_projection()
        with self.assertRaises(ValueError):self.verify()
    def test_wrong_projection_identity(self):
        self.projection['native_run']=dict(self.manifest,run_attempt=3);self.save_projection()
        with self.assertRaises(ValueError):self.verify()
    def test_missing_cms_file(self):
        del self.native['evidence_sha256'][p.FILES[0]];self.build()
        with self.assertRaises(ValueError):self.verify()
    def test_symlink_archive(self):
        path=self.root/'native-evidence-projection.zip';path.rename(self.root/'actual.zip');path.symlink_to(self.root/'actual.zip')
        with self.assertRaises(ValueError):self.verify()

if __name__=='__main__':unittest.main()
