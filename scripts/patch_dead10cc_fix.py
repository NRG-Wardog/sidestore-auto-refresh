#!/usr/bin/env python3
"""Backport upstream Dead10ccFix fix (e98699a) to pinned LiveContainer.

Upstream fix e98699a "Fix #1491: 0xdead10cc regression" registers BOTH
NSExtensionHostDidEnterBackgroundNotification AND UIApplicationDidEnterBackgroundNotification
because either can fire depending on Scene API.
"""
from __future__ import annotations

from pathlib import Path
import sys


MARKER = "DEAD10CC_FIX_E98699A"
TRANSITION_MARKER = "DEAD10CC_TRANSITION_GATE_V1"
TRANSITION_HELPER = '''// DEAD10CC_TRANSITION_GATE_V1
// DEAD10CC_TRANSITION_GATE_BEGIN
typedef struct { int handled; } LCDead10ccTransitionGate;
static int LCDead10ccClaimBackgroundTransition(LCDead10ccTransitionGate *gate) {
    return __atomic_exchange_n(&gate->handled, 1, __ATOMIC_ACQ_REL) == 0;
}
static void LCDead10ccResetBackgroundTransition(LCDead10ccTransitionGate *gate) {
    __atomic_store_n(&gate->handled, 0, __ATOMIC_RELEASE);
}
// DEAD10CC_TRANSITION_GATE_END
'''


