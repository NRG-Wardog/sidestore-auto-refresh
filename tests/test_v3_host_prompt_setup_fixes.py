import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"
HARNESS = ROOT / "tests/fixtures/v3_host_prompt_setup_fixes_harness.swift"
SWIFTC = shutil.which("swiftc")


def block(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end, text.index(start))]


class V3HostPromptSetupFixesTests(unittest.TestCase):
    def test_remove_all_is_a_host_action_and_reaches_backend_action_branch(self):
        shell = SHELL.read_text(encoding="utf-8")
        view = shell[shell.index("if isMulti {"):shell.index("\n            } else {\n                ForEach(options", shell.index("if isMulti {"))]
        self.assertIn("V3MultiSelectPromptAnswerPolicy.isMemberOption", view)
        self.assertIn('Button("Remove All", role: .destructive)', view)
        self.assertIn('actionAnswer("removeAll", fields: fields)', view)
        self.assertIn('actionAnswer("keepAll", fields: fields)', view)
        self.assertIn("selectedMembersAnswer(", view)

        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; executable routing harness runs in macOS CI")
        shell_policy = block(shell,
            "// V3_SETUP_READINESS_SNAPSHOT_POLICY_V1:",
            "enum V3AuthRetryReadinessReconciliationPolicy")
        runtime = RUNTIME.read_text(encoding="utf-8")
        backend_policy = block(runtime,
            "// V3_PROMPT_OPTION_IDENTITY_V1:",
            "// MARK: - Authentication failure classification")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "main.swift"
            executable = Path(temporary) / "host-prompt-setup-fixes"
            source.write_text(shell_policy + "\n" + backend_policy + "\n" +
                              HARNESS.read_text(encoding="utf-8"), encoding="utf-8")
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_HOST_PROMPT_SETUP_FIXES_PASS", result.stdout)

    def test_setup_recalculates_from_revisioned_readiness_snapshot(self):
        shell = SHELL.read_text(encoding="utf-8")
        status = shell[shell.index("final class V3SideStoreStatusStore"):]
        setup_start = status.index("final class V3SetupStore")
        setup = status[setup_start:status.index("struct V3SetupAssistantView", setup_start)]
        self.assertIn("@Published private(set) var jitlessReadinessObservation:", status)
        self.assertIn("sourceFactRevision: revision ?? setupFactRevision", status)
        self.assertIn("func awaitSharedSetupJITLessReadiness() async -> V3SetupReadinessObservation?", status)
        self.assertIn("let sharedReadiness = await status.awaitSharedSetupJITLessReadiness()", setup)
        self.assertIn("let currentFactRevision = status.currentSetupFactRevision", setup)
        self.assertIn("observationToReuse.activeCertificateAvailable", setup)
        self.assertIn("mayApplyFreshObservation", shell)

    def test_executable_snapshot_revision_interleavings(self):
        if not SWIFTC:
            self.skipTest("Swift compiler unavailable; executable snapshot harness runs in macOS CI")
        shell = SHELL.read_text(encoding="utf-8")
        shell_policy = block(shell,
            "// V3_SETUP_READINESS_SNAPSHOT_POLICY_V1:",
            "enum V3AuthRetryReadinessReconciliationPolicy")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "main.swift"
            executable = Path(temporary) / "setup-readiness-interleavings"
            source.write_text(shell_policy + "\n" + HARNESS.read_text(encoding="utf-8"),
                              encoding="utf-8")
            compiled = subprocess.run([SWIFTC, "-parse-as-library", str(source), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_HOST_PROMPT_SETUP_FIXES_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
