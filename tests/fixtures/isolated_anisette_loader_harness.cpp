#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <string>
#include <vector>
#include <map>
#include <array>
#include <stdexcept>
#include <memory>
#include <fstream>
#include <iterator>
#include "Loader/elf_loader_emulator.h"

static int fault_step=0, api_step=0, closed_engines=0;
static int execution_error=0;
static bool early_stop=false;
thread_local bool g_isolated_otp_logging_suppressed=false;
static bool fail_next_allocation=false, fail_map_insertion=false, fail_library_buffer=false;
static int last_opened_fd=-1, last_library_fd=-1;
void *operator new(std::size_t size) {
    if(fail_next_allocation){fail_next_allocation=false;throw std::bad_alloc();}
    if(void *p=std::malloc(size?size:1))return p;
    throw std::bad_alloc();
}
void operator delete(void *p) noexcept { std::free(p); }
void operator delete(void *p,std::size_t) noexcept { std::free(p); }
static int tracked_open(const char *path,int flags,mode_t mode) {
    int fd=::open(path,flags,mode);last_opened_fd=fd;
    if(fd>=0 && fail_map_insertion){fail_map_insertion=false;fail_next_allocation=true;}
    return fd;
}
static FILE *tracked_fopen(const char *path,const char *mode) {
    FILE *f=::fopen(path,mode);last_library_fd=f?fileno(f):-1;
    if(f && fail_library_buffer){fail_library_buffer=false;fail_next_allocation=true;}
    return f;
}
struct uc_engine {
    std::map<uint64_t,uint8_t> memory;
    std::map<int,std::array<uint8_t,16>> registers;
};
static bool fail_api() { return ++api_step==fault_step; }
uc_err uc_open(int,int,uc_engine **out) { if(fail_api())return 1;*out=new uc_engine;return 0; }
uc_err uc_close(uc_engine *uc) { ++closed_engines;delete uc;return 0; }
uc_err uc_mem_map(uc_engine *,uint64_t,size_t,int) { return fail_api()?1:0; }
uc_err uc_hook_add(uc_engine *,uc_hook*,int,void*,void*,uint64_t,uint64_t) { return fail_api()?1:0; }
uc_err uc_mem_write(uc_engine *uc,uint64_t p,const void *data,size_t n) {
    if(fail_api())return 1;for(size_t i=0;i<n;++i)uc->memory[p+i]=((const uint8_t*)data)[i];return 0;
}
uc_err uc_mem_read(uc_engine *uc,uint64_t p,void *data,size_t n) {
    for(size_t i=0;i<n;++i){auto v=uc->memory.find(p+i);if(v==uc->memory.end())return 1;((uint8_t*)data)[i]=v->second;}return 0;
}
static size_t reg_size(int r) { return r==UC_ARM64_REG_W0?4:(r>=UC_ARM64_REG_V0&&r<=UC_ARM64_REG_V31?16:8); }
uc_err uc_reg_write(uc_engine *uc,int r,const void *data) { memcpy(uc->registers[r].data(),data,reg_size(r));return 0; }
uc_err uc_reg_read(uc_engine *uc,int r,void *data) { memcpy(data,uc->registers[r].data(),reg_size(r));return 0; }
uc_err uc_emu_start(uc_engine *uc,uint64_t begin,uint64_t end,uint64_t timeout,size_t count) {
    assert(timeout==5000000 && count==50000000);
    uint64_t pc=early_stop?begin:end;uc_reg_write(uc,UC_ARM64_REG_PC,&pc);
    int32_t code=-45061;uc_reg_write(uc,UC_ARM64_REG_W0,&code);return execution_error;
}
const char *uc_strerror(uc_err) { return "synthetic"; }
static void hook_mem_invalid(){}
static void hook_intr(){}
static void import_callback_router(){}
static void trace_pc_hook(){}
static EmulatorVM *g_active_vm=nullptr;
#define open tracked_open
#define fopen tracked_fopen
#include "loader_functions.inc"
#undef open
#undef fopen

static void reg(EmulatorVM &vm,int which,uint64_t value) { uc_reg_write(vm.uc,which,&value); }
static int64_t result(EmulatorVM &vm) { int64_t value=0;uc_reg_read(vm.uc,UC_ARM64_REG_X0,&value);return value; }
static void denied(EmulatorVM &vm) {
    uint32_t code=0;uc_mem_read(vm.uc,vm.errno_addr,&code,4);assert(result(vm)==-1 && code==EPERM);
}
static std::string read_file(const std::string &path) {
    std::ifstream f(path,std::ios::binary);return std::string(std::istreambuf_iterator<char>(f),{});
}

