"""Synthetic receipt tamper controls, not Core Data execution."""
import copy, hashlib, json, tempfile, unittest, shutil, subprocess, sys, types
from pathlib import Path
from unittest import mock
import startup_upgrade_receipt as M
ROOT=Path(__file__).resolve().parents[2]
class BindingTests(unittest.TestCase):
 def setUp(self):
  self.identity={'workflow_commit':'a'*40,'run_id':'100','run_attempt':'1'}
  self.graph={'LiveContainer':['NRG-Wardog/LiveContainer','b'*40],'SideStore':['NRG-Wardog/SideStore','c'*40]}
  d={'schema':'coredata-owned-framework-discovery-v3','main_bundle_is_isolated_host':True,'main_bundle_model_resources_before':0,'main_bundle_model_resources_after':0,'source_model_found_before_framework_load':False,'unknown_source_model_found_in_explicit_owner':False,'owner_is_requested_framework':True,'main_is_requested_framework':False,'all_bundles_contains_owner':False,'all_frameworks_contains_owner':True,'baseline_source_model_found':False,'baseline_model_compatible':False,'explicit_owner_source_model_found':True,'explicit_supplied_source_model_found':True,'explicit_supplied_model_compatible':True,'explicit_owner_model_compatible':True,'physical_lc_bootstrap_executed':False,'limitation':'synthetic controls only','production_owned_source_model_found':True,'production_owned_model_compatible':True,'production_mapping_selected':True,'production_owned_model_urls_include_source_and_target':True,'production_wrong_version_bundle_exposes_current_model':True,'production_nil_owner_source_model_found':False,'production_unknown_source_model_found':False,'production_wrong_version_source_model_found':False}
  d['isolated_negative_processes']={mode:{'schema':'coredata-isolated-helper-negative-v1','mode':mode,'main_bundle_is_isolated_host':True,'owner_is_requested_framework':True,'current_model_is_v17_7':True,'source_model_found_before_framework_load':False,'baseline_source_model_found':False,'current_model_matches_old_metadata':False,'production_source_model_found':False,'physical_lc_bootstrap_executed':False,'main_bundle_model_resources_before':0,'main_bundle_model_resources_after':0} for mode in ('nil-owner','wrong-version','unknown')}
  report={'schema':'real-coredata-schema-qualification-v1','status':'PASS_REAL_MODELS_SQLITE_NOT_PRODUCTION_RELOCATION','run_identity':self.identity,'acquired_owners':{k:{'repository':v[0],'commit':v[1],'tree':'d'*40} for k,v in self.graph.items()},'acquisition_sha256':'e'*64,'source_before_sha256':'f'*64,'source_after_sha256':'1'*64,'other_graph_owners_acquired':False,'working_directory_outside_checkouts':True,'output_outside_checkouts':True,'source_sha256':{M.PERSISTENT:'2'*64,M.MODEL+'/AltStore 17_6.xcdatamodel/contents':'3'*64},'production_mapping_methods_sha256':'4'*64,'cases':[{'case':name,'status':'PASS'} for name in M.CASES],'framework_discovery':{layout:dict(d,owner_package_type=layout.split('-')[0]) for layout in ('FMWK-resources','APPL-flat')},'physical_device_execution':False,'production_relocation_enabled':False,'scope_limits':['synthetic limitation '+str(i) for i in range(6)]}
  self.value={'schema':M.SCHEMA,'status':M.STATUS,'identity':self.identity,'source_graph':self.graph,'qualification':report,'qualification_sha256':M.canonical(report),'evidence_sha256':{'schema-acquisition.json':'e'*64,'schema-source-immutability.json':'5'*64,'startup-upgrade/report.json':'6'*64,'startup-upgrade/source-before.json':'f'*64,'startup-upgrade/source-after.json':'1'*64},'verifier_sha256':M.sha(Path(M.__file__)),'physical_device_execution':False,'production_relocation_enabled':False}
 def verify(self,v):return M.verify_portable(v,self.graph,self.identity)
 def altered(self,change,*,rehash=False):
  v=copy.deepcopy(self.value);change(v)
  if rehash:v['qualification_sha256']=M.canonical(v['qualification'])
  with self.assertRaises((ValueError,KeyError,TypeError)):self.verify(v)
 def test_exact_portable_binding_passes(self):self.assertIs(self.verify(self.value),self.value)
 def test_identity_graph_hash_and_execution_tamper_rejected(self):
  for change in [lambda v:v['identity'].update(run_id='101'),lambda v:v['source_graph']['SideStore'].__setitem__(1,'9'*40),lambda v:v.update(verifier_sha256='0'*64),lambda v:v.update(physical_device_execution=True),lambda v:v.update(production_relocation_enabled=True),lambda v:v['evidence_sha256'].update({'schema-acquisition.json':'0'*64}),lambda v:v['evidence_sha256'].pop('schema-source-immutability.json'),lambda v:v['evidence_sha256'].update({'../secret.json':'0'*64})]:self.altered(change)
 def test_recomputed_wrapper_cannot_mask_wrong_native_run_or_source(self):
  for change in [lambda v:v['qualification']['run_identity'].update(run_attempt='2'),lambda v:v['qualification']['acquired_owners']['SideStore'].update(commit='0'*40),lambda v:v['qualification'].update(physical_device_execution=True),lambda v:v['qualification']['cases'].pop(),lambda v:v['qualification']['cases'][0].update(status='SKIP'),lambda v:v['qualification']['source_sha256'].update({'../outside':'0'*64}),lambda v:v['qualification'].update(production_relocation_enabled=True)]:self.altered(change,rehash=True)
 def test_all_discovery_positive_and_negative_flags_required(self):
  row=self.value['qualification']['framework_discovery']['FMWK-resources']
  for key,value in row.items():
   if type(value) is bool:
    # Bundle membership remains observational; not part of the causal guard.
    if key in ('all_bundles_contains_owner','all_frameworks_contains_owner'):continue
    with self.subTest(key=key):self.altered(lambda v:v['qualification']['framework_discovery']['FMWK-resources'].__setitem__(key,not value),rehash=True)
  self.altered(lambda v:v['qualification']['framework_discovery']['APPL-flat'].update(main_bundle_model_resources_before=True),rehash=True)
 def test_isolated_negative_process_evidence_required(self):
  for mode in ('nil-owner','wrong-version','unknown'):
   self.altered(lambda v:v['qualification']['framework_discovery']['FMWK-resources']['isolated_negative_processes'].pop(mode),rehash=True)
   for key,value in self.value['qualification']['framework_discovery']['FMWK-resources']['isolated_negative_processes'][mode].items():
    if type(value) is bool:
     self.altered(lambda v:v['qualification']['framework_discovery']['FMWK-resources']['isolated_negative_processes'][mode].__setitem__(key,not value),rehash=True)
 def test_native_source_hash_and_symlink_tamper_rejected(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();source=root/'SideStore';model=source/M.MODEL/'AltStore 17_6.xcdatamodel/contents';persistent=source/M.PERSISTENT
   model.parent.mkdir(parents=True);persistent.parent.mkdir(parents=True);model.write_text('model');persistent.write_text('source')
   report={'source_sha256':{p.relative_to(source).as_posix():M.sha(p) for p in (model,persistent)}}
   M.verify_sources(report,root)
   persistent.write_text('changed')
   with self.assertRaises(ValueError):M.verify_sources(report,root)
   persistent.write_text('source');model.unlink();outside=root/'outside';outside.write_text('model');model.symlink_to(outside)
   with self.assertRaises(ValueError):M.verify_sources(report,root)
 def test_retained_evidence_hashes_and_links_checked(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();folder=root/'startup-upgrade';folder.mkdir()
   for name in ['schema-acquisition.json','schema-source-immutability.json','schema-acquisition.log','schema-controls.log','schema-driver.log','startup-upgrade/report.json','startup-upgrade/source-before.json','startup-upgrade/source-after.json']:(root/name).write_text('{}')
   before=M.current_evidence(root);(folder/'report.json').write_text('{"changed":true}');self.assertNotEqual(before,M.current_evidence(root))
   (folder/'report.json').unlink();(folder/'report.json').symlink_to(root/'schema-acquisition.json')
   with self.assertRaises(ValueError):M.current_evidence(root)
 def test_native_pipeline_requires_separate_pristine_sources_and_binding(self):
  workflow=(ROOT/'.github/workflows/cold-vpn-native.yml').read_text()
  self.assertIn('id: schema_tests',workflow)
  self.assertIn('mkdir schema-sources',workflow)
  self.assertIn('--source "$PWD/schema-sources/SideStore" --output "$PWD/evidence/startup-upgrade"',workflow)
  self.assertIn('startup_upgrade_receipt.py seal --native-sources sources --schema-sources schema-sources',workflow)
  self.assertLess(workflow.index('id: schema_tests'),workflow.index('id: livecontainer_build'))
  step=workflow.split('id: schema_tests',1)[1].split('      - name:',1)[0]
  self.assertIn('set -euo pipefail',step);self.assertNotIn('continue-on-error',step);self.assertNotIn('|| true',step)
  record=(ROOT/'ci/issues-native/record.py').read_text();self.assertIn("verify_startup_upgrade(E / 'startup-upgrade-tests.json'",record)
  package=(ROOT/'ci/issues-native/package_candidate.py').read_text();self.assertEqual(package.count('verify_startup_upgrade(schema_receipt,'),2)
  self.assertIn("provenance['startup_upgrade_regression_sha256'] = sha(schema_receipt)",package)
  for name in ['acquire.py','independent_verify.py']:
   text=(ROOT/'ci/cold-vpn-zopfli'/name).read_text();self.assertIn('startup-upgrade-tests.json',text);self.assertIn('startup_upgrade_regression_sha256',text)
 def test_copied_helper_verifies_outside_repository(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();workspace=root/'unrelated'/'workspace';workspace.mkdir(parents=True)
   shutil.copy2(M.__file__,workspace/'startup_upgrade_receipt.py')
   (workspace/'receipt.json').write_text(json.dumps(self.value))
   code="import json; from pathlib import Path; import startup_upgrade_receipt as m; v=json.loads(Path('receipt.json').read_text()); m.verify_portable(v,v['source_graph'],v['identity']); print('PORTABLE_PASS')"
   result=subprocess.run([sys.executable,'-B','-c',code],cwd=workspace,capture_output=True,text=True,check=True)
   self.assertEqual(result.stdout.strip(),'PORTABLE_PASS')
 def test_local_seal_recheck_preserves_evidence_and_detects_tampering(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();native=root/'native';schema=root/'schema';schema.mkdir();evidence=root/'evidence';folder=evidence/'startup-upgrade';folder.mkdir(parents=True)
   source=native/'SideStore';model=source/M.MODEL/'AltStore 17_6.xcdatamodel/contents';persistent=source/M.PERSISTENT
   model.parent.mkdir(parents=True);persistent.parent.mkdir(parents=True);model.write_text('model');persistent.write_text('source')
   report=copy.deepcopy(self.value['qualification']);report['source_sha256']={p.relative_to(source).as_posix():M.sha(p) for p in (model,persistent)}
   for name in ['schema-acquisition.json','schema-source-immutability.json','startup-upgrade/source-before.json','startup-upgrade/source-after.json']:(evidence/name).write_text('{}')
   for name in ('schema-acquisition.log','schema-controls.log','schema-driver.log'):(evidence/name).write_text('controls')
   for key,name in [('acquisition_sha256','schema-acquisition.json'),('source_before_sha256','startup-upgrade/source-before.json'),('source_after_sha256','startup-upgrade/source-after.json')]:report[key]=M.sha(evidence/name)
   (folder/'report.json').write_text(json.dumps(report));before=M.current_evidence(evidence);calls=[]
   def acquire_verify(sources,acquisition,output):
    self.assertEqual(sources,schema);self.assertEqual(acquisition,evidence/'schema-acquisition.json');self.assertFalse(output.is_relative_to(evidence));calls.append(output);output.write_text('{}')
   output=evidence/'startup-upgrade-tests.json'
   with mock.patch.object(M,'focused_module',return_value=types.SimpleNamespace(verify=acquire_verify)):
    M.seal(output,native,schema,evidence,self.graph,self.identity)
    M.verify_local(output,native,schema,evidence,self.graph,self.identity)
    self.assertEqual(M.current_evidence(evidence),before);self.assertEqual(len(calls),2)
    self.assertTrue(all(not p.exists() for p in calls))
    persistent.write_text('changed')
    with self.assertRaises(ValueError):M.verify_local(output,native,schema,evidence,self.graph,self.identity)
    persistent.write_text('source');(folder/'source-after.json').write_text('{"tampered":true}')
    with self.assertRaises(ValueError):M.verify_local(output,native,schema,evidence,self.graph,self.identity)
 def test_duplicate_receipt_keys_rejected(self):
  with tempfile.TemporaryDirectory() as temporary:
   p=Path(temporary)/'receipt.json';p.write_text('{"status":"PASS","status":"FAIL"}')
   with self.assertRaises(ValueError):M.read(p)
if __name__=='__main__':unittest.main(verbosity=2)
