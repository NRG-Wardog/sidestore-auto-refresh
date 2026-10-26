import copy
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
          'transform.json':{'original_sha256':self.old,'unsigned_sha256':self.unsigned,'runtime_sha256':self.new,'dwarf_sha256':'e'*64},
          'build-receipt.json':{'source':source,'optimization':'Os','recipe_sha256':m.sources()['ci/issue53-lts/build.py'],'library_sha256':self.old,'tools_sha256':{'/trusted/clang':'e'*64},'generated_inventory':{'OpenSSL.framework.dSYM/Contents/Resources/DWARF/OpenSSL':'e'*64,'OpenSSL.framework.dSYM/Contents/Info.plist':'f'*64,'OpenSSL.framework/Versions/A/OpenSSL':self.old}}}
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
        self.receipt={'schema':'openssl-true-local-host-qualification-v1','status':'PASS_MACOS_SYNTHETIC_ONLY','qualification_identity':self.identity,'native_comparison_identity':m.NATIVE,'source':source,'optimization':'Os','source_sha256':m.sources(),'actual_compile_evidence':m.compile_evidence(self.root,files['build-receipt.json']),'runtime_library_path':'/owned/runtime/OpenSSL','statuses':m.STATUS,'device_executed':False,'original_library_sha256':self.old,'transformed_unsigned_sha256':self.unsigned,'transformed_runtime_sha256':self.new,'evidence_sha256':{name:m.sha(self.root/name) for name in list(files)+['build.log','configdata.txt','headers.json']}}
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

class WorkflowTests(unittest.TestCase):
    def test_scoped_native_diagnostic(self):
        text=(m.ROOT/'.github/workflows/openssl-local-host-diagnostic.yml').read_text()
        for required in ('contents: read','set-safe-directory: false','persist-credentials: false','Xcode_26.4.1','--optimization Os','test_host_qualification.py','--evidence qualification --work host-transform'):
            self.assertIn(required,text)
        for forbidden in ('actions: write','always()','build_ios.py','package_candidate.py','--location-trusted'):
            self.assertNotIn(forbidden,text)
        self.assertLess(text.index('test_host_qualification.py'),text.index('build.py --output'))

if __name__=='__main__':unittest.main()
