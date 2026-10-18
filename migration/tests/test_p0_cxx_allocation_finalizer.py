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
spec=importlib.util.spec_from_file_location('cxx_finalizer',ROOT/'migration/finalize_p0_cxx_allocation.py')
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
# Known source candidates are only fixture values, never a finalizer allowlist.
FIXTURE_CANDIDATES={
 'AnisetteKit':f.admission.AK_COMMIT,
 'LiveContainer':'fb376d0ccd8b845576b529dd36d9940843a55794',
 'SideSign':'b829a21c3955b4084fc3a6f613bad9162a8d43ef',
 'SideStore':'5c1f63f89f6b1d441cc3874928ae88e2e5e88201',
 **{n:f.admission.PARENTS[n]for n in ('minimuxer','idevice','jktcp')}}
def h(v):return hashlib.sha256(('SYNTHETIC ONLY '+v).encode()).hexdigest()
def c(v):return hashlib.sha1(('SYNTHETIC ONLY '+v).encode()).hexdigest()
def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(f.dump_bytes(v))

class ArtifactIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.files={'provenance/example.json':b'{"synthetic":true}\n'}
        self.run={'id':123,'run_attempt':1,'head_sha':c('runner'),'status':'completed','conclusion':'success','repository':{'full_name':f.REPOSITORY},
                  'path':f.WORKFLOW,'event':'push','head_branch':'validation/p0-cxx-allocation','html_url':'https://github.com/'+f.REPOSITORY+'/actions/runs/123'}
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
        for phase in ('phase1','phase2','allocation'):
            args+=['--'+phase+'-zip',str(self.root/'synthetic.zip'),'--'+phase+'-run-json',str(self.root/'run.json'),'--'+phase+'-artifact-json',str(self.root/'artifact.json')]
        args+=['--runner-repo',str(self.root),'--approved-inputs',str(self.root/'run.json'),'--approved-inputs-sha256',h('invalid'),
               '--allocation-approved-inputs',str(self.root/'run.json'),'--allocation-approved-inputs-sha256',h('invalid'),'--historical-directory',str(self.root),'--owner','AnisetteKit='+str(self.root),'--final-sidestore-commit',c('unpublished'),'--output',str(out)]
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

