"""Synthetic receipt/log controls only. Never executes native crypto or compiler."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib
import admission as a


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.e=self.root/'evidence';self.e.mkdir()
        self.host=self.root/'lts-host-build';self.ios=self.root/'lts-ios-build'
        self.host.mkdir();self.ios.mkdir()
        self.identity={'workflow_commit':'synthetic-commit','run_id':'synthetic-run','run_attempt':'1'}
        self.source=json.loads((a.ROOT/'ci/issue53-lts/source.json').read_text());self.helpers=a.qualification_sources()
        self.hostbin=self.host/'generated/OpenSSL.framework/Versions/A/OpenSSL';self.hostbin.parent.mkdir(parents=True)
        self.hostbin.write_bytes(b'synthetic host library, never executed')
        self.generated=self.ios/'GeneratedDependencies/OpenSSL35';self.device=self.generated/'OpenSSL.xcframework/ios-arm64/OpenSSL.framework/OpenSSL'
        self.device.parent.mkdir(parents=True);self.device.write_bytes(b'synthetic original device library, never executed')
        dwarf=self.generated/'OpenSSL.xcframework/ios-arm64/dSYMs/OpenSSL.framework.dSYM/Contents/Resources/DWARF/OpenSSL'
        dwarf.parent.mkdir(parents=True);dwarf.write_bytes(b'synthetic dwarf, never executed')
        self.final=self.ios/'measurement-stripped-OpenSSL';self.final.write_bytes(b'synthetic smaller output, never executed')
        self.maclog,self.macconfigs=self.logs(self.host,{'macOS':None},'build.log')
        self.ioslog,self.iosconfigs=self.logs(self.ios,a.TRIPLES,'build-ios.log')
        source_inventory=hashlib.sha256(b'synthetic complete original archive inventory').hexdigest()
        self.mac={'schema':'issue53-lts-generated-dependency-v1','status':'BUILT_NOT_QUALIFIED_NOT_APP_READY',
            'source':self.source,'optimization':'Os','recipe_sha256':self.helpers['ci/issue53-lts/build.py'],
            'build_environment':{'CFLAGS':'-Os -g -fPIC -mmacosx-version-min=11.0'},'product_pins_changed':False,
            'production_admission':False,'device_executed':False,'source_inventory_sha256':source_inventory,
            'library_sha256':a.sha(self.hostbin),'generated_inventory':a.inventory(self.host/'generated'),
            'tools_sha256':{a.CLANG:'a'*64},'configuration_sha256':a.sha(self.macconfigs['macOS'])}
        self.device_receipt={'schema':'issue53-ios-generated-dependency-v1','status':'BUILT_COMPILE_LINK_CHECKED_NOT_RUNTIME_QUALIFIED',
            'source':self.source,'optimization':'Os','recipe_sha256':self.helpers['ci/issue53-lts/build_ios.py'],
            'shared_recipe_sha256':self.helpers['ci/issue53-lts/build.py'],
            'generated_inventory':a.inventory(self.generated),'tools':{'clang':{'invocation':a.CLANG,'sha256':'a'*64}},
            **dict.fromkeys(('device_executed','simulator_executed','production_admission','old_gitlink_overwritten'),False),
            'slices':{key:{'target_triple':triple,'source_inventory_sha256':source_inventory,
                          'build_environment':{'CFLAGS':'-Os -g -fPIC -target '+triple},
                          'configuration_sha256':a.sha(self.iosconfigs[key])} for key,triple in a.TRIPLES.items()}}
        self.save(self.host/'build-receipt.json',self.mac);self.save(self.e/'openssl-host-build-receipt.json',self.mac)
        self.save(self.ios/'ios-build-receipt.json',self.device_receipt);self.save(self.e/'openssl-ios-build-receipt.json',self.device_receipt)
        components={
            'lts-crypto.json':{'status':'PASS_SYNTHETIC_ONLY','library_sha256':a.sha(self.hostbin),
                               'builtin_providers_only':True,'provider_dispatch_images_bound':True},
            'lts-host-zsign.json':{'status':'PASS_SYNTHETIC_UNTRUSTED_CHAIN','library_sha256':a.sha(self.hostbin),'trusted_Apple_signing_identity':False},
            'coload.json':{'status':'PASS_MACOS_DIAGNOSTIC_ONLY','errors':[],'ios_loader_qualified':False}}
        for name,data in components.items():self.save(self.e/name,data)
        self.qualified={'schema':'issue53-lts-qualification-v1','status':'PASS_MACOS_SYNTHETIC_ONLY','failures':[],
            'production_admission':False,'device_executed':False,'library_sha256':a.sha(self.hostbin),
            'input_variant':{'source':self.source,'recipe_sha256':self.mac['recipe_sha256'],
                             'build_receipt_sha256':a.sha(self.e/'openssl-host-build-receipt.json'),'host_binary_sha256':a.sha(self.hostbin)},
            'evidence_sha256':{name:a.sha(self.e/name) for name in components}}
        self.save(self.e/'lts-qualification.json',self.qualified)
        baseline=json.loads((a.ROOT/'ci/issue55-openssl-size/baseline.json').read_text())
        final=self.final.read_bytes();c=zlib.compressobj(9,zlib.DEFLATED,-15);compressed=len(c.compress(final)+c.flush())
        binary_fixture={'structural':{'synthetic_boundary_only':True},'defined_external_names':{'count':baseline['defined_external_names_count'],'sha256':baseline['defined_external_names_sha256']}}
        binary_patch=patch.object(a,'binary_evidence',return_value=binary_fixture)
        self.binary_mock=binary_patch.start();self.addCleanup(binary_patch.stop)
        self.measure={'structural':binary_fixture['structural'],'schema':'issue55-openssl-size-experiment-v1','status':'MEASURED_NOT_PROMOTED',**self.identity,
            'optimization':'Os','production_admission':False,'device_execution_proven':False,
            'qualification_sources_sha256':self.helpers,'consumer_policy_sha256':a.sha(a.ROOT/'ci/issue53-lts/consumer-policy.json'),
            'mac_receipt_sha256':a.sha(self.e/'openssl-host-build-receipt.json'),'ios_receipt_sha256':a.sha(self.e/'openssl-ios-build-receipt.json'),
            'host_qualification_sha256':a.sha(self.e/'lts-qualification.json'),'original_generated_binary_sha256':a.sha(self.device),
            'measured_stripped_binary_sha256':a.sha(self.final),'baseline':baseline,
            'baseline_receipt_sha256':a.sha(a.ROOT/'ci/issue55-openssl-size/baseline.json'),
            'comparison':{'raw_bytes_per_copy':len(final),'deflate9_bytes_per_copy':compressed,
                'raw_bytes_saved_per_copy':baseline['raw_bytes_per_copy']-len(final),
                'deflate9_bytes_saved_per_copy':baseline['deflate9_bytes_per_copy']-compressed,
                'deflate9_bytes_saved_two_separate_copies':2*(baseline['deflate9_bytes_per_copy']-compressed),
                'defined_external_names':{'count':baseline['defined_external_names_count'],'sha256':baseline['defined_external_names_sha256']},
                'final_app_ipa_size_measured':False},
            'actual_compile_evidence':{'macOS':a.compiler_evidence(self.maclog,self.macconfigs,a.CLANG,{'macOS':None}),
                                      'iOS':a.compiler_evidence(self.ioslog,self.iosconfigs,a.CLANG,a.TRIPLES)}}
        self.save(self.e/'size-measurement.json',self.measure)

    @staticmethod
    def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')

    def logs(self,root,triples,filename):
        log=root/filename;lines=[];configs={}
        for key,triple in triples.items():
            directory=root if triple is None else root/key;directory.mkdir(exist_ok=True)
            config=directory/'configdata.txt';config.write_text('CFLAGS => "-O3"\n CFLAGS = -Os -g -fPIC\n');configs[key]=config
            flags=' -target '+triple if triple else ''
            lines.extend([a.CLANG+' -Os'+flags+' -DSTATIC_LEGACY -c providers/legacyprov.c',
                          a.CLANG+' -Os'+flags+' -c providers/defltprov.c'])
        log.write_text('\n'.join(lines)+'\n');return log,configs

    def call(self,expected=None):return a.verify_measurement(self.e,self.host,self.ios,self.identity,expected)

    def test_same_run_full_chain_and_optional_nested_receipt(self):
        result=self.call(self.device_receipt)
        self.binary_mock.assert_called_once()
        self.assertEqual(result['optimization'],'Os');self.assertFalse(result['device_execution_proven'])
        self.assertEqual(result['qualified_device_binary_sha256'],a.sha(self.device))
        self.assertEqual(set(result['receipt_sha256']),{'measurement','host_qualification','host_receipt','ios_receipt'})

    def test_other_workflow_run_attempt_or_helpers_reject(self):
        for key,value in [('workflow_commit','old'),('run_id','old'),('run_attempt','old'),('qualification_sources_sha256',{})]:
            with self.subTest(key=key):
                changed=copy.deepcopy(self.measure);changed[key]=value;self.save(self.e/'size-measurement.json',changed)
                with self.assertRaises(ValueError):self.call()
        self.save(self.e/'size-measurement.json',self.measure)

    def test_missing_evidence_or_link_rejects(self):
        path=self.e/'lts-qualification.json';data=path.read_bytes();path.unlink()
        with self.assertRaises(ValueError):self.call()
        elsewhere=self.root/'qual.json';elsewhere.write_bytes(data);path.symlink_to(elsewhere)
        with self.assertRaises(ValueError):self.call()

    def test_copied_receipt_or_nested_receipt_drift_rejects(self):
        changed=copy.deepcopy(self.device_receipt);changed['optimization']='O2'
        with self.assertRaises(ValueError):self.call(changed)
        self.save(self.e/'openssl-ios-build-receipt.json',changed)
        with self.assertRaises(ValueError):self.call()

    def test_changed_generated_library_or_compiler_log_rejects(self):
        original=self.device.read_bytes();self.device.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.call()
        self.device.write_bytes(original)
        self.ioslog.write_text(self.ioslog.read_text().replace('-Os','-Os -O2'))
        with self.assertRaisesRegex(ValueError,'final optimization'):self.call()

    def test_qualification_component_tampering_rejects(self):
        self.save(self.e/'lts-crypto.json',{'status':'PASS_SYNTHETIC_ONLY'})
        with self.assertRaisesRegex(ValueError,'evidence changed'):self.call()

    def test_hash_consistent_failed_or_unbound_host_qualification_rejects(self):
        for key,value in [('status','FAIL_QUARANTINED'),('input_variant',{})]:
            changed=copy.deepcopy(self.qualified);changed[key]=value;self.save(self.e/'lts-qualification.json',changed)
            measured=copy.deepcopy(self.measure);measured['host_qualification_sha256']=a.sha(self.e/'lts-qualification.json')
            self.save(self.e/'size-measurement.json',measured)
            with self.assertRaises(ValueError):self.call()

    def test_manufactured_savings_and_missing_export_comparison_reject(self):
        changed=copy.deepcopy(self.measure);changed['comparison']['deflate9_bytes_saved_two_separate_copies']+=1
        self.save(self.e/'size-measurement.json',changed)
        with self.assertRaisesRegex(ValueError,'measured size'):self.call()

    def test_current_structure_and_exports_are_recomputed_not_trusted(self):
        self.binary_mock.return_value={'structural':{'changed':True},'defined_external_names':{}}
        with self.assertRaisesRegex(ValueError,'structure'):self.call()
        self.binary_mock.return_value={'structural':self.measure['structural'],'defined_external_names':{}}
        with self.assertRaisesRegex(ValueError,'external API'):self.call()


class ActualBinaryEvidenceTests(unittest.TestCase):
    def test_real_source_owned_macho_guard_and_export_parser(self):
        import sys
        sys.path.insert(0,str(a.ROOT/'tests'))
        from test_macho_stab_guard import fixture
        before,after=fixture(True),fixture(False)
        observed=a.binary_evidence(before,after,after)
        self.assertEqual(observed['defined_external_names'],a.defined_external_names(after))
        with self.assertRaises(ValueError):a.binary_evidence(before,after.replace(b'_public',b'_PubLic'),after)
        with self.assertRaises(ValueError):a.binary_evidence(before,after,after.replace(b'0123456789abcdef',b'0000000000000000'))

class MainWorkflowAdmissionTests(unittest.TestCase):
    def test_same_run_qualification_and_receipt_copies_precede_staging(self):
        text=(a.ROOT/'.github/workflows/issues-native.yml').read_text()
        start=text.index('      - name: Build and seal explicit generated LTS inputs')
        end=text.index('      - name: Capture genuine app lock transition',start)
        block=text[start:end]
        ordered=('test_admission.py','build.py --output lts-host-build --optimization Os',
                 'cp lts-host-build/build-receipt.json evidence/openssl-host-build-receipt.json',
                 'qualify.py --built lts-host-build',
                 'build_ios.py --output lts-ios-build --archive lts-host-build/openssl-3.5.9.tar.gz --optimization Os',
                 'cp lts-ios-build/ios-build-receipt.json evidence/openssl-ios-build-receipt.json',
                 'measure.py --mac lts-host-build --ios lts-ios-build',
                 'record.py stage-openssl lts-ios-build','record.py stage-embedded-openssl lts-ios-build')
        positions=[block.index(token) for token in ordered]
        self.assertEqual(positions,sorted(positions))
        self.assertIn('tee evidence/lts-qualification-driver.log',block)
        self.assertIn('trap collect_crypto_evidence EXIT',block)
        self.assertEqual(block.count('--optimization Os'),2)

if __name__=='__main__':unittest.main()
