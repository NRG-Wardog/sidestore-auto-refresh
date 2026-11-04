"""Run each source-owned issue suite in a fresh process and reject skipped tests."""
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys

SUITES = [
    ('LiveContainer', '.ci/owned-oda-note', 'test_source.py'),
    ('SideStore', '.ci/owned-oda', 'test_sources.py'),
    ('SideStore/Dependencies/SideSign', '.ci/owned-oda', 'test_sources.py'),
    ('LiveContainer', 'tests', 'test_guest_input_lifecycle.py'),
    ('LiveContainer', 'tests', 'test_guest_safe_area_geometry.py'),
    ('LiveContainer', 'tests', 'test_source_app_presentation.py'),
    ('LiveContainer', 'tests', 'test_diagnostic_provider_sequence.py'),
    ('LiveContainer', 'tests', 'test_release_size_configuration.py'),
    ('SideStore', 'tests/appid_expiry', 'test_appid_expiry_bridge.py'),
    ('SideStore', 'tests/reuse_main', 'test_contract.py'),
    ('SideStore', 'tests/cache_compatibility', 'test_host_preservation.py'),
    ('SideStore', 'tests', 'test_diagnostic_provider_sequence.py'),
    ('SideStore', '.ci/initial-provider-rejection', 'test_source.py'),
    ('idevice', '.ci/issue53', 'test_ownership.py'),
    ('idevice', '.ci/issue53', 'test_listener.py'),
    ('idevice', '.ci/issue53', 'test_mss.py'),
    ('idevice', '.ci/issue53', 'test_adapter_clone_scope.py'),
]
RUNNER = '''import sys,unittest
suite=unittest.defaultTestLoader.discover(sys.argv[1],pattern=sys.argv[2])
if not suite.countTestCases(): raise SystemExit('empty test suite')
r=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if r.wasSuccessful() and not r.skipped else 1)
'''
HARNESS_SUITES = ('test_ipa_size_report.py', 'test_combined_packaging.py', 'test_custom_integration_boundaries.py', 'test_packaging_adapters.py')


OWNED_SWIFT_SUITES = (
    ('SideStore/Dependencies/SideSign', '.ci/owned-oda/run.sh', 'PASS owned ODA context:', 10, 300),
    ('SideStore', '.ci/owned-oda/run.sh', 'PASS actual identity reservation:', 1, 120),
    ('SideStore', '.ci/initial-provider-rejection/run.sh',
     'INITIAL_PROVIDER_REJECTION_CONSUMER_PASS actual_mapper=true actual_sequence=true account_calls=false', 1, 300),
)
OWNED_INPUTS = {
    'SideStore/Dependencies/SideSign': (
        'Sources/Anisette/OwnedODAAuthenticationContext.swift',
        'Sources/Anisette/OwnedODANativeSource.swift',
        'Sources/Anisette/OwnedODALibraryPreparation.swift',
        '.ci/owned-oda/Fixture.swift', '.ci/owned-oda/run.sh', '.ci/owned-oda/test_sources.py'),
    'SideStore': ('AltStore/AppDelegate.swift',
        'SideStore/Core/Anisette/V3DiagnosticProviderSequence.swift',
        'SideStore/Core/Anisette/AnisetteProvider.swift',
        'SideStore/Core/Auth/AuthManager.swift',
        'SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift',
        '.ci/owned-oda/IdentityCases.swift', '.ci/owned-oda/generate_identity.py',
        '.ci/owned-oda/run.sh', '.ci/owned-oda/test_sources.py'),
    'LiveContainer': ('LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
        'LiveContainerSwiftUI/Models/V3DiagnosticProviderSequence.swift',
        'LiveContainerSwiftUI/Utilities/V3Localization.swift',
        'Resources/Localizable.xcstrings', '.ci/owned-oda-note/test_source.py',
        'tests/test_diagnostic_provider_sequence.py',
        'tests/provider_sequence/main.swift',
        'tests/provider_sequence/prompt_main.swift'),
}

