"""Patch the pinned LiveProcess return path; never terminate a preserved guest."""
from pathlib import Path
import sys

MARKER = "LC_GUEST_RETURN_V2"

# Shared by the real control and executable geometry regression tests.
GEOMETRY = r'''
static double LCReturnAxisCenter(double origin, double length, double position) {
    if (!isfinite(origin) || !isfinite(length) || length < 0) return 0;
    if (!isfinite(position)) position = 0.5;
    position = fmin(1.0, fmax(0.0, position));
    double inset = fmin(30.0, length / 2.0);
    return origin + inset + position * fmax(0.0, length - 2.0 * inset);
}
'''

CONTROL = GEOMETRY + r'''
// LC_GUEST_RETURN_V2: the control owns no guest process or scene.
@interface LCReturnControl : UIView
@property(nonatomic, strong) UIButton *button;
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
    self.button.accessibilityHint = @"Minimizes this guest without closing it";
    __weak typeof(self) weakControl = self;
    self.button.menu = [UIMenu menuWithTitle:@"" children:@[
        [UIAction actionWithTitle:@"Hide Return Button" image:[UIImage systemImageNamed:@"eye.slash"] identifier:nil handler:^(__kindof UIAction *action) {
            [NSUserDefaults.lcUserDefaults setBool:YES forKey:@"LCHideReturnControl"];
            [weakControl setNeedsLayout];
            NSLog(@"[LC_RETURN] CONTROL_HIDDEN");
        }]
    ]];
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
    if (self.window) {
        CGRect windowSafe = UIEdgeInsetsInsetRect(self.window.bounds, self.window.safeAreaInsets);
        CGRect intersection = CGRectIntersection(rect, [self convertRect:windowSafe fromView:self.window]);
        if (!CGRectIsNull(intersection)) rect = intersection;
    }
    if (!CGRectIsEmpty(self.keyboardFrame) && self.window) {
        CGRect keyboard = [self convertRect:self.keyboardFrame fromCoordinateSpace:self.window.screen.coordinateSpace];
        if (CGRectIntersectsRect(rect, keyboard) && CGRectGetMaxY(keyboard) >= CGRectGetMaxY(rect)) {
            rect.size.height = MAX(0, CGRectGetMinY(keyboard) - CGRectGetMinY(rect));
        }
    }
    return rect;
}
- (void)layoutSubviews {
    [super layoutSubviews];
    CGRect rect = [self availableRect];
    // A 44-point target must not be placed outside a tiny resized window.
    self.button.hidden = [NSUserDefaults.lcUserDefaults boolForKey:@"LCHideReturnControl"] || CGRectIsNull(rect) || rect.size.width < 44 || rect.size.height < 44;
    if (self.button.hidden) return;
    self.button.bounds = CGRectMake(0, 0, 44, 44);
    self.button.center = CGPointMake(LCReturnAxisCenter(rect.origin.x, rect.size.width, self.position.x),
                                    LCReturnAxisCenter(rect.origin.y, rect.size.height, self.position.y));
}
- (void)keyboard:(NSNotification *)note {
    self.keyboardFrame = [note.name isEqualToString:UIKeyboardWillHideNotification] ? CGRectZero : [note.userInfo[UIKeyboardFrameEndUserInfoKey] CGRectValue];
    [self setNeedsLayout];
}
- (void)drag:(UIPanGestureRecognizer *)gesture {
    CGRect rect = [self availableRect];
    CGPoint delta = [gesture translationInView:self];
    CGPoint center = self.button.center;
    double minX = LCReturnAxisCenter(rect.origin.x, rect.size.width, 0);
    double minY = LCReturnAxisCenter(rect.origin.y, rect.size.height, 0);
    double spanX = LCReturnAxisCenter(rect.origin.x, rect.size.width, 1) - minX;
    double spanY = LCReturnAxisCenter(rect.origin.y, rect.size.height, 1) - minY;
    self.position = CGPointMake(spanX > 0 ? MIN(1, MAX(0, (center.x + delta.x - minX) / spanX)) : 0.5,
                                spanY > 0 ? MIN(1, MAX(0, (center.y + delta.y - minY) / spanY)) : 0.5);
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
        NSLog(@"[LC_RETURN] GUEST_MINIMIZE_REQUESTED mode=LIVEPROCESS_PRESERVED_RETURN pid=%d", self.pid);
    } else if (self.lcActivateHost) {
        self.lcActivateHost();
    } else {
        NSLog(@"[LC_RETURN] RETURN_FAILED reason=host_activation_unavailable");
    }
}
'''

