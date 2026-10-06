// Actual emitted Objective-C launch methods, with Foundation and UIKit/OS doubles.
// This is callback/ownership evidence, not extension or on-device scene evidence.
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <dispatch/dispatch.h>
#include <assert.h>
#include <stdio.h>

@interface NSString (Localization)
@property(readonly) NSString *loc;
@end
@implementation NSString (Localization)
- (NSString *)loc { return self; }
@end
@interface NSUserDefaults (GuestTests)
+ (instancetype)lcSharedDefaults;
@end
@implementation NSUserDefaults (GuestTests)
+ (instancetype)lcSharedDefaults { return NSUserDefaults.standardUserDefaults; }
@end

@interface Layer : NSObject
@property BOOL masksToBounds;
@end
@implementation Layer @end
@interface UIView : NSObject
@property CGRect bounds;
@property NSUInteger autoresizingMask;
@property BOOL hidden;
@property NSUInteger removalCount;
@property(nonatomic, strong) Layer *layer;
- (instancetype)initWithFrame:(CGRect)frame;
- (void)removeFromSuperview;
- (void)insertSubview:(UIView *)view atIndex:(NSUInteger)index;
+ (void)transitionWithView:(UIView *)view duration:(double)duration options:(NSUInteger)options animations:(void (^)(void))animations completion:(void (^)(BOOL))completion;
@end
@implementation UIView
- (instancetype)init { if ((self = [super init])) _layer = [Layer new]; return self; }
- (instancetype)initWithFrame:(CGRect)frame { return [self init]; }
- (void)removeFromSuperview { self.removalCount += 1; }
- (void)insertSubview:(UIView *)view atIndex:(NSUInteger)index {}
+ (void)transitionWithView:(UIView *)view duration:(double)duration options:(NSUInteger)options animations:(void (^)(void))animations completion:(void (^)(BOOL))completion { animations(); completion(YES); }
@end
@interface UILabel : UIView
@property NSUInteger lineBreakMode;
@property NSUInteger numberOfLines;
@property(nonatomic, strong) NSString *text;
@property NSUInteger textAlignment;
@end
@implementation UILabel @end

enum { UIViewAutoresizingFlexibleWidth=1, UIViewAutoresizingFlexibleHeight=2,
       UIViewAnimationOptionTransitionCurlUp=1, NSLineBreakByWordWrapping=1,
       NSTextAlignmentCenter=1, UIAlertControllerStyleAlert=1,
       UIAlertActionStyleCancel=1, UIAlertActionStyleDefault=2 };
@interface UIAlertAction : NSObject
+ (instancetype)actionWithTitle:(NSString *)title style:(NSUInteger)style handler:(void (^)(UIAlertAction *))handler;
@end
@implementation UIAlertAction
+ (instancetype)actionWithTitle:(NSString *)title style:(NSUInteger)style handler:(void (^)(UIAlertAction *))handler { return [self new]; }
@end
@interface UIAlertController : NSObject
+ (instancetype)alertControllerWithTitle:(NSString *)title message:(NSString *)message preferredStyle:(NSUInteger)style;
- (void)addAction:(UIAlertAction *)action;
@end
@implementation UIAlertController
+ (instancetype)alertControllerWithTitle:(NSString *)title message:(NSString *)message preferredStyle:(NSUInteger)style { return [self new]; }
- (void)addAction:(UIAlertAction *)action {}
@end
@interface UIPasteboard : NSObject
@property(nonatomic, strong) NSString *string;
+ (instancetype)generalPasteboard;
@end
@implementation UIPasteboard
+ (instancetype)generalPasteboard { return [self new]; }
@end
@interface MultitaskDockManager : NSObject
+ (instancetype)shared;
- (void)removeRunningApp:(NSString *)identifier;
@end
@implementation MultitaskDockManager
+ (instancetype)shared { return [self new]; }
- (void)removeRunningApp:(NSString *)identifier {}
@end
@interface MultitaskRelaunchManager : NSObject
+ (void)scheduleRelaunchIfNeededWithBundleId:(NSString *)bundle dataUUID:(NSString *)data isManualTermination:(BOOL)manual;
@end
@implementation MultitaskRelaunchManager
+ (void)scheduleRelaunchIfNeededWithBundleId:(NSString *)bundle dataUUID:(NSString *)data isManualTermination:(BOOL)manual {}
@end

