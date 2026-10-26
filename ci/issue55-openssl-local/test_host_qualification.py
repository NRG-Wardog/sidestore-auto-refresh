import copy
import ast
import os
import plistlib
import types
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC=importlib.util.spec_from_file_location('host_local',Path(__file__).with_name('qualify_host.py'))
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)

class PairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.paths={x:self.root/x for x in ('A','B')}
        for path in self.paths.values():path.write_bytes(b'identical')
        self.digest=m.sha(self.paths['A']); self.version='OpenSSL 3.5.9'
    def text(self, alias=False):
        rows=[]
        for i,key in enumerate(('A','B')):
            path=self.paths['A' if alias else key]
            rows += [f'HANDLE\t{i}\t{path}',f'VERSION\t{i}\t{self.version}']
            rows += [f'SYMBOL\t{i}\t{s}\t{path}' for s in ('PKCS12_parse','EVP_DigestSignInit','OSSL_PROVIDER_load')]
            rows += [f'PROVIDER\t{i}\t{s}\t{path}' for s in ('default','legacy')]
        return '\n'.join(rows+['GLOBAL\tNONE','PASS cold-process-two-library-probe'])
    def parse(self,text): return m.parse_pair(text,self.paths,self.digest,self.version)
    def test_distinct(self):self.assertEqual(self.parse(self.text())['HANDLE:1'],'B')
    def test_observed_alias(self):self.assertEqual(self.parse(self.text(True))['HANDLE:1'],'A')
    def test_missing_duplicate_unknown(self):
        text=self.text()
        for changed in ('\n'.join(text.splitlines()[1:]),text+'\n'+text.splitlines()[0],text+'\nUNKNOWN\t0\tbad'):
            with self.assertRaises(ValueError):self.parse(changed)
    def test_changed_bytes(self):
        self.paths['B'].write_bytes(b'changed')
        with self.assertRaises(ValueError):self.parse(self.text())
    def test_wrong_path_version(self):
        for changed in (self.text().replace(str(self.paths['B']),str(self.root/'other')),self.text().replace(self.version,'other')):
            with self.assertRaises(ValueError):self.parse(changed)
    def test_global(self):
        self.assertEqual(self.parse(self.text().replace('GLOBAL\tNONE','GLOBAL\t'+str(self.paths['A'])+'\t'+self.version))['GLOBAL'],'A')
    def test_conflicting_global(self):
        with self.assertRaises(ValueError):self.parse(self.text()+'\nGLOBAL\tNONE')