class StageSourceBindingTests(unittest.TestCase):
    """Exercise staged-source validation with explicit synthetic Git bytes."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.stage=self.root/'stage';shutil.copytree(f.STAGE,self.stage)
        prior=ROOT/f.historical.DIRECTORY/'anisette-generated/maintained-source-manifest.json'
        target=self.root/f.historical.DIRECTORY/'anisette-generated/maintained-source-manifest.json'
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
    def test_linked_temp_alias_is_resolved_without_relaxing_source_checks(self):
        alias=self.root/'temp-alias';alias.symlink_to(self.root,target_is_directory=True)
        fixture=StageSourceBindingTests('test_all_staged_runtime_and_generated_sources_match');self.addCleanup(fixture.doCleanups)
        with patch.object(tempfile,'tempdir',str(alias)):fixture.setUp()
        self.assertEqual(fixture.root,Path(fixture.tmp.name).resolve())
        self.assertNotEqual(fixture.root,Path(fixture.tmp.name))
        fixture.verify()
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
        self.edit_generated(lambda value:value.update(files=[r for r in value['files']if r['path']!='Native/Loader/cxx_allocation_runtime.h']))
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


class LinuxAllocationCommandTests(unittest.TestCase):
    def setUp(self):
        self.files={};self.helper={'results':{}}
        for variant,flags in [('normal',[]),('optimized',['-O2','-DNDEBUG']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer'])]:
            common=['c++','-std=c++17','-ffunction-sections','-fdata-sections',*flags,'-I','/work/sources/AnisetteKit/Native/Loader','-I','/work/sources/AnisetteKit/Native','-I','/work/sources/AnisetteKit/Native/include','-I','/work/results/unicorn-headers']
            binary='/evidence/allocation-units/'+variant;obj=binary+'.o'
            loader='/work/sources/AnisetteKit/Native/Loader/elf_loader_emulator.cpp';fixture='/work/sources/AnisetteKit/.ci/native-tests/fixtures/'
            self.helper['results'][variant]={'allocator':{'checks':32,'failures':0,'guest_execution':False},'hooks':{'checks':51,'failures':0,'guest_execution':False}}
            for kind,suffix,commands in [('allocator','',[common+['-c',loader,'-o',obj],common+['-Wl,--gc-sections',fixture+'cxx_allocation_unit.cpp',obj,'-o',binary],[binary]]),('hooks','-hooks',[common+[fixture+'cxx_allocation_hook_unit.cpp','-o',binary+'-hooks'],[binary+'-hooks']])]:
                rows=[{'command':argv,'exit_code':0,'stderr':'','stdout':''}for argv in commands]
                rows[-1]['stdout']=json.dumps(self.helper['results'][variant][kind])
                self.files['allocation-units/'+variant+suffix+'.json']=rows
        prefix=['git','--no-replace-objects','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false']
        source='/work/sources/AnisetteKit';repository='https://github.com/NRG-Wardog/AnisetteKit.git'
        commands={
            'fetch-anisettekit':prefix+['clone','--no-checkout',repository,source],
            'fetch-exact-anisettekit':prefix+['-C',source,'fetch','--no-tags','--no-recurse-submodules','origin',f.admission.AK_COMMIT],
            'checkout-anisettekit':prefix+['-C',source,'checkout','--detach',f.admission.AK_COMMIT],
            'disable-push-anisettekit':prefix+['-C',source,'config','remote.origin.pushurl','disabled://p0-capture-readonly'],
            'unicorn-header-download':['curl','--fail','--location','--proto','=https','--proto-redir','=https','--output','/work/results/Unicorn.xcframework.zip',f.ALLOCATION_FORK_URL],
            'allocation-units':['sudo','-n','unshare','--net','--','/usr/bin/python3','-B',source+'/.ci/native-tests/test_cxx_allocation.py','--unicorn-include','/work/results/unicorn-headers','--output','/evidence/allocation-units'],
            'python-version':['/usr/bin/python3','--version'],
        }
        self.context={'logs/'+label+'.command.json':{'argv':argv,'cwd':None} for label,argv in commands.items()}
        self.context['provenance/unicorn-headers.json']={'url':f.ALLOCATION_FORK_URL,'archive_sha256':f.ALLOCATION_FORK_SHA256,
            'headers_sha256':copy.deepcopy(f.ALLOCATION_HEADER_SHA256),'engine_linked':False,'engine_executed':False}
        self.artifact=type('SyntheticArtifact',(),{'json':lambda _,name:self.files[name] if name in self.files else self.context[name]})()
    def check(self):f.verify_allocation_commands(self.artifact,self.helper)
    def test_exact_host_only_compile_run_graph(self):self.check()
    def test_guest_engine_link_rejected(self):
        self.files['allocation-units/normal.json'][1]['command'].insert(1,'-lunicorn')
        with self.assertRaises(ValueError):self.check()
    def test_missing_sanitizer_rejected(self):
        self.files['allocation-units/sanitized.json'][0]['command'].remove('-fsanitize=address,undefined')
        with self.assertRaises(ValueError):self.check()
    def test_wrong_actual_loader_rejected(self):
        self.files['allocation-units/normal.json'][0]['command'][-3]='/tmp/mock.cpp'
        with self.assertRaises(ValueError):self.check()
    def test_missing_raw_command_rejected(self):
        self.files['allocation-units/normal-hooks.json'].pop()
        with self.assertRaises(ValueError):self.check()
    def test_stderr_diagnostics_rejected(self):
        self.files['allocation-units/sanitized-hooks.json'][1]['stderr']='AddressSanitizer error'
        with self.assertRaises(ValueError):self.check()
    def test_boolean_exit_status_rejected(self):
        self.files['allocation-units/normal.json'][0]['exit_code']=False
        with self.assertRaises(ValueError):self.check()
    def test_output_from_other_binary_rejected(self):
        self.files['allocation-units/optimized-hooks.json'][1]['command']=['/bin/true']
        with self.assertRaises(ValueError):self.check()
    def replace_raw_argument(self,before,after):
        for rows in self.files.values():
            for row in rows:row['command']=[arg.replace(before,after)for arg in row['command']]
    def test_unverified_fourth_include_root_rejected(self):
        self.replace_raw_argument('/work/results/unicorn-headers','/unverified-headers')
        with self.assertRaisesRegex(ValueError,'include roots'):self.check()
    def test_unverified_source_prefix_rejected(self):
        self.replace_raw_argument('/work/sources/AnisetteKit','/unverified-source-copy/AnisetteKit')
        with self.assertRaisesRegex(ValueError,'acquired checkout'):self.check()
    def test_unverified_output_root_rejected(self):
        self.replace_raw_argument('/evidence/allocation-units','/unverified-output')
        with self.assertRaisesRegex(ValueError,'captured driver destination'):self.check()
    def test_driver_and_raw_include_substitution_cannot_change_download_context(self):
        self.replace_raw_argument('/work/results/unicorn-headers','/unverified-headers')
        row=self.context['logs/allocation-units.command.json'];row['argv']=[arg.replace('/work/results/unicorn-headers','/unverified-headers')for arg in row['argv']]
        with self.assertRaisesRegex(ValueError,'invocation binding'):self.check()
    def test_driver_and_raw_source_substitution_cannot_change_acquisition_context(self):
        self.replace_raw_argument('/work/sources/AnisetteKit','/unverified-source-copy/AnisetteKit')
        row=self.context['logs/allocation-units.command.json'];row['argv']=[arg.replace('/work/sources/AnisetteKit','/unverified-source-copy/AnisetteKit')for arg in row['argv']]
        with self.assertRaisesRegex(ValueError,'invocation binding'):self.check()
    def test_wrong_checkout_revision_rejected(self):
        self.context['logs/checkout-anisettekit.command.json']['argv'][-1]=c('other source')
        with self.assertRaisesRegex(ValueError,'checkout command'):self.check()
    def test_source_clone_target_mismatch_rejected(self):
        self.context['logs/fetch-anisettekit.command.json']['argv'][-1]='/other/sources/AnisetteKit'
        with self.assertRaisesRegex(ValueError,'clone target'):self.check()
    def test_download_destination_mismatch_rejected(self):
        self.context['logs/unicorn-header-download.command.json']['argv'][-2]='/other/archive.zip'
        with self.assertRaisesRegex(ValueError,'download destination'):self.check()
    def test_noncanonical_capture_paths_rejected(self):
        for value in ('relative/path','/root/../other','//root/source','/root//source','/root/./source','/root/source/'):
            with self.subTest(value=value),self.assertRaises(ValueError):f.capture_absolute_path(value)
    def test_arbitrary_well_formed_header_hash_rejected(self):
        self.context['provenance/unicorn-headers.json']['headers_sha256']['unicorn.h']='a'*64
        with self.assertRaisesRegex(ValueError,'exact engine-header'):self.check()
    def test_extra_header_rejected(self):
        self.context['provenance/unicorn-headers.json']['headers_sha256']['other.h']='b'*64
        with self.assertRaisesRegex(ValueError,'exact engine-header'):self.check()
    def test_missing_unused_header_rejected(self):
        self.context['provenance/unicorn-headers.json']['headers_sha256'].pop('m68k.h')
        with self.assertRaisesRegex(ValueError,'exact engine-header'):self.check()
    def test_header_slice_hash_swap_rejected(self):
        headers=self.context['provenance/unicorn-headers.json']['headers_sha256']
        headers['arm.h'],headers['arm64.h']=headers['arm64.h'],headers['arm.h']
        with self.assertRaisesRegex(ValueError,'exact engine-header'):self.check()
    def test_interpreter_substitution_rejected(self):
        self.context['logs/allocation-units.command.json']['argv'][5]='/unverified-python'
        with self.assertRaisesRegex(ValueError,'interpreter'):self.check()
    def test_exact_linux_settlement_inventory(self):
        self.assertEqual(f.required_command_names({'phase':'allocation'}),{'compiler-version','python-version','network-sandbox','fetch-anisettekit','fetch-exact-anisettekit','checkout-anisettekit','disable-push-anisettekit','unicorn-header-download','allocation-units'})

if __name__=='__main__':unittest.main()
