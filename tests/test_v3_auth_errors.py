"""Regression coverage for v3.0.3 authentication error reporting (issue #31).

The v3 headless handler must preserve the real typed authentication failure
instead of inferring "bad password" from a repeated credentials prompt.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
WIRE = ROOT / "scripts/templates/v3_wire_contract.swift"
BRIDGE = ROOT / "scripts/templates/v3_service_bridge.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"


def runtime():
    return RUNTIME.read_text(encoding="utf-8")


def shell():
    return SHELL.read_text(encoding="utf-8")


class V3AuthErrorTests(unittest.TestCase):
    def test_missing_auth_session_with_failed_snapshot_stays_unconfirmed(self):
        host_text = shell()
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        self.assertIn('state: "resultUnknown"', primitives)
        self.assertIn("snapshotConfirmed else", primitives)
        self.assertIn("currentAttemptFailure.record(snapshotConfirmed: false", host_text)
        self.assertIn("Last confirmed account status: signed in", primitives)
        self.assertIn("V3AuthSessionUnavailablePolicy.resolve", host_text)

    def test_unknown_auth_without_session_reloads_status_instead_of_faking_cancel(self):
        host_text = shell()
        self.assertIn("V3AuthUnknownResultRecoveryPolicy.action", host_text)
        self.assertIn("case .reloadStatus:", host_text)
        self.assertIn("reloadAuthoritativeAccountStatus()", host_text)
        self.assertIn("V3AuthUnknownResultReconciliationPolicy.reportedState", host_text)
        self.assertIn("V3AuthCancellationFeedbackPolicy.statusLabel", host_text)
        self.assertNotIn('"promptExpired" || auth.state == "resultUnknown") {', host_text)

    def test_provisioning_capacity_rejection_keeps_confirmed_pre_dispatch_guidance(self):
        host_text = shell()
        self.assertIn("V3AuthProvisioningRetryDispatchPolicy.isConfirmedNotDispatched", host_text)
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        self.assertIn("failure.safeCause == .authResponseCapacityUnavailable", primitives)
        self.assertIn("SideStore could not start the provisioning retry", primitives)

    def test_auth_poll_recovery_calls_follow_swift_argument_declaration_order(self):
        host_text = shell()
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        signature = primitives[primitives.index("static func shouldResume("):]
        signature = signature[:signature.index(") -> Bool")]
        self.assertLess(signature.index("promptSubmissionInProgress"),
                        signature.index("pollFailureIsTransient"))
        cursor = 0
        call_count = 0
        marker = "V3AuthPollMonitorRecoveryPolicy.shouldResume("
        while True:
            start = host_text.find(marker, cursor)
            if start < 0:
                break
            ends = [position for position in (host_text.find(") {", start),
                                                host_text.find(") else {", start))
                    if position >= 0]
            end = min(ends) if ends else -1
            self.assertGreater(end, start)
            call = host_text[start:end]
            self.assertLess(call.index("promptSubmissionInProgress"),
                            call.index("pollFailureIsTransient"))
            cursor = end + 3
            call_count += 1
        self.assertEqual(call_count, 2)

    def test_handle_sign_in_result_is_implemented(self):
        text = runtime()
        self.assertIn("func handleSignInResult", text)
        # An empty no-op body would discard the real failure.
        self.assertNotIn(
            "func handleSignInResult(_ result: Result<(ALTAccount, ALTAppleAPISession), Error>) async {}",
            text)

    def test_failure_preserved_in_session(self):
        text = runtime()
        self.assertIn("previousFailure", text)
        self.assertIn("failure.wire", text)
        self.assertIn("CombinedFailure.capture", text)

    def test_success_clears_previous_failure(self):
        text = runtime()
        self.assertIn("previousFailure = nil", text)

    def test_cancellation_does_not_store_failure(self):
        text = runtime()
        self.assertIn("v3ClassifyAuthError", text)
        # Cancellation-class results clear state instead of displaying it.
        self.assertIn("error is CancellationError", text)
        self.assertIn("userCancelled", text)

    def test_typed_classification_covers_all_kinds(self):
        text = runtime()
        for kind in ("invalidCredentials", "appSpecificPasswordRequired", "invalidCode", "rateLimited",
                     "serviceUnavailable", "anisette", "network",
                     "accountRepairRequired", "unknown"):
            self.assertIn(kind, text)
        # Classification is type-based, not string guessing on server text.
        self.assertIn("as? DeveloperPortalError", text)
        self.assertIn("as? ServerError", text)
        self.assertIn("NSURLErrorDomain", text)

    def test_grandslam_rate_limit_codes_classified(self):
        text = runtime()
        for code in ("-22411", "-20102", "-21668"):
            self.assertIn(code, text)

    def test_attempt_markers_are_safe(self):
        text = runtime()
        self.assertIn("[V3_AUTH] ATTEMPT_FAILED", text)
        # Markers carry kinds/stages/codes, never secrets.
        for forbidden in ("password", "appleID", "verificationCode", "authToken",
                          "dsid", "DSID", "idmsToken", "header", "jsonPayload"):
            segment = text[text.index("[V3_AUTH] ATTEMPT_FAILED") - 200:
                           text.index("[V3_AUTH] ATTEMPT_FAILED") + 300]
            self.assertNotIn(forbidden, segment)

    def test_no_attempts_implies_password_assumption(self):
        text = shell()
        self.assertNotIn("That was not accepted. Check the Apple ID and password", text)
        self.assertNotIn("auth.attempts > 1", text)

    def test_host_renders_structured_failure(self):
        text = shell()
        self.assertIn("previousFailure", text)
        self.assertIn("V3AuthStore.failureMessage", text)
        self.assertIn("V3AuthStore.failureDetails", text)

    def test_top_level_previous_failure_is_preserved_until_credentials_submission(self):
        host = shell()
        self.assertIn("V3AuthPromptFailurePolicy.applying(reply: reply, current: previousFailure)", host)
        self.assertIn("V3AuthPromptFailurePolicy.isVisible(auth.previousFailure", host)
        self.assertNotIn('prompt["previousFailure"]', host)
        self.assertIn("clearingAfterSubmission", host)
        self.assertIn("clearPreviousFailure()", host)

    def test_authentication_and_post_auth_provisioning_have_distinct_terminal_states(self):
        runtime_text = runtime()
        host = shell()
        self.assertIn('"authenticatedProvisioningIncomplete"', runtime_text)
        self.assertIn("V3AuthTerminalPolicy.resolve", runtime_text)
        self.assertIn("V3AuthPostAuthenticationFailurePolicy.resolve", runtime_text)
        self.assertIn("postAuthentication.stage", runtime_text)
        post_auth = runtime_text[runtime_text.index('if authenticatedOutcome == "authenticatedProvisioningIncomplete"'):]
        post_auth = post_auth[:post_auth.index('} else if cancelled {')]
        self.assertNotIn("v3ClassifyAuthError(error)", post_auth)
        self.assertNotIn('response["failureKind"]', post_auth)
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        self.assertIn('case "authenticatedProvisioningIncomplete"', primitives)
        self.assertIn("V3AuthStatusTextPolicy.label", host)
        self.assertNotIn('message = "Sign-in failed."', host)
        self.assertIn('wire["kind"] = kind.rawValue', runtime_text)
        self.assertIn('reply["failureKind"] as? String', host)

    def test_reconciled_transport_failure_remains_visible_without_claiming_attempt_success(self):
        host = shell()
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        self.assertIn("currentAttemptFailure.record(snapshotConfirmed:", host)
        self.assertIn("auth.currentAttemptFailure.message", host)
        self.assertIn("struct V3AuthAttemptFailureNotice", primitives)
        self.assertIn("currently reports an account as signed in", primitives)
        self.assertIn("V3AuthReconciliationPresentationPolicy.resolve", host)
        self.assertIn("provisioningIncomplete = true", host)
        run = host[host.index("private func run(sessionID requestedSession: String)"):]
        run = run[:run.index("    private func pollLoop(")]
        self.assertIn('state = "resultUnknown"', run)
        self.assertIn("snapshotConfirmed = await reconcile(force: true, expectedSession: requestedSession)", run)
        self.assertIn('"resultUnknown"].contains(state)', host)
        self.assertIn("V3AuthUnknownResultRecoveryPolicy.action", host)
        self.assertIn("could not confirm that the sign-in request stopped", host)
        retry = host[host.index("func retryProvisioning()"):host.index("var canRetryProvisioning")]
        self.assertIn("currentAttemptFailure.clear()", retry)
        self.assertIn("V3AuthFailureDiagnosticsPolicy.render", host)

    def test_provisioning_retry_keeps_typed_anisette_and_network_guidance(self):
        runtime_text = runtime()
        host = shell()
        self.assertIn("V3AuthPostAuthenticationFailurePolicy.resolve", runtime_text)
        post_auth = runtime_text[runtime_text.index('if authenticatedOutcome == "authenticatedProvisioningIncomplete"'):]
        post_auth = post_auth[:post_auth.index('} else if cancelled {')]
        self.assertNotIn("v3ClassifyAuthError(error)", post_auth)
        auth_failure = runtime_text[runtime_text.index("} else {\n                let failure = CombinedFailure.capture(error, operation: \"signIn\"") :]
        self.assertIn("v3ClassifyAuthError(error)", auth_failure)
        self.assertIn('V3AuthStore.failureMessage(from: ["kind": failureKind])', host)

    def test_reauthentication_cancellation_reconciles_preexisting_account_state(self):
        runtime_text = runtime()
        shell_text = shell()
        self.assertIn("accountAppleIDAtStart", runtime_text)
        self.assertIn("V3AuthAttemptAuthenticationPolicy.confirms", runtime_text)
        self.assertIn('["timedOut", "failed", "cancelled", "resultUnknown", "promptExpired"].contains(state)',
                      (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8"))
        self.assertIn("V3ServiceBridge.authSnapshot(snapshot)", shell_text)
        self.assertIn('V3ServiceBridge.strictBool(reply["resumable"])', shell_text)
        self.assertIn("V3AuthSnapshotAuthorityPolicy.facts(authSnapshot)", shell_text)
        self.assertIn("V3AuthReconciliationPresentationPolicy.resolve", shell_text)
        self.assertIn("The sign-in attempt was cancelled. SideStore currently reports an account as signed in.",
                      (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8"))
        self.assertIn("previousFailure.map { Self.failureMessage(from: $0) }", shell_text)
        self.assertIn("await reconcile(force: true)", shell_text[shell_text.index("func cancel() {", shell_text.index("final class V3AuthStore")):])

    def test_auth_poll_transport_timeout_keeps_bounded_session_monitoring(self):
        host = shell()
        auth_store = host.index("final class V3AuthStore")
        poll_start = host.index("private func pollLoop(id: String, sessionDeadline: Date)", auth_store)
        poll = host[poll_start:host.index("private func apply(_ reply: [String: Any])", poll_start)]
        self.assertIn("V3AuthPollRecoveryPolicy.shouldRetry", poll)
        self.assertIn("sessionDeadline", poll)
        self.assertIn("requestDeadline: sessionDeadline", poll)
        self.assertIn("guard Date() < sessionDeadline", poll)
        self.assertIn("continue", poll)
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        self.assertIn("enum V3AuthPollRecoveryPolicy", primitives)

    def test_failed_unconfirmed_cancel_has_an_available_retry_action(self):
        host = shell()
        self.assertIn('Button(auth.cancellationWasAttempted ? "Retry Cancellation" : "Cancel Unconfirmed Sign-In"', host)
        self.assertIn("V3AuthCancellationRetryPolicy.canRetry", host)
        cancel = host[host.index("func cancel() {", host.index("final class V3AuthStore")):]
        self.assertIn("canRetryCancellation", cancel)

    def test_sign_in_reopening_reconciles_authoritative_side_store_snapshot(self):
        host = shell()
        sign_in = host[host.index("final class V3AuthStore"):host.index("struct V3SignInLink")]
        self.assertIn("func reconcile(force: Bool = false, expectedSession: String? = nil) async", sign_in)
        self.assertIn('request(operation: "snapshot")', sign_in)
        self.assertIn("let authoritative = accountFacts.authenticated", sign_in)
        self.assertNotIn("!account.isEmpty", sign_in)
        self.assertIn("signedIn = false", sign_in)
        self.assertIn(".task { await auth.reconcile() }", host)

    def test_auth_session_deadline_is_separate_from_xpc_start_deadline(self):
        wire = WIRE.read_text(encoding="utf-8")
        bridge = BRIDGE.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")
        auth_cases = service[service.index('case "authBegin":'):service.index('case "authPoll":')]
        self.assertIn("authSessionLifetime", wire)
        self.assertIn('requestPayload["sessionDeadline"]', bridge)
        self.assertIn('payload["sessionDeadline"] as? Date', auth_cases)
        self.assertIn('requestDeadline: request["deadline"] as? Date', auth_cases)
        self.assertNotIn('let deadline = request["deadline"] as? Date', auth_cases)
        self.assertIn("V3AuthSessionExpiryPolicy.response(authenticated: authenticated,", runtime())

    def test_auth_session_ownership_protects_reads_and_reconciles_timeout(self):
        bridge = BRIDGE.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")
        host = shell()
        self.assertIn("authSessionOwnership.hasActiveSession()", bridge)
        self.assertIn("updateAuthSessionOwnership(operation: operation", bridge)
        self.assertIn("V3NotDispatchedReplyPolicy.confirms(response, requestID: id,", bridge)
        self.assertIn("authSessionOwnership.clearAll()", bridge)
        self.assertIn("authSessionOwnership.clear(sessionID: sessionID)", bridge)
        self.assertIn('"authBegin", "authRetryProvisioning"].contains(operation)', service)
        self.assertIn('"operationNotDispatched"] = true', service)
        self.assertIn('"provisioningRetryAvailable": V3HeadlessRuntime.shared.auth.canResumeProvisioning()', service)
        self.assertIn("guard let authSnapshot = V3ServiceBridge.authSnapshot(snapshot) else", host)
        self.assertIn("let accountFacts = V3AuthSnapshotAuthorityPolicy.facts(authSnapshot)", host)
        self.assertIn("let canRetryProvisioning = accountFacts.provisioningRetryAvailable", host)
        self.assertIn("shouldReconcileAfterTerminal(current)", host)
        poll_start = host.index("private func pollLoop(id: String, sessionDeadline: Date)")
        poll_loop = host[poll_start:host.index("private func apply(_ reply: [String: Any])", poll_start)]
        self.assertIn("await reconcile(force: true, expectedSession: id)", poll_loop)
        self.assertIn("V3AuthPromptResponsePolicy.failureMessage(error)", host)
        self.assertIn("V3AuthPromptResponsePolicy.diagnostics(error)", host)
        self.assertIn("Button(\"Copy Diagnostics\", systemImage: \"doc.on.doc\")", host)
        self.assertIn("isSubmissionBlocked: auth.promptResponseBlocked", host)
        retry = host[host.index("private func runProvisioningRetry(previouslyAvailable:"):]
        retry = retry[:retry.index("// V3_FINISH_LATER_PRESERVES_ACCOUNT_V1")]
        self.assertIn("let snapshotConfirmed = await reconcile(force: true, expectedSession: requestedSession)", retry)
        self.assertIn("snapshotConfirmed && provisioningSessionUnavailable", retry)
        self.assertIn("V3ProvisioningRetryRecoveryPolicy.availabilityAfterFailure", retry)
        self.assertIn("V3AuthProvisioningRecoveryPolicy.resolve", host)
        self.assertIn("recovery.showRetryProvisioning", host)
        self.assertIn("recovery.showFinishLater", host)

    def test_auth_transport_failure_keeps_attempt_outcome_unknown(self):
        host = shell()
        run = host[host.index("private func run(sessionID requestedSession: String)"):]
        run = run[:run.index("    private func pollLoop(")]
        self.assertLess(run.index("let snapshotConfirmed = await reconcile(force: true, expectedSession: requestedSession)"),
                        run.index('state = "resultUnknown"'))
        unknownOutcome = run[run.index("let underlyingError = pollFailure?.underlying ?? error"):]
        self.assertIn("V3FailureGuidance.message(underlyingError)", unknownOutcome)
        self.assertNotIn('state = "failed"', unknownOutcome)
        self.assertIn("currentAttemptFailure.record(snapshotConfirmed:", run)

    def test_correlated_not_dispatched_auth_start_does_not_require_cancellation(self):
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        host = shell()
        self.assertIn("authStartNotDispatched", bridge)
        self.assertIn("V3AuthAttemptStartFailurePolicy.confirmedNotDispatched", bridge)
        client_guard = bridge[bridge.index("guard let client = RefreshHandler.shared.client else {"):]
        client_guard = client_guard[:client_guard.index("// Track ownership")]
        self.assertIn("V3AuthAttemptStartFailurePolicy.confirmedNotDispatched", client_guard)
        run = host[host.index("private func run(sessionID requestedSession: String)"):]
        run = run[:run.index("    private func pollLoop(")]
        self.assertLess(run.index("isConfirmedNotDispatched"), run.index('state = "resultUnknown"'))
        self.assertIn('state = "failed"', run)
        self.assertIn("cancellationConfirmed = true", run)
        self.assertIn("session = nil", run)

    def test_provisioning_retry_transport_failure_does_not_claim_saved_session_is_gone(self):
        host = shell()
        retry = host[host.index("private func runProvisioningRetry(previouslyAvailable:"):]
        retry = retry[:retry.index("    // V3_FINISH_LATER_PRESERVES_ACCOUNT_V1")]
        self.assertIn("await reconcile(force: true, expectedSession: requestedSession)", retry)
        self.assertIn("provisioningSessionUnavailable = true", retry)
        self.assertIn("snapshotConfirmed && provisioningSessionUnavailable", retry)
        self.assertIn("previouslyConfirmedAvailable: previouslyAvailable", retry)
        self.assertNotIn("provisioningRetryAvailable = true", retry)
        self.assertNotIn('The saved Apple session is no longer available. Sign in again', retry)
        self.assertIn("V3AuthProvisioningRetryDispatchPolicy.isConfirmedNotDispatched", retry)
        self.assertIn("if snapshotConfirmed {", retry)
        self.assertIn("provisioningSessionUnavailable = !provisioningRetryAvailable", retry)
        self.assertNotIn("provisioningRetryAvailable || previouslyAvailable", retry)
        runtime_text = runtime()
        self.assertIn("V3ProvisioningResumeIdentityPolicy.select", runtime_text)
        self.assertIn("authenticatedSessionAppleID: session?.authenticatedAppleID", runtime_text)
        self.assertIn("hasSession: AuthManager.shared.session != nil", runtime_text)
        self.assertIn("hasTeamAccount: AuthManager.shared.team?.account != nil", runtime_text)

    def test_prompt_expiry_and_session_timeout_have_distinct_recovery_states(self):
        host = shell()
        self.assertIn('state = "promptExpired"', host)
        self.assertIn('state == "promptExpired"', host)
        self.assertIn('state == "timedOut"', host)
        self.assertIn('case "promptExpired": return "Verification expired"', host)
        self.assertIn('case "timedOut": return "Sign-in timed out"', host)
        start = host.index('if V3ServiceBridge.strictBool(reply["promptExpired"]) == true')
        end = host.index("guard V3AuthSessionResponsePolicy.mayApplyReply", start)
        expiry = host[start:end]
        reconcile = expiry.index("await reconcile(force: true, expectedSession: session)")
        self.assertLess(expiry.index('message = "That verification session expired.'), reconcile)
        self.assertGreater(expiry.rfind("return"), reconcile)
        self.assertNotIn("Choose a verification method again.", host)

    def test_auth_reconciliation_rechecks_attempt_generation_after_snapshot(self):
        host = shell()
        sign_in = host[host.index("final class V3AuthStore"):host.index("struct V3SignInLink")]
        reconcile = sign_in[sign_in.index("func reconcile(force: Bool = false, expectedSession: String? = nil)"):]
        reconcile = reconcile[:reconcile.index("private func run(sessionID")]
        self.assertIn("reconciliationGate.begin(sessionID: session, state: state, revision: revision)", reconcile)
        self.assertIn("reconciliationGate.mayApply(ticket, sessionID: session", reconcile)
        self.assertIn("reconciliationGate.invalidate()", sign_in[sign_in.index("func begin()"):
            sign_in.index("var canBegin")])
        self.assertIn("V3AuthReconciliationGate", (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8"))
        self.assertIn("V3AuthReconciliationSessionPolicy.mayStart", sign_in)
        self.assertIn("expectedSession: requestedSession", sign_in)
        self.assertIn("V3AuthAttemptFailureCommitPolicy.mayCommit", sign_in)
        self.assertIn("V3AuthPollFailureRacePolicy.shouldIgnore", sign_in)
        self.assertIn("promptResponseGeneration &+= 1", sign_in)
        self.assertLess(reconcile.index("shouldPreserveActivePrompt"),
                        reconcile.index("if authoritative"))

    def test_superseded_poll_failure_restarts_monitor_for_sign_in_and_provisioning(self):
        sign_in = shell()[shell().index("final class V3AuthStore"):shell().index("struct V3SignInLink")]
        self.assertGreaterEqual(sign_in.count("restartPollMonitorAfterSupersededFailure(sessionID: requestedSession"), 4)
        recovery = sign_in[sign_in.index("private func restartPollMonitorAfterSupersededFailure"):]
        self.assertIn("V3AuthPollMonitorRecoveryPolicy.shouldResume", recovery)
        self.assertIn("pollFailureIsTransient: pollFailureIsTransient", recovery)
        self.assertIn("task = Task", recovery)
        self.assertIn("continuePollingAfterSupersededFailure", recovery)
        self.assertIn("sessionDeadline: sessionDeadline", recovery)

    def test_first_unconfirmed_cancel_is_not_mislabeled_as_retry(self):
        host = shell()
        self.assertIn('@Published private(set) var cancellationWasAttempted = false', host)
        self.assertIn('cancellationWasAttempted ? "Retry Cancellation" : "Cancel Unconfirmed Sign-In"', host)
        self.assertIn('cancellationWasAttempted = true', host)
        self.assertIn('cancellationWasAttempted = false', host)

    def test_auth_cancel_consumes_terminal_reply_and_reconciles_account(self):
        host = shell()
        store = host[host.index("final class V3AuthStore"):host.index("struct V3SignInLink")]
        cancel = store[store.index("func cancel() {"):]
        self.assertIn('terminalReply = try await V3ServiceBridge.shared.request(operation: "authCancel"', cancel)
        self.assertIn("if let terminalReply { apply(terminalReply) }", cancel)
        self.assertIn("await reconcile(force: true)", cancel)
        self.assertIn("if !signedIn, terminalReply == nil", cancel)
        self.assertNotIn('state = "cancelled"\n                message = "Sign-in was cancelled."', cancel)
        self.assertIn("V3AuthSessionResponsePolicy.mayApplyReply", store)
        runtime_text = runtime()
        self.assertIn("session.terminal.isEmpty", runtime_text)
        self.assertIn("!session.cancellationRequested", runtime_text)
        self.assertIn('"responsePending": true', runtime_text)
        run = store[store.index("private func run(sessionID requestedSession: String) async {"):]
        run = run[:run.index("private func pollLoop", 1)]
        self.assertIn("if isCancelling || Task.isCancelled { return }", run)
        self.assertIn('target: requestedSession,', run)
        self.assertIn('payload: ["session": requestedSession, "sessionDeadline": sessionDeadline]', run)
        self.assertIn("session = requestedSession", store[store.index("func begin() {"):])
        self.assertNotIn(".task { auth.begin() }", host)

    def test_password_guidance_only_for_proven_credentials(self):
        text = shell()
        self.assertIn('"invalidCredentials"', text)
        self.assertIn("Check them and try again", text)
        self.assertIn('case "appSpecificPasswordRequired": return "Apple requires an app-specific password for this authentication path."', text)
        self.assertNotIn("appSpecificPasswordRequired: return \"Apple did not accept the Apple ID or password", text)

    def test_no_sensitive_fields_in_prompt(self):
        text = runtime()
        start = text.index("func v3Prompt")
        end = text.index("\n}\n", start) + 3
        prompt_fn = text[start:end]
        for forbidden in ("password", "token", "dsid", "DSID", "header",
                          "jsonPayload", "pairing", "privateKey"):
            self.assertNotIn(forbidden, prompt_fn)

    def test_no_automatic_retry(self):
        text = runtime()
        # The authentication loop belongs to upstream SignInOperation;
        # the v3 handler must not resubmit credentials itself.
        auth_section = text[:text.index("final class V3HeadlessPipelineHandler")]
        self.assertNotIn("while true", auth_section)


if __name__ == "__main__":
    unittest.main()
