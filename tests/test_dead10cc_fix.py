"""Regression coverage for the Dead10ccFix upstream backport (v3.0.3, issue #33).

Backports upstream LiveContainer fix e98699a ("Fix #1491: 0xdead10cc
regression") into the pinned LiveContainer tree: initDead10ccFix() must
register BOTH NSExtensionHostDidEnterBackgroundNotification and
UIApplicationDidEnterBackgroundNotification inside the original
LiveProcess/shared-guest scope, because either can fire
depending on Scene API. Duplicate notifications for one transition are gated.
"""
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dead10cc_patch", ROOT / "scripts/patch_dead10cc_fix.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)

PINNED_FIXTURE = '''@import Foundation;

@interface Dead10ccFix : NSObject
@property(nonatomic) BOOL methodInited;
@property(nonatomic) int deboundeToken;
@end

Dead10ccFix* fix = nil;

void initDead10ccFix(void) {

    if(NSUserDefaults.isLiveProcess) {
        fix = [[Dead10ccFix alloc] init];
        [NSNotificationCenter.defaultCenter addObserver:fix selector:@selector(handleAppDidEnterBackground:) name:NSExtensionHostDidEnterBackgroundNotification object:nil];
    } else if (NSUserDefaults.isSharedApp){
        fix = [[Dead10ccFix alloc] init];
        [NSNotificationCenter.defaultCenter addObserver:fix selector:@selector(handleAppDidEnterBackground:) name:@"UIApplicationDidEnterBackgroundNotification" object:nil];
    }
}

@implementation Dead10ccFix

- (void)handleAppDidEnterBackgroundReal {
    NSSet* locks = [self _lock_lockedFilePathsIgnoring:[NSMutableSet set]];
}

- (void)handleAppDidEnterBackground:(NSNotification *)notification {
    if(!_methodInited) {
    }
}

- (void)_terminateWithStatus:(int)status {
    // Fake implementation from UIApplication
    NSLog(@"[LC] _handleTaskCompletionAndTerminate");
}

@end
'''


