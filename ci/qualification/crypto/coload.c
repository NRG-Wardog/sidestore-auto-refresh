#define _GNU_SOURCE
#include <dlfcn.h>
#include <openssl/provider.h>
#include <openssl/crypto.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define REQUIRE(x) do { if (!(x)) { fprintf(stderr,"FAIL co-load line %d: %s (%s)\n",__LINE__,#x,dlerror()); exit(1); } } while (0)
typedef const char* (*version_fn)(int);
typedef int (*init_fn)(uint64_t,const OPENSSL_INIT_SETTINGS*);
typedef OSSL_PROVIDER* (*load_fn)(OSSL_LIB_CTX*, const char*);
typedef const OSSL_DISPATCH* (*dispatch_fn)(const OSSL_PROVIDER*);
typedef int (*unload_fn)(OSSL_PROVIDER*);
int main(int argc,char**argv) {
    REQUIRE(argc==4);
    int global = strcmp(argv[1],"global")==0;
    REQUIRE(global || strcmp(argv[1],"local")==0);
    void* handles[2];
    for(int i=0;i<2;i++) { handles[i]=dlopen(argv[i+2],RTLD_NOW|(global?RTLD_GLOBAL:RTLD_LOCAL)); REQUIRE(handles[i]); }
    for(int i=0;i<2;i++) {
        version_fn version=(version_fn)dlsym(handles[i],"OpenSSL_version"); REQUIRE(version);
        Dl_info image; REQUIRE(dladdr((void*)version,&image));
        printf("HANDLE\t%d\t%s\nVERSION\t%d\t%s\n",i,image.dli_fname,i,version(OPENSSL_VERSION));
        const char* symbols[]={"PKCS12_parse","EVP_DigestSignInit","OSSL_PROVIDER_load"};
        for(int j=0;j<3;j++) {
            void* symbol=dlsym(handles[i],symbols[j]); REQUIRE(symbol && dladdr(symbol,&image));
            printf("SYMBOL\t%d\t%s\t%s\n",i,symbols[j],image.dli_fname);
        }
        init_fn init=(init_fn)dlsym(handles[i],"OPENSSL_init_crypto"); REQUIRE(init && init(OPENSSL_INIT_NO_LOAD_CONFIG,NULL));
        load_fn load=(load_fn)dlsym(handles[i],"OSSL_PROVIDER_load");
        dispatch_fn dispatch=(dispatch_fn)dlsym(handles[i],"OSSL_PROVIDER_get0_dispatch");
        unload_fn unload=(unload_fn)dlsym(handles[i],"OSSL_PROVIDER_unload"); REQUIRE(load && dispatch && unload);
        const char* names[]={"default","legacy"};
        for(int j=0;j<2;j++) {
            OSSL_PROVIDER* provider=load(NULL,names[j]); REQUIRE(provider);
            const OSSL_DISPATCH* entries=dispatch(provider); REQUIRE(entries && entries[0].function);
            REQUIRE(dladdr((void*)entries[0].function,&image));
            printf("PROVIDER\t%d\t%s\t%s\n",i,names[j],image.dli_fname);
            REQUIRE(unload(provider));
        }
    }
    version_fn global_version=(version_fn)dlsym(RTLD_DEFAULT,"OpenSSL_version");
    if(global_version) {
        Dl_info image; REQUIRE(dladdr((void*)global_version,&image));
        printf("GLOBAL\t%s\t%s\n",image.dli_fname,global_version(OPENSSL_VERSION));
    } else puts("GLOBAL\tNONE");
    puts("PASS cold-process-two-library-probe");
    return 0;
}
