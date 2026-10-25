"""Four-language source/resource contracts; native evidence is separate."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import p2_localization_contract as localization
import p2_permission_contract as permissions

HOST = Path(os.environ['MAINTAINED_LIVE_CONTAINER_TEST_SOURCE']) if os.environ.get('MAINTAINED_LIVE_CONTAINER_TEST_SOURCE') else None
AVAILABLE = bool(HOST and (HOST / 'LiveContainerSwiftUI/Utilities/V3Localization.swift').is_file())
if os.environ.get('V3_P2_REQUIRED') == '1' and not AVAILABLE:
    raise RuntimeError('Required four-language P2 capability needs the selected maintained source')


@unittest.skipUnless(AVAILABLE, 'not_applicable: pre-P2 source')
class FourLanguageTests(unittest.TestCase):
    def test_exact_four_picker_choices_and_script_aware_resolution(self):
        helper = (HOST / 'LiveContainerSwiftUI/Utilities/V3Localization.swift').read_text()
        picker = (HOST / 'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift').read_text()
        self.assertIn('case english = "en", simplifiedChinese = "zh-Hans", traditionalChinese = "zh-Hant", hebrew = "he"', helper)
        for name in ['English', '简体中文', '繁體中文', 'עברית']:
            self.assertIn('return "' + name + '"', helper)
        self.assertIn('ForEach(V3AppLanguage.allCases)', picker)
        self.assertIn('Text(verbatim: language.nativeName)', picker)
        self.assertIn('switch components.dropFirst().first', helper)
        self.assertIn('case "hant": return .traditionalChinese', helper)
        self.assertIn('case "hans": return .simplifiedChinese', helper)
        self.assertIn('case "tw", "hk", "mo": return .traditionalChinese', helper)
        self.assertNotIn('components.contains(', helper)
        self.assertIn('var isRightToLeft: Bool { self == .hebrew }', helper)
        self.assertIn('guard let raw else { return .english }', helper)
        self.assertIn('for code in [language.rawValue, "en"]', helper)

    def test_all_catalog_keys_have_four_language_coverage(self):
        catalog = json.loads((HOST / 'Resources/Localizable.xcstrings').read_text())['strings']
        self.assertEqual(len(catalog), 2088)
        self.assertEqual(localization.LANGUAGES, ('en', 'zh-Hans', 'zh-Hant', 'he'))
        def units(value):
            if isinstance(value, dict):
                if 'stringUnit' in value:
                    yield value['stringUnit']
                for key, child in value.items():
                    if key != 'stringUnit':
                        yield from units(child)
        for key, entry in catalog.items():
            for language in localization.LANGUAGES:
                self.assertIn(language, entry.get('localizations', {}), (key, language))
                translated = list(units(entry['localizations'][language]))
                self.assertTrue(translated, (key, language))
                for unit in translated:
                    self.assertIsInstance(unit.get('value'), str, (key, language))
                    self.assertEqual(unit.get('state'), 'translated', (key, language))
            for language, expected in localization.REQUIRED_TRANSLATIONS.items():
                if key in expected:
                    self.assertEqual(entry['localizations'][language]['stringUnit']['value'], expected[key])
        for key in localization.PLURAL_KEYS:
            plural = catalog[key]['localizations']['zh-Hant']['variations']['plural']
            self.assertIn('other', plural)
            for unit in plural.values():
                localization.validate_int64_format(unit['stringUnit']['value'], expect_count=True)

    def test_compiled_traditional_language_cannot_fall_back_silently(self):
        data = {}
        for language in localization.LANGUAGES:
            data[language + '.lproj/Localizable.strings'] = plistlib.dumps(localization.REQUIRED_TRANSLATIONS[language])
            data[language + '.lproj/Localizable.stringsdict'] = plistlib.dumps({key: {
                'NSStringLocalizedFormatKey': '%#@count@',
                'count': {'NSStringFormatSpecTypeKey': 'NSStringPluralRuleType',
                          'NSStringFormatValueTypeKey': 'lld', 'other': '%lld'},
            } for key in localization.PLURAL_KEYS} | localization.COMPOUND_PLURALS[language])
        self.assertEqual(localization.verify_compiled_resources(data.__getitem__, set(data))['status'], 'passed')
        for suffix in ['strings', 'stringsdict']:
            name = 'zh-Hant.lproj/Localizable.' + suffix
            self.assertEqual(localization.verify_compiled_resources(data.__getitem__, set(data) - {name})['status'], 'failed')
        name = 'zh-Hant.lproj/Localizable.strings'
        for wrong in ['en', 'zh-Hans']:
            data[name] = plistlib.dumps(localization.REQUIRED_TRANSLATIONS[wrong])
            self.assertEqual(localization.verify_compiled_resources(data.__getitem__, set(data))['status'], 'failed')

    def test_traditional_permission_aliases_are_explicit_and_fail_closed(self):
        self.assertEqual(permissions.LANGUAGES, ('en', 'zh-Hans', 'zh-Hant', 'he', 'zh_CN', 'zh_TW', 'zh_HK'))
        canonical = (HOST / 'Resources/zh-Hant.lproj/InfoPlist.strings').read_bytes()
        for alias in ['zh_TW', 'zh_HK']:
            self.assertEqual((HOST / ('Resources/' + alias + '.lproj/InfoPlist.strings')).read_bytes(), canonical)
        data = {language + '.lproj/InfoPlist.strings': plistlib.dumps({
            'CFBundleName': 'LiveContainer', **dict.fromkeys(permissions.PURPOSE_KEYS, permissions.EXPECTED[language]),
        }) for language in permissions.LANGUAGES}
        self.assertEqual(permissions.verify_compiled_permissions(data.__getitem__, set(data))['status'], 'passed')
        for language in ['zh-Hant', 'zh_TW', 'zh_HK']:
            name = language + '.lproj/InfoPlist.strings'
            self.assertEqual(permissions.verify_compiled_permissions(data.__getitem__, set(data) - {name})['status'], 'failed')
        data['zh-Hant.lproj/InfoPlist.strings'] = data['zh-Hans.lproj/InfoPlist.strings']
        self.assertEqual(permissions.verify_compiled_permissions(data.__getitem__, set(data))['status'], 'failed')

    def test_native_fixture_demands_all_four_languages(self):
        ui = (ROOT / 'tests/fixtures/p0_signin_ui_tests.swift').read_text()
        app = (ROOT / 'tests/fixtures/p0_signin_app.swift').read_text()
        renderer = (ROOT / 'scripts/run_p0_signin_rendering.py').read_text()
        self.assertIn('for next in ["en", "he", "zh-Hans", "zh-Hant", target]', ui)
        self.assertIn('"verifiedLanguages": verifiedLanguages', ui)
        self.assertIn('if direction && pluralRuntimeVerified && !verifiedLanguages.contains(language)', ui)
        self.assertIn('["en", "zh-Hans", "zh-Hant", "he"].contains', app)
        self.assertIn('set(verified) != required', renderer)
        self.assertEqual(len(re.findall(r'func test\w+\(', ui)), 4)
        self.assertEqual(len(re.findall(r'screenshot\("', ui)), 3)


if __name__ == '__main__':
    unittest.main()
