"""Compile production state helpers and execute race/file-lifecycle scenarios."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWIFTC = shutil.which("swiftc")
CLANG = shutil.which("clang")
WIRE_CONTRACT = ROOT / "scripts/templates/v3_wire_contract.swift"


class V3BehavioralHarnessTests(unittest.TestCase):
    def compile_and_run(self, source: str, marker: str) -> None:
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; behavioral harnesses run in macOS CI")
        if "enum V3WireContract {" not in source:
            source = WIRE_CONTRACT.read_text(encoding="utf-8") + "\n" + source
        with tempfile.TemporaryDirectory() as temporary:
            main = Path(temporary) / "main.swift"
            executable = Path(temporary) / "behavior"
            main.write_text(source, encoding="utf-8")
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(main), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(marker, result.stdout)

    def test_host_selected_app_group_is_validated_by_production_policy(self):
        if sys.platform != "darwin" or not CLANG:
            self.skipTest("Objective-C Foundation behavioral harness runs in macOS CI")
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "app-group-selection"
            harness = ROOT / "tests/fixtures/v3_app_group_selection_harness.m"
            compiled = subprocess.run([CLANG, "-fobjc-arc", "-framework", "Foundation",
                                       str(harness), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_APP_GROUP_SELECTION_PASS", result.stdout)

    def test_snapshot_epoch_waiters_and_mutation_ownership_execute(self):
        # V3_LOAD_ACTIVITY_OWNERSHIP_V1: one `loading` flag used to mean both
        # "a snapshot is in flight" and "a mutation is in flight", so a caller
        # awaiting authoritative status could join a mutation and be released by
        # the mutation's completion. This executes the ownership interleavings
        # against production gate/epoch/registry helpers, including the case
        # where a presentation ends before an older in-flight snapshot returns.
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        # The primitives reference the error model, so it travels with them.
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_snapshot_ownership_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness, "V3_SNAPSHOT_OWNERSHIP_PASS")

    def test_response_encoding_classification_survives_the_wire(self):
        # V3_RESPONSE_CLASSIFICATION_CARRIER_V1: executes the REAL service
        # encoder and the REAL host reply classifier against each other over
        # real property-list bytes, using the exact production fallback shape
        # that carries BOTH a legacy "error" token and a structured "failure"
        # envelope. Also cross-checks the property-list leaf contract against
        # Foundation itself rather than a hardcoded expectation list.
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_response_classification_harness.swift").read_text(encoding="utf-8")
        # The classifier is the pure half of the bridge; the UIKit-dependent
        # bridge class cannot be compiled standalone, so only the pure
        # operations-and-errors context is taken.
        context = bridge[bridge.index("enum V3CatalogRequestContext {"):]
        context = context[:context.index("\n@MainActor")]
        self.compile_and_run(wire + "\n" + failure + "\n" + helper + "\n" + context + "\n" + harness,
                             "V3_RESPONSE_CLASSIFICATION_PASS")

    def test_operation_refresh_and_settings_state_machines_execute(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_release_behavior_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(helper + "\n" + failure + "\n" + harness, "V3_RELEASE_BEHAVIOR_PASS")

    def test_refresh_intent_operation_creation_failure_resumes_scheduler(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        start = helper.index("enum V3RefreshIntentStartPolicy {")
        end = helper.index("\nenum V3SetupTestAttemptPolicy", start)
        policy = helper[start:end]
        harness = (ROOT / "tests/fixtures/v3_refresh_intent_start_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run("import Foundation\n" + failure + "\n" + policy + "\n" + harness,
                             "V3_REFRESH_INTENT_START_PASS")

    def test_invalid_pairing_remains_typed_and_actionable(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        start = runtime.index("enum V3HeadlessPairingFailure {")
        end = runtime.index("\n// V3_HEADLESS_RUNTIME_V1", start)
        pairing_classifier = runtime[start:end]
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        context = bridge[bridge.index("enum V3CatalogRequestContext {"):]
        context = context[:context.index("\n@MainActor")]
        harness = (ROOT / "tests/fixtures/v3_pairing_failure_guidance_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(wire + "\n" + failure + "\n" + helper + "\n" + context + "\n" + pairing_classifier + "\n" + harness,
                             "V3_PAIRING_FAILURE_GUIDANCE_PASS")

    def test_pairing_import_only_offers_file_retry_for_file_failures(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_pairing_import_recovery_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness,
                             "V3_PAIRING_IMPORT_RECOVERY_PASS")

    def test_pinned_minimuxer_parser_rejects_unrelated_pairing_plists(self):
        side = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        source = Path(side) if side else ROOT / ".audit/v3-side-upstream"
        if not source.is_dir():
            self.skipTest("pinned SideStore source is supplied by macOS CI")
        revision = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(revision, "ff25922e5c13ccfafd83bda5092910d848ebd409")
        common = source / "Dependencies/minimuxer/Common"
        compiler = SWIFTC
        if not compiler:
            self.skipTest("Swift compiler unavailable; pinned pairing parser runs in macOS CI")
        harness = (ROOT / "tests/fixtures/v3_pinned_pairing_parser_harness.swift").read_text(encoding="utf-8")
        program = ((common / "PairingProtocol.swift").read_text(encoding="utf-8") + "\n" +
                   (common / "PairingFile.swift").read_text(encoding="utf-8") + "\n" + harness)
        with tempfile.TemporaryDirectory() as temporary:
            main = Path(temporary) / "main.swift"
            executable = Path(temporary) / "pairing-parser"
            main.write_text(program, encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(main), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_PINNED_PAIRING_PARSER_PASS", result.stdout)

    def test_service_readiness_backoff_is_bounded_and_ready_wins_deadline(self):
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        handler = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        helper = handler[:handler.index("@MainActor\nclass RefreshHandler")]
        harness = (ROOT / "tests/fixtures/v3_service_readiness_policy_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run("import Foundation\n" + failure + "\n" + wire + "\n" + primitives + "\n" + helper + "\n" + harness,
                             "V3_SERVICE_READINESS_POLICY_PASS")

    def test_secret_handoff_keeps_credentials_codes_and_backup_passwords_out_of_plists(self):
        template = (ROOT / "scripts/templates/v3_secret_handoff.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        record = template[template.index("enum V3SecretHandoffRecord {"):template.index("\nenum V3SharedFileRecord {")]
        shared_file = template[template.index("enum V3SharedFileRecord {"):template.index("\nenum V3SecretHandoff {")]
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_secret_handoff_wire_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run("import Foundation\n" + failure + "\n" + wire + "\n" + record + "\n" + shared_file + "\n" + harness,
                             "V3_SECRET_HANDOFF_WIRE_PASS")

    def test_service_admission_retains_active_backend_operation_ownership(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_operation_service_admission_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness,
                             "V3_SERVICE_OPERATION_ADMISSION_PASS")

    def test_auth_session_ownership_reconciliation_and_prompt_recovery_execute(self):
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_auth_ownership_reconciliation_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(wire + "\n" + failure + "\n" + helper + "\n" + harness,
                             "V3_AUTH_OWNERSHIP_RECONCILIATION_PASS")

    def test_operation_pipeline_phase_and_progress_invariants_execute(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_operation_phase_progress_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness, "V3_OPERATION_PHASE_PROGRESS_PASS")

    def test_operation_terminal_harness_covers_delete_reconciliation_and_cancellation(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_operation_terminal_harness.swift").read_text(encoding="utf-8")
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        poll = shell[shell.index("private func pollLoop(id: String, generation: UUID)"):]
        poll = poll[:poll.index("private func answerPrompt(")]
        apply = shell[shell.index("private func apply(_ reply: [String: Any], generation: UUID, sessionID: String)"):]
        apply = apply[:apply.index("private func applyCompletionSettlement(")]
        completion = shell[shell.index("private func applyCompletionSettlement("):]
        completion = completion[:completion.index("private func answerPrompt(")]
        self.assertIn("if V3OperationCompletionPolicy.shouldContinuePolling", poll)
        self.assertIn("applyCompletionSettlement(reply, sessionID: sessionID)", apply)
        self.assertIn("completed successfully", completion)
        self.assertIn("completedAwaitingBackendSettlement", completion)
        self.assertIn("if completionAwaitingSettlement", shell)
        self.assertIn("lastCheck: lastLibraryCheckAt", runtime)
        self.assertIn("V3DeleteReconciliationPolicy.shouldCheckLibrary", runtime)
        self.assertIn("V3DeleteReconciliationPolicy.shouldThrottleLibraryChecks", runtime)
        self.assertIn("V3DeleteReconciliationPolicy.nextCallbackPollDelay", runtime)
        self.assertIn("V3OperationSessionRetentionPolicy.shouldRefreshTerminalAt", runtime)
        self.assertIn("V3OperationSessionRetentionPolicy.isExpired", runtime)
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + wire + "\n" + helper + "\n" + harness,
                             "V3_OPERATION_CANCELLATION_TERMINAL_PASS")

    def test_source_add_persistence_and_duplicate_semantics_execute(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_source_add_persistence_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness, "V3_SOURCE_ADD_PERSISTENCE_PASS")

    def test_refresh_all_request_correlation_terminal_order_and_absorbing_states_execute(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_refresh_all_attempt_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(helper + "\n" + failure + "\n" + harness,
                             "V3_REFRESH_ALL_REQUEST_TERMINAL_PASS")

    def test_scheduler_and_direct_refresh_runs_have_distinct_identity(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_refresh_run_identity_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run("import Foundation\n" + failure + "\n" + helper + "\n" + harness,
                             "V3_REFRESH_RUN_IDENTITY_PASS")

    def test_refresh_all_current_run_failure_and_target_policy_execute(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_refresh_failure_correlation_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness,
                             "V3_REFRESH_FAILURE_CORRELATION_AND_TARGET_POLICY_PASS")

    def test_zero_excess_extensions_skip_prompt_behavior_executes(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_extension_removal_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness, "V3_ZERO_EXTENSION_PROMPT_PASS")

    def test_signing_retry_preserves_stage_and_start_failure_is_distinct(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_operation_retry_failure_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(helper + "\n" + failure + "\n" + harness,
                             "V3_RETRY_SIGNING_STAGE_AND_START_FAILURE_PASS")

    def test_picker_staging_file_lifetime_and_path_validation_execute(self):
        helper = (ROOT / "scripts/templates/v3_ipa_staging.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_ipa_staging_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness, "V3_IPA_STAGING_PASS")

    def test_prompt_cancellation_and_duplicate_answers_execute(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        begin = runtime.index("enum V3PromptAnswerDisposition:")
        end = runtime.index("\n@MainActor\nfinal class V3HeadlessRuntime", begin)
        prompt_center = runtime[begin:end]
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_prompt_race_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + prompt_center + "\n" + harness,
                             "V3_PROMPT_RACE_PASS")

    def test_typed_authentication_provisioning_and_ppq_context_execute(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        begin = runtime.index("enum V3AuthFailureKind:")
        end = runtime.index("// MARK: - Provisioning failure guidance", begin)
        classifier = runtime[begin:end]
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        host_message_start = shell.index("    static func failureMessage(from failure: [String: Any]) -> String {")
        host_message_end = shell.index("\n    static func failureDetails(", host_message_start)
        host_message = shell[host_message_start:host_message_end]
        host_message = host_message.replace("static func failureMessage", "func v3HostAuthFailureMessage")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_auth_classification_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run("import Foundation\n" + failure + "\n" + classifier + "\n" +
                             host_message + "\n" + helper + "\n" + harness,
                             "V3_AUTH_AND_PPQ_CLASSIFICATION_PASS")

    def test_catalog_response_plist_round_trip_and_encoding_classification_execute(self):
        # V3_CATALOG_ROW_PLIST_SAFE_V1: actually serializes and decodes the
        # catalog response, proves the boxed-Optional premise, and proves the
        # two encoder failure modes are distinguished.
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_catalog_response_encoding_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run("import Foundation\n" + wire + "\n" + failure + "\n" + helper + "\n" + harness,
                             "V3_CATALOG_RESPONSE_ENCODING_PASS")

    def test_malformed_structured_failure_never_falls_back_to_legacy_error_token(self):
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        start = bridge.index("enum V3CatalogRequestContext {")
        end = bridge.index("// V3_HOST_COMMAND_BRIDGE_V1", start)
        context = bridge[start:end]
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_malformed_structured_failure_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + wire + "\n" + context + "\n" + harness,
                             "V3_MALFORMED_STRUCTURED_FAILURE_PASS")

    def test_setup_completion_status_issue_routing_and_jitless_execute(self):
        # V3_SETUP_COMPLETION_POLICY_V1, V3_RELOAD_STATUS_VISIBILITY_V1,
        # V3_USER_FACING_ISSUE_V1, V3_STATUS_PRESENTATION_V1,
        # V3_JITLESS_CERT_DISTINCTION_V1, V3_RELOAD_GATE_V1,
        # V3_SOURCE_EDITING_POLICY_V1
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        event_marker = shell.index("// V3_AUTH_READINESS_REFRESH_EVENT_V1")
        event_start = shell.rfind("\n", 0, event_marker) + 1
        event_end = shell.index("\nextension LCAppModel", event_marker)
        readiness_event_contract = shell[event_start:event_end]
        retry_policy_marker = shell.index("enum V3ProvisioningRetryReadinessPolicy")
        retry_policy_start = shell.rfind("\n", 0, retry_policy_marker) + 1
        retry_policy_end = shell.index("\nprivate enum V3JITLessStatusReader", retry_policy_marker)
        readiness_retry_contract = shell[retry_policy_start:retry_policy_end]
        harness = (ROOT / "tests/fixtures/v3_setup_and_semantic_ux_harness.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        # The behavioral fixture executes the same classifier the service catch
        # uses. This small wiring assertion prevents the real anisetteSync
        # request from falling back to the generic command-stage capture.
        anisette_branch = 'else if operation == "anisetteSync" {'
        branch_start = service.index(anisette_branch)
        branch_end = service.index('} else {', branch_start)
        branch = service[branch_start:branch_end]
        self.assertIn("V3AnisetteSyncFailurePolicy.failure(error, id: id).wire", branch)
        self.assertGreater(branch_start, service.index("else if let structuredFailure = error as? CombinedFailure"))
        self.assertLess(branch_start, service.index("CombinedFailure.capture(", branch_start))
        self.compile_and_run(failure + "\n" + helper + "\n" +
                             readiness_event_contract + "\n" +
                             readiness_retry_contract + "\n" + harness,
                             "V3_SETUP_AND_SEMANTIC_UX_PASS")

    def test_sign_in_jitless_ui_uses_authoritative_readiness_and_actions(self):
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        sign_in_start = host.index("struct V3SignInView: View")
        sign_in_end = host.index("\nstruct V3CertificateRow", sign_in_start)
        sign_in = host[sign_in_start:sign_in_end]
        self.assertIn("V3SignInJITLessGuidancePolicy.resolve", sign_in)
        self.assertIn("readiness: status.jitlessReadiness", sign_in)
        self.assertNotIn('Text("Next: Set Up JIT-Less")', sign_in)
        self.assertIn('Label("Review JIT-Less Status"', sign_in)
        self.assertIn('Button("Continue to JIT-Less Setup")', sign_in)
        self.assertIn('Button("Refresh JIT-Less Certificate")', sign_in)
        self.assertIn('Label("Open Certificates"', sign_in)

    def test_two_factor_phone_back_sends_no_delivery_and_allows_new_method(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        policy_start = runtime.index("enum V3TwoFactorPhoneSelectionPolicy {")
        policy_end = runtime.index("\n\n@MainActor\nfinal class V3HeadlessAuthHandler", policy_start)
        production_policy = runtime[policy_start:policy_end]
        choose_start = runtime.index("private func chooseDeliveryMethod(phoneNumbers:")
        choose_end = runtime.index("\n    private func enterVerificationCode(", choose_start)
        choose = runtime[choose_start:choose_end]
        self.assertIn('["id": "changeMethod", "label": "Change Verification Method"]', choose)
        self.assertIn("case .changeMethod:", choose)
        self.assertIn("return try await chooseDeliveryMethod(phoneNumbers: phoneNumbers)", choose)
        self.assertLess(choose.index("case .changeMethod:"), choose.index("2FA_DELIVERY_REQUESTED mode=sms"))
        self.assertLess(choose.index("case .changeMethod:"), choose.index("2FA_DELIVERY_REQUESTED mode=voice"))

        shell_source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        prompt_start = shell_source.index("struct V3PromptSection: View")
        phone_step_start = shell_source.index("case .choosePhoneNumber:", prompt_start)
        phone_step_end = shell_source.index("case .enterVerificationCode:", phone_step_start)
        phone_step = shell_source[phone_step_start:phone_step_end]
        self.assertIn('Button("Change Verification Method"', phone_step)
        self.assertIn('answer["action"] = "changeMethod"', phone_step)
        self.assertIn("twoFactorCancelButton()", phone_step)

        harness = (ROOT / "tests/fixtures/v3_two_factor_phone_method_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run("import Foundation\n" + production_policy + "\n" + harness,
                             "V3_TWO_FACTOR_PHONE_METHOD_PASS")

    def test_shared_refresh_prerequisite_policy_executes(self):
        # V3_REFRESH_PREREQUISITE_POLICY_V1: executes the single authoritative
        # contract, including the canonical pairing failure identity and the
        # fail-open behaviour for an unknown status.
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_refresh_prerequisite_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness,
                             "V3_REFRESH_PREREQUISITE_PASS")

    def test_typed_provisioning_operation_error_guidance_executes(self):
        # V3_PROVISIONING_RESUME_V1: executes the real typed OperationError
        # guidance, proves no associated value is forwarded, and proves an
        # authenticated terminal is distinct from a failed sign-in.
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        guidance_start = runtime.index("func v3OperationErrorGuidance(")
        guidance_end = runtime.index("\n}\n", guidance_start) + len("\n}\n")
        guidance = runtime[guidance_start:guidance_end]
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        policy_start = helper.index("enum V3AuthTerminalPolicy {")
        policy_end = helper.index("\n}\n", policy_start) + len("\n}\n")
        policy = helper[policy_start:policy_end]
        post_policy_start = helper.index("struct V3AuthPostAuthenticationFailurePresentation: Equatable {")
        post_policy_end = helper.index("\n}\n", helper.index(
            "enum V3AuthPostAuthenticationFailurePolicy {", post_policy_start)) + len("\n}\n")
        post_policy = helper[post_policy_start:post_policy_end]
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_provisioning_typed_guidance_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + guidance + "\n" + policy + "\n" + post_policy + "\n" + harness,
                             "V3_PROVISIONING_TYPED_GUIDANCE_PASS")

    def test_auth_2fa_jitless_and_prerequisite_error_contracts_execute(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_auth_jitless_error_behavior_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness,
                             "V3_AUTH_2FA_JITLESS_AND_ERROR_BEHAVIOR_PASS")
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertIn('V3AuthRepairURLPolicy.openableURL(value)', shell)
        self.assertIn('Link("Open Apple Account Repair", destination: repairURL)', shell)


if __name__ == "__main__":
    unittest.main()
