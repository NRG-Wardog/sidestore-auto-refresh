#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

__PRODUCTION_EMULATORS__
__PRODUCTION_HELPER__

static uintptr_t baseline_scan(uint32_t *pc, bool adjacentTBZ) {
    void **mainBundleAddr = 0;
    if (adjacentTBZ) {
__BASELINE_MODERN_LOOP__
    } else {
__BASELINE_LEGACY_LOOP__
    }
    return (uintptr_t)mainBundleAddr;
}

static uintptr_t readableStart;
static size_t readableBytes, reads, deniedReads;
static bool checked_read(uintptr_t address, uint32_t *value) {
    reads++;
    if (address < readableStart || address - readableStart > readableBytes - sizeof(*value)) {
        deniedReads++;
        return false;
    }
    memcpy(value, (const void *)address, sizeof(*value));
    return true;
}

static void reset(uint32_t *code) {
    for (int i = 0; i < LCMainCFBundleInstructionBudget; ++i) code[i] = 0xD503201F;
    readableStart = (uintptr_t)code;
    readableBytes = LCMainCFBundleInstructionBudget * sizeof(*code);
    reads = deniedReads = 0;
}

int main(int argc, char **argv) {
    assert(argc == 2);
    long page = sysconf(_SC_PAGESIZE);
    assert(page >= LCMainCFBundleInstructionBudget * sizeof(uint32_t));
    char *mapping = mmap(NULL, 3 * page, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(mapping != MAP_FAILED);
    assert(mprotect(mapping + page, page, PROT_READ | PROT_WRITE) == 0);
    uint32_t *code = (uint32_t *)(mapping + 2 * page) - LCMainCFBundleInstructionBudget;
    reset(code);

    bool baseline = strncmp(argv[1], "baseline-", 9) == 0;
    const char *scenario = argv[1] + (baseline ? 9 : 6);
    bool modern = true;
    if (strcmp(scenario, "first") == 0) {
        code = (uint32_t *)(mapping + page);
        reset(code);
        code[0] = 0x36000000;
    } else if (strcmp(scenario, "last") == 0) {
        code[LCMainCFBundleInstructionBudget - 2] = 0x90000000;
        code[LCMainCFBundleInstructionBudget - 1] = 0x36000000;
    } else if (strcmp(scenario, "target") == 0) {
        modern = false;
        code[0] = 0x90000000;
        code[1] = 0x37000000 | ((LCMainCFBundleInstructionBudget - 1) << 5);
    } else {
        assert(strcmp(scenario, "none") == 0 || strcmp(scenario, "positive") == 0);
    }

    if (baseline) {
        (void)baseline_scan(code, modern);
        // Every baseline mode supplied by the Python test should have faulted
        // on its protected neighbor/target page, not returned a successful scan.
        return 99;
    }
    if (strcmp(scenario, "positive") != 0) {
        assert(LCFindMainCFBundleAddress((uintptr_t)code, modern, checked_read) == 0);
        assert(reads <= LCMainCFBundleInstructionBudget);
        assert(deniedReads == 0);
        return 0;
    }

    code[0] = 0x90000000; // Existing ADRP x0 pattern.
    code[1] = 0x36000000; // Existing iOS27 TBZ pattern.
    code[2] = 0xF9400000; // LDR x0, [x0].
    uintptr_t expected = (uintptr_t)code & ~(uintptr_t)0xFFF;
    assert(LCFindMainCFBundleAddress((uintptr_t)code, true, checked_read) == expected);
    assert(deniedReads == 0);
    code[1] = 0x37000000 | (3 << 5); // Existing legacy target at index4.
    code[4] = 0xF9400000;
    assert(LCFindMainCFBundleAddress((uintptr_t)code, false, checked_read) == expected);
    code[0] = 0x90000001; // Mismatched ADRP/LDR register.
    assert(LCFindMainCFBundleAddress((uintptr_t)code, false, checked_read) == 0);
    code[0] = 0xD503201F; // Invalid predecessor opcode.
    assert(LCFindMainCFBundleAddress((uintptr_t)code, false, checked_read) == 0);
    code[0] = 0x90000000;
    code[4] = 0xD503201F; // Invalid target opcode.
    assert(LCFindMainCFBundleAddress((uintptr_t)code, false, checked_read) == 0);
    code[4] = 0xF9400000;
    readableBytes = 2 * sizeof(uint32_t); // Checked target read fails.
    assert(LCFindMainCFBundleAddress((uintptr_t)code, false, checked_read) == 0);
    assert(deniedReads == 1);
    reset(code);
    assert(LCFindMainCFBundleAddress(0, true, checked_read) == 0);
    assert(LCFindMainCFBundleAddress((uintptr_t)code + 1, true, checked_read) == 0);
    assert(LCFindMainCFBundleAddress(UINTPTR_MAX - 3, true, checked_read) == 0);
    assert(LCFindMainCFBundleAddress((uintptr_t)code, true, NULL) == 0);
    assert(reads == 0);
    readableBytes = sizeof(uint32_t);
    assert(LCFindMainCFBundleAddress((uintptr_t)code, true, checked_read) == 0);
    assert(reads == 2 && deniedReads == 1);
    puts("CF_BUNDLE_BOUNDED_SCAN_PASS");
    assert(munmap(mapping, 3 * page) == 0);
    return 0;
}
