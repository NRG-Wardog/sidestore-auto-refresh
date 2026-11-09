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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from owner_source import owner_source


def bootstrap_parts():
    source = owner_source("LiveContainer/LCBootstrap.m")
    helper_start = source.index("#include <stdbool.h>")
    runtime_start = source.index("_Static_assert(sizeof(vm_address_t)", helper_start)
    runtime_end = source.index("void overwriteMainNSBundle(", runtime_start)
    call_start = source.index("    // Resolve the existing CF cache before changing NSBundle identity.")
    call_end = source.index("\n    }", source.index('if (!overwriteMainCFBundle(mainCFBundleAddress))', call_start)) + len("\n    }")
    return source[helper_start:runtime_start], source[runtime_start:runtime_end], source[call_start:call_end]


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
        generated = owner_source("LiveContainer/LCBootstrap.m")
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
        helper, actual_runtime, actual_caller = bootstrap_parts()
        runtime = actual_runtime.replace("(__bridge void *)NSBundle.mainBundle._cfBundle", "testGuestBundle")
        caller = actual_caller.replace('return @"', 'return "')
        harness = (ROOT / "tests/fixtures/cf_bundle_runtime_harness.c").read_text()
        harness = harness.replace("__PRODUCTION_HELPER__", helper)
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
            broken = harness.replace('if (!overwriteMainCFBundle(mainCFBundleAddress))', 'if (false)')
            self.assertNotEqual(broken, harness)
            source.write_text(broken)
            subprocess.run([compiler, "-std=c11", str(source), "-o", str(exe)], check=True, capture_output=True)
            def no_core(): resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            failed = subprocess.run([str(exe)], capture_output=True, timeout=5, preexec_fn=no_core)
            self.assertNotEqual(failed.returncode, 0, 'unchecked failed CFBundle write must fail the harness')

    def test_current_adapter_is_checked_and_call_order_is_fail_closed(self):
        source = owner_source("LiveContainer/LCBootstrap.m")
        helper, runtime, caller = bootstrap_parts()
        self.assertLess(caller.index("if (!mainCFBundleAddress)"), caller.index("overwriteMainNSBundle(appBundle)"))
        self.assertIn("if (!overwriteMainCFBundle(mainCFBundleAddress))", caller)
        for token in ["vm_read_overwrite", "copied == size", "vm_write", "observed != (void *)expected"]:
            self.assertIn(token, runtime)
        for token in ["mach_vm", "vm_protect", "*mainBundleAddr ="]:
            self.assertNotIn(token, runtime)
        self.assertNotRegex(runtime, r"\bassert\(")
        self.assertNotIn("while (true)", helper)

    def test_guard_pages_prove_baseline_failure_and_bounded_scan_success(self):
        compiler = shutil.which("cc")
        if not compiler: self.skipTest("C compiler unavailable")
        utils = owner_source("LiveContainer/utils.m")
        emulators = "\n".join(block(utils, signature) for signature in (
            "uint64_t aarch64_get_tbnz_jump_address(",
            "uint64_t aarch64_emulate_adrp(",
            "uint64_t aarch64_emulate_adrp_ldr("))
        helper, _, _ = bootstrap_parts()
        harness = (ROOT / "tests/fixtures/cf_bundle_scan_harness.c").read_text()
        harness = harness.replace("__PRODUCTION_EMULATORS__", emulators)
        harness = harness.replace("__PRODUCTION_HELPER__", helper)
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
