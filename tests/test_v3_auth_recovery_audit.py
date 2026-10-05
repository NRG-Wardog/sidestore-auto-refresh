"""D03-D05: execute the shipped store/button and generated provisioning path.

No recovery decision is restated by the harness. Production Swift is extracted
verbatim; only service IO, Apple IO, and platform rendering are substituted.
"""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def declaration(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 0
    in_string = False
    escaped = False
    for i in range(opening, len(source)):
        c = source[i]
        if in_string:
            if escaped: escaped = False
            elif c == '\\': escaped = True
            elif c == '"': in_string = False
            continue
        if c == '"': in_string = True
        elif c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: return source[start:i + 1]
    raise AssertionError(signature)


def template(name):
    return (ROOT / 'scripts/templates' / name).read_text()


def swift_source(*fragments):
    """Extracted declarations do not include trailing newlines."""
    return '\n\n'.join(fragment.rstrip('\n') for fragment in fragments) + '\n'


class AuditRecoveryTests(unittest.TestCase):
    def compile_and_run(self, source, marker):
        swift = shutil.which('swiftc')
        if not swift:
            self.skipTest('Swift unavailable; executable production harness runs in macOS CI')
        with tempfile.TemporaryDirectory() as temp:
            source_path = Path(temp) / 'main.swift'
            binary = Path(temp) / 'harness'
            source_path.write_text(source)
            compile_result = subprocess.run([swift, '-parse-as-library', str(source_path), '-o', str(binary)],
                                            capture_output=True, text=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(marker, result.stdout)

    def test_swift_fragments_are_separated_without_changing_declarations(self):
        first = 'struct First {}'
        second = '@MainActor\nfinal class Second {}'
        third = 'func third() {}\n'
        self.assertEqual(swift_source(first, second, third),
                         first + '\n\n' + second + '\n\nfunc third() {}\n')

    def test_production_store_reloads_and_real_button_starts_same_account_mode(self):
        shell = template('v3_unified_shell.swift')
        helpers = '\n'.join(declaration(shell, sig) for sig in (
            'private final class V3AuthReadinessSequenceStorage:',
            'enum V3AuthReadinessRefreshEvent {',
            'enum V3AuthRetryReadinessReconciliationPolicy {',
            'enum V3ProvisioningRetryReadinessPolicy {',
            'struct V3ProvisioningRetryReadinessOwnership {',
            'enum V3ProvisioningRetryReadinessSettlement:',
        ))
        store = '@MainActor\n' + declaration(shell, 'final class V3AuthStore:')
        branch = declaration(shell, 'if recovery.showReauthenticateProvisioning {')
        harness = (ROOT / 'tests/fixtures/v3_auth_recovery_store_harness.swift').read_text()
        harness = harness.replace('__PRODUCTION_BUTTON_BRANCH__', branch)
        self.compile_and_run(swift_source('import Combine', template('combined_failure.swift'),
            template('v3_wire_contract.swift'), template('v3_behavioral_primitives.swift'),
            helpers, store, harness), 'V3_AUTH_RECOVERY_STORE_PASS')

    def test_production_completion_evidence_cannot_be_inferred_from_rows(self):
        primitives = template('v3_behavioral_primitives.swift')
        production = '\n'.join(declaration(primitives, sig) for sig in (
            'enum V3AuthIdentityBindingPolicy {', 'enum V3AuthReadStampPolicy {',
            'struct V3ProvisioningCompletionState {',
            'enum V3ProvisioningReauthenticationIdentityPolicy {',
        ))
        harness = (ROOT / 'tests/fixtures/v3_provisioning_completion_harness.swift').read_text()
        self.compile_and_run(swift_source('import Foundation', production, harness),
                             'V3_PROVISIONING_COMPLETION_PASS')

    def test_generation_bypasses_cached_and_silent_paths_and_checks_owner_before_commit(self):
        path = Path(os.environ.get('EMBEDDED_SIDESTORE_TEST_SOURCE', '/workspace/shared/sidestore-review/SideStore'))
        pinned = path / 'SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift'
        if not pinned.is_file():
            self.skipTest('Pinned SideStore unavailable; set EMBEDDED_SIDESTORE_TEST_SOURCE')
        spec = importlib.util.spec_from_file_location('audit_recovery_patch', ROOT / 'scripts/patch_v3_service.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = module.patch_sign_in_operation(pinned.read_text())
        self.assertEqual(module.patch_sign_in_operation(source), source)
        sign_in = declaration(source, 'private func signIn(appleID: String, password: String)')
        self.assertLess(sign_in.index('v3ValidateReauthenticationIdentity'), sign_in.index('AuthManager.shared.signIn'))
        self.assertLess(sign_in.index('returnedAppleID: account.appleID'), sign_in.index('writeAuthenticationCredentials'))
        self.assertLess(sign_in.index('self.isCancelled || Task.isCancelled'), sign_in.index('writeAuthenticationCredentials'))
        self.assertLess(sign_in.index('AuthManager.shared.signIn'), sign_in.index('self.isCancelled || Task.isCancelled'))
        methods = '\n'.join(declaration(source, sig) for sig in (
            'override func execute(parentProgress:',
            'private func startAuthentication(reportProgress:',
            'private func provisioningLoop(account:',
            'private func v3ValidateReauthenticationIdentity(',
        ))
        harness = (ROOT / 'tests/fixtures/v3_reauthentication_operation_harness.swift').read_text()
        harness = harness.replace('__PRODUCTION_OPERATION_METHODS__', methods)
        primitives = template('v3_behavioral_primitives.swift')
        policies = '\n'.join(declaration(primitives, sig) for sig in (
            'enum V3AuthIdentityBindingPolicy {', 'enum V3AuthReadStampPolicy {',
            'enum V3ProvisioningReauthenticationIdentityPolicy {',
            'enum V3ProvisioningResumeExecutionPolicy {',
        ))
        self.compile_and_run(swift_source('import Foundation\nimport CoreFoundation', policies, harness),
                             'V3_REAUTHENTICATION_OPERATION_PASS')

    def test_actual_snapshot_reports_unknown_and_failed_provisioning_with_existing_account(self):
        primitives = template('v3_behavioral_primitives.swift')
        production = '\n'.join(declaration(primitives, sig) for sig in (
            'enum V3AuthIdentityBindingPolicy {', 'struct V3ProvisioningCompletionState {',
        ))
        snapshot = declaration(template('v3_sidestore_service.swift'), 'private func snapshot()')
        harness = (ROOT / 'tests/fixtures/v3_provisioning_snapshot_harness.swift').read_text()
        binding = declaration(template('v3_headless_runtime.swift'), 'func v3ProvisioningCompletionBinding(')
        self.compile_and_run(swift_source('import Foundation\nimport CryptoKit', production, binding,
            harness.replace('__PRODUCTION_SNAPSHOT__', snapshot)), 'V3_PROVISIONING_SNAPSHOT_PASS')

    def test_snapshot_and_runtime_use_completion_and_preserve_mutation_admission(self):
        service = template('v3_sidestore_service.swift')
        snapshot = declaration(service, 'private func snapshot()')
        self.assertIn('provisioningCompletion.status(', snapshot)
        self.assertNotIn('authenticated && activeAccount == nil', snapshot)
        self.assertIn('!activeMutation && operationRecovery == nil && directRecoveryRecord == nil && !recoveryJournalUnreadable', snapshot)
        runtime = template('v3_headless_runtime.swift')
        run = declaration(runtime, 'func run(id: String)')
        self.assertIn('fullProvisioningCompleted: operation.v3DidCompleteProvisioning', run)
        self.assertLess(run.index('try await operation.execute()'), run.index('provisioningCompletion.complete('))
        self.assertIn('ownsActiveSession(id)', run)
        self.assertIn('Keychain.shared.storageRequiresReconciliation()', runtime)
        bridge = template('v3_service_bridge.swift')
        request = declaration(bridge, 'public func request(operation: String,')
        balanced = declaration(request, 'defer {')
        self.assertIn('operation == "authReconcileStorage"', balanced)
        self.assertIn('V3AuthIdentityTransitionFinished', balanced)


if __name__ == '__main__':
    unittest.main()
