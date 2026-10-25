"""Release-only instrumentation evidence, never accepts synthetic test logs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex

KEYS = ('ENABLE_CODE_COVERAGE', 'CLANG_ENABLE_CODE_COVERAGE', 'CLANG_COVERAGE_MAPPING',
        'GCC_GENERATE_TEST_COVERAGE_FILES', 'GCC_INSTRUMENT_PROGRAM_FLOW_ARCS',
        'SWIFT_OPTIMIZATION_LEVEL', 'DEBUG_INFORMATION_FORMAT', 'ENABLE_TESTABILITY')
FLAGS = re.compile(r'(?<![\w-])(?:-fprofile-instr-generate|-fcoverage-mapping|-profile-generate|-profile-coverage-mapping|-fprofile-arcs|-ftest-coverage)(?=[\s=\x27\x22]|$)')

def settings(rows, require_support_size=False):
    if not isinstance(rows, list) or not rows:
        raise ValueError('Missing effective Release settings')
    result = []
    for row in rows:
        values = row['buildSettings']
        if values.get('CONFIGURATION') != 'Release':
            raise ValueError('Not Release settings')
        if values.get('ENABLE_CODE_COVERAGE') != 'NO':
            raise ValueError('Release coverage must explicitly be disabled: ' + row['target'])
        # CLANG_COVERAGE_MAPPING is recorded, but mapping alone does not enable
        # instrumentation; actual emitted flags are checked after compilation.
        for key in ('CLANG_ENABLE_CODE_COVERAGE', 'GCC_GENERATE_TEST_COVERAGE_FILES',
                    'GCC_INSTRUMENT_PROGRAM_FLOW_ARCS'):
            if values.get(key) == 'YES':
                raise ValueError('Secondary coverage instrumentation enabled: ' + key)
        result.append({'target': row['target'], 'configuration': 'Release',
                       'settings': {key: values.get(key) for key in KEYS}})
    if len({row['target'] for row in result}) != len(result):
        raise ValueError('Duplicate effective target')
    if require_support_size:
        support = [row for row in result if row['target'] == 'SideStoreSupport']
        if len(support) != 1 or support[0]['settings']['SWIFT_OPTIMIZATION_LEVEL'] != '-Osize':
            raise ValueError('SideStoreSupport Release must use -Osize')
    return result


def support_commands(text):
    commands = []
    for line in text.splitlines():
        # Only compiler command lines, not echo, diagnostic prose, or test logs.
        stripped = line.strip()
        if not (stripped.startswith(('/', '"/', "'/")) or stripped.startswith('builtin-SwiftDriver -- ')):
            continue
        tokens = shlex.split(stripped)
        if tokens[:2] == ['builtin-SwiftDriver', '--']:
            tokens = tokens[2:]
        if not tokens or not tokens[0].startswith('/') or Path(tokens[0]).name != 'swiftc':
            continue
        modules = [tokens[i+1] for i, token in enumerate(tokens[:-1]) if token == '-module-name']
        if 'SideStoreSupport' not in modules:
            continue
        if modules != ['SideStoreSupport']:
            raise ValueError('Ambiguous Support compiler module')
        modes = []
        for index, token in enumerate(tokens[1:], 1):
            if token in ('-O', '-Onone', '-Osize', '-Ounchecked'):
                if tokens[index - 1] in ('-Xcc', '-Xfrontend', '-Xlinker', '-D'):
                    raise ValueError('Forwarded optimization requires explicit review')
                modes.append(token)
        if not modes or modes[-1] != '-Osize':
            raise ValueError('Support compiler effective optimization is not -Osize')
        commands.append(tokens)
    if not commands:
        raise ValueError('Missing actual SideStoreSupport Swift compiler command')
    return {'target': 'SideStoreSupport', 'effective_optimization': '-Osize',
            'compiler_command_count': len(commands),
            'compiler_commands_sha256': hashlib.sha256(json.dumps(commands, separators=(',', ':')).encode()).hexdigest()}


def build_log(data, require_support_size=False):
    text = data.decode('utf-8', errors='strict')
    support = support_commands(text) if require_support_size else None
    if '** BUILD SUCCEEDED **' not in text or (support is None and not re.search(r'/(?:swiftc|clang(?:\+\+)?)\s', text)):
        raise ValueError('Missing successful actual native compiler log')
    if FLAGS.search(text):
        raise ValueError('Coverage instrumentation remains in native build commands')
    result = {'sha256': hashlib.sha256(data).hexdigest(), 'coverage_flags_present': False,
              'native_build_succeeded': True}
    if require_support_size:
        result['support_size_policy'] = support
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('settings', 'logs'))
    parser.add_argument('--inputs', nargs=2, type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-support-size', action='store_true')
    args = parser.parse_args()
    result = {'schema': 'release-instrumentation-policy-v1', 'configuration': 'Release',
              'workflow_commit': os.environ['GITHUB_SHA'], 'run_id': os.environ['GITHUB_RUN_ID'],
              'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'], 'debug_test_configuration_changed': False}
    result['projects'] = {name: (settings(json.loads(path.read_text()), args.require_support_size and name == 'LiveContainer') if args.mode == 'settings'
                                else build_log(path.read_bytes(), args.require_support_size and name == 'LiveContainer'))
                          for name, path in zip(('LiveContainer', 'SideStore'), args.inputs)}
    if args.require_support_size:
        result['support_size_policy'] = {'required': True, 'target': 'SideStoreSupport',
            'effective_optimization': '-Osize', 'mode': args.mode,
            'input_sha256': hashlib.sha256(args.inputs[0].read_bytes()).hexdigest()}
        if args.mode == 'settings':
            result['support_size_policy']['target_count'] = 1
            result['support_size_policy']['all_target_count'] = len(result['projects']['LiveContainer'])
        else:
            result['support_size_policy'].update(result['projects']['LiveContainer']['support_size_policy'])
    args.output.write_text(json.dumps(result, indent=2) + '\n')

if __name__ == '__main__':
    main()
