#import <Foundation/Foundation.h>
#import <stdlib.h>
#import "../../scripts/templates/LCAppGroupSelectionPolicy.h"

static BOOL gAltStoreAvailable = YES;
static BOOL gPackagedAvailable = YES;

static BOOL (^availability)(NSString *) = ^BOOL(NSString *groupID) {
    if ([groupID isEqualToString:@"group.com.rileytestut.AltStore"]) return gAltStoreAvailable;
    if ([groupID isEqualToString:@"group.com.SideStore.SideStore"]) return gPackagedAvailable;
    if ([groupID isEqualToString:@"group.com.SideStore.SideStore.TESTTEAM"]) return gPackagedAvailable;
    return NO;
};

/* NSCAssert is compiled out in a release build, which would make this harness
 * print its pass marker without checking anything. Fail explicitly instead. */
static void expect(BOOL condition, const char *label) {
    if (!condition) {
        fprintf(stderr, "V3_APP_GROUP_SELECTION_FAIL %s\n", label);
        exit(1);
    }
}

int main(void) {
    @autoreleasepool {
        for (NSString *hostSelection in @[@"group.com.rileytestut.AltStore",
                                         @"group.com.SideStore.SideStore.TESTTEAM"]) {
            gAltStoreAvailable = YES;
            gPackagedAvailable = YES;
            NSString *inherited = LCValidatedAppGroupID(hostSelection, availability);
            expect([inherited isEqualToString:hostSelection],
                   "LiveProcess must honor the host-selected group when its entitlement can open it");

            NSMutableDictionary *launchPayload = [NSMutableDictionary dictionary];
            if (inherited) launchPayload[@"lcAppGroupID"] = inherited;
            NSString *liveProcessSelection = LCValidatedAppGroupID(launchPayload[@"lcAppGroupID"], availability);
            expect([liveProcessSelection isEqualToString:hostSelection],
                   "the validated host choice survives the extension launch payload and LiveProcess validation");

            /* An AltStore-owned group is legitimate and must not be rejected for
             * lacking the SideStore name, even when the packaged entitlement
             * disagrees with it. */
            NSArray<NSString *> *hostPackaged = @[@"group.com.SideStore.SideStore",
                                                 @"group.com.rileytestut.AltStore"];
            NSString *resolved = LCResolvedAppGroupID(hostSelection, hostPackaged, availability);
            expect([resolved isEqualToString:hostSelection],
                   "an explicit runtime group wins over a conflicting packaged entitlement");

            /* Host and service carry different packaged entitlements. Only the
             * host's selection is forwarded, so the service must land on the
             * same identifier rather than on its own packaged list. */
            NSString *serviceResolved = LCResolvedAppGroupID(nil, @[hostSelection], availability);
            expect([serviceResolved isEqualToString:resolved],
                   "the service and the host must resolve the same runtime App Group");
            NSString *hostFallback = LCResolvedAppGroupID(nil, hostPackaged, availability);
            expect(![hostFallback isEqualToString:serviceResolved],
                   "the packaged fallbacks must differ, or the parity case proves nothing");
            expect([hostFallback isEqualToString:@"group.com.SideStore.SideStore"],
                   "the packaged fallback ranks the SideStore group first");

/* An inherited but inaccessible group is rejected, and it never
         * falls through to the packaged entitlement: switching groups would
         * move the shared store underneath the other process. Make the group
         * under test inaccessible rather than some other one. */
        gAltStoreAvailable = NO;
        expect(LCValidatedAppGroupID(@"group.com.rileytestut.AltStore", availability) == nil,
               "an inherited but inaccessible group must be rejected");
        expect(LCResolvedAppGroupID(@"group.com.rileytestut.AltStore", hostPackaged, availability) == nil,
               "an inaccessible selected group must not fall back to a packaged entitlement");
        gAltStoreAvailable = YES;
        gPackagedAvailable = NO;
        expect(LCValidatedAppGroupID(@"group.com.SideStore.SideStore.TESTTEAM", availability) == nil,
               "an inaccessible team-suffixed group must be rejected");
        gPackagedAvailable = YES;

            /* A malformed or path-shaped launch value is never a group. */
            for (id malformed in @[@"", @" group.com.SideStore", @"group.com.SideStore ",
                                   @"group.com.SideStore/../other", @".hidden", @"group..x",
                                   @[@"not", @"a group"]]) {
                expect(LCValidatedAppGroupID(malformed, availability) == nil,
                       "malformed and path-shaped launch values must never be treated as group identifiers");
            }
        }

        /* With nothing published, the packaged entitlement is the fallback, and
         * the SideStore group outranks an AltStore-owned entry. */
        gPackagedAvailable = YES;
        NSString *fallback = LCResolvedAppGroupID(nil, @[@"group.com.rileytestut.AltStore",
                                                         @"group.com.SideStore.SideStore"], availability);
        expect([fallback isEqualToString:@"group.com.SideStore.SideStore"],
               "the packaged fallback ranks the SideStore group first");
        expect(LCResolvedAppGroupID(nil, @[], availability) == nil,
               "no published and no packaged group is an unavailable shared store");
        expect(LCResolvedAppGroupID(nil, @[@"/etc/passwd", @".."], availability) == nil,
               "a packaged list of only malformed entries is an unavailable shared store");
        /* A ranked entry this process cannot open falls through to the next
         * usable one, because the packaged list is a preference, not proof. */
        gPackagedAvailable = NO;
        expect(LCResolvedAppGroupID(nil, @[@"group.com.SideStore.SideStore",
                                           @"group.com.rileytestut.AltStore"], availability) != nil,
               "an unopenable packaged SideStore group falls through to an openable entry");
        gPackagedAvailable = YES;
        puts("V3_APP_GROUP_SELECTION_PASS");
    }
    return 0;
}
