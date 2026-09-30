#ifndef LC_APP_GROUP_IDENTITY_RULES_H
#define LC_APP_GROUP_IDENTITY_RULES_H

#include <stddef.h>
#include <string.h>

/* LC_APP_GROUP_RULE_SET_V1
 *
 * The one App Group selection rule set for the combined build, in plain C so a
 * behavioral harness can execute it on any toolchain instead of only on a Mac.
 *
 * LC_RULE_GROUP_VISIBLE_ASCII
 *     An App Group identifier is printable ASCII with no whitespace, so it can
 *     never be a filesystem path, a control sequence or an embedded payload.
 * LC_RULE_GROUP_NO_SEPARATOR
 *     '/' and '\' never appear. An identifier is never a path.
 * LC_RULE_GROUP_NO_COLON
 *     ':' never appears. A suite name is never a URL scheme.
 * LC_RULE_GROUP_NO_TRAVERSAL
 *     A leading '.' or any ".." is rejected before the value can reach a
 *     container lookup, a UserDefaults suite name or a lock file path.
 * LC_RULE_GROUP_BOUNDED_LENGTH
 *     The identifier is short enough to be a group and nothing else.
 * LC_RULE_EXPLICIT_WINS
 *     A runtime-selected group is authoritative for its own name too. A
 *     legitimate AltStore-owned group selected by LiveContainer is accepted;
 *     rejecting it because it lacks the SideStore name is the defect that made
 *     the host and the service resolve two different shared stores.
 * LC_RULE_EXPLICIT_FAIL_CLOSED
 *     A selected group this process cannot open fails. Continuing to the
 *     packaged Info.plist entitlement would move the shared store underneath
 *     the other process and silently orphan its state.
 * LC_RULE_PACKAGED_FALLBACK_ONLY
 *     With no selected group, the packaged Info.plist entitlement is the only
 *     fallback, for launches that never published one. The packaged SideStore
 *     group is preferred, then the first well-formed entry.
 *
 * V3SharedAppGroup in scripts/templates/v3_shared_app_group.swift implements
 * these same rules for the Swift host, the SideStoreSupport framework and the
 * embedded service. The two implementations cannot share a header across
 * targets, so tests/test_v3_shared_app_group.py executes this file and fails
 * when the Swift rule set drifts from it. Change both together.
 */

#define LC_APP_GROUP_IDENTIFIER_MAX_LENGTH 255
#define LC_APP_GROUP_PACKAGED_PREFIX "group.com.SideStore.SideStore"

typedef enum {
    /* No shared store exists for this process. Callers must fail explicitly. */
    LCAppGroupSelectionUnavailable = 0,
    /* A runtime-selected group was supplied (or inherited) and is usable. */
    LCAppGroupSelectionRuntimeGroup = 1,
    /* No runtime group was published; the packaged entitlement was used. */
    LCAppGroupSelectionPackagedFallback = 2
} LCAppGroupSelectionSource;

static inline int LCAppGroupRuleIsVisibleASCII(unsigned char value) {
    return value >= 0x21u && value <= 0x7Eu; /* LC_RULE_GROUP_VISIBLE_ASCII */
}

static inline int LCAppGroupRuleIsSeparator(unsigned char value) {
    return value == '/' || value == '\\'; /* LC_RULE_GROUP_NO_SEPARATOR */
}

static inline int LCAppGroupRuleIsReserved(unsigned char value) {
    return LCAppGroupRuleIsSeparator(value) || value == ':'; /* LC_RULE_GROUP_NO_COLON */
}

static inline int LCAppGroupIDIsWellFormed(const char *bytes, size_t length) {
    size_t index;
    unsigned char previous = 0;
    if (bytes == NULL || length == 0 ||
        length > (size_t)LC_APP_GROUP_IDENTIFIER_MAX_LENGTH) {
        return 0; /* LC_RULE_GROUP_BOUNDED_LENGTH */
    }
    for (index = 0; index < length; index++) {
        unsigned char current = (unsigned char)bytes[index];
        if (!LCAppGroupRuleIsVisibleASCII(current) || LCAppGroupRuleIsReserved(current)) {
            return 0;
        }
        if (previous == (unsigned char)'.' && current == (unsigned char)'.') { /* LC_RULE_GROUP_NO_TRAVERSAL */
            return 0; /* LC_RULE_GROUP_NO_TRAVERSAL */
        }
        previous = current;
    }
    if ((unsigned char)bytes[0] == (unsigned char)'.') { /* LC_RULE_GROUP_NO_TRAVERSAL */
        return 0; /* LC_RULE_GROUP_NO_TRAVERSAL */
    }
    return 1;
}

