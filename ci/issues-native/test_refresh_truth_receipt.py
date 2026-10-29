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
  if kind=='rust':parts[kind]['logs']['idevice-ffi']='\n'.join('test '+name+' ... ok' for name in C.FFI_CASES)+'\ntest result: ok. 6 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n'
 return {'schema':C.SCHEMA,'status':C.STATUS,'identity':identity,'source_graph':graph,'parts':parts,'part_sha256':{k:C.digest(v) for k,v in parts.items()},'physical_device_execution':False,'os_profile_selection_proven':False,'future_certificate_trust_proven':False}
class ReceiptTests(unittest.TestCase):
 def setUp(self):self.value=fixture()
 def verify(self,value):return C.verify_portable(value,self.value['source_graph'],self.value['identity'])
 def reject(self,change,rehash=True):
  v=copy.deepcopy(self.value);change(v)
  if rehash:v['part_sha256']={k:C.digest(p) for k,p in v['parts'].items()}
  with self.assertRaises((ValueError,TypeError,KeyError)):self.verify(v)
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
