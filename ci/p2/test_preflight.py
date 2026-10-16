import importlib.util,json,plistlib,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('preflight',ROOT/'ci/p2/preflight.py'); preflight=importlib.util.module_from_spec(spec);spec.loader.exec_module(preflight)
spec=importlib.util.spec_from_file_location('rendering',ROOT/'scripts/run_p0_signin_rendering.py'); rendering=importlib.util.module_from_spec(spec);spec.loader.exec_module(rendering)
class IsolatedPreflightTests(unittest.TestCase):
 def test_permission_variants_are_in_fixture_resources_and_exact(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);host=root/'host';project=root/'P0SignIn.xcodeproj';sources=root/'Sources';project.mkdir();sources.mkdir()
   (project/'project.pbxproj').write_bytes((ROOT/'tests/fixtures/p0_signin_project/project.pbxproj').read_bytes());rendering.enable_localization_project(project/'project.pbxproj')
   for lang in preflight.LANGUAGES:
    path=host/'Resources'/(lang+'.lproj')/'InfoPlist.strings';path.parent.mkdir(parents=True);path.write_bytes(('exact-'+lang).encode())
   result=preflight.add_permission_variant(project,sources,host)
   self.assertEqual(len(result),8)
   text=(project/'project.pbxproj').read_text()
   self.assertIn('files = (F30000000000000000000002,',text)
   self.assertIn('isa = PBXVariantGroup;',text)
   for lang in preflight.LANGUAGES:
    self.assertEqual((sources/(lang+'.lproj')/'InfoPlist.strings').read_bytes(),('exact-'+lang).encode())
   with self.assertRaises((ValueError,FileExistsError)):preflight.add_permission_variant(project,sources,host)
 def test_exact_plural_values_reject_changed_missing_and_extra_categories(self):
  from p2_localization_contract import PLURAL_KEYS,LANGUAGES
  with tempfile.TemporaryDirectory() as temp:
   app=Path(temp);catalog={'strings':{}}
   for key in PLURAL_KEYS:
    catalog['strings'][key]={'localizations':{lang:{'variations':{'plural':{'one':{'stringUnit':{'value':lang+' one %lld'}},'other':{'stringUnit':{'value':lang+' other %lld'}}}}} for lang in LANGUAGES}}
   compiled={}
   for lang in LANGUAGES:
    values={key:{'NSStringLocalizedFormatKey':'%#@count@','count':{'NSStringFormatSpecTypeKey':'NSStringPluralRuleType','NSStringFormatValueTypeKey':'lld','one':lang+' one %lld','other':lang+' other %lld'}} for key in PLURAL_KEYS}
    folder=app/(lang+'.lproj');folder.mkdir();path=folder/'Localizable.stringsdict';path.write_bytes(plistlib.dumps(values));compiled[lang]=values
   self.assertEqual(preflight.verify_exact_plural_values(app,catalog)['status'],'passed')
   path=app/'zh-Hant.lproj/Localizable.stringsdict';original=path.read_bytes()
   for mutation in ['changed','missing','extra']:
    values=plistlib.loads(original);rule=values[PLURAL_KEYS[0]]['count']
    if mutation=='changed':rule['other']='wrong-language %lld'
    elif mutation=='missing':rule.pop('one')
    else:rule['two']='extra %lld'
    path.write_bytes(plistlib.dumps(values))
    self.assertEqual(preflight.verify_exact_plural_values(app,catalog)['status'],'failed',mutation)
    path.write_bytes(original)
 def test_rejected_or_partial_products_are_retained_with_exact_hashes(self):
  with tempfile.TemporaryDirectory() as temp:
   evidence=Path(temp);source=evidence/'p0-build/DerivedData/Build/Products/Debug-iphonesimulator/P0SignIn.app/he.lproj/Localizable.strings'
   source.parent.mkdir(parents=True);source.write_bytes(b'failed-resource-bytes')
   result=preflight.retain_compiled_products(evidence)
   self.assertTrue(result['available'])
   relative='Debug-iphonesimulator/P0SignIn.app/he.lproj/Localizable.strings'
   self.assertEqual((evidence/'compiled-products'/relative).read_bytes(),source.read_bytes())
   snapshot=json.loads((evidence/'compiled-products-sha256.json').read_text())
   self.assertEqual(snapshot['files'][relative],preflight.digest(source))
  with tempfile.TemporaryDirectory() as temp:
   self.assertFalse(preflight.retain_compiled_products(Path(temp))['available'])
 def test_exactly_three_required_native_tests_and_no_ui_execution(self):
  self.assertEqual(len(preflight.NATIVE_IDS),3)
  source=(ROOT/'ci/p2/preflight.py').read_text()
  self.assertNotIn('test-without-building',source)
  self.assertNotIn('simctl',source)
  self.assertIn("result.testsRun==35",source)
  self.assertIn("not result.skipped",source)
  self.assertIn("'actualHostPackaging':False",source)
 def test_workflow_is_isolated_read_only_and_does_not_cancel_other_runs(self):
  text=(ROOT/'.github/workflows/p2-localization-preflight.yml').read_text()
  self.assertIn('branches: [validation/p2-localization-preflight]',text)
  self.assertIn('cancel-in-progress: false',text)
  self.assertIn('contents: read',text)
  self.assertIn('persist-credentials: false',text)
  self.assertNotIn('secrets.',text)
  self.assertIn(preflight.CHECKPOINT,text)
if __name__=='__main__':unittest.main()
