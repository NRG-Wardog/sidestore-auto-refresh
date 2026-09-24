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

    def test_operation_refresh_and_settings_state_machines_execute(self):
        helper = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        harness = (ROOT / "tests/fixtures/v3_release_behavior_harness.swift").read_text(encoding="utf-8")
        self.compile_and_run(helper + "\n" + failure + "\n" + harness, "V3_RELEASE_BEHAVIOR_PASS")

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


if __name__ == "__main__":
    unittest.main()
