from pathlib import Path
import json,os,plistlib,re,sys,unittest
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'scripts'))
import p2_permission_contract as contract
HOST=Path(os.environ['MAINTAINED_LIVE_CONTAINER_TEST_SOURCE']) if os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE') else None
AVAILABLE=bool(HOST and (HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').is_file())
if os.environ.get('V3_P2_REQUIRED')=='1' and not AVAILABLE:raise RuntimeError('Required P2 permission resources need the selected maintained source')
def read_strings(path):
 result={}
 for line in path.read_text().splitlines():
  if line.startswith('/*'):continue
  match=re.fullmatch(r'("(?:[^"\\]|\\.)*") = ("(?:[^"\\]|\\.)*");',line)
  if not match:raise ValueError('Invalid authored strings entry')
  key=json.loads(match[1]);value=json.loads(match[2])
  if key in result:raise ValueError('Duplicate key')
  result[key]=value
 return result
@unittest.skipUnless(AVAILABLE,'not_applicable: pre-P2 permission resources')
class PermissionResourcesTests(unittest.TestCase):
 def test_only_known_scalar_purposes_are_localized(self):
  self.assertEqual(len(contract.PURPOSE_KEYS),32)
  expected=json.loads((R/'tests/fixtures/p2_localization_contracts/permission-keys.json').read_text())
  self.assertEqual(set(expected),set(contract.PURPOSE_KEYS))
  for lang in contract.LANGUAGES:
   values=read_strings(HOST/f'Resources/{lang}.lproj/InfoPlist.strings')
   self.assertEqual(set(values),set(expected)|{'CFBundleName'})
   self.assertEqual(values['CFBundleName'],'LiveContainer')
   self.assertEqual({values[k] for k in expected},{contract.EXPECTED[lang]})
 def test_os_resources_do_not_override_app_or_guest_preferences(self):
  for lang in contract.LANGUAGES:
   source=(HOST/f'Resources/{lang}.lproj/InfoPlist.strings').read_text()
   for forbidden in ['AppleLanguages','V3AppLanguage','UserDefaults','requestAuthorization','NSLocationTemporaryUsageDescriptionDictionary']:
    self.assertNotIn(forbidden,source)
  project=(HOST/'LiveContainer.xcodeproj/project.pbxproj').read_text()
  self.assertIn('"zh-Hans",',project)
  self.assertIn('"zh-Hant",',project)
  for alias in ['zh_TW','zh_HK']:
   self.assertEqual((HOST/f'Resources/{alias}.lproj/InfoPlist.strings').read_bytes(),(HOST/'Resources/zh-Hant.lproj/InfoPlist.strings').read_bytes())
  self.assertEqual((HOST/'Resources/zh_CN.lproj/InfoPlist.strings').read_bytes(), (HOST/'Resources/zh-Hans.lproj/InfoPlist.strings').read_bytes())
  self.assertIn('A17ECAFE2DCA00000000F009 /* zh_CN */,',project)
 def test_compiled_permission_checks_fail_closed(self):
  data={lang+'.lproj/InfoPlist.strings':plistlib.dumps({'CFBundleName':'LiveContainer',**dict.fromkeys(contract.PURPOSE_KEYS,contract.EXPECTED[lang])}) for lang in contract.LANGUAGES}
  self.assertEqual(contract.verify_compiled_permissions(data.__getitem__,set(data))['status'],'passed')
  name='he.lproj/InfoPlist.strings';good=plistlib.loads(data[name])
  for mutation in ['missing','wrongLanguage','dictionary','unexpectedPurpose','productName']:
   values=dict(good)
   if mutation=='missing':del values[contract.PURPOSE_KEYS[0]]
   elif mutation=='wrongLanguage':values[contract.PURPOSE_KEYS[0]]=contract.EXPECTED['en']
   elif mutation=='dictionary':values['NSLocationTemporaryUsageDescriptionDictionary']='fake purpose'
   elif mutation=='unexpectedPurpose':values['NSInventedUsageDescription']='invented'
   else:values['CFBundleName']='Changed'
   data[name]=plistlib.dumps(values)
   self.assertEqual(contract.verify_compiled_permissions(data.__getitem__,set(data))['status'],'failed')
 def test_explicit_stage_capability_is_required(self):
  self.assertEqual(contract.verify_ipa('unused',capability_required=False)['status'],'not_applicable')
  for value in [None,0,1,'false','true']:
   with self.assertRaises(ValueError):contract.verify_ipa('unused',capability_required=value)
if __name__=='__main__':unittest.main()
