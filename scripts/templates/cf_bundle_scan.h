#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// LC_CF_BUNDLE_BOUNDED_SCAN_V1
// Bounds existing upstream instruction patterns; it does not infer a new OS
// layout. Every instruction read, including branch destinations, uses the
// caller's checked reader. An unsupported pattern returns no writable address.
enum { LCMainCFBundleInstructionBudget = 256 };
typedef bool (*LCMainCFBundleInstructionReader)(uintptr_t, uint32_t *);

static uintptr_t LCFindMainCFBundleAddress(uintptr_t start, bool adjacentTBZ,
                                         LCMainCFBundleInstructionReader readInstruction) {
    const uintptr_t byteCount = LCMainCFBundleInstructionBudget * sizeof(uint32_t);
    if (!start || start % sizeof(uint32_t) || !readInstruction ||
        start > UINTPTR_MAX - byteCount) return 0;
    const uintptr_t end = start + byteCount;

    for (size_t index = 0; index < LCMainCFBundleInstructionBudget; ++index) {
        uintptr_t pc = start + index * sizeof(uint32_t);
        uint32_t instruction = 0;
        if (!readInstruction(pc, &instruction)) return 0;
        uintptr_t loadAddress = 0;
        if (adjacentTBZ) {
            if ((instruction & 0x7F000000) != 0x36000000) continue;
            loadAddress = pc + sizeof(uint32_t);
        } else {
            loadAddress = (uintptr_t)aarch64_get_tbnz_jump_address(instruction, pc);
            if (!loadAddress) continue;
        }
        // pc-1 must belong to this scan, and the load must be an aligned
        // instruction inside the same finite window before it can be read.
        if (index == 0 || loadAddress < start || loadAddress >= end ||
            (loadAddress - start) % sizeof(uint32_t)) return 0;
        uint32_t adrp = 0, load = 0;
        if (!readInstruction(pc - sizeof(uint32_t), &adrp) ||
            !readInstruction(loadAddress, &load)) return 0;
        // The existing emulator validates ADRP/LDR opcodes and register match.
        return (uintptr_t)aarch64_emulate_adrp_ldr(adrp, load, pc - sizeof(uint32_t));
    }
    return 0;
}
