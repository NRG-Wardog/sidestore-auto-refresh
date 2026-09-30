/* LC_APP_GROUP_RULE_SET_V1 behavioral harness.
 *
 * Executes the real production rule set from
 * scripts/templates/LCAppGroupIdentityRules.h. It is plain C with no Foundation
 * dependency, so it runs with any C toolchain, not only on a Mac.
 *
 * Each case below corresponds to a way the combined build previously split the
 * host and the embedded service across two different shared stores.
 */
#include <stdio.h>
#include <string.h>

#include "../../scripts/templates/LCAppGroupIdentityRules.h"

static int failures = 0;

static void expect(int condition, const char *label) {
    if (!condition) {
        failures++;
        printf("V3_APP_GROUP_IDENTITY_FAIL %s\n", label);
    }
}

static int selection(int runtimeSupplied, int runtimeAvailable, int packagedAvailable) {
    return LCAppGroupSelectionSourceFor(runtimeSupplied, runtimeAvailable, packagedAvailable);
}

/* Resolve the identifier this process would actually use, mirroring the Swift
 * identity resolver exactly: a runtime group is authoritative, the packaged list
 * is ranked only when nothing was published, and an unusable runtime group never
 * falls through. */
static const char *resolve(const char *runtimeGroup, int runtimeAvailable,
                           const char *const *packaged, size_t packagedCount) {
    int source = selection(runtimeGroup != NULL, runtimeGroup != NULL ? runtimeAvailable : 0,
                           LCAppGroupFirstPackagedGroup(packaged, packagedCount) != NULL);
    if (source == LCAppGroupSelectionUnavailable) {
        return NULL;
    }
    if (source == LCAppGroupSelectionRuntimeGroup) {
        return LCAppGroupIDIsWellFormed(runtimeGroup, strlen(runtimeGroup)) ? runtimeGroup : NULL;
    }
    return LCAppGroupFirstPackagedGroup(packaged, packagedCount);
}

