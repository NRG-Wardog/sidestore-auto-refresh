// Synthetic VM for executing the actual transformed native C boundary.
// This models API mechanics, never Apple's provisioning/cache behavior.
#pragma once
#include <map>
#include <vector>
#include <string>
#include <cstring>
#include <cstdint>
#include <stdexcept>
#include "anisette_base.h"
extern thread_local bool g_isolated_otp_logging_suppressed;
#define LOG_UC(...) do { if (!g_isolated_otp_logging_suppressed) anisetteCoreLog(__VA_ARGS__); } while (0)
constexpr int UC_ERR_OK = 0;
struct uc_engine { std::map<uint64_t, std::vector<uint8_t>> memory; };
inline int uc_mem_write(uc_engine *uc, uint64_t addr, const void *data, size_t len) {
    uc->memory[addr] = std::vector<uint8_t>((const uint8_t *)data, (const uint8_t *)data + len); return 0;
}
inline int uc_mem_read(uc_engine *uc, uint64_t addr, void *data, size_t len) {
    auto it = uc->memory.find(addr);
    if (it == uc->memory.end() || it->second.size() < len) return 1;
    memcpy(data, it->second.data(), len); return 0;
}
struct PageAllocator { uint64_t next = 0x1000; uint64_t alloc(size_t len) { auto p=next;next+=len+32;return p;} };
extern int constructed, destroyed, otp_calls, provision_calls;
extern std::string fault, expected_blob, expected_uuid, observed_id;
extern bool stage_observed, require_bounded;
struct EmulatorVM {
    bool read_only_filesystem = false;
    uc_engine *uc;
    PageAllocator heap;
    std::string provisioning_path;
    explicit EmulatorVM(bool checked = false) {
        if (checked && fault == "construct") throw std::runtime_error("synthetic");
        uc = new uc_engine; ++constructed;
    }
    ~EmulatorVM() { delete uc; ++destroyed; }
    uint64_t write_bytes(const uint8_t *bytes, size_t size) {
        auto a=heap.alloc(size); uc_mem_write(uc,a,bytes,size); return a;
    }
    uint64_t write_string(const char *s) { return write_bytes((const uint8_t*)s,strlen(s)+1); }
};
bool load_library_to_vm(EmulatorVM *,const std::string &,const std::string &);
void relocate_all_vm_libraries(EmulatorVM *);
void run_library_constructors(EmulatorVM *);
uint64_t get_vm_symbol_address(EmulatorVM *, const std::string &);
int32_t run_vm_procedure(EmulatorVM *,uint64_t,const std::vector<uint64_t> &,uint64_t = 0,size_t = 0);
