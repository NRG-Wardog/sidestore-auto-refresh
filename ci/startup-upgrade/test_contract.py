import importlib.util,os,pathlib,tempfile,unittest,xml.etree.ElementTree as ET
ROOT=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('qualify',ROOT/'run.py');q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
SOURCE=pathlib.Path(os.environ.get('SIDESTORE_SOURCE',str(ROOT.parent/'SideStore'))).resolve(strict=True)
class ContractTests(unittest.TestCase):
    def test_actual_schema_is_only_optional_fingerprint(self):q.schema_delta(SOURCE)
    def test_actual_production_mapping_methods_are_unmodified(self):
        methods=q.mapping_methods(SOURCE/q.PERSISTENT)
        self.assertIn('NSMappingModel.inferredMappingModel(forSourceModel: sourceModel, destinationModel: destinationModel)',methods)
        self.assertIn('NSMappingModel(from: self.modelLookupBundles()',methods)
        self.assertNotIn('replacePersistentStore',methods)
        self.assertEqual((ROOT/'fixture.swift').read_text().count('// EXACT_PRODUCTION_MAPPING_METHODS'),1)
    def test_discovery_executes_exact_owned_helper(self):
        methods=q.mapping_methods(SOURCE/q.PERSISTENT)
        self.assertTrue(methods.startswith('    private func sourceModel('))
        self.assertIn('private func modelLookupBundles()',methods)
        self.assertEqual((ROOT/'discovery.swift').read_text().count('// EXACT_PRODUCTION_MAPPING_METHODS'),1)
        text=(ROOT/'run.py').read_text()
        self.assertIn("discovery_source.replace('// EXACT_PRODUCTION_MAPPING_METHODS',methods)",text)
        self.assertIn("models/'AltStore 17_7.mom'",text)
    def test_models_are_original_all_versions(self):
        text=(ROOT/'run.py').read_text();self.assertIn("source/MODEL,models",text)
        self.assertNotIn('modelXML.replace',text)
        self.assertIn("'source_sha256':source_hashes",text)
    def test_source_output_overlap_is_rejected(self):
        with self.assertRaises(ValueError):q.validate_layout(SOURCE,SOURCE/'fixture-output')
        with self.assertRaises(ValueError):q.validate_layout(SOURCE,SOURCE.parent)
    def test_live_copy_not_claimed(self):
        text=(ROOT/'run.py').read_text()
        self.assertLess(text.index("'seed-'+journal"),text.index('shutil.copytree(original,scratch)'))
        self.assertIn("'production_relocation_enabled':False",text)
        self.assertIn('No legacy-writer lock exclusion proof.',text)
    def test_destination_conflict_and_readonly_source(self):
        text=(ROOT/'fixture.swift').read_text()
        self.assertIn('for suffix in ["", "-wal", "-shm"]',text)
        self.assertIn('NSReadOnlyPersistentStoreOption: true',text)
        self.assertNotIn('replacePersistentStore',text)
        self.assertNotIn('removeItem',text)
    def test_framework_counterfactual_preserves_physical_scope_limit(self):
        text=(ROOT/'discovery.swift').read_text()
        self.assertIn('Bundle.allBundles',text);self.assertIn('Bundle(for: anchor)',text)
        self.assertIn('physical_lc_bootstrap_executed": false',text)
        self.assertIn("[('FMWK','resources'),('APPL','flat')]",(ROOT/'run.py').read_text())
    def negative_result(self,mode):
        return {'schema':'coredata-isolated-helper-negative-v1','mode':mode,
                **{k:True for k in ('main_bundle_is_isolated_host','owner_is_requested_framework','current_model_is_v17_7')},
                **{k:False for k in ('source_model_found_before_framework_load','baseline_source_model_found','current_model_matches_old_metadata','production_source_model_found','physical_lc_bootstrap_executed')},
                'main_bundle_model_resources_before':0,'main_bundle_model_resources_after':0}
    def test_negative_processes_are_independent_and_strict(self):
        for mode in ('nil-owner','wrong-version','unknown'):
            q.verify_negative(self.negative_result(mode),mode)
            with self.assertRaises(ValueError):q.verify_negative(dict(self.negative_result(mode),production_source_model_found=True),mode)
        text=(ROOT/'discovery.swift').read_text()
        self.assertLess(text.index('if mode != "owned"'),text.index('let explicit ='))
        self.assertIn('return\n    }\n    let explicit',text)
    def discovery_result(self,baseline=False):
        return {'isolated_negative_processes':{m:self.negative_result(m) for m in ('nil-owner','wrong-version','unknown')},'schema':'coredata-owned-framework-discovery-v3','main_bundle_is_isolated_host':True,
                'main_bundle_model_resources_before':0,'main_bundle_model_resources_after':0,
                'source_model_found_before_framework_load':False,'unknown_source_model_found_in_explicit_owner':False,
                'owner_is_requested_framework':True,'main_is_requested_framework':False,
                'explicit_owner_source_model_found':True,'explicit_owner_model_compatible':True,
                'explicit_supplied_source_model_found':True,'explicit_supplied_model_compatible':True,
                'baseline_source_model_found':baseline,'baseline_model_compatible':baseline,
                **{k:True for k in ('production_owned_source_model_found','production_owned_model_compatible','production_mapping_selected','production_owned_model_urls_include_source_and_target','production_wrong_version_bundle_exposes_current_model')},
                **{k:False for k in ('production_nil_owner_source_model_found','production_unknown_source_model_found','production_wrong_version_source_model_found')}}
    def test_discovery_requires_real_isolation_not_desired_result(self):
        q.verify_discovery(self.discovery_result(False))
        with self.assertRaises(ValueError):q.verify_discovery(self.discovery_result(True))
        for key,value in [('main_bundle_is_isolated_host',False),('main_bundle_model_resources_before',1),
                          ('main_bundle_model_resources_after',1),('source_model_found_before_framework_load',True),
                          ('unknown_source_model_found_in_explicit_owner',True),('owner_is_requested_framework',False),
                          ('main_is_requested_framework',True),('explicit_owner_model_compatible',False),
                          ('production_owned_model_compatible',False),('production_mapping_selected',False),
                          ('production_unknown_source_model_found',True),('production_wrong_version_source_model_found',True)]:
            with self.subTest(key=key),self.assertRaises(ValueError):q.verify_discovery(dict(self.discovery_result(),**{key:value}))
    def test_discovery_executable_and_models_not_siblings(self):
        text=(ROOT/'run.py').read_text()
        self.assertIn("discovery=host/'Contents/MacOS/discovery'",text)
        self.assertNotIn("discovery=output/'discovery'",text)
        self.assertIn("(host/'Contents/Resources').mkdir()",text)
        self.assertIn("framework=output/(package+'-'+layout)",text)
    def test_discovery_observes_actual_main_resources_and_preload_negative(self):
        text=(ROOT/'discovery.swift').read_text()
        self.assertIn('FileManager.default.enumerator(at: root',text)
        self.assertIn('if enumerationFailed { throw NSError',text)
        self.assertNotIn('else { return 0 }',text)
        self.assertLess(text.index('let beforeFramework ='),text.index('dlopen(executable.path'))
        self.assertIn('Bundle.main.bundleURL.resolvingSymlinksInPath() == isolatedHost',text)
        self.assertIn('forStoreMetadata: unknownMetadata',text)
    def test_source_inventories_detect_payload_and_mode_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            p=pathlib.Path(directory);f=p/'db';f.write_bytes(b'fixture')
            before=q.inventory(p);f.write_bytes(b'changed');self.assertNotEqual(before,q.inventory(p))
            f.write_bytes(b'fixture');f.chmod(0o400);self.assertNotEqual(before,q.inventory(p))
if __name__=='__main__':unittest.main()
