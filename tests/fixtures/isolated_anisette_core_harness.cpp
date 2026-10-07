#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <string>
#include <vector>
#include <map>
#include <mutex>
#include <memory>
#include <algorithm>
#include <stdexcept>
#include <thread>
#include <atomic>
#include <fstream>
#include <iterator>
#include "Loader/elf_loader_emulator.h"

int constructed=0, destroyed=0, otp_calls=0, provision_calls=0;
std::string fault, expected_blob="SYNTHETIC-EXISTING-BLOB", expected_uuid, observed_id;
bool stage_observed=false, require_bounded=true;
static bool fired=false;
static int close_count=0;
static std::string root;
static std::atomic<int> in_vm{0}, peak_vm{0};
static bool hit(const char *name) { if (!fired && fault==name) { fired=true; errno=EIO; return true; } return false; }
static int injected_mkdir(const char *p,mode_t m) { if(hit("mkdir"))return -1;return ::mkdir(p,m); }
static int injected_open(const char *p,int f,mode_t m) { if(hit("open"))return -1;return ::open(p,f,m); }
static FILE *injected_fdopen(int fd,const char *m) { if(hit("fdopen"))return nullptr;return ::fdopen(fd,m); }
static size_t injected_fwrite(const void *b,size_t s,size_t n,FILE *f) { if(hit("write"))return 0;return ::fwrite(b,s,n,f); }
static int injected_fflush(FILE *f) { if(hit("flush"))return -1;return ::fflush(f); }
static int injected_fclose(FILE *f) { ++close_count;const bool bad=hit("close") || (close_count==2 && hit("readclose"));int r=::fclose(f);return bad ? -1:r; }
static FILE *injected_fopen(const char *p,const char *m) { if(hit("readopen"))return nullptr;return ::fopen(p,m); }
static size_t injected_fread(void *b,size_t s,size_t n,FILE *f) {
    if(hit("read"))return 0;auto r=::fread(b,s,n,f);
    if(hit("mismatch")&&r) ((char*)b)[0]^=1;return r;
}
static int injected_rename(const char *a,const char *b) { if(hit("rename"))return -1;return ::rename(a,b); }
static int injected_unlink(const char *p) { if(hit("cleanup"))return -1;return ::unlink(p); }
static int injected_fgetc(FILE *f) { if(hit("extra"))return 'X';return ::fgetc(f); }
static int injected_ferror(FILE *f) { if(hit("readerror"))return 1;return ::ferror(f); }
static char *injected_strdup(const char *p) { if(hit("alloc"))return nullptr;return ::strdup(p); }

#define mkdir injected_mkdir
#define open injected_open
#define fdopen injected_fdopen
#define fwrite injected_fwrite
#define fflush injected_fflush
#define fclose injected_fclose
#define fopen injected_fopen
#define fread injected_fread
#define rename injected_rename
#define unlink injected_unlink
#define fgetc injected_fgetc
#define ferror injected_ferror
#define strdup injected_strdup
#include "anisette_core_uc.cpp"
#undef mkdir
#undef open
#undef fdopen
#undef fwrite
#undef fflush
#undef fclose
#undef fopen
#undef fread
#undef rename
#undef unlink
#undef fgetc
#undef ferror
#undef strdup

