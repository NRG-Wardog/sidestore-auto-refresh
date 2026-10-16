import importlib.util,json,plistlib,re,sys,tempfile,unittest,shutil,os
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'scripts'))
import run_p0_signin_rendering as renderer
import p2_localization_contract as contract
HOST=Path(os.environ['MAINTAINED_LIVE_CONTAINER_TEST_SOURCE']) if os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE') else None
P2_AVAILABLE=bool(HOST and (HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').is_file())
if os.environ.get('V3_P2_REQUIRED') == '1' and not P2_AVAILABLE:
 raise RuntimeError('Required P2 capability needs an explicit maintained source with localization helpers')
CONTRACTS=R/'tests/fixtures/p2_localization_contracts'
@unittest.skipUnless(P2_AVAILABLE, 'not_applicable: historical/pre-P2 source; no P2 proof claimed')
class ReconstructedP2Tests(unittest.TestCase):
 def test_host_preference_does_not_reset_auth_identity(self):
  shell=(HOST/'LiveContainerSwiftUI/Views/V3UnifiedShell.swift').read_text()
  root=renderer.declaration(shell,'struct V3UnifiedShell')
  self.assertIn('@AppStorage(V3AppLanguage.storageKey, store: V3Localization.hostDefaults)',root)
  self.assertIn('.environment(\\.locale',root);self.assertIn('.environment(\\.layoutDirection',root)
  self.assertNotIn('.id(',root)
  helper=(HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text()
  self.assertNotIn('forKey: "AppleLanguages"',helper)
  self.assertNotIn('appGroupUserDefault',helper)
 def test_native_formatting_keeps_argument_array(self):
  shared=(HOST/'LiveContainerSwiftUI/Utilities/Shared.swift').read_text()
  self.assertIn('arguments: arguments',renderer.member(shared,'func localizeWithFormat'))
  self.assertIn('V3AppLanguage.selected.rawValue',renderer.member(shared,'func localizeWithFormat'))
 def test_actual_catalog_is_in_host_resource_membership(self):
  project=(HOST/'LiveContainer.xcodeproj/project.pbxproj').read_text()
  def obj(identifier):
   match=re.search(r'\b'+identifier+r'[^\n=]*=\s*\{(.*?)\};',project,re.S)
   self.assertIsNotNone(match,identifier)
   return match.group(1)
  host=obj('17DCE99C2C7067EC00731D42')
  self.assertIn('17DCE99B2C7067EC00731D42',host)
  phase=obj('17DCE99B2C7067EC00731D42')
  self.assertIn('isa = PBXResourcesBuildPhase',phase)
  for build,reference in [('A17ECAFE2DCA00000000F001','A17ECAFE2DCA00000000F003'),('A17ECAFE2DCA00000000F002','A17ECAFE2DCA00000000F007')]:
   self.assertIn(build,phase)
   self.assertIn('isa = PBXBuildFile',obj(build))
   self.assertIn('fileRef = '+reference,obj(build))
  self.assertIn('path = Resources/Localizable.xcstrings',obj('A17ECAFE2DCA00000000F003'))
  self.assertIn('lastKnownFileType = text.json.xcstrings',obj('A17ECAFE2DCA00000000F003'))
  variant=obj('A17ECAFE2DCA00000000F007')
  self.assertIn('isa = PBXVariantGroup',variant)
  self.assertIn('name = InfoPlist.strings',variant)
  for reference,lang in [('A17ECAFE2DCA00000000F004','en'),('A17ECAFE2DCA00000000F005','he'),('A17ECAFE2DCA00000000F006','zh-Hans'),('A17ECAFE2DCA00000000F009','zh_CN'),('A17ECAFE2DCA00000000F00A','zh-Hant'),('A17ECAFE2DCA00000000F00B','zh_TW'),('A17ECAFE2DCA00000000F00C','zh_HK')]:
   self.assertIn(reference,variant)
   self.assertIn('Resources/'+lang+'.lproj/InfoPlist.strings',obj(reference))
   self.assertIn('lastKnownFileType = text.plist.strings',obj(reference))
   self.assertIn('sourceTree = SOURCE_ROOT',obj(reference))
  # Explicit host build resources above establish membership. Absence from a
  # non-owning synchronized group's exception set does not establish inclusion.
  self.assertNotIn('fileSystemSynchronizedGroups',host)
 def test_historical_fixture_matrix_unchanged(self):
  before='func testCredentialsDefault( func testCredentialsLargest( func testSubmittingDefault( func testSubmittingLargest( screenshot("prompt-top") screenshot("copy-details") screenshot("cancel-reachable")'
  after=(R/'tests/fixtures/p0_signin_ui_tests.swift').read_text()
  for pattern in [r'func (test\w+)\(',r'screenshot\("([^"]+)"\)']:
   self.assertEqual(re.findall(pattern,before),re.findall(pattern,after))
  self.assertEqual(len(re.findall(r'func test\w+\(',after)),4)
  self.assertEqual(len(re.findall(r'screenshot\("',after)),3)
 def test_live_picker_retains_auth_and_raw_clipboard(self):
  app=(R/'tests/fixtures/p0_signin_app.swift').read_text();ui=(R/'tests/fixtures/p0_signin_ui_tests.swift').read_text()
  self.assertIn('V3LanguageSettingsSection().pickerStyle(.inline)',app)
  self.assertIn('for next in ["en", "he", "zh-Hans", "zh-Hant", target]',ui)
  for proof in ['Language switch reset username','Language switch reset secure field','Language switch dispatched auth action','Language switch replaced auth state']:
   self.assertIn(proof,ui)
  self.assertIn('value as? String == expected',ui)
  self.assertIn('first.frame.midX > last.frame.midX',ui)
  self.assertIn('key.localizeWithFormat(Int64(count))',app)
 def test_pbx_enables_exact_helpers_and_native_catalog(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'project.pbxproj';shutil.copyfile(R/'tests/fixtures/p0_signin_project/project.pbxproj',p)
   renderer.enable_localization_project(p);s=p.read_text()
   self.assertEqual(s.count('P2_LOCALIZATION'),6)
   for name in ['V3Localization.swift','V3LanguageSettings.swift','LocalizedStringFormatting.swift','Localizable.xcstrings']:
    self.assertEqual(s.count('path = '+name+';'),1)
   definitions=re.findall(r'^([A-F0-9]+) =',s,re.M)
   self.assertEqual(len(definitions),len(set(definitions)))
   self.assertTrue(all(len(i)==24 for i in definitions))
 def test_compiled_resources_reject_missing_and_malformed_plural(self):
  self.assertEqual(contract.verify_compiled_resources(lambda n:b'',set())['status'],'failed')
  data={}
  for language in contract.LANGUAGES:
   data[f'{language}.lproj/Localizable.strings']=plistlib.dumps(contract.REQUIRED_TRANSLATIONS[language])
   data[f'{language}.lproj/Localizable.stringsdict']=plistlib.dumps({k:{'NSStringLocalizedFormatKey':'%#@count@','count':{'NSStringFormatSpecTypeKey':'NSStringPluralRuleType','NSStringFormatValueTypeKey':'lld','other':'%lld'}} for k in contract.PLURAL_KEYS} | contract.COMPOUND_PLURALS[language])
  self.assertEqual(contract.verify_compiled_resources(data.__getitem__,set(data))['status'],'passed')
  for language in contract.LANGUAGES:
   path=f'{language}.lproj/Localizable.strings';saved=data[path]
   for key in ['Some details are unavailable in the selected language.','Append Team ID','Background processing submission failed','Install %@']:
    for mutation in ['missing','wrongLanguage','wrongType']:
     changed=plistlib.loads(saved)
     if mutation=='missing':del changed[key]
     elif mutation=='wrongLanguage':changed[key]='incorrect translation'
     else:changed[key]=17
     data[path]=plistlib.dumps(changed)
     self.assertEqual(contract.verify_compiled_resources(data.__getitem__,set(data))['status'],'failed',(language,key,mutation))
   data[path]=saved
  original=plistlib.loads(data['he.lproj/Localizable.stringsdict'])
  for mutation in ['missingRule','missingOther','badType','rawMetadata','undeclaredPlaceholder','extraArgument','wrongPosition','writeToken','outerWriteToken']:
   changed=json.loads(json.dumps(original));row=changed[contract.PLURAL_KEYS[0]]
   if mutation=='missingRule':del row['count']
   elif mutation=='missingOther':del row['count']['other']
   elif mutation=='badType':row['count']['NSStringFormatValueTypeKey']='@'
   elif mutation=='rawMetadata':row['count']['other']='%#@unresolved@'
   elif mutation=='extraArgument':row['count']['other']='%lld %@'
   elif mutation=='wrongPosition':row['count']['other']='%2$lld'
   elif mutation=='writeToken':row['count']['other']='%lld %n'
   elif mutation=='outerWriteToken':row['NSStringLocalizedFormatKey']='%#@count@ %n'
   else:row['NSStringLocalizedFormatKey']='%#@notDeclared@'
   data['he.lproj/Localizable.stringsdict']=plistlib.dumps(changed)
   self.assertEqual(contract.verify_compiled_resources(data.__getitem__,set(data))['status'],'failed',mutation)
  data['he.lproj/Localizable.stringsdict']=plistlib.dumps(original)
  self.assertEqual(contract.verify_compiled_resources(data.__getitem__,set(data))['status'],'passed')
  compound_key=contract.COMPOUND_PLURAL_KEYS[0]
  for language in contract.LANGUAGES:
   name=language+'.lproj/Localizable.stringsdict';saved=data[name]
   # Restore the simple plural fixture before independently mutating compound data.
   base={k:{'NSStringLocalizedFormatKey':'%#@count@','count':{'NSStringFormatSpecTypeKey':'NSStringPluralRuleType','NSStringFormatValueTypeKey':'lld','other':'%lld'}} for k in contract.PLURAL_KEYS} | contract.COMPOUND_PLURALS[language]
   for mutation in ['missingRow','missingArg','extraArg','swappedArgs','wrongNumber','wrongType','changedValue','missingCategory','extraCategory','outerFormat']:
    changed=json.loads(json.dumps(base));row=changed[compound_key]
    if mutation=='missingRow':del changed[compound_key]
    elif mutation=='missingArg':del row['arg2']
    elif mutation=='extraArg':row['arg3']=row['arg1']
    elif mutation=='swappedArgs':row['arg1'],row['arg2']=row['arg2'],row['arg1']
    elif mutation=='wrongNumber':row['arg2']['other']=row['arg2']['other'].replace('%2$lld','%1$lld')
    elif mutation=='wrongType':row['arg2']['NSStringFormatValueTypeKey']='@'
    elif mutation=='changedValue':row['arg1']['other']='wrong language %1$lld'
    elif mutation=='missingCategory':del row['arg1']['other']
    elif mutation=='extraCategory':row['arg1']['few']='extra %1$lld'
    else:row['NSStringLocalizedFormatKey']+=' %n'
    data[name]=plistlib.dumps(changed)
    self.assertEqual(contract.verify_compiled_resources(data.__getitem__,set(data))['status'],'failed',(language,mutation))
   data[name]=plistlib.dumps(base)
   self.assertEqual(contract.verify_compiled_resources(data.__getitem__,set(data))['status'],'passed',language)
   scalar=language+'.lproj/Localizable.strings';values=plistlib.loads(data[scalar]);values[compound_key]='duplicate scalar'
   data[scalar]=plistlib.dumps(values)
   self.assertEqual(contract.verify_compiled_resources(data.__getitem__,set(data))['status'],'failed',(language,'duplicateScalar'))
   data[scalar]=plistlib.dumps(contract.REQUIRED_TRANSLATIONS[language])
  data['he.lproj/Localizable.stringsdict']=plistlib.dumps({'bad':'raw'})
  self.assertEqual(contract.verify_compiled_resources(data.__getitem__,set(data))['status'],'failed')
 def test_p2_evidence_fails_without_runtime_locale_proof(self):
  spec=importlib.util.spec_from_file_location('historical_evidence_tests',R/'tests/test_p0_signin_rendering.py')
  original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);summary=original.P0SignInRenderingEvidenceTests().make_export(root)
   self.assertTrue(renderer.verify_export(root,summary)['passed'])
   self.assertFalse(renderer.verify_export(root,summary,localization=True)['passed'])
   for p in root.glob('*.json'):
    data=json.loads(p.read_text())
    if not isinstance(data,dict) or 'case' not in data:continue
    case=data['case'];language='he' if case.endswith('-largest') else ('zh-Hans' if case.startswith('submitting') else 'en');data.update(language=language,layoutDirectionVerified=True,pluralRuntimeVerified=True,languageSwitchVerified=case=='submitting-default',verifiedLanguages=['en','he','zh-Hans','zh-Hant'] if case=='submitting-default' else [language])
    data['persistenceObservations']=[dict(language=lang,launchArguments=[],selectedThroughSettingsPicker=True,terminatedAndRelaunched=True,selectionRetained=True,renderedLocaleVerified=True,layoutDirectionVerified=True,pluralRuntimeVerified=True) for lang in ['en','he','zh-Hans','zh-Hant']] if case=='submitting-default' else []
    p.write_text(json.dumps(data))
   self.assertTrue(renderer.verify_export(root,summary,localization=True)['passed'])
   p=root/'submitting-default.json';saved=json.loads(p.read_text())
   for key,value in [('language','en'),('layoutDirectionVerified',False),('pluralRuntimeVerified',False),('languageSwitchVerified',False),('persistenceObservations',[]),('persistenceObservations',None),('verifiedLanguages',['en','he','zh-Hans']),('verifiedLanguages',['en','he','zh-Hans','zh-Hans']),('verifiedLanguages',None),('verifiedLanguages',['en','he','zh-Hans','zh-Hant','ru'])]:
    changed=dict(saved);changed[key]=value;p.write_text(json.dumps(changed))
    self.assertFalse(renderer.verify_export(root,summary,localization=True)['passed'],key)
   for key,value in [('language','ru'),('launchArguments',['--language','he']),('selectedThroughSettingsPicker',False),('terminatedAndRelaunched',False),('selectionRetained',False),('renderedLocaleVerified',False),('layoutDirectionVerified',False),('pluralRuntimeVerified',False)]:
    changed=json.loads(json.dumps(saved));changed['persistenceObservations'][1][key]=value;p.write_text(json.dumps(changed))
    self.assertFalse(renderer.verify_export(root,summary,localization=True)['passed'],key)
   p.write_text(json.dumps(saved))
 def test_fixture_catalog_matches_independent_expectations(self):
  catalog=json.loads((HOST/'Resources/Localizable.xcstrings').read_text())['strings']
  expected=json.loads((CONTRACTS/'fixture-translation-expectations.json').read_text())
  for key,values in expected.items():
   for lang,index in [('zh-Hans',0),('he',1),('zh-Hant',2)]:
    self.assertEqual(catalog[key]['localizations'][lang]['stringUnit']['value'],values[index],(key,lang))
  for key in contract.PLURAL_KEYS:
   for lang in contract.LANGUAGES:
    self.assertIn('plural',catalog[key]['localizations'][lang]['variations'])
 def test_exact_maintained_extraction_requires_complete_localization(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);shutil.copytree(HOST,p/'host')
   shell=p/'host/LiveContainerSwiftUI/Views/V3UnifiedShell.swift'
   (p/'host/Resources/Localizable.xcstrings').unlink(missing_ok=True)
   with self.assertRaisesRegex(ValueError,'exact maintained helpers'):
    renderer.extract_sources(shell,p/'unused')
   target=p/'host/Resources/Localizable.xcstrings';target.parent.mkdir(exist_ok=True);shutil.copyfile(HOST/'Resources/Localizable.xcstrings',target)
   extracted,hashes=renderer.extract_sources(shell,p/'unused')
   self.assertEqual(extracted['V3Localization.swift'],(HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text())
   self.assertIn('maintained-localization/Shared.swift',hashes)
   self.assertIn('arguments: arguments',extracted['LocalizedStringFormatting.swift'])
if __name__=='__main__':unittest.main()
