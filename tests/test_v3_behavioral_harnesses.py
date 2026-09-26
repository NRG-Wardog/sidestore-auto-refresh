"""Compile production state helpers and execute race/file-lifecycle scenarios."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWIFTC = shutil.which("swiftc")


class V3BehavioralHarnessTests(unittest.TestCase):
    def compile_and_run(self, source: str, marker: str) -> None:
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; behavioral harnesses run in macOS CI")
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

    def test_snapshot_and_mutation_load_ownership_executes(self):
        # V3_LOAD_ACTIVITY_OWNERSHIP_V1: one `loading` flag used to mean both
        # "a snapshot is in flight" and "a mutation is in flight", so a caller
        # awaiting authoritative status could join a mutation and be released by
        # the mutation's completion. This executes the ten required interleavings
        # against the real gate policy, including the no-duplicate-snapshot and
        # no-stranded-continuation cases.
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_snapshot_ownership_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(helper + "\n" + harness, "V3_SNAPSHOT_OWNERSHIP_PASS")

    def test_response_encoding_classification_survives_the_wire(self):
        # V3_RESPONSE_CLASSIFICATION_CARRIER_V1: executes the REAL service
        # encoder and the REAL host reply classifier against each other over
        # real property-list bytes, using the exact production fallback shape
        # that carries BOTH a legacy "error" token and a structured "failure"
        # envelope. Also cross-checks the property-list leaf contract against
        # Foundation itself rather than a hardcoded expectation list.
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_response_classification_harness.swift").read_text(encoding="utf-8")
        # The classifier is the pure half of the bridge; the UIKit-dependent
        # bridge class cannot be compiled standalone, so only the pure
        # operations-and-errors context is taken.
        context = bridge[bridge.index("enum V3CatalogRequestContext {"):]
        context = context[:context.index("\n@MainActor")]
        self.compile_and_run(wire + "\n" + failure + "\n" + context + "\n" + harness,
                             "V3_RESPONSE_CLASSIFICATION_PASS")

    def test_operation_refresh_and_settings_state_machines_execute(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_release_behavior_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(helper + "\n" + failure + "\n" + harness, "V3_RELEASE_BEHAVIOR_PASS")

    def test_operation_pipeline_phase_and_progress_invariants_execute(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_operation_phase_progress_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness, "V3_OPERATION_PHASE_PROGRESS_PASS")

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
        begin = runtime.index("final class V3PromptCenter:")
        end = runtime.index("\n@MainActor\nfinal class V3HeadlessRuntime", begin)
        prompt_center = runtime[begin:end]
        harness = (ROOT / "tests/fixtures/v3_prompt_race_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run("import Foundation\n" + prompt_center + "\n" + harness,
                             "V3_PROMPT_RACE_PASS")

    def test_typed_authentication_provisioning_and_ppq_context_execute(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        begin = runtime.index("enum V3AuthFailureKind:")
        end = runtime.index("// MARK: - Provisioning failure guidance", begin)
        classifier = runtime[begin:end]
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_auth_classification_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run("import Foundation\n" + failure + "\n" + classifier + "\n" + harness,
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

    def test_setup_completion_status_issue_routing_and_jitless_execute(self):
        # V3_SETUP_COMPLETION_POLICY_V1, V3_RELOAD_STATUS_VISIBILITY_V1,
        # V3_USER_FACING_ISSUE_V1, V3_STATUS_PRESENTATION_V1,
        # V3_JITLESS_CERT_DISTINCTION_V1, V3_RELOAD_GATE_V1,
        # V3_SOURCE_EDITING_POLICY_V1
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_setup_and_semantic_ux_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness,
                             "V3_SETUP_AND_SEMANTIC_UX_PASS")

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
        harness = (ROOT / "tests/fixtures/v3_provisioning_typed_guidance_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run("import Foundation\n" + guidance + "\n" + policy + "\n" + harness,
                             "V3_PROVISIONING_TYPED_GUIDANCE_PASS")

    def test_auth_2fa_jitless_and_prerequisite_error_contracts_execute(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_auth_jitless_error_behavior_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(failure + "\n" + helper + "\n" + harness,
                             "V3_AUTH_2FA_JITLESS_AND_ERROR_BEHAVIOR_PASS")


if __name__ == "__main__":
    unittest.main()
