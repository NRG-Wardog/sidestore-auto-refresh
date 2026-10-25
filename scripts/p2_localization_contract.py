"""Compiled localization checks. Resource presence is not runtime plural proof."""
import plistlib, zipfile, re, json
from pathlib import Path
LANGUAGES = ('en', 'zh-Hans', 'zh-Hant', 'he')
TRANSLATION_MANIFEST = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/p2_localization_contracts/compiled-translations.json').read_text())
if TRANSLATION_MANIFEST.get('schema') != 'p2-compiled-translations-v2' or tuple(TRANSLATION_MANIFEST.get('languages', [])) != LANGUAGES:
    raise ValueError('Invalid sealed localization manifest')
REQUIRED_TRANSLATIONS = TRANSLATION_MANIFEST['translations']
if set(REQUIRED_TRANSLATIONS) != set(LANGUAGES) or any(not isinstance(REQUIRED_TRANSLATIONS[x], dict) or not REQUIRED_TRANSLATIONS[x] for x in LANGUAGES):
    raise ValueError('Incomplete sealed localization manifest')
PLURAL_KEYS = ('lc.appList.appCounter %lld', 'lc.settings.cleanDataFolderConfirm %lld', '%lld apps', '%lld selected', 'Refresh completed for the verified app targets; %lld running app(s) were skipped.', 'Refresh completed. Results for this run were verified; %lld running app(s) were skipped.')
COMPOUND_PLURAL_KEYS = ('lc.settings.moveDanglingFolderComplete %lld %lld',)
COMPOUND_PLURALS = TRANSLATION_MANIFEST['compoundPlurals']
if set(COMPOUND_PLURALS) != set(LANGUAGES) or any(set(COMPOUND_PLURALS[x]) != set(COMPOUND_PLURAL_KEYS) for x in LANGUAGES):
    raise ValueError('Incomplete sealed compound plural manifest')
if TRANSLATION_MANIFEST.get('coverage') != {'catalogKeys': 2088, 'plainScalarKeysPerLanguage': 2081, 'simplePluralKeys': 6, 'compoundPluralKeys': 1}:
    raise ValueError('Wrong explicit catalog coverage accounting')
if any(len(REQUIRED_TRANSLATIONS[x]) != 2081 or set(REQUIRED_TRANSLATIONS[x]) & (set(PLURAL_KEYS) | set(COMPOUND_PLURAL_KEYS)) for x in LANGUAGES):
    raise ValueError('Scalar/plural classes overlap or scalar count drifted')


def validate_int64_format(value, *, expect_count):
    """One supplied Int64; no extra argument, position, width-star or write token."""
    count = 0
    index = 0
    while index < len(value):
        if value[index] != '%': index += 1; continue
        if value.startswith('%%', index): index += 2; continue
        token = '%1$lld' if value.startswith('%1$lld', index) else '%lld' if value.startswith('%lld', index) else None
        if token is None: raise ValueError('Undeclared printf argument or format token')
        count += 1; index += len(token)
    if count != (1 if expect_count else 0): raise ValueError('Unexpected Int64 argument count')


def verify_compiled_resources(read, names):
    failures = []
    for language in LANGUAGES:
        for suffix in ('strings', 'stringsdict'):
            name = f'{language}.lproj/Localizable.{suffix}'
            if name not in names:
                failures.append('Missing compiled localization: ' + name); continue
            try:
                value = plistlib.loads(read(name))
                if not isinstance(value, dict) or not value: raise ValueError('empty catalog')
                if suffix == 'strings':
                    if any(key in value for key in COMPOUND_PLURAL_KEYS): raise ValueError('Compound plural duplicated in scalar table')
                    for key, expected in REQUIRED_TRANSLATIONS[language].items():
                        if value.get(key) != expected: raise ValueError('Missing or wrong-language translation: ' + key)
                if suffix == 'stringsdict':
                    for key, expected in COMPOUND_PLURALS[language].items():
                        if value.get(key) != expected: raise ValueError('Missing or altered compound plural format/argument/category/value: ' + key)
                    for key in PLURAL_KEYS:
                        row=value.get(key)
                        if not isinstance(row, dict): raise ValueError('Missing plural row: ' + key)
                        format_key = row.get('NSStringLocalizedFormatKey')
                        if not isinstance(format_key, str): raise ValueError('Missing native plural format: ' + key)
                        markers = re.findall(r'%(?:[1-9][0-9]*\$)?#@([A-Za-z_][A-Za-z0-9_]*)@', format_key)
                        if len(markers) != 1 or format_key.count('%#') + len(re.findall(r'%[1-9][0-9]*\$#', format_key)) != 1:
                            raise ValueError('Invalid native plural placeholder: ' + key)
                        outer = re.sub(r'%(?:[1-9][0-9]*\$)?#@[A-Za-z_][A-Za-z0-9_]*@', '', format_key)
                        validate_int64_format(outer, expect_count=False)
                        if any(position != '1' for position in re.findall(r'%([1-9][0-9]*)\$#', format_key)): raise ValueError('Invalid outer argument position')
                        rule = row.get(markers[0])
                        if (not isinstance(rule, dict) or rule.get('NSStringFormatSpecTypeKey') != 'NSStringPluralRuleType'
                                or rule.get('NSStringFormatValueTypeKey') != 'lld'
                                or not isinstance(rule.get('other'), str) or not rule['other']):
                            raise ValueError('Incomplete Int64 plural rule: ' + key)
                        for category in ('zero', 'one', 'two', 'few', 'many', 'other'):
                            if category in rule and (not isinstance(rule[category], str) or not rule[category]
                                                     or '%#' in rule[category]
                                                     or re.search(r'%(?:[1-9][0-9]*\$)?lld', rule[category]) is None):
                                raise ValueError('Invalid plural category: ' + key + '/' + category)
                            if category in rule: validate_int64_format(rule[category], expect_count=True)
            except Exception as error:
                failures.append(name + ': ' + str(error))
    return {'status': 'passed' if not failures else 'failed', 'failures': failures,
            'coverage': TRANSLATION_MANIFEST['coverage'],
            'runtimePluralProof': 'requires native UI report and compound plural count combinations'}

def verify_app(app):
    app=Path(app)
    return verify_compiled_resources(lambda n:(app/n).read_bytes(),{str(p.relative_to(app)) for p in app.rglob('*') if p.is_file()})

def verify_ipa(path, *, capability_required):
    if type(capability_required) is not bool: raise ValueError("An authenticated explicit capability boolean is required")
    if not capability_required:
        return {'status':'not_applicable','reason':'pre-P2 candidate does not declare localization capability'}
    with zipfile.ZipFile(path) as archive:
        roots={n.split('/')[1] for n in archive.namelist() if n.startswith('Payload/') and len(n.split('/'))>2 and n.split('/')[1].endswith('.app')}
        if len(roots)!=1: return {'status':'failed','failures':['Ambiguous IPA host app']}
        prefix='Payload/'+next(iter(roots))+'/'
        names={n[len(prefix):] for n in archive.namelist() if n.startswith(prefix)}
        return verify_compiled_resources(lambda n:archive.read(prefix+n),names)
