#import <Foundation/Foundation.h>
#import <stdlib.h>
#import "LCAppGroupIdentityRules.h"

// The host may forward its selected App Group to LiveProcess. Accept only a
// group identifier that the current process can actually open; never accept a
// filesystem path or trust the launch payload by itself.
//
// LC_APP_GROUP_RULE_SET_V1 in LCAppGroupIdentityRules.h is the single rule set.
// This wrapper only adapts it to Objective-C strings and blocks; it restates no
// rule, and neither does the Swift implementation in
// scripts/templates/v3_shared_app_group.swift.
static inline NSString *LCValidatedAppGroupID(id candidate,
                                              BOOL (^isAvailable)(NSString *groupID)) {
    if (![candidate isKindOfClass:NSString.class] || isAvailable == nil) {
        return nil;
    }
    NSString *groupID = (NSString *)candidate;
    if (!LCAppGroupIDIsWellFormed(groupID.UTF8String, (size_t)groupID.length)) {
        return nil;
    }
    if (!isAvailable(groupID)) {
        return nil;
    }
    return groupID;
}

/// The packaged SideStore group, or a team-suffixed variant of it, as written
/// into ALTAppGroups by the signer. Used only to rank the packaged fallback.
static inline BOOL LCIsPackagedSideStoreGroup(NSString *groupID) {
    static NSString *const base = @"group.com.SideStore.SideStore";
    if ([groupID isEqualToString:base]) {
        return YES;
    }
    if (![groupID hasPrefix:[base stringByAppendingString:@"."]]) {
        return NO;
    }
    NSString *suffix = [groupID substringFromIndex:base.length + 1];
    if (suffix.length == 0) {
        return NO;
    }
    for (NSUInteger index = 0; index < suffix.length; index++) {
        unichar character = [suffix characterAtIndex:index];
        if (!((character >= '0' && character <= '9') ||
              (character >= 'A' && character <= 'Z') ||
              (character >= 'a' && character <= 'z'))) {
            return NO;
        }
    }
    return YES;
}

/// Resolve the one App Group this process must use, applying the same
/// explicit-wins / fail-closed precedence as every Swift consumer. Returns nil
/// when no shared store exists, so callers fail instead of silently selecting a
/// different entitlement.
static inline NSString *LCResolvedAppGroupID(id selectedGroup,
                                             NSArray<NSString *> * _Nullable packagedGroups,
                                             BOOL (^isAvailable)(NSString *groupID)) {
    if (selectedGroup != nil && [selectedGroup isKindOfClass:NSString.class]) {
        // LC_RULE_EXPLICIT_WINS, LC_RULE_EXPLICIT_FAIL_CLOSED: a selected group
        // is authoritative for its own name, and an unusable one fails here
        // rather than falling through to a different shared store.
        return LCValidatedAppGroupID(selectedGroup, isAvailable);
    }
    // LC_RULE_PACKAGED_FALLBACK_ONLY
    for (NSString *candidate in packagedGroups) {
        if (LCIsPackagedSideStoreGroup(candidate) && isAvailable(candidate)) {
            return LCValidatedAppGroupID(candidate, isAvailable);
        }
    }
    for (NSString *candidate in packagedGroups) {
        if (LCValidatedAppGroupID(candidate, isAvailable)) {
            return candidate;
        }
    }
    return nil;
}

/// Publish the host's own selection so the embedded service, which cannot see
/// LiveContainer's Objective-C helper, resolves the identical group. Only a
/// validated identifier is published, so a later failure in the service is an
/// honest unavailable-store failure rather than a silent split.
static inline void LCPublishRuntimeAppGroup(id selectedGroup,
                                            BOOL (^isAvailable)(NSString *groupID)) {
    NSString *groupID = LCValidatedAppGroupID(selectedGroup, isAvailable);
    unsetenv("LC_V3_INHERITED_APP_GROUP");
    if (groupID) {
        setenv("LC_V3_INHERITED_APP_GROUP", groupID.UTF8String, 1);
    }
}
