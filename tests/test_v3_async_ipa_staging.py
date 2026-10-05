"""Execute production staging ownership and detached-copy regressions.

Source guards also run without Swift. macOS CI runs the actual FileManager /
NSFileCoordinator harness; actor/ownership tests need only Swift Foundation.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "scripts/templates"
sys.path.insert(0, str(ROOT / "tests"))
from test_combined_service_startup import _matching_swift_brace


def declaration(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    return source[start:_matching_swift_brace(source, opening)]


class AsyncIPAStagingTests(unittest.TestCase):
    def setUp(self):
        self.host = (TEMPLATES / "v3_unified_shell.swift").read_text()
        self.staging = (TEMPLATES / "v3_ipa_staging.swift").read_text()

    def compile_run(self, source, marker):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; executable staging tests run in macOS CI")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "staging.swift"
            binary = Path(directory) / "staging"
            path.write_text(source)
            result = subprocess.run([compiler, "-parse-as-library", str(path), "-o", str(binary)],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(marker, result.stdout)

    def test_copy_runs_in_detached_worker_with_value_inputs_and_cancellation_cleanup(self):
        host = declaration(self.host, "private func stageIPA(")
        worker = declaration(self.staging, "static func stageOffMainActor(")
        self.assertIn("try await V3IPAStaging.stageOffMainActor(", host)
        self.assertNotIn("try V3IPAStaging.stage(", host)
        self.assertIn("Task.detached(priority: .userInitiated)", worker)
        self.assertIn("withTaskCancellationHandler", worker)
        self.assertIn("worker.cancel()", worker)
        self.assertIn("try Task.checkCancellation()", worker)
        self.assertIn("try? cleanup(token: token, containerRoot: containerRoot)", worker)
        self.assertNotIn("self", worker)
        self.assertIn("self.installAttempt.attemptID == attemptID", host)
        self.assertIn("self.installAttempt.phase == .staging", host)
        self.assertIn("!Task.isCancelled", host)
        self.assertIn("cleanupUnclaimedOffMainActor(token: token, containerRoot: container)", host)

    def test_picker_dismissal_and_cancel_are_independent_of_copy_completion(self):
        picker = declaration(self.host, "func documentPicker(_ controller:")
        self.assertIn("stagePickerIPA(url, attemptID: attemptID) == true", picker)
        self.assertIn("dismissPicker(controller, attemptID: attemptID)", picker)
        self.assertIn("dismissedIPAStagingAttemptID = attemptID",
                      declaration(self.host, "func installPickerDidDisappear("))
        self.assertIn("ipaStagingTask?.task.cancel()", declaration(self.host, "func resetInstallUI("))
        self.assertIn('Button("Cancel") { status.cancelIPAStaging() }', self.host)
        self.assertIn('accessibilityIdentifier("V3_IPA_STAGING_CANCEL")', self.host)

    def test_scope_coordinator_and_partial_file_stay_owned_until_copy_finishes(self):
        stage = declaration(self.staging, "static func stage(sourceURL:")
        self.assertLess(stage.index("startAccessingSecurityScopedResource()"),
                        stage.index("coordinator.coordinate("))
        self.assertIn("defer { if scoped { source.stopAccessingSecurityScopedResource() } }", stage)
        self.assertIn("NSFileCoordinator(filePresenter: nil)", stage)
        self.assertIn("fileManager.copyItem(at: readableURL, to: partial)", stage)
        self.assertLess(stage.index("requireRegularNonEmptyFile(partial"),
                        stage.index("fileManager.moveItem(at: partial, to: destination)"))
        self.assertIn("sourceValues.isSymbolicLink != true", stage)
        self.assertIn("removePartial(partialDestination", stage)
        self.assertIn('file.pathExtension == "ipa"', declaration(self.staging, "static func cleanupOrphans("))

    def test_abandoned_partial_cleanup_requires_nonblocking_process_lease(self):
        lease = declaration(self.staging, "private static func acquireCopyLease(")
        cleanup = declaration(self.staging, "static func cleanupOrphans(")
        self.assertIn("LOCK_EX | LOCK_NB", lease)
        self.assertIn("O_NOFOLLOW", lease)
        self.assertIn("opened.st_dev == named.st_dev", lease)
        self.assertIn("opened.st_ino == named.st_ino", lease)
        self.assertIn('file.pathExtension == "lease"', cleanup)
        self.assertIn("create: false", cleanup)
        self.assertIn("defer { releaseCopyLease(lease) }", cleanup)
        self.assertIn("!preservingTokens.contains(token)", cleanup)

    def test_actual_copy_leases_exclude_other_processes_and_reclaim_crash_orphans(self):
        methods = [declaration(self.staging, signature) for signature in (
            "static func canonicalToken(", "static func stagingDirectory(",
            "private static func ensureDirectory(", "private static func copyLeaseURL(",
            "private static func acquireCopyLease(", "private static func releaseCopyLease(",
            "static func cleanupOrphans(")]
        fixture = (ROOT / "tests/fixtures/v3_ipa_copy_lease_harness.swift").read_text()
        self.compile_run(fixture.replace("$LEASE_METHODS$", "\n".join(methods)), "V3_IPA_COPY_LEASE_PASS")

    def test_actual_host_methods_reject_late_results_and_preserve_new_attempt(self):
        model = (TEMPLATES / "v3_behavioral_primitives.swift").read_text()
        declarations = [declaration(model, "struct V3InstallPresentationRequest:"),
                        declaration(model, "struct V3InstallAttemptState {")]
        methods = [declaration(self.host, signature) for signature in (
            "func cancelInstallPicker(", "func cancelIPAStaging()", "func stagePickerIPA(",
            "func stageSharedIPA(", "private func stageIPA(", "private func failIPAStaging(",
            "private func drainInstallPresentation(", "func installPickerDidDisappear(",
            "func resetInstallUI(")]
        fixture = (ROOT / "tests/fixtures/v3_async_ipa_host_harness.swift").read_text()
        fixture = fixture.replace("$STATE$", "\n".join(declarations))
        fixture = fixture.replace("$HOST_METHODS$", "\n".join(methods))
        self.compile_run(fixture, "V3_ASYNC_IPA_HOST_PASS")

    def test_real_coordinated_copy_cancellation_and_io_failures(self):
        if sys.platform != "darwin":
            self.skipTest("NSFileCoordinator and security-scoped URL APIs require macOS")
        # The production worker remains unchanged. Only its synchronous file
        # dependency is wrapped to inject a controllable FileManager subclass.
        native = self.staging.replace("static func stage(sourceURL:", "static func stageNative(sourceURL:", 1)
        native = native.replace("NSFileCoordinator(filePresenter: nil)", "CoordinatedFileAccess(filePresenter: nil)")
        fixture = (ROOT / "tests/fixtures/v3_async_ipa_copy_harness.swift").read_text()
        fixture = fixture.replace("$STAGING$", native)
        self.compile_run(fixture, "V3_ASYNC_IPA_COPY_PASS")


if __name__ == "__main__":
    unittest.main()
