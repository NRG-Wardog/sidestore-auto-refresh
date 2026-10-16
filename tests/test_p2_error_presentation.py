"""Selected-language prose and raw diagnostic separation. Native execution is explicit."""
import hashlib,json,os,plistlib,re,shutil,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
HOST=Path(os.environ['MAINTAINED_LIVE_CONTAINER_TEST_SOURCE']) if os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE') else None
AVAILABLE=bool(HOST and (HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').is_file())
@unittest.skipUnless(AVAILABLE,'not_applicable: selected source has no P2 localization')
class ErrorPresentationTests(unittest.TestCase):
 def test_display_boundary_is_fail_closed_and_keeps_original_snapshot(self):
  s=(HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text()
  boundary=s.split('static func prose(',1)[1].split('// Explicit reviewed display formats.',1)[0]
  self.assertIn('?? unavailableDetails(language: language, bundle: bundle)',boundary)
  self.assertIn('selectedString',boundary)
  self.assertNotIn('for code in [language.rawValue, "en"]',boundary)
  self.assertIn('let original: String',s)
  self.assertIn('var errorDescription: String? { original }',s)
  self.assertIn('(error as? Self) ?? .raw(error.localizedDescription)',s)
  self.assertIn('suffix.prefix { !$0.isWhitespace }',s)
  self.assertIn('translatedProse(remainder, language: language, bundle: bundle, depth: depth + 1)',s)
  self.assertNotIn('let message = prose(',boundary)
  self.assertNotIn('AppleLanguages', '\n'.join(l for l in s.splitlines() if not l.lstrip().startswith('//')))
  picker=(HOST/'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift').read_text()
  self.assertIn('value.rendered(language: .resolve(locale.identifier))',picker)
  self.assertIn('@AppStorage(V3AppLanguage.storageKey, store: V3Localization.hostDefaults)',picker)
  self.assertNotIn('.id(',picker)
 def test_nested_error_arguments_are_never_opaque(self):
  rows=json.loads((ROOT/'tests/fixtures/p2_localization_contracts/dynamic-format-contract.json').read_text())['entries']
  for key,index in [('Failed to contact SideJITServer: %@',0),('Failed to contact JitStreamer-EB server: %@',0),('Failed to move %@ to %@. %@',2),('Source is not reachable: %@. %@',1)]:
   row=next(x for x in rows if x['formatKey']==key)
   self.assertTrue(row['localizeArguments'][index],key)
  row=next(x for x in rows if x['formatKey']=='Error ID: %@')
  self.assertIsNone(re.fullmatch(row['pattern'],'Error ID: SS-X Provider prose must not be an ID'))
 def test_new_catalog_keys_keep_placeholders_in_every_language(self):
  catalog=json.loads((HOST/'Resources/Localizable.xcstrings').read_text())['strings']
  keys=['Some details are unavailable in the selected language.','Install %@','Update %@','Back up %@','Restore %@','Enable JIT for %@','Remove %@ from library and erase its backups','Delete %@ and erase its data and backups','%@ completed successfully.','%@, expires %@','SideStore status: %@, %@']
  for key in keys:
   for language in ['en','he','zh-Hans','zh-Hant']:
    value=catalog[key]['localizations'][language]['stringUnit']['value']
    self.assertEqual(value.count('%@'),key.count('%@'))
    self.assertTrue(value)
 def test_dynamic_shell_surfaces_and_opaque_values_are_separate(self):
  s=(HOST/'LiveContainerSwiftUI/Views/V3UnifiedShell.swift').read_text()
  for old in ['            Text(progress)\n','                    Text(ping).font','                Text(title).font(.headline)','            Text(title).font(.subheadline)','Toggle(title, isOn:', '" (exp "']:
   self.assertFalse(old in s,old)
  for raw in ['Text(app.name)','Text(app.bundleID)','Text(cert.serial)','Text(auth.team)','Text(option["label"] ?? "")']:
   self.assertIn(raw,s)
  self.assertIn('.navigationTitle(V3Localization.prose(request.title',s)
  self.assertIn('SideStore status: %@, %@',s)
 def test_provenance_is_explicit_for_names_and_wire_fallbacks(self):
  shell=(HOST/'LiveContainerSwiftUI/Views/V3UnifiedShell.swift').read_text()
  self.assertIn('if kind == "team" { Text(option["label"] ?? "") }',shell)
  self.assertIn('String(identifier.dropFirst("remove:".count))',shell)
  self.assertIn('detail: status.account, detailIsOpaque: true',shell)
  self.assertIn('if state.detailIsOpaque { Text(state.detail) }',shell)
  self.assertIn('if accountPresent == true { opaqueHealthRows.insert("Account") }',shell)
  self.assertIn('if teamPresent == true { opaqueHealthRows.insert("Team") }',shell)
  runtime=(ROOT/'scripts/templates/v3_headless_runtime.swift').read_text()
  health=runtime.split('static func health()',1)[1].split('static func ',1)[0]
  self.assertIn('"activeAccountPresent": activeAccount != nil, "activeTeamPresent": team != nil',health)
  self.assertIn('"account": account, "team": team?.name ?? "No active team"',health)
 def test_every_compiled_scalar_translation_is_bound_to_source(self):
  import sys
  sys.path.insert(0,str(ROOT/'scripts'))
  import p2_localization_contract as compiled
  catalog_path=HOST/'Resources/Localizable.xcstrings'
  catalog=json.loads(catalog_path.read_text())['strings']
  self.assertEqual(compiled.TRANSLATION_MANIFEST['sourceCatalogSHA256'],hashlib.sha256(catalog_path.read_bytes()).hexdigest())
  for language in compiled.LANGUAGES:
   expected={key:row['localizations'][language]['stringUnit']['value'] for key,row in catalog.items() if 'stringUnit' in row['localizations'][language]}
   self.assertEqual(compiled.REQUIRED_TRANSLATIONS[language],expected)
   self.assertGreater(len(expected),1800)
 def test_host_store_capture_does_not_fall_back_after_nil_host_selector(self):
  helper=(HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text()
  self.assertIn('retainedHostObject("lcUserDefaults")',helper)
  self.assertIn('retainedHostObject("lcMainBundle")',helper)
  self.assertIn('preconditionFailure("Captured host localization context is unavailable")',helper)
  self.assertIn('V3Localization.hostDefaults.set(value.rawValue, forKey: storageKey)',helper)
  for rel in ['LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift','LiveContainerSwiftUI/Views/V3UnifiedShell.swift','MultitaskSupport/MultitaskAppWindow.swift']:
   self.assertIn('@AppStorage(V3AppLanguage.storageKey, store: V3Localization.hostDefaults)',(HOST/rel).read_text())
 def test_alarm_delta_only_binds_new_presentation_resources(self):
  source=(HOST/'LiveContainerSwiftUI/App/LiveContainerAutoRefreshAlarm.swift').read_text()
  new='''        let presentationLocale = Locale(identifier: V3AppLanguage.selected.rawValue)
        let presentationBundle = LocalizedStringResource.BundleDescription.atURL(V3Localization.hostBundle.bundleURL)
        let alert = AlarmPresentation.Alert(
            title: LocalizedStringResource("Automatic refresh needs checking", locale: presentationLocale, bundle: presentationBundle),
            secondaryButton: AlarmButton(text: LocalizedStringResource("Refresh Now", locale: presentationLocale, bundle: presentationBundle), textColor: .white, systemImageName: "arrow.clockwise"),'''
  old='''        let alert = AlarmPresentation.Alert(
            title: "Automatic refresh needs checking",
            secondaryButton: AlarmButton(text: "Refresh Now", textColor: .white, systemImageName: "arrow.clockwise"),'''
  self.assertEqual(source.count(new),1)
  self.assertEqual(hashlib.sha256(source.replace(new,old).encode()).hexdigest(),'e67daa3dfcf257cb0256bf4f3dd6f69407c9d72368ba699e672b158eda75f004')
 @unittest.skipUnless(shutil.which('swiftc'),'Swift compiler unavailable; native error switching not validated')
 def test_native_error_locale_switch_persistence_and_async_snapshots(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);bundle=root/'Fixture.bundle';bundle.mkdir()
   (bundle/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'org.fixture.p2-errors','CFBundleDevelopmentRegion':'en'}))
   catalog=json.loads((HOST/'Resources/Localizable.xcstrings').read_text())['strings']
   for language in ['en','he','zh-Hans','zh-Hant']:
    folder=bundle/(language+'.lproj');folder.mkdir()
    values={key:entry['localizations'][language]['stringUnit']['value'] for key,entry in catalog.items() if 'stringUnit' in entry['localizations'][language]}
    (folder/'Localizable.strings').write_bytes(plistlib.dumps(values))
   helper=(HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text()
   harness=r'''
let bundle = Bundle(path: CommandLine.arguments[1])!
let oldSelection = UserDefaults.standard.object(forKey: V3AppLanguage.storageKey)
defer { if let oldSelection { UserDefaults.standard.set(oldSelection, forKey: V3AppLanguage.storageKey) } else { UserDefaults.standard.removeObject(forKey: V3AppLanguage.storageKey) } }
let opaqueName = "Untranslated User App %@ • שלום"
let deferred = V3DisplayMessage.localized("Install %@", opaqueName)
let snapshot = deferred.original
let providerText = "Unexpected provider prose, password=never-display-this"
let unknown = V3DisplayMessage.raw(providerText).appendingDiagnostic("SS-TEST-42")
let unknownOriginal = unknown.original
let unsafeProviderFormat = "Unknown provider prose 100% %@ %n"
let zeroArgument = V3DisplayMessage.localized(unsafeProviderFormat)
precondition(zeroArgument.original == unsafeProviderFormat)
let collidingName = V3DisplayMessage.localized("Install %@", "Cancel")
for language in V3AppLanguage.allCases + [.english] {
    V3AppLanguage.persist(language)
    precondition(V3AppLanguage.selected == language)
    precondition(UserDefaults.standard.string(forKey: V3AppLanguage.storageKey) == language.rawValue)
    let fallback = V3Localization.string("Some details are unavailable in the selected language.", language: language, bundle: bundle)
    precondition(V3Localization.prose(providerText, language: language, bundle: bundle) == fallback)
    precondition(zeroArgument.rendered(language: language, bundle: bundle) == fallback)
    precondition(collidingName.rendered(language: language, bundle: bundle).contains("Cancel"))
    let unknownRecovery = V3Localization.prose(providerText + "\n\nDownload the last version compatible with this device instead?", language: language, bundle: bundle)
    precondition(unknownRecovery.contains(fallback))
    precondition(unknownRecovery.contains(V3Localization.string("Download the last version compatible with this device instead?", language: language, bundle: bundle)))
    precondition(!unknownRecovery.contains(providerText))
    let display = unknown.rendered(language: language, bundle: bundle)
    precondition(display.contains("SS-TEST-42") && !display.contains(providerText))
    precondition(unknown.original == unknownOriginal && deferred.original == snapshot)
    let localized = deferred.rendered(language: language, bundle: bundle)
    precondition(localized.contains(opaqueName))
    precondition(localized == String(format: V3Localization.string("Install %@", language: language, bundle: bundle), opaqueName))
    let recovered = V3DisplayMessage.preserving(deferred)
    precondition(recovered.original == snapshot && recovered.rendered(language: language, bundle: bundle) == localized)
    let asynchronous = V3DisplayMessage.raw(providerText)
    precondition(asynchronous.rendered(language: language, bundle: bundle) == fallback)
    let repeated = "Copied" + String(repeating: "\nError ID: SS-TEST-42", count: 1000)
    let bounded = V3Localization.prose(repeated, language: language, bundle: bundle)
    precondition(bounded.count < 2000)
    let nested = "Failed to contact SideJITServer: " + providerText
    precondition(V3Localization.prose(nested, language: language, bundle: bundle) == fallback)
    let message = "Copied\nError ID: SS-TEST-42 Cancel"
    let withRecovery = V3Localization.prose(message, language: language, bundle: bundle)
    precondition(withRecovery.contains(V3Localization.string("Cancel", language: language, bundle: bundle)))
    precondition(!withRecovery.contains("\u{2066}SS-TEST-42 Cancel\u{2069}"))
    precondition(message == "Copied\nError ID: SS-TEST-42 Cancel")
}
print("native-error-presentation-passed")
'''
   source=root/'main.swift';source.write_text(helper+'\n'+harness)
   subprocess.run(['swiftc',str(source),'-o',str(root/'test')],check=True,capture_output=True,text=True,timeout=120)
   result=subprocess.run([str(root/'test'),str(bundle)],check=True,capture_output=True,text=True,timeout=30)
   self.assertIn('native-error-presentation-passed',result.stdout)
if __name__=='__main__':unittest.main()
