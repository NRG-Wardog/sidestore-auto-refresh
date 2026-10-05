#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define TARGET_OS_SIMULATOR 1
#define YES 1
#define NO 0
#define KERN_SUCCESS 0
typedef int BOOL;
typedef void *CFBundleRef;
typedef uintptr_t mach_vm_address_t, mach_vm_size_t, vm_offset_t;
typedef unsigned int mach_msg_type_number_t;
static int oldBundle, guestBundle;
static void *cache, *testGuestBundle;
static bool denyRead, shortRead, omitPattern, denyWrite;
static int nsWrites, cacheWrites, successes;
static CFBundleRef __attribute__((aligned(16))) CFBundleGetMainBundle(void) { return &oldBundle; }
static int mach_task_self(void) { return 1; }
static int mach_vm_read_overwrite(int task, mach_vm_address_t address, mach_vm_size_t size,
                                 mach_vm_address_t destination, mach_vm_size_t *copied) {
    assert(task == 1);
    *copied = 0;
    if (denyRead) return 1;
    uintptr_t start = (uintptr_t)CFBundleGetMainBundle;
    if (address == (uintptr_t)&cache && size == sizeof(cache)) {
        memcpy((void *)destination, &cache, size);
    } else if (address >= start && address - start < 256 * sizeof(uint32_t) && size == 4) {
        size_t index = (address - start) / 4;
        uint32_t instruction = omitPattern ? 0 : (index == 1 ? 0x37000020 : 0);
        memcpy((void *)destination, &instruction, size);
    } else return 1;
    *copied = shortRead ? size - 1 : size;
    return KERN_SUCCESS;
}
static int mach_vm_write(int task, mach_vm_address_t address, vm_offset_t bytes,
                         mach_msg_type_number_t size) {
    assert(task == 1 && address == (uintptr_t)&cache && size == sizeof(cache));
    cacheWrites++;
    if (denyWrite) return 1;
    memcpy(&cache, (void *)bytes, size);
    return KERN_SUCCESS;
}
static uint64_t aarch64_get_tbnz_jump_address(uint32_t instruction, uint64_t pc) {
    return instruction == 0x37000020 ? pc + 4 : 0;
}
// Opcode decoding is separately exercised with the original pinned functions.
// This adapter test isolates OS read/write failures and launch propagation.
static uint64_t aarch64_emulate_adrp_ldr(uint32_t adrp, uint32_t load, uint64_t pc) {
    return (uintptr_t)&cache;
}

__PRODUCTION_HELPER__
__PRODUCTION_RUNTIME_WITH_NS_BUNDLE_DOUBLE__

static void overwriteMainNSBundle(void *bundle) { nsWrites++; assert(bundle == &guestBundle); }
static const char *launch(void *appBundle) {
__PRODUCTION_CALLER_WITH_STRING_LITERALS__
    successes++;
    return NULL;
}
static void reset(void) {
    cache = &oldBundle; testGuestBundle = &guestBundle;
    denyRead = shortRead = omitPattern = denyWrite = false;
    nsWrites = cacheWrites = successes = 0;
}
int main(void) {
    reset(); denyRead = true;
    assert(launch(&guestBundle) != NULL);
    assert(nsWrites == 0 && cacheWrites == 0 && successes == 0 && cache == &oldBundle);
    reset(); shortRead = true;
    assert(launch(&guestBundle) != NULL);
    assert(nsWrites == 0 && cacheWrites == 0 && successes == 0 && cache == &oldBundle);
    reset(); omitPattern = true;
    assert(launch(&guestBundle) != NULL);
    assert(nsWrites == 0 && cacheWrites == 0 && successes == 0 && cache == &oldBundle);
    reset(); cache = &guestBundle;
    assert(launch(&guestBundle) != NULL);
    assert(nsWrites == 0 && cacheWrites == 0 && successes == 0);
    reset(); denyWrite = true;
    assert(launch(&guestBundle) != NULL);
    assert(nsWrites == 1 && cacheWrites == 1 && successes == 0 && cache == &oldBundle);
    reset();
    assert(launch(&guestBundle) == NULL);
    assert(nsWrites == 1 && cacheWrites == 1 && successes == 1 && cache == &guestBundle);
    puts("CF_BUNDLE_FAILURE_PROPAGATION_PASS");
    return 0;
}
