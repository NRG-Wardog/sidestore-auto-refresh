from pathlib import Path
import json,plistlib,re,shutil,subprocess,tempfile,unittest,os
R=Path(__file__).resolve().parents[1]
HOST=Path(os.environ['MAINTAINED_LIVE_CONTAINER_TEST_SOURCE']) if os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE') else None
P2_AVAILABLE=bool(HOST and (HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').is_file())
if os.environ.get('V3_P2_REQUIRED') == '1' and not P2_AVAILABLE:
 raise RuntimeError('Required P2 capability needs an explicit maintained source with localization helpers')
CONTRACTS=R/'tests/fixtures/p2_localization_contracts'
@unittest.skipUnless(P2_AVAILABLE, 'not_applicable: historical/pre-P2 source; no P2 proof claimed')
class DynamicDisplayPolicyTests(unittest.TestCase):
 def test_only_reviewed_anchored_templates_are_registered(self):
  contract=json.loads((CONTRACTS/'dynamic-format-contract.json').read_text())
  self.assertEqual(len(contract['entries']),85)
  helper=(HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text()
  for line in helper.splitlines():
   if 'DisplayFormat(key:' in line:
    self.assertEqual(line.count('integerArguments:'),1)
    self.assertIn('options: [.dotMatchesLineSeparators]), localizeArguments:',line)
  for row in contract['entries']:
   self.assertTrue(row['pattern'].startswith('^') and row['pattern'].endswith('$'))
   regex=re.compile(row['pattern']);self.assertEqual(regex.groups,len(row['localizeArguments']))
  literal=r'"(?:\\.|[^"\\])*"'
  row_pattern=re.compile(r'DisplayFormat\(key: ('+literal+r'), regex: try\? NSRegularExpression\(pattern: ('+literal+r'), options: \[.dotMatchesLineSeparators\]\), localizeArguments: (\[[^\]]*\]), integerArguments: (\[[^\]]*\])\)')
  actual=[{'formatKey':json.loads(key),'pattern':json.loads(pattern),'localizeArguments':json.loads(prose),'integerArguments':json.loads(integer)} for key,pattern,prose,integer in row_pattern.findall(helper)]
  expected=[{key:row[key] for key in ['formatKey','pattern','localizeArguments','integerArguments']} for row in contract['entries']]
  self.assertEqual(actual,expected)
  self.assertIn('1e570f73684fcdf7',contract['excluded'])
  self.assertIn('d5dc6b50864b5a69',contract['excluded'])
 def test_opaque_ui_and_diagnostic_values_remain_raw(self):
  s=(HOST/'LiveContainerSwiftUI/Views/V3UnifiedShell.swift').read_text()
  for expression in ['app.name','app.bundleID','cert.serial','server.address','auth.team','auth.provisioningTechnical','auth.currentAttemptFailure.technicalDetails','auth.promptResponseDiagnostics','V3AuthStore.failureDetails(from: failure)','String(tail.suffix(120_000))']:
   self.assertIn('Text('+expression+')',s)
   self.assertNotIn('V3LocalizedText('+expression+')',s)
  self.assertIn('UIPasteboard.general.string = value',s)
 def test_counts_are_numeric_and_unmatched_messages_pass_through(self):
  rows=json.loads((CONTRACTS/'dynamic-format-contract.json').read_text())['entries']
  row=next(r for r in rows if r['formatKey']=='%lld apps')
  self.assertIsNotNone(re.fullmatch(row['pattern'],'2 apps'))
  self.assertIsNone(re.fullmatch(row['pattern'],'My apps'))
  s=(HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text()
  self.assertIn('guard depth < 3 else { return value }',s)
  self.assertIn('guard localized != format.key else { continue }',s)
 def test_pending_recovery_and_auth_policy_bytes_are_unchanged(self):
  import sys
  sys.path.insert(0,str(R/'scripts'))
  import run_p0_signin_rendering as extraction
  import hashlib
  after=(HOST/'LiveContainerSwiftUI/Views/V3UnifiedShell.swift').read_text()
  self.assertEqual(hashlib.sha256(after.split('struct V3UnifiedShell: View {')[0].encode()).hexdigest(), '2449f28374f32628ee88db8a18f04f94e1cf2865b2a52fe88bf7a82b81f25fac')
  self.assertEqual(hashlib.sha256(extraction.declaration(after,'final class V3AuthStore').encode()).hexdigest(), 'd976a42812917e98e9215cd3cc4ad6bcbcfbdb70300a5a56ca3e7ed6932ec435')
 def test_dates_follow_selected_locale_without_changing_schedule_or_diagnostics(self):
  helper=(HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text()
  self.assertIn('locale: Locale(identifier: language.rawValue), calendar: .autoupdatingCurrent',helper)
  self.assertIn('timeZone: .autoupdatingCurrent',helper)
  self.assertIn('calendar.locale = Locale(identifier: language.rawValue)',helper)
  shell=(HOST/'LiveContainerSwiftUI/Views/V3UnifiedShell.swift').read_text()
  self.assertIn('state: setup.localizedScheduleState(language: V3AppLanguage.resolve(dateLocale.identifier))',shell)
  self.assertIn('summary += ", next expected " + deadline.formatted(date: .abbreviated, time: .shortened)',shell)
  self.assertIn('lines.append("Refresh schedule: " + schedule.detail)',shell)
  self.assertNotIn('V3Localization.date', '\n'.join(l for l in shell.splitlines() if 'lines.append(' in l))
  view=(HOST/'LiveContainerSwiftUI/Views/Settings/LCEmbeddedSideStoreRefreshView.swift').read_text()
  self.assertIn('V3Localization.weekdayName(day, language: V3AppLanguage.resolve(dateLocale.identifier))',view)
  self.assertIn('Calendar.autoupdatingCurrent.dateComponents',view)
  self.assertIn('private struct V3HomeView: View {\n    @Environment(\\.locale) private var dateLocale',shell)
  self.assertIn('@State private var certDates: [String: Date] = [:]',shell)
  certificate_rows=shell.split('ForEach(certRows, id:',1)[1].split('Text("SideStore uses its active certificate',1)[0]
  self.assertIn('Text(certDates[row.0].map {',certificate_rows)
  health_rows=shell.split('ForEach(rows, id:',1)[1].split('Section("Certificates")',1)[0]
  self.assertNotIn('certDates[row.0]',health_rows)
  self.assertIn('certDates.removeAll(keepingCapacity: true)',shell)
  diagnose=(HOST/'LiveContainerSwiftUI/Views/Settings/LCJITLessDiagnoseView.swift').read_text()
  self.assertIn('@State var certificateValidateUntil : Date?',diagnose)
  self.assertIn('certificateValidateUntil.map { V3Localization.legacyDate',diagnose)
  for rel in ['AppList/AppSettings/LCAppSettingsView.swift','Settings/DataManagement/LCStorageManagementSections.swift','Settings/LCJITLessDiagnoseView.swift']:
   source=(HOST/'LiveContainerSwiftUI/Views'/rel).read_text()
   self.assertNotIn('DateFormatter()',source)
   self.assertIn('V3AppLanguage.resolve(dateLocale.identifier)',source)
 @unittest.skipUnless(shutil.which('swiftc'),'Swift compiler unavailable; native display policy not validated')
 def test_native_selected_bundle_formats_and_preserves_identifiers(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);bundle=root/'Languages.bundle';bundle.mkdir()
   (bundle/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'org.fixture.localization','CFBundleDevelopmentRegion':'en'}))
   translations={'en':{'Copied':'Copied','Error ID:':'Error ID:','Copied %@':'Copied %@'},'he':{'Copied':'הועתק','Error ID:':'מזהה שגיאה:','Imported account for %@. Sign in with its Apple password to finish.':'יובא חשבון עבור %@. יש להיכנס עם סיסמת Apple כדי לסיים.','%lld apps':'%lld יישומים'},'zh-Hans':{'Copied':'已复制'},'zh-Hant':{'Copied':'已複製'}}
   for lang,values in translations.items():
    folder=bundle/(lang+'.lproj');folder.mkdir();(folder/'Localizable.strings').write_bytes(plistlib.dumps(values))
   helper=(HOST/'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text()
   harness='''
let bundle = Bundle(path: CommandLine.arguments[1])!
precondition(V3AppLanguage.resolve("unknown") == .english)
precondition(V3AppLanguage.allCases.map(\\.rawValue) == ["en", "zh-Hans", "zh-Hant", "he"])
for value in ["zh-Hant", "zh_Hant_TW", "zh-TW", "zh_HK", "zh-MO", "zh-Hant-CN", "zh-TW-x-hans", "zh-Hant-u-nu-latn"] { precondition(V3AppLanguage.resolve(value) == .traditionalChinese) }
for value in ["zh-Hans", "zh_Hans_CN", "zh-CN", "zh-SG", "zh-Hans-TW", "zh-Hans-CN-x-hant", "zh-CN-x-tw"] { precondition(V3AppLanguage.resolve(value) == .simplifiedChinese) }
precondition(V3AppLanguage.resolve("he_IL").isRightToLeft)
precondition(!V3AppLanguage.traditionalChinese.isRightToLeft)
precondition(V3Localization.string("Copied", language: .traditionalChinese, bundle: bundle) == "已複製")
precondition(V3Localization.string("Copied %@", language: .traditionalChinese, bundle: bundle) == "Copied %@")
precondition(V3Localization.string("Copied", language: .hebrew, bundle: bundle) == "הועתק")
precondition(V3Localization.string("missing.key", language: .hebrew, bundle: bundle) == "missing.key")
let email = "fixture@example.invalid"
let input = "Imported account for \\(email). Sign in with its Apple password to finish."
let rendered = V3Localization.text(input, language: .hebrew, bundle: bundle)
precondition(rendered.contains(email) && rendered != input)
precondition(V3Localization.text("2 apps", language: .hebrew, bundle: bundle) == "2 יישומים")
precondition(V3Localization.text("My apps", language: .hebrew, bundle: bundle) == "My apps")
let diagnostic = V3Localization.text("Copied\\nError ID: SS-TEST-01", language: .hebrew, bundle: bundle)
precondition(diagnostic.contains("\\u{2066}SS-TEST-01\\u{2069}"))
precondition(input == "Imported account for fixture@example.invalid. Sign in with its Apple password to finish.")
let instant = Date(timeIntervalSince1970: 1728000000)
for language in V3AppLanguage.allCases {
    let expected = instant.formatted(Date.FormatStyle(date: .complete, time: .shortened,
        locale: Locale(identifier: language.rawValue), calendar: .autoupdatingCurrent,
        timeZone: .autoupdatingCurrent))
    precondition(V3Localization.date(instant, date: .complete, time: .shortened, language: language) == expected)
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: language.rawValue)
    formatter.calendar = .autoupdatingCurrent
    formatter.timeZone = .autoupdatingCurrent
    formatter.dateStyle = .short
    formatter.timeStyle = .medium
    precondition(V3Localization.legacyDate(instant, dateStyle: .short, timeStyle: .medium, language: language) == formatter.string(from: instant))
    var calendar = Calendar.autoupdatingCurrent
    calendar.locale = Locale(identifier: language.rawValue)
    for day in 1...7 { precondition(V3Localization.weekdayName(day, language: language) == calendar.weekdaySymbols[day - 1]) }
}
precondition(V3Localization.weekdayName(0, language: .hebrew).isEmpty)
precondition(V3Localization.weekdayName(8, language: .hebrew).isEmpty)
precondition(V3Localization.date(instant, date: .complete, time: .omitted, language: .english)
    != V3Localization.date(instant, date: .complete, time: .omitted, language: .hebrew))
print("native-display-policy-passed")
'''
   source=root/'main.swift';source.write_text(helper+'\n'+harness)
   subprocess.run(['swiftc',str(source),'-o',str(root/'test')],check=True,capture_output=True,text=True,timeout=120)
   result=subprocess.run([str(root/'test'),str(bundle)],check=True,capture_output=True,text=True,timeout=30)
   self.assertIn('native-display-policy-passed',result.stdout)
if __name__=='__main__':unittest.main()
