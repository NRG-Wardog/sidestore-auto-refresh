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
static bool normal_mode=false;
static bool fired=false;
static int close_count=0;
static std::string root;
static std::atomic<int> in_vm{0}, peak_vm{0};
static std::atomic<int> mixed_phase{0};
static std::vector<uint64_t> normal_procedures;
static std::vector<std::string> checked_created_names;
static unsigned checked_open_attempts=0;
static int normal_uuid_formats=0;
static bool hit(const char *name) {
    if (!fired && fault==name) {
        fired=true;errno=fault=="open"?EACCES:(fault=="write"?ENOSPC:(fault=="close"?EBADF:EIO));return true;
    }
    return false;
}
static std::string injected_format_uuid_string(const uint8_t *identifier) {
    if(normal_mode && ++normal_uuid_formats==2 && fault=="uuidalloc") throw std::bad_alloc();
    return ::format_uuid_string(identifier);
}
static int injected_mkdir(const char *p,mode_t m) { if(hit("mkdir"))return -1;return ::mkdir(p,m); }
static int injected_open(const char *p,int f,mode_t m=0) { if(hit(normal_mode?"rootopen":"open"))return -1;return ::open(p,f,m); }
static int injected_mkdirat(int fd,const char *p,mode_t m) { if(hit("mkdir"))return -1;return ::mkdirat(fd,p,m); }
static int injected_openat(int fd,const char *p,int f,mode_t m=0) {
    if(normal_mode && (f&O_CREAT))++checked_open_attempts;
    const char *stage=(f&O_DIRECTORY)?"uuidopen":((f&O_CREAT)?"open":"readopen");
    if(hit(stage))return -1;int result=::openat(fd,p,f,m);
    if(normal_mode && result>=0 && (f&O_CREAT))checked_created_names.push_back(p);
    return result;
}
static int injected_renameat(int a,const char *p,int b,const char *q) { if(hit("rename"))return -1;return ::renameat(a,p,b,q); }
static FILE *injected_fdopen(int fd,const char *m) { if(hit(strcmp(m,"wb")==0?"fdopen":"readfdopen"))return nullptr;return ::fdopen(fd,m); }
static size_t injected_fwrite(const void *b,size_t s,size_t n,FILE *f) { if(hit("write"))return 0;return ::fwrite(b,s,n,f); }
static int injected_fflush(FILE *f) { if(hit("flush"))return -1;return ::fflush(f); }
static int injected_fclose(FILE *f) {
    ++close_count;const bool bad=hit("close") || (close_count==2 && hit("readclose"));
    const int failed_errno=errno;int r=::fclose(f);
    if(normal_mode && close_count==2 && hit("rootreplace")) {
        assert(::rename(root.c_str(),(root+"-detached").c_str())==0);
        assert(::mkdir(root.c_str(),0700)==0);
        assert(::mkdir((root+"/"+expected_uuid).c_str(),0755)==0);
        std::ofstream replacement(root+"/"+expected_uuid+"/adi.pb");replacement<<"FOREIGN-REPLACEMENT-BLOB";
    }
    if(bad) { errno=failed_errno;return -1; }
    if(normal_mode && fault=="write")errno=EBUSY; // Successful cleanup must not replace ENOSPC.
    return r;
}
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
#define mkdirat injected_mkdirat
#define open injected_open
#define openat injected_openat
#define renameat injected_renameat
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
#define format_uuid_string injected_format_uuid_string
#include "anisette_core_uc.cpp"
#undef mkdir
#undef mkdirat
#undef open
#undef openat
#undef renameat
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
#undef format_uuid_string

