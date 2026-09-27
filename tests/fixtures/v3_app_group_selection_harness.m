#import <Foundation/Foundation.h>
#import "../../scripts/templates/LCAppGroupSelectionPolicy.h"

int main(void) {
    @autoreleasepool {
        NSString *hostSelection = @"group.com.rileytestut.AltStore";
        NSString *inherited = LCValidatedAppGroupID(hostSelection, ^BOOL(NSString *groupID) {
            return [groupID isEqualToString:hostSelection];
        });
        NSCAssert([inherited isEqualToString:hostSelection],
                  @"LiveProcess must honor the host-selected group when its entitlement can open it");

        NSString *unavailable = LCValidatedAppGroupID(hostSelection, ^BOOL(NSString *groupID) {
            return NO;
        });
        NSCAssert(unavailable == nil,
                  @"an inherited but inaccessible group must be rejected");

        NSString *malformed = LCValidatedAppGroupID(@[@"not", @"a group"], ^BOOL(NSString *groupID) {
            return YES;
        });
        NSCAssert(malformed == nil,
                  @"non-string launch values must never be treated as group identifiers");
        puts("V3_APP_GROUP_SELECTION_PASS");
    }
    return 0;
}