# Seal the complete real authentication producer and retry consumer fixture inputs.
INITIAL_REJECTION_INPUTS = {'SideStore/Dependencies/SideSign': ('.ci/auth-request-diagnostics/BoundaryDoubles.swift',
                                     '.ci/auth-request-diagnostics/PrivateProbes.swift',
                                     '.ci/auth-request-diagnostics/README.md',
                                     '.ci/auth-request-diagnostics/RuntimeTests.swift',
                                     '.ci/auth-request-diagnostics/baseline/Authentication.swift',
                                     '.ci/auth-request-diagnostics/baseline/manifest.json',
                                     '.ci/auth-request-diagnostics/generate.py',
                                     '.ci/auth-request-diagnostics/instrumentation-projection.json',
                                     '.ci/auth-request-diagnostics/test_source.py',
                                     '.ci/initial-provider-rejection/RuntimeCases.swift',
                                     '.ci/initial-provider-rejection/authentication-projection.json',
                                     'Sources/Constants.swift',
                                     'Sources/DeveloperPortal/Authentication.swift',
                                     'Sources/DeveloperPortal/DeveloperPortalAPI.swift',
                                     'Sources/DeveloperPortal/ILoaderAuthenticationPolicy.swift',
                                     'Sources/Models/AnisetteData.swift',
                                     'Sources/Models/AuthenticationDiagnosticTrace.swift',
                                     'Sources/Models/AuthenticationHeaderProvenance.swift',
                                     'Sources/Models/Errors.swift',
                                     'Sources/Models/Session.swift',
                                     'Sources/SideSignHeaders.swift'),
 'SideStore': ('.ci/iloader-onboarding-scope/generate.py',
               '.ci/initial-provider-rejection/BoundaryDoubles.swift',
               '.ci/initial-provider-rejection/README.md',
               '.ci/initial-provider-rejection/RuntimeCases.swift',
               '.ci/initial-provider-rejection/generate.py',
               '.ci/initial-provider-rejection/run.sh',
               '.ci/initial-provider-rejection/test_source.py',
               'AltStore/AppDelegate.swift',
               'SideStore/Core/Anisette/V3DiagnosticProviderSequence.swift',
               'SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift')}
for _owner, _names in INITIAL_REJECTION_INPUTS.items():
    OWNED_INPUTS[_owner] = tuple(dict.fromkeys((*OWNED_INPUTS[_owner], *_names)))

INITIAL_REJECTION_MARKERS = (
    "INITIAL_PROVIDER_REJECTION_AUTH_PASS cases=27 actual_authenticate=true native_provider=false",
    "INITIAL_PROVIDER_REJECTION_CONSUMER_PASS actual_mapper=true actual_sequence=true account_calls=false",
    "AUTH_REQUEST_DIAGNOSTICS_FIXTURE_PASS",
    "INITIAL_PROVIDER_REJECTION_CONSTRUCTOR_PRIVATE_PASS",
)


def owned_expected_graph():
    from verify_candidate_graph import load_candidate_graph
    graph = load_candidate_graph()
    return {owner: {'commit': graph['owners'][owner][1],
                    'tree': graph['reviewed_deltas'][owner]['tree']} for owner in OWNED_INPUTS}


def collect_owned_sources(sources):
    sources = Path(sources).resolve(strict=True)
    graph = owned_expected_graph()
    hashes = {}
    for owner, names in OWNED_INPUTS.items():
        root = sources / owner
        actual = subprocess.check_output(['git', '--no-replace-objects', '-C', str(root),
            'rev-parse', 'HEAD', 'HEAD^{tree}'], text=True, timeout=15).splitlines()
        if actual != [graph[owner]['commit'], graph[owner]['tree']]:
            raise ValueError('Owned authentication source graph differs: ' + owner)
        for name in names:
            path = root / name
            cursor = sources
            for component in (owner + '/' + name).split('/'):
                cursor = cursor / component
                if cursor.is_symlink():
                    raise ValueError('Owned authentication input is a symlink')
            if not path.is_file():
                raise ValueError('Missing owned authentication input: ' + owner + '/' + name)
            raw = path.read_bytes()
            committed = subprocess.check_output(['git', '--no-replace-objects', '-C', str(root),
                'cat-file', 'blob', 'HEAD:' + name], timeout=15)
            if raw != committed:
                raise ValueError('Owned authentication input differs from committed blob: ' + owner + '/' + name)
            hashes[owner + '/' + name] = hashlib.sha256(raw).hexdigest()
    return {'source_graph': graph, 'source_sha256': hashes}