# Kept in the existing window registry. A generation key prevents an old scene
# from receiving a new launch callback or being mistaken for a cold launch.
WINDOW_MANAGER = r'''
    // LC_GUEST_RETURN_V2: all registry mutations occur on the main queue.
    static var mainSceneSession: UISceneSession?

    static func activateMainScene(create: () -> Void) {
        if let session = mainSceneSession,
           UIApplication.shared.openSessions.contains(where: { $0.persistentIdentifier == session.persistentIdentifier }) {
            UIApplication.shared.requestSceneSessionActivation(session, userActivity: nil, options: nil) { error in
                print("[LC_RETURN] RETURN_FAILED reason=host_activation error=\(error.localizedDescription)")
            }
        } else {
            mainSceneSession = nil
            create()
        }
        // A request is not proof of a foreground transition.
        print("[LC_RETURN] HOST_ACTIVATION_REQUESTED mode=LIVEPROCESS_PRESERVED_RETURN")
    }

    @objc class func openAppWindow(displayName: String, dataUUID: String, bundleId: String, pidCallback: ((NSNumber, Error?) -> Void)?) {
        if !Thread.isMainThread {
            DispatchQueue.main.async { openAppWindow(displayName: displayName, dataUUID: dataUUID, bundleId: bundleId, pidCallback: pidCallback) }
            return
        }
        guard !appDict.values.contains(where: { $0.dataUUID == dataUUID }) else {
            pidCallback?(NSNumber(value: -1), NSError(domain: "LiveContainerReturn", code: 409,
                userInfo: [NSLocalizedDescriptionKey: "This guest container already has a window or a pending launch. Reopen it instead."]))
            return
        }
        DataManager.shared.model.enableMultipleWindow = true
        var entry = MultitaskAppInfo(displayName: displayName, dataUUID: dataUUID, bundleId: bundleId)
        entry.launchCallback = pidCallback
        appDict[entry.windowID] = entry
        openWindow(id: "appView", value: entry.windowID)
    }

    static func bind(_ controller: AppSceneViewController, windowID: String) {
        guard appDict[windowID] != nil else { return }
        appDict[windowID]?.controller = controller
    }

    static func initialized(_ controller: AppSceneViewController, windowID: String, error: Error?) {
        guard var entry = appDict[windowID] else { return }
        entry.controller = controller
        entry.pid = controller.pid
        let callback = entry.launchCallback
        entry.launchCallback = nil // Consume before calling re-entrant client code.
        appDict[windowID] = entry
        if error != nil {
            controller.appTerminationCleanUp()
            appDict.removeValue(forKey: windowID)
        }
        callback?(NSNumber(value: controller.pid), error)
    }

    static func exited(_ controller: AppSceneViewController, windowID: String) {
        guard let entry = appDict[windowID], entry.controller === controller else { return }
        appDict.removeValue(forKey: windowID)
        entry.launchCallback?(NSNumber(value: -1), NSError(domain: "LiveContainerReturn", code: 410,
            userInfo: [NSLocalizedDescriptionKey: "The guest exited before its launch completed."]))
        print("[LC_RETURN] STALE_GUEST_CLEANED");
    }

    @objc class func openExistingAppWindow(dataUUID: String) -> Bool {
        if !Thread.isMainThread {
            return DispatchQueue.main.sync { openExistingAppWindow(dataUUID: dataUUID) }
        }
        for (key, entry) in Array(appDict) where entry.dataUUID == dataUUID {
            if let controller = entry.controller {
                if !controller.isAppRunning && controller.pid > 0 {
                    // Cleanup on main completes before another launch can register.
                    controller.appTerminationCleanUp()
                    appDict.removeValue(forKey: key)
                    print("[LC_RETURN] STALE_GUEST_CLEANED")
                    continue
                }
            } else if entry.pid > 0 {
                // Missing scene ownership is not a verified resume. Do not touch
                // container registration; the model's in-use check prevents duplicates.
                appDict.removeValue(forKey: key)
                print("[LC_RETURN] RETURN_FAILED reason=retained_controller_missing")
                continue
            }
            openWindow(id: "appView", value: key)
            print(entry.pid > 0 ? "[LC_RETURN] GUEST_RESUMED_EXISTING" : "[LC_RETURN] GUEST_LAUNCH_PENDING")
            return true
        }
        return false
    }
'''

