"""Synthetic offline finalizer fixtures. Never native evidence or production approval.

Git reads and execution reports in end-to-end fixtures are explicit test doubles.
Archive integrity and staged runtime bindings are separately exercised without
those doubles. No fixture is published or retained outside temporary storage.
"""
import copy
import hashlib
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch
import zipfile
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('constructor_finalizer',ROOT/'migration/finalize_p0_constructor_candidate.py')
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
# Known source candidates are only fixture values, never a finalizer allowlist.
FIXTURE_CANDIDATES={
 'AnisetteKit':'94659b4d54eed5729b34f4103b3f70579aa8d8e9',
 'LiveContainer':'fb376d0ccd8b845576b529dd36d9940843a55794',
 'SideSign':'b829a21c3955b4084fc3a6f613bad9162a8d43ef',
 'SideStore':'5c1f63f89f6b1d441cc3874928ae88e2e5e88201',
 **{n:f.admission.PARENTS[n]for n in ('minimuxer','idevice','jktcp')}}
def h(v):return hashlib.sha256(('SYNTHETIC ONLY '+v).encode()).hexdigest()
def c(v):return hashlib.sha1(('SYNTHETIC ONLY '+v).encode()).hexdigest()
def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(f.dump_bytes(v))

class MemoryArtifact:
    raw=f.Artifact.raw
    json=f.Artifact.json
    command=f.Artifact.command
    identity=f.Artifact.identity
    def __init__(self,number):
        self.data={};self.run={'id':number,'run_attempt':1,'head_sha':c('runner'+str(number)),'html_url':'https://github.com/'+f.REPOSITORY+'/actions/runs/'+str(number)}
        self.artifact={'id':number+1000};self.zip_sha256=h('artifact'+str(number));self.metadata_sha256={'run':h('run'+str(number)),'artifact':h('api'+str(number))}
    def raw(self,name):
        f.need(name in self.data,'missing indexed evidence: '+name);return self.data[name]
    def put(self,name,value):self.data[name]=f.dump_bytes(value)
    def log(self,name,text='',args=None):
        self.data['logs/'+name+'.log']=text.encode();self.data['logs/'+name+'.exit-code.txt']=b'0\n'
        self.put('logs/'+name+'.command.json',{'argv':args or ['SYNTHETIC_TEST_COMMAND'],'cwd':None})
    def seal(self):
        self.index={n:f.sha(v)for n,v in self.data.items()};self.index_raw=f.dump_bytes(self.index)
    def close(self):pass

class ArtifactIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.files={'provenance/example.json':b'{"synthetic":true}\n'}
        self.run={'id':123,'run_attempt':1,'head_sha':c('runner'),'status':'completed','conclusion':'success','repository':{'full_name':f.REPOSITORY},
                  'path':f.WORKFLOW,'event':'push','head_branch':'validation/p0-constructor-diagnostics','html_url':'https://github.com/'+f.REPOSITORY+'/actions/runs/123'}
        self.meta={'id':456,'expired':False,'workflow_run':{'id':123,'head_sha':c('runner')}}
    def prepare(self,extra=None,index=None):
        p=self.root/'synthetic.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            with zipfile.ZipFile(p,'w')as z:
                for n,v in self.files.items():z.writestr(n,v)
                z.writestr('evidence-index.json',f.dump_bytes(index if index is not None else {n:f.sha(v)for n,v in self.files.items()}))
                if extra:extra(z)
        self.meta['digest']='sha256:'+f.file_sha(p);write(self.root/'run.json',self.run);write(self.root/'artifact.json',self.meta);return p
    def open(self):return f.Artifact(self.root/'synthetic.zip',self.root/'run.json',self.root/'artifact.json')
    def test_exact_download_index_and_api_binding(self):
        self.prepare();a=self.open();self.addCleanup(a.close);self.assertEqual(a.raw('provenance/example.json'),self.files['provenance/example.json'])
    def test_wrong_artifact_digest(self):
        self.prepare();self.meta['digest']='sha256:'+h('different');write(self.root/'artifact.json',self.meta)
        with self.assertRaisesRegex(ValueError,'digest'):self.open()
    def test_wrong_run_artifact_link(self):
        self.prepare();self.meta['workflow_run']['id']=999;write(self.root/'artifact.json',self.meta)
        with self.assertRaisesRegex(ValueError,'identity'):self.open()
    def test_unsuccessful_run(self):
        self.run['conclusion']='failure';self.prepare()
        with self.assertRaisesRegex(ValueError,'completed/success'):self.open()
    def test_cli_failed_run_never_creates_candidate(self):
        self.run['conclusion']='failure';self.prepare();out=self.root/'candidate';args=[]
        for phase in ('phase1','phase2'):
            args+=['--'+phase+'-zip',str(self.root/'synthetic.zip'),'--'+phase+'-run-json',str(self.root/'run.json'),'--'+phase+'-artifact-json',str(self.root/'artifact.json')]
        args+=['--runner-repo',str(self.root),'--approved-inputs',str(self.root/'run.json'),'--approved-inputs-sha256',h('invalid'),
               '--owner','AnisetteKit='+str(self.root),'--final-sidestore-commit',c('unpublished'),'--output',str(out)]
        with contextlib.redirect_stderr(io.StringIO())as errors:self.assertEqual(f.main(args),1)
        self.assertEqual(json.loads(errors.getvalue())['status'],'REJECTED');self.assertFalse(out.exists())
    def test_expired_artifact(self):
        self.meta['expired']=True;self.prepare()
        with self.assertRaisesRegex(ValueError,'expired'):self.open()
    def test_wrong_repository_workflow_or_branch(self):
        for key,value in [('path','other.yml'),('head_branch','main'),('event','pull_request')]:
            old=self.run[key];self.run[key]=value;self.prepare()
            with self.assertRaises(ValueError):self.open()
            self.run[key]=old
    def test_boolean_run_id_not_integer(self):
        self.run['id']=True;self.prepare()
        with self.assertRaises(ValueError):self.open()
    def test_wrong_index_hash(self):
        self.prepare(index={'provenance/example.json':h('wrong')})
        with self.assertRaisesRegex(ValueError,'drift'):self.open()
    def test_unindexed_file(self):
        self.prepare(extra=lambda z:z.writestr('extra.txt',b'unindexed'))
        with self.assertRaisesRegex(ValueError,'inventory'):self.open()
    def test_missing_indexed_file(self):
        self.prepare(index={'missing.txt':h('missing')})
        with self.assertRaisesRegex(ValueError,'inventory'):self.open()
    def test_duplicate_zip_entry(self):
        self.prepare(extra=lambda z:z.writestr('provenance/example.json',b'duplicate'))
        with self.assertRaisesRegex(ValueError,'duplicate'):self.open()
    def test_unsafe_archive_path(self):
        self.prepare(extra=lambda z:z.writestr('../escape',b'no'))
        with self.assertRaisesRegex(ValueError,'unsafe'):self.open()
    def test_linked_archive_member(self):
        def linked(z):
            i=zipfile.ZipInfo('link');i.create_system=3;i.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(i,b'target')
        self.prepare(extra=linked)
        with self.assertRaisesRegex(ValueError,'linked'):self.open()

class CandidateFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.a=MemoryArtifact(123);self.b=MemoryArtifact(456);self.runner=self.root/'runner'
        _,self.prior,_=f.p0.approval(ROOT);self.old_inventory=f.load(ROOT/f.p0.DIRECTORY/'source-inventory.json')
        self.final=c('final SideStore lock-only');self.repos={n:self.root/n for n in f.admission.CHANGED_OWNERS}
        self.git_files={};self.snapshots={};self.inputs2={'schema_version':1,'phase':'sidestore','source_basis':f.admission.BASIS,'integration_baseline':f.admission.INTEGRATION_PARENT,
            'owners':{},'adapter_files':{'adapter.py':h('adapter')},'contracts':{'registry':'contracts/compatibility-registry.json','registry_sha256':f.file_sha(f.STAGE/'compatibility-registry.json'),
            'validator':'contracts/validate_contracts.py','validator_sha256':f.file_sha(f.STAGE/'validate_contracts.py')},'test_suites':[],
            'submodules':{n:v for n,v in f.p0.child_map(ROOT/f.p0.DIRECTORY).items()if not n.startswith('SideStore/')}}
        self.git_files['adapter.py']=b'SYNTHETIC ONLY adapter'
        self.git_files['contracts/compatibility-registry.json']=(f.STAGE/'compatibility-registry.json').read_bytes()
        self.git_files['contracts/validate_contracts.py']=(f.STAGE/'validate_contracts.py').read_bytes()
        for owner,commit in FIXTURE_CANDIDATES.items():
            previous=self.prior['owners'][owner];changes={};tree=previous['tree']
            if owner in self.repos:
                path=next(iter(self.old_inventory[owner]['changes']));before=copy.deepcopy(self.old_inventory[owner]['changes'][path]['after'])
                after={'mode':'100644','type':'blob','git_object':c(owner+' changed'),'sha256':h(owner+' changed')};changes[path]={'before':before,'after':after};tree=c(owner+' candidate tree')
            row={'repository':previous['repository'],'commit':commit,'tree':tree,'changes':changes};self.inputs2['owners'][owner]=row
            transition={'baseline_commit':f.admission.PARENTS[owner],'commit':commit,'tree':tree,'changes':copy.deepcopy(changes)}
            cumulative=copy.deepcopy(self.old_inventory[owner]);cumulative.update(commit=commit,tree=tree)
            for path,change in changes.items():cumulative['changes'][path]['after']=change['after']
            if owner in self.repos:
                self.snapshots[(owner,f.admission.PARENTS[owner],commit)]=transition
                self.snapshots[(owner,f.BASES[owner],commit)]=cumulative
        for sid,ids in f.SUITE_IDS.items():
            path='tests/'+sid+'.py';data=('SYNTHETIC ONLY suite '+sid).encode();self.git_files[path]=data
            self.inputs2['test_suites'].append({'id':sid,'owner':'LiveContainer'if sid in ('initializer-diagnostics','adi-consumption','descriptor-progression','pending-certificate-host-recovery')else'SideStore',
                'files':{path:f.sha(data)},'modules':['synthetic'],'expected_test_ids':list(ids)})
            report={'tests':len(ids),'failures':0,'errors':0,'skips':0,'expected_failures':0,'unexpected_successes':0,'test_ids':list(ids)}
            self.b.put('provenance/suites/'+sid+'.json',report);self.b.log('suite-'+sid,json.dumps(report)+'\n')
        self.inputs1=copy.deepcopy(self.inputs2);self.inputs1.update(phase='sidesign',contracts=None,test_suites=[],prior_sidesign=None,submodules={})
        self.inputs1['owners']={n:copy.deepcopy(self.inputs2['owners'][n])for n in ('AnisetteKit','SideSign')}
        self.inputs1['owners']['SideSign'].update(commit=c('tested SideSign'),tree=c('tested SideSign tree'))
        self.locks={}
        for owner in ('SideSign','SideStore'):
            old={'version':3,'originHash':h(owner+' old manifest'),'pins':[{'identity':'anisettekit','location':'https://github.com/NRG-Wardog/AnisetteKit.git','state':{'revision':f.admission.PARENTS['AnisetteKit']}}]}
            new=copy.deepcopy(old);new['originHash']=h(owner+' current manifest');new['pins'][0]['state']={'revision':FIXTURE_CANDIDATES['AnisetteKit']}
            self.locks[owner]=(f.dump_bytes(old),f.dump_bytes(new))
        for artifact,inputs,owner in ((self.a,self.inputs1,'SideSign'),(self.b,self.inputs2,'SideStore')):
            self.setup_source_proof(artifact,inputs);self.setup_result(artifact,inputs,owner)
        self.setup_phase1();self.a.seal()
        identity=self.identity(self.a)
        self.inputs2['prior_sidesign']={'index_sha256':identity['evidence_index_sha256'],'run_id':123,'run_attempt':1,'host_commit':identity['host_commit'],'artifact_id':identity['artifact_id'],'artifact_sha256':identity['artifact_sha256']}
        self.setup_result(self.b,self.inputs2,'SideStore');self.setup_phase2()
        self.b.put('provenance/prior-github-identity.json',{'run':{'id':123,'head_sha':identity['host_commit'],'run_attempt':1,'conclusion':'success'},
            'artifact':{'id':identity['artifact_id'],'digest':'sha256:'+identity['artifact_sha256']},'transport':{'repository':f.REPOSITORY,'run_id':'456','run_attempt':'1','host_commit':self.b.run['head_sha']}})
        self.b.put('provenance/prior-sidesign-link.json',{'index_sha256':identity['evidence_index_sha256'],'tested_commit':self.inputs1['owners']['SideSign']['commit'],
            'final_commit':FIXTURE_CANDIDATES['SideSign'],'lock_only_descendant':True,'native83_reused_for_exact_unchanged_AK':True,'prior_run':self.a.json('provenance/result.json')['run']})
        self.b.seal();self.approved=self.b.raw('provenance/approved-inputs.json');self.approved_sha=f.sha(self.approved)
        for owner in ('SideSign','SideStore'):
            before=self.inputs1['owners'][owner]['commit']if owner=='SideSign'else FIXTURE_CANDIDATES[owner];after=FIXTURE_CANDIDATES[owner]if owner=='SideSign'else self.final;lock=f.admission.LOCK_FILES[owner]
            changes={lock:{'before':{'mode':'100644','type':'blob','git_object':c(owner+'old lock'),'sha256':f.sha(self.locks[owner][0])},'after':{'mode':'100644','type':'blob','git_object':c(owner+'new lock'),'sha256':f.sha(self.locks[owner][1])}}}
            self.snapshots[(owner,before,after)]={'baseline_commit':before,'commit':after,'tree':c('final tree'),'changes':changes}
            self.git_files[(owner,before+':'+lock)]=self.locks[owner][0];self.git_files[(owner,after+':'+lock)]=self.locks[owner][1]
        for base in (f.admission.PARENTS['SideStore'],f.BASES['SideStore']):
            row=copy.deepcopy(self.snapshots[('SideStore',base,FIXTURE_CANDIDATES['SideStore'])]);row.update(commit=self.final,tree=c('final tree'));delta=self.snapshots[('SideStore',FIXTURE_CANDIDATES['SideStore'],self.final)]['changes']
            for path,change in delta.items():
                if path in row['changes']:row['changes'][path]['after']=change['after']
                else:row['changes'][path]=copy.deepcopy(change)
            self.snapshots[('SideStore',base,self.final)]=row
        self.patches=[patch.object(f,'git',side_effect=self.git),patch.object(f,'snapshot',side_effect=lambda repo,base,commit:copy.deepcopy(self.snapshots[(repo.name,base,commit)]))]
        for item in self.patches:item.start();self.addCleanup(item.stop)
    def identity(self,a):return {'run_url':a.run['html_url'],'run_attempt':1,'host_commit':a.run['head_sha'],'host_tree':c(a.run['head_sha']+' tree'),'artifact_id':a.artifact['id'],
        'artifact_sha256':a.zip_sha256,'approved_inputs_sha256':f.sha(a.raw('provenance/approved-inputs.json')),'evidence_index_sha256':f.sha(a.index_raw)}
    def git(self,repo,*args):
        if args[0]=='rev-parse':return (c(args[1][:-7]+' tree')+'\n').encode()
        if args[0]=='show':
            key=(repo.name,args[1]);return self.git_files[key]if key in self.git_files else self.git_files[args[1].split(':',1)[1]]
        if args[0]=='merge-base':return b''
        if args[0]=='rev-list':return b'commit parent\n'
        raise AssertionError(('unexpected synthetic Git command',args))
    def setup_source_proof(self,a,inputs):
        report={n:{'commit':r['commit'],'tree':r['tree'],'origin':{'kind':'direct','repository':r['repository']},'tracked_entries':1,'submodules':[]}for n,r in inputs['owners'].items()}
        children=copy.deepcopy(inputs['submodules'])
        if inputs['phase']=='sidestore':
            for n in ('SideSign','minimuxer'):
                r=inputs['owners'][n];children['SideStore/Dependencies/'+n]={'commit':r['commit'],'tree':r['tree']}
        for path,row in children.items():
            owner,child=path.split('/',1);report[owner]['submodules'].append({'path':child,'commit':row['commit'],'tree':row['tree'],'tracked_entries':1,'submodules':[]})
        a.put('provenance/source-before.json',report);a.put('provenance/source-after.json',report)
        ak=inputs['owners']['AnisetteKit'];a.put('provenance/resolved-anisette.json',{'commit':ak['commit'],'tree':ak['tree'],'source_sha256':{n:v['after']['sha256']for n,v in ak['changes'].items()}})
    def setup_result(self,a,inputs,owner):
        a.put('provenance/approved-inputs.json',inputs)
        a.put('provenance/result.json',{'status':'CAPTURED_FOR_REVIEW','last_stage':'captured','source_basis':f.admission.BASIS,'phase':inputs['phase'],
            'production_ready':False,'ipa_built':False,'ADI_executed':False,'device_validation':False,'owners':inputs['owners'],
            'approved_inputs_sha256':f.sha(a.raw('provenance/approved-inputs.json')),
            'run':{'url':a.run['html_url'],'attempt':1,'host_commit':a.run['head_sha'],'host_tree':c(a.run['head_sha']+' tree')},
            'lock':{'sha256':f.sha(self.locks[owner][1]),'origin_hash':{'present':True,'value':h(owner+' current manifest')}}})
        for suffix,raw in zip(('before','after'),self.locks[owner]):a.data['provenance/Package.resolved.'+suffix]=raw
    def setup_phase1(self):
        a=self.a
        for n in ('sidesign-resolve','sidesign-frozen-resolve','sidesign-build'):a.log(n)
        cases=sorted(('deviceInitializationAndFiltering','certificateRequestCSRGeneration','developerPortalSingleton','archiveStoreRoundtrip','archiveDeflateRoundtrip'))
        a.log('sidesign-tests','\n'.join('Test '+n+'() started\nTest '+n+'() passed after 1 second'for n in cases)+'\nTest run with 5 tests passed after 1 second\n')
        a.put('provenance/sidesign-selected-tests.json',{'executed':5,'scope':'sidesign','status':'CAPTURED_FOR_REVIEW','tests':cases})
        hashes={n:v['after']['sha256']for n,v in self.inputs1['owners']['AnisetteKit']['changes'].items()}
        a.put('provenance/native-fork83.json',{'checks':83,'archive_sha256':f.p0.FORK_SHA256,'source_sha256':hashes,'sanitizers':['address','undefined'],'ADI_executed':False,'library_sha256':h('engine')})
        a.log('native-fork83-run','PASS 83 full-production-loader synthetic checks; no ADI executed\n');a.data['logs/native-fork83-run.stderr.log']=b''
        for n in ('native-fork83','constructor-diagnostics'):a.log(n+'-compile',args=['clang++','-fsanitize=address,undefined','-fno-sanitize-recover=all'])
        constructor={'schema':1,'checks':2105,'failures':0,'skipped':0,'case_ids':list(f.admission.REQUIRED_DIAGNOSTIC_CASES),'ADI_executed':False,'device_validation':False}
        a.log('constructor-diagnostics-run',f.dump_bytes(constructor).decode());a.data['logs/constructor-diagnostics-run.stderr.log']=b''
        a.put('provenance/constructor-diagnostics.json',{**constructor,'source_owner':FIXTURE_CANDIDATES['AnisetteKit'],'source_sha256':hashes,'engine_archive_sha256':f.p0.FORK_SHA256,'library_sha256':h('engine'),'sanitizers':['address','undefined']})
        a.put('provenance/anisette-native-boundary.json',{'checks':8,'failures':0,'skipped':0,'case_ids':list(f.BOUNDARY_IDS),'ADI_executed':False,'device_validation':False})
        a.log('anisette-native-boundary','\n'.join(n+' (__main__.MaintainedAnisetteNativeTests.'+n+') ... ok'for n in f.BOUNDARY_IDS)+'\nRan 8 tests in 1.0s\nOK\n')
    def setup_phase2(self):
        for n in ('idevice-build','idevice-xcframework','sidestore-resolve','sidestore-frozen-resolve'):self.b.log(n)
        for n in ('sidestore-build','livecontainer-build'):self.b.log(n,args=['xcodebuild','iphoneos','Release','generic/platform=iOS','CODE_SIGNING_ALLOWED=NO','CODE_SIGNING_REQUIRED=NO','build'])
        self.b.put('provenance/unsigned-products.json',{n:{n:h(n+' binary'),'Info.plist':h(n+' plist')}for n in ('SideStore','LiveContainer')})
        reg=f.load(f.STAGE/'compatibility-registry.json');counts={n:len(f.load(f.STAGE/p['manifest_path'])['sources'])for n,p in reg['owners'].items()}
        self.b.log('p0-contracts',json.dumps({'status':'pass','registry_sha256':f.file_sha(f.STAGE/'compatibility-registry.json'),'source_files':95,'contracts':13,'edges':29,'owners':counts})+'\n')
    def phase2(self):return f.verify_phase2(self.b,self.runner,self.approved,self.approved_sha)
    def build(self):
        # Actual runtime Git-byte validation has dedicated independent fixtures below.
        with patch.object(f,'verify_stage_sources'):
            return f.build_candidate(self.a,self.b,self.runner,self.approved,self.approved_sha,self.repos,self.final)

