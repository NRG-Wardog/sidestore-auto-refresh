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

    # The current initDead10ccFix only registers one notification based on mode.
    # Upstream fix e98699a registers BOTH notifications for both modes.
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

    // DEAD10CC_FIX_E98699A: register BOTH background notifications regardless of mode.
    // Upstream fix e98699a "Fix #1491: 0xdead10cc regression" - either notification
    // can fire depending on Scene API, so just register both.
    fix = [[Dead10ccFix alloc] init];
    [NSNotificationCenter.defaultCenter addObserver:fix selector:@selector(handleAppDidEnterBackground:) name:NSExtensionHostDidEnterBackgroundNotification object:nil];
    [NSNotificationCenter.defaultCenter addObserver:fix selector:@selector(handleAppDidEnterBackground:) name:@"UIApplicationDidEnterBackgroundNotification" object:nil];
    
    // Diagnostics
    NSLog(@"[LC_GUEST_LIFECYCLE] DEAD10CC_FIX_E98699A registered both background observers");
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
    if(!_methodInited) {'''

    text = replace_once(text, old_handle2, new_handle2, "diagnostics notification source")

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
        "NSExtensionHostDidEnterBackgroundNotification",
        "UIApplicationDidEnterBackgroundNotification",
        "LC_GUEST_LIFECYCLE",
        "BACKGROUND source=",
        "DEAD10CC_FIX_E98699A registered both background observers",
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