DOCK_RESUME = r'''
    func bringMultitaskViewToFront(uuid: String, from center: CGPoint? = nil) -> Bool {
        guard let targetView = apps.first(where: { $0.appUUID == uuid })?.view,
              let controller = targetView._viewDelegate() as? DecoratedAppSceneViewController else { return false }
        if !controller.appSceneVC.isAppRunning && controller.appSceneVC.pid > 0 {
            controller.appSceneVC.appTerminationCleanUp()
            // Upstream may intentionally retain the terminated-screen row.
            // Remove that exact old row before registering its replacement.
            removeRunningApp(uuid)
            controller.willMove(toParent: nil)
            targetView.removeFromSuperview()
            controller.removeFromParent()
            print("[LC_RETURN] STALE_GUEST_CLEANED")
            return false
        }
        guard let window = targetView.window else {
            print("[LC_RETURN] RETURN_FAILED reason=retained_view_has_no_window")
            return false
        }
        passURLSchemeToView(targetView)
        animateViewAppearance(targetView, from: center, in: window)
        print(controller.appSceneVC.pid > 0 ? "[LC_RETURN] GUEST_RESUMED_EXISTING" : "[LC_RETURN] GUEST_LAUNCH_PENDING")
        return true
    }
'''

CLEANUP = r'''
- (void)appTerminationCleanUp {
    if (!NSThread.isMainThread) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self appTerminationCleanUp]; });
        return;
    }
    if (_isAppTerminationCleanUpCalled) return;
    _isAppTerminationCleanUpCalled = true;
    self.lcReturnControl.hidden = YES;
    if (self.sceneID) {
        [[PrivClass(FBSceneManager) sharedInstance] destroyScene:self.sceneID withTransitionContext:nil];
    }
    if (self.usesHostingControllerAPI) {
        if (@available(iOS 17.0, *)) {
            [self.hostingController invalidate];
            [self.hostingController.sceneViewController removeFromParentViewController];
            self.hostingController = nil;
        }
    } else if (self.presenter) {
        [self.presenter deactivate];
        [self.presenter invalidate];
    }
    self.presenter = nil;
    // Release the old registration BEFORE notifying code that may relaunch.
    [MultitaskManager unregisterMultitaskContainerWithContainer:self.dataUUID];
    [self.delegate appSceneVCAppDidExit:self];
}
'''

DIRECT_CONTROL = CONTROL.replace("LCReturnControl", "LCDirectReturnControl").replace(
    'Minimizes this guest without closing it', 'Restarts LiveContainer and closes this guest'
)

DIRECT_RUNTIME = r'''
// LC_DIRECT_RETURN_V1: direct guests share the host process, not the dock registry.
@interface LCDirectReturnWindow : UIWindow
@end
@implementation LCDirectReturnWindow
- (UIView *)hitTest:(CGPoint)point withEvent:(UIEvent *)event {
    UIView *hit = [super hitTest:point withEvent:event];
    return (hit == self || hit == self.rootViewController.view) ? nil : hit;
}
@end

@interface LCDirectReturnPresenter : NSObject
@property(nonatomic, strong) NSMutableDictionary<NSString *, LCDirectReturnWindow *> *windows;
@end
@implementation LCDirectReturnPresenter
- (instancetype)init {
    if (!(self = [super init])) return nil;
    self.windows = [NSMutableDictionary new];
    NSNotificationCenter *center = NSNotificationCenter.defaultCenter;
    [center addObserver:self selector:@selector(show:) name:UIWindowDidBecomeVisibleNotification object:nil];
    [center addObserver:self selector:@selector(show:) name:UISceneDidActivateNotification object:nil];
    [center addObserver:self selector:@selector(disconnect:) name:UISceneDidDisconnectNotification object:nil];
    return self;
}
- (void)show:(NSNotification *)notification {
    if (!NSThread.isMainThread) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self show:notification]; });
        return;
    }
    UIWindowScene *scene = nil;
    if ([notification.object isKindOfClass:UIWindow.class]) {
        UIWindow *source = notification.object;
        if ([source isKindOfClass:LCDirectReturnWindow.class] || source.windowLevel != UIWindowLevelNormal) return;
        scene = source.windowScene;
    } else if ([notification.object isKindOfClass:UIWindowScene.class]) {
        scene = notification.object;
    }
    if (!scene || ![scene.session.role isEqualToString:UIWindowSceneSessionRoleApplication]) return;
    NSString *identity = scene.session.persistentIdentifier;
    if (self.windows[identity]) return;
    LCDirectReturnWindow *window = [[LCDirectReturnWindow alloc] initWithWindowScene:scene];
    UIViewController *controller = [UIViewController new];
    window.rootViewController = controller;
    window.backgroundColor = UIColor.clearColor;
    window.windowLevel = UIWindowLevelAlert + 1;
    LCDirectReturnControl *control = [[LCDirectReturnControl alloc] initWithFrame:window.bounds];
    controller.view = control;
    control.action = ^{
        NSLog(@"[LC_RETURN] RETURN_REQUESTED mode=DIRECT_PROCESS_RESTART_RETURN");
        // Match the upstream SideStore escape path. Never used by LiveProcess.
        [LCSharedUtils launchToGuestAppWithClassicMode:0];
    };
    self.windows[identity] = window;
    window.hidden = NO; // Do not steal key-window status from the guest or its keyboard.
    NSLog(@"[LC_RETURN] MODE_DIRECT mode=DIRECT_PROCESS_RESTART_RETURN pid=%d", getpid());
}
- (void)disconnect:(NSNotification *)notification {
    if ([notification.object isKindOfClass:UIWindowScene.class]) {
        UIWindowScene *scene = notification.object;
        [self.windows removeObjectForKey:scene.session.persistentIdentifier];
    }
}
@end
static LCDirectReturnPresenter *lcDirectReturnPresenter;
'''

