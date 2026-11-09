from tests.support.auth_diagnostic_owner import diagnostic_error_types, operation_error_cases, provisioning_diagnostic_helpers
"""Audit D01/D02/R02: exercise generated boundaries and the shipped wire/UI."""
from tests.support.product_sources import ProductSource
from tests.owner_source import owner_source
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


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
    return owner_source("SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift", "SideStore")


def diagnostic_sources():
    runtime = ProductSource('v3_headless_runtime.swift').read_text()
    classifier = runtime[runtime.index('enum V3AuthFailureKind:'):runtime.index('// MARK: - Provisioning failure guidance')]
    shell = ProductSource('v3_unified_shell.swift').read_text()
    message = declaration(shell, '    static func failureMessage(from failure: [String: Any]) -> String {')
    message = message.replace('static func failureMessage', 'func hostFailureMessage')
    fixture = (ROOT / 'tests/fixtures/v3_auth_error_nuance_harness.swift').read_text()
    stub_errors = '\n'.join((diagnostic_error_types(), operation_error_cases(), provisioning_diagnostic_helpers()))
    return '\n'.join((
        ProductSource('v3_wire_contract.swift').read_text(),
        ProductSource('combined_failure.swift').read_text(),
        classifier, declaration(runtime, 'enum V3AuthFailureDisplay {'), message,
        ProductSource('v3_behavioral_primitives.swift').read_text(), stub_errors))


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

    def test_anisette_pair_failures_keep_finite_shared_definition_and_blocked_guidance(self):
        common = ProductSource('combined_failure.swift').read_text()
        runtime = ProductSource('v3_headless_runtime.swift').read_text()
        shell = ProductSource('v3_unified_shell.swift').read_text()
        primitives = ProductSource('v3_behavioral_primitives.swift').read_text()
        error = declaration(common, 'enum LCAnisettePairError:')
        self.assertIn('Error, LocalizedError, Equatable', error)
        cases = re.findall(r'\bcase\s+([^\n]+)', error)
        self.assertEqual(cases, ['orphanedBlob, invalidIdentifier, invalidBlob, migrationPairConflict, stateChanged'])
        self.assertLess(common.index('enum LCAnisettePairError:'), common.index('struct V3AuthenticationPhaseError:'))
        sources = diagnostic_sources()
        self.assertEqual(sources.count('enum LCAnisettePairError:'), 1)
        self.assertIn('if error is LCAnisettePairError { kind = .anisetteIdentityStateInvalid }', runtime)
        self.assertIn('if v3AccountUnderlyingError(error) is LCAnisettePairError { return .anisetteIdentityStateInvalid }', runtime)
        capture = declaration(common, '    public static func capture(')
        self.assertIn('if error is LCAnisettePairError', capture)
        self.assertLess(capture.index('if error is LCAnisettePairError'), capture.index('var cause = error as NSError'))
        account_error = declaration(common, 'struct V3AccountOperationError:')
        self.assertIn('kind == .anisetteIdentityStateInvalid || kind.isRemoteIdentity ? false : nil', account_error)
        self.assertIn('step == .credentialCommit && kind != .anisetteIdentityStateInvalid', account_error)
        self.assertIn('sourceStep: kind == .anisetteIdentityStateInvalid || kind.isRemoteIdentity ? .anisetteFetch : step', account_error)
        for text, anchor in ((runtime, 'enum V3AuthFailureDisplay {'),
                             (shell, '    static func failureMessage(from failure: [String: Any]) -> String {')):
            self.assertIn('case "anisetteIdentityStateInvalid": return LCAnisettePairError.safeMessage', declaration(text, anchor))
        policy = declaration(primitives, 'enum V3AuthTerminalFailureActionPolicy {')
        self.assertIn('"anisetteIdentityStateInvalid": return .blocked', policy)
        self.assertIn('if kind == "anisetteIdentityStateInvalid" { return LCAnisettePairError.recovery }', policy)
        self.assertIn('Keep the existing Anisette and account data unchanged', error)
        for misleading in ('password', 'internet', 'server', 'reset', 'delete', 'Apple authentication succeeded'):
            self.assertNotIn(misleading, error)

    def test_pair_failure_blocks_authenticated_provisioning_retry_and_reauthentication(self):
        runtime = ProductSource('v3_headless_runtime.swift').read_text()
        shell = ProductSource('v3_unified_shell.swift').read_text()
        primitives = ProductSource('v3_behavioral_primitives.swift').read_text()
        post_auth = declaration(runtime, '    private func runLoginPipeline(id: String) async').split(
            'if authenticatedOutcome == "authenticatedProvisioningIncomplete" {', 1)[1].split('} else if cancelled {', 1)[0]
        self.assertIn('let anisetteIdentityStateInvalid = failure.signingContext["typed_error"] == "anisetteIdentityStateInvalid"', post_auth)
        self.assertIn('provisioningRecoveryRequiresReconciliation || anisetteIdentityStateInvalid', post_auth)
        self.assertIn('!provisioningRecoveryRequiresReconciliation && !anisetteIdentityStateInvalid', post_auth)
        self.assertIn('LCAnisettePairError.safeMessage + " " + LCAnisettePairError.recovery', post_auth)
        self.assertIn('failureWire["kind"] = "anisetteIdentityStateInvalid"', post_auth)
        for signature in ('    var provisioningRecoveryActions:', '    var canRetryProvisioning:'):
            self.assertIn('identityStateBlocked: provisioningIdentityStateBlocked', declaration(shell, signature))
        self.assertIn('provisioningIdentityStateBlocked = false', declaration(shell, '    private func clearProvisioningOutcome()'))
        self.assertIn('!provisioningRetryAvailable && !provisioningIdentityStateBlocked', shell)
        reconcile = declaration(shell, '    func reconcile(force:')
        self.assertIn('if provisioningIdentityStateBlocked {', reconcile)
        preserved = reconcile.split('if provisioningIdentityStateBlocked {', 1)[1].split('} else if', 1)[0]
        self.assertIn('provisioningRetryAvailable = false', preserved)
        self.assertNotIn('provisioningMessage =', preserved)
        policy = declaration(primitives, 'enum V3AuthProvisioningRecoveryPolicy {')
        self.assertIn('let retryAllowed = !identityStateBlocked', policy)
        self.assertIn('showReauthenticateProvisioning: !identityStateBlocked', policy)

    def test_readiness_vocabulary_matches_production_failure_enums(self):
        # Run this contract check on Linux too; missing wire vocabulary must not
        # wait for a macOS executable harness to be detected.
        failure = ProductSource('combined_failure.swift').read_text()
        wire = ProductSource('v3_wire_contract.swift').read_text()
        for name, boundary, allowlist in (
            ('SafeCause', 'fileprivate var inferredRetryable', 'knownSafeCauseValues'),
            ('SourceStep', 'var portalUserLabel', 'knownSourceStepValues')):
            enum = declaration(failure, 'public enum ' + name + ':').split(boundary)[0]
            cases = set()
            for line in re.findall(r'\bcase\s+([A-Za-z]\w*(?:\s*,\s*[A-Za-z]\w*)*)', enum):
                cases.update(value.strip() for value in line.split(',') if value.strip())
            values = wire.split('static let ' + allowlist + ': Set<String> = [', 1)[1].split(']', 1)[0]
            allowed = set(re.findall(r'"([^"\n]+)"', values))
            if name == 'SafeCause':
                # Readiness is a narrower protocol than command failure decode.
                # The dedicated real-decoder suite verifies rejection/acceptance.
                self.assertLessEqual(allowed, cases)
                self.assertEqual(cases - allowed, {
                    'directRecoveryRecordMissing', 'directRecoveryTargetMismatch',
                    'directRecoveryModeInvalid', 'directRecoveryPhaseNotEligible'})
            else:
                self.assertLessEqual(allowed, cases)
                self.assertEqual(cases - allowed, {
                    'refreshRecordUpdate', 'certificateSelection', 'provisioningProfileInstallation',
                    'refreshReadiness', 'refreshPreflight', 'refreshMetadataCache', 'refreshApplication'})
        self.assertIn('"sourceStep", "signingContext"])),', wire)

    def test_generated_boundaries_preserve_stage_and_fail_activation(self):
        source = generated_sign_in()
        anisette = declaration(source, '    private func getAnisetteData(')
        self.assertIn('v3AuthenticationPhase(.anisetteFetch)', anisette)
        wrapper = declaration(source, '    private func signIn(')
        self.assertIn('try await self.performSignIn(appleID: appleID, password: password,', wrapper)
        self.assertIn('try await self.v3WithOwnedODA', wrapper)
        signin = declaration(source, '    private func performSignIn(')
        self.assertIn('v3AuthenticationPhase(.appleAuthentication)', signin)
        silent = declaration(source, '    private func silentSignIn(')
        self.assertIn('v3AuthenticationPhase(.accountLookup)', silent)
        loop_wrapper = declaration(source, '    private func provisioningLoop(')
        self.assertIn('try await self.performProvisioningLoop(account: account, session: session, reportProgress: reportProgress)', loop_wrapper)
        self.assertIn('try await self.v3WithOwnedODA', loop_wrapper)
        loop = declaration(source, '    private func performProvisioningLoop(')
        for step, operation in (
            ('fetchTeams', 'self.fetchTeam('), ('saveAccount', 'self.saveTeamAndAccount(team)'),
            ('fetchCertificate', 'self.fetchCertificate('), ('activateCertificate', 'setActiveCertificate('),
            ('registerDevice', 'self.registerCurrentDevice(')):
            self.assertLess(loop.index('diagnosticStep = .' + step), loop.index(operation))
        self.assertIn('if resolvedCertificate == nil {', loop)
        self.assertIn('if diagnosticError.requiresReconciliation || diagnosticError.portalSessionRejected { throw diagnosticError }', loop)
        self.assertIn('resolveProvisioningError(diagnosticError)', loop)
        finalize_wrapper = declaration(source, '    private func finalizeAuthentication(')
        self.assertIn('performFinalizeAuthentication(result: result)', finalize_wrapper)
        self.assertIn('v3WithOwnedODA', finalize_wrapper)
        finalize = declaration(source, '    private func performFinalizeAuthentication(')
        self.assertIn('throw v3AccountOperationFailure(error, step: .activateAccount)', finalize)
        self.assertLess(finalize.index('throw v3AccountOperationFailure'), finalize.index('Database updates completed'))
        persistence_wrapper = declaration(source, '    private func saveTeamAndAccount(')
        self.assertIn('self.performSaveTeamAndAccount(altTeam, makeActive: makeActive)', persistence_wrapper)
        persistence = declaration(source, '    private func performSaveTeamAndAccount(')
        self.assertIn('await context.perform { context.rollback() }', persistence)
        self.assertLess(persistence.index('try context.save()'), persistence.index('UserDefaults.standard.activeAppsLimit'))
        silent = declaration(source, '    private func silentSignIn(')
        self.assertIn('if error is V3AccountOperationError { throw error }', silent)
        auth_loop = declaration(source, '    private func authenticationLoop(')
        self.assertLess(auth_loop.index('local.credentialCommit { throw local }'),
                        auth_loop.index('V3TwoFactorRetryPolicy.shouldReuseCredentialsForCodeRetry'))

    def test_rejected_team_session_offers_reauthentication_not_same_token_retry(self):
        runtime = ProductSource('v3_headless_runtime.swift').read_text()
        handler = declaration(runtime, '    func resolveProvisioningError(')
        rejected = handler.split('if diagnosticError.portalSessionRejected {', 1)[1].split('if diagnosticError.requiresReconciliation', 1)[0]
        self.assertIn('error: diagnosticError, mayRetry: false', rejected)
        self.assertIn('Sign In Again to Finish Setup', rejected)
        self.assertIn('before certificate setup', rejected)
        self.assertNotIn('password', rejected.lower())
        self.assertIn('if resumeUnavailable || portalSessionRejected || provisioningRecoveryRequiresReconciliation', runtime)
        self.assertIn('!resumeUnavailable && !portalSessionRejected && !provisioningRecoveryRequiresReconciliation', runtime)
        retirement = runtime.split('if portalSessionRejected {', 1)[1].split('let postAuthentication', 1)[0]
        self.assertIn('AuthManager.shared.v3ReplaceSession(nil)', retirement)
        expiry = declaration(runtime, '    func expire(id: String)')
        self.assertIn('session.rejectedPortalSessionFailure == nil', expiry)
        self.assertIn('response["resumable"] = false', expiry)
        self.assertIn('response["technicalDetails"] = failure.technicalDetails', expiry)
        self.assertIn('center.sessions[sessionID]?.rejectedPortalSessionFailure =', rejected)
        for forbidden in ('signOut(', 'clearSignInInfo', 'clearActiveCertificate', 'revoke'):
            self.assertNotIn(forbidden, retirement)

    def test_terminal_routes_capture_owned_phase_evidence(self):
        runtime = ProductSource('v3_headless_runtime.swift').read_text()
        run = declaration(runtime, '    private func runLoginPipeline(id: String) async')
        self.assertEqual(run.count('v3CaptureAuthFailure(error, operation: "signIn"'), 2)
        self.assertNotIn('CombinedFailure.capture(error, operation: "signIn"', run)
        source = generated_sign_in()
        wrapper = declaration(source, '    override func execute(')
        self.assertIn('try await self.executeLoginPipeline(parentProgress: parentProgress)', wrapper)
        execute = declaration(source, '    private func executeLoginPipeline(')
        self.assertIn('session.anisetteData = try await self.getAnisetteData()', execute)
        self.assertIn('if self.v3ForceProvisioningRetry', execute)
        # Both paths use the generated owned Anisette boundary; their escaped
        # failures are handled by the production terminal capture helper below.
        self.assertEqual(execute.count('self.getAnisetteData()'), 2)

    def test_typed_errors_round_trip_to_prompt_and_copy_diagnostics(self):
        self.execute(diagnostic_sources() + (ROOT / 'tests/fixtures/v3_account_diagnostics_harness.swift').read_text())

    def test_native_adi_evidence_round_trips_through_terminal_capture_and_copy(self):
        self.execute(diagnostic_sources() + (ROOT / 'tests/fixtures/v3_native_adi_evidence_harness.swift').read_text())

    def test_native_adi_evidence_is_typed_and_diagnostic_only(self):
        common = ProductSource('combined_failure.swift').read_text()
        runtime = ProductSource('v3_headless_runtime.swift').read_text()
        adapter = declaration(runtime, 'func v3AccountOperationFailure(')
        self.assertIn('case .adiError(let code, let description):', adapter)
        self.assertIn('nativeEvidence = .capture(code: code, description: description)', adapter)
        capture = declaration(common, 'struct V3AnisetteNativeEvidence {')
        self.assertIn('1f5a7e36553cc865b873f222b87a6486c0bcc7bf', common)
        self.assertIn('String(subcode) == tail', capture)
        decoder = declaration(capture, '    static func capture(')
        self.assertNotIn('.contains(', decoder)
        self.assertNotIn('.hasPrefix(', decoder)
        for mutation in ('UserDefaults', 'FileManager', 'Keychain', 'retry', 'reset', 'removeItem'):
            self.assertNotIn(mutation, capture)
        validator = declaration(common, '    public static func validatedSigningContext(')
        self.assertIn('Int32(text).map({ String($0) }) == text', validator)
        self.assertIn('V3AnisetteNativeEvidence.Phase(rawValue: text) != nil', validator)
        # Both existing terminal routes already call this production capture;
        # no retry, classification, credential or provider behavior is changed.
        run = declaration(runtime, '    private func runLoginPipeline(id: String) async')
        self.assertEqual(run.count('v3CaptureAuthFailure(error, operation: "signIn"'), 2)

    def test_generated_commit_auth_loop_and_finalize_execute(self):
        source = generated_sign_in()
        # Keep the complete post-auth commit (including verified legacy recovery)
        # in its real generated method boundary, with every dependency supplied.
        sign_in = declaration(source, '    private func performSignIn(')
        commit_start = sign_in.index('        // V3_AUTH_CREDENTIAL_TRANSACTION_V1')
        commit_end = sign_in.index('        return (account, session)', commit_start)
        commit = sign_in[commit_start:commit_end]
        auth = declaration(source, '    private func authenticationLoop(').replace('private func', 'func', 1)
        finalize = (declaration(source, '    private func finalizeAuthentication(') + '\n' +
                    declaration(source, '    private func performFinalizeAuthentication(')).replace('private func', 'func')
        provisioning = (declaration(source, '    private func provisioningLoop(') + '\n' +
                        declaration(source, '    private func performProvisioningLoop(')).replace('private func', 'func')
        harness = (ROOT / 'tests/fixtures/v3_account_boundaries_harness.swift').read_text()
        harness = harness.replace('// GENERATED_COMMIT', commit).replace('// GENERATED_AUTH_LOOP', auth)
        harness = harness.replace('// GENERATED_FINALIZE', finalize).replace('// GENERATED_PROVISIONING', provisioning)
        keychain = ProductSource('embedded_shared_keychain.swift').read_text()
        identity_types = '\n'.join(declaration(keychain, signature) for signature in (
            'struct LCEmbeddedAuthenticationSnapshot:', 'struct LCEmbeddedAuthenticationCandidate:'))
        from tests.support import local_signin_owner_runtime as local_runtime
        # Reuse the local-route dependency closure, but the diagnostic classifier
        # already supplies the actual remote error and owned-context IO shape.
        # Do not redeclare those types or substitute classifier behavior.
        dependencies = local_runtime.dependencies()
        for duplicate in (
            'typealias OwnedODAAuthenticationContext = FixtureAttemptContext\n',
            'enum ILoaderRemoteError: Error { case consentRequired }\n',
        ):
            self.assertEqual(dependencies.count(duplicate), 1)
            dependencies = dependencies.replace(duplicate, '')
        dependencies = dependencies.replace(
            'enum OwnedODAAuthentication { @TaskLocal static var current: FixtureAttemptContext? }',
            'enum OwnedODAAuthentication { @TaskLocal static var current: OwnedODAAuthenticationContext? }')
        dependencies = dependencies.replace(
            'final class FixtureOwnedAttempt {\n    let context = FixtureAttemptContext()',
            'final class FixtureOwnedAttempt {\n    let context = OwnedODAAuthenticationContext()')
        dependencies = dependencies.replace(
            'credentialRoute, provisioning }', 'credentialRoute, provisioning, accountFinalize, cleanup }')
        # These contexts are outside the ordinary-route scenarios. Any accidental
        # transition into their IO must fail, not manufacture a successful proof.
        dependencies += '''
extension OwnedODAAuthenticationContext {
    func invalidate() { fatalError("unexpected owned authentication invalidation") }
    func stagedBlobForCommit() throws -> Data? { fatalError("unexpected owned authentication blob") }
}
'''
        operation_errors = owner_source('SideStore/Core/Operations/Errors/OperationError.swift', 'SideStore')
        keychain_source = owner_source('AltStore/Core/Components/Keychain.swift', 'SideStore')
        dependencies += '\n'.join((
            'extension OperationError {\n' + declaration(operation_errors, '    public static var cancelled:') + '\n}',
            declaration(keychain_source, 'enum V3PendingCertificateCreationError:'),
        ))
        harness = harness.replace('// CURRENT_LOCAL_ROUTE_MEMBERS', local_runtime.members())
        diagnostic = diagnostic_sources()
        self.assertEqual(diagnostic.count('enum V3DeviceSetupDeferralPolicy {'), 1)
        self.assertEqual(diagnostic.count('struct V3ProvisioningReauthenticationIdentityError:'), 1)
        self.execute('\n'.join((diagnostic, identity_types, dependencies, harness)))


if __name__ == '__main__':
    unittest.main()