class FinalizationEvidenceTests(CandidateFixture):
    def test_phase2_all42_exact_ids_and_source_graph(self):
        identity,inputs,suites=self.phase2();self.assertEqual(sum(r['tests']for r in suites.values()),42)
    def test_phase1_native83_constructor2105_boundary8(self):
        identity,inputs,observed=f.verify_phase1(self.a,self.runner,self.inputs2);self.assertEqual(observed['checks'],2105)
    def test_complete_candidate_is_deterministic_and_stays_unapproved(self):
        anchor_before=f.admission.DIAGNOSTIC_REVIEW_SHA256
        a=self.build();b=self.build();self.assertEqual(a,b);files,review,summary=a
        self.assertEqual(f.parse(files['native-receipt.json'])['status'],'CAPTURED_FOR_INDEPENDENT_REVIEW')
        self.assertEqual(review['native_validation']['readiness_scope'],'candidate_for_gated_full_build_review');self.assertFalse(summary['activated'])
        self.assertIsNone(review['native_validation']['review_sha256']);self.assertEqual(f.admission.DIAGNOSTIC_REVIEW_SHA256,anchor_before)
        self.assertEqual(review['files'],{n:f.sha(raw)for n,raw in files.items()})
    def test_candidate_write_is_fresh_and_exact(self):
        files,review,summary=self.build();out=self.root/'candidate';f.write_candidate(out,files,review,summary)
        for n,data in files.items():self.assertEqual((out/'metadata'/n).read_bytes(),data)
        with self.assertRaises(ValueError):f.write_candidate(out,files,review,summary)
    def test_unapproved_input_bytes_rejected(self):
        with self.assertRaises(ValueError):f.verify_phase2(self.b,self.runner,self.approved+b'\n',self.approved_sha)
    def test_input_hash_cannot_substitute_artifact_inputs(self):
        raw=self.approved+b'\n'
        with self.assertRaises(ValueError):f.verify_phase2(self.b,self.runner,raw,f.sha(raw))
    def test_run_attempt_mismatch_rejected(self):
        self.b.run['run_attempt']=2
        with self.assertRaises(ValueError):self.phase2()
    def test_build_failure_rejected(self):
        self.b.data['logs/sidestore-build.exit-code.txt']=b'1\n'
        with self.assertRaises(ValueError):self.phase2()
    def test_wrong_unsigned_build_flags_rejected(self):
        self.b.put('logs/sidestore-build.command.json',{'argv':['xcodebuild','build']})
        with self.assertRaises(ValueError):self.phase2()
    def test_changed_transport_owner_rejected(self):
        value=copy.deepcopy(self.inputs2);value['owners']['idevice']['commit']=c('transport drift');raw=f.dump_bytes(value)
        self.b.put('provenance/approved-inputs.json',value);result=self.b.json('provenance/result.json');result['approved_inputs_sha256']=f.sha(raw);self.b.put('provenance/result.json',result)
        with self.assertRaisesRegex(ValueError,'unchanged-owner'):f.verify_phase2(self.b,self.runner,raw,f.sha(raw))
    def test_missing_unsigned_executable_rejected(self):
        v=self.b.json('provenance/unsigned-products.json');v['SideStore'].pop('SideStore');self.b.put('provenance/unsigned-products.json',v)
        with self.assertRaises(ValueError):self.phase2()
    def test_source_owner_mismatch_rejected(self):
        v=self.b.json('provenance/source-before.json');v['SideStore']['commit']=c('other');self.b.put('provenance/source-before.json',v)
        with self.assertRaises(ValueError):self.phase2()
    def test_missing_recursive_child_rejected(self):
        for name in ('source-before.json','source-after.json'):
            v=self.b.json('provenance/'+name);v['SideStore']['submodules'].pop();self.b.put('provenance/'+name,v)
        with self.assertRaises(ValueError):self.phase2()
    def test_child_tree_mismatch_rejected(self):
        for name in ('source-before.json','source-after.json'):
            v=self.b.json('provenance/'+name);v['SideStore']['submodules'][0]['tree']=c('wrong');self.b.put('provenance/'+name,v)
        with self.assertRaises(ValueError):self.phase2()
    def test_source_drift_after_capture_rejected(self):
        v=self.b.json('provenance/source-after.json');v['AnisetteKit']['tracked_entries']+=1;self.b.put('provenance/source-after.json',v)
        with self.assertRaises(ValueError):self.phase2()
    def test_native_constructor_stderr_rejected(self):
        self.a.data['logs/constructor-diagnostics-run.stderr.log']=b'AddressSanitizer issue'
        with self.assertRaises(ValueError):self.build()
    def test_missing_native_engine_library_hash_rejected(self):
        for path in ('provenance/native-fork83.json','provenance/constructor-diagnostics.json'):
            value=self.a.json(path);value.pop('library_sha256');self.a.put(path,value)
        with self.assertRaises(ValueError):self.build()
    def test_native_missing_fail_fast_flag_rejected(self):
        self.a.put('logs/constructor-diagnostics-compile.command.json',{'argv':['clang++','-fsanitize=address,undefined']})
        with self.assertRaises(ValueError):self.build()
    def test_native_missing_constructor_case_rejected(self):
        v=f.parse(self.a.data['logs/constructor-diagnostics-run.log']);v['case_ids'].pop();self.a.data['logs/constructor-diagnostics-run.log']=f.dump_bytes(v)
        with self.assertRaises(ValueError):self.build()
    def test_boundary_skip_output_rejected(self):
        self.a.data['logs/anisette-native-boundary.log']=self.a.data['logs/anisette-native-boundary.log'].replace(b'... ok',b'... skipped',1)
        with self.assertRaises(ValueError):self.build()
    def test_phase2_prior_native_link_mismatch_rejected(self):
        v=self.b.json('provenance/prior-sidesign-link.json');v['native83_reused_for_exact_unchanged_AK']=False;self.b.put('provenance/prior-sidesign-link.json',v)
        with self.assertRaises(ValueError):self.build()
    def test_missing_final_lock_successor_rejected(self):
        self.final=FIXTURE_CANDIDATES['SideStore']
        with self.assertRaises(ValueError):self.build()
    def test_final_runtime_change_rejected(self):
        self.snapshots[('SideStore',FIXTURE_CANDIDATES['SideStore'],self.final)]['changes']['unexpected.swift']={}
        with self.assertRaises(ValueError):self.build()
    def test_final_lock_bytes_mismatch_rejected(self):
        key=('SideStore',self.final+':'+f.admission.LOCK_FILES['SideStore']);self.git_files[key]+=b'\n'
        with self.assertRaises(ValueError):self.build()
    def test_candidate_write_failure_leaves_no_output(self):
        files,review,summary=self.build();review['files'][next(iter(files))]=h('bad expected write');out=self.root/'candidate'
        with self.assertRaises(ValueError):f.write_candidate(out,files,review,summary)
        self.assertFalse(out.exists())
    def test_output_inside_builder_or_linked_parent_rejected(self):
        with self.assertRaises(ValueError):f.validate_output(ROOT/'new-candidate')
        link=self.root/'linked';link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError):f.validate_output(link/'candidate')


