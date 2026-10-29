"""Additive Russian candidate proof. Historical four-language contracts stay frozen."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import zipfile

CATALOG = 'Resources/Localizable.xcstrings'
SOURCE_FILES = (CATALOG, 'LiveContainerSwiftUI/Utilities/V3Localization.swift',
    'LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift', 'LiveContainer/Localization.m',
    'Resources/ru.lproj/InfoPlist.strings', 'Resources/en.lproj/InfoPlist.strings',
    'LiveContainer.xcodeproj/project.pbxproj', 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
    'tests/test_russian_localization.py',
    'tests/fixtures/russian_localization.swift')
COUNTS = (0, 1, 2, 5, 11, 12, 14, 21, 22, 25, 101, 111)
CATEGORIES = {'one', 'few', 'many', 'other'}
STATUS = 'RUSSIAN_COMPILED_RESOURCES_AND_PRODUCTION_RENDERER_PASS_NOT_DEVICE_VISUAL_PROOF'
OTHER_DISCLOSURE = 'compiled value verified; not selected naturally by Int64 Russian cardinal rules'
REFRESH_TRUTH_PLURAL = 'Profile renewal was verified for the attempted targets; %lld running app(s) were skipped. Future launch and certificate trust are not guaranteed.'
REFRESH_TRUTH_RUSSIAN_SHA256 = '9ab2adcd246e067e3dd300635a5cd323fc936e9855dbfcc406942ac149640718'

def require(ok, message):
    if not ok: raise ValueError(message)

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def source_identity(live):
    live = Path(live)
    def git(*args):
        return subprocess.check_output(['git', '--no-replace-objects', '-C', str(live), *args], timeout=15)
    from verify_candidate_graph import load_candidate_graph
    pin = load_candidate_graph()['owners']['LiveContainer'][1]
    head = git('rev-parse', 'HEAD').decode().strip()
    require(head == pin, 'Russian source HEAD differs from candidate graph')
    hashes = {}
    for relative in SOURCE_FILES:
        path = live / relative
        require(path.is_file() and not path.is_symlink(), 'Russian input missing or linked: ' + relative)
        require(path.read_bytes() == git('cat-file', 'blob', 'HEAD:' + relative), 'Russian input differs from committed source: ' + relative)
        hashes[relative] = sha(path)
    return {'commit': head, 'tree': git('rev-parse', 'HEAD^{tree}').decode().strip(), 'sha256': hashes}

def unit(row):
    require(isinstance(row, dict) and set(row) == {'stringUnit'}, 'Unexpected Russian unit shape')
    value = row['stringUnit']
    require(value.get('state') == 'translated' and isinstance(value.get('value'), str) and value['value'], 'Russian translation absent or unfinished')
    return value['value']

def russian_catalog(catalog):
    entries = catalog['strings']; scalars = {}; plurals = {}
    for key, entry in entries.items():
        row = entry.get('localizations', {}).get('ru')
        require(isinstance(row, dict), 'Missing explicit Russian entry: ' + key)
        if set(row) == {'stringUnit'}: scalars[key] = unit(row)
        elif set(row) == {'variations'}:
            require(set(row['variations']) == {'plural'}, 'Unknown Russian variation')
            rules = row['variations']['plural']; require(set(rules) == CATEGORIES, 'Russian plural categories incomplete: ' + key)
            plurals[key] = {'kind': 'simple', 'values': {k: unit(v) for k, v in rules.items()}}
        elif set(row) == {'stringUnit', 'substitutions'}:
            outer = unit({'stringUnit': row['stringUnit']}); substitutions = {}
            for name, rule in row['substitutions'].items():
                require(set(rule) == {'argNum', 'formatSpecifier', 'variations'} and rule['formatSpecifier'] == 'lld', 'Unexpected Russian substitution shape')
                require(type(rule['argNum']) is int and rule['argNum'] in (1, 2), 'Unexpected Russian substitution argument')
                require(set(rule['variations']) == {'plural'} and set(rule['variations']['plural']) == CATEGORIES, 'Russian substitution categories incomplete')
                substitutions[name] = {'position': rule['argNum'], 'values': {k: unit(v) for k, v in rule['variations']['plural'].items()}}
            require(sorted(v['position'] for v in substitutions.values()) == [1, 2], 'Two distinct Russian substitution arguments required')
            plurals[key] = {'kind': 'compound', 'format': outer, 'substitutions': substitutions}
        else: raise ValueError('Unknown Russian catalog row: ' + key)
    require(len(scalars) + len(plurals) == len(entries) and len(entries) >= 2100, 'Incomplete Russian catalog coverage')
    added = int(REFRESH_TRUTH_PLURAL in entries)
    if added:
        row = entries[REFRESH_TRUTH_PLURAL]['localizations']['ru']
        require(hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == REFRESH_TRUTH_RUSSIAN_SHA256, 'Reviewed refresh truth Russian plural differs')
        require(plurals.get(REFRESH_TRUTH_PLURAL, {}).get('kind') == 'simple', 'Refresh truth plural shape differs')
    require(sum(v['kind'] == 'simple' for v in plurals.values()) == 6 + added and sum(v['kind'] == 'compound' for v in plurals.values()) == 1, 'Reviewed plural inventory differs')
    return scalars, plurals

def check_rule(actual, values, position=None):
    expected = {'NSStringFormatSpecTypeKey': 'NSStringPluralRuleType', 'NSStringFormatValueTypeKey': 'lld'}
    expected.update({k: v.replace('%arg', '%' + str(position) + '$lld') if position else v for k, v in values.items()})
    require(isinstance(actual, dict) and set(actual) == set(expected), 'Compiled Russian plural rule fields differ')
    for key, value in expected.items():
        observed = actual[key]
        if position is None and isinstance(observed, str): observed = observed.replace('%1$lld', '%lld')
        require(observed == value, 'Compiled Russian plural value differs: ' + key)

def verify_tables(catalog, strings, dictionary):
    scalars, plurals = russian_catalog(catalog)
    require(strings == scalars, 'Compiled Russian scalar table differs from full source catalog')
    require(set(dictionary) == set(plurals), 'Compiled Russian plural key inventory differs')
    for key, spec in plurals.items():
        actual = dictionary[key]; require(isinstance(actual, dict), 'Invalid compiled Russian plural')
        fmt = actual.get('NSStringLocalizedFormatKey'); require(isinstance(fmt, str), 'Missing compiled Russian format')
        if spec['kind'] == 'simple':
            found = re.fullmatch(r'%(?:1\$)?#@([A-Za-z_][A-Za-z0-9_]*)@', fmt)
            require(found is not None, 'Unexpected simple Russian plural outer format')
            name = found.group(1); require(set(actual) == {'NSStringLocalizedFormatKey', name}, 'Unexpected simple Russian fields')
            check_rule(actual[name], spec['values'])
        else:
            require(set(actual) == {'NSStringLocalizedFormatKey', *spec['substitutions']}, 'Unexpected compound Russian fields')
            expected = spec['format']
            for name, rule in spec['substitutions'].items():
                expected = expected.replace('%#@' + name + '@', '%' + str(rule['position']) + '$#@' + name + '@')
                check_rule(actual[name], rule['values'], rule['position'])
            require(fmt == expected, 'Compiled Russian compound argument positions differ')
    return {'catalog_keys': len(scalars) + len(plurals), 'scalar_keys': len(scalars), 'simple_plurals': sum(v['kind'] == 'simple' for v in plurals.values()), 'compound_plurals': 1, 'categories': sorted(CATEGORIES)}

def category(n):
    if n % 10 == 1 and n % 100 != 11: return 'one'
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14): return 'few'
    return 'many'

def cases(catalog):
    _, plurals = russian_catalog(catalog); result = []
    for key, spec in plurals.items():
        if spec['kind'] == 'simple':
            for n in COUNTS:
                value = spec['values'][category(n)].replace('%1$lld', str(n)).replace('%lld', str(n))
                result.append({'key': key, 'arguments': [n], 'expected': value})
        else:
            for a in COUNTS:
                for b in COUNTS:
                    value = spec['format']
                    for name, rule in spec['substitutions'].items():
                        n = (a, b)[rule['position'] - 1]
                        value = value.replace('%#@' + name + '@', rule['values'][category(n)].replace('%arg', str(n)))
                    result.append({'key': key, 'arguments': [a, b], 'expected': value})
    return result

SWIFT = r'''import Foundation
struct Case: Decodable { let key: String; let arguments: [Int64]; let expected: String }
let bundle = Bundle(path: CommandLine.arguments[1])!
let cases = try JSONDecoder().decode([Case].self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2])))
let scalars = try JSONDecoder().decode([String:String].self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[3])))
for (key, expected) in scalars { precondition(V3Localization.string(key, language: .russian, bundle: bundle) == expected) }
precondition(V3AppLanguage.resolve("ru-RU") == .russian && !V3AppLanguage.russian.isRightToLeft)
for item in cases {
    let message: V3DisplayMessage
    if item.arguments.count == 1 { message = .localized(item.key, item.arguments[0]) }
    else { precondition(item.arguments.count == 2); message = .localized(item.key, item.arguments[0], item.arguments[1]) }
    let actual = message.rendered(language: .russian, bundle: bundle)
    guard actual == item.expected else { fatalError("Russian production plural mismatch for owned case") }
}
print("RUSSIAN_PRODUCTION_COMPILED_PLURALS_PASS count=\(cases.count)")
'''

def decode_plist(data):
    # Xcode may emit binary/XML or OpenStep .strings depending on the phase.
    # Parse values without rewriting the compiled artifact being authenticated.
    try:
        return plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError):
        with tempfile.TemporaryDirectory(prefix='russian-plist-') as folder:
            path = Path(folder) / 'compiled.strings'; path.write_bytes(data)
            converted = subprocess.check_output(['plutil', '-convert', 'xml1', '-o', '-', str(path)], timeout=15)
            return plistlib.loads(converted)

def compiled_bundle(live, bundle):
    catalog = json.loads((Path(live) / CATALOG).read_text())
    values = []
    for name in ('Localizable.strings', 'Localizable.stringsdict'):
        path = Path(bundle) / 'ru.lproj' / name
        require(path.is_file() and not path.is_symlink() and not path.parent.is_symlink(), 'Missing or linked compiled Russian resource')
        values.append(decode_plist(path.read_bytes()))
    return verify_tables(catalog, *values)

def run(live, output):
    live = Path(live).resolve(strict=True); output = Path(output)
    require(not output.exists(), 'Russian evidence directory must be fresh')
    output.mkdir(parents=True); identity = source_identity(live)
    def command(name, argv, cwd=None, timeout=120):
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        (output / (name + '.log')).write_text(p.stdout + p.stderr)
        require(p.returncode == 0, 'Russian qualification command failed: ' + name)
        return p.stdout
    runner = "import sys,unittest; s=unittest.defaultTestLoader.discover('tests',pattern='test_russian_localization.py'); r=unittest.TextTestRunner(verbosity=2).run(s); sys.exit(0 if s.countTestCases()>0 and r.wasSuccessful() and not r.skipped else 1)"
    # Retain case count before run: unittest may replace completed cases with None.
    runner = runner.replace("r=unittest", "n=s.countTestCases(); r=unittest").replace("s.countTestCases()>0 and", "n==5 and")
    command('source-native', [sys.executable, '-B', '-c', runner], live)
    bundle = output / 'Russian.bundle'; bundle.mkdir()
    (bundle / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'invalid.fixture.russian', 'CFBundleName': 'Russian', 'CFBundlePackageType': 'BNDL'}))
    command('catalog-compile', ['xcrun', 'xcstringstool', 'compile', '--output-directory', str(bundle), str(live / CATALOG)])
    coverage = compiled_bundle(live, bundle)
    command('permissions-compile', ['plutil', '-convert', 'binary1', '-o', str(bundle / 'ru.lproj/InfoPlist.strings'), str(live / 'Resources/ru.lproj/InfoPlist.strings')])
    permissions = plistlib.loads((bundle / 'ru.lproj/InfoPlist.strings').read_bytes())
    require(isinstance(permissions, dict) and len(permissions) >= 30 and all(isinstance(k, str) and isinstance(v, str) and v for k, v in permissions.items()), 'Incomplete compiled Russian permission resources')
    expected = cases(json.loads((live / CATALOG).read_text()))
    (output / 'cases.json').write_text(json.dumps(expected, ensure_ascii=False))
    scalar_values, _ = russian_catalog(json.loads((live / CATALOG).read_text()))
    (output / 'scalars.json').write_text(json.dumps(scalar_values, ensure_ascii=False))
    (output / 'main.swift').write_text(SWIFT)
    # Keep the executable outside uploaded evidence; retain its hash and logs.
    with tempfile.TemporaryDirectory(prefix='russian-native-') as native:
        binary = Path(native) / 'russian-plurals'
        command('renderer-compile', ['swiftc', str(live / SOURCE_FILES[1]), str(output / 'main.swift'), '-o', str(binary)])
        binary_sha = sha(binary)
        stdout = command('renderer-run', [str(binary), str(bundle), str(output / 'cases.json'), str(output / 'scalars.json')], timeout=30)
    require(stdout.strip() == 'RUSSIAN_PRODUCTION_COMPILED_PLURALS_PASS count=' + str(len(expected)), 'Russian runtime marker/count differs')
    require(identity == source_identity(live), 'Russian source changed during qualification')
    report = {'status': STATUS, 'source': identity, 'coverage': coverage, 'native_integer_cases': len(expected), 'native_scalar_cases': len(scalar_values),
        'integer_counts': list(COUNTS), 'other_category': OTHER_DISCLOSURE, 'native_fixture_sha256': binary_sha,
        'visual_device_proof': False, 'files_sha256': {p.relative_to(output).as_posix(): sha(p) for p in output.rglob('*') if p.is_file()}}
    (output / 'receipt.json').write_text(json.dumps(report, indent=2))
    return report

def validate_disclosures(value, integer_cases, scalar_cases):
    require(value.get('visual_device_proof') is False, 'Russian receipt must not claim visual device proof')
    require(value.get('integer_counts') == list(COUNTS) and all(type(n) is int for n in value['integer_counts']), 'Russian count matrix differs')
    require(value.get('other_category') == OTHER_DISCLOSURE, 'Russian other-category disclosure differs')
    for key, expected in [('native_integer_cases', integer_cases), ('native_scalar_cases', scalar_cases)]:
        require(type(value.get(key)) is int and value[key] == expected, 'Russian native count differs: ' + key)
    require(re.fullmatch(r'[0-9a-f]{64}', value.get('native_fixture_sha256', '')) is not None, 'Russian fixture hash missing')

def verify_receipt(path, live):
    path = Path(path); value = json.loads(path.read_text())
    require(value.get('status') == STATUS and value.get('source') == source_identity(live), 'Russian receipt source/status differs')
    catalog = json.loads((Path(live) / CATALOG).read_text()); expected_cases = cases(catalog); expected_scalars = russian_catalog(catalog)[0]
    validate_disclosures(value, len(expected_cases), len(expected_scalars))
    require(json.loads((path.parent / 'cases.json').read_text()) == expected_cases and json.loads((path.parent / 'scalars.json').read_text()) == expected_scalars, 'Russian native expected values differ')
    require((path.parent / 'main.swift').read_text() == SWIFT, 'Russian native fixture body differs')
    require((path.parent / 'renderer-run.log').read_text().strip() == 'RUSSIAN_PRODUCTION_COMPILED_PLURALS_PASS count=' + str(len(expected_cases)), 'Russian runtime log differs')
    native_log = (path.parent / 'source-native.log').read_text()
    require(re.search(r'Ran 5 tests in ', native_log) is not None and '\nOK\n' in native_log and 'skipped' not in native_log, 'Russian source-native tests did not pass without skips')
    files = value.get('files_sha256', {})
    require(files and files == {p.relative_to(path.parent).as_posix(): sha(p) for p in path.parent.rglob('*') if p.is_file() and p != path}, 'Russian receipt evidence changed')
    require(value.get('coverage') == compiled_bundle(live, path.parent / 'Russian.bundle'), 'Russian compiled coverage differs')
    return value

def zip_resource(archive, name):
    info = archive.getinfo(name)
    require(not info.is_dir() and stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG) and info.file_size <= 32 * 1024 * 1024, 'Nonregular or oversized Russian ZIP resource')
    parts = name.split('/')
    for i in range(1, len(parts)):
        parent = '/'.join(parts[:i])
        for candidate in (parent, parent + '/'):
            if candidate in archive.namelist():
                mode = stat.S_IFMT(archive.getinfo(candidate).external_attr >> 16)
                require(mode in (0, stat.S_IFDIR), 'Non-directory Russian ZIP parent')
    return archive.read(name)

def verify_ipa(path, live, *, expected_permissions):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist(); require(len(names) == len(set(names)), 'Duplicate IPA entry')
        roots = {n.split('/')[1] for n in names if n.startswith('Payload/') and len(n.split('/')) > 2 and n.split('/')[1].endswith('.app')}
        require(len(roots) == 1, 'Ambiguous Russian host bundle')
        prefix = 'Payload/' + next(iter(roots)) + '/ru.lproj/'
        resources = [prefix + 'Localizable.' + suffix for suffix in ('strings', 'stringsdict')]
        for name in resources:
            require(name in names, 'Missing or linked IPA Russian resource')
            zip_resource(archive, name)
        permission_name = prefix + 'InfoPlist.strings'
        require(permission_name in names, 'Missing or linked IPA Russian permission resource')
        require(decode_plist(zip_resource(archive, permission_name)) == expected_permissions, 'Packaged Russian permission resources differ')
        coverage = verify_tables(json.loads((Path(live) / CATALOG).read_text()), *(decode_plist(zip_resource(archive, n)) for n in resources))
        resources.append(permission_name)
        return {'status': 'COMPILED_RUSSIAN_IPA_RESOURCES_MATCH_SOURCE', 'coverage': coverage,
            'resource_sha256': {n: hashlib.sha256(zip_resource(archive, n)).hexdigest() for n in resources}, 'ipa_sha256': sha(path)}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--live', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); run(args.live, args.output)
