import inspect
import json
from pathlib import Path
import sys
import unittest
import tempfile
import command_evidence

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]/'tests'))
import measure
from test_macho_stab_guard import fixture


class SizeExperimentTests(unittest.TestCase):
    def receipt(self):
        root=HERE.parent/'issue53-lts'
        return {'schema':'issue53-ios-generated-dependency-v1',
            'status':'BUILT_COMPILE_LINK_CHECKED_NOT_RUNTIME_QUALIFIED',
            'source':json.loads((root/'source.json').read_text()),
            'recipe_sha256':measure.build.digest(root/'build_ios.py'),
            'shared_recipe_sha256':measure.build.digest(root/'build.py'),
            'slices':dict.fromkeys(('ios-arm64','sim-arm64','sim-x86_64'),{}),
            'merged_simulator_module_probe_sha256':dict.fromkeys(('arm64','x86_64'),'fixture'),
            'xcframework_relative_path':'GeneratedDependencies/OpenSSL35/OpenSSL.xcframework',
            **dict.fromkeys(('device_executed','simulator_executed','production_admission','old_gitlink_overwritten'),False)}

    def test_default_optimization_and_finite_explicit_mode(self):
        self.assertEqual(inspect.signature(measure.build.build).parameters['optimization'].default,'O2')
        self.assertEqual(inspect.signature(measure.build_ios.generate).parameters['optimization'].default,'O2')
        self.assertEqual(measure.build.optimization_flag('Os'),'-Os')
        for value in ('Oz','O3','Os -DREMOVE_PROVIDER',None):
            with self.assertRaises(ValueError):measure.build.optimization_flag(value)

    def test_experiment_cannot_be_installed_by_default(self):
        receipt=self.receipt();measure.install_ios.validate_receipt(receipt)
        receipt['optimization']='Os'
        with self.assertRaisesRegex(ValueError,'not admitted'):measure.install_ios.validate_receipt(receipt)
        measure.install_ios.validate_receipt(receipt,allow_size_experiment=True)
        receipt['optimization']='Oz'
        with self.assertRaises(ValueError):measure.install_ios.validate_receipt(receipt,allow_size_experiment=True)

    def test_explicit_experiment_does_not_bypass_source_policy(self):
        receipt=self.receipt();receipt.update(optimization='Os',shared_recipe_sha256='wrong')
        with self.assertRaisesRegex(ValueError,'source/recipe'):
            measure.install_ios.validate_receipt(receipt,allow_size_experiment=True)

    def test_defined_external_api_binding_and_actual_sizes(self):
        data=fixture(False);names=measure.external_names(data)
        baseline={'defined_external_names_count':names['count'],'defined_external_names_sha256':names['sha256'],
                  'raw_bytes_per_copy':len(data)+20,'deflate9_bytes_per_copy':measure.deflated(data)+10}
        report=measure.compare(data,baseline)
        self.assertEqual(report['deflate9_bytes_saved_two_separate_copies'],20)
        self.assertFalse(report['final_app_ipa_size_measured'])
        with self.assertRaisesRegex(ValueError,'API closure'):measure.compare(data.replace(b'_public',b'_PubLic'),baseline)
        with self.assertRaises(ValueError):measure.compare(b'not Mach-O',baseline)

    def test_baseline_exact_recognized_current_ipa(self):
        value=json.loads((HERE/'baseline.json').read_text())
        self.assertEqual(value['run_id'],'40229079312')
        self.assertEqual(value['binary_sha256'],'d490bc3e908c6e5f6935b030eb340e1b16dd2a62541b95fb440a8a44f5a88ce8')
        self.assertEqual(value['zip_entry_compressed_bytes_per_copy'],[value['deflate9_bytes_per_copy']]*2)
        self.assertEqual(len(value['copies']),2)

    def test_recipe_keeps_provider_arch_and_debug_policy(self):
        for filename in ('build.py','build_ios.py'):
            text=(HERE.parent/'issue53-lts'/filename).read_text()
            for token in ('no-module','enable-legacy','no-asm',' -g -fPIC '):self.assertIn(token,text)
            self.assertIn("'optimization':optimization",text)
        self.assertEqual(set(measure.build_ios.SLICES),{'ios-arm64','sim-arm64','sim-x86_64'})

    def test_actual_compile_and_config_final_flag_controls(self):
        for tokens in (['-Os','-O2'],['-Os','-O'],['-Os','-fprofile-arcs'],
                       ['-Os','-DOPENSSL_NO_AES'],['-Os','-DSTATIC_LEGACY=0'],['-Os','-UOPENSSL_PIC']):
            with self.assertRaises(ValueError):command_evidence.check_flags(tokens)
        command_evidence.check_flags(['-O3','-Os','-g','-fPIC'])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);log=root/'build.log';config=root/'config.txt'
            config.write_text(' CFLAGS => "-O3"\n CFLAGS = -Os -g -fPIC\n')
            log.write_text('/known/clang -Os -DSTATIC_LEGACY -c providers/legacyprov.c\n'
                           '/known/clang -Os -c providers/defltprov.c\n')
            result=command_evidence.verify(log,{'macOS':config},'/known/clang',{'macOS':None})
            self.assertEqual(result['slices']['macOS']['actual_compile_commands'],2)
            log.write_text(log.read_text().replace('-Os','-Os -O2'))
            with self.assertRaisesRegex(ValueError,'final optimization'):
                command_evidence.verify(log,{'macOS':config},'/known/clang',{'macOS':None})

    def test_missing_provider_and_mismatched_slice_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);log=root/'build.log';config=root/'config.txt'
            config.write_text('CFLAGS = -Os -g\n')
            log.write_text('/known/clang -Os -c crypto/test.c\n')
            with self.assertRaisesRegex(ValueError,'Provider'):
                command_evidence.verify(log,{'macOS':config},'/known/clang',{'macOS':None})
            with self.assertRaisesRegex(ValueError,'architecture'):
                command_evidence.verify(log,{'ios':config},'/known/clang',{'ios':'arm64-apple-ios15.0'})

    def test_dedicated_workflow_has_no_app_build_or_promotion(self):
        text=(HERE.parents[1]/'.github/workflows/issue55-openssl-size.yml').read_text()
        self.assertIn("branches: ['test/issue55-openssl-size']",text)
        self.assertIn('contents: read',text)
        self.assertNotIn('actions: read',text)
        self.assertEqual(text.count('--optimization Os'),2)
        self.assertIn('ci/issue53-lts/qualify.py',text)
        self.assertNotIn('package_candidate.py',text)
        self.assertNotIn('install_ios.py',text)
        self.assertIn("steps.measurement.outcome == 'success'",text)

if __name__=='__main__':unittest.main()
