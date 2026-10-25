#include "common.h"
#include "mach-o.h"
#include "signing.h"
#include <openssl/provider.h>
#include <openssl/pem.h>
#include <openssl/cms.h>
#include <openssl/err.h>
#include <dlfcn.h>

#define REQUIRE(x) do { if (!(x)) { fprintf(stderr, "FAIL synthetic ZSign line %d: %s\n", __LINE__, #x); ERR_print_errors_fp(stderr); exit(1); } } while (0)
static void save(const char* name, const string& value) {
    FILE* out = fopen(name, "wb"); REQUIRE(out);
    REQUIRE(fwrite(value.data(), 1, value.size(), out) == value.size()); REQUIRE(fclose(out) == 0);
}
int main() {
    REQUIRE(OPENSSL_init_crypto(OPENSSL_INIT_NO_LOAD_CONFIG, nullptr) == 1);
    OSSL_PROVIDER* base = OSSL_PROVIDER_load(nullptr, "default"); REQUIRE(base);
    OSSL_PROVIDER* legacy = OSSL_PROVIDER_load(nullptr, "legacy"); REQUIRE(legacy);
    Dl_info image; REQUIRE(dladdr((void*)&OpenSSL_version, &image));
    printf("IMAGE %s\nVERSION %s\n", image.dli_fname, OpenSSL_version(OPENSSL_VERSION));
    for (auto entry : {std::make_pair("default", base), std::make_pair("legacy", legacy)}) {
        const OSSL_DISPATCH* dispatch = OSSL_PROVIDER_get0_dispatch(entry.second);
        REQUIRE(dispatch && dispatch[0].function && dladdr((void*)dispatch[0].function, &image));
        printf("PROVIDER\t%s\t%s\n", entry.first, image.dli_fname);
    }
    EVP_PKEY_CTX* context = EVP_PKEY_CTX_new_id(EVP_PKEY_RSA, nullptr); REQUIRE(context);
    REQUIRE(EVP_PKEY_keygen_init(context) == 1 && EVP_PKEY_CTX_set_rsa_keygen_bits(context, 2048) == 1);
    EVP_PKEY* key = nullptr; REQUIRE(EVP_PKEY_keygen(context, &key) == 1); EVP_PKEY_CTX_free(context);
    X509* cert = X509_new(); REQUIRE(cert);
    REQUIRE(X509_set_version(cert, 2) == 1 && ASN1_INTEGER_set(X509_get_serialNumber(cert), 123) == 1);
    REQUIRE(X509_gmtime_adj(X509_getm_notBefore(cert), 0) && X509_gmtime_adj(X509_getm_notAfter(cert), 3600));
    REQUIRE(X509_set_pubkey(cert, key) == 1);
    REQUIRE(X509_NAME_add_entry_by_txt(X509_get_subject_name(cert), "CN", MBSTRING_ASC,
        (const unsigned char*)"UNTRUSTED synthetic ZSign fixture", -1, -1, 0) == 1);
    // Match only the PUBLIC issuer name required by the real production branch.
    // The ephemeral key does NOT chain to Apple. No CA private key/trust modification.
    BIO* caData = BIO_new_mem_buf(ZSignAsset::s_szAppleDevCACert, -1); REQUIRE(caData);
    X509* ca = PEM_read_bio_X509(caData, nullptr, nullptr, nullptr); REQUIRE(ca);
    REQUIRE(X509_set_issuer_name(cert, X509_get_subject_name(ca)) == 1);
    REQUIRE(X509_sign(cert, key, EVP_sha256()) > 0);
    X509_free(ca); BIO_free(caData);
    ZSignAsset asset; asset.m_evpPKey = key; asset.m_x509Cert = cert;
    string code(8193, '\0');
    for (size_t i=0; i<code.size(); ++i) code[i] = (char)((i*31+7)&255);
    string sha1, sha256, cms;
    REQUIRE(ZSign::SlotBuildCodeDirectory(false, (uint8_t*)code.data(), (uint32_t)code.size(),
        nullptr, 0, code.size(), 1, "invalid.synthetic.fixture", "SYNTHETIC", "", "", "", "", "", true, false, sha1));
    REQUIRE(ZSign::SlotBuildCodeDirectory(true, (uint8_t*)code.data(), (uint32_t)code.size(),
        nullptr, 0, code.size(), 1, "invalid.synthetic.fixture", "SYNTHETIC", "", "", "", "", "", true, false, sha256));
    REQUIRE(ZSign::SlotBuildCMSSignature(&asset, sha1, sha256, cms));
    REQUIRE(cms.size() > 8);
    save("code.bin", code); save("cd-sha1.bin", sha1); save("cd-sha256.bin", sha256);
    save("cms-slot.bin", cms); save("cms.der", cms.substr(8));
    unsigned char* certDER = nullptr; int size = i2d_X509(cert, &certDER); REQUIRE(size > 0);
    save("synthetic-cert.der", string((char*)certDER, size)); OPENSSL_free(certDER);
    puts("PASS actual-ZSign-code-directories-and-CMS-produced-untrusted-chain");
    X509_free(cert); EVP_PKEY_free(key); OSSL_PROVIDER_unload(legacy); OSSL_PROVIDER_unload(base);
    return 0;
}