@class DecoratedAppSceneViewController;
@interface AppSceneViewController : NSObject
@property int pid;
@property BOOL isAppRunning;
@property BOOL ownsContainer;
@property BOOL cleaned;
@property(readonly) bool isAppTerminationCleanUpCalled;
@property NSUInteger cleanupCount;
@property(nonatomic, strong) NSError *lcLaunchError;
@property(nonatomic, strong) NSString *bundleId;
@property(weak) DecoratedAppSceneViewController *delegate;
- (void)appTerminationCleanUp;
- (void)terminate;
@end
@interface DecoratedAppSceneViewController : NSObject
@property BOOL lcLaunchSettled;
@property bool isAppTerminationRequested;
@property int pid;
@property(nonatomic, strong) NSString *dataUUID;
@property(nonatomic, strong) UIView *view;
@property(nonatomic, strong) AppSceneViewController *appSceneVC;
@property(copy) void (^pidAvailableHandler)(NSNumber *, NSError *);
- (void)lcCompleteLaunch:(AppSceneViewController *)controller error:(NSError *)error;
- (void)appSceneVC:(AppSceneViewController *)vc didInitializeWithError:(NSError *)error;
- (void)appSceneVCAppDidExit:(AppSceneViewController *)vc;
- (void)closeWindow;
- (void)updateOriginalFrame;
- (void)presentViewController:(id)controller animated:(BOOL)animated completion:(void (^)(void))completion;
@end
@implementation AppSceneViewController
- (bool)isAppTerminationCleanUpCalled { return self.cleaned; }
- (void)appTerminationCleanUp {
    if (self.cleaned) return;
    self.cleaned = YES;
    self.cleanupCount += 1;
    self.isAppRunning = NO;
    self.ownsContainer = NO;
    [self.delegate appSceneVCAppDidExit:self];
}
- (void)terminate { [self appTerminationCleanUp]; }
@end
@implementation DecoratedAppSceneViewController
- (instancetype)init { if ((self = [super init])) _view = [UIView new]; return self; }
- (void)updateOriginalFrame {}
- (void)presentViewController:(id)controller animated:(BOOL)animated completion:(void (^)(void))completion {}
// __PRODUCTION_METHODS__
@end

