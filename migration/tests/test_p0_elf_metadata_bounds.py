"""Offline schema/negative tests. Synthetic data never grants runtime readiness."""
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'migration'),str(ROOT)]
import p0_elf_metadata_bounds as admission
import p0_constructor_diagnostics as historical
import p0_maintained_sources as native
import maintained_sources
import maintained_package_evidence
import finalize_p0_elf_metadata_bounds as finalizer


def helper():
    return {'schema':1,'source_sha256':copy.deepcopy(admission.HELPER_SOURCES),
        'results':{v:copy.deepcopy(admission.METADATA_EXPECTED) for v in ('normal','optimized','sanitized')},
        'sanitizers':['address','undefined'],'leak_sanitizer_enabled':False,
        'guest_execution':False,'device_validation':False,'proprietary_input':False}


class BytesArtifact:
    """Explicit nonproduction fixture, only for validator rejection testing."""
    def __init__(self): self.data={};self.index={}
    def put(self,name,value):self.data[name]=(json.dumps(value)+'\n').encode();self.index[name]='f'*64
    def raw(self,name):
        if name not in self.data:raise ValueError('missing test fixture '+name)
        return self.data[name]
    def json(self,name):return native.parse(self.raw(name))
    def command(self,name):return finalizer.Artifact.command(self,name)


def settled_command(a,name):
    a.put('logs/'+name+'.command.json',{'argv':['SYNTHETIC_COMMAND'],'cwd':None})
    state={'pgid':123,'quiescent':True,'pipesJoined':True,'signals':[],'identityRetainedForSignals':False,'reapedBeforeSignals':True,
        'postReapPolicy':'read-only-group-disappearance','errors':[],'leaderReaped':True}
    a.put('logs/'+name+'.settlement.json',{'ownedProcessGroup':state,'returncode':0,'terminationReturncode':0,'timeout_seconds':7200})
    groups=a.json('provenance/owned-command-groups.json') if 'provenance/owned-command-groups.json' in a.data else []
    groups.append({'label':name,**state});a.put('provenance/owned-command-groups.json',groups)
    if 'logs/'+name+'.log' not in a.data:a.data['logs/'+name+'.log']=b''
    a.data['logs/'+name+'.exit-code.txt']=b'0\n'
    a.index.update({key:'f'*64 for key in a.data})


def helper_artifact():
    a=BytesArtifact();value=helper();a.put('metadata-units/result.json',value)
    a.put('provenance/elf-metadata-bounds.json',dict(value,status='CAPTURED_FOR_REVIEW',source_owner=admission.AK_COMMIT,source_tree=admission.AK_TREE))
    for v,flags in (('normal',[]),('optimized',['-O2','-DNDEBUG']),('sanitized',['-O1','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer'])):
        a.put('metadata-units/'+v+'-compile.command.json',['/usr/bin/c++','-std=c++17','-Wall','-Wextra','-Werror',*flags,'/fixture/.ci/native-tests/fixtures/elf_metadata_bounds_unit.cpp','-o','/fixture/'+v])
        a.put('metadata-units/'+v+'-run.command.json',['/fixture/'+v]);a.put('metadata-units/'+v+'-run.stdout',admission.METADATA_EXPECTED)
        for step in ('compile','run'):
            a.data['metadata-units/'+v+'-'+step+'.exit-code.txt']=b'0\n';a.data['metadata-units/'+v+'-'+step+'.stderr']=b''
    a.data['logs/elf-metadata-units.exit-code.txt']=b'0\n';a.put('logs/elf-metadata-units.log',{'variants':3,'checks_per_variant':713,'failures':0,'skipped':0})
    settled_command(a,'elf-metadata-units')
    return a


