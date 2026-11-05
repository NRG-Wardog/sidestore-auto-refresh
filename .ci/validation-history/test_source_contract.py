import copy
import json
from pathlib import Path
import tempfile
import unittest
import source_contract as s

class SourceContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.history={p:('old '+p).encode() for p in s.OVERRIDES}
        self.current={p:('new '+p).encode() for p in s.OVERRIDES|s.DEPENDENCIES|s.ADDED_WORKFLOWS}
        self.history['security.py']=self.current['security.py']=b'keep strict privacy checks'
        self.current['migration/archive.yml']=b'archive frozen'
        self.current['.github/workflows/validation-history-contract.yml']=b'history workflow'
        self.m={'protected_sha256':{p:s.sha(b) for p,b in self.history.items()},'archived_workflows_sha256':{'migration/archive.yml':s.sha(b'archive frozen')}}
        self.c={'schema':'reviewed-main-source-successor-v1','historical_manifest_sha256':'','historical_commit':s.HISTORICAL,'qualified_commit':s.QUALIFIED,'qualified_tree':s.TREE,'protected_successors':{p:{'historical_sha256':s.sha(self.history[p]),'reviewed_sha256':s.sha(self.current[p]),'origin':'source_guard_repair' if p.endswith('maintained-packaging-contract.yml') else 'qualified_candidate'} for p in s.OVERRIDES},'added_workflows_sha256':{p:s.sha(self.current[p]) for p in s.ADDED_WORKFLOWS},'dependency_sha256':{p:s.sha(self.current[p]) for p in s.DEPENDENCIES}}
        for p,b in self.current.items():self.put(p,b)
        self.history['.ci/validation-history/manifest.json']=json.dumps(self.m).encode()
        self.put('.ci/validation-history/manifest.json',json.dumps(self.m).encode());self.c['historical_manifest_sha256']=s.sha(json.dumps(self.m).encode());self.save()
    def put(self,p,b):
        p=self.root/p;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
    def save(self):self.put('.ci/validation-history/reviewed-successor.json',json.dumps(self.c).encode())
    def objects(self,root,ref,path):return (self.history if ref==s.HISTORICAL else self.current)[path]
    def verify(self):return s.verify_sources(self.root,object_read=self.objects,tree_read=lambda root,ref:s.TREE)
    def test_exact_successor_and_historical_source_pass(self):self.verify()
    def test_unknown_protected_override_rejected(self):
        self.c['protected_successors']['security.py']=dict(next(iter(self.c['protected_successors'].values())));self.save()
        with self.assertRaisesRegex(ValueError,'scope'):self.verify()
    def test_changed_privacy_guard_rejected(self):
        self.put('security.py',b'weakened')
        with self.assertRaisesRegex(ValueError,'Reviewed source'):self.verify()
    def test_historical_manifest_not_rewritten(self):
        self.put('.ci/validation-history/manifest.json',json.dumps(dict(self.m,extra=True)).encode())
        with self.assertRaisesRegex(ValueError,'Frozen historical'):self.verify()
    def test_coordinated_manifest_and_digest_tamper_rejected(self):
        self.m['protected_sha256'].pop('security.py')
        raw=json.dumps(self.m).encode();self.put('.ci/validation-history/manifest.json',raw)
        self.c['historical_manifest_sha256']=s.sha(raw);self.save()
        with self.assertRaisesRegex(ValueError,'Frozen historical manifest object'):self.verify()
    def test_historical_mapping_not_rewritten(self):
        self.c['protected_successors']['scripts/package_livecontainer_combined.py']['historical_sha256']='0'*64;self.save()
        with self.assertRaisesRegex(ValueError,'Historical mapping'):self.verify()
    def test_current_source_tamper_rejected(self):
        self.put('scripts/package_livecontainer_combined.py',b'tampered')
        with self.assertRaisesRegex(ValueError,'Reviewed source'):self.verify()
    def test_source_cannot_be_rebound_to_other_commit(self):
        self.c['qualified_commit']='a'*40;self.save()
        with self.assertRaisesRegex(ValueError,'lineage'):self.verify()
    def test_qualified_object_mismatch_rejected(self):
        self.current['scripts/package_livecontainer_combined.py']=b'other'
        with self.assertRaisesRegex(ValueError,'Qualified source object'):self.verify()
    def test_original_historical_object_mismatch_rejected(self):
        self.history['scripts/package_livecontainer_combined.py']=b'other'
        with self.assertRaisesRegex(ValueError,'Historical source object'):self.verify()
    def test_dependency_omission_rejected(self):
        self.c['dependency_sha256'].pop('scripts/ipa_size_report.py');self.save()
        with self.assertRaisesRegex(ValueError,'dependency closure'):self.verify()
    def test_dependency_bytes_tamper_rejected(self):
        self.put('scripts/packaging_adapters.py',b'tampered')
        with self.assertRaisesRegex(ValueError,'successor bytes'):self.verify()
    def test_workflow_scope_omission_rejected(self):
        self.c['added_workflows_sha256'].pop(next(iter(s.ADDED_WORKFLOWS)));self.save()
        with self.assertRaisesRegex(ValueError,'workflow scope'):self.verify()
    def test_unknown_workflow_rejected(self):
        self.put('.github/workflows/unreviewed.yml',b'write permissions')
        with self.assertRaisesRegex(ValueError,'workflow inventory'):self.verify()
    def test_workflow_bytes_tamper_rejected(self):
        self.put(next(iter(s.ADDED_WORKFLOWS)),b'tampered')
        with self.assertRaisesRegex(ValueError,'successor bytes'):self.verify()
    def test_linked_protected_source_rejected(self):
        p=self.root/'security.py';p.unlink();p.symlink_to(self.root/'migration/archive.yml')
        with self.assertRaisesRegex(ValueError,'linked'):self.verify()
    def test_duplicate_contract_keys_rejected(self):
        raw=json.dumps(self.c);self.put('.ci/validation-history/reviewed-successor.json',(raw[:-1]+',"schema":"reviewed-main-source-successor-v1"}').encode())
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.verify()
    def test_qualified_tree_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError,'Git tree'):
            s.verify_sources(self.root,object_read=self.objects,tree_read=lambda root,ref:'0'*40)
    def test_archived_workflow_tamper_rejected(self):
        self.put('migration/archive.yml',b'new history')
        with self.assertRaisesRegex(ValueError,'Reviewed source'):self.verify()
if __name__=='__main__':unittest.main()
