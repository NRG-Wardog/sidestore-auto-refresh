"""Execute the actual scan with guard pages; not an iOS compatibility claim."""
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import patch_cf_bundle_scan as patch

PIN = "12377cf3b91d51739a33f14a302e5f522b238593"


def upstream(path):
    root = os.environ.get("LIVE_CONTAINER_TEST_SOURCE")
    if not root:
        raise unittest.SkipTest("Pinned LiveContainer source is supplied by macOS CI")
    return subprocess.check_output(["git", "-C", root, "show", PIN + ":" + path], text=True)


def block(source, anchor, occurrence=0):
    start = -1
    for _ in range(occurrence + 1):
        start = source.index(anchor, start + 1)
    opening = source.index("{", start)
    depth = 0
    for end in range(opening, len(source)):
        if source[end] == "{": depth += 1
        if source[end] == "}":
            depth -= 1
            if depth == 0: return source[start:end + 1]
    raise AssertionError("unbalanced source block")


class CFBundleScanTests(unittest.TestCase):
    def test_runtime_failure_returns_before_launch_success_or_unchecked_write(self):
        compiler = shutil.which("cc")
        if not compiler: self.skipTest("C compiler unavailable")
        # Only the ObjC getter expression and NSString literal syntax are
        # replaced by C surface doubles; production adapter/control flow stays.
        runtime = patch.RUNTIME.replace("(__bridge void *)NSBundle.mainBundle._cfBundle", "testGuestBundle")
        caller = patch.NEW_CALL.replace('return @"', 'return "')
        harness = (ROOT / "tests/fixtures/cf_bundle_runtime_harness.c").read_text()
        harness = harness.replace("__PRODUCTION_HELPER__", patch.TEMPLATE.read_text())
        harness = harness.replace("__PRODUCTION_RUNTIME_WITH_NS_BUNDLE_DOUBLE__", runtime)
        harness = harness.replace("__PRODUCTION_CALLER_WITH_STRING_LITERALS__", caller)
        with tempfile.TemporaryDirectory() as name:
            source, exe = Path(name) / "runtime.c", Path(name) / "runtime"
            source.write_text(harness)
            built = subprocess.run([compiler, "-std=c11", str(source), "-o", str(exe)],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CF_BUNDLE_FAILURE_PROPAGATION_PASS", result.stdout)

    def test_pinned_patch_is_idempotent_and_preserves_surrounding_source(self):
        original = upstream("LiveContainer/LCBootstrap.m")
        generated = patch.patch_text(original)
        self.assertEqual(patch.patch_text(generated), generated)
        old_start = original.index("void overwriteMainCFBundle(void)")
        new_start = generated.index("#include <stdbool.h>")
        self.assertEqual(original[:old_start], generated[:new_start].replace("#include <mach/mach_vm.h>\n", ""))
        original_tail = original[original.index("void overwriteMainNSBundle("):]
        generated_tail = generated[generated.index("void overwriteMainNSBundle("):]
        self.assertEqual(generated_tail.replace(patch.NEW_CALL, patch.OLD_CALL), original_tail)
        self.assertLess(generated.index("if (!mainCFBundleAddress)"),
                        generated.index("    overwriteMainNSBundle(appBundle);"))
        self.assertIn("if (!overwriteMainCFBundle(mainCFBundleAddress))", generated)
        self.assertIn("mach_vm_read_overwrite", generated)
        self.assertIn("mach_vm_write", generated)
        self.assertNotIn("vm_protect", patch.RUNTIME)
        self.assertNotIn("assert(", patch.RUNTIME)
        self.assertNotIn("*mainBundleAddr =", generated)

    def test_drift_or_partial_patch_fails_without_writing(self):
        original = upstream("LiveContainer/LCBootstrap.m")
        generated = patch.patch_text(original)
        for broken in (original.replace("while (true)", "for (;;)", 1),
                       generated.replace("mach_vm_read_overwrite", "unchecked_read", 1),
                       generated.replace(patch.NEW_CALL, patch.OLD_CALL)):
            with tempfile.TemporaryDirectory() as name:
                path = Path(name) / "LCBootstrap.m"
                path.write_text(broken)
                with self.assertRaises(ValueError): patch.patch_bootstrap(path)
                self.assertEqual(path.read_text(), broken)

    def test_guard_pages_prove_baseline_failure_and_bounded_scan_success(self):
        compiler = shutil.which("cc")
        if not compiler: self.skipTest("C compiler unavailable")
        original = upstream("LiveContainer/LCBootstrap.m")
        old = block(original, "void overwriteMainCFBundle(void)")
        utils = upstream("LiveContainer/utils.m")
        emulators = "\n".join(block(utils, signature) for signature in (
            "uint64_t aarch64_get_tbnz_jump_address(",
            "uint64_t aarch64_emulate_adrp(",
            "uint64_t aarch64_emulate_adrp_ldr("))
        helper = patch.TEMPLATE.read_text()
        self.assertIn(helper, patch.patch_text(original))
        harness = (ROOT / "tests/fixtures/cf_bundle_scan_harness.c").read_text()
        harness = harness.replace("__PRODUCTION_EMULATORS__", emulators)
        harness = harness.replace("__PRODUCTION_HELPER__", helper)
        harness = harness.replace("__BASELINE_MODERN_LOOP__", block(old, "while (true)", 0))
        harness = harness.replace("__BASELINE_LEGACY_LOOP__", block(old, "while (true)", 1))
        with tempfile.TemporaryDirectory() as name:
            source, exe = Path(name) / "scan.c", Path(name) / "scan"
            source.write_text(harness)
            built = subprocess.run([compiler, "-std=c11", "-D_DEFAULT_SOURCE", str(source), "-o", str(exe)],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            def no_core(): resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            for scenario in ("none", "first", "last", "target"):
                with self.subTest(scenario=scenario):
                    baseline = subprocess.run([str(exe), "baseline-" + scenario],
                                              capture_output=True, timeout=5, preexec_fn=no_core)
                    self.assertIn(baseline.returncode, (-signal.SIGSEGV, -signal.SIGBUS))
                    fixed = subprocess.run([str(exe), "fixed-" + scenario], capture_output=True, timeout=5)
                    self.assertEqual(fixed.returncode, 0, fixed.stderr)
            positive = subprocess.run([str(exe), "fixed-positive"], capture_output=True, text=True, timeout=5)
            self.assertEqual(positive.returncode, 0, positive.stderr)
            self.assertIn("CF_BUNDLE_BOUNDED_SCAN_PASS", positive.stdout)


if __name__ == "__main__":
    unittest.main()