class AdmissionTests(unittest.TestCase):
    def test_pending_review_fails_before_reading_caller_metadata(self):
        with patch.object(admission,'REVIEW_SHA256',None), patch.object(admission,'read',side_effect=AssertionError('must not read')), patch.object(historical,'approval',side_effect=AssertionError('must not inherit yet')):
            with self.assertRaisesRegex(ValueError,'pending'):admission.approval(ROOT)
    def test_pending_review_rejects_all_entrypoints(self):
        pins={'source_basis':admission.BASIS}
        functions=[lambda:admission.load_pins(pins,ROOT,maintained_sources),lambda:admission.contract_basis(pins,ROOT),
            lambda:admission.contract_files(pins,ROOT),lambda:admission.anisette_manifest(pins,ROOT),
            lambda:admission.verify_all(ROOT,pins,None,False,maintained_sources),lambda:admission.acquire(ROOT,pins,maintained_sources),
            lambda:maintained_sources.contract_basis(pins),lambda:maintained_sources.expected_anisette_evidence(pins),
            lambda:maintained_package_evidence.contract_files(pins)]
        for action in functions:
            with self.subTest(action=action),patch.object(admission,'REVIEW_SHA256',None),self.assertRaisesRegex(ValueError,'pending'):action()
    def test_historical_gates_remain_usable_only_for_old_basis(self):
        _,review,_=historical.approval(ROOT)
        pins=admission.base_pins(ROOT)
        self.assertEqual(historical.validate_pins(pins,review),pins)
        pins['source_basis']=admission.BASIS
        with self.assertRaises(ValueError):historical.validate_pins(pins,review)
        with self.assertRaises(ValueError):native.load_pins(pins,ROOT,maintained_sources)
    def test_valid_synthetic_helper_schema(self):self.assertEqual(admission.validate_helper(helper()),helper())
    def test_helper_rejects_partial_variants(self):
        v=helper();del v['results']['sanitized']
        with self.assertRaises(ValueError):admission.validate_helper(v)
    def test_helper_rejects_712_or_boolean_counts(self):
        for count in (712,714,True,713.0,'713'):
            v=helper();v['results']['normal']['checks']=count
            with self.subTest(count=count),self.assertRaises(ValueError):admission.validate_helper(v)
    def test_helper_rejects_failure_or_skip_fields(self):
        for field,val in [('failures',1),('skipped',0)]:
            v=helper();v['results']['optimized'][field]=val
            with self.subTest(field=field),self.assertRaises(ValueError):admission.validate_helper(v)
    def test_helper_rejects_wrong_actual_sources(self):
        v=helper();v['source_sha256']['Native/Loader/elf_metadata_bounds.h']='a'*64
        with self.assertRaises(ValueError):admission.validate_helper(v)
    def test_helper_rejects_unsafe_execution_claims(self):
        for key in ('guest_execution','device_validation','proprietary_input'):
            v=helper();v[key]=True
            with self.subTest(key=key),self.assertRaises(ValueError):admission.validate_helper(v)
    def test_helper_rejects_sanitizer_change(self):
        for field,val in [('sanitizers',['undefined']),('leak_sanitizer_enabled',True)]:
            v=helper();v[field]=val
            with self.subTest(field=field),self.assertRaises(ValueError):admission.validate_helper(v)
    def test_new_basis_uses_sha256_source_records(self):
        self.assertEqual(maintained_sources.anisette_source_hashes({'source_basis':admission.BASIS,'files':[{'path':'x','sha256':'a'*64}]}),{'x':'a'*64})
    def test_reviewed_manifest_exact_bytes(self):
        self.assertEqual(native.sha((ROOT/admission.DIRECTORY/'reviewed-ak-manifest.json').read_bytes()),admission.MANIFEST_SHA256)
    def test_staged_metadata_has_no_approval_or_success(self):
        if admission.REVIEW_SHA256 is not None:
            admission.approval(ROOT)
            return
        for name in ('reviewed-approval.json','native-receipt.json','SideSign-resolver.json','SideStore-resolver.json'):
            self.assertFalse((ROOT/admission.DIRECTORY/name).exists())


class ActualManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.path='anisette-generated/maintained-source-manifest.json'
        for name,source in ((self.path,ROOT/admission.DIRECTORY/self.path),('inherited/p0-constructor-diagnostics-v1/'+self.path,ROOT/historical.DIRECTORY/self.path)):
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        self.review={'owners':{'AnisetteKit':{'commit':admission.AK_COMMIT,'tree':admission.AK_TREE}}}
    def manifest(self):
        # Isolate manifest validation from the intentionally unavailable CI approval.
        with patch.object(admission,'approval',return_value=(self.root,self.review,{})),patch.object(admission,'validate_pins'):
            return admission.anisette_manifest({},ROOT)
    def test_actual_current_source_inventory_is_accepted(self):
        manifest=self.manifest();configured=os.environ.get('P0_BOUNDS_MANIFEST_SOURCE') or os.environ.get('P0_BOUNDS_ANISETTE_TEST_SOURCE')
        self.assertTrue(configured,'P0_BOUNDS_ANISETTE_TEST_SOURCE must name the exact current AK checkout')
        source=Path(configured)
        self.assertEqual(maintained_sources.git(source,'rev-parse','HEAD').decode().strip(),admission.AK_COMMIT)
        self.assertEqual(maintained_sources.git(source,'rev-parse','HEAD^{tree}').decode().strip(),admission.AK_TREE)
        for row in manifest['files']:
            self.assertEqual(native.sha((source/row['path']).read_bytes()),row['sha256'],row['path'])
        self.assertIn('Native/Loader/elf_metadata_bounds.h',{row['path']for row in manifest['files']})
    def test_missing_helper_inventory_is_rejected(self):
        path=self.root/self.path;value=native.parse(path.read_bytes())
        value['files']=[row for row in value['files'] if row['path']!='Native/Loader/elf_metadata_bounds.h']
        path.write_text(json.dumps(value)+'\n')
        with self.assertRaisesRegex(ValueError,'sources missing'):self.manifest()


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.owners={o:{'commit':c,'tree':'b'*40} for o,c in admission.PARENTS.items()}
        self.owners['AnisetteKit']={'commit':admission.AK_COMMIT,'tree':admission.AK_TREE}
        self.transitions={o:{'changes':{}} for o in admission.PARENTS}
        self.transitions['AnisetteKit']['changes']={p:{'before':None,'after':{'mode':r['mode'],'type':'blob','git_object':r['git_blob'],'sha256':r['sha256']}} for p,r in admission.AK_FILES.items()}
        r=admission.FIXTURE_FILE
        self.transitions['AnisetteKit']['changes'][r['path']]={'before':{'mode':r['mode'],'type':'blob','git_object':r['before_git_blob'],'sha256':r['before_sha256']},'after':{'mode':r['mode'],'type':'blob','git_object':r['git_blob'],'sha256':r['sha256']}}
        self.transitions['SideSign']['changes']={'Package.swift':{},'Package.resolved':{}}
        self.transitions['SideStore']['changes']={'Dependencies/SideSign':{'before':{'mode':'160000','type':'commit','git_object':admission.PARENTS['SideSign']},'after':{'mode':'160000','type':'commit','git_object':self.owners['SideSign']['commit']}},admission.LOCK_FILES['SideStore']:{}}
    def check(self):admission.validate_delta(self.transitions,{'owners':self.owners})
    def test_exact_synthetic_delta(self):self.check()
    def test_extra_runtime_file_rejected(self):
        self.transitions['AnisetteKit']['changes']['Native/extra.cpp']={}
        with self.assertRaises(ValueError):self.check()
    def test_changed_reviewed_file_rejected(self):
        self.transitions['AnisetteKit']['changes']['Native/Loader/elf_metadata_bounds.h']['after']['sha256']='a'*64
        with self.assertRaises(ValueError):self.check()
    def test_lock_not_optional(self):
        del self.transitions['SideSign']['changes']['Package.resolved']
        with self.assertRaises(ValueError):self.check()
    def test_host_must_be_unchanged(self):
        self.owners['LiveContainer']['commit']='a'*40
        with self.assertRaises(ValueError):self.check()
    def test_exact_manifest_revision_change(self):
        old=('revision: "'+admission.PARENTS['AnisetteKit']+'"').encode()
        new=old.replace(admission.PARENTS['AnisetteKit'].encode(),admission.AK_COMMIT.encode())
        admission.verify_manifest_pin(old,new)
        with self.assertRaises(ValueError):admission.verify_manifest_pin(old,new+b'extra configuration')
    def test_gitlink_must_match_final_sidesign_owner(self):
        self.transitions['SideStore']['changes']['Dependencies/SideSign']['after']['git_object']='f'*40
        with self.assertRaises(ValueError):self.check()
    def test_p1_or_p2_change_rejected(self):
        self.transitions['SideStore']['changes']['SideStore/P1.swift']={}
        with self.assertRaises(ValueError):self.check()