class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.identity={'workflow_commit':'a'*40,'run_id':'123','run_attempt':'1'}
        self.old='b'*64;self.new='c'*64;self.unsigned='d'*64
        source=m.read(m.ROOT/'ci/issue53-lts/source.json')
        files={'baseline/lts-qualification.json':{'status':'PASS_MACOS_SYNTHETIC_ONLY','failures':[],'library_sha256':self.old,'evidence_sha256':{}},
          'transformed/crypto.json':{'status':'PASS_SYNTHETIC_ONLY','library_sha256':self.new,'builtin_providers_only':True,'provider_dispatch_images_bound':True,'version':source['version_text'],'loaded_image':'/owned/runtime/OpenSSL'},
          'transformed/local-host-zsign.json':{'status':'PASS_SYNTHETIC_UNTRUSTED_CHAIN','library_sha256':self.new,'cases':['Apple-Security-CMS-signature-and-tamper'],'version':source['version_text'],'loaded_image':'/owned/runtime/OpenSSL'},
          'transformed/coload.json':{'status':'PASS_MACOS_DIAGNOSTIC_ONLY','errors':[],'libraries':{'host':self.new}},
          'transformed/paired-identical.json':{'status':'PASS_BASELINE_EQUIVALENT_BINDINGS','matrices':{k:{x:{} for x in ('local-AB','local-BA','global-AB','global-BA')} for k in ('original','transformed')}},
          'transform.json':{'original_sha256':self.old,'unsigned_sha256':self.unsigned,'runtime_sha256':self.new,'dwarf_sha256':'e'*64,'original_framework_inventory':{'Versions/A/OpenSSL':{'kind':'file','mode':493,'sha256':self.old}},'transformed_framework_inventory':{'Versions/A/OpenSSL':{'kind':'file','mode':493,'sha256':self.new}}},
          'build-receipt.json':{'source':source,'optimization':'Os','recipe_sha256':m.sources()['ci/issue53-lts/build.py'],'library_sha256':self.old,'tools_sha256':{'/trusted/clang':'e'*64},'generated_inventory':{'OpenSSL.framework.dSYM/Contents/Resources/DWARF/OpenSSL':'e'*64,'OpenSSL.framework.dSYM/Contents/Info.plist':'f'*64,'OpenSSL.framework/Versions/A/OpenSSL':self.old}}}
        (self.root/'framework-Info.plist').write_bytes(b'synthetic info')
        (self.root/'framework-CodeResources.plist').write_bytes(b'synthetic resources')
        seal=m.seal_module();stage=files['transform.json']
        info_sha=m.sha(self.root/'framework-Info.plist');resources_sha=m.sha(self.root/'framework-CodeResources.plist')
        stage['original_framework_inventory'][seal.INFO]={'kind':'file','mode':420,'sha256':info_sha}
        stage['transformed_framework_inventory'][seal.INFO]={'kind':'file','mode':420,'sha256':info_sha}
        stage['transformed_framework_inventory'][seal.SEAL_DIR]={'kind':'directory','mode':493}
        stage['transformed_framework_inventory'][seal.SEAL_FILE]={'kind':'file','mode':420,'sha256':resources_sha}
        stage['framework_seal']={'schema':'host-framework-resource-seal-v1','ios_policy_changed':False,'only_original_binary_and_exact_seal_changed':True,'signature':{'binary_sha256':self.new,'special_slots_verified':[1,2,3],'info_plist_sha256':info_sha,'code_resources_sha256':resources_sha}}
        rows={'GLOBAL':None}
        for i in ('0','1'):
            rows.update({'HANDLE:'+i:'A','VERSION:'+i:source['version_text']})
            rows.update({'SYMBOL:'+i+':'+k:'A' for k in ('PKCS12_parse','EVP_DigestSignInit','OSSL_PROVIDER_load')})
            rows.update({'PROVIDER:'+i+':'+k:'A' for k in ('default','legacy')})
        for cases in files['transformed/paired-identical.json']['matrices'].values():
            for case in cases:cases[case]={**rows,'GLOBAL':'A' if case.startswith('global') else None}
        for name,content in {'build.log':'/trusted/clang -Os -DSTATIC_LEGACY -c providers/defltprov.c\n/trusted/clang -Os -DSTATIC_LEGACY -c providers/legacyprov.c\n','configdata.txt':'CFLAGS = -Os\n','headers.json':'{}'}.items():
            (self.root/name).write_text(content)
        files['build-receipt.json'].update(configuration_sha256=m.sha(self.root/'configdata.txt'),header_inventory_sha256=m.sha(self.root/'headers.json'))
        for name,value in files.items():
            p=self.root/name;p.parent.mkdir(exist_ok=True,parents=True);m.write(p,value)
        self.receipt={'schema':'openssl-true-local-host-qualification-v1','status':'PASS_MACOS_SYNTHETIC_ONLY','qualification_identity':self.identity,'native_comparison_identity':m.NATIVE,'source':source,'optimization':'Os','source_sha256':m.sources(),'actual_compile_evidence':m.compile_evidence(self.root,files['build-receipt.json']),'runtime_library_path':'/owned/runtime/OpenSSL','statuses':m.STATUS,'device_executed':False,'original_library_sha256':self.old,'transformed_unsigned_sha256':self.unsigned,'transformed_runtime_sha256':self.new,'evidence_sha256':{name:m.sha(self.root/name) for name in list(files)+['build.log','configdata.txt','headers.json','framework-Info.plist','framework-CodeResources.plist']}}
        self.path=self.root/'host-local-qualification.json'
    def verify(self,receipt=None):
        m.write(self.path,receipt or self.receipt)
        return m.verify_host_local_qualification(self.path,m.sha(self.path),self.identity,m.NATIVE)
    def test_binding(self):self.assertEqual(self.verify()['transformed_runtime_sha256'],self.new)
    def test_wrong_source_status_identity(self):
        for key,value in [('source_sha256',{}),('statuses',{}),('device_executed',True),('optimization','O2'),('native_comparison_identity',self.identity)]:
            changed=copy.deepcopy(self.receipt);changed[key]=value
            with self.assertRaises(ValueError):self.verify(changed)
    def test_supporting_change(self):
        (self.root/'transform.json').write_text('{}')
        with self.assertRaises(ValueError):self.verify()
    def test_expected_digest(self):
        self.verify()
        with self.assertRaises(ValueError):m.verify_host_local_qualification(self.path,'0'*64,self.identity,m.NATIVE)
    def test_inventory_traversal(self):
        self.receipt['evidence_sha256']['../escape']='a'*64
        with self.assertRaises(ValueError):self.verify()
    def test_missing_support(self):
        del self.receipt['evidence_sha256']['transform.json']
        with self.assertRaises(ValueError):self.verify()
    def test_rehashed_wrong_runtime_and_pair(self):
        for name, mutate in [('transformed/crypto.json',lambda v:v.update(library_sha256='f'*64)),('transformed/paired-identical.json',lambda v:v['matrices']['original']['local-AB'].clear())]:
            path=self.root/name; original=path.read_bytes();value=json.loads(original);mutate(value);m.write(path,value)
            changed=copy.deepcopy(self.receipt);changed['evidence_sha256'][name]=m.sha(path)
            with self.assertRaises(ValueError):self.verify(changed)
            path.write_bytes(original)
    def test_effective_flags_not_just_label(self):
        path=self.root/'build.log';path.write_text(path.read_text().replace('-Os','-O2'))
        changed=copy.deepcopy(self.receipt);changed['evidence_sha256']['build.log']=m.sha(path)
        with self.assertRaises(ValueError):self.verify(changed)
    def test_symlink_support(self):
        p=self.root/'transform.json'; data=p.read_bytes();p.unlink();target=self.root/'other';target.write_bytes(data);p.symlink_to(target)
        with self.assertRaises(ValueError):self.verify()

class FrameworkCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.library=self.root/'source/OpenSSL.framework/Versions/A/OpenSSL'
        self.library.parent.mkdir(parents=True);self.library.write_bytes(b'fixture-not-executable');self.library.chmod(0o755)
        (self.library.parent/'Headers').mkdir();(self.library.parent/'Headers/ssl.h').write_text('fixture header')
        (self.library.parent/'Resources').mkdir();(self.library.parent/'Resources/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'OpenSSL','DTPlatformName':'macosx'}))
        self.destination=self.root/'copy/OpenSSL.framework/Versions/A/OpenSSL'
    def test_full_layout_and_unchanged_real_consumer_prefix(self):
        before=m.copy_framework(self.library,self.destination)
        self.assertEqual(before,m.validate_framework_layout(self.destination))
        # Execute only the unchanged source-owned admission prefix, never crypto,
        # subprocesses or the synthetic library bytes. This is not runtime proof.
        tree=ast.parse((m.ROOT/'ci/issue53-crypto/run.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        prefix=fn.body[:6]
        self.assertIsInstance(prefix[-1],ast.If);self.assertIsInstance(prefix[-1].test,ast.Name);self.assertEqual(prefix[-1].test.id,'framework')
        fn.body=prefix;module=ast.Module(body=[fn],type_ignores=[]);ast.fix_missing_locations(module)
        namespace={'Path':Path,'plistlib':plistlib,'platform':types.SimpleNamespace(system=lambda:'Darwin'),'digest':m.sha}
        exec(compile(module,'reviewed-consumer-admission-prefix','exec'),namespace)
        namespace['run'](types.SimpleNamespace(output=self.root/'unused.json',library=self.destination,sha256=m.sha(self.destination),builtin_providers=True))
        self.assertFalse((self.destination.parents[2]/'Info.plist').exists())
    def test_binary_only_delta(self):
        before=m.copy_framework(self.library,self.destination);old=m.sha(self.destination);self.destination.write_bytes(b'changed')
        m.verify_framework_delta(before,m.framework_inventory(self.destination.parents[2]),old,m.sha(self.destination))
    def test_metadata_header_mode_or_added_file_rejected(self):
        before=m.copy_framework(self.library,self.destination);digest=m.sha(self.destination)
        for path in (self.destination.parent/'Resources/Info.plist',self.destination.parent/'Headers/ssl.h'):
            original=path.read_bytes();path.write_bytes(original+b'changed')
            with self.assertRaises(ValueError):m.verify_framework_delta(before,m.framework_inventory(self.destination.parents[2]),digest,digest)
            path.write_bytes(original)
        self.destination.chmod(0o600)
        with self.assertRaises(ValueError):m.verify_framework_delta(before,m.framework_inventory(self.destination.parents[2]),digest,digest)
    def test_missing_or_wrong_platform_metadata_rejected_before_copy(self):
        plist=self.library.parent/'Resources/Info.plist';plist.write_bytes(plistlib.dumps({'CFBundleExecutable':'OpenSSL','DTPlatformName':'iphoneos'}))
        with self.assertRaises(ValueError):m.copy_framework(self.library,self.destination)
        self.assertFalse(self.destination.parents[2].exists())
    def test_internal_links_preserved_external_rejected(self):
        link=self.library.parents[2]/'Headers';link.symlink_to('Versions/A/Headers',target_is_directory=True)
        m.copy_framework(self.library,self.destination)
        self.assertEqual(os.readlink(self.destination.parents[2]/'Headers'),'Versions/A/Headers')
        link.unlink();link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError):m.validate_framework_layout(self.library)
    def test_rejected_delta_persisted_before_assertion(self):
        before=m.copy_framework(self.library,self.destination);digest=m.sha(self.destination)
        added=self.destination.parent/'unexpected';added.write_text('diagnostic')
        after=m.framework_inventory(self.destination.parents[2]);report=self.root/'delta.json'
        with self.assertRaises(ValueError):m.record_framework_delta(report,before,after,digest,digest)
        observed=m.read(report)
        self.assertEqual(observed['status'],'OBSERVED_NOT_ADMITTED')
        self.assertEqual(set(observed['added']),{'Versions/A/unexpected'})
        self.assertEqual(observed['before'],before);self.assertEqual(observed['after'],after)
    def test_isolated_signing_only_copies_guarded_bytes_and_verifies_actual_target(self):
        m.copy_framework(self.library,self.destination);evidence=self.root/'evidence';evidence.mkdir();calls=[]
        old=self.destination.read_bytes();new=b'fake-signed-bytes'
        def tool(command,**kwargs):
            calls.append(command);path=Path(command[-1])
            self.assertNotIn('--ignore-resources',command)
            if '--verify' in command:
                self.assertEqual(path.read_bytes(),new)
                return types.SimpleNamespace(returncode=0,stdout='synthetic strict verify')
            self.assertEqual(path.name,'OpenSSL');self.assertNotIn(self.destination.parents[2],path.parents)
            path.write_bytes(new);(path.parent/'_CodeSignature').mkdir();(path.parent/'_CodeSignature/resource').write_text('isolated side effect')
            return types.SimpleNamespace(returncode=0)
        guard=types.SimpleNamespace(verify=mock.Mock(return_value={'synthetic':True}))
        with mock.patch.object(m.subprocess,'run',side_effect=tool):m.isolated_codesign(self.destination,'adhoc-sign',guard,evidence)
        self.assertEqual(self.destination.read_bytes(),new);self.assertEqual(calls[-1][-1],str(self.destination))
        guard.verify.assert_called_once_with(new,old,preserve_stabs=True,require_reduction=False,host_legacy_dyld=True)
        self.assertFalse((self.destination.parents[2]/'_CodeSignature').exists())
        self.assertEqual(m.read(evidence/'adhoc-sign-copyback-delta.json')['added'],{})
    def test_guard_rejection_never_copies_back(self):
        m.copy_framework(self.library,self.destination);evidence=self.root/'evidence';evidence.mkdir();old=self.destination.read_bytes()
        def tool(command,**kwargs):
            if '--verify' not in command:Path(command[-1]).write_bytes(b'bad')
            return types.SimpleNamespace(returncode=0,stdout='synthetic')
        guard=types.SimpleNamespace(verify=mock.Mock(side_effect=ValueError('structural rejected')))
        with mock.patch.object(m.subprocess,'run',side_effect=tool),self.assertRaisesRegex(ValueError,'structural'):
            m.isolated_codesign(self.destination,'adhoc-sign',guard,evidence)
        self.assertEqual(self.destination.read_bytes(),old)
    def test_actual_target_verification_failure_retains_exact_evidence(self):
        m.copy_framework(self.library,self.destination);evidence=self.root/'evidence';evidence.mkdir()
        def tool(command,**kwargs):
            path=Path(command[-1])
            if '--verify' not in command:path.write_bytes(b'fake-signed')
            return types.SimpleNamespace(returncode=1 if '--verify' in command and path==self.destination else 0,stdout='synthetic target verification failed')
        guard=types.SimpleNamespace(verify=mock.Mock(return_value={'synthetic':True}))
        with mock.patch.object(m.subprocess,'run',side_effect=tool),self.assertRaisesRegex(ValueError,'actual-framework-target'):
            m.isolated_codesign(self.destination,'adhoc-sign',guard,evidence)
        self.assertTrue((evidence/'adhoc-sign-copyback-delta.json').is_file())
        self.assertIn('failed',(evidence/'actual-framework-target-codesign-verify.log').read_text())
    def test_signature_removal_also_isolated(self):
        m.copy_framework(self.library,self.destination);evidence=self.root/'evidence';evidence.mkdir();old=self.destination.read_bytes()
        def tool(command,**kwargs):
            self.assertIn('--remove-signature',command);self.assertNotIn(self.destination.parents[2],Path(command[-1]).parents)
            Path(command[-1]).write_bytes(b'unsigned');return types.SimpleNamespace(returncode=0)
        guard=types.SimpleNamespace(verify=mock.Mock(return_value={'synthetic':True}))
        with mock.patch.object(m.subprocess,'run',side_effect=tool):m.isolated_codesign(self.destination,'remove-signature',guard,evidence)
        guard.verify.assert_called_once_with(old,b'unsigned',preserve_stabs=True,require_reduction=False,host_legacy_dyld=True)

    def test_existing_destination_not_overwritten(self):
        self.destination.parents[2].mkdir(parents=True)
        with self.assertRaises(ValueError):m.copy_framework(self.library,self.destination)

class VersionCommandTests(unittest.TestCase):
    def test_actual_version_context_and_binary_are_both_verified(self):
        tree=ast.parse(Path(m.__file__).read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='transform')
        calls=[node for node in ast.walk(fn) if isinstance(node,ast.Call)]
        signs=[node for node in calls if isinstance(node.func,ast.Attribute) and node.func.attr=='run' and node.args and isinstance(node.args[0],ast.List) and any(isinstance(x,ast.Constant) and x.value=='--sign' for x in node.args[0].elts)]
        self.assertEqual(len(signs),1);self.assertEqual(ast.unparse(signs[0].args[0].elts[-1]),'str(destination.parent)')
        verifies={node.args[1].value:ast.unparse(node.args[0]) for node in calls if isinstance(node.func,ast.Name) and node.func.id=='verify_actual_signature'}
        self.assertEqual(verifies,{'actual-framework-version':'destination.parent','actual-framework-target':'destination'})

class WorkflowTests(unittest.TestCase):
    def test_scoped_native_diagnostic(self):
        text=(m.ROOT/'.github/workflows/openssl-local-host-diagnostic.yml').read_text()
        for required in ('contents: read','set-safe-directory: false','persist-credentials: false','Xcode_26.4.1','--optimization Os','test_host_qualification.py','test_framework_seal.py','test_host_legacy_dyld_policy.py','transformed-NOT-ADMITTED','path: host-transform/runtime/OpenSSL.framework/',"steps.build.outcome == 'success'",'--evidence qualification --work host-transform'):
            self.assertIn(required,text)
        for forbidden in ('actions: write','always()','build_ios.py','package_candidate.py','--location-trusted'):
            self.assertNotIn(forbidden,text)
        self.assertLess(text.index('test_host_qualification.py'),text.index('build.py --output'))

if __name__=='__main__':unittest.main()
