"""Host-only source policy and synthetic receipt mutation tests; no execution evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
import p0_cxx_allocation as prior
import p2_composed_host as p2
import maintained_sources as gate
import maintained_package_evidence as package
import verify_candidate_ipa as ipa
STAGE=ROOT/p2.DIRECTORY
PRIOR=ROOT/prior.DIRECTORY

def load(path):return json.loads(path.read_bytes())
def dump(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
 return hashlib.sha256(path.read_bytes()).hexdigest()
def capture_fixture(owners):
 # Deliberately synthetic positive schema fixture, never emitted as real evidence.
 return {'schema_version':1,'status':'INDEPENDENTLY_REVIEWED_COMPOSED_HOST_PASS','scope':p2.FRESH_SCOPE,
  'identity':{'run_url':'https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/1','run_attempt':1,'artifact_id':1,
   'artifact_sha256':'a'*64,'evidence_index_sha256':'b'*64,'host_commit':p2.NATIVE_HOST_COMMIT,'host_tree':p2.NATIVE_HOST_TREE,'approved_inputs_sha256':p2.NATIVE_INPUT_SHA256},
  'tested_owners':{o:{k:owners[o][k]for k in ('commit','tree')}for o in ('LiveContainer','SideStore','AnisetteKit')},
  'consumer_tests':{'tests':5,'failures':0,'errors':0,'skipped':0,'test_ids':list(prior.CONSUMER_SUITE_IDS['init_diagnostics'])},
  'product':{'configuration':'Release','sdk':'iphonesimulator','compile':'passed','localization':'passed','exact_plural_values':'passed','permissions':'passed'},
  'claims':{k:False for k in ('fresh_ui_execution','guest_execution','ADI_executed','device_validation','ipa_acceptance')},
  'evidence_files':{n:'c'*64 for n in ('result.json','inputs.json','source-before.json','source-after.json','consumer-sources-before.json','consumer-sources-after.json','consumer-tests.json','product-identity.json','compiled-resources.json')}}
def ui_fixture():
 return {'status':'INDEPENDENTLY_REVIEWED_PARENT_UI','scope':p2.UI_SCOPE,'source_commit':p2.LC_PARENT,'source_tree':p2.LC_PARENT_TREE,'run_url':p2.UI_RUN_URL,'run_attempt':1,'artifact_id':p2.UI_ARTIFACT_ID,'artifact_sha256':p2.UI_ARTIFACT_SHA256,'evidence_index_sha256':'d'*64,'languages':['en','zh-Hans','zh-Hant','he'],'composed_ui_reexecuted':False,'service_failure_unresolved':True,'full_app_acceptance':False,'final_composition_acceptance':False}

class PendingTests(unittest.TestCase):
 def test_pending_before_any_metadata_read(self):
  with patch.object(p2,'REVIEW_SHA256',None),patch.object(p2,'read',side_effect=AssertionError('untrusted read')):
   with self.assertRaisesRegex(ValueError,'pending'):p2.approval(ROOT)
 def test_all_routes_fail_closed(self):
  pins={'source_basis':p2.BASIS}
  with patch.object(p2,'REVIEW_SHA256',None),tempfile.TemporaryDirectory()as tmp:
   path=Path(tmp)/'pins.json';dump(path,pins)
   for call in (lambda:gate.load_pins(path),lambda:gate.contract_basis(pins),lambda:gate.expected_anisette_evidence(pins),lambda:package.contract_files(pins),lambda:gate.verify_all(Path(tmp),pins),lambda:gate.acquire(Path(tmp),pins)):
    with self.subTest(call=call),self.assertRaisesRegex(ValueError,'pending'):call()
 def test_active_p0_pins_and_admission_unchanged(self):
  pins=gate.load_pins(STAGE/'historical-p0/maintained-sources.json');self.assertEqual(pins['source_basis'],prior.BASIS)
  if p2.REVIEW_SHA256 is None:self.assertEqual(pins,gate.load_pins(ROOT/'migration/maintained-sources.json'))
 def test_inherited_diagnostic_binary_gate_required(self):
  with self.assertRaisesRegex(ValueError,'diagnostic keys'):ipa.verify_constructor_diagnostic_binaries(b'',b'',b'',{'source_basis':p2.BASIS})
 def test_no_fake_success_receipts_in_pending_tree(self):
  if p2.REVIEW_SHA256 is not None:
   p2.approval(ROOT);return
  for name in ('reviewed-approval.json','native-capture.json','parent-ui-basis.json'):
   self.assertFalse((STAGE/name).exists())

class SourceMetadataTests(unittest.TestCase):
 def setUp(self):
  _,self.review,_=prior.approval(ROOT)
 def test_exact_source_only_metadata(self):
  delta=p2.validate_source_metadata(STAGE,PRIOR,self.review);self.assertEqual(len(delta['changes']),35)
 def test_all_historical_bytes_preserved(self):
  for name in p2.inherited_names(self.review)-{'maintained-sources.json'}:
   self.assertEqual((STAGE/'historical-p0'/name).read_bytes(),(PRIOR/name).read_bytes())
 def test_exact_composition_files(self):
  value=load(STAGE/'composition-parent-delta.json');self.assertEqual(set(value['changes']),p2.COMPOSITION_FILES)
 def test_only_four_contract_hashes_change(self):
  a=load(PRIOR/'owners/LiveContainer/runtime-contract.json');b=load(STAGE/'owners/LiveContainer/runtime-contract.json')
  changes=[x['path']for x,y in zip(a['sources'],b['sources'])if x!=y];self.assertEqual(changes,[
   'LiveContainerSwiftUI/App/AppDelegate.swift','LiveContainerSwiftUI/App/LiveContainerAutoRefreshAlarm.swift',
   'LiveContainerSwiftUI/Views/Settings/LCEmbeddedSideStoreRefreshView.swift','LiveContainerSwiftUI/Views/V3UnifiedShell.swift'])
 def test_source_metadata_mutations_rejected(self):
  mutations=[('reviewed-source-delta.json',lambda v:v.update(commit=p2.LC_PARENT)),
   ('composition-parent-delta.json',lambda v:v['changes'].pop('SideStoreSupport/SideStore.swift')),
   ('source-inventory.json',lambda v:v['AnisetteKit'].update(commit=p2.LC_COMMIT)),
   ('submodules.json',lambda v:v.pop('LiveContainer/OpenSSL')),
   ('owners/AnisetteKit/runtime-contract.json',lambda v:v.update(owner='LiveContainer')),
   ('owners/LiveContainer/runtime-contract.json',lambda v:v['provides'].pop()),
   ('compatibility-registry.json',lambda v:v['edges'].pop()),
   ('provenance/source-evidence.json',lambda v:v['owners']['LiveContainer']['sources'].pop())]
  for name,change in mutations:
   with self.subTest(name=name),tempfile.TemporaryDirectory()as tmp:
    target=Path(tmp)/'metadata';shutil.copytree(STAGE,target);value=load(target/name);change(value);dump(target/name,value)
    with self.assertRaises(ValueError):p2.validate_source_metadata(target,PRIOR,self.review)
 def test_actual_local_git_delta_if_configured(self):
  # CI need not fetch another source merely to run synthetic policy tests.
  value=os.environ.get('P2_COMPOSED_LIVE_CONTAINER_TEST_SOURCE')
  if not value:return
  source=Path(value)
  self.assertEqual(p2.git_delta(source,self.review['owners']['LiveContainer']['commit'],p2.LC_COMMIT),load(STAGE/'reviewed-source-delta.json'))
  self.assertEqual(p2.git_delta(source,p2.LC_PARENT,p2.LC_COMMIT),load(STAGE/'composition-parent-delta.json'))

class SyntheticCaptureTests(unittest.TestCase):
 def setUp(self):
  _,review,_=prior.approval(ROOT);self.owners=p2.expected_owners(review);self.capture=capture_fixture(self.owners)
 def test_synthetic_exact_schema(self):p2.validate_capture(self.capture,self.owners)
 def test_native_count_skip_and_type_mutations(self):
  for key,value in [('tests',4),('skipped',1),('failures',1),('errors',False)]:
   with self.subTest(key=key):
    record=copy.deepcopy(self.capture);record['consumer_tests'][key]=value
    with self.assertRaises(ValueError):p2.validate_capture(record,self.owners)
 def test_test_id_changes_rejected(self):
  record=copy.deepcopy(self.capture);record['consumer_tests']['test_ids'][0]='unverified'
  with self.assertRaises(ValueError):p2.validate_capture(record,self.owners)
 def test_unverified_source_or_tree_rejected(self):
  for owner in self.capture['tested_owners']:
   for key in ('commit','tree'):
    with self.subTest(owner=owner,key=key):
     record=copy.deepcopy(self.capture);record['tested_owners'][owner][key]='a'*40
     with self.assertRaises(ValueError):p2.validate_capture(record,self.owners)
 def test_old_ui_cannot_substitute_for_native(self):
  record=copy.deepcopy(self.capture);record['identity']['run_url']=p2.UI_RUN_URL
  with self.assertRaises(ValueError):p2.validate_capture(record,self.owners)
 def test_runner_or_input_changed_rejected(self):
  for key in ('host_commit','host_tree','approved_inputs_sha256'):
   record=copy.deepcopy(self.capture);record['identity'][key]='a'*len(record['identity'][key])
   with self.subTest(key=key),self.assertRaises(ValueError):p2.validate_capture(record,self.owners)
 def test_scope_overclaims_rejected(self):
  for key in self.capture['claims']:
   for wrong in (True,0):
    record=copy.deepcopy(self.capture);record['claims'][key]=wrong
    with self.subTest(key=key,wrong=wrong),self.assertRaises(ValueError):p2.validate_capture(record,self.owners)
 def test_platform_or_resource_failure_rejected(self):
  for key,value in [('sdk','iphoneos'),('compile','pending'),('localization','failed'),('exact_plural_values','failed'),('permissions','failed')]:
   record=copy.deepcopy(self.capture);record['product'][key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):p2.validate_capture(record,self.owners)
 def test_native_reference_inventory_exact(self):
  for name in ('missing','extra'):
   record=copy.deepcopy(self.capture)
   if name=='missing':record['evidence_files'].pop('source-before.json')
   else:record['evidence_files']['arbitrary']='a'*64
   with self.subTest(name=name),self.assertRaises(ValueError):p2.validate_capture(record,self.owners)
 def test_parent_ui_scope_exact(self):p2.validate_ui_basis(ui_fixture())
 def test_ui_cannot_be_relabeled_composed_or_wider(self):
  for key,value in [('composed_ui_reexecuted',True),('source_commit',p2.LC_COMMIT),('source_tree',p2.LC_TREE),('scope','All application UI'),('service_failure_unresolved',False),('full_app_acceptance',True),('final_composition_acceptance',True),('artifact_id',True),('run_attempt',True)]:
   row=ui_fixture();row[key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):p2.validate_ui_basis(row)

class SyntheticApprovalTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.stage=Path(self.tmp.name)/'metadata'
  shutil.copytree(STAGE,self.stage)
  _,old,_=prior.approval(ROOT);owners=p2.expected_owners(old)
  dump(self.stage/'native-capture.json',capture_fixture(owners));dump(self.stage/'parent-ui-basis.json',ui_fixture())
  self.review={'schema_version':1,'source_basis':p2.BASIS,'integration_parent':p2.INTEGRATION_PARENT,'inherited_p0_review_sha256':prior.REVIEW_SHA256,
   'owners':owners,'files':{},'native_validation':{'source_basis':p2.BASIS,'readiness_scope':'eligible_for_gated_full_build','review_sha256':None,
   'inherited_p0_review_sha256':prior.REVIEW_SHA256,'fresh_composed_capture_sha256':hashlib.sha256((self.stage/'native-capture.json').read_bytes()).hexdigest(),
   'parent_ui_basis_sha256':hashlib.sha256((self.stage/'parent-ui-basis.json').read_bytes()).hexdigest(),'tested_owners':capture_fixture(owners)['tested_owners'],'scope':p2.FRESH_SCOPE}}
  names=p2.SOURCE_NAMES|{'native-capture.json','parent-ui-basis.json'}|{'historical-p0/'+name for name in p2.inherited_names(old)}
  self.review['files']={n:hashlib.sha256((self.stage/n).read_bytes()).hexdigest()for n in names}
 def check(self):
  anchor=dump(self.stage/'reviewed-approval.json',self.review)
  with patch.object(p2,'REVIEW_SHA256',anchor):return p2.approval(ROOT,self.stage)
 def test_synthetic_exact_approval_schema_and_package(self):
  _,review,_=self.check()
  pins={'schema_version':1,'integration_baseline':prior.BASELINE,'source_basis':p2.BASIS,'contract_registry_sha256':review['files']['compatibility-registry.json'],
   'owners':{o:{k:v for k,v in row.items()if k!='tree'}for o,row in review['owners'].items()},'native_validation':{**review['native_validation'],'review_sha256':hashlib.sha256((self.stage/'reviewed-approval.json').read_bytes()).hexdigest()}}
  with patch.object(p2,'REVIEW_SHA256',pins['native_validation']['review_sha256']):
   p2.validate_pins(pins,review);files=p2.contract_files(pins,ROOT,self.stage)
   self.assertEqual(set(files),{'reviewed-approval.json',*review['files']})
   self.assertIn('historical-p0/allocation-helper.json',files)
 def pins(self):
  _,review,_=self.check();anchor=hashlib.sha256((self.stage/'reviewed-approval.json').read_bytes()).hexdigest()
  return {'schema_version':1,'integration_baseline':prior.BASELINE,'source_basis':p2.BASIS,'contract_registry_sha256':review['files']['compatibility-registry.json'],
   'owners':{o:{k:v for k,v in row.items()if k!='tree'}for o,row in review['owners'].items()},'native_validation':{**review['native_validation'],'review_sha256':anchor}}
 def test_activated_package_roundtrip_and_forgery_rejection(self):
  pins=self.pins();original=p2.approval
  def approved(integration_root,metadata_root=None):return original(integration_root,self.stage if metadata_root is None else metadata_root)
  output=Path(self.tmp.name)/'package'
  with patch.object(p2,'REVIEW_SHA256',pins['native_validation']['review_sha256']),patch.object(p2,'approval',side_effect=approved):
   self.assertEqual(gate.contract_basis(pins)[0],self.stage)
   hashes=package.collect_contract_evidence(output,pins);package.verify_contract_evidence(output,hashes,pins)
   manifest=gate.expected_anisette_evidence(pins)
   self.assertEqual(manifest,load(PRIOR/'anisette-generated/maintained-source-manifest.json'))
   self.assertEqual(manifest['source_basis'],prior.BASIS)
   for name in ('native-capture.json','historical-p0/allocation-helper.json','historical-p0/native-receipt.json','reviewed-source-delta.json'):
    path=output/package.CONTRACT_DIRECTORY/name;raw=path.read_bytes();old=hashes[name]
    path.write_bytes(raw+b'\n');hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    with self.subTest(path=name),self.assertRaises(ValueError):package.verify_contract_evidence(output,hashes,pins)
    path.write_bytes(raw);hashes[name]=old
   extra=output/package.CONTRACT_DIRECTORY/'unsealed.json';extra.write_text('{}')
   with self.assertRaisesRegex(ValueError,'inventory'):package.verify_contract_evidence(output,hashes,pins)
 def test_rehashed_other_owner_cannot_be_admitted(self):
  self.review['owners']['SideStore']['commit']=p2.LC_COMMIT
  with self.assertRaisesRegex(ValueError,'owner graph'):self.check()
 def test_rehashed_historical_native_evidence_rejected(self):
  name='historical-p0/native-receipt.json';value=load(self.stage/name);value['guest_execution']=True
  self.review['files'][name]=dump(self.stage/name,value)
  with self.assertRaisesRegex(ValueError,'inherited P0'):self.check()
 def test_rehashed_native_scope_cannot_claim_full_build(self):
  self.review['native_validation']['scope']='Full device and guest acceptance'
  with self.assertRaisesRegex(ValueError,'readiness'):self.check()
 def test_missing_or_extra_metadata_inventory_rejected(self):
  self.review['files']['invented.json']='a'*64
  with self.assertRaisesRegex(ValueError,'inventory'):self.check()

if __name__=='__main__':unittest.main()
