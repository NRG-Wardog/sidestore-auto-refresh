"""Exact generated-runner and requested launch-policy contract; no app edits."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import plistlib
import stat
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
TARGET = 'HostSettingsProbeUITests'
RUNNER_ID = 'org.sidestore.audit.hostsettings.uitests.xctrunner'
TEST_ID = 'org.sidestore.audit.hostsettings.uitests'


def require(value, message):
    if not value:
        raise ValueError(message)


def exact(actual, expected, label='generated configuration'):
    require(type(actual) is type(expected), 'Wrong exact type: ' + label)
    if type(expected) is dict:
        require(set(actual) == set(expected), 'Unexpected/missing keys: ' + label)
        for key in expected:
            exact(actual[key], expected[key], label + '.' + key)
    elif type(expected) is list:
        require(len(actual) == len(expected), 'Wrong count: ' + label)
        for i, (a, b) in enumerate(zip(actual, expected)):
            exact(a, b, label + '[' + str(i) + ']')
    else:
        require(actual == expected, 'Value differs: ' + label)


def reference():
    return json.loads((HERE / 'generated-contract.json').read_text())


def product_closure(testroot):
    """Exact reviewed product layout, including the DYLD search root siblings."""
    testroot = Path(testroot)
    require(testroot.is_dir() and not testroot.is_symlink(), 'Missing/unlinked product root required')
    expected = json.loads((HERE / 'products-layout.json').read_text())
    actual, payloads = {}, {}
    for path in sorted(testroot.rglob('*')):
        st = path.lstat()
        kind = 'directory' if stat.S_ISDIR(st.st_mode) else 'regular' if stat.S_ISREG(st.st_mode) else 'forbidden'
        name = path.relative_to(testroot).as_posix()
        actual[name] = {'type': kind, 'mode': stat.S_IMODE(st.st_mode)}
        require(kind != 'forbidden', 'Nonregular product closure member')
        if kind == 'regular':
            raw = path.read_bytes()
            payloads[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            if path.name == 'Info.plist':
                info = plistlib.loads(raw)
                require(info.get('CFBundleIdentifier') != 'com.kdt.livecontainer', 'Target app collision in runner products')
    exact(actual, expected, 'complete Build/Products closure')
    return {'layout': actual, 'payloads': payloads}


def strict_plist(raw):
    require(type(raw) is bytes and len(raw) <= 1024 * 1024 and b'<!ENTITY' not in raw.upper(), 'Unsafe generated plist')
    try:
        tree = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError('Malformed generated XML plist') from error
    require(tree.tag == 'plist' and tree.attrib == {'version': '1.0'} and len(tree) == 1, 'Unknown plist envelope')
    for node in tree.iter('dict'):
        children, keys = list(node), set()
        require(len(children) % 2 == 0, 'Malformed plist dictionary')
        for i in range(0, len(children), 2):
            key = children[i]
            require(key.tag == 'key' and key.text and key.text not in keys and not list(key), 'Duplicate/invalid plist key')
            keys.add(key.text)
    return plistlib.loads(raw)


def canonical(value):
    require(type(value) is str and value.startswith('/') and not value.startswith('//'), 'Absolute root required')
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value) and '\\' not in value and ':' not in value, 'Ambiguous root bytes')
    require(all(p not in ('', '.', '..') for p in value[1:].split('/')), 'Noncanonical path root')
    return PurePosixPath(value)


def roots(testroot, developer, runtime):
    result = {'__TESTROOT__': str(testroot), '__TESTHOST__': str(testroot / 'Debug-iphonesimulator/HostSettingsProbeUITests-Runner.app'),
              '__PLATFORMS__': str(developer / 'Platforms'), '__SIMRUNTIMEROOT__': str(runtime)}
    for value in result.values():
        canonical(value)
    return result


def expand(value, bindings):
    require(type(value) is str and value, 'Empty path list')
    parts = value.split(':')
    require(all(parts) and len(parts) == len(set(parts)), 'Empty/duplicate path component')
    output = []
    for part in parts:
        if part == '/usr/lib/libRPAC.dylib':
            output.append(part)
            continue
        token, slash, tail = part.partition('/')
        require(slash and token in bindings and tail and '__' not in tail, 'Unknown/repeated path token')
        require(all(p not in ('', '.', '..') for p in tail.split('/')), 'Path traversal/noncanonical component')
        value = str(canonical(bindings[token] + '/' + tail))
        require(PurePosixPath(value).is_relative_to(PurePosixPath(bindings[token])), 'Path escapes root')
        output.append(value)
    return ':'.join(output)


def requested_environment(bindings):
    target = reference()[TARGET]['UITargetAppEnvironmentVariables']
    return {key: expand(value, bindings) if value.startswith('__') else value for key, value in target.items()}


def unlinked(path, boundary):
    path, boundary = Path(path).absolute(), Path(boundary).absolute()
    require(path.is_relative_to(boundary), 'Path outside runner root')
    for current in (path, *path.parents):
        require(not current.is_symlink(), 'Linked runner path: ' + str(current))
        if current == boundary:
            break
    return path


def validate_generated(raw, testroot, app, developer, runtime):
    data = strict_plist(raw)
    exact(data, reference())
    bindings = roots(testroot, developer, runtime)
    target = data[TARGET]
    runner = unlinked(expand(target['TestHostPath'], bindings), testroot)
    bundle = unlinked(expand(target['TestBundlePath'], bindings), testroot)
    require(runner.is_dir() and bundle.is_dir(), 'Missing generated runner/test bundle')
    require(bundle == runner / 'PlugIns/HostSettingsProbeUITests.xctest', 'Test bundle outside runner PlugIns')
    require(not Path(app).resolve().is_relative_to(runner.resolve()) and
            not runner.resolve().is_relative_to(Path(app).resolve()), 'Runner substitutes for genuine product')
    require([expand(p, bindings) for p in target['DependentProductPaths']] == [str(runner), str(bundle)], 'Wrong dependency closure')
    info = plistlib.loads(unlinked(runner / 'Info.plist', testroot).read_bytes())
    require(info.get('CFBundleIdentifier') == RUNNER_ID, 'Actual runner bundle identity differs')
    expanded = {}
    for role in ('EnvironmentVariables', 'TestingEnvironmentVariables', 'UITargetAppEnvironmentVariables'):
        expanded[role] = {key: expand(value, bindings) if value.startswith('__') or value.startswith('/') else value
                          for key, value in target[role].items()}
    return {'runnerPath': str(runner), 'testBundlePath': str(bundle), 'formatVersion': 1,
            'targetApplicationMode': 'external-XCUIApplication-bundleIdentifier', 'unmodifiedGeneratedFile': True,
            'rootBindings': bindings, 'expandedEnvironmentMaps': expanded,
            'expectedInitialEnvironment': requested_environment(bindings),
            'performanceCheckerEnabled': True, 'effectiveEnvironmentEmptyProven': False}


def inspect_products(wiring, parser):
    runner, bundle = Path(wiring['runnerPath']), Path(wiring['testBundlePath'])
    closure_before = product_closure(wiring['rootBindings']['__TESTROOT__'])
    before = parser.bundle_inventory(runner)
    require(all(row['type'] == 'regular' for row in before.values()), 'Unreviewed runner product symlink')
    observations = []
    for label, path, identifier, executable, package, filetype in (
        ('runner', runner, RUNNER_ID, 'HostSettingsProbeUITests-Runner', 'APPL', 2),
        ('test-bundle', bundle, TEST_ID, 'HostSettingsProbeUITests', 'BNDL', 8)):
        prefix = '' if path == runner else 'PlugIns/HostSettingsProbeUITests.xctest/'
        info_raw = unlinked(path / 'Info.plist', runner).read_bytes()
        info = plistlib.loads(info_raw)
        require(info.get('CFBundleIdentifier') == identifier and info.get('CFBundleExecutable') == executable and
                info.get('CFBundlePackageType') == package and info.get('DTPlatformName') == 'iphonesimulator', 'Generated product identity/platform differs')
        require(hashlib.sha256(info_raw).hexdigest() == before[prefix + 'Info.plist']['sha256'], 'Product plist changed')
        row = before[prefix + executable]
        require(row['type'] == 'regular' and row['mode'] == 0o755 and row['executable'] is True, 'Wrong runner executable mode/type')
        raw = unlinked(path / executable, runner).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == row['sha256'], 'Runner executable changed')
        macho = parser.parse_macho(raw)
        require(macho['container'] == 'fat32' and len(macho['slices']) == 2, 'Wrong reviewed universal container/count')
        require([(s['cpuType'], s['cpuSubtype']) for s in macho['slices']] == [(0x01000007, 3), (0x0100000c, 0)], 'Wrong exact reviewed architecture set')
        require(all(s['bits'] == 64 and s['fileType'] == filetype and s['buildVersion']['platform'] == 7 for s in macho['slices']), 'Wrong simulator slice type/platform')
        observations.append({'label': label, 'infoPlistSHA256': hashlib.sha256(info_raw).hexdigest(),
                             'info': info, 'executableSHA256': row['sha256'], 'machO': macho})
    after = parser.bundle_inventory(runner)
    require(before == after, 'Runner products mutated during inspection')
    closure_after = product_closure(wiring['rootBindings']['__TESTROOT__'])
    require(closure_before == closure_after, 'Complete product closure changed during inspection')
    return {'inventoryBefore': before, 'inventoryAfter': after, 'productClosure': closure_after,
            'products': observations, 'executed': False}


def verify_launch_policy(report, expected):
    policy = report.get('launchPolicy')
    exact(policy, {'status': 'cleared-before-single-launch', 'initialEnvironment': expected,
                  'initialArguments': [], 'expectedInitialEnvironment': expected,
                  'initialEnvironmentMatched': True, 'initialEnvironmentKeyCount': 7, 'initialArgumentCount': 0,
                  'missingExpectedKeys': [], 'mismatchedExpectedKeys': [], 'unknownKeyCount': 0,
                  'requestedEnvironmentEmpty': True, 'requestedArgumentsEmpty': True,
                  'performanceCheckerEnabled': True, 'effectiveEnvironmentEmptyProven': False,
                  'requestedEnvironmentAfterClearing': {}, 'requestedArgumentsAfterClearing': []}, 'actual launch receipt')
    require(type(report.get('launchCount')) is int and report['launchCount'] == 1, 'Wrong actual launch count')
    return {'status': 'verified', **policy, 'claim': 'Only requested target launch inputs cleared; instrumentation retained.'}


def policy_events(output, expected):
    """Retain only bounded source-emitted receipts, never unknown launch values."""
    marker = 'P2_REQUESTED_LAUNCH_POLICY_V1:'
    rows = []
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'Duplicate event key')
            result[key] = value
        return result
    for line in output.splitlines():
        if marker not in line:
            continue
        raw = line.split(marker, 1)[1]
        require(len(raw.encode()) <= 32768 and len(rows) < 3, 'Oversized/repeated policy event')
        row = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False, 'Nonfinite event'))
        require(type(row) is dict and set(row) == {'schemaVersion', 'stage', 'launchCount', 'policy'}, 'Wrong event envelope')
        require(type(row['schemaVersion']) is int and row['schemaVersion'] == 1 and type(row['launchCount']) is int and row['launchCount'] in (0, 1), 'Wrong event scalar')
        require(row['stage'] in ('requested-target-launch-policy', 'cleared-requested-target-launch-inputs', 'genuine-host-launch'), 'Unknown policy stage')
        policy = row['policy']
        mandatory = {'status', 'expectedInitialEnvironment', 'initialEnvironmentMatched', 'initialEnvironmentKeyCount', 'initialArgumentCount',
                     'missingExpectedKeys', 'mismatchedExpectedKeys', 'unknownKeyCount', 'performanceCheckerEnabled', 'effectiveEnvironmentEmptyProven'}
        optional = {'initialEnvironment', 'initialArguments', 'requestedEnvironmentEmpty', 'requestedArgumentsEmpty',
                    'requestedEnvironmentAfterClearing', 'requestedArgumentsAfterClearing'}
        require(type(policy) is dict and mandatory <= set(policy) <= mandatory | optional, 'Unreviewed policy keys')
        require(policy['status'] in ('initial-observed', 'cleared-before-single-launch'), 'Unknown policy status')
        exact(policy['expectedInitialEnvironment'], expected)
        for key in ('initialEnvironmentMatched', 'performanceCheckerEnabled', 'effectiveEnvironmentEmptyProven', 'requestedEnvironmentEmpty', 'requestedArgumentsEmpty'):
            if key in policy:
                require(type(policy[key]) is bool, 'Wrong policy Boolean')
        require(policy['performanceCheckerEnabled'] and not policy['effectiveEnvironmentEmptyProven'], 'Policy scope differs')
        for key in ('initialEnvironmentKeyCount', 'initialArgumentCount', 'unknownKeyCount'):
            require(type(policy[key]) is int and 0 <= policy[key] <= 1024, 'Wrong policy count')
        for key in ('missingExpectedKeys', 'mismatchedExpectedKeys'):
            require(type(policy[key]) is list and all(type(v) is str and v in expected for v in policy[key]) and
                    policy[key] == sorted(set(policy[key])), 'Unknown/redacted key metadata')
        for key, value in [('initialEnvironment', expected), ('initialArguments', []), ('requestedEnvironmentAfterClearing', {}), ('requestedArgumentsAfterClearing', [])]:
            if key in policy:
                exact(policy[key], value, 'allowlisted event field ' + key)
        rows.append(row)
    return {'schemaVersion': 1, 'events': rows, 'forwardedByXcode': bool(rows), 'effectiveEnvironmentEmptyProven': False}


def verify_policy_events(receipt, expected):
    require(type(receipt) is dict and receipt.get('forwardedByXcode') is True and receipt.get('effectiveEnvironmentEmptyProven') is False,
            'Unbuffered policy forwarding not observed')
    rows = receipt.get('events')
    require(type(rows) is list and len(rows) == 3, 'Missing bounded policy sequence')
    final = rows[2]['policy']
    verify_launch_policy({'launchPolicy': final, 'launchCount': rows[2]['launchCount']}, expected)
    initial = {key: value for key, value in final.items() if not key.startswith('requested')}
    initial['status'] = 'initial-observed'
    cleared = {**final, 'status': 'initial-observed'}
    for row, stage, count, policy in zip(rows, ('requested-target-launch-policy', 'cleared-requested-target-launch-inputs', 'genuine-host-launch'), (0, 0, 1), (initial, cleared, final)):
        exact(row, {'schemaVersion': 1, 'stage': stage, 'launchCount': count, 'policy': policy}, 'ordered policy event')
    return True
