"""Retain whitelisted settings failure diagnostics; never admit a build."""
import argparse
import hashlib
import json
from pathlib import Path

KEYS = ('CONFIGURATION', 'ENABLE_CODE_COVERAGE', 'CLANG_ENABLE_CODE_COVERAGE',
        'CLANG_COVERAGE_MAPPING', 'CLANG_COVERAGE_MAPPING_LINKER_ARGS',
        'CLANG_CLANG_COVERAGE_MAPPING_LINKER_ARGS', 'SWIFTC_CLANG_COVERAGE_MAPPING_LINKER_ARGS', 'GCC_GENERATE_TEST_COVERAGE_FILES',
        'GCC_INSTRUMENT_PROGRAM_FLOW_ARCS', 'SWIFT_OPTIMIZATION_LEVEL',
        'DEBUG_INFORMATION_FORMAT', 'ENABLE_TESTABILITY')


def capture(source, exit_code):
    report = {'schema': 'release-settings-diagnostic-v1', 'xcodebuild_exit_code': exit_code,
              'policy_admission': False, 'source_exists': source.is_file()}
    if not source.is_file():
        return report
    data = source.read_bytes()
    report.update(input_sha256=hashlib.sha256(data).hexdigest(), input_bytes=len(data))
    try:
        rows = json.loads(data)
    except (ValueError, UnicodeError):
        report['parse_status'] = 'INVALID_JSON'
        return report
    if not isinstance(rows, list):
        report['parse_status'] = 'NOT_ARRAY'
        return report
    report['parse_status'] = 'PARSED'
    report['rows'] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('buildSettings'), dict):
            report['rows'].append({'shape': 'INVALID_ROW'})
            continue
        report['rows'].append({'target': row.get('target'),
                              'full_row_sha256': hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8')).hexdigest(), 'settings':
                              {key: row['buildSettings'].get(key) for key in KEYS}})
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--exit-code', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.output.open('x') as output:
        json.dump(capture(args.input, args.exit_code), output, indent=2)
        output.write('\n')

if __name__ == '__main__':
    main()
