"""Execute production host state helpers for cancellation and stale-result races."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWIFTC = shutil.which("swiftc")


class V3HostStateTests(unittest.TestCase):
    def test_auth_readiness_events_are_consumed_by_long_lived_root_owner(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        root = shell[shell.index("struct V3UnifiedTabs: View"):
                     shell.index("final class V3SideStoreStatusStore")]
        self.assertIn(".onReceive(NotificationCenter.default.publisher(for: V3AuthReadinessRefreshEvent.notificationName))", root)
        self.assertIn("V3AuthReadinessRefreshEvent.sessionID(from: notification)", root)
        self.assertIn("V3AuthReadinessRefreshEvent.attemptSequence(from: notification)", root)
        self.assertIn("status.refreshSetupFactsAfterAuthentication(sessionID: sessionID,", root)
        self.assertIn("attemptSequence: attemptSequence)", root)
        status = shell[shell.index("final class V3SideStoreStatusStore"):
                       shell.index("\nstruct V3SideStoreApp")]
        refresh_method = status[status.index("func refreshSetupFactsAfterAuthentication(sessionID: String, attemptSequence: UInt64) {"):]
        refresh_method = refresh_method[:refresh_method.index("\n    }")]
        self.assertLess(refresh_method.index("authReadinessRefreshEventLedger.claim"),
                        refresh_method.index("invalidateSetupFacts()"))
        self.assertIn("invalidateSetupFacts()", refresh_method)
        self.assertIn("setupFactObservation = .deferred", refresh_method)
        self.assertIn("startSetupFactObservation()", refresh_method)
        self.assertIn("awaitAuthReadinessRefresh()", status)
        self.assertIn("awaitSharedSetupJITLessReadiness()", status)
        sign_in_start = shell.index("struct V3SignInView: View")
        sign_in_end = shell.index("\nstruct V3CertificateRow", sign_in_start)
        sign_in = shell[sign_in_start:sign_in_end]
        self.assertIn("V3AuthReadinessRefreshEvent.post(sessionID: readinessEventSessionID,", shell)
        self.assertNotIn("refreshSetupFactsAfterAuthentication", sign_in)
        self.assertNotIn("successfulProvisioningRetryRevision", sign_in)
        self.assertIn('URL(string: "livecontainer://jitless-setup")', sign_in,
                      "canonical LiveContainer setup routing must remain in place")

    def test_health_and_setup_consume_one_shared_jitless_fact(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        setup_store_start = shell.index("final class V3SetupStore")
        setup_store_end = shell.index("\nstruct V3SetupAssistantView", setup_store_start)
        setup_store = shell[setup_store_start:setup_store_end]
        recalculate_start = setup_store.index("func recalculate(status: V3SideStoreStatusStore) async {")
        recalculate_end = setup_store.index("    func buildDiagnostics", recalculate_start)
        recalculate = setup_store[recalculate_start:recalculate_end]
        self.assertLess(recalculate.index("await status.awaitSharedSetupJITLessReadiness()"),
                        recalculate.index("status.beginSetupFactObservation()"))
        self.assertIn("V3SetupReadinessObservationPolicy.shouldFetchLocalReadiness(sharedReadiness)", recalculate)
        self.assertIn('request(operation: "healthSnapshot")', recalculate)
        self.assertIn("status.jitlessActiveCertificateAvailable", recalculate)

        setup_view = shell[setup_store_end:shell.index("// V3_UNIFIED_SHELL_V1_END")]
        self.assertIn(".onChange(of: status.jitlessReadiness)", setup_view)
        self.assertIn("Task { await setup.recalculate(status: status) }", setup_view)

        health_start = shell.index("struct V3HealthView: View")
        health_end = shell.index("\nstruct V3BackupsView", health_start)
        health = shell[health_start:health_end]
        self.assertIn("private var jitlessReadiness: V3JITLessReadiness {\n        status.jitlessReadiness ?? .unknown", health)
        self.assertIn("private var activeCertificateAvailable: Bool {\n        status.jitlessActiveCertificateAvailable ?? false", health)
        self.assertNotIn("@State private var jitlessReadiness", health)
        self.assertNotIn("jitlessReadiness = readiness.readiness", health)
        self.assertIn("status.recordJITLessReadiness(readiness.readiness", health)

    def test_auth_terminal_posts_once_and_retry_refresh_does_not_duplicate_account_reload(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        start = shell.index("struct V3SignInView: View")
        end = shell.index("\nstruct V3CertificateRow", start)
        sign_in = shell[start:end]
        self.assertNotIn("onChange(of: auth.successfulProvisioningRetryRevision)", sign_in)
        auth_reload = sign_in[sign_in.index(".onChange(of: auth.isSignedIn)"):]
        auth_reload = auth_reload[:auth_reload.index(".onDisappear")]
        self.assertIn("guard isSignedIn else { return }", auth_reload)
        self.assertIn("status.reload()", auth_reload,
                      "the sign-in view still reloads account status")
        self.assertNotIn("invalidateSetupFacts()", auth_reload)
        self.assertNotIn("refreshSetupFactsAfterAuthentication", auth_reload,
                         "the long-lived root is the only readiness refresh consumer")

        auth_store = shell[shell.index("final class V3AuthStore"):]
        auth_store = auth_store[:auth_store.index("\nstruct V3SignInLink")]
        self.assertIn("private var pendingAuthenticationReadinessSessionID: String?", auth_store)
        self.assertIn("private var provisioningRetryReadinessOwnership = V3ProvisioningRetryReadinessOwnership()", auth_store)
        self.assertIn("provisioningRetryReadinessOwnership.begin(sessionID: requestedSession)", auth_store)
        self.assertNotIn("provisioningRetryInProgress", auth_store,
                         "retry identity is session-owned, not a task-scoped shared flag")
        apply = auth_store[auth_store.index("private func apply(_ reply: [String: Any])"):
                           auth_store.index("func clearPreviousFailure()")]
        self.assertIn("provisioningRetryReadinessOwnership.settle(", apply)
        self.assertIn("retryReadinessSettlement == .committed", apply)
        self.assertIn("retryReadinessSettlement == .notRetry && terminalAuthenticationSucceeded", apply)
        self.assertIn("belongsToCurrentOrReconciledRetry, terminalAuthenticationSucceeded", apply)
        self.assertIn("V3AuthReadinessRefreshEvent.post(sessionID: readinessEventSessionID,", apply)
        self.assertIn("pendingAuthenticationReadinessSessionID = requestedSession", auth_store)
        self.assertIn("pendingAuthenticationReadinessAttemptSequence = V3AuthReadinessRefreshEvent.nextAttemptSequence()", auth_store)
        self.assertIn("provisioningRetryReadinessAttemptSequence = V3AuthReadinessRefreshEvent.nextAttemptSequence()", auth_store)
        self.assertIn("pendingAuthenticationReadinessSessionID = nil", apply)
        reconcile = auth_store[auth_store.index("func reconcile(force:"):auth_store.index("private func resolveUnavailableAuthSession")]
        self.assertIn("pendingReadinessSessionID = pendingAuthenticationReadinessSessionID", reconcile)
        self.assertIn("V3AuthReadinessRefreshEvent.post(sessionID: pendingReadinessSessionID,", reconcile)
        self.assertIn("V3AuthRetryReadinessReconciliationPolicy.shouldPublish", reconcile)
        self.assertIn("reconciledProvisioningRetryReadinessSessionID = retrySessionID", reconcile)
        self.assertIn("attemptSequence: retryAttemptSequence", reconcile)
        self.assertIn("pendingAuthenticationReadinessSessionID = nil", reconcile)
        self.assertNotIn("successfulProvisioningRetryRevision", auth_store)
        self.assertIn("handoffAfterSupersededPollFailure(sessionID: sessionID)", auth_store)
        self.assertIn("provisioningRetryReadinessOwnership.owns(sessionID: sessionID)", auth_store)
        self.assertIn("provisioningRetryReadinessOwnership.allowsPromptResponse(sessionID: session)", auth_store)
        self.assertIn("mutating func begin(sessionID: String)", shell)
        self.assertIn("private(set) var highestConsumedAttemptSequence: UInt64 = 0", shell)
        self.assertIn("attemptSequence > highestConsumedAttemptSequence", shell)
        self.assertIn("mutating func release(sessionID: String)", shell)
        self.assertIn("mutating func settle(currentSessionID: String?, replySessionID: String?", shell)
        self.assertIn('let committedBeforeCancel = cancellationInProgress && replyState == "completed"', shell)
        cancel = auth_store[auth_store.index("    func cancel() {"):]
        self.assertIn("if let terminalReply { apply(terminalReply) }", cancel)
        retry_start = auth_store[auth_store.index("private func runProvisioningRetry("):
                                 auth_store.index("private func run(sessionID requestedSession:")]
        self.assertIn('if session != requestedSession ||', retry_start)
        self.assertIn('(reply["session"] as? String) != requestedSession', retry_start)

    def test_sign_in_disappearance_leaves_certificate_invalidation_to_root_event_owner(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        start = shell.index("struct V3SignInView: View")
        end = shell.index("private var statusText", start)
        sign_in_lifecycle = shell[start:end]
        self.assertIn("auth.cancel()", sign_in_lifecycle)
        disappear_start = sign_in_lifecycle.rindex(".onDisappear {")
        disappear_end = sign_in_lifecycle.index("\n        }", disappear_start)
        disappear = sign_in_lifecycle[disappear_start:disappear_end]
        self.assertIn("status.reload()", disappear)
        self.assertNotIn("status.invalidateSetupFacts()", disappear)
        self.assertIn("status.refreshSetupFactsAfterAuthentication(sessionID: sessionID,", shell)

    def test_unreadable_recovery_journal_is_visible_and_blocks_new_work(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertIn("unresolvedRecoveryJournalUnreadable", shell)
        self.assertIn('operation: "recoveryDiscardUnreadable"', shell)
        self.assertIn("I checked; no SideStore operation is running", shell)
        self.assertIn("guard unresolvedOperationRecovery != nil || unresolvedRefreshRecoveryRunID != nil ||", shell)

    def test_certificate_mutations_invalidate_cached_jitless_readiness(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        start = shell.index("private func runConfirmed(action: String, serial: String) async {")
        end = shell.index("\n}\n\nstruct V3DeveloperServicesView", start)
        mutation = shell[start:end]
        self.assertIn("status.invalidateSetupFacts()", mutation)
        self.assertLess(mutation.index("status.invalidateSetupFacts()"), mutation.index("status.reload()"))

    def test_waiter_cancellation_and_health_reload_races_execute(self):
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; host state harness runs in macOS CI")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        helpers = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_host_state_harness.swift").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "main.swift"
            executable = Path(temporary) / "host-state"
            source.write_text(failure + "\n" + wire + "\n" + helpers + "\n" + harness, encoding="utf-8")
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_HOST_STATE_PASS", result.stdout)

    def test_shell_uses_cancellation_safe_waiters_and_health_rerun_queue(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertIn("withTaskCancellationHandler", shell)
        self.assertIn("cancelSnapshotWaiter(waiterID)", shell)
        self.assertIn("snapshotWaiterRegistry.takeAll()", shell)
        self.assertIn("V3SnapshotErrorPolicy.shouldMarkDisconnected(error)", shell)
        health = shell[shell.index("struct V3HealthView"):shell.index("private func openJITLessSetup", shell.index("struct V3HealthView"))]
        self.assertIn("status.invalidateSetupFacts()", health)
        self.assertIn("status.beginSetupFactObservation()", health)
        self.assertIn("while reloadQueue.finishIteration()", health)
        self.assertIn("healthRevisionIsCurrent(factRevision)", health)
        self.assertGreaterEqual(shell.count("V3PairingPresentationPolicy.displayText(statusConnected: status.connected,"), 3)
        action = shell[shell.index("if let action = status.issue?.primaryAction"):]
        action = action[:action.index("if status.hasUncertainInstallCancellation")]
        self.assertIn("if status.performPrimaryIssueAction()", action)
        self.assertIn("status.clearIssue()", action)
        self.assertIn("issue = nil", shell[shell.index("private func presentBusy()"):shell.index("private func runMutation", shell.index("private func presentBusy()"))])


if __name__ == "__main__":
    unittest.main()