static DecoratedAppSceneViewController *makeGuest(int pid) {
    DecoratedAppSceneViewController *owner = [DecoratedAppSceneViewController new];
    owner.dataUUID = @"container";
    owner.appSceneVC = [AppSceneViewController new];
    owner.appSceneVC.pid = pid;
    owner.appSceneVC.isAppRunning = pid > 0;
    owner.appSceneVC.ownsContainer = pid > 0;
    owner.appSceneVC.delegate = owner;
    return owner;
}
static void drainMain(void) {
    __block BOOL done = NO;
    dispatch_async(dispatch_get_main_queue(), ^{ done = YES; });
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:2];
    while (!done && deadline.timeIntervalSinceNow > 0) {
        [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
    }
    assert(done);
}
int main(void) {
    @autoreleasepool {
        NSError *originalError = [NSError errorWithDomain:@"ActualExtension" code:765 userInfo:nil];
        __block NSUInteger calls = 0;
        DecoratedAppSceneViewController *failed = makeGuest(0);
        __weak DecoratedAppSceneViewController *weakFailed = failed;
        failed.pidAvailableHandler = ^(NSNumber *pid, NSError *error) {
            assert(pid == nil && error == originalError);
            assert(weakFailed.appSceneVC.cleaned && !weakFailed.appSceneVC.ownsContainer);
            assert(weakFailed.pidAvailableHandler == nil);
            calls += 1;
            // Re-entry through a duplicate terminal event cannot settle twice.
            [weakFailed appSceneVCAppDidExit:weakFailed.appSceneVC];
        };
        [failed appSceneVC:failed.appSceneVC didInitializeWithError:originalError];
        drainMain();
        if (calls != 1) return 23; // The original error branch never calls back.
        assert(failed.appSceneVC.cleanupCount == 1);
        [failed appSceneVC:failed.appSceneVC didInitializeWithError:originalError];
        drainMain(); assert(calls == 1);

        // Closing before a PID arrives must retire the owning controller.
        DecoratedAppSceneViewController *closed = makeGuest(0);
        __weak DecoratedAppSceneViewController *weakClosed = closed;
        __block NSUInteger closeCalls = 0;
        closed.pidAvailableHandler = ^(NSNumber *pid, NSError *error) {
            assert(pid == nil && error != nil);
            assert(weakClosed.appSceneVC.cleaned && !weakClosed.appSceneVC.ownsContainer);
            closeCalls += 1;
        };
        [closed closeWindow];
        assert(closeCalls == 1 && closed.appSceneVC.cleaned && closed.view.removalCount == 1);
        [closed appSceneVC:closed.appSceneVC didInitializeWithError:nil];
        drainMain(); assert(closeCalls == 1);

        // The upstream terminated screen is deliberately retained until Close.
        // Already-cleaned and nil initializer controllers must remain closable.
        DecoratedAppSceneViewController *terminated = makeGuest(6);
        [terminated.appSceneVC appTerminationCleanUp];
        assert(terminated.view.removalCount == 0);
        [terminated closeWindow];
        assert(terminated.view.removalCount == 1);
        assert(terminated.appSceneVC.cleanupCount == 1);
        DecoratedAppSceneViewController *nilController = makeGuest(0);
        nilController.appSceneVC = nil;
        [nilController closeWindow];
        assert(nilController.view.removalCount == 1);

        // Cancellation/exit preserves the exact original NSError, then a new
        // owner can launch from the callback without the old owner touching it.
        DecoratedAppSceneViewController *cancelled = makeGuest(7);
        __weak DecoratedAppSceneViewController *weakCancelled = cancelled;
        __block DecoratedAppSceneViewController *replacement = nil;
        __block NSUInteger cancellationCalls = 0;
        cancelled.pidAvailableHandler = ^(NSNumber *pid, NSError *error) {
            assert(pid == nil && error == originalError);
            assert(!weakCancelled.appSceneVC.ownsContainer);
            cancellationCalls += 1;
            replacement = makeGuest(8);
        };
        cancelled.appSceneVC.lcLaunchError = originalError;
        [cancelled.appSceneVC appTerminationCleanUp];
        [cancelled appSceneVC:cancelled.appSceneVC didInitializeWithError:nil];
        [cancelled appSceneVC:cancelled.appSceneVC didInitializeWithError:originalError];
        drainMain();
        assert(cancellationCalls == 1 && replacement.appSceneVC.ownsContainer);

        DecoratedAppSceneViewController *live = makeGuest(9);
        __weak DecoratedAppSceneViewController *weakLive = live;
        __block NSUInteger successCalls = 0;
        live.pidAvailableHandler = ^(NSNumber *pid, NSError *error) {
            assert(pid.intValue == 9 && error == nil);
            assert(weakLive.appSceneVC.ownsContainer);
            successCalls += 1;
            [weakLive appSceneVC:weakLive.appSceneVC didInitializeWithError:nil];
        };
        [live appSceneVC:live.appSceneVC didInitializeWithError:nil];
        drainMain(); drainMain();
        assert(successCalls == 1 && live.appSceneVC.cleanupCount == 0);
        [live.appSceneVC appTerminationCleanUp];
        assert(successCalls == 1);
        puts("GUEST_LAUNCH_COMPLETION_PASS");
    }
    return 0;
}
