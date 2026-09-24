"""Regression coverage for the v3 Install / Sideload App reliability task.

New bug (NOT #27, which only added the missing UI action): the v3 install
flow can fail with a silent first attempt (back to Apps, no terminal error)
and, when it does surface, with a generic "installation: failed" that drops
the real structured failure.

Covers:
 1. first-attempt failure is visibly terminal, never a silent dismiss
 2. picker cancellation stays a clean cancel, not an install failure
    3. staged-file failures reach a pre-install file-preparation terminal path
 4. signing/provisioning errors are preserved, never mislabelled
 5. transport errors keep their stage via explicit markers/domains
 6. InstallationProxy failures land in the installation stage
 7. ApplicationVerificationFailed is preserved end to end
 8. 0xe8008024 reads as profile/application-verification rejection
 9. 0xe8008018 reads as signing-identity rejection
10. unknown install errors stay honest generic installation failures
11. the session id survives end to end as the correlation identifier
12. no sensitive material crosses XPC in terminal replies
13. no duplicate mutation or automatic retry is introduced
14. a successful install reloads authoritative installed-app state
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"
FAILURE = ROOT / "scripts/templates/combined_failure.swift"


def shell():
    return SHELL.read_text(encoding="utf-8")


def runtime():
    return RUNTIME.read_text(encoding="utf-8")


def service():
    return SERVICE.read_text(encoding="utf-8")


def operation_sheet():
    text = shell()
    start = text.index("struct V3OperationSheet")
    end = text.index("struct V3PromptSection", start)
    return text[start:end]


class InstallFirstAttemptTests(unittest.TestCase):
    def test_backend_cancelled_is_visible_not_silent(self):
        # A backend "cancelled" (watchdog/remote cancel the user did not tap)
        # renders an explicit terminal message; the old bare dismiss() sent
        # the user back to Apps with no explanation.
        sheet = operation_sheet()
        apply = sheet[sheet.index("private func apply"):]
        cancelled = apply[apply.index('case "cancelled"'):]
        cancelled = cancelled[:cancelled.index("case ", 10)]
        self.assertIn("cancelled before it finished", cancelled)
        self.assertIn("message =", cancelled)
        self.assertNotIn("dismiss()", cancelled)

    def test_failed_state_always_sets_message(self):
        sheet = operation_sheet()
        apply = sheet[sheet.index("private func apply"):]
        failed = apply[apply.index('case "failed":'):]
        failed = failed[:failed.index("default:", 10)]
        self.assertIn("message =", failed)

    def test_unreadable_poll_reply_is_an_explicit_failure(self):
        # A poll reply without a readable state must throw a named failure
        # instead of stalling the sheet on a spinner forever.
        sheet = operation_sheet()
        self.assertIn("unreadable operation state", sheet)

    def test_concurrent_perform_explains_itself(self):
        # Starting an operation while one is presented used to return
        # silently, which looks exactly like an ignored first tap.
        text = shell()
        self.assertIn("Another operation is already running", text)

    def test_picker_cancellation_is_a_clean_cancel(self):
        # Cancelling the picker does not stage a file or create a session.
        text = shell()
        self.assertIn("func documentPickerWasCancelled", text)
        self.assertIn("finish(nil)", text)
        self.assertIn("if let token = selectedInstallToken", text)
        self.assertIn("if let url {", text)
        self.assertIn("presentImmediately: false", text)

    def test_file_selection_failure_shows_alert(self):
        text = shell()
        fn = text[text.index("func stageSharedIPA"):]
        fn = fn[:fn.index("struct V3SideStoreApp")]
        self.assertIn("self.error =", fn)
        self.assertIn("V3IPAStaging.stage", fn)
        self.assertIn("CombinedIPAFileError", fn)
        self.assertNotIn("localizedDescription) }", fn)

    def test_file_preparation_failures_are_not_installation_proxy_failures(self):
        text = runtime()
        staging = (ROOT / "scripts/templates/v3_ipa_staging.swift").read_text(encoding="utf-8")
        self.assertIn("CombinedIPAFileError(.missingFile)", staging)
        self.assertIn("CombinedIPAFileError(.emptyFile)", staging)
        self.assertIn("CombinedIPAFileError(.invalidPackage)", staging)
        self.assertIn("CombinedFailure(operation: operation, stage: .filePreparation", FAILURE.read_text(encoding="utf-8"))
        self.assertIn("V3IPAStaging.inspect(token: token", text)


class InstallStructuredFailureTests(unittest.TestCase):
    def test_install_kinds_map_to_installation_stage(self):
        text = runtime()
        fn = text[text.index("private func terminalFailure"):]
        fn = fn[:fn.index("\n    }\n", fn.index("terminalFailure")) + 6]
        self.assertIn('"install"', fn)
        self.assertIn('"installURL"', fn)
        self.assertIn('"installSharedIPA"', fn)
        self.assertIn('"update"', fn)
        self.assertIn("stage = .installation", fn)

    def test_terminal_failure_attaches_structured_payload(self):
        # The backend terminal reply carries the user message, the fixed
        # diagnostics line, and the wire failure dict keyed by the session id.
        text = runtime()
        fn = text[text.index("private func terminalFailure"):]
        for key in ('"message": failure.message', '"technical": failure.technicalDetails',
                    '"failure": failure.wire', '"retryable"'):
            self.assertIn(key, fn)
        self.assertIn("CombinedFailure.capture(error, operation: kind, stage: stage, id: id)", fn)

    def test_host_renders_structured_failure(self):
        sheet = operation_sheet()
        self.assertIn('(reply["failure"] as? [String: Any])', sheet)
        self.assertIn("CombinedFailure.decode", sheet)
        self.assertIn("failureContext.recordPipelineFailure(failure)", sheet)
        self.assertIn('Section("What happened")', sheet)
        self.assertIn('Section("What you can do")', sheet)
        self.assertIn('DisclosureGroup("Technical details")', sheet)
        self.assertIn("Copy Diagnostics", sheet)

    def test_missing_failure_payload_has_safe_structured_fallback(self):
        sheet = operation_sheet()
        self.assertIn('CombinedFailure(operation: request.operation,', sheet)
        self.assertIn("The operation could not start, so the app pipeline did not run.",
                      (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8"))
        self.assertIn("exact underlying cause could not be safely identified",
                      FAILURE.read_text(encoding="utf-8"))

    def test_ipa_parse_errors_reach_terminal_path(self):
        # The staged path validates its archive and maps malformed input to a
        # pre-install file error before the pipeline can start.
        text = runtime()
        self.assertIn("OperationError.invalidApp", text)
        self.assertIn("readAppMetadata", text)
        self.assertIn("V3IPAStaging.inspect(token: token", text)
        self.assertIn("catch { throw CombinedIPAFileError(.invalidPackage) }", text)

    def test_pipeline_errors_are_preserved_not_relabelled(self):
        # The gate forwards the native pipeline result untouched. Signing and
        # provisioning stage evidence is added at the typed PipelineExecutor
        # boundary; the generic capture layer does not map broad SideSign domains.
        text = runtime()
        self.assertIn("gate.settle(result.map { _ in () })", text)
        failure = FAILURE.read_text(encoding="utf-8")
        capture = failure[failure.index("static func capture"):]
        for domain in ("SideSignErrorDomain", "ALTServerErrorDomain", "ALTAppleAPIErrorDomain"):
            self.assertNotIn('case "' + domain + '"', capture)
        self.assertIn('cause.userInfo["LCStructuredFailureStageV1"]', capture)
        self.assertIn('cause.userInfo["LCStructuredFailureCauseV1"]', capture)

    def test_unknown_install_error_stays_honest(self):
        text = runtime()
        fn = text[text.index("private func terminalFailure"):]
        # Unknown errors keep the caller installation stage with the generic
        # installation message; the underlying domain/code still travel.
        self.assertIn("stage = .installation", fn)
        failure = FAILURE.read_text(encoding="utf-8")
        self.assertIn("SideStore could not complete the application installation.", failure)


class InstallPPQEndToEndTests(unittest.TestCase):
    def test_ppq_verdict_token_in_diagnostics(self):
        failure = FAILURE.read_text(encoding="utf-8")
        self.assertIn("installVerdict=profileBanned", failure)
        self.assertIn("installVerdict=signingIdentityRejected", failure)
        # Bounded: only the two fixed installd codes produce a token.
        verdict = failure[failure.index("private var installVerdict"):]
        verdict = verdict[:verdict.index("\n    }\n") + 6]
        self.assertIn("0xE8008024", verdict)
        self.assertIn("0xE8008018", verdict)

    def test_ppq_messages_avoid_account_ban_claim(self):
        failure = FAILURE.read_text(encoding="utf-8")
        self.assertIn("provisioning profile is banned during application verification", failure)
        self.assertIn("identity used to sign the executable is no longer valid", failure)
        self.assertNotIn("banned your account", failure)

    def test_correlation_is_the_session_id(self):
        # terminalFailure passes the operation session id as the capture id,
        # which becomes the wire correlationID shown in Copy Diagnostics.
        text = runtime()
        self.assertIn("CombinedFailure.capture(error, operation: kind, stage: stage, id: id)", text)
        failure = FAILURE.read_text(encoding="utf-8")
        self.assertIn("correlationID = UUID(uuidString: id) != nil ? id : UUID().uuidString", failure)
        self.assertIn("correlation=", failure)


class InstallPrivacyAndSafetyTests(unittest.TestCase):
    def test_terminal_reply_carries_no_sensitive_keys(self):
        text = runtime()
        fn = text[text.index("private func terminalFailure"):]
        fn = fn[:fn.index("\n    }\n") + 6]
        for forbidden in ("password", "token", "dsid", "DSID", "pairing",
                          "privateKey", "header", "appleID", "phoneID"):
            self.assertNotIn(forbidden, fn)

    def test_no_automatic_install_retry(self):
        # Each attempt has an explicit generation and session ID. Retry waits
        # for cancellation acknowledgement and task completion before start.
        sheet = operation_sheet()
        self.assertEqual(sheet.count('operation: "opStart"'), 1)
        self.assertIn('"session": generation.uuidString', sheet)
        self.assertIn('request(operation: "opCancel"', sheet)
        self.assertIn("await oldTask?.value", sheet)
        self.assertIn("private var retryAllowed", sheet)
        self.assertNotIn('request.operation != "installSharedIPA"', sheet)
        for forbidden in ("Timer.", "DispatchQueue.main.asyncAfter", "Task.sleep(nanoseconds: 5"):
            self.assertNotIn(forbidden, sheet)
        self.assertNotIn('"V3SharedIPA."', shell())

    def test_single_flight_mutation_gates_intact(self):
        self.assertIn("mutationID", service())
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        self.assertIn("activeMutation", bridge)

    def test_successful_install_reloads_authoritative_state(self):
        sheet = operation_sheet()
        apply = sheet[sheet.index("private func apply"):]
        completed = apply[apply.index('case "completed":'):]
        completed = completed[:completed.index("case ", 10)]
        self.assertIn("status.reload()", completed)
        self.assertIn("status.reload()", sheet[sheet.index("onDisappear"):])
        # The installed-apps snapshot is the authority the UI reconciles.
        self.assertIn("accept(try await V3ServiceBridge.shared.request(operation: \"snapshot\"))", shell())


if __name__ == "__main__":
    unittest.main()
