"""Source metadata and synthetic topology tests; no native execution claims."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];STAGE=ROOT/'migration/p0-elf-metadata-bounds-v1';PRIOR=ROOT/'migration/p0-constructor-diagnostics-v1'
spec=importlib.util.spec_from_file_location('constructor_contract_gate',STAGE/'validate_contracts.py');gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(p):return json.loads(p.read_bytes())
def dump(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n');return sha(p.read_bytes())
class BoundsSyntheticGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.reg=read(STAGE/'compatibility-registry.json');self.ev=read(STAGE/self.reg['evidence']['path']);self.manifests={};self.roots={}
        for owner,pin in self.reg['owners'].items():
            m=read(STAGE/pin['manifest_path']);self.roots[owner]=str(self.root/'sources'/owner)
            for row in m['sources']:
                p=Path(self.roots[owner])/row['path'];p.parent.mkdir(parents=True,exist_ok=True);p.write_text('SYNTHETIC ONLY '+owner+':'+row['path']+'\n');row['sha256']=sha(p.read_bytes());os.chmod(p,0o755 if row['mode']=='100755'else 0o644)
            self.manifests[owner]=m;self.ev['owners'][owner]['sources']=copy.deepcopy(m['sources'])
        self.seal_test_fixture()
    def seal_test_fixture(self):
        for owner,m in self.manifests.items():
            pin=self.reg['owners'][owner];pin['manifest_sha256']=dump(self.root/pin['manifest_path'],m)
        self.reg['evidence']['sha256']=dump(self.root/self.reg['evidence']['path'],self.ev);self.path=self.root/'compatibility-registry.json';self.anchor=dump(self.path,self.reg)
    def check(self):return gate.validate(self.path,self.anchor,self.roots)
    def test_exact_synthetic_graph(self):
        result=self.check();self.assertEqual((result['source_files'],result['contracts'],result['edges']),(96,13,29))
    def test_linked_temporary_ancestor_is_canonicalized_but_linked_source_is_rejected(self):
        # Model macOS /var -> /private/var using a bounded, test-owned link.
        linked_tmp=self.root/'linked-tmp';linked_tmp.symlink_to(self.root,target_is_directory=True)
        fixture=BoundsSyntheticGraphTests('test_exact_synthetic_graph');self.addCleanup(fixture.doCleanups)
        with patch.object(tempfile,'tempdir',str(linked_tmp)):fixture.setUp()
        self.assertNotEqual(Path(fixture.tmp.name),fixture.root)
        self.assertEqual(fixture.root,Path(fixture.tmp.name).resolve())
        fixture.test_exact_synthetic_graph()
        linked_source=fixture.root/'linked-source';linked_source.symlink_to(fixture.roots['AnisetteKit'],target_is_directory=True)
        fixture.roots['AnisetteKit']=str(linked_source)
        with self.assertRaises(OSError):fixture.check()
    def test_missing_constructor_provider_rejected(self):
        m=self.manifests['SideStore'];m['provides']=[v for v in m['provides']if v['contract']!='constructor-diagnostics'];self.seal_test_fixture()
        with self.assertRaises(gate.ContractError):self.check()
    def test_missing_constructor_consumer_rejected(self):
        m=self.manifests['LiveContainer'];m['requires']=[v for v in m['requires']if v['contract']!='constructor-diagnostics'];self.seal_test_fixture()
        with self.assertRaises(gate.ContractError):self.check()
    def test_missing_header_binding_rejected(self):
        d=next(v for v in self.manifests['AnisetteKit']['provides']if v['contract']=='constructor-diagnostics');d['source_paths'].remove('Native/Loader/vm_initialization_diagnostics.h');self.seal_test_fixture()
        with self.assertRaises(gate.ContractError):self.check()
    def test_source_drift_rejected(self):
        (Path(self.roots['AnisetteKit'])/'Native/Loader/vm_initialization_diagnostics.h').write_text('drift')
        with self.assertRaises(gate.ContractError):self.check()
    def test_contract_version_drift_rejected(self):
        self.reg['contracts']['constructor-diagnostics']['version']='2.0.0';self.seal_test_fixture()
        with self.assertRaises(gate.ContractError):self.check()
    def test_registry_drift_rejected(self):
        self.path.write_bytes(self.path.read_bytes()+b'\n')
        with self.assertRaises(gate.ContractError):self.check()
class BoundsMetadataTests(unittest.TestCase):
    def test_historical_contracts_and_edges_unchanged(self):
        old=read(PRIOR/'compatibility-registry.json');new=read(STAGE/'compatibility-registry.json')
        self.assertEqual(new['contracts'],old['contracts']);self.assertEqual(new['edges'],old['edges'])
    def test_all_source_records_are_hash_bound_and_consistent(self):
        registry=read(STAGE/'compatibility-registry.json');evidence=read(STAGE/registry['evidence']['path'])
        self.assertEqual(sha((STAGE/registry['evidence']['path']).read_bytes()),registry['evidence']['sha256'])
        total=0
        for owner,pin in registry['owners'].items():
            path=STAGE/pin['manifest_path'];m=read(path);total+=len(m['sources'])
            self.assertEqual(sha(path.read_bytes()),pin['manifest_sha256'])
            self.assertEqual(evidence['owners'][owner],{k:m[k]for k in ('sources','provenance')})
        self.assertEqual(total,96)
    def test_other_owner_runtime_and_validator_unchanged(self):
        registry=read(STAGE/'compatibility-registry.json')
        for name in ['validate_contracts.py',*[p['manifest_path'] for o,p in registry['owners'].items() if o!='AnisetteKit']]:
            self.assertEqual((STAGE/name).read_bytes(),(PRIOR/name).read_bytes())
    def test_helper_is_bound_to_actual_reviewed_manifest(self):
        contract=read(STAGE/'owners/AnisetteKit/runtime-contract.json');repair=read(STAGE/'reviewed-ak-manifest.json')
        record=next(r for r in contract['sources'] if r['path']=='Native/Loader/elf_metadata_bounds.h')
        expected=next(r for r in repair['paths'] if r['path']==record['path'])
        self.assertEqual(record['sha256'],expected['sha256'])
        self.assertIn(record['path'],next(p for p in contract['provides'] if p['contract']=='anisette-provider')['source_paths'])
    def test_all_prior_compiled_literals_retained(self):
        old=read(PRIOR/'anisette-generated/maintained-source-manifest.json');new=read(STAGE/'anisette-generated/maintained-source-manifest.json')
        for literal in old['required_compiled_evidence']['literals']:self.assertIn(literal,new['required_compiled_evidence']['literals'])
        self.assertIn('Native/Loader/elf_metadata_bounds.h',{r['path']for r in new['files']})

if __name__=='__main__':unittest.main()
