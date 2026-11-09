"""Exercise Dead10cc behavior from the exact maintained owner, not a patch generator.

The portable C probes execute extracted production blocks with OS doubles.
They are not a UIKit or physical-device claim. Source checkout is mandatory in CI.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.owner_source import owner_source as read_owner_source

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = "LiveContainer/Tweaks/Dead10ccFix.m"


def owner_source():
    return read_owner_source(SOURCE_PATH)


def descriptor_probe(source):
    start = source.index('            int fd = open(')
    end = source.index('            if ((fl.l_type', start)
    return source[start:end]


DESCRIPTOR_HARNESS = r'''
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


class OwnerSourceAdmissionTests(unittest.TestCase):
    def test_wrong_pin_and_modified_blob_are_rejected(self):
        expected = json.loads((ROOT/'ci/issues-native/candidate-graph.json').read_text())['owners']['LiveContainer'][1]
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)/SOURCE_PATH
            path.parent.mkdir(parents=True)
            path.write_bytes(b'owner')
            with patch.dict(os.environ, {'MAINTAINED_LIVE_CONTAINER_TEST_SOURCE': name}):
                with patch('subprocess.check_output', return_value='0'*40+'\n'):
                    with self.assertRaisesRegex(ValueError, 'current pinned owner'):
                        owner_source()
                for modified in (False, True):
                    path.write_bytes(b'modified' if modified else b'owner')
                    with patch('subprocess.check_output', side_effect=[expected+'\n', b'owner']):
                        if modified:
                            with self.assertRaisesRegex(ValueError, 'pinned Git blob'):
                                owner_source()
                        else:
                            self.assertEqual(owner_source(), 'owner')
                path.unlink()
                target = Path(name)/'linked'
                target.write_bytes(b'owner')
                path.symlink_to(target)
                with patch('subprocess.check_output', side_effect=[expected+'\n', b'owner']):
                    with self.assertRaisesRegex(ValueError, 'pinned Git blob'):
                        owner_source()


class Dead10ccFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = owner_source()
        cls.compiler = shutil.which('cc') or shutil.which('clang')
        if not cls.compiler:
            raise RuntimeError('C compiler is required for maintained-owner runtime probes')

    def run_c(self, source):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            path, binary = root/'probe.c', root/'probe'
            path.write_text(source)
            build = subprocess.run([self.compiler, str(path), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stderr)
            return subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)

    def test_descriptor_probe_executes_owner_and_detects_leak_regression(self):
        probe = descriptor_probe(self.source)
        self.assertEqual(probe.count('close(fd);'), 1)
        for label, value, expected in (
            ('owner', probe, 0),
            ('missing-close', probe.replace('close(fd);', ''), 17),
            ('reject-valid-fd-zero-and-one', probe.replace('fd < 0', 'fd <= 1'), 17),
        ):
            with self.subTest(label=label):
                result = self.run_c(DESCRIPTOR_HARNESS.replace('__PRODUCTION_PROBE__', value))
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_transition_gate_executes_owner_and_rejects_missing_reset(self):
        start = self.source.index('// DEAD10CC_TRANSITION_GATE_BEGIN')
        end = self.source.index('// DEAD10CC_TRANSITION_GATE_END', start)
        helper = self.source[start:end]
        harness = r'''
int main(void) {
    LCDead10ccTransitionGate gate = {0};
    if (LCDead10ccClaimBackgroundTransition(&gate) != 1) return 1;
    if (LCDead10ccClaimBackgroundTransition(&gate) != 0) return 2;
    LCDead10ccResetBackgroundTransition(&gate);
    if (LCDead10ccClaimBackgroundTransition(&gate) != 1) return 3;
    if (LCDead10ccClaimBackgroundTransition(&gate) != 0) return 4;
    return 0;
}
'''
        self.assertEqual(self.run_c(helper+harness).returncode, 0)
        reset = '__atomic_store_n(&gate->handled, 0, __ATOMIC_RELEASE);'
        self.assertEqual(helper.count(reset), 1)
        self.assertEqual(self.run_c(helper.replace(reset, '')+harness).returncode, 3)

    def test_resource_lifetimes_are_balanced_on_failure_and_success(self):
        text = self.source
        self.assertIn('if (pidinfo == NULL) return nil;', text)
        self.assertIn('if (pidinfo_size <= 0) {\n        free(pidinfo);\n        return nil;', text)
        self.assertIn('for (; count--; fdinfo++)', text)
        self.assertIn('    free(pidinfo);\n\n    NSMutableSet *lockedFilePaths', text)
        probe = descriptor_probe(text)
        self.assertLess(probe.index('close(fd);'), probe.index('if (lock == -1)'))

    def test_background_and_foreground_notifications_are_guest_scoped(self):
        text = self.source
        init = text[text.index('void initDead10ccFix(void)'):text.index('@implementation Dead10ccFix')]
        self.assertIn('if (!NSUserDefaults.isLiveProcess && !NSUserDefaults.isSharedApp) return;', init)
        self.assertEqual(init.count('addObserver:fix'), 4)
        for name in ('NSExtensionHostDidEnterBackgroundNotification',
                     'UIApplicationDidEnterBackgroundNotification',
                     'UIApplicationWillEnterForegroundNotification',
                     'NSExtensionHostDidBecomeActiveNotification'):
            self.assertIn(name, init)
        self.assertIn('LCDead10ccClaimBackgroundTransition(&_backgroundTransitionGate)', text)
        self.assertIn('LCDead10ccResetBackgroundTransition(&_backgroundTransitionGate)', text)

    def test_diagnostics_do_not_claim_interruption_or_fake_keepalive(self):
        for required in ('[LC_GUEST_LIFECYCLE]', 'BACKGROUND source=', 'DEAD10CC_PREPARATION pid='):
            self.assertIn(required, self.source)
        for forbidden in ('PROCESS_INTERRUPTED', 'AVAudioSession', 'beginBackgroundTask',
                          'setMinimumBackgroundFetchInterval', 'while (1)', 'while(true)',
                          'keepalive', 'keep-alive', 'wakeLock', 'idleTimerDisabled'):
            self.assertNotIn(forbidden, self.source)


if __name__ == '__main__':
    unittest.main()
