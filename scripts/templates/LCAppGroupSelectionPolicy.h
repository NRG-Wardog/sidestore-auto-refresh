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
// scripts/templates/v3_shared_app_group.swift. Publishing the resolved group is
// the Swift resolver's job: only Swift can see the whole identity, and an
// Objective-C publisher would be a second implementation of the same decision.
static inline NSString *LCValidatedAppGroupID(id candidate,
                                              BOOL (^isAvailable)(NSString *groupID)) {
    if (![candidate isKindOfClass:NSString.class] || isAvailable == nil) {
        return nil;
    }
    NSString *groupID = (NSString *)candidate;
    // The rule set measures bytes, so measure bytes: passing the UTF-16 length
    // would let a multi-byte identifier through a bound it cannot satisfy.
    const char *utf8 = groupID.UTF8String;
    if (utf8 == NULL || !LCAppGroupIDIsWellFormed(utf8, strlen(utf8))) {
        return nil;
    }
    if (!isAvailable(groupID)) {
        return nil;
    }
    return groupID;
}

/// Resolve the one App Group this process must use, applying the same
/// explicit-wins / fail-closed precedence as every Swift consumer. Returns nil
/// when no shared store exists, so callers fail instead of silently selecting a
/// different entitlement. The ranking comes from the rule set, not from a second
/// implementation written here.
static inline NSString *LCResolvedAppGroupID(id selectedGroup,
                                             NSArray<NSString *> * _Nullable packagedGroups,
                                             BOOL (^isAvailable)(NSString *groupID)) {
    if (selectedGroup != nil && [selectedGroup isKindOfClass:NSString.class]) {
        // LC_RULE_EXPLICIT_WINS, LC_RULE_EXPLICIT_FAIL_CLOSED
        return LCValidatedAppGroupID(selectedGroup, isAvailable);
    }
    // LC_RULE_PACKAGED_FALLBACK_ONLY. The rule set produces the order; this loop
    // decides which ranked entry this process can actually open. A packaged list
    // is a preference, not proof of entitlement, so an unopenable top entry falls
    // through to the next one instead of reporting no shared store at all.
    size_t count = (size_t)packagedGroups.count;
    if (count == 0) {
        return nil;
    }
    const char **candidates = (const char **)calloc(count, sizeof(char *));
    if (candidates == NULL) {
        return nil;
    }
    for (NSUInteger index = 0; index < packagedGroups.count; index++) {
        id candidate = packagedGroups[index];
        // A malformed ALTAppGroups entry must not reach a UTF8 conversion.
        candidates[index] = [candidate isKindOfClass:NSString.class]
            ? [(NSString *)candidate UTF8String] : NULL;
    }
    const char *ordered[64];
    size_t written = LCAppGroupOrderPackaged((const char *const *)candidates, count,
                                             ordered, sizeof(ordered) / sizeof(ordered[0]));
    NSString *resolved = nil;
    for (size_t index = 0; index < written && resolved == nil; index++) {
        resolved = LCValidatedAppGroupID([NSString stringWithUTF8String:ordered[index]], isAvailable);
    }
    free(candidates);
    return resolved;
}
