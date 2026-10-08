"""Exact diagnostic-source deltas and package evidence; no readiness fabrication."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import maintained_sources as gate
import maintained_package_evidence as package
import combined_build_evidence as collector
import verify_candidate_ipa as verifier


def diagnostic_pins():
    return {'source_basis': gate.DIAGNOSTIC_BASIS,
            'contract_registry_sha256': gate.DIAGNOSTIC_REGISTRY,
            'owners': {'AnisetteKit': {'commit': 'e530b84687ebea2e7d1115119e1a6d18372de14b'}}}


class DiagnosticPolicyTests(unittest.TestCase):
    def test_explicit_basis_and_reviewed_hash_are_both_required(self):
        pins=diagnostic_pins()
        self.assertEqual(gate.contract_basis(pins)[1],gate.DIAGNOSTIC_REGISTRY)
        for change in ({'source_basis':'unknown'}, {'source_basis':None}, {'contract_registry_sha256':gate.REGISTRY}):
            bad={**pins,**change}
            with self.subTest(change=change),self.assertRaises(ValueError):gate.contract_basis(bad)

    def test_old_native_receipt_cannot_self_authorize_diagnostic_pins(self):
        pins=json.loads((ROOT/'migration/tests/fixtures/accepted-maintained-sources-a939e4c.json').read_bytes())
        pins.update(source_basis=gate.DIAGNOSTIC_BASIS,contract_registry_sha256=gate.DIAGNOSTIC_REGISTRY)
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'pins.json';p.write_text(json.dumps(pins))
            with self.assertRaisesRegex(ValueError,'own reviewed native receipt'):gate.load_pins(p)

    def test_anisette_manifest_cannot_bind_another_owner_revision(self):
        pins=diagnostic_pins();pins['owners']['AnisetteKit']['commit']='62ce85c8798d8eab8e29752aba7dc9f1f6a5b80d'
        with self.assertRaisesRegex(ValueError,'another owner revision'):gate.expected_anisette_evidence(pins)

    def test_immutable_metadata_and_contract_copy_are_independently_bound(self):
        pins=diagnostic_pins();files=package.contract_files(pins)
        self.assertEqual(len(files),12)
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);hashes=package.collect_contract_evidence(out,pins)
            package.verify_contract_evidence(out,hashes,pins)
            relative='provenance/accepted-to-diagnostic-delta.json'
            p=out/package.CONTRACT_DIRECTORY/relative
            doc=json.loads(p.read_bytes());doc['runtime_delta'][0]['old_sha256']='0'*64;p.write_text(json.dumps(doc))
            hashes[relative]=hashlib.sha256(p.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError,'metadata hash mismatch'):package.verify_contract_evidence(out,hashes,pins)

    def test_diagnostic_source_data_has_exact_inventory_and_frozen_checkpoints(self):
        delta=gate.diagnostic_delta(diagnostic_pins())
        self.assertEqual(len(delta['runtime_delta']),7);self.assertEqual(len(delta['nonruntime_delta']),15)
        self.assertEqual(delta['accepted_graph']['SideStore']['source_checkpoint'],'9d8c71ed69684f805325ef440983e74d97113a71')
        self.assertEqual(len(gate.expected_anisette_evidence(diagnostic_pins())['files']),6)
        self.assertNotIn('AltStore/AppDelegate.swift',gate.ALLOWED_TRANSITIONS['SideStore'])
        self.assertNotIn('AnisetteKit',gate.ALLOWED_TRANSITIONS)

    def test_ci_uses_acquired_source_and_missing_explicit_source_cannot_skip(self):
        workflow=(ROOT/'.github/workflows/livecontainer-build.yml').read_text()
        step=workflow.split('- name: Test maintained acquisition and contract gates',1)[1].split('- name:',1)[0]
        self.assertIn('ADI_DIAGNOSTIC_ANISETTE_SOURCE: ${{ github.workspace }}/work/AnisetteKit',step)
        self.assertIn('--start-directory builder/migration/tests',step)
        self.assertIn('--start-directory builder/migration/contracts/tests',step)
        self.assertEqual(step.count('--allowlist builder/scripts/required_test_skip_allowlist.json'),2)
        with tempfile.TemporaryDirectory() as directory:
            env={**os.environ,'ADI_DIAGNOSTIC_ANISETTE_SOURCE':str(Path(directory)/'missing')}
            result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),
                'DiagnosticPackageTests.test_exact_six_sources_and_all_native_markers_pass'],
                env=env,text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Explicit diagnostic Anisette source is missing',result.stderr)
        self.assertNotIn('skipped=',result.stderr)


class DiagnosticTransitionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.git('init','--quiet');(self.root/'Source.swift').write_text('let value = 1\n');self.git('add','Source.swift');self.git('commit','--quiet','-m','base');self.base=self.git('rev-parse','HEAD').strip()
        old=self.identity('Source.swift');(self.root/'Source.swift').write_text('let value = 2\n');self.git('add','Source.swift');self.git('commit','--quiet','-m','diagnostic');self.diag=self.git('rev-parse','HEAD').strip();new=self.identity('Source.swift')
        self.row={'owner':'AnisetteKit','path':'Source.swift',**{'old_'+k:v for k,v in old.items()},**{'new_'+k:v for k,v in new.items()}}
        self.spec={'source_checkpoint':self.base,'commit':self.diag,'repository':'https://github.com/NRG-Wardog/AnisetteKit.git'}
        self.delta={'accepted_graph':{'AnisetteKit':{**self.spec,'commit':self.base,'tree':self.git('rev-parse',self.base+'^{tree}').strip()}},'diagnostic_source_tuple':{'AnisetteKit':{'commit':self.diag,'tree':self.git('rev-parse',self.diag+'^{tree}').strip()}},'runtime_delta':[self.row],'nonruntime_delta':[]}
    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.root),'-c','user.name=Diagnostic fixture','-c','user.email=fixture@example.invalid',*args],text=True,stderr=subprocess.PIPE)
    def identity(self,name):
        mode,kind,oid=self.git('ls-tree','HEAD','--',name).split('\t')[0].split()
        return {'mode':mode,'git_blob':oid,'sha256':hashlib.sha256((self.root/name).read_bytes()).hexdigest()}
    def verify(self):
        # Fixture metadata models the independently hash-approved loader. The
        # transition function still reads real Git ancestry, trees and blobs.
        with mock.patch.object(gate,'diagnostic_delta',return_value=self.delta):
            gate.verify_source_transition(self.root,'AnisetteKit',self.spec,{})
    def test_exact_real_git_delta_passes(self):self.verify()
    def test_wrong_old_hash_and_omitted_path_fail(self):
        self.row['old_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'source hash differs'):self.verify()
        self.delta['runtime_delta']=[]
        with self.assertRaisesRegex(ValueError,'inventory differs'):self.verify()
    def test_extra_runtime_edit_after_diagnostic_checkpoint_fails(self):
        (self.root/'Source.swift').write_text('let hiddenChange = 3\n');self.git('add','Source.swift');self.git('commit','--quiet','-m','extra');self.spec['commit']=self.git('rev-parse','HEAD').strip()
        with self.assertRaisesRegex(ValueError,'after approved checkpoint'):self.verify()
    def test_mode_and_tree_substitution_fail(self):
        self.row['new_mode']='100755'
        with self.assertRaisesRegex(ValueError,'mode/blob differs'):self.verify()
        self.delta['diagnostic_source_tuple']['AnisetteKit']['tree']='0'*40
        with self.assertRaisesRegex(ValueError,'source tree changed'):self.verify()

    def test_dependency_basis_requires_complete_exact_final_tree_delta(self):
        basis={'schema_version':1,'owner':'SideSign','purpose':'diagnostic_dependency_source_transition',
            'source_registry_sha256':gate.DIAGNOSTIC_REGISTRY,
            'accepted':{'commit':self.base,'tree':self.git('rev-parse',self.base+'^{tree}').strip()},
            'candidate':{'commit':self.diag,'tree':self.git('rev-parse',self.diag+'^{tree}').strip()},
            'changes':{'Source.swift':{label:{'mode':self.row[prefix+'_mode'],
                'blob':self.row[prefix+'_git_blob'],'sha256':self.row[prefix+'_sha256']}
                for label,prefix in [('before','old'),('after','new')]}}}
        def check():
            with mock.patch.object(gate,'diagnostic_dependency_reference',return_value=('unused','1'*64)), \
                 mock.patch.object(gate,'read_diagnostic_reference',return_value=(None,basis)):
                gate.verify_diagnostic_dependency_transition(self.root,'SideSign',self.base,self.spec,{})
        check()
        for field,bad in [('mode','100755'),('blob','0'*40),('sha256','0'*64)]:
            original=basis['changes']['Source.swift']['after'][field]
            basis['changes']['Source.swift']['after'][field]=bad
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'mode/blob/hash'):check()
            basis['changes']['Source.swift']['after'][field]=original
        original=basis['changes'];basis['changes']={}
        with self.assertRaisesRegex(ValueError,'inventory differs'):check()
        basis['changes']=original
        (self.root/'Unreviewed.swift').write_text('let injected = true\n')
        self.git('add','Unreviewed.swift');self.git('commit','--quiet','-m','unreviewed')
        self.spec['commit']=self.git('rev-parse','HEAD').strip()
        with self.assertRaisesRegex(ValueError,'identity differs'):check()

    def test_sidestore_dependency_basis_binds_child_and_gitlink(self):
        self.git('update-index','--add','--cacheinfo','160000',self.base,'Dependencies/SideSign')
        self.git('commit','--quiet','-m','child wiring');self.spec['commit']=self.git('rev-parse','HEAD').strip()
        checkpoint={'commit':self.base,'tree':self.git('rev-parse',self.base+'^{tree}').strip()}
        child={'candidate':checkpoint}
        basis={'schema_version':1,'owner':'SideStore','purpose':'diagnostic_dependency_source_transition',
            'source_registry_sha256':gate.DIAGNOSTIC_REGISTRY,'source_checkpoint':checkpoint,
            'candidate':{'commit':self.spec['commit'],'tree':self.git('rev-parse','HEAD^{tree}').strip()},
            'sidesign':{'repository':'https://github.com/NRG-Wardog/SideSign.git',**checkpoint,'basis_sha256':'a'*64},
            'changes':{'Source.swift':{label:{'mode':self.row[prefix+'_mode'],
                'blob':self.row[prefix+'_git_blob'],'sha256':self.row[prefix+'_sha256']}
                for label,prefix in [('before','old'),('after','new')]},
                'Dependencies/SideSign':{'before':None,'after':{'mode':'160000','commit':self.base}}}}
        pins={'owners':{'SideSign':{'repository':'https://github.com/NRG-Wardog/SideSign.git','commit':self.base}}}
        def check():
            with mock.patch.object(gate,'diagnostic_dependency_reference',side_effect=lambda p,o,k:(o,'a'*64)), \
                 mock.patch.object(gate,'read_diagnostic_reference',side_effect=lambda p,n,d:(None,child if n=='SideSign' else basis)):
                gate.verify_diagnostic_dependency_transition(self.root,'SideStore',self.base,self.spec,pins)
        check()
        basis['sidesign']['basis_sha256']='b'*64
        with self.assertRaisesRegex(ValueError,'child dependency basis'):check()
        basis['sidesign']['basis_sha256']='a'*64
        basis['changes']['Dependencies/SideSign']['after']['commit']='0'*40
        with self.assertRaisesRegex(ValueError,'mode/blob/hash'):check()


class DiagnosticDependencyReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.pins=diagnostic_pins()
        self.pins['diagnostic_dependencies']={owner:{'basis_sha256':'a'*64,'resolver_receipt_sha256':'b'*64}
            for owner in ('SideSign','SideStore')}

    def test_only_fixed_owner_and_kind_names_are_accepted(self):
        self.assertEqual(gate.diagnostic_dependency_reference(self.pins,'SideSign','basis'),
                         ('dependencies/SideSign-basis.json','a'*64))
        for owner,kind in [('AnisetteKit','basis'),('../SideSign','basis'),('SideStore','../../secret')]:
            with self.subTest(owner=owner,kind=kind),self.assertRaises(ValueError):
                gate.diagnostic_dependency_reference(self.pins,owner,kind)
        self.pins['diagnostic_dependencies']['SideSign']['path']='arbitrary.json'
        with self.assertRaisesRegex(ValueError,'schema'):gate.diagnostic_dependency_reference(self.pins,'SideSign','basis')

    def read(self,name,data,digest=None):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        with mock.patch.object(gate,'contract_basis',return_value=(self.root,gate.DIAGNOSTIC_REGISTRY)):
            return gate.read_diagnostic_reference(self.pins,name,digest or hashlib.sha256(data).hexdigest())

    def test_exact_receipt_bytes_and_unique_json_keys_required(self):
        name='dependencies/SideSign-basis.json'
        self.assertEqual(self.read(name,b'{"observed":true}')[1],{'observed':True})
        with self.assertRaisesRegex(ValueError,'reviewed hash'):self.read(name,b'{}','0'*64)
        with self.assertRaisesRegex(ValueError,'duplicate'):self.read(name,b'{"v":1,"v":2}')
        with self.assertRaisesRegex(ValueError,'oversized'):self.read(name,b' '* (1024*1024+1))

    def test_symlink_and_arbitrary_receipt_paths_fail_closed(self):
        target=self.root/'target.json';target.write_text('{}')
        directory=self.root/'dependencies';directory.mkdir()
        (directory/'SideSign-basis.json').symlink_to(target)
        with mock.patch.object(gate,'contract_basis',return_value=(self.root,gate.DIAGNOSTIC_REGISTRY)):
            for name in ('dependencies/SideSign-basis.json','../target.json'):
                with self.subTest(name=name),self.assertRaises(ValueError):
                    gate.read_diagnostic_reference(self.pins,name,hashlib.sha256(b'{}').hexdigest())


class DiagnosticPackageTests(unittest.TestCase):
    def setUp(self):
        source=os.environ.get('ADI_DIAGNOSTIC_ANISETTE_SOURCE')
        self.source=Path(source) if source else ROOT/'work/AnisetteKit'
        if not (self.source/'Native/Loader/adi_consumption_debug.h').is_file():
            if source is not None:
                self.fail('Explicit diagnostic Anisette source is missing')
            self.skipTest('Exact diagnostic Anisette source required; full diagnostic CI acquires it before tests')
        self.pins=diagnostic_pins();self.expected=gate.expected_anisette_evidence(self.pins)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.output=self.root/'evidence';self.manifest=self.root/'manifest.json'
        self.manifest.write_bytes((gate.contract_basis(self.pins)[0]/'anisette-generated/maintained-source-manifest.json').read_bytes())
        self.literals=[i['value'].encode() for i in self.expected['required_compiled_evidence']['literals']]
        self.binary=b'\0'.join(self.literals)
    def collect(self):
        with mock.patch.object(collector.subprocess,'run'),mock.patch.object(gate,'load_pins',return_value=self.pins):
            return collector.collect_isolated_anisette_evidence(self.source,self.manifest,self.output,self.binary,self.root/'validated-pins.json')
    def test_exact_six_sources_and_all_native_markers_pass(self):
        binding=self.collect();self.assertEqual(len(binding['source_sha256']),6)
        verifier.verify_isolated_anisette_evidence(self.output,binding,self.binary,self.pins)
    def test_every_required_native_marker_is_enforced(self):
        for missing in self.literals:
            binary=b'\0'.join(x for x in self.literals if x!=missing)
            with self.subTest(missing=missing),self.assertRaises(ValueError):verifier.verify_isolated_anisette_binary(binary,self.expected)
    def test_omitted_header_and_rehashed_source_are_rejected(self):
        binding=self.collect();root=self.output/verifier.ANISETTE_EVIDENCE_DIRECTORY
        header=root/'Native/Loader/adi_consumption_debug.h';original=header.read_bytes();header.unlink()
        with self.assertRaisesRegex(ValueError,'inventory mismatch'):verifier.verify_isolated_anisette_evidence(self.output,binding,self.binary,self.pins)
        header.write_bytes(original+b'\n// unexpected runtime edit\n');binding['source_sha256']['Native/Loader/adi_consumption_debug.h']=hashlib.sha256(header.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'source binding differs'):verifier.verify_isolated_anisette_evidence(self.output,binding,self.binary,self.pins)
    def test_rehashed_manifest_cannot_bless_another_producer(self):
        binding=self.collect();p=self.output/verifier.ANISETTE_EVIDENCE_DIRECTORY/verifier.ANISETTE_EVIDENCE_MANIFEST
        doc=json.loads(p.read_bytes());doc['anisettekit_revision']='0'*40;p.write_text(json.dumps(doc));binding['manifest_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'approved immutable bytes'):verifier.verify_isolated_anisette_evidence(self.output,binding,self.binary,self.pins)

if __name__=='__main__':unittest.main(verbosity=2)
