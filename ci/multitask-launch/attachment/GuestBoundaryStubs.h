#import <UIKit/UIKit.h>
@interface AppSceneViewController : UIViewController
- (void)handleStatusBarTapAction:(UIAction *)action;
@end
@interface DecoratedAppSceneViewController : UIViewController
@property(nonatomic,strong) AppSceneViewController *appSceneVC;
@end
@interface VirtualWindowsHostView : UIView
@property(nonatomic) BOOL shouldForwardTapAction;
@end

@interface UIView (FixturePrivateView)
- (UIViewController *)_viewDelegate;
@end
