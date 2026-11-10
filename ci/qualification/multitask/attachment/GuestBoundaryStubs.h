#import <UIKit/UIKit.h>
@interface AppSceneViewController : UIViewController
- (void)handleStatusBarTapAction:(UIAction *)action;
@end
@interface DecoratedAppSceneViewController : UIViewController
@property(nonatomic,strong) AppSceneViewController *appSceneVC;
@end
#import "VirtualWindowsHostView.h"
@interface BaselineVirtualWindowsHostView : VirtualWindowsHostView
@end

@interface UIView (FixturePrivateView)
- (UIViewController *)_viewDelegate;
@end