int main() {
    // Actual checked constructor releases the engine on every Unicorn error.
    for(int step=1;step<=11;++step) {
        fault_step=step;api_step=0;int before=closed_engines;
        bool threw=false;try { EmulatorVM vm(true); } catch(const std::runtime_error&) { threw=true; }
        assert(threw);assert(closed_engines-before==(step==1?0:1));
    }
    fault_step=0;api_step=0;
    EmulatorVM normal(true),probe(true);probe.read_only_filesystem=true;
    g_active_vm=&normal;
    assert(run_vm_procedure(&probe,0x1234,{})==-45061 && g_active_vm==&normal);
    execution_error=5;
    assert(run_vm_procedure(&probe,0x1234,{})==-1005 && g_active_vm==&normal);
    execution_error=0;early_stop=true;
    assert(run_vm_procedure(&probe,0x1234,{})==-1000 && g_active_vm==&normal);
    early_stop=false;
    assert(run_vm_procedure(&probe,0,{})==-1 && g_active_vm==&normal);
    g_active_vm=nullptr;
    assert(run_vm_procedure(&probe,0x1234,{})==-45061 && g_active_vm==nullptr);

    char temporary[]="/tmp/isolated-loader-test-XXXXXX";assert(mkdtemp(temporary));
    std::string root=temporary,canonical=root+"/canonical",created=root+"/new";
    {std::ofstream f(canonical);f<<"DO-NOT-MODIFY";}
    assert(chmod(canonical.c_str(),0600)==0);
    {
        // Actual library loader: allocation after fopen must still close FILE*.
        EmulatorVM exception_vm(true);
        fail_library_buffer=true;bool caught=false;
        try { load_library_to_vm(&exception_vm,canonical,"synthetic"); }
        catch(const std::bad_alloc&){caught=true;}
        assert(caught && last_library_fd>=0 && fcntl(last_library_fd,F_GETFD)==-1 && errno==EBADF);
        // Actual open hook: failed fd_map ownership transfer must close host FD.
        auto pathname=exception_vm.write_string(canonical.c_str());
        reg(exception_vm,UC_ARM64_REG_X0,pathname);reg(exception_vm,UC_ARM64_REG_X1,0);reg(exception_vm,UC_ARM64_REG_X2,0);
        fail_map_insertion=true;caught=false;
        try { hook_open(&exception_vm); } catch(const std::bad_alloc&){caught=true;}
        assert(caught && last_opened_fd>=0 && fcntl(last_opened_fd,F_GETFD)==-1 && errno==EBADF);
        assert(exception_vm.fd_map.empty());
    }
    const auto path=probe.write_string(canonical.c_str());
    for(uint64_t flags:{1ULL,2ULL,0x40ULL,0x200ULL,0x400ULL,0x410000ULL}) {
        reg(probe,UC_ARM64_REG_X0,path);reg(probe,UC_ARM64_REG_X1,flags);reg(probe,UC_ARM64_REG_X2,0600);
        hook_open(&probe);denied(probe);
    }
    // Even a deliberately inserted writable descriptor cannot bypass policy.
    int writable=open(canonical.c_str(),O_RDWR);assert(writable>=0);probe.fd_map[90]=writable;
    const uint8_t value[]={'X','X','X'};auto data=probe.write_bytes(value,3);
    reg(probe,UC_ARM64_REG_X0,90);reg(probe,UC_ARM64_REG_X1,data);reg(probe,UC_ARM64_REG_X2,3);
    hook_write(&probe);denied(probe);
    reg(probe,UC_ARM64_REG_X0,90);reg(probe,UC_ARM64_REG_X1,0);hook_ftruncate(&probe);denied(probe);
    reg(probe,UC_ARM64_REG_X0,path);reg(probe,UC_ARM64_REG_X1,0777);hook_chmod(&probe);denied(probe);
    auto newpath=probe.write_string(created.c_str());
    reg(probe,UC_ARM64_REG_X0,newpath);reg(probe,UC_ARM64_REG_X1,0700);hook_mkdir(&probe);denied(probe);
    mode_t before=umask(0077);reg(probe,UC_ARM64_REG_X0,0);hook_umask(&probe);denied(probe);
    assert(umask(before)==0077);
    assert(read_file(canonical)=="DO-NOT-MODIFY" && access(created.c_str(),F_OK)!=0);
    struct stat st;assert(stat(canonical.c_str(),&st)==0 && (st.st_mode&0777)==0600);
    // Read-only opens continue to function.
    reg(probe,UC_ARM64_REG_X0,path);reg(probe,UC_ARM64_REG_X1,0);reg(probe,UC_ARM64_REG_X2,0);
    hook_open(&probe);assert(result(probe)>=10);hook_close(&probe);
    // Normal hooks retain their pre-existing writable behavior.
    auto normalpath=normal.write_string(created.c_str());
    reg(normal,UC_ARM64_REG_X0,normalpath);reg(normal,UC_ARM64_REG_X1,1|0x40);reg(normal,UC_ARM64_REG_X2,0600);
    hook_open(&normal);assert(result(normal)>=10);uint64_t fd=result(normal);
    auto normaldata=normal.write_bytes(value,3);
    reg(normal,UC_ARM64_REG_X0,fd);reg(normal,UC_ARM64_REG_X1,normaldata);reg(normal,UC_ARM64_REG_X2,3);
    hook_write(&normal);assert(result(normal)==3);
    reg(normal,UC_ARM64_REG_X0,fd);reg(normal,UC_ARM64_REG_X1,2);hook_ftruncate(&normal);assert(result(normal)==0);
    reg(normal,UC_ARM64_REG_X0,fd);hook_close(&normal);assert(result(normal)==0 && read_file(created)=="XX");
    // Actual destructor closes every still-owned host descriptor.
    auto disposable=new EmulatorVM(true);int held=open(canonical.c_str(),O_RDONLY);assert(held>=0);
    disposable->fd_map[10]=held;delete disposable;assert(fcntl(held,F_GETFD)==-1 && errno==EBADF);
    probe.fd_map.erase(90);close(writable);
    assert(unlink(canonical.c_str())==0 && unlink(created.c_str())==0 && rmdir(root.c_str())==0);
    puts("ISOLATED_LOADER_CONTAINMENT_PASS");
}
