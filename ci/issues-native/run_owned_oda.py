"""Qualify the exact candidate AK owner; all downloaded binaries stay outside evidence."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile
from verify_candidate_graph import load_candidate_graph, git

HERE = Path(__file__).resolve().parent
UNICORN_URL = 'https://github.com/mahee96/unicorn/releases/download/2.1.4-xcf-a53ddc9/Unicorn.xcframework.zip'
UNICORN_SHA = '52e4ac9e2d704c4941adc2c381df8706aabf673dc843611a90b33ad349d562db'
APK_URL = 'https://apps.mzstatic.com/content/android-apple-music-apk/applemusic.apk'
LIBRARIES = {
    'libCoreADI.so': {'bytes': 1792592, 'sha256': '9d5b557cf3faf88c34c394ab2cd7fc1a3b4acf3aed51f28cd0bc093858107c22'},
    'libstoreservicescore.so': {'bytes': 2388480, 'sha256': 'bd335e6963ba5ae2f5babca613435ad56d2660a9aee37df35f876c458d105ce4'},
}
SWIFT_TESTS = (
    'testCancelAfterInitializationBeforeFetchDrainsAndCleans',
    'testAlreadyCancelledTaskStillDrainsInitializedAttempt',
    'testInvalidSnapshotCannotCreateAttemptRoot',
    'testAbandonedInitializedAttemptCleansOwnedRootOnly',
    'testFreshProvisioningLookupFailureDrainsWithoutNativeEntry',
    'testFreshProvisioningCancellationWaitsForSessionAndNativeDrain',
    'testExplicitCancelAtCompletedNativeReturnBoundarySuppressesHeaders',
    'testMemoryOnlyClientNeverReadsOrDeletesExistingDiskState',
)
SUITES = ('native', 'historical_engine', 'legacy', 'logging', 'swift', 'startup')
STARTUP_FLAGS = ('engine_returned', 'libraries_initialized', 'provisioning_path_initialized',
                 'android_id_initialized', 'invalid_blob_rejected', 'drained', 'owned_root_removed')


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def validate_log(name, output):
    if name == 'native':
        for marker in ('PASS: control, filesystem, pinned-header syntax, actual C ABI with mock engine',
                       'PASS: pinned real Unicorn synthetic ARM64 procedures'):
            require(output.count(marker) == 1, 'Missing/duplicated native execution marker')
        require('NOT RUN:' not in output, 'Native fixture skipped')
    elif name == 'historical_engine':
        value = json.loads(output)
        require(value.get('schema') == 'owned-oda-historical-engine-v1' and value.get('unicorn_archive_sha256') == UNICORN_SHA,
                'Historical engine receipt differs')
        require(value.get('checks') == {'android_tls': 83, 'constructor_diagnostics': 2105}, 'Historical engine fixture counts differ')
        require(value.get('ADI_executed') is False and value.get('device_validation') is False and value.get('all_passed') is True,
                'Historical engine result differs')
    elif name in ('legacy', 'logging'):
        count = 9 if name == 'legacy' else 13
        require(len(re.findall(r'^Ran ' + str(count) + r' tests in ', output, re.M)) == 1,
                'Historical suite count differs: ' + name)
        require(re.search(r'^OK$', output, re.M) is not None and 'skipped' not in output.lower(),
                'Historical suite failed or skipped: ' + name)
    elif name == 'swift':
        passed = re.findall(r'^Test Case .*OwnedOnDeviceAnisetteAttemptTests.* passed \(', output, re.M)
        require(len(passed) == len(SWIFT_TESTS), 'Owned XCTest count differs')
        for test in SWIFT_TESTS:
            require(len(re.findall(r'^Test Case .*' + re.escape(test) + r'(?![A-Za-z0-9_]).* passed \(', output, re.M)) == 1,
                    'Missing/duplicated owned XCTest: ' + test)


def validate_startup(value):
    require(value.get('passed') is True and value.get('unicorn_sha256') == UNICORN_SHA, 'Startup fixture failed/pin differs')
    require(value.get('verified_inputs', {}).get('recognized_libraries') == LIBRARIES, 'Trusted library hashes/sizes differ')
    observed = value.get('verified_inputs', {}).get('archive_sha256_observed', '')
    require(re.fullmatch('[a-f0-9]{64}', observed) is not None, 'Missing observed APK digest')
    rows = value.get('commands', [])
    require(len(rows) == 3 and all(row.get('returncode') == 0 for row in rows), 'Startup command failed or absent')
    flags = json.loads(rows[-1]['stdout'])
    require(flags == {name: 1 for name in STARTUP_FLAGS}, 'Startup compatibility/drain result differs')
    require(all(not row.get('stderr') for row in rows), 'Unexpected startup diagnostics require review')


def source_identity(graph):
    owner = graph['owners']['AnisetteKit']
    require(owner[0] == 'NRG-Wardog/AnisetteKit', 'Unexpected AK owner')
    return {'repository': owner[0], 'commit': owner[1], 'tree': graph['reviewed_deltas']['AnisetteKit']['tree']}


def verify_receipt(path, graph_path=HERE / 'candidate-graph.json'):
    path = Path(path)
    value = json.loads(path.read_text())
    expected = source_identity(load_candidate_graph(graph_path))
    require(value.get('schema') == 'owned-oda-candidate-v1' and value.get('source') == expected, 'Owned ODA source receipt differs')
    require(value.get('all_passed') is True and value.get('device_or_authentication_proven') is False, 'Owned ODA qualification incomplete')
    require(value.get('unicorn_sha256') == UNICORN_SHA, 'Owned ODA dependency pin differs')
    require(set(value.get('suites', {})) == set(SUITES), 'Owned ODA suite closure differs')
    for name in SUITES:
        row = value['suites'][name]
        require(row.get('status') == 'PASS' and row.get('returncode') == 0, 'Owned ODA suite did not pass: ' + name)
        log = path.parent / (name + '.log')
        require(log.is_file() and not log.is_symlink() and digest(log) == row.get('log_sha256'), 'Owned ODA log drift: ' + name)
        validate_log(name, log.read_text())
        if name == 'historical_engine':
            historical = json.loads(log.read_text())
            require(historical.get('source') == expected, 'Historical engine source receipt differs')
            require(set(historical.get('fixture_sha256', {})) == {'android_tls', 'constructor_diagnostics'} and
                    all(re.fullmatch('[a-f0-9]{64}', value) is not None for value in historical['fixture_sha256'].values()),
                    'Historical fixture source digests absent')
            require(re.fullmatch('[a-f0-9]{64}', historical.get('engine_library_sha256_observed', '')) is not None,
                    'Historical engine library digest absent')
    startup = path.parent / 'trusted-startup.json'
    require(startup.is_file() and not startup.is_symlink() and digest(startup) == value.get('startup_receipt_sha256'), 'Trusted startup receipt drift')
    validate_startup(json.loads(startup.read_text()))
    return value


def execute(name, command, cwd, evidence, timeout, rows, runner=subprocess.run):
    log = evidence / (name + '.log')
    try:
        result = runner(list(map(str, command)), cwd=cwd, text=True, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=timeout)
        output, code = result.stdout, result.returncode
    except OSError as error:
        output, code = 'SUBPROCESS_LAUNCH_FAILED: ' + type(error).__name__ + '\n', 127
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ''
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='replace')
        output += '\nBOUNDED_SUBPROCESS_TIMEOUT\n'
        code = 124
    log.write_text(output)
    print(output, end='', flush=True)
    row = {'status': 'FAIL', 'returncode': code, 'log_sha256': digest(log)}
    rows[name] = row
    if code == 0:
        try:
            validate_log(name, output)
            row['status'] = 'PASS'
        except ValueError as error:
            row['validation_error'] = str(error)
    return row['status'] == 'PASS'


def download(url, target, maximum, expected_sha=None, expected_size=None):
    require(not target.exists() and not target.is_symlink(), 'Download leaf must be fresh')
    subprocess.run(['curl', '--fail', '--location', '--proto', '=https', '--connect-timeout', '20',
                    '--max-time', '240', '--max-filesize', str(maximum), '--output', str(target), url],
                   check=True, timeout=250, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(target.is_file() and not target.is_symlink() and target.stat().st_size <= maximum, 'Invalid downloaded artifact')
    if expected_size is not None:
        require(target.stat().st_size == expected_size, 'Recognized archive size differs')
    if expected_sha is not None:
        require(digest(target) == expected_sha, 'Pinned artifact digest differs')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources', type=Path, default=Path('sources'))
    p.add_argument('--evidence', type=Path, default=Path('evidence/owned-oda'))
    p.add_argument('--inputs', type=Path, default=Path('qualification-inputs'))
    p.add_argument('--graph', type=Path, default=HERE / 'candidate-graph.json')
    args = p.parse_args()
    source = (args.sources / 'AnisetteKit').resolve()
    evidence, inputs = args.evidence.resolve(), args.inputs.resolve()
    artifact_root = Path('evidence').resolve()
    require(not inputs.is_relative_to(evidence) and not evidence.is_relative_to(inputs) and not inputs.is_relative_to(artifact_root), 'Inputs must stay outside evidence')
    require(not args.inputs.is_symlink() and not args.evidence.is_symlink(), 'No symlink qualification roots')
    evidence.mkdir(parents=True, exist_ok=True)
    # Preflight failure must never leave an earlier successful attempt receipt.
    for name in ('receipt.json', 'trusted-startup.json', *(suite + '.log' for suite in SUITES)):
        (evidence / name).unlink(missing_ok=True)
    expected = source_identity(load_candidate_graph(args.graph))
    def verify_source():
        require(git(source, 'rev-parse', 'HEAD') == expected['commit'] and git(source, 'rev-parse', 'HEAD^{tree}') == expected['tree'], 'Candidate AK commit/tree differs')
        require(not git(source, 'diff', '--name-only', 'HEAD'), 'Candidate AK tracked inputs changed')
    verify_source()
    test_source = (source / 'Tests/AnisetteKitTests/OwnedOnDeviceAnisetteAttemptTests.swift').read_text()
    require(set(re.findall(r'^    func (test\w+)\(', test_source, re.M)) == set(SWIFT_TESTS), 'Owned Swift source inventory differs')
    inputs.mkdir(parents=True, exist_ok=True, mode=0o700)
    rows = {}
    receipt = {'schema': 'owned-oda-candidate-v1', 'source': expected, 'suites': rows,
               'all_passed': False, 'device_or_authentication_proven': False}
    try:
        with tempfile.TemporaryDirectory(prefix='owned-oda-', dir=inputs) as temp:
            working = Path(temp)
            unicorn, apk = working / 'Unicorn.xcframework.zip', working / 'applemusic.apk'
            dependency_ok = False
            try:
                download(UNICORN_URL, unicorn, 512 * 1024 * 1024, expected_sha=UNICORN_SHA)
                receipt['unicorn_sha256'] = digest(unicorn)
                dependency_ok = True
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                receipt['dependency_error_type'] = type(error).__name__
            native_ok = False
            if dependency_ok:
                native_ok = execute('native', [sys.executable, '-B', source / '.ci/owned-attempt/run.py', '--unicorn-archive', unicorn], source, evidence, 360, rows)
                historical_ok = execute('historical_engine', [sys.executable, '-B', Path(__file__).resolve(), 'historical-engines',
                    '--source', source, '--unicorn-archive', unicorn, '--graph', args.graph.resolve()], source, evidence, 420, rows)
                native_ok = native_ok and historical_ok
            else:
                rows['native'] = {'status': 'NOT_RUN_DEPENDENCY_FAILURE'}
                rows['historical_engine'] = {'status': 'NOT_RUN_DEPENDENCY_FAILURE'}
            execute('legacy', [sys.executable, '-B', source / '.ci/native-tests/run_tests.py'], source, evidence, 240, rows)
            execute('logging', [sys.executable, '-B', source / '.ci/native-logging/test_native_logging_contract.py'], source, evidence, 120, rows)
            if dependency_ok:
                execute('swift', ['swift', 'test', '--filter', 'OwnedOnDeviceAnisetteAttemptTests'], source, evidence, 720, rows)
            else:
                rows['swift'] = {'status': 'NOT_RUN_DEPENDENCY_FAILURE'}
            if native_ok:
                try:
                    download(APK_URL, apk, 142139820, expected_size=142139820)
                    okay = execute('startup', [sys.executable, '-B', source / '.ci/owned-attempt/run_verified_startup.py', '--archive', apk,
                        '--unicorn-archive', unicorn, '--receipt', evidence / 'trusted-startup.json'], source, evidence, 240, rows)
                    if okay:
                        validate_startup(json.loads((evidence / 'trusted-startup.json').read_text()))
                        receipt['startup_receipt_sha256'] = digest(evidence / 'trusted-startup.json')
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    rows.setdefault('startup', {})['status'] = 'FAIL'
                    rows['startup']['error_type'] = type(error).__name__
            else:
                rows['startup'] = {'status': 'NOT_RUN_NATIVE_FAILURE'}
        verify_source()
        receipt['all_passed'] = set(rows) == set(SUITES) and all(row.get('status') == 'PASS' for row in rows.values())
    finally:
        (evidence / 'receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    verify_receipt(evidence / 'receipt.json', args.graph)


def historical_main(argv):
    """Existing source-owned fixtures, exact static engine, no source patching."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--unicorn-archive', type=Path, required=True)
    parser.add_argument('--graph', type=Path, required=True)
    args = parser.parse_args(argv)
    source = args.source.resolve()
    expected = source_identity(load_candidate_graph(args.graph))
    require(git(source, 'rev-parse', 'HEAD') == expected['commit'] and git(source, 'rev-parse', 'HEAD^{tree}') == expected['tree'], 'Historical engine source differs')
    require(not git(source, 'diff', '--name-only', 'HEAD'), 'Historical engine source dirty')
    require(digest(args.unicorn_archive) == UNICORN_SHA, 'Historical engine archive digest differs')
    fixtures = {'android_tls': '.ci/native-tests/fixtures/android_tls_constructor.cpp',
                'constructor_diagnostics': '.ci/native-tests/fixtures/constructor_diagnostics.cpp'}
    outputs = {}
    with tempfile.TemporaryDirectory(prefix='owned-oda-historical-engine-') as directory:
        work = Path(directory); headers = work / 'headers'; headers.mkdir()
        prefix = 'Unicorn.xcframework/macos-arm64_x86_64/'
        with zipfile.ZipFile(args.unicorn_archive) as archive:
            for name in archive.namelist():
                if name.startswith(prefix + 'Headers/') and name.endswith('.h'):
                    relative = Path(name[len(prefix + 'Headers/'):])
                    require(not relative.is_absolute() and '..' not in relative.parts, 'Invalid pinned header path')
                    target = headers / relative; target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
            library = work / 'libunicorn.a'; library.write_bytes(archive.read(prefix + 'libunicorn.a'))
        for name, relative in fixtures.items():
            # These unchanged historical fixtures explicitly define their own
            # TLS suppression/log sink. Do not also link core/base and duplicate
            # those symbols. The full real loader and pinned engine are linked.
            command = ['c++', '-std=c++17', '-UNDEBUG', '-pthread', '-I', str(source / 'Native'),
                       '-I', str(source / 'Native/include'), '-I', str(source / 'Native/Loader'), '-I', str(headers),
                       str(source / relative), str(source / 'Native/Loader/elf_loader_emulator.cpp'), str(library), '-o', str(work / name)]
            compiled = subprocess.run(command, capture_output=True, text=True, timeout=120)
            require(compiled.returncode == 0 and not compiled.stderr, 'Historical fixture compile failed: ' + name + '\n' + compiled.stderr)
            result = subprocess.run([str(work / name)], capture_output=True, text=True, timeout=60)
            require(result.returncode == 0 and not result.stderr, 'Historical fixture run failed: ' + name + '\n' + result.stderr)
            outputs[name] = result.stdout
        require(outputs['android_tls'] == 'PASS 83 full-production-loader synthetic checks; no ADI executed\n', 'Android TLS historical fixture result differs')
        parser_path = source / '.ci/native-tests/test_constructor_diagnostics.py'
        spec = importlib.util.spec_from_file_location('owned_oda_constructor_contract', parser_path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        module.parse_fixture_result(outputs['constructor_diagnostics'], '', 0)
        library_digest = digest(library)
    require(not git(source, 'diff', '--name-only', 'HEAD'), 'Historical fixtures mutated source')
    receipt = {'schema': 'owned-oda-historical-engine-v1', 'source': expected, 'unicorn_archive_sha256': UNICORN_SHA,
               'engine_library_sha256_observed': library_digest, 'checks': {'android_tls': 83, 'constructor_diagnostics': 2105},
               'ADI_executed': False, 'device_validation': False, 'all_passed': True,
               'fixture_sha256': {name: digest(source / relative) for name, relative in fixtures.items()}}
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'historical-engines':
        historical_main(sys.argv[2:])
    else:
        main()
