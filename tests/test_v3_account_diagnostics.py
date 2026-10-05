"""Audit D01/D02/R02: exercise generated boundaries and the shipped wire/UI."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def declaration(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == '{':
            depth += 1
        elif source[index] == '}':
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError('unterminated declaration ' + signature)


def generated_sign_in():
    location = os.environ.get('EMBEDDED_SIDESTORE_TEST_SOURCE') or os.environ.get('SIDESTORE_TEST_SOURCE')
    if not location:
        raise unittest.SkipTest('Pinned SideStore source unavailable; CI supplies EMBEDDED_SIDESTORE_TEST_SOURCE')
    path = Path(location) / 'SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift'
    original = path.read_text(encoding='utf-8')
    service = load('patch_v3_service')
    source = service.patch_sign_in_operation(original)
    assert service.patch_sign_in_operation(source) == source
    source = service.apply_embedded_credential_snapshot_patch(source, 'patch_sign_in_operation')
    return service.headless_certificate_serial_log_redaction(source, 'SignInOperation')


def diagnostic_sources():
    runtime = (ROOT / 'scripts/templates/v3_headless_runtime.swift').read_text()
    classifier = runtime[runtime.index('enum V3AuthFailureKind:'):runtime.index('// MARK: - Provisioning failure guidance')]
    shell = (ROOT / 'scripts/templates/v3_unified_shell.swift').read_text()
    message = declaration(shell, '    static func failureMessage(from failure: [String: Any]) -> String {')
    message = message.replace('static func failureMessage', 'func hostFailureMessage')
    fixture = (ROOT / 'tests/fixtures/v3_auth_error_nuance_harness.swift').read_text()
    stub_errors = fixture[:fixture.index('@main')]
    return '\n'.join((
        (ROOT / 'scripts/templates/v3_wire_contract.swift').read_text(),
        (ROOT / 'scripts/templates/combined_failure.swift').read_text(),
        classifier, message,
        (ROOT / 'scripts/templates/v3_behavioral_primitives.swift').read_text(), stub_errors))


class AccountDiagnosticTests(unittest.TestCase):
    def execute(self, source):
        compiler = shutil.which('swiftc')
        if not compiler:
            self.skipTest('Swift compiler unavailable; account diagnostic harness runs in macOS CI')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'main.swift'
            binary = Path(temporary) / 'account-diagnostics'
            path.write_text(source)
            compiled = subprocess.run([compiler, '-parse-as-library', str(path), '-o', str(binary)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn('PASS', executed.stdout)

    def test_generated_boundaries_preserve_stage_and_fail_activation(self):
        source = generated_sign_in()
        loop = declaration(source, '    private func provisioningLoop(')
        for step, operation in (
            ('fetchTeams', 'self.fetchTeam('), ('saveAccount', 'self.saveTeamAndAccount(team)'),
            ('fetchCertificate', 'self.fetchCertificate('), ('activateCertificate', 'setActiveCertificate('),
            ('registerDevice', 'self.registerCurrentDevice(')):
            self.assertLess(loop.index('diagnosticStep = .' + step), loop.index(operation))
        self.assertIn('if resolvedCertificate == nil {', loop)
        self.assertIn('if diagnosticError.requiresReconciliation { throw diagnosticError }', loop)
        self.assertIn('resolveProvisioningError(diagnosticError)', loop)
        finalize = declaration(source, '    private func finalizeAuthentication(')
        self.assertIn('throw v3AccountOperationFailure(error, step: .activateAccount)', finalize)
        self.assertLess(finalize.index('throw v3AccountOperationFailure'), finalize.index('Database updates completed'))
        persistence = declaration(source, '    private func saveTeamAndAccount(')
        self.assertIn('await context.perform { context.rollback() }', persistence)
        self.assertLess(persistence.index('try context.save()'), persistence.index('UserDefaults.standard.activeAppsLimit'))
        silent = declaration(source, '    private func silentSignIn(')
        self.assertIn('if error is V3AccountOperationError { throw error }', silent)
        auth_loop = declaration(source, '    private func authenticationLoop(')
        self.assertLess(auth_loop.index('local.credentialCommit { throw local }'),
                        auth_loop.index('V3TwoFactorRetryPolicy.shouldReuseCredentialsForCodeRetry'))

    def test_typed_errors_round_trip_to_prompt_and_copy_diagnostics(self):
        self.execute(diagnostic_sources() + (ROOT / 'tests/fixtures/v3_account_diagnostics_harness.swift').read_text())

    def test_generated_commit_auth_loop_and_finalize_execute(self):
        source = generated_sign_in()
        commit_start = source.index('        // V3_AUTH_CREDENTIAL_TRANSACTION_V1')
        commit_end = source.index('        return (account, session)', commit_start)
        commit = source[commit_start:commit_end]
        auth = declaration(source, '    private func authenticationLoop(').replace('private func', 'func', 1)
        finalize = declaration(source, '    private func finalizeAuthentication(').replace('private func', 'func', 1)
        provisioning = declaration(source, '    private func provisioningLoop(').replace('private func', 'func', 1)
        harness = (ROOT / 'tests/fixtures/v3_account_boundaries_harness.swift').read_text()
        harness = harness.replace('// GENERATED_COMMIT', commit).replace('// GENERATED_AUTH_LOOP', auth)
        harness = harness.replace('// GENERATED_FINALIZE', finalize).replace('// GENERATED_PROVISIONING', provisioning)
        self.execute(diagnostic_sources() + harness)


if __name__ == '__main__':
    unittest.main()