class StageSourceBindingTests(unittest.TestCase):
    """Exercise staged-source validation with explicit synthetic Git bytes."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.stage=self.root/'stage';shutil.copytree(f.STAGE,self.stage)
        prior=ROOT/f.p0.DIRECTORY/'anisette-generated/maintained-source-manifest.json'
        target=self.root/f.p0.DIRECTORY/'anisette-generated/maintained-source-manifest.json'
        target.parent.mkdir(parents=True);shutil.copyfile(prior,target)
        self.registry=f.load(self.stage/'compatibility-registry.json');self.data={};self.mode='100644'
        self.repos={n:self.root/n for n in f.admission.CHANGED_OWNERS}
        self.owners={n:{'commit':FIXTURE_CANDIDATES[n],'tree':c(n+' staged tree')}for n in self.repos}
        for owner in self.repos:
            path=self.registry['owners'][owner]['manifest_path'];manifest=f.load(self.stage/path)
            for row in manifest['sources']:
                raw=('SYNTHETIC ONLY '+owner+'/'+row['path']).encode();self.data[(owner,row['path'])]=raw;row['sha256']=f.sha(raw)
            write(self.stage/path,manifest)
        path='anisette-generated/maintained-source-manifest.json';manifest=f.load(self.stage/path)
        manifest.update(anisettekit_revision=self.owners['AnisetteKit']['commit'],anisettekit_tree=self.owners['AnisetteKit']['tree'])
        for row in manifest['files']:
            key=('AnisetteKit',row['path']);raw=self.data.setdefault(key,('SYNTHETIC ONLY AnisetteKit/'+row['path']).encode());row['sha256']=f.sha(raw)
        write(self.stage/path,manifest);self.reseal()
        for item in (patch.object(f,'STAGE',self.stage),patch.object(f,'ROOT',self.root),patch.object(f,'git',side_effect=self.git)):
            item.start();self.addCleanup(item.stop)
    def reseal(self):
        for pin in self.registry['owners'].values():pin['manifest_sha256']=f.file_sha(self.stage/pin['manifest_path'])
        write(self.stage/'compatibility-registry.json',self.registry)
        self.inputs={'contracts':{'registry_sha256':f.file_sha(self.stage/'compatibility-registry.json')}}
    def git(self,repo,*args):
        if args[0]=='show':return self.data[(repo.name,args[1].split(':',1)[1])]
        if args[0]=='ls-tree':return (self.mode+' blob '+c('synthetic blob')+'\t'+args[-1]+'\0').encode()
        raise AssertionError(args)
    def verify(self):f.verify_stage_sources(self.inputs,self.repos,self.owners)
    def edit_generated(self,edit):
        path=self.stage/'anisette-generated/maintained-source-manifest.json';manifest=f.load(path);edit(manifest);write(path,manifest)
    def test_all_staged_runtime_and_generated_sources_match(self):self.verify()
    def test_actual_git_byte_drift_rejected(self):
        self.data[next(iter(self.data))]+=b'drift'
        with self.assertRaisesRegex(ValueError,'source bytes'):self.verify()
    def test_actual_git_mode_drift_rejected(self):
        self.mode='100755'
        with self.assertRaisesRegex(ValueError,'source mode'):self.verify()
    def test_registry_byte_drift_rejected(self):
        with (self.stage/'compatibility-registry.json').open('ab')as stream:stream.write(b'\n')
        with self.assertRaisesRegex(ValueError,'registry drift'):self.verify()
    def test_owner_manifest_hash_drift_rejected(self):
        with (self.stage/self.registry['owners']['AnisetteKit']['manifest_path']).open('ab')as stream:stream.write(b'\n')
        with self.assertRaisesRegex(ValueError,'source/evidence drift'):self.verify()
    def test_generated_identity_drift_rejected(self):
        self.edit_generated(lambda value:value.update(anisettekit_revision=c('other')))
        with self.assertRaisesRegex(ValueError,'identity'):self.verify()
    def test_missing_constructor_header_rejected(self):
        self.edit_generated(lambda value:value.update(files=[r for r in value['files']if r['path']!='Native/Loader/vm_initialization_diagnostics.h']))
        with self.assertRaisesRegex(ValueError,'inventory'):self.verify()
    def test_missing_inherited_adi_literal_rejected(self):
        self.edit_generated(lambda value:value['required_compiled_evidence']['literals'].pop(0))
        with self.assertRaisesRegex(ValueError,'compiled evidence'):self.verify()
    def test_missing_constructor_literal_rejected(self):
        self.edit_generated(lambda value:value['required_compiled_evidence'].update(literals=[r for r in value['required_compiled_evidence']['literals']if r['value']not in f.admission.REQUIRED_DIAGNOSTIC_LITERALS]))
        with self.assertRaisesRegex(ValueError,'compiled evidence'):self.verify()
    def test_duplicate_generated_source_rejected(self):
        self.edit_generated(lambda value:value['files'].append(copy.deepcopy(value['files'][0])))
        with self.assertRaisesRegex(ValueError,'duplicate'):self.verify()

# Each suite rejection gets its own stable discovered test ID.
def bad_suite(field,value):
    def test(self):
        name='initializer-diagnostics';r=self.b.json('provenance/suites/'+name+'.json');r[field]=value
        self.b.put('provenance/suites/'+name+'.json',r);self.b.log('suite-'+name,f.dump_bytes(r).decode())
        with self.assertRaises(ValueError):self.phase2()
    return test
for field,value in (('tests',4),('failures',1),('errors',1),('skips',1),('expected_failures',1),('unexpected_successes',1),('test_ids',['unexecuted']),('failures',False)):
    name=field+('_boolean'if value is False else'')
    setattr(FinalizationEvidenceTests,'test_suite_reject_'+name,bad_suite(field,value))
if __name__=='__main__':unittest.main()
