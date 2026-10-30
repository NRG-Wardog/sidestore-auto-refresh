"""Synthetic evidence contract negatives, never source/native fixture execution."""
import copy,json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest import mock
import refresh_truth_receipt as C

def fixture():
 graph={o:['NRG-Wardog/'+o.split('/')[-1],'a'*40] for o in C.INPUTS}
 identity={'workflow_commit':'b'*40,'run_id':'100','run_attempt':'1'}
 source={'owners':{o:{'repository':g[0],'commit':g[1],'tree':'c'*40} for o,g in graph.items()},'source_sha256':{o+'/'+p:'d'*64 for o,paths in C.INPUTS.items() for p in paths}}
 parts={}
 for kind in ('swift','rust'):
  logs={o:'\nRan '+str(n)+' tests in 0.123s\n\nOK\n' for o,(_,n) in C.SUITES.items()} if kind=='swift' else {'idevice':'\n'.join('test '+name+' ... ok' for name in C.RUST_CASES)+'\ntest result: ok. 4 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n'}
  parts[kind]={'schema':C.SCHEMA+'-part','kind':kind,'status':'PASS','identity':identity,'source_graph':graph,'source':source,'logs':logs,'compiler':{'path_sha256':'e'*64,'version':'synthetic compiler model'},'physical_device_execution':False}
  if kind=='swift':parts[kind]['logs']['LiveContainer-observations']+='\n'.join('PASS '+name for name in C.OBSERVATION_CASES)+'\nREFRESH_OBSERVATIONS_PASS\n'
  if kind=='swift':
   for owner,(schema,cases) in C.LEGACY_NATIVE.items():parts[kind]['logs'][owner]+=json.dumps({'schema':schema,'status':'PASS','cases':list(cases)})+'\n'
  if kind=='swift':parts[kind]['logs']['SideStore-legacy-coordinator']+='\n'.join(C.LEGACY_COORDINATOR_MARKERS)+'\n'
  if kind=='swift':parts[kind]['logs']['LiveContainer-legacy']+='\n'.join('PASS '+name for name in C.LEGACY_HOST_CASES)+'\nLEGACY_REPLACEMENT_HOST_PASS\n'
  if kind=='rust':parts[kind]['logs']['idevice-ffi']='\n'.join('test '+name+' ... ok' for name in C.FFI_CASES)+'\ntest result: ok. 6 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n'
 return {'schema':C.SCHEMA,'status':C.STATUS,'identity':identity,'source_graph':graph,'parts':parts,'part_sha256':{k:C.digest(v) for k,v in parts.items()},'physical_device_execution':False,'os_profile_selection_proven':False,'future_certificate_trust_proven':False}