def die(message: str) -> None:
    raise SystemExit(message)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_dead10cc(live_root: Path) -> None:
    path = live_root / "LiveContainer" / "Tweaks" / "Dead10ccFix.m"
    if not path.exists():
        die(f"Dead10ccFix.m not found at {path}")
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return

    text = replace_once(text, "@import Foundation;\n", "@import Foundation;\n\n" + TRANSITION_HELPER,
                        "background transition gate helper")
    text = replace_once(text, "@interface Dead10ccFix : NSObject\n",
        "@interface Dead10ccFix : NSObject {\n"
        "@private\n"
        "    LCDead10ccTransitionGate _backgroundTransitionGate;\n"
        "}\n", "background transition gate storage")

    # Scope remains the original guest processes. Both notifications are
    # registered there because either one can report the same transition.
    old_init = '''void initDead10ccFix(void) {

    if(NSUserDefaults.isLiveProcess) {
        fix = [[Dead10ccFix alloc] init];
        [NSNotificationCenter.defaultCenter addObserver:fix selector:@selector(handleAppDidEnterBackground:) name:NSExtensionHostDidEnterBackgroundNotification object:nil];
    } else if (NSUserDefaults.isSharedApp){
        fix = [[Dead10ccFix alloc] init];
        [NSNotificationCenter.defaultCenter addObserver:fix selector:@selector(handleAppDidEnterBackground:) name:@"UIApplicationDidEnterBackgroundNotification" object:nil];
    }
}'''

    new_init = '''void initDead10ccFix(void) {

    // DEAD10CC_FIX_E98699A: retain the original guest-only scope while
    // registering both notifications because either may report one transition.
    if (!NSUserDefaults.isLiveProcess && !NSUserDefaults.isSharedApp) return;
    fix = [[Dead10ccFix alloc] init];
    [NSNotificationCenter.defaultCenter addObserver:fix selector:@selector(handleAppDidEnterBackground:) name:NSExtensionHostDidEnterBackgroundNotification object:nil];
    [NSNotificationCenter.defaultCenter addObserver:fix selector:@selector(handleAppDidEnterBackground:) name:@"UIApplicationDidEnterBackgroundNotification" object:nil];
    [NSNotificationCenter.defaultCenter addObserver:fix selector:@selector(handleAppWillEnterForeground:) name:@"UIApplicationWillEnterForegroundNotification" object:nil];
    [NSNotificationCenter.defaultCenter addObserver:fix selector:@selector(handleAppWillEnterForeground:) name:@"NSExtensionHostDidBecomeActiveNotification" object:nil];
    NSLog(@"[LC_GUEST_LIFECYCLE] DEAD10CC_FIX_E98699A registered both observers in guest process");
}'''

    text = replace_once(text, old_init, new_init, "initDead10ccFix both observers")

    # Add diagnostics to handleAppDidEnterBackgroundReal
    old_handle = '''- (void)handleAppDidEnterBackgroundReal {
    NSSet* locks = [self _lock_lockedFilePathsIgnoring:[NSMutableSet set]];'''

    new_handle = '''- (void)handleAppDidEnterBackgroundReal {
    NSLog(@"[LC_GUEST_LIFECYCLE] BACKGROUND source=%@", @"extension_host_or_uiapp");
    NSSet* locks = [self _lock_lockedFilePathsIgnoring:[NSMutableSet set]];'''

    text = replace_once(text, old_handle, new_handle, "diagnostics background source")

    # Add diagnostics to handleAppDidEnterBackground
    old_handle2 = '''- (void)handleAppDidEnterBackground:(NSNotification *)notification {
    if(!_methodInited) {'''

    new_handle2 = '''- (void)handleAppDidEnterBackground:(NSNotification *)notification {
    NSString* src = [notification.name isEqualToString:NSExtensionHostDidEnterBackgroundNotification] ? @"extension_host" : @"uiapplication";
    NSLog(@"[LC_GUEST_LIFECYCLE] BACKGROUND source=%@", src);
    if (!LCDead10ccClaimBackgroundTransition(&_backgroundTransitionGate)) {
        NSLog(@"[LC_GUEST_LIFECYCLE] BACKGROUND_DUPLICATE source=%@", src);
        return;
    }
    if(!_methodInited) {'''

    text = replace_once(text, old_handle2, new_handle2, "diagnostics notification source")

    old_foreground = '''- (void)handleAppDidEnterBackground:(NSNotification *)notification {
    NSString* src = [notification.name isEqualToString:NSExtensionHostDidEnterBackgroundNotification] ? @"extension_host" : @"uiapplication";'''
    new_foreground = '''- (void)handleAppWillEnterForeground:(NSNotification *)notification {
    LCDead10ccResetBackgroundTransition(&_backgroundTransitionGate);
    NSLog(@"[LC_GUEST_LIFECYCLE] FOREGROUND_RESET source=%@", notification.name);
}

- (void)handleAppDidEnterBackground:(NSNotification *)notification {
    NSString* src = [notification.name isEqualToString:NSExtensionHostDidEnterBackgroundNotification] ? @"extension_host" : @"uiapplication";'''
    text = replace_once(text, old_foreground, new_foreground, "foreground transition reset")

    # Add diagnostics to _terminateWithStatus
    old_terminate = '''- (void)_terminateWithStatus:(int)status {
    // Fake implementation from UIApplication
    NSLog(@"[LC] _handleTaskCompletionAndTerminate");'''

    new_terminate = '''- (void)_terminateWithStatus:(int)status {
    // Fake implementation from UIApplication
    NSLog(@"[LC_GUEST_LIFECYCLE] PROCESS_INTERRUPTED pid=%d", getpid());
    NSLog(@"[LC] _handleTaskCompletionAndTerminate");'''

    text = replace_once(text, old_terminate, new_terminate, "diagnostics process interrupted")

    path.write_text(text, encoding="utf-8")


def verify(live_root: Path) -> None:
    path = live_root / "LiveContainer" / "Tweaks" / "Dead10ccFix.m"
    text = path.read_text(encoding="utf-8")
    
    required = [
        MARKER,
        TRANSITION_MARKER,
        "NSExtensionHostDidEnterBackgroundNotification",
        "UIApplicationDidEnterBackgroundNotification",
        "UIApplicationWillEnterForegroundNotification",
        "NSExtensionHostDidBecomeActiveNotification",
        "LC_GUEST_LIFECYCLE",
        "BACKGROUND source=",
        "BACKGROUND_DUPLICATE source=",
        "FOREGROUND_RESET source=",
        "DEAD10CC_FIX_E98699A registered both observers in guest process",
        "PROCESS_INTERRUPTED pid=",
    ]
    missing = [needle for needle in required if needle not in text]
    if missing:
        die(f"Dead10ccFix verification failed: missing {missing}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_dead10cc_fix.py <livecontainer-root>")
    root = Path(sys.argv[1]).resolve()
    if not (root / "LiveContainer.xcodeproj").exists():
        die(f"not a LiveContainer checkout: {root}")
    patch_dead10cc(root)
    verify(root)
    print("Dead10ccFix backport applied and verified")


if __name__ == "__main__":
    main()
