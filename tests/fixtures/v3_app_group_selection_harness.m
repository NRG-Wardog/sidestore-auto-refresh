#import <Foundation/Foundation.h>
#import "../../scripts/templates/LCAppGroupSelectionPolicy.h"

static BOOL gAltStoreAvailable = YES;
static BOOL gPackagedAvailable = YES;

static BOOL (^availability)(NSString *) = ^BOOL(NSString *groupID) {
    if ([groupID isEqualToString:@"group.com.rileytestut.AltStore"]) return gAltStoreAvailable;
    if ([groupID isEqualToString:@"group.com.SideStore.SideStore"]) return gPackagedAvailable;
    if ([groupID isEqualToString:@"group.com.SideStore.SideStore.TESTTEAM"]) return gPackagedAvailable;
    return NO;
};

int main(void) {
    @autoreleasepool {
        for (NSString *hostSelection in @[@"group.com.rileytestut.AltStore",
                                         @"group.com.SideStore.SideStore.TESTTEAM"]) {
            gAltStoreAvailable = YES;
            gPackagedAvailable = YES;
            NSString *inherited = LCValidatedAppGroupID(hostSelection, availability);
            NSCAssert([inherited isEqualToString:hostSelection],
                      @"LiveProcess must honor the host-selected group when its entitlement can open it");

            NSMutableDictionary *launchPayload = [NSMutableDictionary dictionary];
            if (inherited) launchPayload[@"lcAppGroupID"] = inherited;
            NSString *liveProcessSelection = LCValidatedAppGroupID(launchPayload[@"lcAppGroupID"], availability);
            NSCAssert([liveProcessSelection isEqualToString:hostSelection],
                      @"the validated host choice survives the extension launch payload and LiveProcess validation");

            // An AltStore-owned group is legitimate and must not be rejected for
            // lacking the SideStore name, even when the packaged entitlement
            // disagrees with it.
            NSArray<NSString *> *packaged = @[@"group.com.SideStore.SideStore"];
            NSString *resolved = LCResolvedAppGroupID(hostSelection, packaged, availability);
            NSCAssert([resolved isEqualToString:hostSelection],
                      @"an explicit runtime group wins over a conflicting packaged entitlement");

            // The service sees the same value republished as its inherited group
            // and resolves the identical identifier, so both processes take the
            // same cross-process lock and stage into the same container.
            NSString *serviceResolved = LCResolvedAppGroupID(nil, @[hostSelection], availability);
            NSCAssert([serviceResolved isEqualToString:resolved],
                      @"the service and the host must resolve the same runtime App Group");

            // An inherited but inaccessible group is rejected, and it never
            // falls through to the packaged entitlement: switching groups would
            // move the shared store underneath the other process.
            gAltStoreAvailable = NO;
            NSCAssert(LCValidatedAppGroupID(hostSelection, availability) == nil,
                      @"an inherited but inaccessible group must be rejected");
            NSCAssert(LCResolvedAppGroupID(hostSelection, packaged, availability) == nil,
                      @"an inaccessible selected group must not fall back to a packaged entitlement");
            gAltStoreAvailable = YES;

            // A malformed or path-shaped launch value is never a group.
            for (id malformed in @[@"", @" group.com.SideStore", @"group.com.SideStore ",
                                   @"group.com.SideStore/../other", @".hidden", @"group..x",
                                   @[@"not", @"a group"]]) {
                NSCAssert(LCValidatedAppGroupID(malformed, availability) == nil,
                          @"malformed and path-shaped launch values must never be treated as group identifiers");
            }
        }

        // With nothing published, the packaged entitlement is the fallback, and
        // the SideStore group outranks an AltStore-owned entry.
        gPackagedAvailable = YES;
        NSString *fallback = LCResolvedAppGroupID(nil, @[@"group.com.rileytestut.AltStore",
                                                         @"group.com.SideStore.SideStore"], availability);
        NSCAssert([fallback isEqualToString:@"group.com.SideStore.SideStore"],
                  @"the packaged fallback ranks the SideStore group first");
        NSCAssert(LCResolvedAppGroupID(nil, @[], availability) == nil,
                  @"no published and no packaged group is an unavailable shared store");

        // Publishing the host's validated selection is what keeps the service on
        // the same group; an unvalidated value publishes nothing at all.
        unsetenv("LC_V3_INHERITED_APP_GROUP");
        LCPublishRuntimeAppGroup(@"group.com.SideStore/../escape", availability);
        NSCAssert(getenv("LC_V3_INHERITED_APP_GROUP") == NULL,
                  @"a malformed group must never be published to the service");
        LCPublishRuntimeAppGroup(@"group.com.rileytestut.AltStore", availability);
        NSCAssert(strcmp(getenv("LC_V3_INHERITED_APP_GROUP"), "group.com.rileytestut.AltStore") == 0,
                  @"the host publishes its validated selection for the service");
        LCPublishRuntimeAppGroup(nil, availability);
        NSCAssert(getenv("LC_V3_INHERITED_APP_GROUP") == NULL,
                  @"publishing nothing clears a previously published group");
        puts("V3_APP_GROUP_SELECTION_PASS");
    }
    return 0;
}