static std::string contents(const std::string &path) {
    std::ifstream file(path,std::ios::binary);return std::string(std::istreambuf_iterator<char>(file),{});
}
bool load_library_to_vm(EmulatorVM *vm,const std::string &,const std::string &) {
    assert(vm->read_only_filesystem);
    stage_observed=contents(root+"/"+expected_uuid+"/adi.pb")==expected_blob;
    assert(stage_observed);return fault!="load";
}
void relocate_all_vm_libraries(EmulatorVM *) {}
void run_library_constructors(EmulatorVM *) {}
uint64_t get_vm_symbol_address(EmulatorVM *,const std::string &name) {
    const std::vector<std::string> symbols={"kq56gsgHG6","nf92ngaK92","Sph98paBcz","qi864985u0","rsegvyrt87","uv5t6nhkui"};
    for(size_t i=0;i<symbols.size();++i)if(name==symbols[i])return fault=="symbol"&&i==3?0:i+1;
    return 0;
}
int32_t run_vm_procedure(EmulatorVM *vm,uint64_t proc,const std::vector<uint64_t>&args,uint64_t timeout,size_t count) {
    if(require_bounded) assert(timeout==5000000 && count==50000000);
    if(proc==2)vm->provisioning_path=std::string((char*)vm->uc->memory[args[0]].data());
    if(proc==3)observed_id=std::string((char*)vm->uc->memory[args[0]].data());
    if(proc>4){++provision_calls;return -45063;}
    if(fault=="setup"&&proc==2)return -45054;
    if(proc!=4)return 0;
    ++otp_calls;
    assert(contents(vm->provisioning_path+"/adi.pb")==expected_blob);
    int concurrent=++in_vm;int old=peak_vm.load();while(old<concurrent&&!peak_vm.compare_exchange_weak(old,concurrent)){}
    std::this_thread::yield();--in_vm;
    if(fault=="otp")return -45061;
    if(fault=="throw")throw std::runtime_error("synthetic");
    const uint8_t mid[]={1,2,3},otp[]={4,5,6};
    uint64_t midaddr=vm->write_bytes(mid,3),otpaddr=vm->write_bytes(otp,3);
    uint32_t len=fault=="length"?5000:3;
    uc_mem_write(vm->uc,args[1],&midaddr,8);uc_mem_write(vm->uc,args[2],&len,4);
    uc_mem_write(vm->uc,args[3],&otpaddr,8);uc_mem_write(vm->uc,args[4],&len,4);
    if(fault=="outputread")vm->uc->memory.erase(args[1]);
    return 0;
}

int main(int argc,char **argv) {
    assert(argc==2);fault=argv[1];
    anisetteCoreSetLogging(1);
    char temporary[]="/tmp/isolated-adi-test-XXXXXX";assert(mkdtemp(temporary));root=temporary;
    uint8_t uuid[16];for(int i=0;i<16;++i)uuid[i]=i;expected_uuid=format_uuid_string(uuid);
    // A warmed normal provider must survive every probe result byte-for-byte.
    auto normal=new EmulatorVM;g_shared_vm=normal;g_libraries_initialized=true;
    g_current_prov_path="unchanged-normal-path";g_current_android_id="UNCHANGEDNORMAL";
    if(fault=="existing") {
        assert(mkdir((root+"/"+expected_uuid).c_str(),0700)==0);
        std::ofstream file(root+"/"+expected_uuid+"/adi.pb");file<<"DO-NOT-CHANGE";
    }
    if(fault=="rootpermissions")assert(chmod(root.c_str(),0755)==0);
    auto invoke=[&] {
        char *json=nullptr;
        const uint32_t len=fault=="empty"?0:static_cast<uint32_t>(expected_blob.size());
        int result=get_anisette_headers_isolated_uc(root.c_str(),root.c_str(),uuid,
            (const uint8_t*)expected_blob.data(),len,&json);
        if(fault=="ok"||fault=="concurrent")assert(result==0 && json && strstr(json,"AQID"));
        else if(fault=="otp")assert(result==-45061);
        else assert(result!=0);
        assert(json && !strstr(json,"SYNTHETIC-EXISTING-BLOB"));free_c_string(json);
    };
    if(fault=="concurrent") {
        std::thread a(invoke),b(invoke);a.join();b.join();assert(peak_vm==1);
    } else invoke();
    assert(g_shared_vm==normal && g_libraries_initialized);
    assert(g_current_prov_path=="unchanged-normal-path" && g_current_android_id=="UNCHANGEDNORMAL");
    assert(provision_calls==0 && constructed-destroyed==1);
    assert(!g_isolated_otp_logging_suppressed && anisetteCoreIsLoggingEnabled()==1);
    if(otp_calls)assert(observed_id=="0001020304050607");
    if(fault=="existing") {
        assert(contents(root+"/"+expected_uuid+"/adi.pb")=="DO-NOT-CHANGE");
        assert(unlink((root+"/"+expected_uuid+"/adi.pb").c_str())==0);
        assert(rmdir((root+"/"+expected_uuid).c_str())==0);
    }
    else assert(access((root+"/"+expected_uuid).c_str(),F_OK)!=0);
    delete normal;g_shared_vm=nullptr;assert(constructed==destroyed);
    assert(rmdir(root.c_str())==0);
    LOG_UC("NORMAL_LOG_RETAINED\n");
    puts("ISOLATED_NATIVE_OTP_PASS");
}
