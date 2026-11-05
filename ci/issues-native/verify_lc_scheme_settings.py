"""Check the actual Release scheme invocation, separate from all-target scope."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from release_coverage import settings


def verify(rows):
    validated = settings(rows)
    if len(validated) != 1 or validated[0]['target'] != 'LiveContainer':
        raise ValueError('Expected only the LiveContainer scheme primary target')
    if validated[0]['settings']['CLANG_COVERAGE_MAPPING'] != 'NO':
        raise ValueError('Actual LiveContainer Release scheme mapping must explicitly be NO')
    return validated[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = args.input.read_bytes()
    row = verify(json.loads(data))
    report = {'schema':'livecontainer-release-scheme-settings-v1',
              'workflow_commit':os.environ['GITHUB_SHA'], 'run_id':os.environ['GITHUB_RUN_ID'],
              'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'], 'input_sha256':hashlib.sha256(data).hexdigest(),
              'target':row, 'settings_only':True, 'native_compiler_and_linker_validation_required':True}
    args.output.write_text(json.dumps(report,indent=2)+'\n')

if __name__ == '__main__': main()
