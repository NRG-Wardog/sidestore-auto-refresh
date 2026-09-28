"""Execute production host state helpers for cancellation and stale-result races."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWIFTC = shutil.which("swiftc")


class V3HostStateTests(unittest.TestCase):
    def test_successful_sign_in_invalidates_and_refreshes_readiness_in_place(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        start = shell.index("struct V3SignInView: View")
        end = shell.index("\nstruct V3CertificateRow", start)
        sign_in = shell[start:end]
        auth_change_start = sign_in.index(".onChange(of: auth.isSignedIn)")
        auth_change_end = sign_in.index(".onChange(of: status.jitlessReadiness)", auth_change_start)
        auth_change = sign_in[auth_change_start:auth_change_end]
        self.assertIn("authenticationStateChanged(isSignedIn: isSignedIn)", auth_change)
        self.assertIn("guard isSignedIn else { return }", auth_change)
        refresh = auth_change.index("status.refreshSetupFactsAfterSignIn()")
        reload = auth_change.index("status.reload()", refresh)
        self.assertLess(refresh, reload)
        refresh_method = shell[shell.index("func refreshSetupFactsAfterSignIn() {"):]
        refresh_method = refresh_method[:refresh_method.index("\n    }")]
        self.assertIn("invalidateSetupFacts()", refresh_method)
        self.assertIn("setupFactObservation = .deferred", refresh_method)
        self.assertIn("Task { await observeSetupFacts() }", refresh_method)
        self.assertIn("jitlessReadinessObservation.observe(readiness)", sign_in)
        self.assertIn("readinessForPresentation(\n                status.jitlessReadiness)", sign_in)
        self.assertIn('URL(string: "livecontainer://jitless-setup")', sign_in,
                      "canonical LiveContainer setup routing must remain in place")

    def test_successful_provisioning_retry_refreshes_readiness_without_signed_in_edge(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        start = shell.index("struct V3SignInView: View")
        end = shell.index("\nstruct V3CertificateRow", start)
        sign_in = shell[start:end]
        retry_change_start = sign_in.index(".onChange(of: auth.successfulProvisioningRetryRevision)")
        retry_change_end = sign_in.index(".onDisappear", retry_change_start)
        retry_change = sign_in[retry_change_start:retry_change_end]
        self.assertIn("guard revision > 0 else { return }", retry_change)
        self.assertIn("jitlessReadinessObservation.certificateMayHaveChanged()", retry_change)
        self.assertIn("status.refreshSetupFactsAfterSignIn()", retry_change)
        self.assertNotIn("status.reload()", retry_change,
                         "the health refresh must not duplicate the account snapshot")

        auth_store = shell[shell.index("final class V3AuthStore"):]
        auth_store = auth_store[:auth_store.index("\nstruct V3SignInLink")]
        self.assertIn("@Published private(set) var successfulProvisioningRetryRevision: UInt64 = 0", auth_store)
        self.assertIn("private var provisioningRetryReadinessOwnership = V3ProvisioningRetryReadinessOwnership()", auth_store)
        self.assertIn("provisioningRetryReadinessOwnership.begin(sessionID: requestedSession)", auth_store)
        self.assertNotIn("provisioningRetryInProgress", auth_store,
                         "retry identity is session-owned, not a task-scoped shared flag")
        apply = auth_store[auth_store.index("private func apply(_ reply: [String: Any])"):
                           auth_store.index("func clearPreviousFailure()")]
        self.assertIn("provisioningRetryReadinessOwnership.settle(", apply)
        self.assertIn("successfulProvisioningRetryRevision &+= 1", apply)
        self.assertIn('replyState == "completed"', shell)
        self.assertIn("handoffAfterSupersededPollFailure(sessionID: sessionID)", auth_store)
        self.assertIn("provisioningRetryReadinessOwnership.owns(sessionID: sessionID)", auth_store)
        self.assertIn("provisioningRetryReadinessOwnership.allowsPromptResponse(sessionID: session)", auth_store)
        self.assertIn("mutating func begin(sessionID: String)", shell)
        self.assertIn("mutating func release(sessionID: String)", shell)
        self.assertIn("mutating func settle(currentSessionID: String?, replySessionID: String?", shell)
        self.assertIn('let committedBeforeCancel = cancellationInProgress && replyState == "completed"', shell)
        cancel = auth_store[auth_store.index("    func cancel() {"):]
        self.assertIn("if let terminalReply { apply(terminalReply) }", cancel)
        retry_start = auth_store[auth_store.index("private func runProvisioningRetry("):
                                 auth_store.index("private func run(sessionID requestedSession:")]
        self.assertIn('if session != requestedSession ||', retry_start)
        self.assertIn('(reply["session"] as? String) != requestedSession', retry_start)

    def test_all_sign_in_routes_invalidate_jitless_facts_before_reload(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        start = shell.index("struct V3SignInView: View")
        end = shell.index("private var statusText", start)
        sign_in_lifecycle = shell[start:end]
        self.assertIn("auth.cancel()", sign_in_lifecycle)
        disappear_start = sign_in_lifecycle.rindex(".onDisappear {")
        disappear_end = sign_in_lifecycle.index("\n        }", disappear_start)
        disappear = sign_in_lifecycle[disappear_start:disappear_end]
        invalidation = disappear.index("status.invalidateSetupFacts()")
        reload = disappear.index("status.reload()", invalidation)
        self.assertLess(invalidation, reload)

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
