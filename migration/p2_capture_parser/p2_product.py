"""Read product bytes as data; independently replay resource and Mach-O checks."""
import ast
import plistlib
import re
import stat
import struct
from p2_archive import need, parse, sha, archive_receipt
from p2_sources import blob, PINS

LANGUAGES = ('en', 'zh-Hans', 'zh-Hant', 'he')


def literal(raw, name):
    for node in ast.parse(raw).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('missing source constant: ' + name)


def macho_platforms(raw):
    def thin(offset, size):
        need(size >= 28 and offset + size <= len(raw), 'truncated Mach-O')
        kind = raw[offset:offset+4]
        formats = {b'\xce\xfa\xed\xfe':('<',28),b'\xcf\xfa\xed\xfe':('<',32),
                   b'\xfe\xed\xfa\xce':('>',28),b'\xfe\xed\xfa\xcf':('>',32)}
        need(kind in formats, 'not a supported Mach-O executable')
        endian, header = formats[kind]
        cpu, subtype, filetype, count, length, flags = struct.unpack_from(endian+'6I', raw, offset+4)
        need(cpu in (0x01000007, 0x0100000c) and filetype == 2 and 0 < count <= 10000 and header + length <= size, 'unexpected Mach-O executable header')
        position = offset + header; end = position + length; platforms = []
        for _ in range(count):
            need(position + 8 <= end, 'truncated Mach-O load command')
            command, length = struct.unpack_from(endian+'II', raw, position)
            need(length >= 8 and length % 4 == 0 and position + length <= end, 'invalid Mach-O command length')
            if command == 0x32:
                need(length >= 24, 'truncated build-version command')
                platforms.append(struct.unpack_from(endian+'I', raw, position+8)[0])
            need(command not in (0x24, 0x25, 0x2f, 0x30), 'legacy/non-simulator platform declaration')
            position += length
        need(position == end and platforms == [7], 'Mach-O platform is not exclusively iOS Simulator')
        return platforms
    magic = raw[:4]
    fat = {b'\xca\xfe\xba\xbe':('>',False),b'\xbe\xba\xfe\xca':('<',False),
           b'\xca\xfe\xba\xbf':('>',True),b'\xbf\xba\xfe\xca':('<',True)}
    if magic not in fat:
        return thin(0, len(raw))
    endian, wide = fat[magic]; need(len(raw) >= 8, 'truncated fat Mach-O')
    count = struct.unpack_from(endian+'I', raw, 4)[0]; width = 32 if wide else 20
    need(0 < count <= 16 and 8 + count*width <= len(raw), 'fat Mach-O architecture bound')
    spans = []; result = []
    for index in range(count):
        row = struct.unpack_from(endian+('IIQQII' if wide else 'IIIII'), raw, 8+index*width)
        offset, size = row[2:4]
        need(offset >= 8+count*width and size > 0 and offset+size <= len(raw), 'fat Mach-O slice bound')
        need(all(offset+size <= a or offset >= b for a,b in spans), 'overlapping Mach-O slices')
        spans.append((offset,offset+size)); result.extend(thin(offset,size))
    return result


def check_format(value, expected):
    need(isinstance(value, str), 'plural format must be text')
    count = 0; index = 0
    while index < len(value):
        if value[index] != '%': index += 1; continue
        if value.startswith('%%', index): index += 2; continue
        token = '%1$lld' if value.startswith('%1$lld', index) else '%lld' if value.startswith('%lld', index) else None
        need(token, 'undeclared plural argument or format token')
        count += 1; index += len(token)
    need(count == expected, 'plural Int64 argument count differs')


