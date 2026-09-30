#import <Foundation/Foundation.h>
#import "../../scripts/templates/LCAppGroupSelectionPolicy.h"

int main(void) {
    @autoreleasepool {
        for (NSString *hostSelection in @[@"group.com.rileytestut.AltStore",
                                         @"group.com.SideStore.SideStore.TESTTEAM"]) {
            NSString *inherited = LCValidatedAppGroupID(hostSelection, ^BOOL(NSString *groupID) {
                return [groupID isEqualToString:hostSelection];
            });
            NSCAssert([inherited isEqualToString:hostSelection],
                      @"LiveProcess must honor the host-selected group when its entitlement can open it");

            NSMutableDictionary *launchPayload = [NSMutableDictionary dictionary];
            if (inherited) launchPayload[@"lcAppGroupID"] = inherited;
            NSString *liveProcessSelection = LCValidatedAppGroupID(launchPayload[@"lcAppGroupID"], ^BOOL(NSString *groupID) {
                return [groupID isEqualToString:hostSelection];
            });
            NSCAssert([liveProcessSelection isEqualToString:hostSelection],
                      @"the validated host choice survives the extension launch payload and LiveProcess validation");

            // Staging and recovery consume this exact inherited ID. A re-signer
            // may grant only the team-suffixed group, not the original base ID.

            NSString *unavailable = LCValidatedAppGroupID(hostSelection, ^BOOL(NSString *groupID) {
                return NO;
            });
            NSCAssert(unavailable == nil,
                      @"an inherited but inaccessible group must be rejected");
        }

        NSString *malformed = LCValidatedAppGroupID(@[@"not", @"a group"], ^BOOL(NSString *groupID) {
            return YES;
        });
        NSCAssert(malformed == nil,
                  @"non-string launch values must never be treated as group identifiers");
        puts("V3_APP_GROUP_SELECTION_PASS");
    }
    return 0;
}