static std::string contents(const std::string &path) {
    std::ifstream file(path,std::ios::binary);return std::string(std::istreambuf_iterator<char>(file),{});
}
static int open_descriptor_count() {
    int count=0;for(int fd=0;fd<1024;++fd)if(fcntl(fd,F_GETFD)!=-1)++count;return count;
}
bool load_library_to_vm(EmulatorVM *vm,const std::string &,const std::string &) {
    if(normal_mode) { assert(!vm->read_only_filesystem);return fault!="load"; }
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
    if(normal_mode)normal_procedures.push_back(proc);
    if(require_bounded) assert(timeout==5000000 && count==50000000);
    if(proc==2)vm->provisioning_path=std::string((char*)vm->uc->memory[args[0]].data());
    if(proc==3)observed_id=std::string((char*)vm->uc->memory[args[0]].data());
    if(proc>4){++provision_calls;return -45063;}
    if(fault=="setup"&&proc==2)return -45054;
    if(proc!=4)return 0;
    ++otp_calls;
    if(normal_mode && contents(vm->provisioning_path+"/adi.pb")!=expected_blob) return -45061;
    assert(contents(vm->provisioning_path+"/adi.pb")==expected_blob);
    int concurrent=++in_vm;int old=peak_vm.load();while(old<concurrent&&!peak_vm.compare_exchange_weak(old,concurrent)){}
    std::this_thread::yield();--in_vm;
    if(fault=="mixed") {
        mixed_phase=1;
        while(mixed_phase.load()!=2)std::this_thread::yield();
    }
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

static std::string native_trace(const char *json) {
    const char *prefix="\"v3_native_trace\":\"";
    const char *start=strstr(json,prefix);
#if V3_TEMPORARY_ANISETTE_TRACE_ENABLED
    assert(start);start+=strlen(prefix);const char *end=strchr(start,'"');assert(end);
    std::string value(start,end);
    assert(value.size()<=1024 && std::count(value.begin(),value.end(),',')<32);
    for(const auto &private_value:{expected_blob,expected_uuid,root,std::string("0001020304050607"),
            std::string("AQID"),std::string("BAUG"),std::string("X-Apple-I-MD"),
            std::string("PASSWORD-CANARY"),std::string("TOKEN-CANARY")})
        if(!private_value.empty())assert(value.find(private_value)==std::string::npos);
    return value;
#else
    assert(!start);return "disabled";
#endif
}

int main(int argc,char **argv) {
    assert(argc==2);fault=argv[1];
    if(fault.rfind("normal_",0)==0) { normal_mode=true;require_bounded=false;fault=fault.substr(7); }
    anisetteCoreSetLogging(1);
    char temporary[]="/tmp/isolated-adi-test-XXXXXX";assert(mkdtemp(temporary));root=temporary;
    uint8_t uuid[16];for(int i=0;i<16;++i)uuid[i]=i;expected_uuid=format_uuid_string(uuid);
    // A warmed normal provider must survive every probe result byte-for-byte.
    auto normal=new EmulatorVM;g_shared_vm=normal;g_libraries_initialized=true;
    g_current_prov_path="unchanged-normal-path";g_current_android_id="UNCHANGEDNORMAL";
    if(normal_mode) {
        anisetteCoreSetLogging(0);
        if(fault=="invalid") {
            char *untouched=reinterpret_cast<char *>(1);
            assert(get_anisette_headers_uc(root.c_str(),root.c_str(),nullptr,
                (const uint8_t*)expected_blob.data(),static_cast<uint32_t>(expected_blob.size()),&untouched)==-1);
            assert(untouched==reinterpret_cast<char *>(1));
            delete normal;g_shared_vm=nullptr;assert(rmdir(root.c_str())==0);
            puts("NORMAL_INVALID_ARGUMENT_PASS");return 0;
        }
        const bool cold=fault=="cold", fresh=fault=="fresh";
        const bool concurrent=fault=="concurrent", retry=fault=="retrywrite";
        const std::string scenario=fault;
        if(cold) {
            delete normal;g_shared_vm=nullptr;g_libraries_initialized=false;fault="ok";
        }
        if(fresh) { fault="ok";expected_blob="SYNTHETIC-FRESH-PROVISION-BLOB"; }
        if(fault=="zero") { fault="ok";expected_blob.clear(); }
        if(fault=="large") { fault="ok";expected_blob=std::string(1048577,'B'); }
        if(retry) fault="write";
        const std::string directory=root+"/"+expected_uuid;
        const std::string destination=directory+"/adi.pb";
        const std::string temporary_file=directory+"/.adi.pb.checked-"+std::to_string(getpid())+"-0";
        const std::string historical_temporary=directory+"/.adi.pb.checked-staging";
        const std::string previous="PRESERVE-PREVIOUS-BLOB", foreign="DO-NOT-CHANGE-TEMPORARY";
        const std::string outside=root+"-outside";
        assert(::mkdir(outside.c_str(),0700)==0);
        { std::ofstream f(outside+"/adi.pb");f<<previous; }
        if(!fresh) {
            assert(::mkdir(directory.c_str(),0755)==0);
            std::ofstream f(destination);f<<previous;
        }
        std::string provisioning_root=root;
        if(scenario=="rootlink") {
            assert(symlink(outside.c_str(),(root+"/root-link").c_str())==0);
            provisioning_root=root+"/root-link";
        }
        if(scenario=="uuidlink" || scenario=="uuidfile") {
            assert(unlink(destination.c_str())==0);assert(rmdir(directory.c_str())==0);
            if(scenario=="uuidlink")assert(symlink(outside.c_str(),directory.c_str())==0);
            else { std::ofstream f(directory);f<<previous; }
        }
        if(scenario=="filelink" || scenario=="hardlink" || scenario=="fifo") {
            assert(unlink(destination.c_str())==0);
            if(scenario=="filelink")assert(symlink((outside+"/adi.pb").c_str(),destination.c_str())==0);
            else if(scenario=="hardlink")assert(link((outside+"/adi.pb").c_str(),destination.c_str())==0);
            else assert(mkfifo(destination.c_str(),0600)==0);
        }
        if(scenario=="temp_exists" || scenario=="crash_leftover") { std::ofstream f(temporary_file);f<<foreign; }
        if(scenario=="temp_full") {
            for(int i=0;i<16;++i) { std::ofstream f(directory+"/.adi.pb.checked-"+std::to_string(getpid())+"-"+std::to_string(i));f<<foreign; }
        }
        if(scenario=="crash_leftover") { std::ofstream f(historical_temporary);f<<"PARTIAL-CRASH-LEFTOVER"; }
        if(scenario=="temp_link")assert(symlink((outside+"/adi.pb").c_str(),temporary_file.c_str())==0);
        if(scenario=="rootpermissions")assert(chmod(root.c_str(),0777)==0);
        if(scenario=="uuidpermissions")assert(chmod(directory.c_str(),0777)==0);
        if(scenario=="uuidalloc") {
            const int descriptors=open_descriptor_count();char *json=nullptr;bool threw=false;
            try {
                get_anisette_headers_uc(root.c_str(),root.c_str(),uuid,(const uint8_t*)expected_blob.data(),
                    static_cast<uint32_t>(expected_blob.size()),&json);
            } catch(const std::bad_alloc &) { threw=true; }
            assert(threw && normal_uuid_formats==2 && open_descriptor_count()==descriptors);
            assert(otp_calls==0 && g_shared_vm==normal && contents(destination)==previous);
            assert(access(temporary_file.c_str(),F_OK)!=0 && !json);
            assert(unlink(destination.c_str())==0 && rmdir(directory.c_str())==0);
            assert(unlink((outside+"/adi.pb").c_str())==0 && rmdir(outside.c_str())==0);
            delete normal;g_shared_vm=nullptr;assert(rmdir(root.c_str())==0);
            puts("NORMAL_ALLOCATION_FD_PASS");return 0;
        }
        auto invoke_normal=[&]() {
            char *json=nullptr;
            int result=get_anisette_headers_uc(root.c_str(),provisioning_root.c_str(),uuid,
                (const uint8_t*)expected_blob.data(),static_cast<uint32_t>(expected_blob.size()),&json);
            assert(json);printf("NATIVE_TRACE=%s\n",native_trace(json).c_str());
            if(result==-6) {
                assert(strstr(json,"Checked OTP staging failed"));
                int expected_errno=0;
                if(fault=="open")expected_errno=EACCES;
                if(fault=="write")expected_errno=ENOSPC;
                if(fault=="flush" || fault=="readclose")expected_errno=EIO;
                if(fault=="close")expected_errno=EBADF;
                if(fault=="temp_full")expected_errno=EEXIST;
                if(expected_errno) {
                    const std::string exact="\"error\":\"Checked OTP staging failed (errno "+std::to_string(expected_errno)+")\"";
                    assert(strstr(json,exact.c_str()));
                }
                if(fault=="mismatch" || fault=="extra" || fault=="rootpermissions" || fault=="uuidpermissions" ||
                    fault=="filelink" || fault=="hardlink" || fault=="fifo" || fault=="rootreplace")
                    assert(strstr(json,"\"error\":\"Checked OTP staging failed\""));
            }
            free_c_string(json);return result;
        };
        int result;
        if(concurrent) {
            int second=-99;
            std::thread a([&]{result=invoke_normal();}),b([&]{second=invoke_normal();});
            a.join();b.join();assert(result==0 && second==0 && otp_calls==2);
        } else result=invoke_normal();
        const bool foreign_temporary=scenario=="temp_exists" || scenario=="temp_link" || scenario=="crash_leftover";
        const bool promoted=fault=="ok" || fault=="otp" || fault=="symbol" || concurrent || foreign_temporary;
        if(promoted) {
            if(fault=="ok" || concurrent || foreign_temporary) assert(result==0);
            else assert(result!=0);
            assert(contents(destination)==expected_blob);
            if(!concurrent)assert(otp_calls==(fault=="symbol"?0:1));
        } else {
            assert(result==-6 && otp_calls==0);
            if(scenario!="uuidfile" && scenario!="fifo" && scenario!="rootreplace")assert(contents(destination)==previous);
            if(scenario=="rootreplace") {
                assert(contents(destination)=="FOREIGN-REPLACEMENT-BLOB");
                assert(contents(root+"-detached/"+expected_uuid+"/adi.pb")==previous);
                for(const auto &name:checked_created_names)
                    assert(access((root+"-detached/"+expected_uuid+"/"+name).c_str(),F_OK)!=0);
            }
            if(scenario=="uuidfile")assert(contents(directory)==previous);
            assert(std::find(normal_procedures.begin(),normal_procedures.end(),4)==normal_procedures.end());
        }
        if(scenario=="temp_exists" || scenario=="crash_leftover" || scenario=="temp_full")assert(contents(temporary_file)==foreign);
        else if(scenario=="temp_link") { struct stat st;assert(lstat(temporary_file.c_str(),&st)==0 && S_ISLNK(st.st_mode)); }
        else assert(access(temporary_file.c_str(),F_OK)!=0);
        for(const auto &name:checked_created_names)assert(access((directory+"/"+name).c_str(),F_OK)!=0);
        if(scenario=="crash_leftover")assert(contents(historical_temporary)=="PARTIAL-CRASH-LEFTOVER");
        if(scenario=="open")assert(checked_open_attempts==1);
        if(scenario=="temp_full") {
            assert(checked_open_attempts==16 && checked_created_names.empty());
            for(int i=0;i<16;++i) {
                const auto name=directory+"/.adi.pb.checked-"+std::to_string(getpid())+"-"+std::to_string(i);
                assert(contents(name)==foreign);assert(unlink(name.c_str())==0);
            }
        }
        assert(contents(outside+"/adi.pb")==previous);
        assert(access((outside+"/"+expected_uuid).c_str(),F_OK)!=0);
        assert(provision_calls==0 && g_libraries_initialized && !g_shared_vm->read_only_filesystem);
        if(!cold)assert(g_shared_vm==normal && constructed==1 && destroyed==0);
        else assert(constructed==2 && destroyed==1);
        if(scenario=="mkdir" || scenario=="rootopen" || scenario=="rootlink" || scenario=="rootpermissions") {
            assert(g_current_prov_path=="unchanged-normal-path" && g_current_android_id=="UNCHANGEDNORMAL");
            assert(normal_procedures.empty());
        } else {
            assert(g_current_prov_path==directory && g_current_android_id=="0001020304050607");
            const size_t first=cold?1:0;
            if(cold)assert(normal_procedures[0]==1);
            assert(normal_procedures[first]==2 && normal_procedures[first+1]==3);
        }
        if(retry) {
            fault="ok";fired=false;close_count=0;
            assert(invoke_normal()==0 && otp_calls==1 && contents(destination)==expected_blob);
            assert(g_shared_vm==normal && constructed==1 && destroyed==0);
            assert(access(temporary_file.c_str(),F_OK)!=0);
        }
        if(scenario=="uuidlink" || scenario=="uuidfile")assert(unlink(directory.c_str())==0);
        else {
            unlink(temporary_file.c_str());unlink(historical_temporary.c_str());
            unlink(destination.c_str());assert(rmdir(directory.c_str())==0);
        }
        if(scenario=="rootlink")assert(unlink(provisioning_root.c_str())==0);
        if(scenario=="rootreplace") {
            assert(unlink((root+"-detached/"+expected_uuid+"/adi.pb").c_str())==0);
            assert(rmdir((root+"-detached/"+expected_uuid).c_str())==0);
            assert(rmdir((root+"-detached").c_str())==0);
        }
        assert(unlink((outside+"/adi.pb").c_str())==0);assert(rmdir(outside.c_str())==0);
        delete g_shared_vm;g_shared_vm=nullptr;assert(rmdir(root.c_str())==0);
        assert(constructed==destroyed);
        puts("NORMAL_NATIVE_TRACE_PASS");return 0;
    }
    if(fault=="tracecap") {
        char *json=strdup("{\"error\":\"synthetic\"}");
        {
            NativeOTPTrace trace(&json);
            for(int i=0;i<10000;++i)trace.add(NativeOTPStage::ArgumentsOK);
#if !V3_TEMPORARY_ANISETTE_TRACE_ENABLED
            assert(trace.count==0 && trace.length==0);
#endif
        }
        printf("NATIVE_TRACE=%s\n",native_trace(json).c_str());free_c_string(json);
        delete normal;g_shared_vm=nullptr;assert(rmdir(root.c_str())==0);
        puts("NATIVE_TRACE_CAP_PASS");return 0;
    }
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
        if(fault=="ok"||fault=="concurrent"||fault=="mixed")assert(result==0 && json && strstr(json,"AQID"));
        else if(fault=="otp")assert(result==-45061);
        else assert(result!=0);
        assert(json && !strstr(json,"SYNTHETIC-EXISTING-BLOB"));
        printf("NATIVE_TRACE=%s\n",native_trace(json).c_str());free_c_string(json);
    };
    if(fault=="concurrent") {
        std::thread a(invoke),b(invoke);a.join();b.join();assert(peak_vm==1);
    } else if(fault=="mixed") {
        auto invalid=[&] {
            while(mixed_phase.load()!=1)std::this_thread::yield();
            char *json=nullptr;
            int result=get_anisette_headers_isolated_uc(root.c_str(),root.c_str(),uuid,
                (const uint8_t*)expected_blob.data(),0,&json);
            assert(result==-1 && json);
            printf("NATIVE_TRACE=%s\n",native_trace(json).c_str());free_c_string(json);
            mixed_phase=2;
        };
        std::thread a(invoke),b(invalid);a.join();b.join();
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