def resources(app, runner, host, capture_commit):
    translations = parse(blob(runner, capture_commit, 'tests/fixtures/p2_localization_contracts/compiled-translations.json'))
    catalog = parse(blob(host, PINS['LiveContainer'][0], 'Resources/Localizable.xcstrings'))
    localization_source = blob(runner, capture_commit, 'scripts/p2_localization_contract.py')
    permission_source = blob(runner, capture_commit, 'scripts/p2_permission_contract.py')
    plurals = literal(localization_source, 'PLURAL_KEYS')
    compound = literal(localization_source, 'COMPOUND_PLURAL_KEYS')
    coverage = {'catalogKeys':2088,'plainScalarKeysPerLanguage':2081,'simplePluralKeys':6,'compoundPluralKeys':1}
    need(translations['schema'] == 'p2-compiled-translations-v2' and tuple(translations['languages']) == LANGUAGES and
         translations['coverage'] == coverage and len(catalog['strings']) == 2088 and len(plurals) == 6 and len(compound) == 1,
         'sealed source resource inventory differs')
    for language in LANGUAGES:
        values = plistlib.loads(app.raw(language+'.lproj/Localizable.strings'))
        plural_values = plistlib.loads(app.raw(language+'.lproj/Localizable.stringsdict'))
        expected = translations['translations'][language]
        need(len(expected) == 2081 and all(values.get(k) == v for k,v in expected.items()), 'compiled scalar translation differs')
        need(not set(compound) & set(values), 'compound plural duplicated as scalar')
        need(set(translations['compoundPlurals'][language]) == set(compound), 'compound plural inventory differs')
        for key, value in translations['compoundPlurals'][language].items():
            need(plural_values.get(key) == value, 'compiled compound plural differs')
        for key in plurals:
            row = plural_values[key]; fmt = row['NSStringLocalizedFormatKey']
            pattern = r'%(?:[1-9][0-9]*\$)?#@([A-Za-z_][A-Za-z0-9_]*)@'
            names = re.findall(pattern, fmt)
            need(len(names) == 1 and fmt.count('%#') + len(re.findall(r'%[1-9][0-9]*\$#',fmt)) == 1, 'ambiguous compiled plural variable')
            need(all(p == '1' for p in re.findall(r'%([1-9][0-9]*)\$#',fmt)), 'wrong plural argument position')
            check_format(re.sub(pattern,'',fmt), 0)
            rule = row[names[0]]
            need(rule.get('NSStringFormatSpecTypeKey') == 'NSStringPluralRuleType' and rule.get('NSStringFormatValueTypeKey') == 'lld', 'plural rule type differs')
            actual = {k:v for k,v in rule.items() if k not in ('NSStringFormatSpecTypeKey','NSStringFormatValueTypeKey')}
            expected = {k:v['stringUnit']['value'] for k,v in catalog['strings'][key]['localizations'][language]['variations']['plural'].items()}
            need(actual == expected and actual.get('other'), 'compiled plural categories/text differ')
            for value in actual.values(): check_format(value,1)
    permission_languages = literal(permission_source,'LANGUAGES')
    purpose_keys = literal(permission_source,'PURPOSE_KEYS')
    expected = literal(permission_source,'EXPECTED')
    expected.update(zh_CN=expected['zh-Hans'],zh_TW=expected['zh-Hant'],zh_HK=expected['zh-Hant'])
    need(tuple(permission_languages) == ('en','zh-Hans','zh-Hant','he','zh_CN','zh_TW','zh_HK') and len(purpose_keys) == 32, 'permission source inventory differs')
    for language in permission_languages:
        values = plistlib.loads(app.raw(language+'.lproj/InfoPlist.strings'))
        need(set(values) == set(purpose_keys)|{'CFBundleName'} and values['CFBundleName'] == 'LiveContainer' and
             all(values[key] == expected[language] for key in purpose_keys), 'compiled permission resource differs')
    return {'localization':{'status':'passed','failures':[],'coverage':coverage,
                           'runtimePluralProof':'requires native UI report and compound plural count combinations'},
            'exactPluralValues':{'status':'passed','failures':[],'checkedLanguageRows':24,'runtimePluralSelection':False},
            'permissions':{'status':'passed','failures':[],'selection':'OS-managed; independent of in-app language preference',
                           'inheritedTemporaryLocationDictionary':'malformed/unconfigured entry unchanged; requires separate reviewed change'}}


def verify_product(evidence, runner, host, capture_commit, result):
    name = 'LiveContainer.app.zip'
    with evidence.nested(name, links=True) as app:
        archive_receipt(result['appArchive'], app, evidence.files[name], evidence.entries[name].file_size)
        before = evidence.json('LiveContainer.app.before-inventory.json')
        after = evidence.json('LiveContainer.app.after-inventory.json')
        need(before == after == app.inventory, 'app archive before/after inventory differs')
        product_rows = {}
        for path, row in app.inventory.items():
            kind = row['type']; item = app.entries[path]
            need(item.external_attr >> 16 == {'file':stat.S_IFREG,'directory':stat.S_IFDIR,'symlink':stat.S_IFLNK}[kind] | row['mode'], 'app ZIP full mode differs')
            if kind == 'file':
                product_rows[path] = {'type':'regular','mode':row['mode'],'sha256':row['sha256'],
                                      'bytes':row['bytes'],'executable':bool(row['mode'] & 0o111)}
            elif kind == 'symlink':
                raw = row['target'].encode()
                product_rows[path] = {'type':'symlink','mode':row['mode'],'link':row['target'],'bytes':len(raw),'sha256':sha(raw)}
        info = plistlib.loads(app.raw('Info.plist'))
        need(info.get('CFBundleIdentifier') == 'com.kdt.livecontainer' and info.get('CFBundleExecutable') == 'LiveContainer' and
             info.get('CFBundlePackageType') == 'APPL' and info.get('CFBundleSupportedPlatforms') == ['iPhoneSimulator'], 'retained app identity differs')
        product = {'bundleIdentifier':'com.kdt.livecontainer','executable':'LiveContainer',
                   'infoPlistSHA256':app.files['Info.plist'],'executableSHA256':app.files['LiveContainer'],'files':product_rows}
        need(evidence.json('product-identity.json') == product, 'retained product bytes differ from identity')
        platforms = macho_platforms(app.raw('LiveContainer',128*1024**2))
        need(len(result['machOPlatforms']) == len(platforms) and all(x in ('IOSSIMULATOR','7') for x in result['machOPlatforms']), 'Mach-O raw/header platforms differ')
        resource = resources(app,runner,host,capture_commit)
        need(evidence.json('compiled-resources.json') == {'scope':'Genuine candidate simulator product compiled resources; no runtime acceptance',
             'bundleIdentifier':'com.kdt.livecontainer','productInventory':product_rows,**resource}, 'compiled resource receipt does not match retained bytes')
        return {'archive_sha256':evidence.files[name],'entries':len(app.entries),'file_count':len(product_rows),
                'executable_sha256':app.files['LiveContainer'],'info_plist_sha256':app.files['Info.plist'],
                'macho_simulator_slices':len(platforms),'resource_checks':resource}