class Dead10ccFixTests(unittest.TestCase):
    def test_pinned_source_patch_is_idempotent_and_scoped(self):
        source = os.getenv("LIVE_CONTAINER_TEST_SOURCE")
        if not source:
            self.skipTest("Pinned LiveContainer source is supplied by macOS CI")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            original = Path(source) / "LiveContainer/Tweaks/Dead10ccFix.m"
            target = root / "LiveContainer/Tweaks/Dead10ccFix.m"
            target.parent.mkdir(parents=True)
            shutil.copy2(original, target)
            patch.patch_dead10cc(root)
            first = target.read_bytes()
            patch.patch_dead10cc(root)
            self.assertEqual(first, target.read_bytes())
            text = target.read_text(encoding="utf-8")
            self.assertIn("!NSUserDefaults.isLiveProcess && !NSUserDefaults.isSharedApp", text)
            self.assertIn("LCDead10ccClaimBackgroundTransition", text)
            self.assertIn("handleAppWillEnterForeground", text)

    def test_registers_both_background_notifications(self):
        import tempfile
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "LiveContainer" / "Tweaks"
            target.mkdir(parents=True)
            (target / "Dead10ccFix.m").write_text(PINNED_FIXTURE, encoding="utf-8")
            patch.patch_dead10cc(root)
            text = (target / "Dead10ccFix.m").read_text(encoding="utf-8")
        self.assertIn("NSExtensionHostDidEnterBackgroundNotification", text)
        self.assertIn("UIApplicationDidEnterBackgroundNotification", text)
        # Both notifications stay inside the same original guest-process scope.
        self.assertIn("if (!NSUserDefaults.isLiveProcess && !NSUserDefaults.isSharedApp) return;", text)
        self.assertIn("handleAppWillEnterForeground:", text)

    def test_matches_upstream_fix_behavior(self):
        import tempfile
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "LiveContainer" / "Tweaks"
            target.mkdir(parents=True)
            (target / "Dead10ccFix.m").write_text(PINNED_FIXTURE, encoding="utf-8")
            patch.patch_dead10cc(root)
            text = (target / "Dead10ccFix.m").read_text(encoding="utf-8")
        # Single shared fix instance, paired notifications plus resume reset.
        self.assertEqual(text.count("addObserver:fix"), 4)
        self.assertIn("DEAD10CC_FIX_E98699A", text)
        self.assertIn("LCDead10ccClaimBackgroundTransition(&_backgroundTransitionGate)", text)

    def test_patch_is_idempotent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "LiveContainer" / "Tweaks"
            target.mkdir(parents=True)
            (target / "Dead10ccFix.m").write_text(PINNED_FIXTURE, encoding="utf-8")
            patch.patch_dead10cc(root)
            first = (target / "Dead10ccFix.m").read_text(encoding="utf-8")
            patch.patch_dead10cc(root)
            second = (target / "Dead10ccFix.m").read_text(encoding="utf-8")
        self.assertEqual(first, second)

    def test_no_fake_background_keepalive(self):
        source = (ROOT / "scripts/patch_dead10cc_fix.py").read_text(encoding="utf-8")
        for forbidden in ("silent audio", "AVAudioSession", "beginBackgroundTask",
                          "setMinimumBackgroundFetchInterval", "while (1)", "while(true)",
                          "keepalive", "keep-alive", "wakeLock", "idleTimerDisabled"):
            self.assertNotIn(forbidden, source)

    def test_lifecycle_diagnostics_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "LiveContainer" / "Tweaks"
            target.mkdir(parents=True)
            (target / "Dead10ccFix.m").write_text(PINNED_FIXTURE, encoding="utf-8")
            patch.patch_dead10cc(root)
            text = (target / "Dead10ccFix.m").read_text(encoding="utf-8")
        self.assertIn("[LC_GUEST_LIFECYCLE]", text)
        self.assertIn("BACKGROUND source=", text)
        self.assertIn("PROCESS_INTERRUPTED pid=", text)

    def test_shipped_template_references_both_observers(self):
        # The CI host-preflight greps enforce the same markers in the final
        # prepared tree; the template-level patch must contain them too.
        source = (ROOT / "scripts/patch_dead10cc_fix.py").read_text(encoding="utf-8")
        self.assertIn("NSExtensionHostDidEnterBackgroundNotification", source)
        self.assertIn("UIApplicationDidEnterBackgroundNotification", source)

    def test_transition_gate_executes_actual_patched_code(self):
        import shutil
        import subprocess
        import tempfile
        compiler = shutil.which("cc") or shutil.which("clang")
        if not compiler:
            self.skipTest("C compiler unavailable; transition gate executes in macOS CI")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "LiveContainer" / "Tweaks"
            target.mkdir(parents=True)
            source = target / "Dead10ccFix.m"
            source.write_text(PINNED_FIXTURE, encoding="utf-8")
            patch.patch_dead10cc(root)
            text = source.read_text(encoding="utf-8")
            helper = text[text.index("// DEAD10CC_TRANSITION_GATE_BEGIN"):text.index("// DEAD10CC_TRANSITION_GATE_END")]
            c_source = helper + r'''
#include <assert.h>
#include <stdio.h>
int main(void) {
    LCDead10ccTransitionGate gate = {0};
    assert(LCDead10ccClaimBackgroundTransition(&gate) == 1);
    assert(LCDead10ccClaimBackgroundTransition(&gate) == 0);
    LCDead10ccResetBackgroundTransition(&gate);
    assert(LCDead10ccClaimBackgroundTransition(&gate) == 1);
    assert(LCDead10ccClaimBackgroundTransition(&gate) == 0);
    puts("DEAD10CC_TRANSITION_GATE_PASS");
    return 0;
}
'''
            c_file = root / "gate.c"
            executable = root / "gate"
            c_file.write_text(c_source, encoding="utf-8")
            subprocess.run([compiler, str(c_file), "-o", str(executable)], check=True,
                           capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn("DEAD10CC_TRANSITION_GATE_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