PATHS = (
    "MultitaskSupport/AppSceneViewController.m", "MultitaskSupport/AppSceneViewController.h",
    "MultitaskSupport/MultitaskAppWindow.swift", "MultitaskSupport/MultitaskDockView.swift",
    "SideStoreSupport/SideStoreHooks.m", "LiveContainerSwiftUI/Models/LCAppModel.swift",
    "LiveContainerSwiftUI/Views/LCTabView.swift",
    "LiveContainer/LCBootstrap.m",
    "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift",
)


def replace(text, old, new, label):
    if text.count(old) != 1:
        raise ValueError(f"Changed upstream anchor: {label}: {old[:100]}")
    return text.replace(old, new, 1)


def section(text, start, end, replacement, label):
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"Changed upstream section: {label}")
    a = text.index(start)
    b = text.index(end, a)
    return text[:a] + replacement + "\n\n" + text[b:]


def verify(texts):
    implementation, header, window, dock, hooks, model, tab, bootstrap, settings = (texts[p] for p in PATHS)
    for text, token in ((implementation, CONTROL), (implementation, METHODS), (implementation, CLEANUP),
                        (window, WINDOW_MANAGER), (dock, DOCK_RESUME), (header, "lcActivateHost"),
                        (hooks, "DIRECT_PROCESS_RESTART_RETURN"), (model, "LC_RETURN_CONTAINER_GUARD"),
                        (tab, "MultitaskWindowManager.mainSceneSession")):
        if token.strip() not in text:
            raise ValueError("Incomplete guest-return patch: " + token[:65])
    if "DataManager.shared.model.pidCallback" in window:
        raise ValueError("Global cross-window callback survived")
    if DIRECT_CONTROL.strip() not in bootstrap or DIRECT_RUNTIME.strip() not in bootstrap:
        raise ValueError("Incomplete direct guest Return patch")
    if 'Toggle("Show Return Button"' not in settings:
        raise ValueError("Return visibility setting missing")


