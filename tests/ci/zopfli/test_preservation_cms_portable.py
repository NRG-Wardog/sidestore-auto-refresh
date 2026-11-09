"""Synthetic portable-admission controls; never claims native execution."""

import sys as _ci_bootstrap_sys
from pathlib import Path as _ci_bootstrap_path
_ci_bootstrap_sys.path.insert(0,str(_ci_bootstrap_path(__file__).resolve().parents[3]/'ci/packaging/zopfli'))
import copy
import json
from pathlib import Path
import tempfile
import unittest
import preservation_cms_portable as p

class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contract = p.decode(p.read_bytes(p.HERE/'cms-source-contract.json'))
        self.identity = {'workflow_commit':self.contract['native_builder'],'run_id':'123','run_attempt':'2'}
        self.host = {'schema':'issue53-lts-generated-dependency-v1','status':'BUILT_NOT_QUALIFIED_NOT_APP_READY',
            'source':self.contract['openssl'],'recipe_sha256':self.contract['host_recipe_sha256'],
            'static_archives_sha256':{'/build/install/lib/libcrypto.a':'b'*64,'/build/install/lib/libssl.a':'c'*64},
            'header_inventory_sha256':'d'*64}
        self.cms = {'schema':'profile-cms-native-v1','status':'passed','openssl':'3.5.9','qualified':True,
            'time_policy':'inner-current-system-time;legacy-no-check-time','tested_at_utc':'2026-10-31T19:00:00+00:00',
            'checks':self.contract['cms_cases'],'count':58}
        self.public = {'schema':'preservation-der-public-v1','status':'PASS','cases':self.contract['public_cases'],
            'cms_verified':True,'device_executed':False}
        self.receipt = {'schema':'preservation-cms-native-v1','status':'PASS_EXACT_CRYPTO_AND_SWIFT_PUBLIC_BOUNDARY_NOT_DEVICE',
            'run':self.identity,'source_sha256':self.contract['source_sha256'],
            'crypto':{'version':'3.5.9','build_receipt_sha256':'','source_archive_sha256':self.contract['openssl']['sha256'],
                'libcrypto_sha256':'b'*64,'headers_inventory_sha256':'d'*64},
            'tool_sha256':{'/usr/bin/clang':'e'*64,'/usr/bin/swiftc':'f'*64},'openssl_cli_sha256':'a'*64,
            'cms_cases':self.contract['cms_cases'],'public_cases':self.contract['public_cases'],'log_sha256':{},
            'cms_verified':True,'device_executed':False,'public_profile_bytes_retained':False}
        self.flush()
    def write(self,name,value):
        (self.root/name).write_text(json.dumps(value,sort_keys=True)+'\n')
    def flush(self):
        self.write(p.FILES[1],self.cms); self.write(p.FILES[2],self.public); self.write(p.FILES[3],self.host)
        self.receipt['crypto']['build_receipt_sha256']=p.sha((self.root/p.FILES[3]).read_bytes())
        self.receipt['log_sha256']={x:p.sha((self.root/x).read_bytes()) for x in p.FILES[1:3]}
        self.write(p.FILES[0],self.receipt)
    def verify(self): return p.verify(self.root,self.identity)
    def reject(self):
        self.flush()
        with self.assertRaises((ValueError,TypeError,KeyError)): self.verify()
    def test_positive_closed_scope(self):
        r=self.verify();self.assertEqual(r['source_count'],20);self.assertEqual(r['cms_case_count'],58)
        self.assertEqual(r['public_case_count'],24);self.assertFalse(r['device_executed'])
    def test_wrong_run_attempt_builder(self):
        for key,value in [('run_id','124'),('run_attempt','1'),('workflow_commit','a'*40)]:
            original=self.receipt['run'];self.receipt['run']={**self.identity,key:value};self.reject();self.receipt['run']=original
    def test_caller_cannot_select_unreviewed_builder(self):
        self.identity={**self.identity,'workflow_commit':'a'*40};self.receipt['run']=self.identity;self.reject()
    def test_missing_source_or_changed_source(self):
        old=self.receipt['source_sha256'];name=next(iter(old))
        for value in ({k:v for k,v in old.items() if k!=name},{**old,name:'e'*64},{**old,'extra':'f'*64}):
            self.receipt['source_sha256']=value;self.reject()
    def test_new_diagnostic_source_dependencies_cannot_be_omitted(self):
        original=self.receipt['source_sha256']
        for suffix in ('/TestsCMS/diagnostic_support.py','/TestsCMS/approved-inner-provenance.json'):
            names=[name for name in original if name.endswith(suffix)]
            self.assertEqual(len(names),1)
            self.receipt['source_sha256']={k:v for k,v in original.items() if k!=names[0]}
            self.reject()
        self.receipt['source_sha256']=original
    def test_diagnostic_native_and_public_cases_cannot_be_omitted(self):
        for obj,key,predicate in ((self.cms,'checks',lambda n:n.startswith('diagnostic_')),(self.public,'cases',lambda n:n.endswith('signer-diagnostic-facade'))):
            original=obj[key]
            for name in filter(predicate,original):
                obj[key]=[n for n in original if n!=name]
                self.reject()
            obj[key]=original
    def test_inspection_cases_cannot_be_omitted_or_reused_from_prior_suite(self):
        self.assertEqual(len(self.contract['cms_cases']),58)
        self.assertEqual(len(self.contract['public_cases']),24)
        for obj,key,predicate in ((self.cms,'checks',lambda n:n.startswith('inspection_')),
                                  (self.public,'cases',lambda n:'inspection-' in n)):
            original=obj[key]
            names=[n for n in original if predicate(n)]
            self.assertTrue(names)
            for name in names:
                obj[key]=[n for n in original if n!=name]
                self.reject()
            obj[key]=original
        # Even a self-consistent former40/18 report cannot qualify new bytes.
        self.cms['checks']=[n for n in self.contract['cms_cases'] if not n.startswith('inspection_')]
        self.cms['count']=40
        self.public['cases']=[n for n in self.contract['public_cases'] if 'inspection-' not in n]
        self.receipt['cms_cases']=self.cms['checks'];self.receipt['public_cases']=self.public['cases']
        self.reject()
    def test_no_device_or_profile_retention_or_unverified_claim(self):
        for key,value in [('device_executed',True),('public_profile_bytes_retained',True),('cms_verified',1)]:
            old=self.receipt[key];self.receipt[key]=value;self.reject();self.receipt[key]=old
    def test_cms_cases_missing_duplicate_reordered_extra(self):
        original=self.cms['checks']
        for value in (original[1:],original+[original[0]],list(reversed(original)),original+['raw']):
            self.cms['checks']=value;self.reject()
    def test_public_cases_missing_duplicate_reordered_extra(self):
        original=self.public['cases']
        for value in (original[1:],original+[original[0]],list(reversed(original)),original+['raw']):
            self.public['cases']=value;self.reject()
    def test_crypto_version_or_false_qualification(self):
        for key,value in [('openssl','3.5.7'),('qualified',False),('qualified',1),('time_policy','ignore-time'),('count',58.0),('count',28),('count',True)]:
            old=self.cms[key];self.cms[key]=value;self.reject();self.cms[key]=old
    def test_host_source_recipe_header_archive_drift(self):
        original=copy.deepcopy(self.host)
        for key,value in [('source',{**self.host['source'],'version':'3.5.7'}),('recipe_sha256','a'*64),('header_inventory_sha256','a'*64),('static_archives_sha256',{'/x/libcrypto.a':'a'*64}),('static_archives_sha256',{'/x/libssl.a':'b'*64}),('static_archives_sha256',{'/x/libcrypto.a':'b'*64,'/y/libcrypto.a':'b'*64})]:
            self.host=copy.deepcopy(original);self.host[key]=value;self.reject()
    def test_payload_hash_drift_without_rehash(self):
        for name in p.FILES[1:]:
            self.flush();(self.root/name).write_text('{}\n')
            with self.assertRaises(ValueError):self.verify()
    def test_extra_raw_fields_rejected(self):
        for obj in (self.receipt,self.cms,self.public):
            obj['raw_profile']='forbidden';self.reject();del obj['raw_profile']
    def test_duplicate_json_key_rejected(self):
        for name in (p.FILES[0],p.FILES[1],p.FILES[2]):
            self.flush();text=(self.root/name).read_text();(self.root/name).write_text(text.replace('{','{"schema":"duplicate",',1))
            with self.assertRaises(ValueError):self.verify()
    def test_symlink_missing_oversized(self):
        for name in p.FILES:
            self.flush();path=self.root/name;data=path.read_bytes();path.unlink()
            with self.assertRaises(ValueError):self.verify()
            target=self.root/'target';target.write_bytes(data);path.symlink_to(target)
            with self.assertRaises(ValueError):self.verify()
            path.unlink();path.write_bytes(b' '* (2*1024*1024+1))
            with self.assertRaises(ValueError):self.verify()
    def test_public_log_must_be_single_record(self):
        path=self.root/p.FILES[2];path.write_bytes(path.read_bytes()*2)
        with self.assertRaises(ValueError):self.verify()
    def test_invalid_or_non_utc_timestamp(self):
        for timestamp in ('2026-99-99T00:00:00+00:00','2026-10-31T00:00:00','2026-10-31T00:00:00+01:00'):
            self.cms['tested_at_utc']=timestamp;self.reject()

if __name__=='__main__':unittest.main()