class ReceiptTests(unittest.TestCase):
 def setUp(self):self.value=fixture()
 def verify(self,value):return C.verify_portable(value,self.value['source_graph'],self.value['identity'])
 def reject(self,change,rehash=True):
  v=copy.deepcopy(self.value);change(v)
  if rehash:v['part_sha256']={k:C.digest(p) for k,p in v['parts'].items()}
  with self.assertRaises((ValueError,TypeError,KeyError)):self.verify(v)
 def test_observation_native_suite_is_mandatory(self):
  self.assertEqual(C.SUITE_OWNERS['LiveContainer-observations'],'LiveContainer')
  self.assertEqual(C.SUITES['LiveContainer-observations'],('.ci/refresh-observations/test_refresh_observations.py',3))
  self.assertEqual(len(C.OBSERVATION_CASES),47)
  self.reject(lambda v:v['parts']['swift']['logs'].pop('LiveContainer-observations'))
  for replacement in ('Ran 3 tests in 0.1s\nOK (skipped=1)\n','Ran 3 tests in 0.1s\nOK\n'):
   self.reject(lambda v:v['parts']['swift']['logs'].__setitem__('LiveContainer-observations',replacement))
 def test_observation_runtime_cases_and_completion_are_exact(self):
  for old,new in [('PASS '+C.OBSERVATION_CASES[0]+'\n',''),('PASS '+C.OBSERVATION_CASES[0],'PASS '+C.OBSERVATION_CASES[1]),('REFRESH_OBSERVATIONS_PASS',''),('REFRESH_OBSERVATIONS_PASS','REFRESH_OBSERVATIONS_PASS\nREFRESH_OBSERVATIONS_PASS')]:
   self.reject(lambda v:v['parts']['swift']['logs'].__setitem__('LiveContainer-observations',v['parts']['swift']['logs']['LiveContainer-observations'].replace(old,new)))
 def test_observation_source_closure_is_bound(self):
  for path in ('.ci/vpn-refresh-recovery/test_classifier_dependency.py','.ci/comprehensive-diagnostics/generate_wire_fixture.py','.ci/refresh-observations/test_refresh_observations.py','.ci/refresh-observations/cases.swift','.ci/refresh-observations/localization_keys.json'):
   self.assertIn(path,C.INPUTS['LiveContainer'])
   self.reject(lambda v:v['parts']['swift']['source']['source_sha256'].pop('LiveContainer/'+path))
  for path in ('SideStore/Core/DeviceApi/MinimuxerWrapper.swift','tests/cold_vpn_readiness/test_transport_observation.py','tests/cold_vpn_readiness/transport_observation_cases.swift'):
   self.assertIn(path,C.INPUTS['SideStore'])
 def test_explicit_replacement_mutation_boundaries_are_source_bound(self):
  paths=('SideStore/Core/Operations/PipelineExecutionHandler.swift','SideStore/Core/Operations/PipelineOperations/UpdateAppCertificateOperation.swift','SideStore/Core/Operations/PipelineOperations/StageAppOperation.swift','SideStore/Core/Operations/PipelineOperations/SendAppOperation.swift','SideStore/Core/Operations/PipelineOperations/RemoveAppExtensionsOperation.swift','SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift','SideStore/Core/Operations/PipelineOperations/ResignAppOperation.swift')
  for path in paths:
   self.assertIn(path,C.INPUTS['SideStore'])
   self.reject(lambda v:v['parts']['swift']['source']['source_sha256'].pop('SideStore/'+path))
 def test_legacy_native_results_cannot_be_missing_duplicated_or_static(self):
  for owner,(schema,cases) in C.LEGACY_NATIVE.items():
   self.assertIn(owner,C.SUITES)
   self.reject(lambda v:v['parts']['swift']['logs'].pop(owner))
   header='Ran '+str(C.SUITES[owner][1])+' tests in 0.1s\nOK\n'
   row=json.dumps({'schema':schema,'status':'PASS','cases':list(cases)})
   for bad in (header,header+row+'\n'+row,header+row.replace('"PASS"','"STATIC_PASS"'),header+row.replace(cases[0],cases[1]),header+row.replace('"status": "PASS"','"status": "PASS", "status": "PASS"')):
    self.reject(lambda v:v['parts']['swift']['logs'].__setitem__(owner,bad))
 def test_legacy_hook_fixture_inputs_bound(self):
  self.assertEqual(C.SUITES['SideStore-legacy-hooks'],('tests/legacy_replacement_hooks/test_contract.py',4))
  self.assertEqual(C.SUITE_OWNERS['SideStore-legacy-hooks'],'SideStore')
  self.assertEqual(len(C.LEGACY_NATIVE['SideStore-legacy-hooks'][1]),35)
  for path in ('SideStore/Utils/common/AppBundleFingerprint.swift','tests/legacy_replacement_hooks/test_contract.py','tests/legacy_replacement_hooks/doubles.swift','tests/legacy_replacement_hooks/cases.swift'):
   self.assertIn(path,C.INPUTS['SideStore'])
   self.reject(lambda v:v['parts']['swift']['source']['source_sha256'].pop('SideStore/'+path))
  for path in ('Sources/CodeSigning/CodeSignerAPI.swift','Sources/CodeSigning/ExtensionSigningPolicy.swift'):
   self.assertIn(path,C.INPUTS['SideStore/Dependencies/SideSign'])
   self.reject(lambda v:v['parts']['swift']['source']['source_sha256'].pop('SideStore/Dependencies/SideSign/'+path))
 def test_legacy_coordinator_counterfactual_evidence_required(self):
  owner='SideStore-legacy-coordinator'
  self.assertEqual(C.SUITES[owner],('tests/legacy_replacement/test_contract.py',4))
  self.assertEqual(C.SUITE_OWNERS[owner],'SideStore')
  for marker in C.LEGACY_COORDINATOR_MARKERS:
   for replacement in ('',marker+'\n'+marker):
    self.reject(lambda v:v['parts']['swift']['logs'].__setitem__(owner,v['parts']['swift']['logs'][owner].replace(marker,replacement)))
  for path in ('tests/legacy_replacement/test_contract.py','tests/legacy_replacement/Boundary.swift','tests/legacy_replacement/Cases.swift','tests/legacy_replacement/README.md'):
   self.assertIn(path,C.INPUTS['SideStore'])
   self.reject(lambda v:v['parts']['swift']['source']['source_sha256'].pop('SideStore/'+path))
 def test_legacy_host_native_case_and_source_closure_required(self):
  owner='LiveContainer-legacy'
  self.assertEqual(C.SUITES[owner],('.ci/legacy-replacement/test_contract.py',4))
  self.assertEqual(C.SUITE_OWNERS[owner],'LiveContainer')
  self.assertEqual(len(C.LEGACY_HOST_CASES),64)
  for old,new in [('PASS '+C.LEGACY_HOST_CASES[0]+'\n',''),('PASS '+C.LEGACY_HOST_CASES[0],'PASS '+C.LEGACY_HOST_CASES[1]),('LEGACY_REPLACEMENT_HOST_PASS',''),('LEGACY_REPLACEMENT_HOST_PASS','LEGACY_REPLACEMENT_HOST_PASS\nLEGACY_REPLACEMENT_HOST_PASS')]:
   self.reject(lambda v:v['parts']['swift']['logs'].__setitem__(owner,v['parts']['swift']['logs'][owner].replace(old,new)))
  for path in ('.ci/legacy-replacement/test_contract.py','.ci/legacy-replacement/cases.swift','.ci/legacy-replacement/localization_keys.json'):
   self.assertIn(path,C.INPUTS['LiveContainer'])
   self.reject(lambda v:v['parts']['swift']['source']['source_sha256'].pop('LiveContainer/'+path))
 def test_valid_synthetic_contract(self):self.assertIs(self.verify(self.value),self.value)
 def test_identity_source_status_and_claims_rejected(self):
  for key,bad in [('status','PASS_STATIC'),('physical_device_execution',True),('os_profile_selection_proven',True),('future_certificate_trust_proven',1)]:
   with self.subTest(key=key):self.reject(lambda v:v.__setitem__(key,bad))
  self.reject(lambda v:v['parts']['swift']['identity'].__setitem__('run_id','101'))
  self.reject(lambda v:v['parts']['rust']['source_graph']['idevice'].__setitem__(1,'f'*40))
 def test_missing_extra_and_rehashed_changed_sources_rejected(self):
  self.reject(lambda v:v['parts'].pop('rust'))
  self.reject(lambda v:v['parts']['swift']['source']['source_sha256'].pop(next(iter(v['parts']['swift']['source']['source_sha256']))))
  self.reject(lambda v:v['parts']['rust']['source']['source_sha256'].__setitem__('unreviewed.swift','d'*64))
  self.reject(lambda v:v['parts']['rust']['source']['owners']['idevice'].__setitem__('commit','f'*40))
 def test_skipped_empty_wrong_count_native_tests_rejected(self):
  for log in ('Ran 0 tests in 0.001s\nOK\n','Ran 2 tests in 0.001s\nOK (skipped=1)\n','Ran 2 tests in 0.001s\nFAILED\n'):
   self.reject(lambda v:v['parts']['swift']['logs'].__setitem__('LiveContainer',log))
  self.reject(lambda v:v['parts']['rust']['logs'].__setitem__('idevice','test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured;'))
  self.reject(lambda v:v['parts']['rust']['logs'].__setitem__('idevice',v['parts']['rust']['logs']['idevice'].replace(C.RUST_CASES[0],C.RUST_CASES[1])))
 def test_digests_and_duplicate_keys_rejected(self):
  self.reject(lambda v:v['part_sha256'].__setitem__('swift','f'*64),False)
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'bad.json';p.write_text('{"status":"PASS","status":"FAIL"}')
   with self.assertRaisesRegex(ValueError,'duplicate receipt key'):C.read(p)
 def test_portable_receipt_validates_from_unrelated_directory(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();shutil.copy2(C.__file__,root/'refresh_truth_receipt.py');(root/'receipt.json').write_text(json.dumps(self.value))
   script="from pathlib import Path; import refresh_truth_receipt as c; v=c.read(Path('receipt.json')); c.verify_portable(v,v['source_graph'],v['identity']); print('PASS_SYNTHETIC')"
   result=subprocess.run([sys.executable,'-B','-c',script],cwd=root,check=True,capture_output=True,text=True,timeout=30)
   self.assertEqual(result.stdout.strip(),'PASS_SYNTHETIC')
 def test_inherited_parent_git_root_is_rejected(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();source=root/'LiveContainer';source.mkdir()
   subprocess.run(['git','init','-q',str(root)],check=True)
   graph={'owners':{'LiveContainer':['NRG-Wardog/LiveContainer','a'*40]},'reviewed_deltas':{'LiveContainer':{'tree':'b'*40}}}
   with mock.patch.object(C,'INPUTS',{'LiveContainer':('test.swift',)}),self.assertRaisesRegex(ValueError,'inherited parent Git root'):C.collect(root,graph)
class SealedCompilerLifecycleTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.sources=Path(self.temp.name).resolve();self.root=self.sources/'SideStore';self.root.mkdir()
  self.git=lambda *args:subprocess.check_output(['git','-C',str(self.root),'-c','core.hooksPath=/dev/null',*args],text=True).strip()
  self.git('init','-q');self.git('config','user.name','synthetic');self.git('config','user.email','fixture@example.invalid')
  self.source=self.root/'source.swift';self.source.write_text('let value = 1\n')
  self.lock=self.root/'AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved';self.lock.parent.mkdir(parents=True);self.lock.write_text('original lock')
  self.git('add','.');self.git('commit','-qm','synthetic source')
  self.commit=self.git('rev-parse','HEAD');self.tree=self.git('rev-parse','HEAD^{tree}')
  self.graph={'owners':{'SideStore':['NRG-Wardog/SideStore',self.commit]},'reviewed_deltas':{'SideStore':{'tree':self.tree}}}
  self.patch=mock.patch.object(C,'INPUTS',{'SideStore':('source.swift',)});self.patch.start()
 def tearDown(self):self.patch.stop();self.temp.cleanup()
 def seal(self):
  return {'SideStore':{'commit':self.commit,'tree':self.tree,'origin':'synthetic',
    'files':{name:{'mode':'100644','sha256':C.sha(self.root/name)} for name in self.git('ls-files').splitlines()}}}
 def test_only_sealed_resolver_metadata_lifecycle_is_accepted(self):
  before=C.collect(self.sources,self.graph)
  self.lock.write_text('reviewed resolver originHash metadata model')
  with self.assertRaisesRegex(ValueError,'dirty'):C.collect(self.sources,self.graph)
  self.assertEqual(C.collect(self.sources,self.graph,self.seal()),before)
 def test_staged_blob_with_restored_worktree_is_rejected(self):
  sealed=self.seal();self.source.write_text('staged change');self.git('add','source.swift');self.source.write_text('let value = 1\n')
  with self.assertRaisesRegex(ValueError,'index differs'):C.collect(self.sources,self.graph,sealed)
 def test_mode_change_rejected(self):
  sealed=self.seal();self.source.chmod(0o755)
  with self.assertRaisesRegex(ValueError,'mode differs'):C.collect(self.sources,self.graph,sealed)
 def test_changed_source_and_unsealed_extra_rejected(self):
  sealed=self.seal();self.source.write_text('changed')
  with self.assertRaisesRegex(ValueError,'tracked compiler source differs'):C.collect(self.sources,self.graph,sealed)
  self.source.write_text('let value = 1\n');(self.root/'unexpected.swift').write_text('unsealed')
  with self.assertRaisesRegex(ValueError,'unsealed generated'):C.collect(self.sources,self.graph,sealed)
 def test_even_rehashed_snapshot_cannot_accept_changed_source(self):
  self.source.write_text('changed')
  with self.assertRaisesRegex(ValueError,'unexpected tracked compiler modification'):C.collect(self.sources,self.graph,self.seal())

if __name__=='__main__':unittest.main(verbosity=2)
