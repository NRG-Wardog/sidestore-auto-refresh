"""Synthetic generated-input closure tests, never native OpenSSL execution."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

from verify_candidate_graph import BASELINE_OWNERS
# The source-graph validator has its own controls; these fixtures own fake pins.
with patch('verify_candidate_graph.load_candidate_graph', return_value={'owners': BASELINE_OWNERS}):
    import record as recorder


def macho(platform=2, minimum=0x000f0000, load_openssl=False):
    commands = struct.pack('<6I',0x32,24,platform,minimum,0x001a0000,0)
    commands += struct.pack('<2I16s',0x1b,24,b'0123456789abcdef')
    name=b'@rpath/OpenSSL.framework/OpenSSL\0'
    length=(24+len(name)+7)//8*8
    commands += struct.pack('<6I',0xc if load_openssl else 0xd,length,24,0,0,0)+name+bytes(length-24-len(name))
    return struct.pack('<8I',0xfeedfacf,0x100000c,0,6,3,len(commands),0,0)+commands


class GeneratedOpenSSLInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.sources=self.root/'sources';self.evidence=self.root/'evidence';self.tmp=self.root/'native'
        self.evidence.mkdir();self.tmp.mkdir()
        self.consumer=self.sources/'LiveContainer';project=self.consumer/'LiveContainer.xcodeproj/project.pbxproj';project.parent.mkdir(parents=True)
        project.write_text('path = GeneratedDependencies/OpenSSL35/OpenSSL.xcframework;')
        self.built=self.root/'built';generated=self.built/'GeneratedDependencies/OpenSSL35'
        self.framework=generated/'OpenSSL.xcframework/ios-arm64/OpenSSL.framework';self.framework.mkdir(parents=True)
        (self.framework/'OpenSSL').write_bytes(macho())
        (self.framework/'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'OpenSSL','CFBundleIdentifier':'org.openssl.generated.lts'}))
        (generated/'LICENSE.txt').write_text('synthetic license')
        (generated/'OpenSSL.xcframework/Info.plist').write_bytes(plistlib.dumps({'AvailableLibraries':[
            {'LibraryIdentifier':'ios-arm64','LibraryPath':'OpenSSL.framework','SupportedPlatform':'ios','SupportedArchitectures':['arm64']},
            {'LibraryIdentifier':'ios-arm64_x86_64-simulator','LibraryPath':'OpenSSL.framework','SupportedPlatform':'ios','SupportedPlatformVariant':'simulator','SupportedArchitectures':['arm64','x86_64']}]}))
        self.installer=recorder.generated_installer();folder=self.installer.HERE
        receipt={'schema':'issue53-ios-generated-dependency-v1','status':'BUILT_COMPILE_LINK_CHECKED_NOT_RUNTIME_QUALIFIED',
                 'source':json.loads((folder/'source.json').read_text()),'recipe_sha256':recorder.sha(folder/'build_ios.py'),
                 'shared_recipe_sha256':recorder.sha(folder/'build.py'),
                 'slices':{k:{} for k in ('ios-arm64','sim-arm64','sim-x86_64')},
                 'merged_simulator_module_probe_sha256':{'arm64':'0'*64,'x86_64':'0'*64},
                 'xcframework_relative_path':'GeneratedDependencies/OpenSSL35/OpenSSL.xcframework',
                 'generated_inventory':self.installer.build.inventory(generated),
                 'device_executed':False,'simulator_executed':False,'production_admission':False,'old_gitlink_overwritten':False}
        (self.built/'ios-build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        for name,value in [('S',self.sources),('E',self.evidence),('TMP',self.tmp)]:
            mock=patch.object(recorder,name,value);mock.start();self.addCleanup(mock.stop)
        env=patch.dict(os.environ,{'GITHUB_SHA':'workflow','GITHUB_RUN_ID':'run','GITHUB_RUN_ATTEMPT':'1'});env.start();self.addCleanup(env.stop)

    def stage(self):
        with patch.object(recorder,'tracked',return_value={'project':'immutable'}), patch.object(recorder,'git',return_value=recorder.OWNERS['LiveContainer'][1]):
            return recorder.stage_openssl(self.built)

    def snapshot(self):
        with patch.object(recorder,'repos',return_value={}), patch.object(recorder,'generated_embedded_snapshot',return_value={'present':False}):
            recorder.snapshot('compiler-inputs');recorder.snapshot('after-build')

    def product(self):
        target=self.tmp/'native-LiveContainer/Build/Products/Release-iphoneos/LiveContainer.app/Frameworks/OpenSSL.framework'
        shutil.copytree(self.framework,target)
        consumer=target.parent/'ZSign.dylib'
        consumer.parent.mkdir(exist_ok=True);consumer.write_bytes(macho(load_openssl=True))
        return target

    def test_generated_inputs_sealed_separately_and_exact_native_payload(self):
        self.assertEqual(recorder.generated_openssl_snapshot(),{'present':False})
        with self.assertRaisesRegex(RuntimeError,'not been staged'):recorder.generated_openssl_snapshot(required=True)
        captured=self.stage();self.snapshot();self.product()
        self.assertIn('provenance.json',captured['files'])
        self.assertIn('stage_receipt_sha256',captured)
        result=recorder.verify_generated_openssl_payload()
        self.assertEqual(result['generated_binary_sha256'],result['native_binary_sha256'])
        self.assertFalse(result['historical_gitlink_used_as_payload'])
        self.assertFalse(result['target_executed'])

    def test_bytes_extra_files_modes_and_run_drift_rejected(self):
        self.stage();root=self.consumer/'GeneratedDependencies/OpenSSL35'
        path=root/'LICENSE.txt';data=path.read_bytes();path.write_bytes(b'tampered')
        with self.assertRaisesRegex(RuntimeError,'stage identity/bytes drifted'):recorder.generated_openssl_snapshot(required=True,sealed=True)
        path.write_bytes(data);original_mode=path.stat().st_mode;path.chmod(0o700)
        with self.assertRaisesRegex(RuntimeError,'stage identity/bytes drifted'):recorder.generated_openssl_snapshot(required=True,sealed=True)
        path.chmod(original_mode)
        (root/'unexpected').write_text('extra')
        with self.assertRaisesRegex(RuntimeError,'stage identity/bytes drifted'):recorder.generated_openssl_snapshot(required=True,sealed=True)
        (root/'unexpected').unlink()
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'other'}),self.assertRaisesRegex(RuntimeError,'stage identity/bytes drifted'):
            recorder.generated_openssl_snapshot(required=True,sealed=True)

    def test_missing_snapshot_wrong_payload_and_platform_rejected(self):
        self.stage();self.snapshot();product=self.product();(product/'OpenSSL').write_bytes(macho(platform=7))
        with self.assertRaisesRegex(RuntimeError,'payload differs'):recorder.verify_generated_openssl_payload()
        shutil.copyfile(self.framework/'OpenSSL',product/'OpenSSL')
        document=json.loads((self.evidence/'after-build.json').read_text());document.pop('generated-openssl')
        (self.evidence/'after-build.json').write_text(json.dumps(document))
        with self.assertRaisesRegex(RuntimeError,'snapshot drift'):recorder.verify_generated_openssl_payload()
        for data in (macho(platform=7),macho(minimum=0x000e0000),b'not a binary'):
            (self.root/'bad').write_bytes(data)
            with self.assertRaises(RuntimeError):recorder.generated_ios_macho(self.root/'bad')

    def test_actual_lock_transition_boolean_is_not_hardcoded(self):
        path=self.evidence/'app-lock-transition.json'
        for expected in (True,False):
            path.write_text(json.dumps({'working_checkout_has_uncommitted_app_lock':expected}))
            self.assertIs(recorder.working_app_lock_differs(),expected)
        for invalid in (None,0,1,'true',[]):
            path.write_text(json.dumps({'working_checkout_has_uncommitted_app_lock':invalid}))
            with self.assertRaisesRegex(RuntimeError,'strict boolean'):recorder.working_app_lock_differs()

    def test_symlink_escape_and_real_alias_parent_are_checked_canonically(self):
        self.stage();self.snapshot();self.product()
        alias=self.root/'alias';alias.symlink_to(self.root,target_is_directory=True)
        with patch.object(recorder,'S',alias/'sources'),patch.object(recorder,'E',alias/'evidence'),patch.object(recorder,'TMP',alias/'native'):
            self.assertTrue(recorder.verify_generated_openssl_payload()['generated_inputs_unchanged'])
        root=self.consumer/'GeneratedDependencies/OpenSSL35';link=root/'escape';link.symlink_to('/etc/hosts')
        with self.assertRaisesRegex(RuntimeError,'Linked generated compiler input'):recorder.generated_openssl_snapshot(required=True,sealed=True)


class EmbeddedGeneratedInputsTests(unittest.TestCase):
    setUp = GeneratedOpenSSLInputsTests.setUp

    def fixture_overlay(self):
        from types import SimpleNamespace
        owner='SideStore/Dependencies/minimuxer';consumer=self.sources/owner
        (consumer/'DeviceGateway').mkdir(parents=True)
        (consumer/'DeviceGateway/Package.swift').write_text('explicit local dependency selection fixture')
        overlay=consumer/'GeneratedDependencies/RemotePairingKit'
        nested=json.loads((self.built/'ios-build-receipt.json').read_text())
        receipt={'source_repository':'https://github.com/mahee96/RemotePairingKit.git',
                 'source_commit':'a'*40,'source_tree':'b'*40,'adapter_sha256':'c'*64,
                 'generated_build_receipt':nested}
        def stage(built, upstream, actual_consumer):
            self.assertTrue(actual_consumer.samefile(consumer))
            overlay.mkdir(parents=True)
            (overlay/'Package.swift').write_text('synthetic source-owned overlay')
            (overlay/'generated-provenance.json').write_text(json.dumps(receipt))
            shutil.copytree(self.built/'GeneratedDependencies/OpenSSL35',overlay/'GeneratedDependencies/OpenSSL35')
        def validate(actual_consumer):
            self.assertTrue(actual_consumer.samefile(consumer))
            return overlay/'GeneratedDependencies/OpenSSL35',receipt
        installer=SimpleNamespace(stage=stage,validate_embedded=validate)
        return owner,overlay,installer

    def test_embedded_overlay_is_separate_sealed_input_and_native_payload(self):
        owner,overlay,installer=self.fixture_overlay()
        with patch.object(recorder,'generated_installer',return_value=installer), \
             patch.object(recorder,'tracked',return_value={'source':'unchanged'}), \
             patch.object(recorder,'git',return_value=recorder.OWNERS[owner][1]):
            recorded=recorder.stage_embedded_openssl(self.built,self.root/'upstream-fixture')
            self.assertIn('Package.swift',recorded['files'])
            self.assertIn('generated-provenance.json',recorded['files'])
            with patch.object(recorder,'repos',return_value={}),patch.object(recorder,'generated_openssl_snapshot',return_value={'present':False}):
                recorder.snapshot('compiler-inputs');recorder.snapshot('after-build')
            app=self.tmp/'native-SideStore/Build/Products/Release-iphoneos/SideStore.app'
            shutil.copytree(self.framework,app/'Frameworks/OpenSSL.framework')
            main=bytearray(macho(load_openssl=True));struct.pack_into('<I',main,12,2);(app/'SideStore').write_bytes(main)
            result=recorder.verify_generated_openssl_payload(embedded=True)
            self.assertEqual(result['consumer'],'SideStore')
            self.assertFalse(result['historical_gitlink_used_as_payload'])
            (overlay/'unexpected').write_text('compiler input injection')
            with self.assertRaisesRegex(RuntimeError,'stage identity/bytes drifted'):
                recorder.generated_embedded_snapshot(required=True,sealed=True)

    def test_drift_diagnostic_records_empty_directory_and_modes_without_admitting(self):
        owner,overlay,installer=self.fixture_overlay()
        with patch.object(recorder,'generated_installer',return_value=installer), \
             patch.object(recorder,'tracked',return_value={'source':'unchanged'}), \
             patch.object(recorder,'git',return_value=recorder.OWNERS[owner][1]):
            recorder.stage_embedded_openssl(self.built,self.root/'upstream-fixture')
            sealed=(self.evidence/'generated-embedded-openssl-stage.json').read_bytes()
            (overlay/'synthetic-empty-directory').mkdir()
            with self.assertRaisesRegex(RuntimeError,'stage identity/bytes drifted'):
                recorder.generated_embedded_snapshot(required=True,sealed=True)
            report=json.loads((self.evidence/'generated-embedded-openssl-stage-drift.json').read_text())
            self.assertFalse(report['accepted'])
            self.assertEqual(report['status'],'REJECTED_NOT_RESEALED')
            self.assertIsNone(report['file_changes']['synthetic-empty-directory']['expected'])
            self.assertEqual(report['file_changes']['synthetic-empty-directory']['observed']['kind'],'directory')
            (overlay/'synthetic-empty-directory').rmdir()
            file=overlay/'GeneratedDependencies/OpenSSL35/LICENSE.txt';original_mode=file.stat().st_mode&0o777;file.chmod(0o444)
            installer.validate_embedded=lambda consumer: (_ for _ in ()).throw(ValueError('adapter would reject mode'))
            with self.assertRaisesRegex(RuntimeError,'stage identity/bytes drifted'):
                recorder.generated_embedded_snapshot(required=True,sealed=True)
            report=json.loads((self.evidence/'generated-embedded-openssl-stage-drift.json').read_text())
            self.assertEqual(report['file_changes']['GeneratedDependencies/OpenSSL35/LICENSE.txt']['observed']['mode'],0o444)
            self.assertEqual((self.evidence/'generated-embedded-openssl-stage.json').read_bytes(),sealed)
            file.chmod(original_mode)
            with self.assertRaisesRegex(ValueError,'adapter would reject mode'):
                recorder.generated_embedded_snapshot(required=True,sealed=True)

    def test_native_bundle_presence_without_linked_consumer_is_rejected(self):
        GeneratedOpenSSLInputsTests.stage(self)
        GeneratedOpenSSLInputsTests.snapshot(self)
        product=GeneratedOpenSSLInputsTests.product(self)
        (product.parent/'ZSign.dylib').write_bytes(macho())
        with self.assertRaisesRegex(RuntimeError,'does not link'):
            recorder.verify_generated_openssl_payload()


if __name__=='__main__':unittest.main(verbosity=2)
