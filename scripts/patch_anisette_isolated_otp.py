#!/usr/bin/env python3
"""Add an isolated, existing-blob-only OTP probe to the exact AnisetteKit pin."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

PIN = "1f5a7e36553cc865b873f222b87a6486c0bcc7bf"
TEMPLATES = Path(__file__).parent / "templates"
PATHS = ("Native/anisette_core_uc.cpp", "Native/include/anisette_core.h",
         "Native/Loader/elf_loader_emulator.cpp", "Native/Loader/elf_loader_emulator.h",
         "Sources/AnisetteDataProvider.swift")
ORIGINAL_SHA256 = dict(zip(PATHS, (
    "abee10baeca21b7d175872f1f05bd4c9b36f62dd563404e6e2a17b8a7c7c2d60",
    "887326b1b3d78426f39472654b58e75876d2e57f91ce7b0ff1bc3e0343ba0672",
    "9d2d00012d1019bf7e99c2b16a9af6570eec30f75a7a01cffa15be05c5b4384c",
    "83b76029f2360edb70cef85abc09ec8e2f6c7ee122fb4f16b2ac7458f48a9c7a",
    "f501ed0ee8ec218aa8e8c0237fca00ecc916193f6b8b14b77bf1b949850680fc")))


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Isolated Anisette OTP anchor drift: " + old[:80])
    return text.replace(old, new, 1)


def transform(path, source):
    if path == PATHS[0]:
        source = once(source, '#include <mutex>', '#include <mutex>\n#include <memory>\n#include <stdexcept>\n#include <algorithm>\n#include <errno.h>')
        source = once(source, 'static EmulatorVM *g_shared_vm = nullptr;',
                      'thread_local bool g_isolated_otp_logging_suppressed = false;\n\nstatic EmulatorVM *g_shared_vm = nullptr;')
        source = once(source, '    std::string &out_err\n)', '    std::string &out_err,\n    bool isolated = false\n)')
        for call in ('load_lib_ptr, {lib_path_vm, 0}', 'set_prov_ptr, {prov_path_vm}',
                     'set_id_ptr, {android_id_vm, (uint64_t)android_id.length()}'):
            source = once(source, 'run_vm_procedure(vm, ' + call + ')',
                          'run_vm_procedure(vm, ' + call + ', isolated ? 5000000 : 0, isolated ? 50000000 : 0)')
        start = source.index('int32_t get_anisette_headers_uc(')
        end = source.index('\nint32_t start_provision_uc(', start)
        original = source[start:end]
        helper = once(original, 'int32_t get_anisette_headers_uc(', 'static int32_t get_anisette_headers_uc_locked(')
        helper = once(helper, '    char **out_json\n)', '    char **out_json,\n    bool isolated\n)')
        helper = once(helper, '    std::lock_guard<std::mutex> lock(g_vm_mutex);\n', '')
        helper = once(helper, 'identifier, uuid_prov_dir, err)', 'identifier, uuid_prov_dir, err, isolated)')
        helper = once(helper, '    FILE* f = fopen(adi_pb_path.c_str(), "wb");\n    if (f) {\n        fwrite(adi_pb, 1, adi_pb_len, f);\n        fclose(f);\n    }',
                      '    if (!isolated) {\n        FILE* f = fopen(adi_pb_path.c_str(), "wb");\n        if (f) {\n            fwrite(adi_pb, 1, adi_pb_len, f);\n            fclose(f);\n        }\n    }')
        helper = once(helper, 'run_vm_procedure(vm, otp_req_ptr, {dsid, mid_ptr, mid_len_ptr, otp_ptr, otp_len_ptr})',
                      'run_vm_procedure(vm, otp_req_ptr, {dsid, mid_ptr, mid_len_ptr, otp_ptr, otp_len_ptr}, isolated ? 5000000 : 0, isolated ? 50000000 : 0)')
        for call in ('uc_mem_read(vm->uc, mid_ptr, &final_mid_addr, 8)',
                     'uc_mem_read(vm->uc, mid_len_ptr, &final_mid_len, 4)',
                     'uc_mem_read(vm->uc, otp_ptr, &final_otp_addr, 8)',
                     'uc_mem_read(vm->uc, otp_len_ptr, &final_otp_len, 4)',
                     'uc_mem_read(vm->uc, final_mid_addr, mid_data.data(), final_mid_len)',
                     'uc_mem_read(vm->uc, final_otp_addr, otp_data.data(), final_otp_len)'):
            helper = once(helper, call + ';',
                'if (' + call + ' != UC_ERR_OK && isolated) {\n'
                '        *out_json = strdup("{\\"error\\":\\"Isolated OTP output invalid\\"}");\n'
                '        return ANISETTE_ERR_INVALID_JSON_RESPONSE;\n    }')
        helper = once(helper, '    std::vector<uint8_t> mid_data(final_mid_len);',
            '''    if (isolated && (!final_mid_addr || !final_otp_addr || !final_mid_len || !final_otp_len ||
                     final_mid_len > 4096 || final_otp_len > 4096)) {
        *out_json = strdup("{\\"error\\":\\"Isolated OTP output invalid\\"}");
        return ANISETTE_ERR_INVALID_JSON_RESPONSE;
    }
    std::vector<uint8_t> mid_data(final_mid_len);''')
        source = source[:start] + helper + '\n' + (TEMPLATES / 'isolated_anisette_otp.cpp').read_text() + source[end:]
    elif path == PATHS[1]:
        source = once(source, 'int32_t start_provision_uc(', '''// V3_ISOLATED_ANISETTE_OTP_V1: caller supplies a private, existing temp root.
// Never provisions. The identifier derivation is identical to the normal API.
int32_t get_anisette_headers_isolated_uc(
    const char *lib_dir, const char *provisioning_dir, const uint8_t *identifier,
    const uint8_t *adi_pb, uint32_t adi_pb_len, char **out_json
);

int32_t start_provision_uc(''')
    elif path == PATHS[2]:
        source = once(source, '#include <random>', '#include <random>\n#include <stdexcept>\n#include <memory>')
        source = once(source, 'EmulatorVM::EmulatorVM()', 'EmulatorVM::EmulatorVM(bool checked)')
        source = once(source, '        abort();\n    }\n\n    uc_mem_map(uc, kHeapAddress',
                      '        if (checked) throw std::runtime_error("Isolated VM initialization failed");\n        abort();\n    }\n\n    auto check = [&](uc_err result) {\n        if (checked && result != UC_ERR_OK) {\n            uc_close(uc); uc = nullptr;\n            throw std::runtime_error("Isolated VM initialization failed");\n        }\n    };\n    check(uc_mem_map(uc, kHeapAddress')
        # Only constructor initialization is changed; checked=false preserves legacy semantics.
        begin = source.index('EmulatorVM::EmulatorVM(bool checked)')
        end = source.index('EmulatorVM::~EmulatorVM()', begin)
        block = source[begin:end]
        block = block.replace('UC_PROT_READ | UC_PROT_WRITE);', 'UC_PROT_READ | UC_PROT_WRITE));', 1)
        import re
        block = re.sub(r'(?m)^    (uc_mem_map\(.*\));$', r'    check(\1);', block)
        block = re.sub(r'(?m)^    (uc_mem_write\(.*\));$', r'    check(\1);', block)
        block = re.sub(r'(?m)^    (uc_hook_add\([^;]+\));$', r'    check(\1);', block)
        source = source[:begin] + block + source[end:]
        source = once(source, 'EmulatorVM::~EmulatorVM() {\n',
                      'EmulatorVM::~EmulatorVM() {\n    // V3_ISOLATED_ANISETTE_OTP_V1: dispose guest-owned host descriptors.\n    for (const auto &entry : fd_map) close(entry.second);\n    fd_map.clear();\n')
        source = once(source, '    g_active_vm = vm;\n', '''    // V3_ISOLATED_ANISETTE_OTP_V1: a temporary VM must not leave a dangling fallback.
    struct ActiveVMScope {
        EmulatorVM *previous;
        explicit ActiveVMScope(EmulatorVM *current) : previous(g_active_vm) { g_active_vm = current; }
        ~ActiveVMScope() { g_active_vm = previous; }
    } active_vm_scope(vm);
''')
        source = once(source, '    if (timeout_us == 0) timeout_us = 0;\n    if (max_count == 0) max_count = 0;',
                      '    if (vm->read_only_filesystem) {\n        if (timeout_us == 0) timeout_us = 5000000;\n        if (max_count == 0) max_count = 50000000;\n    }')
        source = once(source, 'static int linux_to_darwin_open_flags(int linux_flags) {', '''// V3_ISOLATED_ANISETTE_OTP_V1: probe guests cannot mutate the host filesystem.
// Staging occurs in the host wrapper before this VM is initialized. A native
// operation requiring a write fails closed rather than weakening this policy.
static bool deny_read_only_mutation(EmulatorVM *vm) {
    if (!vm->read_only_filesystem) return false;
    uint32_t denied = EPERM;
    uc_mem_write(vm->uc, vm->errno_addr, &denied, sizeof(denied));
    int64_t result = -1;
    uc_reg_write(vm->uc, UC_ARM64_REG_X0, &result);
    return true;
}

static int linux_to_darwin_open_flags(int linux_flags) {''')
        for name in ('write', 'ftruncate', 'mkdir', 'chmod', 'umask'):
            anchor = 'static void hook_' + name + '(EmulatorVM *vm) {\n'
            source = once(source, anchor, anchor + '    if (deny_read_only_mutation(vm)) return;\n')
        start = source.index('static void hook_open(EmulatorVM *vm) {')
        end = source.index('static void hook_close(', start)
        block = source[start:end]
        block = once(block, '    std::string path;\n', '''    // Linux access mode, O_CREAT, O_TRUNC, O_APPEND and O_TMPFILE.
    if (((flags & 3) != 0 || (flags & (0x0040 | 0x0200 | 0x0400 | 0x410000)) != 0) &&
        deny_read_only_mutation(vm)) return;
    std::string path;
''')
        source = source[:start] + block + source[end:]
        start = source.index('bool load_library_to_vm(')
        end = source.index('void relocate_all_vm_libraries(', start)
        block = source[start:end]
        block = once(block, '    fseek(f, 0, SEEK_END);',
                      '    std::unique_ptr<FILE, int (*)(FILE *)> file_owner(f, fclose);\n    fseek(f, 0, SEEK_END);')
        block = block.replace('fclose(f);', 'file_owner.reset();')
        source = source[:start] + block + source[end:]
        start = source.index('static void hook_open(')
        end = source.index('static void hook_close(', start)
        block = source[start:end]
        block = once(block, '    int host_fd = open(path.c_str(), host_flags, (mode_t)mode);', '''    int host_fd = open(path.c_str(), host_flags, (mode_t)mode);
    struct HostFDGuard {
        int fd;
        ~HostFDGuard() { if (fd >= 0) close(fd); }
    } host_fd_guard{host_fd};''')
        block = once(block, '        vm->fd_map[guest_fd] = host_fd;',
                      '        vm->fd_map[guest_fd] = host_fd;\n        host_fd_guard.fd = -1;')
        source = source[:start] + block + source[end:]
    elif path == PATHS[3]:
        source = once(source, '    EmulatorVM();', '    explicit EmulatorVM(bool checked = false);')
        source = once(source, '    uc_engine *uc;', '    bool read_only_filesystem = false;\n    uc_engine *uc;')
        source = once(source, '#define LOG_UC(...) anisetteCoreLog(__VA_ARGS__)',
                      'extern thread_local bool g_isolated_otp_logging_suppressed;\n'
                      '#define LOG_UC(...) do { if (!g_isolated_otp_logging_suppressed) anisetteCoreLog(__VA_ARGS__); } while (0)')
    elif path == PATHS[4]:
        source += '\n' + (TEMPLATES / 'isolated_anisette_otp.swift').read_text()
    else:
        raise ValueError("Unexpected Anisette source")
    return source


def expected_files(root):
    env = dict(os.environ, GIT_NO_LAZY_FETCH='1')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], env=env)
    if git('rev-parse', 'HEAD').decode().strip() != PIN:
        raise ValueError('Isolated OTP requires the exact pinned AnisetteKit revision')
    return {path: (git('show', PIN + ':' + path), None) for path in PATHS}


def _source_identity(details):
    return (details.st_dev, details.st_ino, details.st_mode, details.st_uid)


def _replace_verified_source(target, expected, original, details):
    """Replace an owned source atomically, retaining even a read-only mode."""
    current = target.lstat()
    if (not stat.S_ISREG(current.st_mode) or _source_identity(current) != _source_identity(details)
            or target.read_bytes() != original):
        raise ValueError('Anisette source changed before replacement: ' + str(target))
    if hasattr(os, 'geteuid') and current.st_uid != os.geteuid():
        raise ValueError('Anisette source is not owned by the current user: ' + str(target))
    descriptor, temporary = tempfile.mkstemp(prefix='.' + target.name + '.isolated-otp-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            descriptor = -1  # fdopen owns the descriptor, including exceptional exits.
            if stream.write(expected) != len(expected):
                raise OSError('Incomplete Anisette source write')
            stream.flush()
            os.fchmod(stream.fileno(), stat.S_IMODE(current.st_mode))
            os.fsync(stream.fileno())
        if (_source_identity(target.lstat()) != _source_identity(current)
                or target.read_bytes() != original):
            raise ValueError('Anisette source changed during replacement: ' + str(target))
        os.replace(temporary, target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def patch(root, verify=False):
    outputs = {}
    for relative, (original, _) in expected_files(root).items():
        target = root / relative
        for parent in (root, *target.parents):
            if parent == root.parent: break
            if parent.is_symlink():
                raise ValueError('Substituted Anisette source directory')
        if target.is_symlink() or not target.is_file():
            raise ValueError('Missing or substituted Anisette source: ' + relative)
        expected = transform(relative, original.decode()).encode()
        current = target.read_bytes()
        if current != expected and (verify or current != original):
            raise ValueError('Anisette source drift: ' + relative)
        details = target.lstat()
        if hasattr(os, 'geteuid') and details.st_uid != os.geteuid():
            raise ValueError('Anisette source is not owned by the current user: ' + relative)
        outputs[target] = (expected, current, details)
    if not verify:
        for target, (expected, current, details) in outputs.items():
            if current != expected:
                _replace_verified_source(target, expected, current, details)
    return len(outputs)


def _evidence_for(originals):
    files = []
    for relative in PATHS:
        original = originals[relative]
        if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256[relative]:
            raise ValueError('Pinned Anisette source hash drift: ' + relative)
        prepared = transform(relative, original.decode()).encode()
        files.append({'path': relative, 'original_sha256': ORIGINAL_SHA256[relative],
                      'prepared_sha256': hashlib.sha256(prepared).hexdigest()})
    return {'schema_version': 1, 'anisettekit_revision': PIN,
            'marker': 'V3_ISOLATED_ANISETTE_OTP_V1',
            'native_symbol': 'get_anisette_headers_isolated_uc',
            'compiled_literal': 'Isolated OTP staging failed',
            'swift_api': 'IsolatedAnisetteOTPProvider.getExistingHeaders', 'files': files}


def expected_evidence():
    """Trusted builder expectations for verifying copied evidence without Git."""
    fixtures = Path(__file__).resolve().parents[1] / 'tests/fixtures/pinned_anisettekit'
    return _evidence_for({path: (fixtures / path).read_bytes() for path in PATHS})


def evidence(root):
    patch(root, verify=True)
    return _evidence_for({path: original for path, (original, _) in expected_files(root).items()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('anisette_root', type=Path)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--evidence-output', type=Path)
    args = parser.parse_args()
    count = patch(args.anisette_root, args.verify)
    if args.evidence_output:
        args.evidence_output.write_text(json.dumps(evidence(args.anisette_root), indent=2, sort_keys=True) + '\n')
    print('ISOLATED_ANISETTE_OTP_PASS files=' + str(count))
