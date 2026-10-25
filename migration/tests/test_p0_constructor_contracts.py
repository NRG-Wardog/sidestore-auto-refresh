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
ROOT=Path(__file__).resolve().parents[2];STAGE=ROOT/'migration/p0-constructor-diagnostics-v1';PRIOR=ROOT/'migration/p0-native-basis-v1'
spec=importlib.util.spec_from_file_location('constructor_contract_gate',STAGE/'validate_contracts.py');gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(p):return json.loads(p.read_bytes())
def dump(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n');return sha(p.read_bytes())
class ConstructorMetadataTests(unittest.TestCase):
    def setUp(self):
        self.reg=read(STAGE/'compatibility-registry.json');self.old=read(PRIOR/'compatibility-registry.json');self.ev=read(STAGE/self.reg['evidence']['path'])
    def test_reconstructed_registry_has_previous_independent_stage_digest(self):
        self.assertEqual(sha((STAGE/'compatibility-registry.json').read_bytes()),'94fd38ce234622ac5429a2e2b454418f8c0a5251dd25812744e0fab4f511755b')
    def test_all_owner_hashes_and_source_evidence_match(self):
        self.assertEqual(sha((STAGE/self.reg['evidence']['path']).read_bytes()),self.reg['evidence']['sha256'])
        for owner,pin in self.reg['owners'].items():
            m=read(STAGE/pin['manifest_path']);self.assertEqual(sha((STAGE/pin['manifest_path']).read_bytes()),pin['manifest_sha256']);self.assertEqual(self.ev['owners'][owner],{k:m[k]for k in ('sources','provenance')})
    def test_previous_contracts_unchanged(self):
        self.assertEqual({k:self.reg['contracts'][k]for k in self.old['contracts']},self.old['contracts']);self.assertEqual(set(self.reg['contracts'])-set(self.old['contracts']),{'constructor-diagnostics'})
    def test_previous_edges_preserved_with_exact_constructor_edges(self):
        self.assertEqual([e for e in self.reg['edges']if e['contract']!='constructor-diagnostics'],self.old['edges'])
        self.assertEqual([e for e in self.reg['edges']if e['contract']=='constructor-diagnostics'],[
            {'consumer':'LiveContainer','provider':'SideStore','version':'1.0.0','contract':'constructor-diagnostics'},
            {'consumer':'SideStore','provider':'AnisetteKit','version':'1.0.0','contract':'constructor-diagnostics'}])
    def test_unaffected_contracts_and_validator_unchanged(self):
        for n in ['validate_contracts.py',*['owners/'+o+'/runtime-contract.json'for o in ('SideSign','idevice','jktcp','minimuxer')]]:self.assertEqual((STAGE/n).read_bytes(),(PRIOR/n).read_bytes())
    def test_all_inherited_p0_files_unchanged(self):
        for n in ['reviewed-approval.json',*read(PRIOR/'reviewed-approval.json')['files']]:self.assertEqual((STAGE/'inherited/p0-native-basis-v1'/n).read_bytes(),(PRIOR/n).read_bytes())
    def test_constructor_native_header_bound(self):
        m=read(STAGE/'owners/AnisetteKit/runtime-contract.json');d=next(v for v in m['provides']if v['contract']=='constructor-diagnostics');self.assertIn('Native/Loader/vm_initialization_diagnostics.h',d['source_paths'])
    def test_restored_phase1_locks_match_real_resolver_receipt(self):
        receipt=read(STAGE/'SideSign-resolver.json')
        self.assertEqual(receipt['run_url'],'https://github.com/NRG-Wardog/LiveContainer/actions/runs/38973641877')
        self.assertEqual(receipt['artifact_sha256'],'29655fbd29879207b92dbfd5cc56d21c487bf9ccbf8dd0a166925491b1f875b2')
        for suffix,key in (('before','lock_before_sha256'),('after','lock_sha256')):
            self.assertEqual(sha((STAGE/('resolver-locks/SideSign.Package.resolved.'+suffix)).read_bytes()),receipt[key])
        self.assertEqual(read(STAGE/'resolver-locks/SideSign.Package.resolved.after')['originHash'],receipt['origin_hash']['value'])
    def test_regenerated_manifest_retains_old_literals_and_adds_constructor(self):
        manifest=read(STAGE/'anisette-generated/maintained-source-manifest.json');prior=read(PRIOR/'anisette-generated/maintained-source-manifest.json')
        self.assertEqual(manifest['anisettekit_revision'],'94659b4d54eed5729b34f4103b3f70579aa8d8e9')
        self.assertEqual(manifest['anisettekit_tree'],'78d7fdb9f77c461419b89df5a285dfd74fa642b5')
        for item in prior['required_compiled_evidence']['literals']:self.assertIn(item,manifest['required_compiled_evidence']['literals'])
        self.assertIn('Library constructor execution failed [v1;kind=',[item['value']for item in manifest['required_compiled_evidence']['literals']])
        self.assertIn('Native/Loader/vm_initialization_diagnostics.h',[item['path']for item in manifest['files']])
class ConstructorSyntheticGraphTests(unittest.TestCase):
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
        result=self.check();self.assertEqual((result['source_files'],result['contracts'],result['edges']),(95,13,29))
    def test_linked_temporary_ancestor_is_canonicalized_but_linked_source_is_rejected(self):
        # Model macOS /var -> /private/var using a bounded, test-owned link.
        linked_tmp=self.root/'linked-tmp';linked_tmp.symlink_to(self.root,target_is_directory=True)
        fixture=ConstructorSyntheticGraphTests('test_exact_synthetic_graph');self.addCleanup(fixture.doCleanups)
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
if __name__=='__main__':unittest.main()
