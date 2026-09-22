"""Regression coverage for the Dead10ccFix upstream backport (v3.0.3, issue #33).

Backports upstream LiveContainer fix e98699a ("Fix #1491: 0xdead10cc
regression") into the pinned LiveContainer tree: initDead10ccFix() must
register BOTH NSExtensionHostDidEnterBackgroundNotification and
UIApplicationDidEnterBackgroundNotification regardless of guest mode,
because either can fire depending on Scene API.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dead10cc_patch", ROOT / "scripts/patch_dead10cc_fix.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)

PINNED_FIXTURE = '''@import Foundation;

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
        # Conditional single registration is gone; both observers are unconditional.
        self.assertNotIn("isLiveProcess) {", text)
        self.assertNotIn("isSharedApp){", text)

    def test_matches_upstream_fix_behavior(self):
        import tempfile
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "LiveContainer" / "Tweaks"
            target.mkdir(parents=True)
            (target / "Dead10ccFix.m").write_text(PINNED_FIXTURE, encoding="utf-8")
            patch.patch_dead10cc(root)
            text = (target / "Dead10ccFix.m").read_text(encoding="utf-8")
        # Single shared fix instance, both observers on it.
        self.assertEqual(text.count("addObserver:fix"), 2)
        self.assertIn("DEAD10CC_FIX_E98699A", text)

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


if __name__ == "__main__":
    unittest.main()
