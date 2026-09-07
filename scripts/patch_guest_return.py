"""Add a host-owned return control to retained LiveProcess scenes."""
from pathlib import Path
import sys


CONTROL = r'''
// LC_GUEST_RETURN_V1: this view never owns a guest process or scene.
@interface LCReturnControl : UIView
@property(nonatomic) UIButton *button;
@property(nonatomic, copy) void (^action)(void);
@property(nonatomic) CGPoint position;
@property(nonatomic) CGRect keyboardFrame;
@end
@implementation LCReturnControl
- (instancetype)initWithFrame:(CGRect)frame {
    if (!(self = [super initWithFrame:frame])) return nil;
    self.backgroundColor = UIColor.clearColor;
    self.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    NSArray *saved = [NSUserDefaults.lcUserDefaults arrayForKey:@"LCReturnControlPosition"];
    self.position = CGPointMake(0.95, 0.25);
    if (saved.count == 2 && [saved[0] isKindOfClass:NSNumber.class] && [saved[1] isKindOfClass:NSNumber.class]) {
        double x = [saved[0] doubleValue], y = [saved[1] doubleValue];
        if (isfinite(x) && isfinite(y) && x >= 0 && x <= 1 && y >= 0 && y <= 1) self.position = CGPointMake(x, y);
    }
    self.button = [UIButton buttonWithType:UIButtonTypeSystem];
    self.button.backgroundColor = UIColor.secondarySystemBackgroundColor;
    self.button.layer.cornerRadius = 22;
    [self.button setImage:[UIImage systemImageNamed:@"arrow.uturn.backward.circle.fill"] forState:UIControlStateNormal];
    self.button.accessibilityLabel = @"Return to LiveContainer";
    self.button.accessibilityHint = @"Keeps the guest open when supported";
    [self.button addTarget:self action:@selector(tapped) forControlEvents:UIControlEventTouchUpInside];
    [self.button addGestureRecognizer:[[UIPanGestureRecognizer alloc] initWithTarget:self action:@selector(drag:)]];
    [self addSubview:self.button];
    [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(keyboard:) name:UIKeyboardWillChangeFrameNotification object:nil];
    [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(keyboard:) name:UIKeyboardWillHideNotification object:nil];
    NSLog(@"[LC_RETURN] CONTROL_SHOWN");
    return self;
}
- (void)dealloc { [NSNotificationCenter.defaultCenter removeObserver:self]; }
- (UIView *)hitTest:(CGPoint)point withEvent:(UIEvent *)event {
    UIView *hit = [super hitTest:point withEvent:event];
    return hit == self ? nil : hit;
}
- (CGRect)availableRect {
    CGRect rect = UIEdgeInsetsInsetRect(self.bounds, self.safeAreaInsets);
    if (!CGRectIsEmpty(self.keyboardFrame) && self.window) {
        CGRect keyboard = [self convertRect:self.keyboardFrame fromCoordinateSpace:self.window.screen.coordinateSpace];
        if (CGRectIntersectsRect(rect, keyboard) && CGRectGetMaxY(keyboard) >= CGRectGetMaxY(rect)) {
            rect.size.height = MAX(0, CGRectGetMinY(keyboard) - CGRectGetMinY(rect));
        }
    }
    rect = CGRectInset(rect, 30, 30);
    rect.size.width = MAX(0, rect.size.width);
    rect.size.height = MAX(0, rect.size.height);
    return rect;
}
- (void)layoutSubviews {
    [super layoutSubviews];
    CGRect rect = [self availableRect];
    self.button.bounds = CGRectMake(0, 0, 44, 44);
    self.button.center = CGPointMake(rect.origin.x + self.position.x * rect.size.width, rect.origin.y + self.position.y * rect.size.height);
}
- (void)keyboard:(NSNotification *)note {
    self.keyboardFrame = [note.name isEqualToString:UIKeyboardWillHideNotification] ? CGRectZero : [note.userInfo[UIKeyboardFrameEndUserInfoKey] CGRectValue];
    [self setNeedsLayout];
}
- (void)drag:(UIPanGestureRecognizer *)gesture {
    CGRect rect = [self availableRect];
    CGPoint delta = [gesture translationInView:self];
    CGPoint center = self.button.center;
    self.position = CGPointMake(rect.size.width > 0 ? MIN(1, MAX(0, (center.x + delta.x - rect.origin.x) / rect.size.width)) : 0.5,
                                rect.size.height > 0 ? MIN(1, MAX(0, (center.y + delta.y - rect.origin.y) / rect.size.height)) : 0.5);
    [gesture setTranslation:CGPointZero inView:self];
    [self setNeedsLayout];
    [self layoutIfNeeded];
    if (gesture.state == UIGestureRecognizerStateEnded || gesture.state == UIGestureRecognizerStateCancelled) {
        [NSUserDefaults.lcUserDefaults setObject:@[@(self.position.x), @(self.position.y)] forKey:@"LCReturnControlPosition"];
        NSLog(@"[LC_RETURN] CONTROL_MOVED");
    }
}
- (void)tapped { if (self.action) self.action(); }
@end
'''

