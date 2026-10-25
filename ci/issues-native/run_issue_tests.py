"""Run each source-owned issue suite in a fresh process and reject skipped tests."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

SUITES = [
    ('LiveContainer', 'tests', 'test_guest_input_lifecycle.py'),
    ('LiveContainer', 'tests', 'test_guest_safe_area_geometry.py'),
    ('LiveContainer', 'tests', 'test_source_app_presentation.py'),
    ('LiveContainer', 'tests', 'test_diagnostic_provider_sequence.py'),
    ('LiveContainer', 'tests', 'test_release_size_configuration.py'),
    ('SideStore', 'tests/appid_expiry', 'test_appid_expiry_bridge.py'),
    ('SideStore', 'tests/reuse_main', 'test_contract.py'),
    ('SideStore', 'tests', 'test_diagnostic_provider_sequence.py'),
    ('idevice', '.ci/issue53', 'test_ownership.py'),
]
RUNNER = '''import sys,unittest
suite=unittest.defaultTestLoader.discover(sys.argv[1],pattern=sys.argv[2])
if not suite.countTestCases(): raise SystemExit('empty test suite')
r=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if r.wasSuccessful() and not r.skipped else 1)
'''
HARNESS_SUITES = ('test_ipa_size_report.py', 'test_combined_packaging.py', 'test_custom_integration_boundaries.py')


def verify_issue_receipt(path):
    value = json.loads(Path(path).read_text())
    expected = [{'owner': owner, 'suite': folder + '/' + pattern, 'status': 'PASS_NO_SKIPS'}
                for owner, folder, pattern in SUITES]
    if (value.get('suites') != expected or value.get('harness_suites') != list(HARNESS_SUITES)
            or value.get('registry_runtime_double') != 'PASS'
            or value.get('signer_policy_and_production_body_double') != 'PASS'
            or value.get('all_suites_passed_no_skips') is not True
            or value.get('target_device_execution') is not False):
        raise ValueError('issue test receipt closure or result differs')
    return value


def main():
    if not shutil.which('swiftc'):
        raise SystemExit('Swift compiler required; no skipped policy execution is accepted')
    rows = []
    for owner, folder, pattern in SUITES:
        cwd = Path('sources') / owner
        if not list((cwd / folder).glob(pattern)):
            raise SystemExit('missing required test: ' + owner + '/' + folder + '/' + pattern)
        subprocess.run([sys.executable, '-B', '-c', RUNNER, folder, pattern], cwd=cwd, check=True)
        rows.append({'owner': owner, 'suite': folder + '/' + pattern, 'status': 'PASS_NO_SKIPS'})
    subprocess.run(['bash', 'tests/reuse_main/run.sh'], cwd='sources/SideStore', check=True)
    subprocess.run(['bash', '.ci/reuse-main/run.sh'], cwd='sources/SideStore/Dependencies/SideSign', check=True)
    for pattern in HARNESS_SUITES:
        subprocess.run([sys.executable, '-B', '-c', RUNNER, 'tests', pattern], cwd='harness', check=True)
    Path('evidence/issue-tests.json').write_text(json.dumps({'suites': rows,
        'registry_runtime_double': 'PASS', 'signer_policy_and_production_body_double': 'PASS', 'target_device_execution': False,
        'harness_suites': list(HARNESS_SUITES), 'all_suites_passed_no_skips': True}, indent=2))
    verify_issue_receipt('evidence/issue-tests.json')


if __name__ == '__main__':
    main()
