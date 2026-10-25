"""Check real compiled resources against the supplied exact production catalog.

Forward-ports the format/resource negative-control intent of SAR 0cc9078e
scripts/p2_localization_contract.py without its sealed old translations,
renderer, simulator, owner selection or historical capability skips.
This proves resource correspondence, not locale switching or device behavior.
"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess

LANGUAGES = ('en', 'zh-Hans', 'zh-Hant', 'he')
CATEGORIES = {'zero', 'one', 'two', 'few', 'many', 'other'}
FORMAT = 'NSStringLocalizedFormatKey'
SPEC = 'NSStringFormatSpecTypeKey'
TYPE = 'NSStringFormatValueTypeKey'
MARKER = re.compile(r'%(?:(\d+)\$)?#@([A-Za-z_][A-Za-z0-9_]*)@')
MAX_BYTES = 16 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key: ' + key)
        result[key] = value
    return result


def validate_int64_format(value, *, argument=1, expect_count=True):
    """Accept one declared Int64, never width-star, extra argument or %n."""
    require(isinstance(value, str), 'Format is not a string')
    count, index = 0, 0
    while index < len(value):
        if value[index] != '%':
            index += 1
            continue
        if value.startswith('%%', index):
            index += 2
            continue
        match = re.match(r'%(?:(\d+)\$)?lld', value[index:])
        require(match is not None, 'Undeclared printf token')
        require((int(match[1]) if match[1] else 1) == argument,
                'Wrong Int64 argument position')
        count += 1
        index += len(match[0])
    require(count == int(expect_count), 'Unexpected Int64 argument count')


def unit(row):
    require(isinstance(row, dict) and set(row) == {'stringUnit'}, 'Unsupported string unit')
    value = row['stringUnit']
    require(isinstance(value, dict) and value.get('state') == 'translated'
            and isinstance(value.get('value'), str), 'Missing translated value')
    return value['value']


def categories(row, *, argument=1, substitution=False):
    require(isinstance(row, dict) and set(row) == {'variations'}, 'Unsupported plural shape')
    variations = row['variations']
    require(isinstance(variations, dict) and set(variations) == {'plural'}, 'Unsupported variation')
    values = variations['plural']
    require(isinstance(values, dict) and 'other' in values and set(values) <= CATEGORIES,
            'Invalid plural categories')
    result = {}
    for category, entry in values.items():
        value = unit(entry)
        require(bool(value), 'Empty plural category')
        if substitution:
            require(value.count('%arg') == 1, 'Compound category must declare one %arg')
            value = value.replace('%arg', '%' + str(argument) + '$lld')
        validate_int64_format(value, argument=argument)
        result[category] = value
    return result


def catalog_contract(catalog):
    require(isinstance(catalog, dict) and catalog.get('sourceLanguage') == 'en'
            and str(catalog.get('version')) == '1.0', 'Unsupported catalog header')
    strings = catalog.get('strings')
    require(isinstance(strings, dict) and bool(strings), 'Empty catalog')
    result = {}
    shapes = {}
    for language in LANGUAGES:
        scalar, simple, compound = {}, {}, {}
        for key, row in strings.items():
            require(isinstance(row, dict) and row.get('shouldTranslate') is not False,
                    'Unsupported excluded catalog key: ' + key)
            localized = row.get('localizations', {}).get(language)
            require(isinstance(localized, dict), 'Missing translation: ' + language + '/' + key)
            if set(localized) == {'stringUnit'}:
                scalar[key] = unit(localized)
            elif set(localized) == {'variations'}:
                simple[key] = categories(localized)
            elif set(localized) == {'stringUnit', 'substitutions'}:
                outer = unit({'stringUnit': localized['stringUnit']})
                substitutions = localized['substitutions']
                require(isinstance(substitutions, dict) and bool(substitutions), 'Empty substitutions')
                matches = list(MARKER.finditer(outer))
                require(len(matches) == len(substitutions)
                        and {m[2] for m in matches} == set(substitutions),
                        'Compound placeholders differ from arguments')
                validate_int64_format(MARKER.sub('', outer), expect_count=False)
                expected, numbers = {}, []
                for name, item in substitutions.items():
                    require(isinstance(item, dict) and set(item) == {'argNum', 'formatSpecifier', 'variations'},
                            'Unsupported substitution schema')
                    number = item['argNum']
                    require(type(number) is int and number > 0 and item['formatSpecifier'] == 'lld',
                            'Invalid substitution argument')
                    numbers.append(number)
                    expected[name] = {SPEC: 'NSStringPluralRuleType', TYPE: 'lld',
                                      **categories({'variations': item['variations']},
                                                   argument=number, substitution=True)}
                require(sorted(numbers) == list(range(1, len(substitutions) + 1)),
                        'Substitution positions must be unique and contiguous')
                def positional(match):
                    number = substitutions[match[2]]['argNum']
                    require(match[1] is None or int(match[1]) == number, 'Wrong compound outer position')
                    return '%' + str(number) + '$#@' + match[2] + '@'
                expected[FORMAT] = MARKER.sub(positional, outer)
                compound[key] = expected
            else:
                raise ValueError('Unsupported localization schema: ' + language + '/' + key)
        shape = (set(scalar), set(simple), set(compound))
        if shapes:
            require(shape == shapes['keys'], 'Catalog key classes differ between languages')
        shapes['keys'] = shape
        result[language] = {'scalar': scalar, 'simple': simple, 'compound': compound}
    return result


def verify_compiled_resources(contract, tables):
    """Tables are parsed compiled plists, never generated expectations as proof."""
    failures = []
    for language in LANGUAGES:
        try:
            expected = contract[language]
            scalar = tables[language + '.lproj/Localizable.strings']
            plural = tables[language + '.lproj/Localizable.stringsdict']
            require(isinstance(scalar, dict) and scalar == expected['scalar'],
                    'Scalar keys/values/types differ from current catalog')
            require(isinstance(plural, dict)
                    and set(plural) == set(expected['simple']) | set(expected['compound']),
                    'Plural key inventory differs')
            for key, expected_categories in expected['simple'].items():
                row = plural[key]
                require(isinstance(row, dict) and isinstance(row.get(FORMAT), str), 'Missing plural format')
                marker = MARKER.fullmatch(row[FORMAT])
                require(marker is not None and marker[1] in (None, '1'), 'Invalid simple outer argument')
                require(set(row) == {FORMAT, marker[2]}, 'Extra/missing simple plural argument')
                rule = row[marker[2]]
                require(isinstance(rule, dict) and rule.get(SPEC) == 'NSStringPluralRuleType'
                        and rule.get(TYPE) == 'lld' and set(rule) == {SPEC, TYPE} | set(expected_categories),
                        'Plural category/type inventory differs')
                for category, value in expected_categories.items():
                    actual = rule[category]
                    validate_int64_format(actual)
                    # Xcode can make the single argument explicitly positional.
                    require(actual.replace('%1$lld', '%lld') == value.replace('%1$lld', '%lld'),
                            'Plural value/language differs')
            for key, expected_row in expected['compound'].items():
                require(plural[key] == expected_row, 'Compound format/argument/category/value differs')
        except (ValueError, KeyError, TypeError) as error:
            failures.append(language + ': ' + str(error))
    first = contract[LANGUAGES[0]]
    return {'schema': 'current-catalog-compiled-resources-v1',
            'status': 'passed' if not failures else 'failed', 'failures': failures,
            'languages': list(LANGUAGES),
            'coverage': {kind + '_keys_per_language': len(first[kind]) for kind in ('scalar', 'simple', 'compound')},
            'scope': 'Compiled resource correspondence only; not runtime locale, UI, device or authentication proof.'}


def regular_bytes(path):
    require(path.is_file() and not path.is_symlink(), 'Missing or linked input: ' + str(path))
    require(path.stat().st_size <= MAX_BYTES, 'Input exceeds resource bound')
    return path.read_bytes()


def read_compiled(path):
    raw = regular_bytes(path)
    try:
        value = plistlib.loads(raw)
    except plistlib.InvalidFileException:
        # Apple's compiler may emit OpenStep .strings; convert read-only to stdout.
        converted = subprocess.run(['plutil', '-convert', 'xml1', '-o', '-', str(path)],
                                   check=True, capture_output=True, timeout=30)
        require(len(converted.stdout) <= MAX_BYTES, 'Converted plist exceeds bound')
        value = plistlib.loads(converted.stdout)
    return value, hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--resources', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    raw = regular_bytes(args.catalog)
    contract = catalog_contract(json.loads(raw, object_pairs_hook=unique_object))
    require(args.resources.is_dir() and not args.resources.is_symlink(), 'Missing or linked resource root')
    tables, hashes = {}, {}
    for language in LANGUAGES:
        directory = args.resources / (language + '.lproj')
        require(directory.is_dir() and not directory.is_symlink(), 'Missing or linked language directory')
        for suffix in ('strings', 'stringsdict'):
            name = language + '.lproj/Localizable.' + suffix
            tables[name], hashes[name] = read_compiled(args.resources / name)
    report = verify_compiled_resources(contract, tables)
    report.update(catalog_sha256=hashlib.sha256(raw).hexdigest(), compiled_resource_sha256=hashes)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'coverage': report['coverage']}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
