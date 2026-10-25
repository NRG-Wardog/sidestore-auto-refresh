"""Additional source-owned setup regressions. No app, provider, network or device execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from verify_comprehensive_diagnostics import regular, require, digest, write_json, artifact_inventory, require_marker

HERE = Path(__file__).resolve().parent
STATUS = 'SETUP_STATE_SOURCE_FIXTURES_PASS_NOT_DEVICE_VALIDATION'
BASELINE_OWNERS = {
    'LiveContainer': {'commit': 'cb21f150e8907d2f8ed1bc7658342bac3fbb9926',
                      'tree': '2d96ff02b99d4d4a52650582500ab1124486f6ef'},
    'SideStore/Dependencies/SideSign': {'commit': 'd05efa30f37bad7c7ddaa50186fc1ac8ba703eca',
        'tree': 'f3af3a9045733d8002465142cad20cf71a7e1076'},
    'SideStore': {'commit': '638fa62ce2a540efe6c92dd20c282c32ddc0b2e6',
                  'tree': '99d6650e20b29b5c061b2c1ea04f746b51417537'},
}
SUITES = ('setup-localization',
 'early-refresh-boundaries',
 'certificate-portal-evidence',
 'remote-identity-reuse',
 'setup-connection-evidence',
 'profile-preconditions',
 'vpn-refresh-recovery',
 'host-refresh-bundle',
 'cache-compatibility')
LOCALIZATION = 'LiveContainer/.ci/comprehensive-diagnostics'
EARLY = 'LiveContainer/.ci/early-refresh-boundaries'
PORTAL = 'LiveContainer/.ci/certificate-portal-evidence'
IDENTITY = 'SideStore/.ci/remote-identity-reuse'
IDENTITY_CASE_COUNT = 55
CONNECTION = 'LiveContainer/.ci/setup-connection-evidence'
VPN_RECOVERY = 'LiveContainer/.ci/vpn-refresh-recovery'
HOST_BUNDLE = 'SideStore/tests/host_refresh_bundle'
CACHE_COMPATIBILITY = 'SideStore/tests/cache_compatibility'
ACTIVATION_NEGATIVE_MUTATIONS = ('cause', 'code', 'generic_timeout_precedence', 'missing_override',
                                 'operation', 'raw_provider_copy', 'stage')
LOCALIZATION_CASE_COUNT = 1292
LOCALIZATION_KEY_COUNT = 184
LOCALIZATION_VALUE_COUNT = 736
LOCALIZATION_BASELINE_MISSING = 160
IDENTITY_SOURCE_WIRING = ('signin_returns_actual_attempt_handoff', 'signin_credential_transactions_record_selected_route', 'both_verified_terminal_paths_commit_identity', 'full_terminal_commit_precedes_journal_persist', 'headless_admission_captured_before_driver', 'profile_coalescers_use_scoped_admission', 'app_id_coalescer_uses_scoped_admission', 'portal_transport_checks_scoped_context', 'outer_sync_app_ids_captures_before_detached', 'outer_refresh_captures_before_detached', 'outer_background_refresh_captures_before_detached', 'background_callback_installed_before_enqueue_and_cancellation_checked', 'app_manager_completion_after_successful_admission_scope', 'pipeline_completion_after_successful_admission_scope', 'heavy_pipeline_does_not_publish_terminal_callback')
IDENTITY_SWIFT = ('CandidateCore.swift', 'BoundaryDoubles.swift', 'CoreCases.swift',
                  'CandidateRuntime.swift', 'RuntimeDoubles.swift', 'RuntimeCases.swift')
LOCALIZATION_INPUTS = {
    'ui': 'LiveContainer/LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
    'localization': 'LiveContainer/LiveContainerSwiftUI/Utilities/V3Localization.swift',
    'catalog': 'LiveContainer/Resources/Localizable.xcstrings',
    'display': 'LiveContainer/LiveContainerSwiftUI/Utilities/V3LanguageSettings.swift',
    'identity_diagnostics': 'LiveContainer/SideStoreSupport/SideStore.swift',
}
LOCALIZATION_FILES = ('generate_setup_localization_fixture.py', 'setup_localization_harness.swift',
                      'test_setup_localization_contract.py')
COMMANDS = ('delivered-baseline-extract',
 'localization-source',
 'early-source',
 'portal-source',
 'identity-controls',
 'identity-source',
 'connection-source',
 'profile-preconditions-source',
 'vpn-recovery-source',
 'host-refresh-bundle-source',
 'cache-compatibility-source',
 'localization-compile',
 'early-baseline-compile',
 'early-candidate-compile',
 'portal-baseline-compile',
 'portal-baseline-host-compile',
 'portal-candidate-compile',
 'portal-candidate-host-compile',
 'identity-compile',
 'connection-compile',
 'profile-preconditions-compile',
 'vpn-recovery-compile',
 'host-refresh-bundle-compile',
 'cache-compatibility-compile',
 'localization-run',
 'early-baseline-run',
 'early-candidate-run',
 'portal-baseline-run',
 'portal-baseline-host-run',
 'portal-candidate-run',
 'portal-candidate-host-run',
 'identity-run',
 'connection-run',
 'profile-preconditions-run',
 'vpn-recovery-run',
 'host-refresh-bundle-run',
 'cache-compatibility-run')
COMPILE_COMMANDS = tuple(name for name in COMMANDS if name.endswith('-compile'))


SUITE_INPUTS = {'early-refresh-boundaries': {'production': ['LiveContainer/LiveContainerSwiftUI/App/AppDelegate.swift',
                                             'LiveContainer/LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
                                             'LiveContainer/SideStoreSupport/SideStore.swift',
                                             'LiveContainer/SideStoreSupport/SideStoreClient.swift',
                                             'SideStore/AltStore/AppDelegate.swift',
                                             'SideStore/AltStore/Core/Model/StoreApp.swift',
                                             'SideStore/AltStore/Intents/App '
                                             'Intents/RefreshAllAppsIntent.swift',
                                             'SideStore/AltStore/Managing Apps/AppManager.swift',
                                             'SideStore/Shared/Errors/ALTWrappedError.swift',
                                             'SideStore/Shared/Extensions/Bundle+AltStore.swift',
                                             'SideStore/Shared/Extensions/NSError+AltStore.swift',
                                             'SideStore/SideStore/Core/Auth/AuthManager.swift',
                                             'SideStore/SideStore/Core/DeviceApi/MinimuxerWrapper.swift',
                                             'SideStore/SideStore/Core/Operations/AppOperation.swift',
                                             'SideStore/SideStore/Core/Operations/Errors/OperationError.swift',
                                             'SideStore/SideStore/Core/Operations/OperationStep.swift',
                                             'SideStore/SideStore/Core/Operations/PipelineExecutor.swift',
                                             'SideStore/SideStore/Core/Operations/PipelineOperations/PreflightChecksOperation.swift',
                                             'SideStore/SideStore/Core/Operations/PipelineRunner.swift',
                                             'SideStore/SideStore/Core/Operations/RefreshGroup.swift',
                                             'SideStore/SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift'],
                              'support': ['LiveContainer/.ci/comprehensive-diagnostics/generate_wire_fixture.py',
                                          'LiveContainer/.ci/comprehensive-diagnostics/refresh_failure_doubles.swift',
                                          'LiveContainer/.ci/comprehensive-diagnostics/refresh_handler_doubles.swift',
                                          'LiveContainer/.ci/comprehensive-diagnostics/refresh_failure_harness.swift',
                                          'SideStore/tests/refresh_failure/pipeline_harness.swift'],
                              'fixture': ['LiveContainer/.ci/early-refresh-boundaries/generate_regression.py',
                                          'LiveContainer/.ci/early-refresh-boundaries/early_doubles.swift',
                                          'LiveContainer/.ci/early-refresh-boundaries/early_cases.swift',
                                          'LiveContainer/.ci/early-refresh-boundaries/baseline-inputs.json',
                                           'LiveContainer/.ci/early-refresh-boundaries/test_contract.py',
                                           'LiveContainer/.ci/early-refresh-boundaries/README.md']},
 'certificate-portal-evidence': {'production': ['SideStore/AltStore/AppDelegate.swift',
                                                'LiveContainer/SideStoreSupport/SideStore.swift',
                                                'SideStore/SideStore/Core/Operations/PipelineExecutor.swift',
                                                'SideStore/SideStore/Core/Auth/DeveloperPortalProxy.swift',
                                                'SideStore/SideStore/Core/Operations/Errors/OperationError.swift',
                                                'SideStore/Shared/Errors/ALTWrappedError.swift',
                                                'SideStore/Dependencies/SideSign/Sources/DeveloperPortal/DeveloperPortalAPI.swift',
                                                'SideStore/Dependencies/SideSign/Sources/DeveloperPortal/Certificates.swift',
                                                'SideStore/Dependencies/SideSign/Sources/Models/DeveloperPortalResponses.swift',
                                                'SideStore/Dependencies/SideSign/Sources/Models/X509Certificate.swift',
                                                'SideStore/Dependencies/SideSign/Sources/Models/Errors.swift',
                                                'SideStore/Dependencies/SideSign/Sources/Constants.swift',
                                                'SideStore/Dependencies/SideSign/Sources/SideSign.swift'],
                                 'support': ['LiveContainer/.ci/comprehensive-diagnostics/generate_wire_fixture.py',
                                             'LiveContainer/.ci/certificate-portal-evidence/generate_regression.py',
                                             'LiveContainer/.ci/certificate-portal-evidence/portal_doubles.swift',
                                             'LiveContainer/.ci/certificate-portal-evidence/service_cases.swift',
                                             'LiveContainer/.ci/certificate-portal-evidence/host_cases.swift']}}

SUITE_INPUTS['remote-identity-reuse'] = {'production': ['SideStore/AltStore/AppDelegate.swift',
                'SideStore/AltStore/Managing Apps/AppManager.swift',
                'SideStore/SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift',
                'SideStore/AltStore/Core/Components/Keychain.swift',
                'SideStore/Dependencies/SideSign/Sources/Anisette/ILoaderRemoteAnisette.swift',
                'SideStore/Dependencies/SideSign/Sources/DeveloperPortal/DeveloperPortalAPI.swift',
                'SideStore/SideStore/Core/Anisette/AnisetteProvider.swift',
                'SideStore/SideStore/Core/Auth/AuthManager.swift',
                'SideStore/SideStore/Core/Auth/DeveloperPortalProxy.swift',
                'SideStore/SideStore/Core/Operations/PipelineOperations/FetchProvisioningProfilesOperation.swift',
                'SideStore/SideStore/Core/Operations/PipelineRunner.swift',
                'SideStore/SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift',
                'SideStore/SideStore/Core/Operations/StandaloneOperations/SyncAppIDsOperation.swift',
                'SideStore/SideStore/Utils/concurrency/TaskChainCoalescer.swift'],
 'support': ['SideStore/.ci/remote-identity-reuse/generate_fixture.py',
             'SideStore/.ci/remote-identity-reuse/BoundaryDoubles.swift',
             'SideStore/.ci/remote-identity-reuse/CoreCases.swift',
             'SideStore/.ci/remote-identity-reuse/RuntimeDoubles.swift',
             'SideStore/.ci/remote-identity-reuse/RuntimeCases.swift',
             'SideStore/.ci/remote-identity-reuse/run.sh',
             'SideStore/.ci/remote-identity-reuse/README.md',
             'SideStore/.ci/remote-identity-reuse/test_generator.py']}

# Candidate-only suites do not redefine or widen the retained delivered baseline.
ADDITIVE_SUITE_INPUTS = {'setup-connection-evidence': {'production': ['LiveContainer/LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
                                              'LiveContainer/SideStoreSupport/SideStore.swift',
                                              'SideStore/AltStore/AppDelegate.swift',
                                              'LiveContainer/.ci/comprehensive-diagnostics/generate_wire_fixture.py',
                                              'LiveContainer/Resources/Localizable.xcstrings'],
                               'support': [],
                               'fixture': ['LiveContainer/.ci/setup-connection-evidence/generate_fixture.py',
                                           'LiveContainer/.ci/setup-connection-evidence/cases.swift'],
                               'other': ['LiveContainer/.ci/setup-connection-evidence/README.md']},
 'profile-preconditions': {'production': ['LiveContainer/LiveContainerSwiftUI/App/AppDelegate.swift',
                                          'LiveContainer/LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
                                          'LiveContainer/SideStoreSupport/SideStore.swift',
                                          'LiveContainer/SideStoreSupport/SideStoreClient.swift',
                                          'SideStore/AltStore/AppDelegate.swift',
                                          'SideStore/AltStore/Intents/App Intents/RefreshAllAppsIntent.swift',
                                          'SideStore/AltStore/Managing Apps/AppManager.swift',
                                          'SideStore/Shared/Errors/ALTWrappedError.swift',
                                          'SideStore/Shared/Extensions/NSError+AltStore.swift',
                                          'SideStore/SideStore/Core/Operations/AppOperation.swift',
                                          'SideStore/SideStore/Core/Operations/Errors/OperationError.swift',
                                          'SideStore/SideStore/Core/Operations/OperationStep.swift',
                                          'SideStore/SideStore/Core/Operations/PipelineExecutor.swift',
                                          'SideStore/SideStore/Core/Operations/PipelineRunner.swift',
                                          'SideStore/SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift'],
                           'support': ['LiveContainer/.ci/comprehensive-diagnostics/generate_wire_fixture.py',
                                       'LiveContainer/.ci/comprehensive-diagnostics/refresh_failure_doubles.swift',
                                       'LiveContainer/.ci/comprehensive-diagnostics/refresh_handler_doubles.swift',
                                       'LiveContainer/.ci/comprehensive-diagnostics/refresh_failure_harness.swift',
                                       'SideStore/tests/refresh_failure/pipeline_harness.swift',
                                       'LiveContainer/.ci/early-refresh-boundaries/generate_regression.py',
                                       'LiveContainer/.ci/early-refresh-boundaries/early_doubles.swift'],
                           'fixture': ['LiveContainer/.ci/early-refresh-boundaries/generate_profile_regression.py',
                                       'LiveContainer/.ci/early-refresh-boundaries/profile_cases.swift',
                                       'LiveContainer/.ci/early-refresh-boundaries/PROFILE-PRECONDITIONS.md'],
                           'other': []},
 'vpn-refresh-recovery': {'production': ['LiveContainer/LiveContainerSwiftUI/App/AppDelegate.swift',
                                         'LiveContainer/SideStoreSupport/SideStore.swift',
                                         'SideStore/SideStore/Core/Operations/PipelineExecutor.swift',
                                         'SideStore/SideStore/Core/Operations/Errors/OperationError.swift',
                                         'LiveContainer/.ci/comprehensive-diagnostics/generate_wire_fixture.py',
                                         'LiveContainer/SideStoreSupport/XPCClient.m',
                                         'SideStore/AltStore/AppDelegate.swift'],
                          'support': [],
                          'fixture': ['LiveContainer/.ci/vpn-refresh-recovery/generate_fixture.py',
                                      'LiveContainer/.ci/vpn-refresh-recovery/cases.swift'],
                          'other': ['LiveContainer/.ci/vpn-refresh-recovery/README.md']}}
ADDITIVE_SUITE_INPUTS['host-refresh-bundle'] = {
    'production': ['SideStore/SideStore/Core/Operations/PipelineRunner.swift',
                   'SideStore/SideStore/Utils/common/AppBundleFingerprint.swift'],
    'support': [],
    'fixture': [HOST_BUNDLE + '/' + name for name in
                ('BoundaryDoubles.swift', 'Cases.swift', 'test_contract.py', 'generate_fixture.py')],
    'other': []}
ADDITIVE_SUITE_INPUTS['cache-compatibility'] = {'production': ['SideStore/AltStore/Core/Model/InstalledApp.swift', 'SideStore/AltStore/Extensions/FileManager+URLs.swift', 'SideStore/SideStore/Core/Operations/PipelineOperations/CacheAppOperation.swift', 'SideStore/SideStore/Core/Operations/PipelineRunner.swift', 'SideStore/SideStore/Utils/common/AppBundleFingerprint.swift'], 'support': [], 'fixture': ['SideStore/tests/cache_compatibility/Cases.swift', 'SideStore/tests/cache_compatibility/OriginalAppBundleFingerprint.swift', 'SideStore/tests/cache_compatibility/OriginalCacheAppOperation.swift', 'SideStore/tests/cache_compatibility/generate_fixture.py', 'SideStore/tests/cache_compatibility/BeforeCanonicalAnchorCacheAppOperation.swift'], 'other': []}
ADDITIVE_SUITE_INPUTS['vpn-refresh-recovery'] = {'production': ['LiveContainer/LiveContainerSwiftUI/App/AppDelegate.swift', 'LiveContainer/SideStoreSupport/SideStore.swift', 'SideStore/SideStore/Core/Operations/PipelineExecutor.swift', 'SideStore/SideStore/Core/Operations/Errors/OperationError.swift', 'LiveContainer/.ci/comprehensive-diagnostics/generate_wire_fixture.py', 'LiveContainer/SideStoreSupport/XPCClient.m', 'SideStore/AltStore/AppDelegate.swift', 'SideStore/SideStore/Core/Operations/PipelineRunner.swift', 'SideStore/tests/vpn_transport_recovery/generate_fixture.py'], 'support': ['SideStore/Dependencies/minimuxer/Sources/MinimuxerImpl.swift', 'SideStore/Dependencies/minimuxer/Sources/Services/DeviceEndpoint.swift', 'SideStore/Dependencies/minimuxer/Sources/MinimuxerInternalError.swift'], 'fixture': ['LiveContainer/.ci/vpn-refresh-recovery/generate_fixture.py', 'LiveContainer/.ci/vpn-refresh-recovery/cases.swift', 'LiveContainer/.ci/vpn-refresh-recovery/OriginalRefreshReadinessFacts.swift', 'LiveContainer/.ci/vpn-refresh-recovery/cold-peer-source.json', 'LiveContainer/.ci/vpn-refresh-recovery/PlatformDoubles.swift'], 'other': ['LiveContainer/.ci/vpn-refresh-recovery/README.md'], 'transport_fixture': ['SideStore/Dependencies/minimuxer/Sources/RefreshTransportPolicy.swift', 'SideStore/Dependencies/minimuxer/Sources/MinimuxerImpl.swift', 'SideStore/Dependencies/minimuxer/Sources/Services/NetworkIfaceScanner.swift', 'SideStore/Dependencies/minimuxer/Sources/Services/NetworkObserverService.swift', 'SideStore/Dependencies/minimuxer/Sources/MinimuxerError.swift', 'SideStore/Dependencies/minimuxer/Sources/MinimuxerApi.swift', 'SideStore/Dependencies/minimuxer/Common/PairingProtocol.swift', 'SideStore/Dependencies/minimuxer/DeviceGateway/DeviceGatewayError.swift', 'SideStore/SideStore/Core/DeviceApi/MinimuxerWrapper.swift', 'SideStore/SideStore/Core/Operations/Errors/OperationError.swift', 'SideStore/SideStore/Core/Operations/PipelineExecutor.swift', 'SideStore/tests/vpn_transport_recovery/generate_fixture.py', 'SideStore/tests/vpn_transport_recovery/interface_doubles.swift', 'SideStore/tests/vpn_transport_recovery/OriginalRefreshTransportPolicy.swift', 'SideStore/tests/vpn_transport_recovery/OriginalReadinessUnavailable.swift', 'SideStore/tests/vpn_transport_recovery/OriginalTransportSnapshot.swift', 'SideStore/tests/vpn_transport_recovery/baseline-receipt.json']}
VPN_OBSERVATION_HOOK = {'source': 'LiveContainer/LiveContainerSwiftUI/App/AppDelegate.swift', 'anchor': 'final class LiveContainerVPNActivationState {', 'original_sha256': 'a53908f887987a74d7aae922ee47f0fdd2c82ac5e8fb1d571f90de7b2fc6ef50', 'instrumented_sha256': 'f7eda491d45193769ef2cdce9c731cb528ae3f77e5aa30014f5524bfddba6e02', 'exact_insertion_count': 1, 'description': 'Observation-only didSet on leftHost awaits the original background observer assignment before injecting active; no fixture writes leftHost and no production decision or method body changes.'}
ADDITIVE_SPECS = {
    'setup-connection-evidence': {'prefix': 'connection', 'folder': CONNECTION,
        'generator': 'generate_fixture.py', 'receipt': 'sources.json',
        'source_schema': 'setup-connection-sources-v1', 'swift': 'setup-connection.swift',
        'cases_schema': 'setup-connection-cases-v1', 'case_count': 25},
    'profile-preconditions': {'prefix': 'profile-preconditions', 'folder': EARLY,
        'generator': 'generate_profile_regression.py', 'receipt': 'profile-sources.json',
        'source_schema': 'profile-preconditions-source-extraction-v1', 'swift': 'profile-preconditions.swift',
        'cases_schema': 'profile-preconditions-cases-v1', 'case_count': 25},
    'vpn-refresh-recovery': {'prefix': 'vpn-recovery', 'folder': VPN_RECOVERY,
        'generator': 'generate_fixture.py', 'receipt': 'sources.json',
        'source_schema': 'vpn-refresh-recovery-sources-v1', 'swift': 'vpn-refresh-recovery.swift',
        'cases_schema': 'vpn-refresh-recovery-cases-v1', 'case_count': 89},
    'host-refresh-bundle': {'prefix': 'host-refresh-bundle', 'folder': HOST_BUNDLE,
        'generator': 'generate_fixture.py', 'receipt': 'sources.json',
        'source_schema': 'v3-host-refresh-bundle-source-v1', 'swift': 'host-refresh-bundle.swift',
        'cases_schema': 'v3-host-refresh-bundle-runtime-v1', 'case_count': 30},
    'cache-compatibility': {'prefix': 'cache-compatibility', 'folder': 'SideStore/tests/cache_compatibility', 'generator': 'generate_fixture.py', 'receipt': 'sources.json', 'source_schema': 'cache-compatibility-source-v1', 'swift': 'cache-compatibility.swift', 'cases_schema': 'cache-compatibility-cases-v1', 'case_count': 26},
}


def load_contract(path):
    value = json.loads(path.read_text())
    require(value.get('schema') == 'setup-state-recovery-contract-v1', 'Setup contract schema differs')
    require(value.get('source_inputs_bound') is True, 'Setup source hashes remain unbound')
    require(value.get('suites') == list(SUITES), 'Setup suite closure differs')
    require(value.get('delivered_baseline', {}).get('owners') == BASELINE_OWNERS,
            'Setup delivered baseline commit/tree differs')
    required = set(LOCALIZATION_INPUTS.values()) | {LOCALIZATION + '/' + f for f in LOCALIZATION_FILES}
    required |= {EARLY + '/test_contract.py', PORTAL + '/test_generator.py'}
    for spec in SUITE_INPUTS.values():
        for paths in spec.values():
            required.update(paths)
    for spec in ADDITIVE_SUITE_INPUTS.values():
        for paths in spec.values():
            required.update(paths)
    require(value.get('additive_suite_inputs') == ADDITIVE_SUITE_INPUTS, 'Additive exact source path closure differs')
    require(value.get('suite_inputs') == SUITE_INPUTS, 'Setup exact source path closure differs')
    for key in ('input_sha256',):
        hashes = value.get(key)
        require(isinstance(hashes, dict) and required <= set(hashes), 'Incomplete setup source hashes')
        require(all(isinstance(k, str) and isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v)
                    for k, v in hashes.items()), 'Malformed setup source hashes')
    baseline = value['delivered_baseline'].get('input_sha256')
    require(isinstance(baseline, dict) and set(LOCALIZATION_INPUTS.values()).union(*(set(v['production']) for v in SUITE_INPUTS.values())) == set(baseline),
            'Incomplete delivered baseline source hashes')
    require(all(isinstance(k, str) and k.split('/', 1)[0] in BASELINE_OWNERS and
                isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) for k, v in baseline.items()),
            'Malformed delivered baseline source hashes')
    for suite, count in [('setup-localization', LOCALIZATION_CASE_COUNT), ('early-refresh-boundaries', 24),
                         ('certificate-portal-evidence', 19), ('remote-identity-reuse', IDENTITY_CASE_COUNT),
                         *((suite, spec['case_count']) for suite, spec in ADDITIVE_SPECS.items())]:
        names = value.get('case_names', {}).get(suite)
        require(isinstance(names, list) and len(names) == count and len(set(names)) == count and
                all(isinstance(n, str) and n for n in names), 'Invalid exact case closure: ' + suite)
    return value


def inventory(sources, contract):
    actual = {p: digest(regular(sources, p).read_bytes()) for p in contract['input_sha256']}
    require(actual == contract['input_sha256'], 'Setup candidate source hashes differ')
    for folder in (EARLY, PORTAL, IDENTITY, CONNECTION, VPN_RECOVERY, HOST_BUNDLE, CACHE_COMPATIBILITY):
        root = sources / folder
        require(root.is_dir() and not root.is_symlink(), 'Missing setup fixture package')
        for p in root.rglob('*'):
            require(not p.is_symlink(), 'Symlink setup fixture input')
            if p.is_file():
                require(p.relative_to(sources).as_posix() in actual, 'Unpinned setup fixture input')
    return actual


def extract_baseline(sources, contract, output, receipt):
    """Same pinned local-object pattern as extract_deferred_baseline; never fetch."""
    baseline = contract['delivered_baseline']
    require(not sources.is_symlink(), 'Symlink setup source root')
    sources = sources.resolve(strict=True)
    require(not output.exists() and not output.is_symlink() and
            not receipt.exists() and not receipt.is_symlink(), 'Fresh baseline output required')
    require(not output.resolve().is_relative_to(sources), 'Baseline output inside source inputs')
    environment = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                       GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1',
                       GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='')
    def git(owner, *arguments):
        checkout = sources / owner
        require(checkout.is_dir() and not checkout.is_symlink() and (checkout / '.git').exists(),
                'Missing exact local Git checkout')
        result = subprocess.run(['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
                                '-C', str(checkout), *arguments], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                                env=environment, timeout=60)
        return result.stdout
    for owner, value in baseline['owners'].items():
        commit = value['commit']
        require(git(owner, 'cat-file', '-t', commit).decode().strip() == 'commit', 'Delivered commit absent')
        require(git(owner, 'rev-parse', commit + '^{tree}').decode().strip() == value['tree'],
                'Delivered source tree differs')
    contents = {}
    for name, expected in baseline['input_sha256'].items():
        owner = next((o for o in sorted(BASELINE_OWNERS, key=len, reverse=True) if name.startswith(o + '/')), None)
        require(owner is not None, 'Unknown baseline owner')
        relative = name[len(owner) + 1:]
        # Reject unsafe paths before requesting an object or creating output.
        require(relative and not relative.startswith('/') and '..' not in Path(relative).parts and
                name == Path(name).as_posix(), 'Unsafe baseline relative path')
        raw = git(owner, 'show', baseline['owners'][owner]['commit'] + ':' + relative)
        require(digest(raw) == expected, 'Delivered baseline source bytes differ: ' + name)
        contents[name] = raw
    output.mkdir(parents=True)
    for name, raw in contents.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        require(digest(regular(output, name).read_bytes()) == baseline['input_sha256'][name],
                'Delivered baseline changed during extraction')
    write_json(receipt, {'schema': 'setup-delivered-baseline-v1', 'origin': 'exact-local-git-objects',
                        **baseline, 'network': False, 'target_execution': False})


def baseline_receipt(output, contract):
    value = json.loads(regular(output, 'delivered-baseline.json').read_text())
    expected = {'schema': 'setup-delivered-baseline-v1', 'origin': 'exact-local-git-objects',
                **contract['delivered_baseline'], 'network': False, 'target_execution': False}
    require(value == expected, 'Delivered baseline extraction receipt differs')
    actual = {name: digest(regular(output / 'delivered-baseline', name).read_bytes())
              for name in contract['delivered_baseline']['input_sha256']}
    require(actual == contract['delivered_baseline']['input_sha256'], 'Retained delivered baseline changed')
    return value


def exact_cases(path, schema, expected, **attributes):
    value = json.loads(path.read_text())
    require(value.get('schema') == schema and value.get('status') == 'pass', 'Fixture runtime did not pass')
    require(value.get('case_count') == len(expected) and isinstance(value.get('cases'), list) and len(set(value['cases'])) == len(expected) and
            sorted(value['cases']) == sorted(expected),
            'Fixture case closure differs')
    require(all(value.get(k) == v and type(value.get(k)) is type(v) for k, v in attributes.items()),
            'Fixture runtime boundary differs')
    return value


def retained_baseline_copy(contract):
    sections = {'Checked after a successful refresh': 'assistant',
                'Tunnel interface present (not a CoreDevice proof)': 'setup_store',
                'Tunnel not present': 'setup_store', 'Verified by successful refresh': 'assistant'}
    return {key: {'source': 'LiveContainerSwiftUI/Views/V3UnifiedShell.swift',
                  'source_sha256': contract['delivered_baseline']['input_sha256'][LOCALIZATION_INPUTS['ui']],
                  'baseline_sections': [section], 'emitted_by_current_ui': False}
            for key, section in sections.items()}


def localization_extraction(output, contract):
    root = output / 'setup-localization'
    value = json.loads(regular(root, 'sources.json').read_text())
    require(value.get('schema') == 'setup-localization-sources-v1' and
            value.get('status') == 'generated_not_compiled', 'Localization extraction status differs')
    require(value.get('source_sha256') == {k: contract['input_sha256'][p] for k, p in LOCALIZATION_INPUTS.items()},
            'Localization current-source extraction differs')
    require(value.get('baseline_sha256') == {k: contract['delivered_baseline']['input_sha256'][p]
                                           for k, p in LOCALIZATION_INPUTS.items()},
            'Localization baseline-source extraction differs')
    require(value.get('fixture_inputs') == {n: contract['input_sha256'][LOCALIZATION + '/' + n]
                                          for n in LOCALIZATION_FILES}, 'Localization fixture hashes differ')
    require(value.get('key_count') == LOCALIZATION_KEY_COUNT and value.get('localized_value_count') == LOCALIZATION_VALUE_COUNT and
            value.get('expected_native_case_count') == LOCALIZATION_CASE_COUNT and
            value.get('expected_case_names') == contract['case_names']['setup-localization'],
            'Localization exact case contract differs')
    require(value.get('retained_baseline_copy') == retained_baseline_copy(contract),
            'Inherited baseline copy provenance differs')
    hashes = value.get('output_sha256')
    require(isinstance(hashes, dict) and 'fixture.swift' in hashes and 'baseline_missing_keys.json' in hashes,
            'Localization generated closure absent')
    require({n: digest(regular(root, n).read_bytes()) for n in hashes} == hashes,
            'Localization generated bytes differ')
    require(set(root.glob('*.swift')) == {root / 'fixture.swift'}, 'Unexpected localization generated Swift')
    static = json.loads(regular(root, 'static-contract.json').read_text())
    require(static.get('schema') == 'setup-localization-static-contract-v1' and static.get('status') == 'pass' and
            static.get('candidate_key_coverage') == 'pass' and static.get('baseline_key_coverage') == 'fail_as_expected' and
            len(static.get('baseline_missing', [])) == LOCALIZATION_BASELINE_MISSING and
            static.get('missing_translation_mutation_cases') == LOCALIZATION_VALUE_COUNT and
            isinstance(static.get('identity_diagnostic_keys'), list) and
            len(set(static['identity_diagnostic_keys'])) == len(static['identity_diagnostic_keys']) == 6 and
            static.get('source_sha256') == value['source_sha256'] and
            static.get('baseline_sha256') == value['baseline_sha256'],
            'Localization static/negative-control receipt differs')
    require(static.get('retained_baseline_copy') == retained_baseline_copy(contract) and
            static.get('exact_connection_scope_negative_control') == 'unrelated_cancel_mutation_rejected',
            'Localization deliberate-scope negative control differs')
    return value


class Runner:
    def __init__(self, output):
        self.output, self.commands = output, []
    def command(self, name, argv, marker=None, *, collect_compile_failure=False):
        require(name in COMMANDS and name not in [r['name'] for r in self.commands], 'Unexpected/repeated setup command')
        require(not collect_compile_failure or (name in COMPILE_COMMANDS and marker is None),
                'Only setup compile commands may collect failures')
        row = {'name': name, 'argv': list(map(str, argv)), 'exit_code': None}
        self.commands.append(row)
        log = self.output / ('commands/' + name + '.log')
        log.parent.mkdir(exist_ok=True)
        try:
            result = subprocess.run(row['argv'], cwd=self.output, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=300)
            data, row['exit_code'] = result.stdout, result.returncode
        except subprocess.TimeoutExpired as error:
            data = (error.stdout or b'') + b'\nCOMMAND_TIMEOUT\n'
            row['timed_out'] = True
        except OSError as error:
            data = str(error).encode()
            row['launch_error'] = type(error).__name__
        log.write_bytes(data)
        row.update(log=log.relative_to(self.output).as_posix(), stdout_sha256=digest(data))
        if not collect_compile_failure:
            require(type(row['exit_code']) is int and row['exit_code'] == 0, 'Setup command failed: ' + name)
        if marker:
            require_marker(data.decode('utf-8', errors='replace'), marker)
            row['success_marker'] = marker
        return row


def variant_extraction(output, contract, suite, variant):
    root = output / suite / variant
    value = json.loads(regular(root, 'sources.json').read_text())
    schemas = {'early-refresh-boundaries': 'early-refresh-source-extraction-v1',
               'certificate-portal-evidence': 'certificate-portal-evidence-sources-v2'}
    require(value.get('schema') == schemas[suite] and value.get('variant') == variant and
            value.get('status') == 'GENERATED_NOT_COMPILED_NOT_RUN', 'Variant extraction identity differs')
    for key in ('swift_executed', 'native_provider_executed', 'network_executed'):
        require(value.get(key) is False, 'Unbounded fixture generation: ' + key)
    names = contract['case_names'][suite]
    require(value.get('expected_case_names') == names and value.get('expected_case_count') == len(names),
            'Variant extraction case closure differs')
    spec = SUITE_INPUTS[suite]
    production = contract['delivered_baseline']['input_sha256'] if variant == 'baseline' else contract['input_sha256']
    require(value.get('inputs_sha256') == {p: production[p] for p in spec['production']},
            'Variant actual-source inputs differ')
    require(value.get('support_inputs_sha256') == {p: contract['input_sha256'][p] for p in spec['support']},
            'Variant candidate support inputs differ')
    if suite == 'early-refresh-boundaries':
        require(value.get('admitted_route_body_extracted') is (variant == 'candidate'),
                'Early-refresh admitted-route helper extraction differs')
        require(value.get('fixture_inputs_sha256') == {p: contract['input_sha256'][p] for p in spec['fixture']},
                'Early-refresh fixture inputs differ')
        hashes = {'early-refresh.swift': value.get('generated_sha256')}
    else:
        hashes = value.get('generated_sha256')
        require(isinstance(hashes, dict) and set(hashes) == {'host.swift', 'service.swift'},
                'Portal exact generated closure differs')
    require({p.name for p in root.glob('*.swift')} == set(hashes), 'Unexpected generated Swift file')
    require({p: digest(regular(root, p).read_bytes()) for p in hashes} == hashes,
            'Variant generated source bytes differ')
    return value



def identity_extraction(output, contract):
    root = output / 'remote-identity-reuse'
    value = json.loads(regular(root, 'source-evidence.json').read_text())
    require(value.get('schema') == 'remote-identity-reuse-source-evidence-v1' and
            value.get('status') == 'GENERATED_NOT_COMPILED_NOT_RUN', 'Identity source extraction status differs')
    for key in ('swift_executed', 'network_executed', 'native_provider_executed', 'native_keychain_executed'):
        require(value.get(key) is False, 'Unbounded identity generation: ' + key)
    spec = SUITE_INPUTS['remote-identity-reuse']
    for key, paths, inventory in (
        ('inputs_sha256', spec['production'], contract['input_sha256']),
        ('baseline_inputs_sha256', spec['production'], contract['delivered_baseline']['input_sha256']),
        ('support_inputs_sha256', spec['support'], contract['input_sha256'])):
        require(value.get(key) == {p: inventory[p] for p in paths}, 'Identity actual-source input closure differs: ' + key)
    require(value.get('expected_case_names') == contract['case_names']['remote-identity-reuse'],
            'Identity source-owned expected cases differ')
    hashes = value.get('outputs_sha256')
    require(isinstance(hashes, dict) and set(hashes) == set(IDENTITY_SWIFT) and
            {p.name for p in root.glob('*.swift')} == set(IDENTITY_SWIFT), 'Identity generated Swift closure differs')
    require({p: digest(regular(root, p).read_bytes()) for p in hashes} == hashes,
            'Identity generated source bytes differ')
    require(value.get('baseline_source_gaps') == {'protected_identity_record_absent': True,
            'protected_identity_adapter_absent': True, 'attempt_identity_only': True},
            'Delivered baseline identity negative controls differ')
    require(value.get('candidate_source_wiring') == {name: True for name in IDENTITY_SOURCE_WIRING},
            'Identity exact source-wiring controls differ')
    spans = value.get('production_extracts')
    require(isinstance(spans, list) and len(spans) > 0 and all(
        isinstance(row, dict) and row.get('source') in spec['production'] and
        isinstance(row.get('anchor'), str) and row['anchor'] and
        type(row.get('first_line')) is int and row['first_line'] > 0 and
        isinstance(row.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', row['sha256']) for row in spans),
        'Identity production extraction receipts differ')
    return value


def additive_extraction(output, contract, suite):
    spec, inputs = ADDITIVE_SPECS[suite], ADDITIVE_SUITE_INPUTS[suite]
    root = output / suite
    value = json.loads(regular(root, spec['receipt']).read_text())
    if suite == 'host-refresh-bundle':
        require(value.get('schema') == spec['source_schema'] and value.get('pass') is True,
                'Host bundle source extraction did not pass')
        for key in ('swift_executed', 'network_executed', 'provider_executed', 'device_executed'):
            require(value.get(key) is False, 'Unbounded host bundle generation: ' + key)
        require(value.get('source_assertion_count') == 34 and
                value.get('expected_case_names') == contract['case_names'][suite] and
                value.get('expected_case_count') == spec['case_count'],
                'Host bundle static assertion or case closure differs')
        for key, paths in (('inputs', inputs['production']), ('support', inputs['fixture'])):
            require(value.get(key) == [{'path': p.removeprefix('SideStore/'),
                    'sha256': contract['input_sha256'][p]} for p in paths],
                    'Host bundle exact source path or hash closure differs: ' + key)
        require({p.name for p in root.glob('*.swift')} == {spec['swift']} and
                value.get('generated_source') == {'path': spec['swift'],
                    'sha256': digest(regular(root, spec['swift']).read_bytes())},
                'Host bundle generated source closure or bytes differ')
        return value
    require(value.get('schema') == spec['source_schema'] and
            value.get('status') == 'STATIC_PASS_GENERATED_NOT_COMPILED_NOT_RUN',
            'Additive extraction identity differs: ' + suite)
    for key in ('swift_compiled', 'swift_executed', 'native_provider_executed', 'network_executed'):
        require(value.get(key) is False, 'Unbounded additive generation: ' + key)
    require(value.get('expected_case_names') == contract['case_names'][suite] and
            value.get('expected_case_count') == spec['case_count'], 'Additive exact source case closure differs')
    require(value.get('inputs_sha256') == {p: contract['input_sha256'][p] for p in inputs['production']},
            'Additive production source hashes differ')
    support_hashes = {p: contract['input_sha256'][p] for p in inputs['support']}
    if suite == 'vpn-refresh-recovery':
        # The source-owned receipt uses exact dependency-relative paths.
        prefix = 'SideStore/Dependencies/minimuxer/'
        require(all(p.startswith(prefix) for p in support_hashes), 'VPN support path scope differs')
        support_hashes = {p[len(prefix):]: value for p, value in support_hashes.items()}
        require(value.get('cold_guard_provenance') == 'actual_unchanged_guard_blocks_and_historical_baseline' and
                value.get('baseline_classifier_sha256') == contract['input_sha256'][
                    VPN_RECOVERY + '/OriginalRefreshReadinessFacts.swift'],
                'VPN actual dependency or original classifier proof differs')
    require(value.get('support_inputs_sha256', {}) == support_hashes,
            'Additive support source hashes differ')
    if suite == 'vpn-refresh-recovery':
        require(value.get('transport_fixture_inputs_sha256') == {
                p: contract['input_sha256'][p] for p in inputs['transport_fixture']},
                'VPN transport fixture exact source path or hash closure differs')
        require(value.get('fixture_observation_hook') == VPN_OBSERVATION_HOOK,
                'VPN deterministic observation hook differs')

    require(value.get('fixture_inputs_sha256') == {Path(p).name: contract['input_sha256'][p] for p in inputs['fixture']},
            'Additive fixture source hashes differ')
    require({p.name for p in root.glob('*.swift')} == {spec['swift']} and
            digest(regular(root, spec['swift']).read_bytes()) == value.get('generated_sha256'),
            'Additive generated Swift closure or bytes differ')
    if suite == 'cache-compatibility':
        require(all(value.get(key) is True for key in ('strict_compute_byte_identical', 'host_pipeline_byte_identical', 'execute_and_pruning_byte_identical', 'original_cache_key_byte_identical_except_symbol', 'original_cache_flow_except_retained_safety_boundaries_symbol_and_logs', 'copy_helper_overwrite_disabled', 'cache_components_lstat_no_follow', 'production_darwin_import_present', 'trusted_apps_root_canonicalized_once', 'alias_regression_uses_production_getter', 'alias_before_reference_byte_identical')),
                'Cache compatibility static proof differs')
    if suite == 'setup-connection-evidence':
        require(value.get('host_activation_message_contract') == 'exact_finite_guard_both_mirrors_before_generic_timeout' and
                value.get('host_activation_negative_static_mutations') == list(ACTIVATION_NEGATIVE_MUTATIONS),
                'Connection finite activation negative controls differ')
    if suite == 'profile-preconditions':
        require(value.get('check_count') == len(value.get('checks', [])) == 25 and
                len(set(value['checks'])) == 25, 'Profile static controls differ')
    return value


def additive_cases(output, contract, suite):
    if suite == 'host-refresh-bundle':
        # The source-owned executable emits one JSON document to stdout, without
        # accepting an output-path argument. Retain and verify that exact log.
        value = json.loads(regular(output, 'commands/host-refresh-bundle-run.log').read_text())
        require(value.get('schema') == ADDITIVE_SPECS[suite]['cases_schema'] and value.get('pass') is True,
                'Host bundle runtime did not pass')
        require(type(value.get('case_count')) is int and value['case_count'] == 30 and
                value.get('cases') == contract['case_names'][suite], 'Host bundle ordered case closure differs')
        for key in ('network_executed', 'provider_executed', 'device_executed'):
            require(value.get(key) is False, 'Forbidden host bundle runtime boundary: ' + key)
        return value
    value = exact_cases(regular(output, suite + '/cases.json'), ADDITIVE_SPECS[suite]['cases_schema'],
                        contract['case_names'][suite], network_executed=False, native_provider_executed=False)
    require(value['cases'] == contract['case_names'][suite], 'Additive ordered case closure differs')
    if suite == 'cache-compatibility':
        require(value.get('fake_only') is True and value.get('foundation_crypto_real') is True, 'Cache fixture boundary differs')
    return value


def all_evidence(output, contract):
    evidence = {'baseline': baseline_receipt(output, contract),
                'localization': localization_extraction(output, contract), 'variants': {}, 'cases': {}}
    evidence['cases']['setup-localization'] = exact_cases(
        regular(output, 'setup-localization/cases.json'), 'setup-localization-cases-v1',
        contract['case_names']['setup-localization'])
    early_static = json.loads(regular(output, 'early-refresh-boundaries/contract.json').read_text())
    require(early_static.get('schema') == 'early-refresh-portable-contract-v1' and
            early_static.get('status') == 'STATIC_PASS_RUNTIME_NOT_RUN' and
            early_static.get('check_count') == len(early_static.get('checks', [])) == 102 and
            early_static.get('expected_generic_baseline_cases') == 12 and
            early_static.get('variants') == ['baseline', 'candidate'] and
            early_static.get('swift_compiled') is False and early_static.get('swift_executed') is False,
            'Early-refresh static controls differ')
    portal_static = json.loads(regular(output, 'certificate-portal-evidence/static-controls.json').read_text())
    require(portal_static.get('schema') == 'portal-portability-controls-v1' and portal_static.get('status') == 'pass' and
            portal_static.get('check_count') == len(portal_static.get('checks', [])) == 6 and
            portal_static.get('swift_executed') is False and portal_static.get('network_executed') is False,
            'Portal static controls differ')
    evidence['static_controls'] = {'early': early_static, 'portal': portal_static}
    for suite in ('early-refresh-boundaries', 'certificate-portal-evidence'):
        for variant in ('baseline', 'candidate'):
            key = suite + '/' + variant
            evidence['variants'][key] = variant_extraction(output, contract, suite, variant)
            if suite == 'early-refresh-boundaries':
                evidence['cases'][key] = exact_cases(regular(output, key + '/cases.json'),
                    'early-refresh-cases-v1', contract['case_names'][suite], variant=variant,
                    network_executed=False, native_provider_executed=False)
            else:
                evidence['cases'][key] = exact_cases(regular(output, key + '/results.json'),
                    'portal-evidence-runtime-v1', contract['case_names'][suite], candidate=variant == 'candidate',
                    fake_only=True, network_executed=False, native_provider_executed=False)
    evidence['identity'] = identity_extraction(output, contract)
    evidence['cases']['remote-identity-reuse'] = exact_cases(
        regular(output, 'remote-identity-reuse/cases.json'), 'remote-identity-reuse-cases-v1',
        contract['case_names']['remote-identity-reuse'], fake_only=True,
        network_executed=False, native_provider_executed=False, native_keychain_executed=False)
    evidence['additive'] = {suite: additive_extraction(output, contract, suite) for suite in ADDITIVE_SPECS}
    for suite in ADDITIVE_SPECS:
        evidence['cases'][suite] = additive_cases(output, contract, suite)
    return evidence


def markers():
    result = {'localization-run': 'SETUP_LOCALIZATION_RENDERER_PASS cases=' + str(LOCALIZATION_CASE_COUNT),
              'connection-run': 'SETUP_CONNECTION_EVIDENCE_PASS cases=25',
              'vpn-recovery-run': 'VPN_REFRESH_RECOVERY_PASS cases=89',
              'identity-controls': 'OK',
              'cache-compatibility-run': 'CACHE_COMPATIBILITY_CASES_PASS',
              'identity-run': 'REMOTE_IDENTITY_REUSE_RUNTIME_PASS cases=' + str(IDENTITY_CASE_COUNT)}
    for variant in ('baseline', 'candidate'):
        result['early-' + variant + '-run'] = 'PASS early_refresh_actual_appmanager_pipeline_source_chain cases=24'
        result['portal-' + variant + '-run'] = 'PASS portal_service_' + variant + ' cases=19'
        result['portal-' + variant + '-host-run'] = 'PASS portal_json_ipc_copy cases=19'
    return result


def verify_receipt(path, contract_path=None):
    contract_path = contract_path or HERE / 'setup-state-recovery-contract.json'
    contract = load_contract(contract_path)
    report = json.loads(path.read_text())
    require(report.get('schema') == 'setup-state-recovery-run-v1' and report.get('status') == STATUS and
            report.get('suites') == list(SUITES), 'Setup-state fixtures did not pass')
    require(not report.get('source_failures'), 'Setup source generation failures remain')
    require(not report.get('runtime_failures') and not report.get('blocked_runtime_dependencies'),
            'Setup runtime failures or blocked dependencies remain')
    require(report.get('contract_sha256') == digest(contract_path.read_bytes()), 'Setup contract receipt differs')
    require(report.get('source_inputs_before') == contract['input_sha256'] and
            report.get('source_inputs_after') == contract['input_sha256'] and
            report.get('source_input_bytes_unchanged') is True, 'Setup input immutability missing')
    for key in ('network_executed', 'native_provider_executed', 'native_keychain_executed', 'app_executed', 'device_executed'):
        require(report.get(key) is False, 'Forbidden setup execution claim: ' + key)
    require(report.get('source_owned_fixtures_executed') is True, 'Setup source fixtures did not execute')
    commands = report.get('commands', [])
    require([r.get('name') for r in commands] == list(COMMANDS), 'Missing or repeated setup command')
    for row in commands:
        require(type(row.get('exit_code')) is int and row['exit_code'] == 0 and
                not row.get('timed_out') and not row.get('launch_error'), 'Setup command did not pass')
        if row['name'].endswith('-compile'):
            args = row.get('argv', [])
            expected = ['-swift-version', '5', '-parse-as-library']
            if row['name'] in ('host-refresh-bundle-compile', 'cache-compatibility-compile'):
                expected = ['-swift-version', '5', '-framework', 'CryptoKit']
            if row['name'].startswith('portal-') and '-host-' not in row['name']:
                expected += ['-module-name', 'SideSign']
            if row['name'] == 'identity-compile':
                expected += ['-framework', 'Security']
                require(len(args) == 1 + len(expected) + len(IDENTITY_SWIFT) + 2 and
                        args[1:1 + len(expected)] == expected and args[-2] == '-o' and
                        [Path(p).name for p in args[1 + len(expected):-2]] == list(IDENTITY_SWIFT),
                        'Identity compile-only argument closure differs')
            else:
                require(len(args) == len(expected) + 4 and args[1:1 + len(expected)] == expected and
                        args[-2] == '-o' and args[-3].endswith('.swift'),
                        'Compile-only source fixture argument closure differs')
        log = regular(path.parent, row['log']).read_bytes()
        require(digest(log) == row.get('stdout_sha256'), 'Setup command log changed')
        if row['name'] in markers():
            marker = markers()[row['name']]
            require(row.get('success_marker') == marker, 'Setup marker receipt differs')
            require_marker(log.decode('utf-8', errors='replace'), marker)
    require(report.get('evidence') == all_evidence(path.parent, contract), 'Setup evidence receipt differs')
    require(report.get('artifact_sha256') == artifact_inventory(path.parent), 'Setup retained artifact changed')
    return report


def run(sources, swiftc, output, contract_path=None):
    contract_path = contract_path or HERE / 'setup-state-recovery-contract.json'
    contract = load_contract(contract_path)
    require(not sources.is_symlink(), 'Symlink setup source root')
    sources = sources.resolve(strict=True)
    before = inventory(sources, contract)
    require(not output.exists() and not output.is_symlink() and
            not output.resolve().is_relative_to(sources), 'Setup output must be fresh and outside sources')
    require(swiftc.is_file(), 'Missing Swift compiler')
    swiftc = swiftc.resolve()
    output.mkdir(parents=True)
    output = output.resolve()
    runner = Runner(output)
    report = {'schema': 'setup-state-recovery-run-v1', 'status': 'FAILED', 'suites': [],
              'contract_sha256': digest(contract_path.read_bytes()), 'source_inputs_before': before,
              'source_inputs_after': {}, 'source_input_bytes_unchanged': False,
              'network_executed': False, 'native_provider_executed': False,
              'app_executed': False, 'device_executed': False, 'native_keychain_executed': False,
              'source_owned_fixtures_executed': False,
              'commands': runner.commands}
    def python(relative):
        require(relative in before, 'Setup command source not bound')
        return [sys.executable, '-B', regular(sources, relative)]
    def compile(name, file, binary, module=None):
        require(file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(output),
                'Unsafe generated setup source')
        args = [swiftc, '-swift-version', '5', '-parse-as-library']
        if name in ('host-refresh-bundle-compile', 'cache-compatibility-compile'):
            args = [swiftc, '-swift-version', '5', '-framework', 'CryptoKit']
        if module:
            require(module == 'SideSign', 'Unexpected setup fixture module')
            args += ['-module-name', module]
        runner.command(name, args + [file, '-o', binary], collect_compile_failure=True)
    runtime_failures, blocked_dependencies = [], []
    def runtime(name, argv):
        try:
            runner.command(name, argv, markers().get(name))
        except ValueError as error:
            # Runner retains the real exit/timeout/launch/log row before raising.
            # Keep marker failures too, without making a failed command successful.
            runtime_failures.append({'name': name, 'failure': str(error)})
            return False
        return True
    try:
        baseline = output / 'delivered-baseline'
        runner.command('delivered-baseline-extract', [sys.executable, '-B', Path(__file__).resolve(),
            'extract-baseline', '--sources', sources, '--contract', contract_path, '--output', baseline,
            '--receipt', output / 'delivered-baseline.json'])
        baseline_receipt(output, contract)
        localization = output / 'setup-localization'
        early = output / 'early-refresh-boundaries'
        portal = output / 'certificate-portal-evidence'
        identity = output / 'remote-identity-reuse'
        source_failures = []
        def generate(name, argv, validate, marker=None):
            try:
                runner.command(name, argv, marker)
                validate()
            except Exception as error:
                source_failures.append({'name': name, 'failure': str(error)})
        generate('localization-source', python(LOCALIZATION + '/test_setup_localization_contract.py') +
            ['--candidate-root', sources / 'LiveContainer', '--baseline-root', baseline / 'LiveContainer',
             '--output', localization], lambda: localization_extraction(output, contract))
        generate('early-source', python(EARLY + '/test_contract.py') +
            ['--candidate-root', sources, '--baseline-root', baseline, '--output', early],
            lambda: [variant_extraction(output, contract, 'early-refresh-boundaries', v) for v in ('baseline', 'candidate')])
        generate('portal-source', python(PORTAL + '/test_generator.py') +
            ['--candidate-root', sources, '--baseline-root', baseline, '--output', portal],
            lambda: [variant_extraction(output, contract, 'certificate-portal-evidence', v) for v in ('baseline', 'candidate')])
        generate('identity-controls', python(IDENTITY + '/test_generator.py'), lambda: None, markers()['identity-controls'])
        generate('identity-source', python(IDENTITY + '/generate_fixture.py') +
            ['--candidate-root', sources, '--baseline-root', baseline, '--output', identity],
            lambda: identity_extraction(output, contract))
        for suite, spec in ADDITIVE_SPECS.items():
            generate(spec['prefix'] + '-source', python(spec['folder'] + '/' + spec['generator']) +
                ['--candidate-root', sources / 'SideStore' if suite in ('host-refresh-bundle', 'cache-compatibility') else sources,
                 '--output', output / suite],
                lambda suite=suite: additive_extraction(output, contract, suite))
        if source_failures:
            report['source_failures'] = source_failures
            require(False, 'Setup source commands failed: ' + '; '.join(row['failure'] for row in source_failures))
        identity_files = [regular(identity, name) for name in IDENTITY_SWIFT]

        # The independent source families compile once each, retaining every compiler
        # failure. No fixture may run until the complete compile closure passes.
        compile('localization-compile', localization / 'fixture.swift', localization / 'fixture')
        for variant in ('baseline', 'candidate'):
            folder = early / variant
            compile('early-' + variant + '-compile', folder / 'early-refresh.swift', folder / 'fixture')
        for variant in ('baseline', 'candidate'):
            folder = portal / variant
            prefix = 'portal-' + variant
            compile(prefix + '-compile', folder / 'service.swift', folder / 'service', module='SideSign')
            compile(prefix + '-host-compile', folder / 'host.swift', folder / 'host')
        runner.command('identity-compile', [swiftc, '-swift-version', '5', '-parse-as-library',
            '-framework', 'Security', *identity_files, '-o', identity / 'fixture'], collect_compile_failure=True)
        for suite, spec in ADDITIVE_SPECS.items():
            folder = output / suite
            compile(spec['prefix'] + '-compile', folder / spec['swift'], folder / 'fixture')
        compiled = [row for row in runner.commands if row['name'] in COMPILE_COMMANDS]
        require([row['name'] for row in compiled] == list(COMPILE_COMMANDS), 'Setup compile closure differs')
        failed = [row['name'] for row in compiled if type(row['exit_code']) is not int or
                  row['exit_code'] != 0 or row.get('timed_out') or row.get('launch_error')]
        require(not failed, 'Setup compile commands failed: ' + ', '.join(failed))

        runtime('localization-run', [localization / 'fixture', localization])
        for variant in ('baseline', 'candidate'):
            folder = early / variant
            name = 'early-' + variant + '-run'
            runtime(name, [folder / 'fixture', folder / 'cases.json'])
        for variant in ('baseline', 'candidate'):
            folder = portal / variant
            prefix = 'portal-' + variant
            if runtime(prefix + '-run', [folder / 'service', folder / 'cases.plist']):
                runtime(prefix + '-host-run', [folder / 'host', folder / 'cases.plist', folder / 'results.json'])
            else:
                blocked_dependencies.append({'name': prefix + '-host-run', 'dependency': prefix + '-run',
                                             'reason': 'Service command or required success marker failed'})
        runtime('identity-run', [identity / 'fixture', '--cases-output', identity / 'cases.json'])
        for suite, spec in ADDITIVE_SPECS.items():
            folder = output / suite
            argv = [folder / 'fixture'] if suite == 'host-refresh-bundle' else [folder / 'fixture', folder / 'cases.json']
            runtime(spec['prefix'] + '-run', argv)
        if runtime_failures:
            report.update(runtime_failures=runtime_failures, blocked_runtime_dependencies=blocked_dependencies)
            require(False, 'Setup runtime commands failed: ' + '; '.join(
                row['name'] + ': ' + row['failure'] for row in runtime_failures))
        report['evidence'] = all_evidence(output, contract)
        require([r['name'] for r in runner.commands] == list(COMMANDS), 'Setup command order differs')
        report['source_inputs_after'] = inventory(sources, contract)
        require(report['source_inputs_after'] == before, 'Setup fixtures changed source inputs')
        report.update(source_input_bytes_unchanged=True, source_owned_fixtures_executed=True,
                      suites=list(SUITES), status=STATUS)
    except Exception as error:
        report['failure'] = str(error)
        raise
    finally:
        report['artifact_sha256'] = artifact_inventory(output)
        write_json(output / 'report.json', report)
    return verify_receipt(output / 'report.json', contract_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'extract-baseline', 'verify'])
    parser.add_argument('--sources', type=Path)
    parser.add_argument('--swiftc', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--contract', type=Path, default=HERE / 'setup-state-recovery-contract.json')
    args = parser.parse_args()
    if args.action == 'extract-baseline':
        extract_baseline(args.sources, load_contract(args.contract), args.output, args.receipt)
        print('EXACT_DELIVERED_SETUP_BASELINE_EXTRACTED_NO_NETWORK')
    elif args.action == 'verify':
        verify_receipt(args.receipt, args.contract)
        print(STATUS)
    else:
        run(args.sources, args.swiftc, args.output, args.contract)
        print(STATUS)


if __name__ == '__main__':
    main()
