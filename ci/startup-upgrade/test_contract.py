import importlib.util,os,pathlib,tempfile,unittest,xml.etree.ElementTree as ET
ROOT=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('qualify',ROOT/'run.py');q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
SOURCE=pathlib.Path(os.environ.get('SIDESTORE_SOURCE',str(ROOT.parent/'SideStore'))).resolve(strict=True)
class ContractTests(unittest.TestCase):
    def test_actual_schema_is_only_optional_fingerprint(self):q.schema_delta(SOURCE)
    def test_actual_production_mapping_methods_are_unmodified(self):
        methods=q.mapping_methods(SOURCE/q.PERSISTENT)
        self.assertIn('NSMappingModel.inferredMappingModel(forSourceModel: sourceModel, destinationModel: destinationModel)',methods)
        self.assertIn('NSMappingModel(from: Bundle.allBundles',methods)
        self.assertNotIn('replacePersistentStore',methods)
        self.assertEqual((ROOT/'fixture.swift').read_text().count('// EXACT_PRODUCTION_MAPPING_METHODS'),1)
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
    def test_framework_counterfactual_does_not_require_hypothesis(self):
        text=(ROOT/'discovery.swift').read_text()
        self.assertIn('Bundle.allBundles',text);self.assertIn('Bundle(for: anchor)',text)
        self.assertIn('physical_lc_bootstrap_executed": false',text)
        self.assertIn("[('FMWK','resources'),('APPL','flat')]",(ROOT/'run.py').read_text())
    def test_source_inventories_detect_payload_and_mode_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            p=pathlib.Path(directory);f=p/'db';f.write_bytes(b'fixture')
            before=q.inventory(p);f.write_bytes(b'changed');self.assertNotEqual(before,q.inventory(p))
            f.write_bytes(b'fixture');f.chmod(0o400);self.assertNotEqual(before,q.inventory(p))
if __name__=='__main__':unittest.main()
