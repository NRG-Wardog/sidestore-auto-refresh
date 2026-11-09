"""Maintained guest lifecycle pilot; not a UIKit/device qualification.

Legacy suites remain intact until broader behavioral coverage is reviewed.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests'))
from owner_source import owner_source, owner_root

SCENE = 'MultitaskSupport/AppSceneViewController.m'
DECORATED = 'MultitaskSupport/DecoratedAppSceneViewController.m'
DOCK = 'MultitaskSupport/MultitaskDockView.swift'


def block(source, anchor):
    if source.count(anchor) != 1:
        raise ValueError('ambiguous source anchor: '+anchor)
    start = source.index(anchor)
    opening = source.index('{', start)
    depth = 1
    for end in range(opening+1, len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if depth == 0:
            return source[start:end+1]
    raise ValueError('unbalanced source block')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def verify_input_boundary(scene):
    editing = block(scene, '- (void)lcEndGuestEditing')
    for token in ['NSThread.isMainThread', '[self.contentView endEditing:YES]',
                  '[self.viewIfLoaded endEditing:YES]', '[self.hostingController.sceneViewController resignFirstResponder]']:
        require(token in editing, 'missing scoped input cleanup')
    for token in ['dispatch_', 'connectedScenes', 'UIApplication', '_kill', 'removeFromSuperview']:
        require(token not in editing, 'input cleanup must not disturb another guest')
    for anchor, action in [('- (void)lcReturnToHost', 'self.lcActivateHost()'),
                           ('- (void)appTerminationCleanUp', '_isAppTerminationCleanUpCalled = true')]:
        body = block(scene, anchor)
        require('[self lcEndGuestEditing]' in body and body.index('[self lcEndGuestEditing]') < body.index(action),
                'input cleanup must precede lifecycle transition')
    cleanup = block(scene, '- (void)appTerminationCleanUp')
    require(cleanup.index('if (_isAppTerminationCleanUpCalled) return') < cleanup.index('[self lcEndGuestEditing]'),
            'reentrant cleanup must not steal new focus')


def verify_host_boundary(dock):
    prepare = block(dock, '@objc public func prepareVirtualWindowHost()')
    for token in ['precondition(Thread.isMainThread)', 'root.viewIfLoaded?.window === window',
                  'controller.parent !== root', 'guard windowHostingView.attach(toHostView: root.view)']:
        require(token in prepare, 'missing host admission boundary')
    require(prepare.index('controller.parent !== root') < prepare.index('windowHostingView.attach'), 'owner must be checked before attachment')
    require('removeFromParent' not in prepare, 'must not migrate live guest ownership')
    resume = block(dock, 'func bringMultitaskViewToFront(uuid:')
    require('guard prepareVirtualWindowHost() != nil, let window = targetView.window' in resume,
            'resume must recheck host before window use')


def verify_alert_boundary(source):
    finish = block(source, '- (BOOL)finishLaunchErrorAlert:')
    for token in ['NSThread.isMainThread', '!alert', 'self.launchErrorFinished',
                  'self.launchErrorAlert != alert', 'self.launchErrorGeneration != generation']:
        require(token in finish, 'stale alert must not own completion')
    require(finish.index('return NO') < finish.index('self.launchErrorFinished = YES'), 'guard precedes terminal change')
    require('self.launchErrorGeneration += 1' in finish, 'finish invalidates queued presentation')
    refresh = block(source, '- (void)refreshLocalizedLaunchError')
    require(refresh.count('strongSelf.launchErrorGeneration != generation') == 2, 'both asynchronous completions need generation guards')
    require(refresh.count('strongSelf.launchErrorAlert != previous') == 2, 'both asynchronous completions need alert identity guards')
    require('if (self.launchErrorFinished || !previous) return' in refresh, 'must not resurrect completed alert')
    for token in ['lcCompleteLaunch:', 'launchErrorFinished = NO', 'pidAvailableHandler', 'lcLaunchSettled =']:
        require(token not in refresh, 'locale rebuild must not restart launch settlement')
    alert = block(source, '- (UIAlertController *)localizedLaunchErrorAlert')
    require('if (![weakSelf finishLaunchErrorAlert:weakAlert generation:generation]) return' in alert,
            'stale copy must be ignored')
    require('UIPasteboard.generalPasteboard.string = details' in alert, 'copy keeps original diagnostic details')


class GuestOwnerSourceTests(unittest.TestCase):
    def test_input_cleanup_and_negative_control(self):
        source = owner_source(SCENE)
        verify_input_boundary(source)
        for broken in [source.replace('[self lcEndGuestEditing];', '/* removed */'),
                       source.replace('[self.contentView endEditing:YES]', '[UIApplication endEditing:YES]')]:
            with self.assertRaises(ValueError): verify_input_boundary(broken)

    def test_host_admission_and_negative_control(self):
        source = owner_source(DOCK)
        verify_host_boundary(source)
        for broken in [source.replace('prepareVirtualWindowHost() != nil, ', ''),
                       source.replace('controller.parent !== root', 'false')]:
            with self.assertRaises(ValueError): verify_host_boundary(broken)

    def test_alert_generation_and_negative_control(self):
        source = owner_source(DECORATED)
        verify_alert_boundary(source)
        for broken in [source.replace('strongSelf.launchErrorGeneration != generation', 'false', 1),
                       source.replace('self.launchErrorGeneration += 1', 'self.launchErrorGeneration += 0'),
                       source.replace('if (![weakSelf finishLaunchErrorAlert:weakAlert generation:generation]) return;', '')]:
            with self.assertRaises(ValueError): verify_alert_boundary(broken)


class GuestOwnerRuntimeTests(unittest.TestCase):
    def test_owner_launch_completion_with_existing_counterfactuals(self):
        # Verify every file the pinned runner reads or executes before running it.
        paths = [SCENE, DECORATED, 'tests/guest_launch_completion/fixture.m',
                 'tests/run_guest_launch_completion.py', 'LiveContainerSwiftUI/Models/LCAppModel.swift',
                 'LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBannerViewController.swift']
        for path in paths: owner_source(path)
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([sys.executable, str(owner_root()/'tests/run_guest_launch_completion.py'),
                            '--require-native', '--output', directory], check=True, capture_output=True, timeout=180)
            report = json.loads((Path(directory)/'report.json').read_text())
            self.assertEqual(report['status'], 'PASS')
            self.assertTrue(report['native_executed'])
            self.assertEqual(set(report['counterfactuals']), {'nil-guard', 'synchronous-interruption'})
            self.assertTrue(all(x['rejected'] for x in report['counterfactuals'].values()))

    def test_actual_alert_ownership_gate_rejects_stale_actions(self):
        clang = shutil.which('clang')
        if sys.platform != 'darwin' or not clang:
            raise RuntimeError('macOS clang is required for alert ownership runtime')
        method = block(owner_source(DECORATED), '- (BOOL)finishLaunchErrorAlert:')
        harness = r'''#import <Foundation/Foundation.h>
#include <assert.h>
@interface UIAlertController : NSObject @end
@implementation UIAlertController @end
@interface Owner : NSObject
@property BOOL launchErrorFinished;
@property NSUInteger launchErrorGeneration;
@property(strong) UIAlertController *launchErrorAlert;
@end
@implementation Owner
// ACTUAL_METHOD
@end
int main(void) { @autoreleasepool {
    Owner *owner = [Owner new];
    UIAlertController *current = [UIAlertController new], *old = [UIAlertController new];
    owner.launchErrorAlert = current; owner.launchErrorGeneration = 3;
    assert(![owner finishLaunchErrorAlert:nil generation:3]);
    assert(![owner finishLaunchErrorAlert:old generation:3]);
    assert(![owner finishLaunchErrorAlert:current generation:2]);
    assert(!owner.launchErrorFinished && owner.launchErrorAlert == current && owner.launchErrorGeneration == 3);
    assert([owner finishLaunchErrorAlert:current generation:3]);
    assert(owner.launchErrorFinished && owner.launchErrorAlert == nil && owner.launchErrorGeneration == 4);
    assert(![owner finishLaunchErrorAlert:current generation:3]);
} return 0; }
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for broken in [False, True]:
                actual = method.replace('self.launchErrorGeneration != generation', 'NO') if broken else method
                self.assertEqual(actual == method, not broken)
                (path/'gate.m').write_text(harness.replace('// ACTUAL_METHOD', actual))
                subprocess.run([clang, '-fobjc-arc', '-framework', 'Foundation', str(path/'gate.m'),
                                '-o', str(path/'gate')], check=True, capture_output=True, timeout=60)
                result = subprocess.run([str(path/'gate')], capture_output=True, timeout=15)
                self.assertEqual(result.returncode == 0, not broken)

    def test_current_control_methods_use_original_runtime_harness(self):
        clang = shutil.which('clang')
        if sys.platform != 'darwin' or not clang:
            raise RuntimeError('macOS clang is required for guest control runtime')
        source = owner_source(SCENE)
        fixture = (ROOT/'tests/fixtures/guest_return_control_harness.m').read_text()
        methods = {'// COLOR_DECODER': block(source, 'static UIColor *LCGuestReturnColor'),
                   '// COLLAPSE_METHOD': block(source, '- (void)collapse {'),
                   '// TAPPED_METHOD': block(source, '- (void)tapped {')}
        for marker, method in methods.items(): fixture = fixture.replace(marker, method)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for mutated in [False, True]:
                text = fixture
                if mutated:
                    text = text.replace('self.collapsed = NO;', 'self.collapsed = YES;')
                    self.assertNotEqual(text, fixture)
                (path/'control.m').write_text(text)
                subprocess.run([clang, '-fobjc-arc', '-fblocks', '-framework', 'Foundation',
                                str(path/'control.m'), '-o', str(path/'control')], check=True, capture_output=True, timeout=60)
                result = subprocess.run([str(path/'control')], capture_output=True, timeout=15)
                self.assertEqual(result.returncode == 0, not mutated)
                if not mutated: self.assertIn(b'RETURN_CONTROL_TESTS_PASSED', result.stdout)


if __name__ == '__main__': unittest.main()