int main(void) {
    /* A re-signer may grant only a team-suffixed group, not the packaged name. */
    const char *suffixed = "group.com.SideStore.SideStore.TESTTEAM";
    /* LiveContainer's own selection can remain an AltStore-owned group. */
    const char *altStore = "group.com.rileytestut.AltStore";
    const char *packaged = "group.com.SideStore.SideStore";
    const char *packagedOnly[] = {packaged};
    const char *both[] = {altStore, suffixed};
    const char *emptyPackaged[] = {NULL};

    /* 1. A SideStore-suffixed group is a legitimate runtime selection. */
    expect(LCAppGroupIDIsWellFormed(suffixed, strlen(suffixed)) == 1,
           "suffixed group must be well formed");
    expect(LCAppGroupIsPackagedSideStoreGroup(suffixed) == 1,
           "a team-suffixed group must rank as the packaged group");
    expect(LCAppGroupIsPackagedSideStoreGroup(altStore) == 0,
           "an AltStore-owned group is not the packaged group");
    expect(LCAppGroupIsPackagedSideStoreGroup("group.com.SideStore.SideStore.") == 0,
           "an empty team suffix must not rank as the packaged group");
    expect(LCAppGroupIsPackagedSideStoreGroup("group.com.SideStore.SideStore.TEAM-1") == 0,
           "a non-alphanumeric team suffix must not rank as the packaged group");
    expect(resolve(suffixed, 1, packagedOnly, 1) != NULL
               && strcmp(resolve(suffixed, 1, packagedOnly, 1), suffixed) == 0,
           "suffixed group must be selected");

    /* 2. An AltStore-owned selected group is NOT rejected for its name. */
    expect(LCAppGroupIDIsWellFormed(altStore, strlen(altStore)) == 1,
           "altstore group must be well formed");
    expect(resolve(altStore, 1, packagedOnly, 1) != NULL
               && strcmp(resolve(altStore, 1, packagedOnly, 1), altStore) == 0,
           "an explicitly selected AltStore-owned group must win over the packaged one");

    /* 3. Host/service parity. The two processes have DIFFERENT packaged
     *    entitlements and different process-local facts, which is the real
     *    situation: only the host's selection is forwarded. If the resolver
     *    preferred anything but the inherited runtime group, these two would
     *    diverge, and the host and the service would take different locks. */
    {
        const char *hostPackaged[] = {packaged, altStore};
        const char *servicePackaged[] = {suffixed};
        const char *hostView = resolve(altStore, 1, hostPackaged, 2);
        const char *serviceView = resolve(altStore, 1, servicePackaged, 1);
        expect(hostView != NULL && serviceView != NULL
                   && strcmp(hostView, serviceView) == 0,
               "host and service must resolve the same runtime group despite different packaged entitlements");
        /* The packaged lists really do disagree, so the agreement above is not
         * an artefact of identical inputs. */
        expect(strcmp(resolve(NULL, 0, hostPackaged, 2), resolve(NULL, 0, servicePackaged, 1)) != 0,
               "the packaged fallbacks must differ, or the parity case proves nothing");
        /* And the packaged ranking the rule documents is the production one:
         * the SideStore group outranks an AltStore-owned entry. */
        expect(strcmp(resolve(NULL, 0, hostPackaged, 2), packaged) == 0,
               "the packaged fallback must rank the SideStore group first");
    }

    /* 4. An unavailable runtime group fails; it never falls back. */
    expect(selection(1, 0, 1) == LCAppGroupSelectionUnavailable,
           "an unavailable selected group must be unavailable even with a packaged fallback");
    expect(resolve(altStore, 0, packagedOnly, 1) == NULL,
           "an unavailable selected group must not switch to the packaged group");

    /* 5. Conflicting Info.plist vs explicit runtime group. */
    {
        const char *chosen = resolve(altStore, 1, packagedOnly, 1);
        expect(chosen != NULL && strcmp(chosen, altStore) == 0
                   && strcmp(chosen, packaged) != 0,
               "a conflicting Info.plist entitlement must not override the runtime group");
        /* And with no runtime group the packaged list is used, ranked. */
        expect(resolve(NULL, 0, both, 2) != NULL
                   && strcmp(resolve(NULL, 0, both, 2), suffixed) == 0,
               "the packaged fallback must still be available for a launch that published nothing");
    }

    /* 6. Cross-process lock ownership: the group that names the lock file is
     *    the group the store is opened under, in both processes, and neither
     *    can reach a state where it locks a different one. */
    {
        const char *hostLock = resolve(altStore, 1, packagedOnly, 1);
        const char *serviceLock = resolve(altStore, 1, both, 2);
        expect(hostLock != NULL && strcmp(hostLock, serviceLock) == 0,
               "the process-shared lock must resolve one group in both processes");
        /* If either process could not open the published group it must get no
         * lock at all rather than a different one. */
        expect(resolve(altStore, 0, packagedOnly, 1) == NULL,
               "a process that cannot open the published group must take no lock, not another one");
        expect(selection(0, 0, 0) == LCAppGroupSelectionUnavailable,
               "no published and no packaged group must be unavailable");
        expect(resolve(NULL, 0, emptyPackaged, 1) == NULL,
               "an empty packaged list must be unavailable");
    }

    /* 7. Nothing malformed, path-shaped or unbounded reaches a container
     *    lookup, a UserDefaults suite name or a lock path. */
    {
        char longGroup[LC_APP_GROUP_IDENTIFIER_MAX_LENGTH + 8];
        size_t index;
        const char *rejected[] = {
            "",                             /* empty */
            " group.com.SideStore",         /* leading space */
            "group.com.SideStore ",         /* trailing space */
            "group.com.SideStore/../other", /* separator and traversal */
            "group.com\\SideStore",         /* separator */
            "group.com.SideStore:shared",   /* colon */
            ".hidden.group",                /* leading dot */
            "group..com.SideStore",         /* traversal */
            "group.com.SideStore\n",        /* control character */
        };
        for (index = 0; index < sizeof(rejected) / sizeof(rejected[0]); index++) {
            expect(LCAppGroupIDIsWellFormed(rejected[index], strlen(rejected[index])) == 0,
                   "a malformed group identifier must be rejected");
        }
        memset(longGroup, 'a', sizeof(longGroup) - 1);
        longGroup[sizeof(longGroup) - 1] = '\0';
        expect(LCAppGroupIDIsWellFormed(longGroup, strlen(longGroup)) == 0,
               "an over-long identifier must be rejected");
        {
            char exactly[LC_APP_GROUP_IDENTIFIER_MAX_LENGTH + 1];
            memset(exactly, 'a', LC_APP_GROUP_IDENTIFIER_MAX_LENGTH);
            exactly[LC_APP_GROUP_IDENTIFIER_MAX_LENGTH] = '\0';
            expect(LCAppGroupIDIsWellFormed(exactly, strlen(exactly)) == 1,
                   "the maximum permitted length must still be accepted");
        }
        expect(LCAppGroupIDIsWellFormed(NULL, 0) == 0,
               "a null identifier must be rejected");
        /* A malformed entry never becomes the packaged fallback. */
        {
            const char *malformed[] = {"/etc/passwd", "..", "group.com.SideStore"};
            const char *picked = LCAppGroupFirstWellFormed(malformed, 3);
            expect(picked != NULL && strcmp(picked, "group.com.SideStore") == 0,
                   "the packaged scan must skip malformed entries");
        }
    }

    if (failures == 0) {
        puts("V3_APP_GROUP_IDENTITY_PASS");
        return 0;
    }
    printf("V3_APP_GROUP_IDENTITY_FAILURES=%d\n", failures);
    return 1;
}
