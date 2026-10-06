"""Pinned virtual launch terminal paths; no iOS/private-API acceptance claim."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("guest_launch_patch", ROOT / "scripts/patch_guest_return.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
PIN = "12377cf3b91d51739a33f14a302e5f522b238593"
DECORATED = "MultitaskSupport/DecoratedAppSceneViewController.m"


def block(text, anchor):
    start = text.index(anchor)
    opening = text.index("{", start)
    depth = 0
    for end in range(opening, len(text)):
        if text[end] == "{": depth += 1
        if text[end] == "}":
            depth -= 1
            if depth == 0: return text[start:end + 1]
    raise AssertionError("Unbalanced source block")


def pinned():
    root = os.environ.get("LIVE_CONTAINER_TEST_SOURCE")
    if not root:
        raise unittest.SkipTest("Pinned LiveContainer source is supplied by macOS CI")
    return {path: subprocess.check_output(["git", "-C", root, "show", PIN + ":" + path], text=True)
            for path in patch.PATHS}


def generated(original):
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        for relative, text in original.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        patch.patch(root)
        result = {relative: (root / relative).read_text() for relative in original}
        patch.patch(root)
        assert result == {relative: (root / relative).read_text() for relative in original}
        return result


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

    def test_pinned_baseline_omits_failure_completion_and_patch_routes_it(self):
        old = pinned()
        new = generated(old)
        anchor = "- (void)appSceneVC:(AppSceneViewController*)vc didInitializeWithError:"
        baseline = block(old[DECORATED], anchor)
        old_error_branch = block(baseline, "if(error)")
        self.assertNotIn("pidAvailableHandler", old_error_branch)
        corrected = block(new[DECORATED], anchor)
        self.assertIn("[self lcCompleteLaunch:vc error:launchError]", block(corrected, "if (launchError)"))
        helper = block(new[DECORATED], "- (void)lcCompleteLaunch:")
        self.assertLess(helper.index("self.lcLaunchSettled = YES"), helper.index("appTerminationCleanUp"))
        self.assertLess(helper.index("self.pidAvailableHandler = nil"), helper.index("appTerminationCleanUp"))
        self.assertLess(helper.index("appTerminationCleanUp"), helper.index("if (completion) completion"))
        self.assertIn("if (self.lcLaunchSettled) return", corrected)
        self.assertIn("error ? nil : @(controller.pid), error", helper)

    def test_pending_close_retires_controller_and_late_request_cannot_register(self):
        old = pinned()
        new = generated(old)
        old_close = block(old[DECORATED], "- (void)closeWindow")
        new_close = block(new[DECORATED], "- (void)closeWindow")
        self.assertIn("[self appSceneVCAppDidExit:self.appSceneVC]", old_close)
        self.assertNotIn("appTerminationCleanUp", old_close)
        self.assertIn("[self.appSceneVC appTerminationCleanUp]", new_close)
        self.assertIn("if (self.appSceneVC && !self.appSceneVC.isAppTerminationCleanUpCalled)", new_close)
        self.assertIn("[self appSceneVCAppDidExit:self.appSceneVC]", new_close)
        self.assertIn("@property(nonatomic, readonly) bool isAppTerminationCleanUpCalled;",
                      new["MultitaskSupport/AppSceneViewController.h"])
        implementation = new["MultitaskSupport/AppSceneViewController.m"]
        completion = implementation.split("beginExtensionRequestWithInputItems:@[item] completion:", 1)[1]
        self.assertLess(completion.index("if (self.isAppTerminationCleanUpCalled) return"),
                        completion.index("registerMultitaskContainer"))
        self.assertLess(patch.CLEANUP.index("unregisterMultitaskContainer"), patch.CLEANUP.index("appSceneVCAppDidExit"))
        self.assertIn(patch.VIRTUAL_LAUNCH_EXIT.strip(), block(new[DECORATED], "- (void)appSceneVCAppDidExit:"))

    def test_cancellation_retains_original_error_before_cleanup_and_is_main_owned(self):
        new = generated(pinned())
        cancellation = block(new["MultitaskSupport/AppSceneViewController.m"], "setRequestCancellationBlock:")
        self.assertIn("dispatch_async(dispatch_get_main_queue()", cancellation)
        self.assertLess(cancellation.index("weakSelf.lcLaunchError = error"), cancellation.index("appTerminationCleanUp"))
        self.assertIn("error:vc.lcLaunchError ?: ", patch.VIRTUAL_LAUNCH_EXIT)
        self.assertNotIn("NSCocoaErrorDomain", patch.VIRTUAL_LAUNCH_EXIT)
        self.assertNotIn("NSExecutableLoadError", patch.VIRTUAL_LAUNCH_EXIT)

    def test_partial_patch_fails_without_writes(self):
        original = pinned()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for relative, text in original.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            patch.patch(root)
            target = root / DECORATED
            target.write_text(target.read_text().replace("self.pidAvailableHandler = nil;", "// Missing consumption"))
            before = {p: (root / p).read_bytes() for p in original}
            with self.assertRaisesRegex(ValueError, "virtual guest launch completion"):
                patch.patch(root)
            self.assertEqual(before, {p: (root / p).read_bytes() for p in original})

    @unittest.skipUnless(sys.platform == "darwin", "Objective-C Foundation launch harness requires macOS")
    def test_actual_emitted_methods_settle_errors_close_reentry_and_success_once(self):
        compiler = shutil.which("clang")
        self.assertIsNotNone(compiler, "macOS CI must provide the Objective-C compiler")
        old = pinned()
        new = generated(old)
        harness = (ROOT / "tests/fixtures/guest_launch_completion_harness.m").read_text()
        anchors = ("- (void)closeWindow", "- (void)appSceneVCAppDidExit:",
                   "- (void)appSceneVC:(AppSceneViewController*)vc didInitializeWithError:")
        with tempfile.TemporaryDirectory() as name:
            for label, source, expected in (("baseline", old[DECORATED], 23), ("patched", new[DECORATED], 0)):
                methods = "\n".join(block(source, anchor) for anchor in anchors)
                if label == "patched":
                    methods = block(source, "- (void)lcCompleteLaunch:") + "\n" + methods
                file, exe = Path(name) / (label + ".m"), Path(name) / label
                file.write_text(harness.replace("// __PRODUCTION_METHODS__", methods))
                built = subprocess.run([compiler, "-fobjc-arc", "-fblocks", "-framework", "Foundation",
                                        "-framework", "CoreGraphics", str(file), "-o", str(exe)], capture_output=True, text=True)
                self.assertEqual(built.returncode, 0, built.stderr)
                result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                if label == "patched": self.assertIn("GUEST_LAUNCH_COMPLETION_PASS", result.stdout)


if __name__ == "__main__": unittest.main()