def patch(root):
    root = Path(root)
    texts = {p: (root / p).read_text(encoding="utf-8") for p in PATHS}
    implementation, header, window, dock, hooks, model, tab, bootstrap, settings = (texts[p] for p in PATHS)
    if MARKER in implementation:
        verify(texts)
        return
    if "LC_GUEST_RETURN_V1" in implementation:
        raise ValueError("Reapply the return patch to clean pinned sources, not a V1-patched tree")

    implementation = replace(implementation, '#import "UIKitPrivate+MultitaskSupport.h"',
                             '#import "UIKitPrivate+MultitaskSupport.h"\n#include <math.h>\n' + CONTROL, "control")
    implementation = replace(implementation, "@property int resizeDebounceToken;", "@property(nonatomic, strong) LCReturnControl *lcReturnControl;\n@property int resizeDebounceToken;", "control property")
    implementation = replace(implementation, "@implementation AppSceneViewController", "@implementation AppSceneViewController\n" + METHODS, "return method")
    implementation = replace(implementation, "    [self.view addSubview:_contentView];", "    [self.view addSubview:_contentView];\n    [self.view setNeedsLayout]; // Re-show control after asynchronous guest initialization.", "control readiness")
    implementation = replace(implementation, "return _pid > 0 && getpgid(_pid) > 0;", "return !_isAppTerminationCleanUpCalled && _pid > 0 && getpgid(_pid) > 0;", "retired process is not resumable")
    implementation = section(implementation, "- (void)appTerminationCleanUp {", "- (void)setBackgroundNotificationEnabled:", CLEANUP, "synchronous cleanup")
    implementation = replace(implementation, "- (void)setUpAppPresenter {", "- (void)setUpAppPresenter {\n    if (_isAppTerminationCleanUpCalled || !self.isAppRunning) {\n        [self appTerminationCleanUp];\n        return;\n    }", "cancelled guest cannot create a scene")
    request_start = "    [_extension beginExtensionRequestWithInputItems:@[item] completion:^(NSUUID *identifier) {"
    a = implementation.index(request_start)
    b = implementation.index("\n    return self;", a)
    request = implementation[a:b]
    if request.count("    }];") != 1:
        raise ValueError("Changed extension launch completion boundary")
    updated_request = request.replace(request_start, request_start + "\n        dispatch_async(dispatch_get_main_queue(), ^{\n            if (self.isAppTerminationCleanUpCalled) return;", 1)
    updated_request = updated_request.replace("    }];", "        });\n    }];", 1)
    implementation = implementation[:a] + updated_request + implementation[b:]

    header = replace(header, "- (void)terminate;", "@property(nonatomic, copy) void (^lcActivateHost)(void);\n- (void)terminate;", "host action")

    window = replace(window, "    var bundleId: String\n", "    var bundleId: String\n    let windowID = UUID().uuidString\n    var pid: Int32 = 0\n    weak var controller: AppSceneViewController?\n    var launchCallback: ((NSNumber, Error?) -> Void)?\n", "per-window launch identity")
    window = section(window, "    @objc class func openAppWindow(", "\n}\n\n@available(iOS 16.1, *)\nstruct AppSceneViewSwiftUI", WINDOW_MANAGER, "window registry")
    window = replace(window, "    @Binding var show: Bool", '    @Environment(\\.openWindow) private var returnOpenWindow\n    let windowID: String\n    @Binding var show: Bool', "window action")
    window = replace(window, "        let onExit: () -> Void", "        let windowID: String\n        let onExit: () -> Void", "coordinator identity")
    window = replace(window, "        init(onAppInitialize: @escaping (Int32, Error?) -> Void, onExit: @escaping () -> Void) {", "        init(windowID: String, onAppInitialize: @escaping (Int32, Error?) -> Void, onExit: @escaping () -> Void) {\n            self.windowID = windowID", "coordinator init")
    window = replace(window, "        func appSceneVCAppDidExit(_: AppSceneViewController!) {\n            onExit()\n        }", "        func appSceneVCAppDidExit(_ vc: AppSceneViewController!) {\n            MultitaskWindowManager.exited(vc, windowID: windowID)\n            onExit()\n        }", "window exit")
    window = replace(window, "            onAppInitialize(vc.pid, error)", "            DispatchQueue.main.async {\n                MultitaskWindowManager.initialized(vc, windowID: self.windowID, error: error)\n                self.onAppInitialize(vc.pid, error)\n            }", "launch completion")
    window = replace(window, "        Coordinator(onAppInitialize: onAppInitialize, onExit: {", "        Coordinator(windowID: windowID, onAppInitialize: onAppInitialize, onExit: {", "make coordinator")
    window = replace(window, "        return AppSceneViewController(bundleId: bundleId, dataUUID: dataUUID, delegate: context.coordinator)", '''        guard let controller = AppSceneViewController(bundleId: bundleId, dataUUID: dataUUID, delegate: context.coordinator) else {
            print("[LC_RETURN] RETURN_FAILED reason=guest_controller_initialization_failed")
            return UIViewController()
        }
        MultitaskWindowManager.bind(controller, windowID: windowID)
        controller.lcActivateHost = {
            MultitaskWindowManager.activateMainScene(create: { returnOpenWindow(id: "Main") })
        }
        return controller''', "make controller")
    window = replace(window, "AppSceneViewSwiftUI(show: $show,", "AppSceneViewSwiftUI(windowID: appInfo.windowID, show: $show,", "pass window identity")
    window = replace(window, "                        DataManager.shared.model.pidCallback?(NSNumber(value: pid), error)\n                        DataManager.shared.model.pidCallback = nil\n", "", "remove global callback")

    dock = section(dock, "    func bringMultitaskViewToFront(uuid:", "    private func passURLSchemeToView(", DOCK_RESUME, "resume owning window")
    dock = section(dock, "    @objc public func removeRunningApp(", "    @objc public func showDock()", '''    @objc public func removeRunningApp(_ appUUID: String) {
        if !Thread.isMainThread {
            DispatchQueue.main.async { self.removeRunningApp(appUUID) }
            return
        }
        self.apps.removeAll { $0.appUUID == appUUID }
        if self.apps.isEmpty { self.hideDock() }
        else if self.isVisible { self.updateDockFrame() }
    }''', "remove stale dock entry synchronously")

    model = replace(model, "            return found\n", '            if !found { print("[LC_RETURN] GUEST_COLD_LAUNCH_REQUIRED") }\n            return found\n', "honest resume diagnostics")
    model = replace(model, "    private func bringExistingMultitaskWindowIfNeeded(dataUUID: String, urlScheme: String?) async -> Bool {", '    private func bringExistingMultitaskWindowIfNeeded(dataUUID: String, urlScheme: String?) async -> Bool {\n        print("[LC_RETURN] GUEST_RESUME_REQUESTED")', "resume diagnostics")
    model = replace(model, '''            if await bringExistingMultitaskWindowIfNeeded(dataUUID: currentDataFolder, urlScheme: urlStr) {
                return
            }''', '''            if await bringExistingMultitaskWindowIfNeeded(dataUUID: currentDataFolder, urlScheme: urlStr) {
                return
            }
            // LC_RETURN_CONTAINER_GUARD: failed scene lookup must not duplicate a live container.
            if await MainActor.run(body: { MultitaskManager.isUsing(container: currentDataFolder) }) {
                throw "lc.container.inUse".loc + "\\nA retained guest still owns this container. Close its existing window before relaunching."
            }''', "container ownership")
    hooks = replace(hooks, "    [LCSharedUtils launchToGuestAppWithClassicMode:0];", '    NSLog(@"[LC_RETURN] MODE_DIRECT mode=DIRECT_PROCESS_RESTART_RETURN");\n    [LCSharedUtils launchToGuestAppWithClassicMode:0];', "direct fallback diagnostic")
    tab = replace(tab, "            shouldToggleMainWindowOpen = true\n", "            shouldToggleMainWindowOpen = true\n            if #available(iOS 16.1, *) {\n                MultitaskWindowManager.mainSceneSession = sceneDelegate.window?.windowScene?.session\n            }\n", "capture real main scene")
    tab = replace(tab, "                    DataManager.shared.model.mainWindowOpened = false", "                    DataManager.shared.model.mainWindowOpened = false\n                    if #available(iOS 16.1, *), MultitaskWindowManager.mainSceneSession?.persistentIdentifier == scene1.session.persistentIdentifier {\n                        MultitaskWindowManager.mainSceneSession = nil\n                    }", "clear disconnected main scene")
    bootstrap = replace(bootstrap, "extern char **environ;", "#include <math.h>\n" + DIRECT_CONTROL + DIRECT_RUNTIME + "\nextern char **environ;", "direct return presenter")
    bootstrap = replace(bootstrap, "    // Go!", "    // Install before guest UIApplication/scene creation, never inside LiveProcess.\n    if (!isLiveProcess && !isSideStore) {\n        lcDirectReturnPresenter = [LCDirectReturnPresenter new];\n    }\n    // Go!", "direct launch route")
    settings = replace(settings, "    @State var errorShow = false", '    @AppStorage("LCHideReturnControl", store: UserDefaults.lc()) private var hideReturnControl = false\n    @State var errorShow = false', "visibility preference")
    settings = replace(settings, "            Form {", '''            Form {
                Section("Guest Controls") {
                    Toggle("Show Return Button", isOn: Binding(get: { !hideReturnControl }, set: { hideReturnControl = !$0 }))
                }''', "restore return control")
    updated = dict(zip(PATHS, (implementation, header, window, dock, hooks, model, tab, bootstrap, settings)))
    verify(updated)
    # Validate every anchor before writing any file, so upstream drift is not a partial patch.
    for relative, text in updated.items():
        (root / relative).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_guest_return.py <pinned-livecontainer-root>")
    patch(Path(sys.argv[1]))
