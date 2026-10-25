import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('russian_gate', Path(__file__).with_name('verify_russian_localization.py'))
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)

def unit(value): return {'stringUnit': {'state': 'translated', 'value': value}}
def fixture():
    strings = {f'key{i}': {'localizations': {'ru': unit('Текст ' + str(i))}} for i in range(2093)}
    compiled = {f'key{i}': 'Текст ' + str(i) for i in range(2093)}; plurals = {}
    categories = {'one':'%lld файл','few':'%lld файла','many':'%lld файлов','other':'%lld файла'}
    for i in range(6):
        key = f'plural{i} %lld'; strings[key] = {'localizations': {'ru': {'variations': {'plural': {k:unit(v) for k,v in categories.items()}}}}}
        plurals[key] = {'NSStringLocalizedFormatKey':'%#@arg1@','arg1':{'NSStringFormatSpecTypeKey':'NSStringPluralRuleType','NSStringFormatValueTypeKey':'lld',**categories}}
    subs = {f'arg{i}': {'argNum': i, 'formatSpecifier': 'lld', 'variations':{'plural': {k:unit(v.replace('%lld','%arg')) for k,v in categories.items()}}} for i in (1,2)}
    strings['compound %lld %lld'] = {'localizations': {'ru': {'stringUnit':unit('Перенесено %#@arg1@ и %#@arg2@')['stringUnit'], 'substitutions':subs}}}
    plurals['compound %lld %lld'] = {'NSStringLocalizedFormatKey':'Перенесено %1$#@arg1@ и %2$#@arg2@'}
    for i in (1,2): plurals['compound %lld %lld'][f'arg{i}'] = {'NSStringFormatSpecTypeKey':'NSStringPluralRuleType','NSStringFormatValueTypeKey':'lld',**{k:v.replace('%lld',f'%{i}$lld') for k,v in categories.items()}}
    return {'strings':strings},compiled,plurals

class RussianGateTests(unittest.TestCase):
    def setUp(self): self.catalog,self.strings,self.plurals=fixture()
    def verify(self): return r.verify_tables(self.catalog,self.strings,self.plurals)
    def test_full_exact_compiled_inventory(self): self.assertEqual(self.verify()['catalog_keys'],2100)
    def test_missing_russian_entry_is_not_english_fallback_coverage(self):
        self.catalog['strings']['key0']['localizations']={'en':unit('English')}
        with self.assertRaisesRegex(ValueError,'Missing explicit'):self.verify()
    def test_wrong_language_scalar_is_rejected(self):
        self.strings['key0']='English'
        with self.assertRaisesRegex(ValueError,'scalar table'):self.verify()
    def test_missing_or_extra_compiled_entries_rejected(self):
        self.strings['unreviewed']='Текст'
        with self.assertRaisesRegex(ValueError,'scalar table'):self.verify()
    def test_unfinished_translation_is_rejected(self):
        self.catalog['strings']['key0']['localizations']['ru']['stringUnit']['state']='new'
        with self.assertRaisesRegex(ValueError,'unfinished'):self.verify()
    def test_all_four_plural_categories_required(self):
        del self.catalog['strings']['plural0 %lld']['localizations']['ru']['variations']['plural']['other']
        with self.assertRaisesRegex(ValueError,'categories incomplete'):self.verify()
    def test_compiled_other_and_format_tokens_exact(self):
        self.plurals['plural0 %lld']['arg1']['other']='%s файла'
        with self.assertRaisesRegex(ValueError,'value differs'):self.verify()
    def test_compound_argument_permutation_rejected(self):
        self.plurals['compound %lld %lld']['NSStringLocalizedFormatKey']='Перенесено %2$#@arg1@ и %1$#@arg2@'
        with self.assertRaisesRegex(ValueError,'positions differ'):self.verify()
    def test_extra_printf_rule_rejected(self):
        self.plurals['plural0 %lld']['arg1']['zero']='%n'
        with self.assertRaisesRegex(ValueError,'fields differ'):self.verify()
    def test_actual_integer_categories_and_compound_cross_product(self):
        self.assertEqual([r.category(n) for n in (0,1,2,5,11,12,14,21,22,25)],['many','one','few','many','many','many','many','one','few','many'])
        rows=r.cases(self.catalog);self.assertEqual(len(rows),216)
        self.assertIn({'key':'compound %lld %lld','arguments':[21,22],'expected':'Перенесено 21 файл и 22 файла'},rows)
    def test_final_ipa_requires_both_actual_compiled_resources(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'Resources').mkdir();(root/r.CATALOG).write_text(json.dumps(self.catalog));ipa=root/'test.ipa'
            with zipfile.ZipFile(ipa,'w') as z:
                z.writestr('Payload/App.app/ru.lproj/Localizable.strings',plistlib.dumps(self.strings))
                z.writestr('Payload/App.app/ru.lproj/Localizable.stringsdict',plistlib.dumps(self.plurals))
                z.writestr('Payload/App.app/ru.lproj/InfoPlist.strings',plistlib.dumps({'NSCameraUsageDescription':'Камера'}))
            self.assertEqual(r.verify_ipa(ipa,root,expected_permissions={'NSCameraUsageDescription':'Камера'})['coverage']['catalog_keys'],2100)
            with zipfile.ZipFile(ipa,'w') as z:z.writestr('Payload/App.app/ru.lproj/Localizable.strings',plistlib.dumps(self.strings))
            with self.assertRaisesRegex(ValueError,'Missing or linked'):r.verify_ipa(ipa,root,expected_permissions={'NSCameraUsageDescription':'Камера'})
    def test_runtime_receipt_disclosures_cannot_overclaim(self):
        good={'visual_device_proof':False,'integer_counts':list(r.COUNTS),'other_category':r.OTHER_DISCLOSURE,'native_integer_cases':216,'native_scalar_cases':2093,'native_fixture_sha256':'a'*64}
        r.validate_disclosures(good,216,2093)
        for key,value in [('visual_device_proof',True),('integer_counts',[1,2,5]),('other_category','all categories ran'),('native_integer_cases',True),('native_scalar_cases',True),('native_fixture_sha256','invalid')]:
            changed={**good,key:value}
            with self.subTest(key=key), self.assertRaises(ValueError):r.validate_disclosures(changed,216,2093)
    def test_zip_special_nodes_and_parent_links_fail_closed(self):
        import stat
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'zip'
            for mode,parent in [(stat.S_IFIFO,False),(stat.S_IFCHR,False),(stat.S_IFREG,True)]:
                with zipfile.ZipFile(path,'w') as z:
                    if parent:
                        info=zipfile.ZipInfo('Payload/App.app/ru.lproj');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(info,'elsewhere')
                    info=zipfile.ZipInfo('Payload/App.app/ru.lproj/Localizable.strings');info.create_system=3;info.external_attr=(mode|0o644)<<16;z.writestr(info,b'fixture')
                with zipfile.ZipFile(path) as z, self.assertRaises(ValueError):r.zip_resource(z,'Payload/App.app/ru.lproj/Localizable.strings')

    def test_native_gate_does_not_accept_skip_or_missing_runtime(self):
        source=Path(r.__file__).read_text();self.assertIn('not r.skipped',source);self.assertIn("'swiftc'",source);self.assertIn("'xcstringstool'",source)
        self.assertIn('V3DisplayMessage',r.SWIFT);self.assertIn('V3Localization.string',r.SWIFT)

if __name__=='__main__':unittest.main()
