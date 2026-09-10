// Execute the injected actions and color decoder with Foundation on macOS.
// This checks state transitions and persistence, not UIKit rendering or gestures.
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#include <assert.h>
#include <math.h>
#include <stdio.h>

static NSUserDefaults *preferences;
@interface NSUserDefaults (ReturnTests)
+ (instancetype)lcUserDefaults;
@end
@implementation NSUserDefaults (ReturnTests)
+ (instancetype)lcUserDefaults { return preferences; }
@end

@interface UIColor : NSObject
@property double red;
@property double green;
@property double blue;
@property double alpha;
+ (instancetype)colorWithRed:(double)red green:(double)green blue:(double)blue alpha:(double)alpha;
@end
@implementation UIColor
+ (instancetype)colorWithRed:(double)red green:(double)green blue:(double)blue alpha:(double)alpha {
    UIColor *color = [UIColor new];
    color.red = red; color.green = green; color.blue = blue; color.alpha = alpha;
    return color;
}
@end

// COLOR_DECODER

@interface TestControl : NSObject
@property BOOL collapsed;
@property CGPoint position;
@property NSUInteger layoutRequests;
@property(copy) void (^action)(void);
- (void)setNeedsLayout;
- (void)collapse;
- (void)tapped;
@end
@implementation TestControl
- (void)setNeedsLayout { self.layoutRequests += 1; }
// COLLAPSE_METHOD
// TAPPED_METHOD
@end

static void assertColor(UIColor *color, NSUInteger expected) {
    assert(fabs(color.red - ((expected >> 16) & 0xFF) / 255.0) < 0.000001);
    assert(fabs(color.green - ((expected >> 8) & 0xFF) / 255.0) < 0.000001);
    assert(fabs(color.blue - (expected & 0xFF) / 255.0) < 0.000001);
    assert(color.alpha == 1);
}

int main(void) {
    @autoreleasepool {
        NSString *suite = [@"GuestReturnTests." stringByAppendingString:NSProcessInfo.processInfo.globallyUniqueString];
        preferences = [[NSUserDefaults alloc] initWithSuiteName:suite];
        @try {
            NSString *colorKey = @"LCGuestReturnTintRGB";
            assertColor(LCGuestReturnColor(colorKey, 0x007AFF), 0x007AFF);
            for (NSNumber *rgb in @[@0, @0xFFFFFF, @0x123456, @0xFF0000, @0x00FF00, @0x0000FF]) {
                [preferences setObject:rgb forKey:colorKey];
                assertColor(LCGuestReturnColor(colorKey, 0x007AFF), rgb.unsignedIntegerValue);
            }
            // Corrupt and older preference data must yield a visible default.
            for (id invalid in @[@"red", @[], @{}, @(-1), @0x1000000, @1.5, @(NAN), @(INFINITY)]) {
                [preferences setObject:invalid forKey:colorKey];
                assertColor(LCGuestReturnColor(colorKey, 0x007AFF), 0x007AFF);
            }
            [preferences removeObjectForKey:colorKey];
            assertColor(LCGuestReturnColor(colorKey, 0xF2F2F7), 0xF2F2F7);

            __block NSUInteger actions = 0;
            TestControl *control = [TestControl new];
            control.action = ^{ actions += 1; };
            control.position = CGPointMake(0.2, 0.7);
            [control collapse];
            assert(control.collapsed && control.position.x == 0 && control.position.y == 0.7);
            [control tapped];
            assert(!control.collapsed && actions == 0); // Expansion must never Return.
            [control tapped];
            assert(!control.collapsed && actions == 1); // Default remains expanded.
            assert([preferences objectForKey:@"LCHideReturnControl"] == nil);
            assert([preferences objectForKey:@"LCGuestReturnStartsCollapsed"] == nil);

            [preferences setBool:YES forKey:@"LCGuestReturnStartsCollapsed"];
            control.position = CGPointMake(0.8, 0.3);
            [control collapse];
            assert(control.position.x == 1 && control.position.y == 0.3);
            for (int reopen = 0; reopen < 3; reopen++) {
                NSUInteger before = actions;
                [control tapped];
                assert(!control.collapsed && actions == before);
                [control tapped];
                assert(control.collapsed && actions == before + 1);
                assert(control.position.x == 1 && control.position.y == 0.3);
            }
            assert([preferences boolForKey:@"LCGuestReturnStartsCollapsed"]);
            assert([preferences objectForKey:@"LCHideReturnControl"] == nil);
            [preferences setBool:NO forKey:@"LCGuestReturnStartsCollapsed"];
            [control tapped];
            [control tapped];
            assert(!control.collapsed);
            assert(control.layoutRequests > 0);
            puts("RETURN_CONTROL_TESTS_PASSED");
        } @finally {
            [preferences removePersistentDomainForName:suite];
        }
    }
    return 0;
}
