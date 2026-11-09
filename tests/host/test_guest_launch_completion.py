"""Maintained virtual launch terminal paths; no iOS/private-API acceptance claim."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests'))
from guest_owner_parts import implementations, block
DECORATED = "MultitaskSupport/DecoratedAppSceneViewController.m"


class GuestLaunchCompletionTests(unittest.TestCase):
    def test_changed_native_launch_sources_are_required_in_package_evidence(self):
        def load(name):
            spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        collector = load("combined_build_evidence")
        verifier = load("verify_candidate_ipa")
        changed = {"MultitaskSupport/AppSceneViewController.h",
                   "MultitaskSupport/AppSceneViewController.m", DECORATED}
        self.assertTrue(changed.issubset(collector.V3_HOST_SOURCE_PATHS))
        self.assertTrue(changed.issubset(verifier.REQUIRED_GENERATED_HOST_SOURCES))

    def test_current_failure_completion_settles_before_cleanup_reentry(self):
        new = implementations().sources
        anchor = "- (void)appSceneVC:(AppSceneViewController*)vc didInitializeWithError:"
        corrected = block(new[DECORATED], anchor)
        self.assertIn("[self lcCompleteLaunch:vc error:launchError]", block(corrected, "if (launchError)"))
        helper = block(new[DECORATED], "- (void)lcCompleteLaunch:")
        self.assertLess(helper.index("self.lcLaunchSettled = YES"), helper.index("appTerminationCleanUp"))
        self.assertLess(helper.index("self.pidAvailableHandler = nil"), helper.index("appTerminationCleanUp"))
        self.assertLess(helper.index("appTerminationCleanUp"), helper.index("if (completion) completion"))
        self.assertIn("if (self.lcLaunchSettled) return", corrected)
        self.assertIn("error ? nil : @(controller.pid), error", helper)

    def test_pending_close_retires_controller_and_late_request_cannot_register(self):
        new = implementations().sources
        new_close = block(new[DECORATED], "- (void)closeWindow")
        self.assertIn("[self.appSceneVC appTerminationCleanUp]", new_close)
        self.assertIn("if (self.appSceneVC && !self.appSceneVC.isAppTerminationCleanUpCalled)", new_close)
        self.assertIn("[self appSceneVCAppDidExit:self.appSceneVC]", new_close)
        self.assertIn("@property(nonatomic, readonly) bool isAppTerminationCleanUpCalled;",
                      new["MultitaskSupport/AppSceneViewController.h"])
        implementation = new["MultitaskSupport/AppSceneViewController.m"]
        completion = implementation.split("beginExtensionRequestWithInputItems:@[item] completion:", 1)[1]
        self.assertLess(completion.index("if (self.isAppTerminationCleanUpCalled) return"),
                        completion.index("registerMultitaskContainer"))
        self.assertLess(block(implementation, "- (void)appTerminationCleanUp").index("unregisterMultitaskContainer"), block(implementation, "- (void)appTerminationCleanUp").index("appSceneVCAppDidExit"))
        self.assertIn("[self lcCompleteLaunch:vc error:vc.lcLaunchError ?: ", block(new[DECORATED], "- (void)appSceneVCAppDidExit:"))

    def test_cancellation_retains_original_error_before_cleanup_and_is_main_owned(self):
        new = implementations().sources
        cancellation = block(new["MultitaskSupport/AppSceneViewController.m"], "setRequestCancellationBlock:")
        self.assertIn("dispatch_async(dispatch_get_main_queue()", cancellation)
        self.assertLess(cancellation.index("weakSelf.lcLaunchError = error"), cancellation.index("appTerminationCleanUp"))
        exit_body = block(new[DECORATED], "- (void)appSceneVCAppDidExit:")
        self.assertIn("error:vc.lcLaunchError ?: ", exit_body)
        self.assertNotIn("NSCocoaErrorDomain", exit_body)
        self.assertNotIn("NSExecutableLoadError", exit_body)

    def test_owner_completion_consumes_callback_before_reentry(self):
        helper = block(implementations().sources[DECORATED], '- (void)lcCompleteLaunch:')
        self.assertEqual(helper.count('self.pidAvailableHandler = nil'), 1)
        self.assertLess(helper.index('self.pidAvailableHandler = nil'), helper.index('appTerminationCleanUp'))

    @unittest.skipUnless(sys.platform == "darwin", "Objective-C Foundation launch harness requires macOS")
    def test_actual_owner_methods_settle_errors_close_reentry_and_success_once(self):
        compiler = shutil.which("clang")
        self.assertIsNotNone(compiler, "macOS CI must provide the Objective-C compiler")
        new = implementations().sources
        harness = (ROOT / "tests/fixtures/guest_launch_completion_harness.m").read_text()
        anchors = ("- (void)closeWindow", "- (void)appSceneVCAppDidExit:",
                   "- (void)appSceneVC:(AppSceneViewController*)vc didInitializeWithError:")
        with tempfile.TemporaryDirectory() as name:
            for label, source, expected in (("missing-callback", new[DECORATED].replace('if (completion) completion(error ? nil : @(controller.pid), error);', '/* callback omitted */'), 23), ("owner", new[DECORATED], 0)):
                methods = "\n".join(block(source, anchor) for anchor in anchors)
                methods = block(source, "- (void)lcCompleteLaunch:") + "\n" + methods
                file, exe = Path(name) / (label + ".m"), Path(name) / label
                file.write_text(harness.replace("// __PRODUCTION_METHODS__", methods))
                built = subprocess.run([compiler, "-fobjc-arc", "-fblocks", "-framework", "Foundation",
                                        "-framework", "CoreGraphics", str(file), "-o", str(exe)], capture_output=True, text=True)
                self.assertEqual(built.returncode, 0, built.stderr)
                result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                if label == "owner": self.assertIn("GUEST_LAUNCH_COMPLETION_PASS", result.stdout)


if __name__ == "__main__": unittest.main()
