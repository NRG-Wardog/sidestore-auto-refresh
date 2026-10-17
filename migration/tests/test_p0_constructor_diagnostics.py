"""Reconstructed synthetic admission tests. No native success is asserted."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import p0_constructor_diagnostics as d
import p0_maintained_sources as p0
import maintained_sources as gate
import maintained_package_evidence as package
import verify_candidate_ipa as verifier

def raw(v):return (json.dumps(v,sort_keys=True,indent=2)+'\n').encode()
def h(v):return hashlib.sha256(('SYNTHETIC ONLY '+v).encode()).hexdigest()
def c(v):return hashlib.sha1(('SYNTHETIC ONLY '+v).encode()).hexdigest()

class ConstructorAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        shutil.copytree(ROOT/p0.DIRECTORY,self.root/p0.DIRECTORY)
        base,prior,old=p0.approval(self.root)
        self.directory=self.root/d.DIRECTORY;shutil.copytree(base,self.directory)
        shutil.copytree(base,self.directory/'inherited/p0-native-basis-v1')
        self.put('inherited/p0-pins.json',d.base_pins(self.root))
        owners=copy.deepcopy(prior['owners']);inventory=self.get('source-inventory.json');transitions={}
        for owner,row in owners.items():
            t={'baseline_commit':row['commit'],'commit':row['commit'],'tree':row['tree'],'changes':{}}
            if owner in d.CHANGED_OWNERS:
                row.update(commit=c(owner),tree=c(owner+' tree'));t.update(commit=row['commit'],tree=row['tree'])
                path=next(iter(inventory[owner]['changes']));change=inventory[owner]['changes'][path]
                after={'mode':'100644','type':'blob','git_object':c(owner+' blob'),'sha256':h(owner+' bytes')}
                t['changes'][path]={'before':copy.deepcopy(change['after']),'after':after};change['after']=after
                inventory[owner].update(commit=row['commit'],tree=row['tree'])
            transitions[owner]=t
        self.put('source-inventory.json',inventory);self.put('source-transition.json',transitions)
        children=self.get('submodules.json');children['SideStore/Dependencies/SideSign'].update(commit=owners['SideSign']['commit'],tree=owners['SideSign']['tree']);self.put('submodules.json',children)
        manifest=self.get('anisette-generated/maintained-source-manifest.json');manifest.update(source_basis=d.BASIS,anisettekit_revision=owners['AnisetteKit']['commit'],anisettekit_tree=owners['AnisetteKit']['tree'])
        manifest['files'].append({'path':'Native/Loader/vm_initialization_diagnostics.h','sha256':h('header')})
        manifest['required_compiled_evidence']['literals'].extend({'value':v}for v in d.REQUIRED_DIAGNOSTIC_LITERALS)
        self.put('anisette-generated/maintained-source-manifest.json',manifest)
        native=copy.deepcopy(prior['native_validation']);native.update(source_basis=d.BASIS,inherited_p0_review_sha256=p0.P0_REVIEW_SHA256,
            run_url='https://github.com/NRG-Wardog/LiveContainer/actions/runs/123',artifact_id=123,artifact_sha256=h('artifact'),
            approved_inputs_sha256=h('inputs'),evidence_index_sha256=h('index'),host_commit=c('runner'),host_tree=c('runner tree'),
            tested_owners={name:{'commit':row['commit'],'tree':row['tree']}for name,row in owners.items()})
        native['tested_children']['SideStore']['Dependencies/SideSign']=owners['SideSign']['commit'];phases={}
        for owner in d.LOCK_FILES:
            resolver=self.get(owner+'-resolver.json');resolver.update({k:native[k]for k in d.RUN_FIELDS});resolver.update(source_basis=d.BASIS,
                purpose='independently_reviewed_p0_constructor_diagnostics_swiftpm_resolution',anisette_commit=owners['AnisetteKit']['commit'],
                resolver_tested_commit=owners[owner]['commit'],resolver_tested_tree=owners[owner]['tree'])
            self.put(owner+'-resolver.json',resolver);phases[owner]={'status':'CAPTURED_FOR_REVIEW',**{k:resolver[k]for k in (*d.RUN_FIELDS,'resolver_tested_commit','resolver_tested_tree','lock_before_sha256','lock_sha256','origin_hash')}}
        self.receipt={'status':'INDEPENDENTLY_REVIEWED_P0_CONSTRUCTOR_DIAGNOSTICS_NATIVE_PASS','source_basis':d.BASIS,
            'native_validation':native,'native_builds':old['native_builds'],
            'canonical_native_fork83':{'checks':83,'failures':0,'source_owner':owners['AnisetteKit']['commit'],'engine_archive_sha256':p0.FORK_SHA256},
            'constructor_diagnostics':{'checks':2105,'failures':0,'skipped':0,'source_owner':owners['AnisetteKit']['commit'],'case_ids':list(d.REQUIRED_DIAGNOSTIC_CASES)},
            'constructor_regression':{'tests':8,'failures':0,'skipped':0},
            'consumer_suites':{'tested_owners':{n:copy.deepcopy(native['tested_owners'][n])for n in ('AnisetteKit','SideStore','LiveContainer')},
                **{n:{'tests':len(ids),'failures':0,'errors':0,'skipped':0,'test_ids':list(ids)}for n,ids in d.CONSUMER_SUITE_IDS.items()}},
            'phase_evidence':phases,'tested_submodules':children,'ADI_executed':False,'device_validation':False}
        files={n:None for n in prior['files']};files.update({'inherited/p0-native-basis-v1/'+n:None for n in ['reviewed-approval.json',*prior['files']]});files.update({'inherited/p0-pins.json':None,'source-transition.json':None})
        self.review={'schema_version':1,'source_basis':d.BASIS,'integration_parent':d.INTEGRATION_PARENT,'inherited_p0_review_sha256':p0.P0_REVIEW_SHA256,'owners':owners,'files':files,'native_validation':native}
    def get(self,name):return p0.parse((self.directory/name).read_bytes())
    def put(self,name,value):
        p=self.directory/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw(value))
    def seal(self):
        self.put('native-receipt.json',self.receipt)
        for name in self.review['files']:self.review['files'][name]=p0.sha((self.directory/name).read_bytes())
        self.put('reviewed-approval.json',self.review);return p0.sha((self.directory/'reviewed-approval.json').read_bytes())
    def check(self):
        with patch.object(d,'DIAGNOSTIC_REVIEW_SHA256',self.seal()):return d.approval(self.root)
    def pins(self,anchor):return {'schema_version':1,'integration_baseline':gate.BASELINE,'source_basis':d.BASIS,
        'contract_registry_sha256':self.review['files']['compatibility-registry.json'],
        'owners':{n:{k:v for k,v in row.items()if k!='tree'}for n,row in self.review['owners'].items()},
        'native_validation':{**self.review['native_validation'],'review_sha256':anchor}}
    def test_complete_synthetic_receipt_passes(self):self.check()
    def test_pending_anchor_rejects_before_metadata_read(self):
        with patch.object(d,'DIAGNOSTIC_REVIEW_SHA256',None),patch.object(d,'read',side_effect=AssertionError('must not read')):
            with self.assertRaises(ValueError):d.approval(self.root)
    def test_caller_pins_checked_by_every_metadata_entry(self):
        anchor=self.seal();pins=self.pins(anchor)
        with patch.object(d,'DIAGNOSTIC_REVIEW_SHA256',anchor):
            d.load_pins(pins,self.root,gate);pins['owners']['SideStore']['commit']=d.PARENTS['SideStore']
            for action in (lambda:d.load_pins(pins,self.root,gate),lambda:d.contract_basis(pins,self.root),lambda:d.contract_files(pins,self.root),lambda:d.anisette_manifest(pins,self.root)):
                with self.assertRaises(ValueError):action()
    def test_native_owner_graph_rejects_missing_owner(self):
        self.review['native_validation']['tested_owners'].pop('jktcp')
        with self.assertRaises(ValueError):self.check()
    def test_no_transport_successor_allowed(self):
        self.review['owners']['jktcp']['commit']=c('other')
        with self.assertRaises(ValueError):self.check()
    def test_historical_native_run_cannot_be_recycled(self):
        self.review['native_validation']['run_url']=p0.approval(self.root)[1]['native_validation']['run_url']
        with self.assertRaises(ValueError):self.check()
    def test_old_receipt_status_rejected(self):
        self.receipt['status']='INDEPENDENTLY_REVIEWED_P0_NATIVE_PASS'
        with self.assertRaises(ValueError):self.check()
    def test_extra_receipt_claim_rejected(self):
        self.receipt['ipa_built']=True
        with self.assertRaises(ValueError):self.check()
    def test_diagnostic_case_inventory_cannot_drop_case(self):
        self.receipt['constructor_diagnostics']['case_ids'].pop()
        with self.assertRaises(ValueError):self.check()
    def test_diagnostic_case_inventory_cannot_duplicate_case(self):
        self.receipt['constructor_diagnostics']['case_ids'][-1]=self.receipt['constructor_diagnostics']['case_ids'][0]
        with self.assertRaises(ValueError):self.check()
    def test_consumer_test_ids_must_match_exactly(self):
        self.receipt['consumer_suites']['init_diagnostics']['test_ids'][0]='unexecuted'
        with self.assertRaises(ValueError):self.check()
    def test_consumer_graph_cannot_use_old_source(self):
        self.receipt['consumer_suites']['tested_owners']['LiveContainer']['commit']=d.PARENTS['LiveContainer']
        with self.assertRaises(ValueError):self.check()
    def test_no_extra_review_files(self):
        self.put('extra.json',{});self.review['files']['extra.json']=None
        with self.assertRaises(ValueError):self.check()
    def test_frozen_p0_evidence_cannot_be_rehashed(self):
        p=self.directory/'inherited/p0-native-basis-v1/native-receipt.json';p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaises(ValueError):self.check()
    def test_unrelated_contract_cannot_be_rehashed(self):
        p=self.directory/'owners/jktcp/runtime-contract.json';p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaises(ValueError):self.check()
    def test_source_transition_parent_must_be_delivered_p0(self):
        v=self.get('source-transition.json');v['LiveContainer']['baseline_commit']=c('P1');self.put('source-transition.json',v)
        with self.assertRaises(ValueError):self.check()
    def test_source_transition_before_must_match_delivered_p0(self):
        v=self.get('source-transition.json');next(iter(v['AnisetteKit']['changes'].values()))['before']={'wrong':'data'};self.put('source-transition.json',v)
        with self.assertRaises(ValueError):self.check()
    def test_inventory_cannot_change_outside_transition(self):
        v=self.get('source-inventory.json');v['SideStore']['changes']['unreviewed']={};self.put('source-inventory.json',v)
        with self.assertRaises(ValueError):self.check()
    def test_retained_resolver_lock_bytes_are_bound(self):
        p=self.directory/'resolver-locks/SideStore.Package.resolved.after';p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaises(ValueError):self.check()
    def test_resolver_phase_identity_is_bound(self):
        v=self.get('SideStore-resolver.json');v['artifact_sha256']=h('different');self.put('SideStore-resolver.json',v)
        with self.assertRaises(ValueError):self.check()
    def test_package_roundtrip_retains_and_checks_old_and_new_evidence(self):
        anchor=self.seal();pins=self.pins(anchor);out=self.root/'output';out.mkdir()
        with patch.object(d,'DIAGNOSTIC_REVIEW_SHA256',anchor),patch.object(package,'ROOT',self.root):
            hashes=package.collect_contract_evidence(out,pins);package.verify_contract_evidence(out,hashes,pins)
            self.assertIn('inherited/p0-native-basis-v1/native-receipt.json',hashes)
            p=out/package.CONTRACT_DIRECTORY/'native-receipt.json';p.write_bytes(b'{}\n');hashes['native-receipt.json']=p0.sha(p.read_bytes())
            with self.assertRaises(ValueError):package.verify_contract_evidence(out,hashes,pins)
    def test_anisette_manifest_requires_native_constructor_literal(self):
        anchor=self.seal();pins=self.pins(anchor)
        with patch.object(d,'DIAGNOSTIC_REVIEW_SHA256',anchor):
            manifest=d.anisette_manifest(pins,self.root);literals=[row['value'].encode()for row in manifest['required_compiled_evidence']['literals']]
            verifier.verify_isolated_anisette_binary(b'\0'.join(literals),manifest)
            with self.assertRaises(ValueError):verifier.verify_isolated_anisette_binary(b'\0'.join(literals[:-1]),manifest)
    def test_actual_transition_rejects_merged_lane_or_snapshot_drift(self):
        sys.path.insert(0,str(ROOT));import prepare_candidate_inventory
        roots={n:self.root/n for n in d.PARENTS};legacy=Mock();legacy.ROOT=ROOT;legacy.git.return_value=b'commit parent\n';transitions=self.get('source-transition.json')
        with patch.object(prepare_candidate_inventory,'snapshot',side_effect=lambda source,base,commit:transitions[source.name]):
            d.verify_transitions(roots,self.review,self.directory,legacy);legacy.git.return_value=b'commit parent merge\n'
            with self.assertRaises(ValueError):d.verify_transitions(roots,self.review,self.directory,legacy)
        with patch.object(prepare_candidate_inventory,'snapshot',return_value={}):
            with self.assertRaises(ValueError):d.verify_transitions(roots,self.review,self.directory,legacy)
    def test_historical_p0_bytes_unchanged(self):
        paths=['scripts/p0_maintained_sources.py']+subprocess.check_output(['git','-C',str(ROOT),'ls-tree','-r','--name-only',d.INTEGRATION_PARENT,'--',p0.DIRECTORY],text=True).splitlines()
        for name in paths:self.assertEqual((ROOT/name).read_bytes(),subprocess.check_output(['git','-C',str(ROOT),'show',d.INTEGRATION_PARENT+':'+name]))

# Separate exact unittest IDs ensure each count/skip/claim rejection is executed.
def mutation_test(path,value):
    def test(self):
        target=self.receipt
        for key in path[:-1]:target=target[key]
        target[path[-1]]=value
        with self.assertRaises(ValueError):self.check()
    return test
for name,path,value in (
 ('constructor_wrong_checks',('constructor_diagnostics','checks'),2104),
 ('constructor_failure',('constructor_diagnostics','failures'),1),
 ('constructor_skip',('constructor_diagnostics','skipped'),1),
 ('aggregate_wrong_count',('constructor_regression','tests'),7),
 ('aggregate_skip',('constructor_regression','skipped'),1),
 ('init_consumer_wrong_count',('consumer_suites','init_diagnostics','tests'),4),
 ('init_consumer_skip',('consumer_suites','init_diagnostics','skipped'),1),
 ('adi_consumer_wrong_count',('consumer_suites','adi_consumption','tests'),5),
 ('adi_consumer_error',('consumer_suites','adi_consumption','errors'),1),
 ('adi_consumer_skip',('consumer_suites','adi_consumption','skipped'),1),
 ('bool_not_integer',('constructor_diagnostics','failures'),False),
 ('adi_execution_claim',('ADI_executed',),True),
 ('device_execution_claim',('device_validation',),True)):
    setattr(ConstructorAdmissionTests,'test_reject_'+name,mutation_test(path,value))

if __name__=='__main__':unittest.main()