static inline int LCAppGroupSelectionSourceFor(int runtimeGroupSupplied,
                                               int runtimeGroupAvailable,
                                               int packagedFallbackAvailable) {
    if (runtimeGroupSupplied) {
        /* LC_RULE_EXPLICIT_WINS, LC_RULE_EXPLICIT_FAIL_CLOSED */
        if (!runtimeGroupAvailable) {
            return LCAppGroupSelectionUnavailable;
        }
        return LCAppGroupSelectionRuntimeGroup;
    }
    /* LC_RULE_PACKAGED_FALLBACK_ONLY */
    if (packagedFallbackAvailable) {
        return LCAppGroupSelectionPackagedFallback;
    }
    return LCAppGroupSelectionUnavailable;
}

/* The first well-formed entry, so a caller scanning a packaged entitlement list
 * never propagates a malformed or path-shaped value. */
static inline const char *LCAppGroupFirstWellFormed(const char * const *candidates,
                                                    size_t count) {
    size_t index;
    if (candidates == NULL) {
        return NULL;
    }
    for (index = 0; index < count; index++) {
        const char *candidate = candidates[index];
        if (candidate == NULL) {
            continue;
        }
        if (LCAppGroupIDIsWellFormed(candidate, strlen(candidate))) {
            return candidate;
        }
    }
    return NULL;
}

/* The packaged group, or the team-suffixed variant a re-signer writes. This is
 * the ranking LC_RULE_PACKAGED_FALLBACK_ONLY uses; it is the same predicate as
 * isPackagedSideStoreGroup in v3_shared_app_group.swift, and
 * LCAppGroupOrderPackaged is the same ordering the Swift identity resolver and
 * LCResolvedAppGroupID build. */
static inline int LCAppGroupIsPackagedSideStoreGroup(const char *candidate) {
    static const char base[] = LC_APP_GROUP_PACKAGED_PREFIX;
    const size_t baseLength = sizeof(base) - 1;
    size_t index, length;
    if (candidate == NULL) {
        return 0;
    }
    length = strlen(candidate);
    if (length == baseLength && strncmp(candidate, base, baseLength) == 0) {
        return 1;
    }
    if (length <= baseLength + 1 || strncmp(candidate, base, baseLength) != 0 ||
        candidate[baseLength] != '.') {
        return 0;
    }
    for (index = baseLength + 1; index < length; index++) {
        unsigned char value = (unsigned char)candidate[index];
        if (!((value >= '0' && value <= '9') ||
              (value >= 'A' && value <= 'Z') ||
              (value >= 'a' && value <= 'z'))) {
            return 0;
        }
    }
    return 1;
}

/* LC_RULE_PACKAGED_FALLBACK_ONLY, step one: the order a packaged entitlement
 * list is tried in. The SideStore group and its team-suffixed variants come
 * first, then the remaining well-formed entries. Nothing here knows about
 * availability: this is the order, not the choice. */
static inline size_t LCAppGroupOrderPackaged(const char * const *candidates,
                                             size_t count,
                                             const char **ordered,
                                             size_t orderedCapacity) {
    size_t index, written = 0;
    if (candidates == NULL || ordered == NULL) {
        return 0;
    }
    for (index = 0; index < count && written < orderedCapacity; index++) {
        const char *candidate = candidates[index];
        if (candidate == NULL || !LCAppGroupIDIsWellFormed(candidate, strlen(candidate))) {
            continue;
        }
        if (LCAppGroupIsPackagedSideStoreGroup(candidate)) {
            ordered[written++] = candidate;
        }
    }
    for (index = 0; index < count && written < orderedCapacity; index++) {
        const char *candidate = candidates[index];
        if (candidate == NULL || !LCAppGroupIDIsWellFormed(candidate, strlen(candidate))) {
            continue;
        }
        if (!LCAppGroupIsPackagedSideStoreGroup(candidate)) {
            ordered[written++] = candidate;
        }
    }
    return written;
}

/* LC_RULE_PACKAGED_FALLBACK_ONLY, step two: the first entry in that order. A
 * caller that also has to prove the container opens walks the same order with
 * its own availability check; this is the shortcut for a caller that does not. */
static inline const char *LCAppGroupFirstPackagedGroup(const char * const *candidates,
                                                       size_t count) {
    const char *ordered[64];
    size_t written = LCAppGroupOrderPackaged(candidates, count, ordered,
                                             sizeof(ordered) / sizeof(ordered[0]));
    return written == 0 ? NULL : ordered[0];
}

#endif /* LC_APP_GROUP_IDENTITY_RULES_H */
