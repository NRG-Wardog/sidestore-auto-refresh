import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import build
import build_ios
import install_ios

class IOSRecipeTests(unittest.TestCase):
    def test_actual_platform_and_minos_fail_closed(self):
        build_ios.validate_macho(' platform IOS\n minos 15.0\n','IOS')
        for text in [' platform MACOS\n minos 15.0\n',' platform IOS\n minos 12.0\n','']:
            with self.assertRaises(ValueError):build_ios.validate_macho(text,'IOS')

    def test_simulator_headers_preserve_arch_specific_generated_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();a=root/'a';b=root/'b';a.mkdir();b.mkdir()
            for directory in [a,b]:(directory/'same.h').write_text('same')
            (a/'configuration.h').write_text('arm body');(b/'configuration.h').write_text('x86 body')
            changed=build_ios.merge_headers(a,b,root/'out')
            self.assertEqual(changed,['configuration.h'])
            self.assertEqual((root/'out/arch/arm64/configuration.h').read_text(),'arm body')
            self.assertEqual((root/'out/arch/x86_64/configuration.h').read_text(),'x86 body')
            self.assertIn('#error Unsupported',(root/'out/configuration.h').read_text())
            (b/'extra.h').write_text('extra')
            with self.assertRaisesRegex(ValueError,'sets differ'):build_ios.merge_headers(a,b,root/'bad')

    def test_platform_gate_never_executes_on_linux(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(build_ios.platform,'system',return_value='Linux'),patch.object(build,'command') as command:
            with self.assertRaisesRegex(ValueError,'macOS'):build_ios.generate(Path(temp)/'out')
            command.assert_not_called()

    def test_installer_rejects_source_or_recipe_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            (root/'ios-build-receipt.json').write_text(json.dumps({'schema':'issue53-ios-generated-dependency-v1','status':'BUILT_COMPILE_LINK_CHECKED_NOT_RUNTIME_QUALIFIED'}))
            with self.assertRaisesRegex(ValueError,'source/recipe'):install_ios.validate(root)

    def test_installer_requires_complete_bytes_and_explicit_consumer(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();built=root/'built';built.mkdir()
            generated=built/'GeneratedDependencies/OpenSSL35';generated.mkdir(parents=True)
            (generated/'fixture').write_bytes(b'synthetic output, never executed')
            receipt={'schema':'issue53-ios-generated-dependency-v1','status':'BUILT_COMPILE_LINK_CHECKED_NOT_RUNTIME_QUALIFIED',
                'source':json.loads((build_ios.HERE/'source.json').read_text()),
                'recipe_sha256':build.digest(build_ios.HERE/'build_ios.py'),'shared_recipe_sha256':build.digest(build_ios.HERE/'build.py'),
                'device_executed':False,'simulator_executed':False,'production_admission':False,'old_gitlink_overwritten':False,
                'slices':{key:{} for key in build_ios.SLICES},'merged_simulator_module_probe_sha256':{'arm64':'0'*64,'x86_64':'0'*64},
                'xcframework_relative_path':'GeneratedDependencies/OpenSSL35/OpenSSL.xcframework','generated_inventory':build.inventory(generated)}
            (built/'ios-build-receipt.json').write_text(json.dumps(receipt))
            consumer=root/'consumer';project=consumer/'LiveContainer.xcodeproj';project.mkdir(parents=True)
            (project/'project.pbxproj').write_text('old dependency')
            with self.assertRaisesRegex(ValueError,'explicitly selected'):install_ios.install(built,consumer)
            (project/'project.pbxproj').write_text('path = GeneratedDependencies/OpenSSL35/OpenSSL.xcframework;')
            complete=receipt['slices'].copy();receipt['slices'].pop('sim-x86_64')
            (built/'ios-build-receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError,'closure'):install_ios.validate(built)
            receipt['slices']=complete;(built/'ios-build-receipt.json').write_text(json.dumps(receipt))
            copied=install_ios.install(built,consumer)
            self.assertEqual((copied/'fixture').read_bytes(),(generated/'fixture').read_bytes())
            self.assertTrue((copied/'provenance.json').is_file())
            with self.assertRaisesRegex(ValueError,'existing consumer'):install_ios.install(built,consumer)
            (generated/'unexpected').write_bytes(b'drift')
            with self.assertRaisesRegex(ValueError,'bytes differ'):install_ios.validate(built)

    def test_generated_inventory_is_complete_and_no_legacy_path_mutation(self):
        source=(build_ios.HERE/'build_ios.py').read_text();installer=(build_ios.HERE/'install_ios.py').read_text()
        self.assertIn("build.inventory(generated)",source)
        self.assertIn("build.inventory(target)!=receipt['generated_inventory']",installer)
        self.assertNotIn('OpenSSL/Frameworks',installer)
        self.assertIn("'old_gitlink_overwritten':False",source)
        self.assertIn("'no-module','enable-legacy'",source)
        self.assertEqual(set(build_ios.SLICES),{'ios-arm64','sim-arm64','sim-x86_64'})

if __name__=='__main__':unittest.main()