METHODS = r'''
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    if (!self.lcReturnControl) {
        self.lcReturnControl = [[LCReturnControl alloc] initWithFrame:self.view.bounds];
        __weak typeof(self) weakSelf = self;
        self.lcReturnControl.action = ^{ [weakSelf lcReturnToHost]; };
        [self.view addSubview:self.lcReturnControl];
    }
    self.lcReturnControl.frame = self.view.bounds;
    self.lcReturnControl.hidden = !self.isAppRunning;
    [self.view bringSubviewToFront:self.lcReturnControl];
}
- (void)lcReturnToHost {
    NSLog(@"[LC_RETURN] RETURN_REQUESTED pid=%d", self.pid);
    NSLog(@"[LC_RETURN] MODE_LIVEPROCESS");
    if (!self.isAppRunning) {
        NSLog(@"[LC_RETURN] RETURN_FAILED reason=guest_exited");
        [self appTerminationCleanUp];
        return;
    }
    if ([self.delegate isKindOfClass:DecoratedAppSceneViewController.class]) {
        [(DecoratedAppSceneViewController *)self.delegate minimizeWindow];
        NSLog(@"[LC_RETURN] GUEST_MINIMIZED mode=LIVEPROCESS_PRESERVED_RETURN pid=%d", self.pid);
        // Minimize's animation reveals the existing host; it does not suspend the guest.
    } else if (self.lcActivateHost) {
        self.lcActivateHost();
    } else {
        NSLog(@"[LC_RETURN] RETURN_FAILED reason=host_activation_unavailable");
    }
}
'''


def change(root, relative, old, new):
    path = root / relative
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise ValueError(f"Changed upstream anchor: {relative}: {old[:80]}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch(root):
    implementation = "MultitaskSupport/AppSceneViewController.m"
    change(root, implementation, "@property int resizeDebounceToken;", "@property(nonatomic) LCReturnControl *lcReturnControl;\n@property int resizeDebounceToken;")
    change(root, implementation, '#import "UIKitPrivate+MultitaskSupport.h"', '#import "UIKitPrivate+MultitaskSupport.h"\n#include <math.h>\n' + CONTROL)
    change(root, implementation, "@implementation AppSceneViewController", "@implementation AppSceneViewController\n" + METHODS)
    change(root, "MultitaskSupport/AppSceneViewController.h", "- (void)terminate;", "@property(nonatomic, copy) void (^lcActivateHost)(void);\n- (void)terminate;")
    window = "MultitaskSupport/MultitaskAppWindow.swift"
    change(root, window, "    @Binding var show: Bool", '    @Environment(\\.openWindow) private var returnOpenWindow\n    @Binding var show: Bool')
    change(root, window, "        return AppSceneViewController(bundleId: bundleId, dataUUID: dataUUID, delegate: context.coordinator)", '''        let controller = AppSceneViewController(bundleId: bundleId, dataUUID: dataUUID, delegate: context.coordinator)
        guard let controller else {
            print("[LC_RETURN] RETURN_FAILED reason=guest_controller_initialization_failed")
            return UIViewController()
        }
        controller.lcActivateHost = {
            print("[LC_RETURN] HOST_ACTIVATION_REQUESTED mode=LIVEPROCESS_PRESERVED_RETURN")
            returnOpenWindow(id: "Main")
        }
        return controller
'''.rstrip())
    model = "LiveContainerSwiftUI/Models/LCAppModel.swift"
    change(root, window, "    var bundleId: String\n", "    var bundleId: String\n    var pid: Int32 = 0\n")
    change(root, window, "            if a.value.dataUUID == dataUUID {", '''            if a.value.dataUUID == dataUUID {
                if a.value.pid > 0 && getpgid(a.value.pid) <= 0 {
                    appDict.removeValue(forKey: a.key)
                    MultitaskManager.unregisterMultitaskContainer(container: dataUUID)
                    print("[LC_RETURN] STALE_GUEST_CLEANED")
                    return false
                }''')
    change(root, window, "                            self.pid = Int(pid)", "                            self.pid = Int(pid)\n                            MultitaskWindowManager.appDict[appInfo.dataUUID]?.pid = pid")
    dock = "MultitaskSupport/MultitaskDockView.swift"
    change(root, dock, '''        guard let windowScene = UIApplication.shared.connectedScenes.first as? UIWindowScene else {
            return false
        }

        for window in windowScene.windows {''', '''        let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap { $0.windows }
        for window in windows {''')
    change(root, dock, "                passURLSchemeToView(targetView)", '''                if let controller = targetView._viewDelegate() as? DecoratedAppSceneViewController,
                   !controller.appSceneVC.isAppRunning {
                    controller.appSceneVC.appTerminationCleanUp()
                    print("[LC_RETURN] STALE_GUEST_CLEANED")
                    return false
                }
                passURLSchemeToView(targetView)''')
    change(root, model, "            return found\n", '''            print(found ? "[LC_RETURN] GUEST_RESUMED_EXISTING" : "[LC_RETURN] GUEST_COLD_LAUNCH")
            return found
''')
    change(root, model, "    private func bringExistingMultitaskWindowIfNeeded(dataUUID: String, urlScheme: String?) async -> Bool {", "    private func bringExistingMultitaskWindowIfNeeded(dataUUID: String, urlScheme: String?) async -> Bool {\n        print(\"[LC_RETURN] GUEST_RESUME_REQUESTED\")")
    hooks = "SideStoreSupport/SideStoreHooks.m"
    change(root, hooks, "    [LCSharedUtils launchToGuestAppWithClassicMode:0];", '    NSLog(@"[LC_RETURN] MODE_DIRECT mode=DIRECT_PROCESS_RESTART_RETURN");\n    [LCSharedUtils launchToGuestAppWithClassicMode:0];')


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
