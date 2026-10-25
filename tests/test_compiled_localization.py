"""Synthetic negatives, independent of a sealed historical catalog or simulator."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_compiled_localization.py'
spec = importlib.util.spec_from_file_location('compiled_localization', SCRIPT)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def unit(value):
    return {'stringUnit': {'state': 'translated', 'value': value}}


def fixture():
    catalog = {'sourceLanguage': 'en', 'version': '1.0', 'strings': {}}
    for key in ('label', 'items %lld', 'folders %lld %lld'):
        catalog['strings'][key] = {'localizations': {}}
    tables = {}
    for language in c.LANGUAGES:
        catalog['strings']['label']['localizations'][language] = unit(language + ' label')
        catalog['strings']['items %lld']['localizations'][language] = {
            'variations': {'plural': {'one': unit(language + ' %lld item'),
                                      'other': unit(language + ' %lld items')}}}
        compound = unit(language + ' %#@first@ and %#@second@')
        compound['substitutions'] = {
            name: {'argNum': number, 'formatSpecifier': 'lld',
                   'variations': {'plural': {'one': unit(language + ' %arg ' + noun),
                                             'other': unit(language + ' %arg ' + noun + 's')}}}
            for name, number, noun in [('first', 1, 'folder'), ('second', 2, 'tweak')]}
        catalog['strings']['folders %lld %lld']['localizations'][language] = compound
        tables[language + '.lproj/Localizable.strings'] = {'label': language + ' label'}
        tables[language + '.lproj/Localizable.stringsdict'] = {
            'items %lld': {c.FORMAT: '%#@count@', 'count': {
                c.SPEC: 'NSStringPluralRuleType', c.TYPE: 'lld',
                'one': language + ' %lld item', 'other': language + ' %lld items'}},
            'folders %lld %lld': {
                c.FORMAT: language + ' %1$#@first@ and %2$#@second@',
                'first': {c.SPEC: 'NSStringPluralRuleType', c.TYPE: 'lld',
                          'one': language + ' %1$lld folder', 'other': language + ' %1$lld folders'},
                'second': {c.SPEC: 'NSStringPluralRuleType', c.TYPE: 'lld',
                           'one': language + ' %2$lld tweak', 'other': language + ' %2$lld tweaks'}}}
    return catalog, tables


class FormatTests(unittest.TestCase):
    def test_declared_int64_and_escaped_literal(self):
        for value in ('%lld', '%1$lld', 'done %% %lld'):
            c.validate_int64_format(value)
        c.validate_int64_format('%2$lld', argument=2)
        c.validate_int64_format('only %% literal', expect_count=False)

    def test_unsafe_or_wrong_argument_rejected(self):
        for value in ('%n', '%lld %n', '%lld %@', '%2$lld', '%*lld', '%1$*lld', '%d', '%lld %lld', '%', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                c.validate_int64_format(value)
        with self.assertRaises(ValueError):
            c.validate_int64_format('%lld', expect_count=False)
        with self.assertRaises(ValueError):
            c.validate_int64_format('%lld', argument=2)


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.catalog, self.tables = fixture()
        self.contract = c.catalog_contract(self.catalog)

    def fails(self, tables):
        self.assertEqual(c.verify_compiled_resources(self.contract, tables)['status'], 'failed')

    def test_independent_fixture_passes_and_counts_derive_from_catalog(self):
        result = c.verify_compiled_resources(self.contract, self.tables)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['coverage'], {'scalar_keys_per_language': 1,
                         'simple_keys_per_language': 1, 'compound_keys_per_language': 1})
        for language in c.LANGUAGES:
            self.catalog['strings'].setdefault('new label', {'localizations': {}})['localizations'][language] = unit('new ' + language)
            self.tables[language + '.lproj/Localizable.strings']['new label'] = 'new ' + language
        self.assertEqual(c.verify_compiled_resources(c.catalog_contract(self.catalog), self.tables)['status'], 'passed')

    def test_single_argument_compiler_position_is_allowed(self):
        for language in c.LANGUAGES:
            row = self.tables[language + '.lproj/Localizable.stringsdict']['items %lld']
            row[c.FORMAT] = '%1$#@count@'
            row['count']['one'] = row['count']['one'].replace('%lld', '%1$lld')
        self.assertEqual(c.verify_compiled_resources(self.contract, self.tables)['status'], 'passed')

    def test_each_required_language_and_table_cannot_be_missing(self):
        for name in self.tables:
            changed = copy.deepcopy(self.tables)
            del changed[name]
            with self.subTest(name=name):
                self.fails(changed)

    def test_traditional_resources_cannot_fall_back_to_other_language(self):
        for language in ('en', 'zh-Hans'):
            for suffix in ('strings', 'stringsdict'):
                changed = copy.deepcopy(self.tables)
                changed['zh-Hant.lproj/Localizable.' + suffix] = changed[language + '.lproj/Localizable.' + suffix]
                with self.subTest(language=language, suffix=suffix):
                    self.fails(changed)

    def test_scalar_missing_wrong_type_wrong_value_extra_or_plural_duplicate(self):
        for language in c.LANGUAGES:
            for mutation in ('missing', 'type', 'value', 'extra', 'plural-duplicate'):
                changed = copy.deepcopy(self.tables)
                row = changed[language + '.lproj/Localizable.strings']
                if mutation == 'missing': del row['label']
                elif mutation == 'type': row['label'] = 17
                elif mutation == 'value': row['label'] = 'incorrect translation'
                elif mutation == 'extra': row['unexpected'] = 'extra'
                else: row['folders %lld %lld'] = 'incorrect duplicate'
                with self.subTest(language=language, mutation=mutation): self.fails(changed)

    def test_simple_plural_mutations_are_rejected(self):
        mutations = ('missing-row', 'extra-row', 'missing-rule', 'extra-argument', 'missing-other',
                     'extra-category', 'wrong-type', 'raw-placeholder', 'extra-format-argument',
                     'wrong-position', 'write-token', 'outer-write', 'wrong-value', 'wrong-outer-position')
        for mutation in mutations:
            changed = copy.deepcopy(self.tables)
            table = changed['he.lproj/Localizable.stringsdict']; row = table['items %lld']; rule = row['count']
            if mutation == 'missing-row': del table['items %lld']
            elif mutation == 'extra-row': table['extra'] = row
            elif mutation == 'missing-rule': del row['count']
            elif mutation == 'extra-argument': row['extra'] = dict(rule)
            elif mutation == 'missing-other': del rule['other']
            elif mutation == 'extra-category': rule['few'] = 'he %lld items'
            elif mutation == 'wrong-type': rule[c.TYPE] = '@'
            elif mutation == 'raw-placeholder': rule['other'] = '%#@unresolved@'
            elif mutation == 'extra-format-argument': rule['other'] = '%lld %@'
            elif mutation == 'wrong-position': rule['other'] = '%2$lld'
            elif mutation == 'write-token': rule['other'] = '%lld %n'
            elif mutation == 'outer-write': row[c.FORMAT] += ' %n'
            elif mutation == 'wrong-value': rule['other'] = 'wrong %lld items'
            else: row[c.FORMAT] = '%2$#@count@'
            with self.subTest(mutation=mutation): self.fails(changed)

    def test_compound_plural_mutations_are_rejected_in_every_language(self):
        for language in c.LANGUAGES:
            for mutation in ('missing-argument', 'extra-argument', 'swapped', 'number', 'type',
                             'value', 'missing-category', 'extra-category', 'outer', 'write-token'):
                changed = copy.deepcopy(self.tables)
                row = changed[language + '.lproj/Localizable.stringsdict']['folders %lld %lld']
                if mutation == 'missing-argument': del row['second']
                elif mutation == 'extra-argument': row['third'] = dict(row['first'])
                elif mutation == 'swapped': row['first'], row['second'] = row['second'], row['first']
                elif mutation == 'number': row['second']['other'] = row['second']['other'].replace('%2$', '%1$')
                elif mutation == 'type': row['second'][c.TYPE] = '@'
                elif mutation == 'value': row['first']['other'] = 'wrong %1$lld folders'
                elif mutation == 'missing-category': del row['first']['other']
                elif mutation == 'extra-category': row['first']['few'] = 'extra %1$lld folders'
                elif mutation == 'outer': row[c.FORMAT] += ' %n'
                else: row['second']['other'] += ' %n'
                with self.subTest(language=language, mutation=mutation): self.fails(changed)

    def test_missing_translation_unsupported_schema_and_untranslated_state_fail_closed(self):
        for mutation in ('missing-language', 'state', 'unknown-variation', 'excluded', 'empty'):
            changed = copy.deepcopy(self.catalog)
            row = changed['strings']['label']
            if mutation == 'missing-language': del row['localizations']['he']
            elif mutation == 'state': row['localizations']['he']['stringUnit']['state'] = 'new'
            elif mutation == 'unknown-variation': row['localizations']['he'] = {'variations': {'device': {}}}
            elif mutation == 'excluded': row['shouldTranslate'] = False
            else: changed['strings'] = {}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): c.catalog_contract(changed)

    def test_invalid_compound_source_arguments_fail_closed(self):
        for mutation in ('duplicate-number', 'wrong-type', 'extra-format', 'duplicate-placeholder', 'wrong-position'):
            changed = copy.deepcopy(self.catalog)
            row = changed['strings']['folders %lld %lld']['localizations']['he']
            if mutation == 'duplicate-number': row['substitutions']['second']['argNum'] = 1
            elif mutation == 'wrong-type': row['substitutions']['second']['formatSpecifier'] = '@'
            elif mutation == 'extra-format': row['substitutions']['second']['variations']['plural']['other'] = unit('%arg %n')
            elif mutation == 'duplicate-placeholder': row['stringUnit']['value'] = '%#@first@ %#@first@'
            else: row['stringUnit']['value'] = '%2$#@first@ %#@second@'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): c.catalog_contract(changed)

    def test_duplicate_catalog_json_key_rejected(self):
        with self.assertRaises(ValueError): json.loads('{"a":1,"a":2}', object_pairs_hook=c.unique_object)

    def test_cli_hashes_actual_files_and_rejects_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); catalog = root / 'Localizable.xcstrings'
            catalog.write_text(json.dumps(self.catalog)); resources = root / 'resources'; resources.mkdir()
            for name, table in self.tables.items():
                path = resources / name; path.parent.mkdir(exist_ok=True)
                path.write_bytes(plistlib.dumps(table))
            report = root / 'report.json'
            command = [sys.executable, '-B', str(SCRIPT), '--catalog', str(catalog),
                       '--resources', str(resources), '--report', str(report)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(report.read_text())
            self.assertEqual(value['catalog_sha256'], hashlib.sha256(catalog.read_bytes()).hexdigest())
            self.assertEqual(len(value['compiled_resource_sha256']), 8)
            (resources / 'zh-Hant.lproj/Localizable.strings').write_bytes(plistlib.dumps({'label': 'wrong'}))
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(report.read_text())['status'], 'failed')

    def test_missing_or_linked_file_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / 'input'
            with self.assertRaises(ValueError): c.regular_bytes(path)
            target = root / 'target'; target.write_text('data'); path.symlink_to(target)
            with self.assertRaises(ValueError): c.regular_bytes(path)


if __name__ == '__main__':
    unittest.main()
