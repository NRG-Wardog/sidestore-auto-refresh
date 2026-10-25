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
    @unittest.skipUnless(sys.platform == "darwin", "iPhoneOS SDK compile requires macOS/Xcode")
    def test_production_adapter_compiles_against_actual_iphoneos_sdk(self):
        # Use the real target SDK and unmodified ObjC/Mach adapter, not the
        # host-only C syscall doubles below. Missing SDK/toolchain is a failure
        # on macOS CI, never an additional skip.
        sdk = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"],
                                      text=True).strip()
        original = upstream("LiveContainer/LCBootstrap.m")
        generated = patch.patch_text(original)
        start = generated.index("#include <stdbool.h>")
        end = generated.index("void overwriteMainNSBundle(", start)
        adapter = generated[start:end]
        declarations = '''#import <Foundation/Foundation.h>
#import <CoreFoundation/CoreFoundation.h>
#include <mach/mach.h>
#include <stdint.h>
@interface NSBundle (CFBundleSDKProbe)
- (id)_cfBundle;
@end
uint64_t aarch64_get_tbnz_jump_address(uint32_t, uint64_t);
uint64_t aarch64_emulate_adrp_ldr(uint32_t, uint32_t, uint64_t);
'''
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / "CFBundleSDKProbe.m"
            source.write_text(declarations + adapter)
            built = subprocess.run([
                "xcrun", "--sdk", "iphoneos", "clang", "-target", "arm64-apple-ios15.0",
                "-isysroot", sdk, "-fobjc-arc", "-fsyntax-only",
                "-Werror=implicit-function-declaration", "-Werror=shorten-64-to-32",
                str(source)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)

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
        self.assertEqual(original[:old_start], generated[:new_start])
        original_tail = original[original.index("void overwriteMainNSBundle("):]
        generated_tail = generated[generated.index("void overwriteMainNSBundle("):]
        self.assertEqual(generated_tail.replace(patch.NEW_CALL, patch.OLD_CALL), original_tail)
        self.assertLess(generated.index("if (!mainCFBundleAddress)"),
                        generated.index("    overwriteMainNSBundle(appBundle);"))
        self.assertIn("if (!overwriteMainCFBundle(mainCFBundleAddress))", generated)
        self.assertIn("vm_read_overwrite", generated)
        self.assertIn("vm_write", generated)
        self.assertNotIn("#include <mach/mach_vm.h>", generated)
        self.assertNotIn("mach_vm", patch.RUNTIME)
        self.assertNotIn("vm_protect", patch.RUNTIME)
        self.assertNotRegex(patch.RUNTIME, r"\bassert\(")
        self.assertNotIn("*mainBundleAddr =", generated)

    def test_drift_or_partial_patch_fails_without_writing(self):
        original = upstream("LiveContainer/LCBootstrap.m")
        generated = patch.patch_text(original)
        for broken in (original.replace("while (true)", "for (;;)", 1),
                       generated.replace("vm_read_overwrite", "unchecked_read", 1),
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