class FinalizerTests(unittest.TestCase):
    def test_synthetic_helper_commands_match(self):finalizer.verify_helper_artifact(helper_artifact())
    def test_raw_helper_result_must_match(self):
        a=helper_artifact();v=copy.deepcopy(admission.METADATA_EXPECTED);v['checks']=83;a.put('metadata-units/normal-run.stdout',v)
        with self.assertRaises(ValueError):finalizer.verify_helper_artifact(a)
    def test_helper_compile_flags_required(self):
        a=helper_artifact();a.put('metadata-units/sanitized-compile.command.json',['c++'])
        with self.assertRaises(ValueError):finalizer.verify_helper_artifact(a)
    def test_helper_run_must_be_compiled_executable(self):
        a=helper_artifact();a.put('metadata-units/normal-run.command.json',['/another/binary'])
        with self.assertRaises(ValueError):finalizer.verify_helper_artifact(a)
    def test_helper_stderr_fails(self):
        a=helper_artifact();a.data['metadata-units/sanitized-run.stderr']=b'UBSan failure'
        with self.assertRaises(ValueError):finalizer.verify_helper_artifact(a)
    def test_guest_artifact_not_fresh_helper_evidence(self):
        for name in ('logs/native-fork83-run.log','logs/constructor-diagnostics-run.log','provenance/constructor-diagnostics.json'):
            a=helper_artifact();a.put(name,{})
            with self.subTest(name=name),self.assertRaises(ValueError):finalizer.verify_helper_artifact(a)
    def test_helper_aggregate_skips_fail(self):
        a=helper_artifact();a.put('logs/elf-metadata-units.log',{'variants':3,'checks_per_variant':713,'failures':0,'skipped':1})
        with self.assertRaises(ValueError):finalizer.verify_helper_artifact(a)
    def test_complete_definition_handles_comments_and_strings(self):
        code=b'prefix\nint f() { const char *s="}"; /* } */ // }\n if (true) { return 1; } return 0; }\nnext'
        offset,body=finalizer.complete_definition(code,'int f(')
        self.assertEqual(offset,7);self.assertTrue(body.endswith(b'return 0; }'));self.assertNotIn(b'next',body)
    def test_duplicate_signature_rejected(self):
        with self.assertRaises(ValueError):finalizer.complete_definition(b'int f() {} int f() {}','int f(')
    def test_truncated_definition_rejected(self):
        with self.assertRaises(ValueError):finalizer.complete_definition(b'int f() { return 0;','int f(')
    def test_output_cannot_mutate_builder(self):
        with self.assertRaises(ValueError):finalizer.validate_output(ROOT/'should-not-exist')
    def test_candidate_writer_never_creates_reviewed_approval(self):
        with tempfile.TemporaryDirectory() as t:
            dest=Path(t).resolve()/'candidate';files={'example.txt':b'synthetic'};review={'files':{'example.txt':native.sha(b'synthetic')}}
            finalizer.write_candidate(dest,files,review,{'activated':False})
            self.assertTrue((dest/'candidate-review.json').exists());self.assertFalse((dest/'metadata/reviewed-approval.json').exists())
    def test_quiescence_cannot_be_asserted_without_groups(self):
        a=BytesArtifact();a.put('provenance/result.json',{'writers_quiescent':True,'retention_errors':[]});a.put('provenance/approved-inputs.json',{'phase':'sidesign','owners':{'AnisetteKit':{},'SideSign':{}},'submodules':{}});a.put('provenance/owned-command-groups.json',[])
        with self.assertRaises(ValueError):finalizer.verify_quiescence(a)
    def test_quiescence_unknown_or_false_rejected(self):
        for state in (None,False):
            a=BytesArtifact();a.put('provenance/result.json',{'writers_quiescent':state,'retention_errors':[]})
            with self.subTest(state=state),self.assertRaises(ValueError):finalizer.verify_quiescence(a)
    def test_command_requires_command_and_settlement_and_owned_group(self):
        for missing in ('command','settlement','group'):
            a=BytesArtifact();settled_command(a,'sidesign-build')
            if missing=='group':a.put('provenance/owned-command-groups.json',[])
            else:
                key='logs/sidesign-build.'+missing+'.json';del a.data[key];del a.index[key]
            with self.subTest(missing=missing),self.assertRaises(ValueError):a.command('sidesign-build')
    def test_phase_inventory_cannot_omit_whole_required_command_proof(self):
        inputs={'phase':'sidesign','owners':{'AnisetteKit':{},'SideSign':{}},'submodules':{}}
        a=BytesArtifact();a.put('provenance/result.json',{'writers_quiescent':True,'retention_errors':[]});a.put('provenance/approved-inputs.json',inputs)
        for name in finalizer.required_command_names(inputs):settled_command(a,name)
        finalizer.verify_quiescence(a)
        for suffix in ('command.json','settlement.json'):
            key='logs/sidesign-build.'+suffix;del a.data[key];del a.index[key]
        a.put('provenance/owned-command-groups.json',[row for row in a.json('provenance/owned-command-groups.json') if row['label']!='sidesign-build'])
        with self.assertRaisesRegex(ValueError,'phase-specific'):finalizer.verify_quiescence(a)
        with self.assertRaises(ValueError):a.command('sidesign-build')
    def test_missing_phase2_suite_command_proof_is_rejected(self):
        inputs={'phase':'sidestore','owners':{name:{} for name in admission.PARENTS},'submodules':{}}
        required=finalizer.required_command_names(inputs)
        self.assertIn('suite-initializer-diagnostics',required);self.assertIn('livecontainer-build',required)
        a=BytesArtifact();a.put('provenance/result.json',{'writers_quiescent':True,'retention_errors':[]});a.put('provenance/approved-inputs.json',inputs)
        for name in required-{'suite-initializer-diagnostics'}:settled_command(a,name)
        with self.assertRaisesRegex(ValueError,'phase-specific'):finalizer.verify_quiescence(a)
    def test_forged_historical_artifact_not_accepted(self):
        root,review,_=historical.approval(ROOT)
        with self.assertRaises(ValueError):finalizer.verify_historical_artifacts([],root,review)


if __name__=='__main__':unittest.main()
