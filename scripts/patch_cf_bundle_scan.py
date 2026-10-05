"""Bound the pinned CFBundle instruction scan and fail startup explicitly."""
from pathlib import Path

MARKER = "LC_CF_BUNDLE_BOUNDED_SCAN_V1"
TEMPLATE = Path(__file__).with_name("templates") / "cf_bundle_scan.h"

RUNTIME = '''
static bool LCReadMainCFBundleMemory(uintptr_t address, void *value, size_t size) {
    mach_vm_size_t copied = 0;
    return mach_vm_read_overwrite(mach_task_self(), (mach_vm_address_t)address,
        (mach_vm_size_t)size, (mach_vm_address_t)(uintptr_t)value, &copied) == KERN_SUCCESS
        && copied == size;
}

static bool LCReadMainCFBundleInstruction(uintptr_t address, uint32_t *instruction) {
    return LCReadMainCFBundleMemory(address, instruction, sizeof(*instruction));
}

static void **LCResolveMainCFBundleAddress(void) {
    // Initialize the existing cache before checking the decoded storage value.
    CFBundleRef expected = CFBundleGetMainBundle();
    if (!expected) return NULL;
    bool adjacentTBZ = false;
#if !TARGET_OS_SIMULATOR
    if (@available(iOS 27.0, *)) adjacentTBZ = true;
#endif
    uintptr_t address = LCFindMainCFBundleAddress((uintptr_t)CFBundleGetMainBundle,
        adjacentTBZ, LCReadMainCFBundleInstruction);
    void *observed = NULL;
    if (!address || address % sizeof(void *) ||
        !LCReadMainCFBundleMemory(address, &observed, sizeof(observed)) ||
        observed != (void *)expected) return NULL;
    return (void **)address;
}

static BOOL overwriteMainCFBundle(void **address) {
    void *value = (__bridge void *)NSBundle.mainBundle._cfBundle;
    if (!address || !value) return NO;
    // A protection/layout change must return failure, never fault on a raw
    // pointer store or change VM protections to force the write through.
    return mach_vm_write(mach_task_self(), (mach_vm_address_t)(uintptr_t)address,
        (vm_offset_t)(uintptr_t)&value, (mach_msg_type_number_t)sizeof(value)) == KERN_SUCCESS;
}

'''

OLD_CALL = '''    // Overwrite NSBundle
    overwriteMainNSBundle(appBundle);

    // Overwrite CFBundle
    overwriteMainCFBundle();'''
NEW_CALL = '''    // Resolve the existing CF cache before changing NSBundle identity.
    void **mainCFBundleAddress = LCResolveMainCFBundleAddress();
    if (!mainCFBundleAddress) {
        return @"The main bundle layout could not be verified. Guest startup was stopped.";
    }

    // Overwrite NSBundle
    overwriteMainNSBundle(appBundle);

    // Overwrite CFBundle only after a bounded, checked lookup.
    if (!overwriteMainCFBundle(mainCFBundleAddress)) {
        return @"The main bundle cache could not be updated. Guest startup was stopped.";
    }'''


def patch_text(text: str) -> str:
    replacement = TEMPLATE.read_text(encoding="utf-8") + "\n" + RUNTIME
    include = "#include <mach/mach_vm.h>\n"
    if MARKER in text:
        if text.count(replacement) != 1 or text.count(NEW_CALL) != 1 or text.count(include) != 1:
            raise ValueError("CFBundle bounded scan is partial or changed")
        return text
    start = "void overwriteMainCFBundle(void) {"
    end = "void overwriteMainNSBundle(NSBundle *newBundle) {"
    if text.count(start) != 1 or text.count(end) != 1 or text.count(OLD_CALL) != 1:
        raise ValueError("Pinned CFBundle scan anchors changed")
    first, last = text.index(start), text.index(end)
    old = text[first:last]
    # Fail closed if the upstream strategy changed; do not replace an unknown
    # scan merely because its function happens to have the same name.
    required = ("while (true)", "aarch64_get_tbnz_jump_address", "aarch64_emulate_adrp_ldr",
                "if(@available(iOS 27.0, *))", "assert(mainBundleAddr != NULL)")
    if first >= last or old.count("while (true)") != 2 or any(x not in old for x in required):
        raise ValueError("Pinned CFBundle scan implementation changed")
    anchor = "#include <mach/mach.h>\n"
    if text.count(anchor) != 1:
        raise ValueError("Pinned Mach import anchor changed")
    text = text[:first] + replacement + text[last:]
    return text.replace(anchor, anchor + include, 1).replace(OLD_CALL, NEW_CALL, 1)


def patch_bootstrap(path: Path) -> None:
    original = path.read_text(encoding="utf-8")
    updated = patch_text(original)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
