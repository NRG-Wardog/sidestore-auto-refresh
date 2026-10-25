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

RESOURCE_SCANNER = '''- (NSMutableSet *)_lock_lockedFilePathsIgnoring:(NSMutableSet *)ignoring {
    void *pidinfo = malloc(pidinfo_size);
    pidinfo_size = proc_pidinfo(pid, PROC_PIDLISTFDS, 0, pidinfo, pidinfo_size);
    if (pidinfo_size >= 8) {
        struct proc_fdinfo *fdinfo = (struct proc_fdinfo *)pidinfo;
        while (count--) {
            fdinfo++;
        }
    }

    NSMutableSet *lockedFilePaths = [NSMutableSet set];
    for (NSString *path in openFilePaths) {
            int fd = open(path_c, O_RDONLY | O_NOCTTY);
            if (fd <= 1) {
                continue;
            }

            struct flock fl;
            memset(&fl, 0, sizeof(fl));
            fl.l_type = F_WRLCK;
            fl.l_pid = pid;

            int lock = fcntl(fd, F_GETLKPID, &fl);
            if (lock == -1) {
                continue;
            }

            if ((fl.l_type &~ F_UNLCK) == 1) {
                [lockedFilePaths addObject:path];
            }
    }
    return lockedFilePaths;
}
'''

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
PINNED_FIXTURE = PINNED_FIXTURE.replace("@implementation Dead10ccFix\n",
                                      "@implementation Dead10ccFix\n\n" + RESOURCE_SCANNER)


class Dead10ccFixTests(unittest.TestCase):
    def test_scan_balances_allocation_before_lock_detection(self):
        generated = patch.patch_resource_lifetimes(RESOURCE_SCANNER)
        self.assertEqual(generated, patch.patch_resource_lifetimes(generated))
        self.assertIn("if (pidinfo == NULL) return nil;", generated)
        self.assertLess(generated.index("free(pidinfo);"),
                        generated.index("NSMutableSet *lockedFilePaths"))
        self.assertLess(generated.index("close(fd);"), generated.index("if (lock == -1)"))
        self.assertEqual(generated.count("free(pidinfo);"), 1)
        self.assertEqual(generated.count("close(fd);"), 1)

    def test_resource_patch_fails_closed_on_drift_or_partial_prepared_source(self):
        generated = patch.patch_resource_lifetimes(RESOURCE_SCANNER)
        for broken in (RESOURCE_SCANNER.replace("fdinfo++;", "fdinfo += 1;"),
                       generated.replace("    free(pidinfo);\n", ""),
                       generated.replace("            close(fd);\n", "")):
            with self.assertRaises(SystemExit):
                patch.patch_resource_lifetimes(broken)

    def test_existing_observer_patch_is_upgraded_without_duplicate_observers(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "LiveContainer/Tweaks/Dead10ccFix.m"
            source.parent.mkdir(parents=True)
            source.write_text(PINNED_FIXTURE)
            patch.patch_dead10cc(root)
            expected = source.read_text()
            legacy = expected.replace(patch.patch_resource_lifetimes(RESOURCE_SCANNER),
                                      RESOURCE_SCANNER)
            self.assertIn(patch.MARKER, legacy)
            self.assertNotIn(patch.RESOURCE_MARKER, legacy)
            source.write_text(legacy)
            patch.patch_dead10cc(root)
            self.assertEqual(source.read_text(), expected)
            patch.verify(root)

    def test_descriptor_probe_executes_generated_code_and_baseline_leaks(self):
        import subprocess
        compiler = shutil.which("cc") or shutil.which("clang")
        if not compiler:
            self.skipTest("C compiler unavailable")

        def probe(source):
            start = source.index("            int fd = open(")
            end = source.index("            if ((fl.l_type", start)
            return source[start:end]

        # The open/fcntl/close production block is unchanged in this extraction.
        # Only OS functions/types are doubles; no UIKit runtime is simulated.
        harness = r'''
#include <assert.h>
#include <string.h>
enum { O_RDONLY = 0, O_NOCTTY = 1, F_WRLCK = 2, F_GETLKPID = 3 };
struct flock { int l_type; int l_pid; };
static int next_fd, next_result, outstanding, opens, closes, calls;
static int open(const char *path, int flags) {
    opens++;
    if (next_fd >= 0) outstanding++;
    return next_fd;
}
static int fcntl(int fd, int command, struct flock *fl) {
    assert(fd == next_fd && fd >= 0);
    calls++;
    return next_result;
}
static int close(int fd) {
    assert(fd == next_fd && fd >= 0 && outstanding == 1);
    outstanding--; closes++; return 0;
}
int main(void) {
    const char *path_c = "fixture"; int pid = 123;
    int descriptors[] = {-1, 0, 1, 2, 42};
    for (int i = 0; i < 5; ++i) {
        for (int failure = 0; failure < 2; ++failure) {
            next_fd = descriptors[i]; next_result = failure ? -1 : 0;
            outstanding = opens = closes = calls = 0;
            for (int path = 0; path < 1; ++path) {
__PRODUCTION_PROBE__
            }
            if (outstanding != 0) return 17;
            assert(opens == 1);
            assert(closes == (next_fd >= 0));
            assert(calls == (next_fd >= 0));
        }
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for label, source, expected in (
                ("baseline", RESOURCE_SCANNER, 17),
                ("patched", patch.patch_resource_lifetimes(RESOURCE_SCANNER), 0),
            ):
                cfile, executable = root / (label + ".c"), root / label
                cfile.write_text(harness.replace("__PRODUCTION_PROBE__", probe(source)))
                built = subprocess.run([compiler, str(cfile), "-o", str(executable)],
                                       capture_output=True, text=True)
                self.assertEqual(built.returncode, 0, built.stderr)
                run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=5)
                self.assertEqual(run.returncode, expected, label + run.stderr)

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
            self.assertIn("free(pidinfo);", text)
            self.assertIn("close(fd);", text)
            # The C harness exercises exactly this emitted resource probe,
            # not a rewritten model of its descriptor ownership.
            start, end = "            int fd = open(", "            if ((fl.l_type"
            actual_probe = text[text.index(start):text.index(end, text.index(start))]
            fixture = patch.patch_resource_lifetimes(RESOURCE_SCANNER)
            fixture_probe = fixture[fixture.index(start):fixture.index(end, fixture.index(start))]
            self.assertEqual(actual_probe, fixture_probe)

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
        self.assertIn("- (void)handleAppWillEnterForeground:(NSNotification *)notification;", text)

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
        self.assertIn("DEAD10CC_PREPARATION pid=", text)
        self.assertNotIn("PROCESS_INTERRUPTED", text)

    def test_prepared_tree_upgrades_false_interruption_diagnostic(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "LiveContainer/Tweaks/Dead10ccFix.m"
            source.parent.mkdir(parents=True)
            source.write_text(PINNED_FIXTURE)
            patch.patch_dead10cc(root)
            expected = source.read_text()
            source.write_text(expected.replace("DEAD10CC_PREPARATION pid=", "PROCESS_INTERRUPTED pid="))
            patch.patch_dead10cc(root)
            self.assertEqual(source.read_text(), expected)
            patch.verify(root)

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