def run_owned_swift_suites(sources, evidence):
    sources, evidence = Path(sources).resolve(strict=True), Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    provenance = collect_owned_sources(sources)
    rows = []
    for owner, script, marker, repeats, timeout in OWNED_SWIFT_SUITES:
        result = subprocess.run(['bash', script], cwd=sources / owner, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        rejection_suite = script == '.ci/initial-provider-rejection/run.sh'
        log_name = 'owned-initial-provider-rejection.log' if rejection_suite else 'owned-' + owner.replace('/', '_') + '.log'
        (evidence / log_name).write_text(result.stdout)
        print(result.stdout, flush=True)
        result.check_returncode()
        if rejection_suite:
            for required in INITIAL_REJECTION_MARKERS:
                observed = [line for line in result.stdout.splitlines() if line.startswith(required.split(' ', 1)[0])]
                if observed != [required]:
                    raise ValueError('Required initial rejection execution missing or duplicate: ' + required)
        if sum(line.startswith(marker) for line in result.stdout.splitlines()) != repeats:
            raise ValueError('Required owned Swift executions not observed: ' + owner)
        rows.append({'owner': owner, 'suite': script, 'status': 'PASS', 'executions': repeats})
    if provenance != collect_owned_sources(sources):
        raise ValueError('Owned authentication inputs changed during execution')
    return dict(provenance, swift_suites=rows, target_device_execution=False)


def verify_owned_receipt(value, source_provenance=None):
    expected_rows = [{'owner': o, 'suite': s, 'status': 'PASS', 'executions': n}
                     for o, s, _, n, _ in OWNED_SWIFT_SUITES]
    expected_paths = {owner + '/' + name for owner, names in OWNED_INPUTS.items() for name in names}
    if (not isinstance(value, dict) or set(value) != {'source_graph', 'source_sha256', 'swift_suites', 'target_device_execution'}
            or value['source_graph'] != owned_expected_graph() or value['swift_suites'] != expected_rows
            or value['target_device_execution'] is not False
            or not all(type(row.get('executions')) is int for row in value['swift_suites'])
            or not isinstance(value['source_sha256'], dict) or set(value['source_sha256']) != expected_paths
            or not all(isinstance(x, str) and re.fullmatch('[0-9a-f]{64}', x) for x in value['source_sha256'].values())):
        raise ValueError('Owned authentication receipt graph, closure or execution differs')
    if source_provenance is not None and source_provenance != {key: value[key] for key in ('source_graph', 'source_sha256')}:
        raise ValueError('Owned authentication receipt differs from actual committed inputs')
    return value


RUST_FEATURES = {
    'idevice': 'ring,usbmuxd,cryptexd,tunnel_tcp_stack',
    # Custom USB tunnel startup imports HeartbeatClient unconditionally.
    # Production defaults include heartbeat; explicit no-default tests must too.
    'idevice-ffi': 'ring,tcp,usbmuxd,house_arrest,mobile_image_mounter,syslog_relay,springboardservices,tss,tunnel_tcp_stack,heartbeat,afc,amfi,bt_packet_logger,companion_proxy,location_simulation,misagent,mobileactivationd,notification_proxy,pcapd,preboard_service,screenshotr',
}
REQUIRED_RUST_TESTS = {
    'idevice': ('ddi_defaults_keep_distinct_persistence_fields',
                'custom_persistence_is_not_replaced_by_nonce_persistence',
                'real_listener_preserves_full_wire_disconnect_ids',
                'generic_tunnel_mss_is_bounded_without_small_mtu_underflow'),
    'idevice-ffi': ('optional_null_plist_is_absent',
                    'caller_owned_wrapper_survives_repeated_independent_clones',
                    'caller_retains_single_heap_owner_after_clone_is_dropped',
                    'actual_ffi_writes_full_width_disconnect_and_clears_stale_outputs',
                    'ffi_error_event_clears_outputs_and_null_input_is_rejected',
                    'lockdown_errors_preserve_provider',
                    'image_mounter_errors_preserve_provider',
                    'os_trace_errors_preserve_provider',
                    'syslog_errors_preserve_provider',
                    'springboard_errors_preserve_provider',
                    'house_arrest_errors_preserve_provider',
                    'consumed_connection_drops_socket_exactly_once_on_invalid_label',
                    'preboard_service_connect_errors_preserve_provider',
                    'notification_proxy_connect_errors_preserve_provider',
                    'companion_proxy_connect_errors_preserve_provider',
                    'screenshotr_connect_errors_preserve_provider',
                    'amfi_connect_errors_preserve_provider',
                    'bt_packet_logger_connect_errors_preserve_provider',
                    'pcapd_connect_errors_preserve_provider',
                    'lockdown_location_simulation_connect_errors_preserve_provider',
                    'misagent_connect_errors_preserve_provider',
                    'afc_client_connect_errors_preserve_provider',
                    'afc2_client_connect_errors_preserve_provider',
                    'heartbeat_connect_errors_preserve_provider',
                    'mobileactivationd_client_borrows_provider_until_freed',
                    ),
}


def validate_rust_output(package, output):
    summaries = re.findall(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;', output)
    if len(summaries) != 1 or int(summaries[0][0]) == 0 or any(int(x) for x in summaries[0][1:]):
        raise ValueError('Rust suite missing, failed, ignored or filtered: ' + package)
    for name in REQUIRED_RUST_TESTS[package]:
        if len(re.findall(r'^test [^\n ]*::' + re.escape(name) + r' \.\.\. ok$', output, re.M)) != 1:
            raise ValueError('Required real Rust test not executed exactly once: ' + name)



GENERATED_PROVIDER_MARKERS = (
    'BORROWED_PROVIDERS_PASS paths=13 success_failure_retry_move=true',
    'BORROWED_PROVIDER_GENERATED_HEADER_PASS complete_wrappers=12 paths=13',
)


def validate_generated_provider_output(output):
    for required in GENERATED_PROVIDER_MARKERS:
        observed = [line for line in output.splitlines() if line.startswith(required.split(' ', 1)[0])]
        if observed != [required]:
            raise ValueError('Generated-header ownership execution missing or duplicate: ' + required)


def verify_native_receipt(path):
    value = json.loads(Path(path).read_text())
    expected = {'features': RUST_FEATURES, 'required_tests': {k: list(v) for k, v in REQUIRED_RUST_TESTS.items()},
                'rust_suites': 'PASS_NO_SKIPS', 'generated_header_c_and_cpp': 'PASS',
                'toolchain': '1.98.1', 'target_device_execution': False}
    if value != expected or value.get('target_device_execution') is not False:
        raise ValueError('Native issue receipt closure or result differs')
    return value


def run_idevice_native():
    evidence = Path('evidence')
    evidence.mkdir(exist_ok=True)
    receipt = evidence / 'idevice-issue-tests.json'
    receipt.unlink(missing_ok=True)
    for package, features in RUST_FEATURES.items():
        command = ['cargo', '+1.98.1', 'test', '--locked', '-p', package, '--lib',
                   '--no-default-features', '--features', features, '--', '--color', 'never']
        result = subprocess.run(command, cwd='sources/idevice', text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800)
        (evidence / (package + '-tests.log')).write_text(result.stdout)
        print(result.stdout, flush=True)
        result.check_returncode()
        validate_rust_output(package, result.stdout)
    result = subprocess.run(['bash', '.ci/issue53/check_listener_abi.sh'], cwd='sources/idevice',
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    (evidence / 'idevice-listener-generated-abi.log').write_text(result.stdout)
    print(result.stdout, flush=True)
    result.check_returncode()
    validate_generated_provider_output(result.stdout)
    receipt.write_text(json.dumps({'features': RUST_FEATURES,
        'required_tests': REQUIRED_RUST_TESTS, 'rust_suites': 'PASS_NO_SKIPS',
        'generated_header_c_and_cpp': 'PASS', 'toolchain': '1.98.1',
        'target_device_execution': False}, indent=2))
    verify_native_receipt(receipt)


def verify_issue_receipt(path):
    value = json.loads(Path(path).read_text())
    verify_owned_receipt(value.get('owned_authentication'), collect_owned_sources(Path('sources')))
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
    Path('evidence').mkdir(exist_ok=True)
    Path('evidence/issue-tests.json').unlink(missing_ok=True)
    if not shutil.which('swiftc'):
        raise SystemExit('Swift compiler required; no skipped policy execution is accepted')
    # Bind the actual prompt extraction, production policy and native fixtures
    # before their mandatory source-owned suite executes, not only afterward.
    owned_before = collect_owned_sources('sources')
    rows = []
    for owner, folder, pattern in SUITES:
        cwd = Path('sources') / owner
        if not list((cwd / folder).glob(pattern)):
            raise SystemExit('missing required test: ' + owner + '/' + folder + '/' + pattern)
        subprocess.run([sys.executable, '-B', '-c', RUNNER, folder, pattern], cwd=cwd, check=True)
        rows.append({'owner': owner, 'suite': folder + '/' + pattern, 'status': 'PASS_NO_SKIPS'})
    subprocess.run(['bash', 'tests/reuse_main/run.sh'], cwd='sources/SideStore', check=True)
    subprocess.run(['bash', '.ci/reuse-main/run.sh'], cwd='sources/SideStore/Dependencies/SideSign', check=True)
    owned = run_owned_swift_suites('sources', 'evidence')
    verify_owned_receipt(owned, owned_before)
    for pattern in HARNESS_SUITES:
        subprocess.run([sys.executable, '-B', '-c', RUNNER, 'tests', pattern], cwd='harness', check=True)
    Path('evidence/issue-tests.json').write_text(json.dumps({'suites': rows, 'owned_authentication': owned,
        'registry_runtime_double': 'PASS', 'signer_policy_and_production_body_double': 'PASS', 'target_device_execution': False,
        'harness_suites': list(HARNESS_SUITES), 'all_suites_passed_no_skips': True}, indent=2))
    verify_issue_receipt('evidence/issue-tests.json')


if __name__ == '__main__':
    if sys.argv[1:] == ['--idevice-native']:
        run_idevice_native()
    elif sys.argv[1:]:
        raise SystemExit('unknown arguments')
    else:
        main()
