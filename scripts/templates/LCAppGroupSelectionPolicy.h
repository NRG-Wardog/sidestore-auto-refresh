#import <Foundation/Foundation.h>

// The host may forward its selected App Group to LiveProcess. Accept only a
// group identifier that the current process can actually open; never accept a
// filesystem path or trust the launch payload by itself.
static inline NSString *LCValidatedAppGroupID(id candidate,
                                              BOOL (^isAvailable)(NSString *groupID)) {
    if (![candidate isKindOfClass:NSString.class] || isAvailable == nil) {
        return nil;
    }
    NSString *groupID = (NSString *)candidate;
    if (groupID.length == 0 || !isAvailable(groupID)) {
        return nil;
    }
    return groupID;
}
