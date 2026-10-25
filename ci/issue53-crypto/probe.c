#define _GNU_SOURCE
#include <openssl/crypto.h>
#include <openssl/provider.h>
#include <openssl/evp.h>
#include <openssl/pkcs12.h>
#include <openssl/pem.h>
#include <openssl/x509.h>
#include <openssl/err.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Synthetic, process-local credentials only. Never reads a user's key/store. */
#define REQUIRE(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); ERR_print_errors_fp(stderr); exit(1); } } while (0)
static void save(const char *name, const unsigned char *data, size_t size) {
    FILE *f = fopen(name, "wb"); REQUIRE(f); REQUIRE(fwrite(data, 1, size, f) == size); REQUIRE(fclose(f) == 0);
}
static void provider_image(OSSL_PROVIDER *provider, const char *name) {
    const OSSL_DISPATCH *dispatch = OSSL_PROVIDER_get0_dispatch(provider);
    Dl_info image;
    REQUIRE(dispatch && dispatch[0].function && dladdr((void *)dispatch[0].function, &image) && image.dli_fname);
    printf("PROVIDER_IMAGE %s %s\n", name, image.dli_fname);
}
static void roundtrip(EVP_PKEY *key, X509 *cert, int legacy) {
    const char *password = "synthetic-fixture-only";
    PKCS12 *p12 = PKCS12_create(password, "synthetic", key, cert, NULL,
        legacy ? NID_pbe_WithSHA1And3_Key_TripleDES_CBC : 0,
        legacy ? NID_pbe_WithSHA1And40BitRC2_CBC : 0, 2048, 2048, 0);
    REQUIRE(p12);
    unsigned char *der = NULL;
    int size = i2d_PKCS12(p12, &der); REQUIRE(size > 0);
    PKCS12_free(p12);
    const unsigned char *cursor = der;
    p12 = d2i_PKCS12(NULL, &cursor, size); REQUIRE(p12 && cursor == der + size);
    EVP_PKEY *decodedKey = NULL; X509 *decodedCert = NULL;
    REQUIRE(PKCS12_parse(p12, password, &decodedKey, &decodedCert, NULL) == 1);
    REQUIRE(EVP_PKEY_eq(key, decodedKey) == 1 && X509_cmp(cert, decodedCert) == 0);
    EVP_PKEY_free(decodedKey); X509_free(decodedCert);
    decodedKey = NULL; decodedCert = NULL;
    REQUIRE(PKCS12_parse(p12, "incorrect", &decodedKey, &decodedCert, NULL) == 0);
    EVP_PKEY_free(decodedKey); X509_free(decodedCert); ERR_clear_error();
    REQUIRE(PKCS12_verify_mac(p12, password, -1) == 1);
    const ASN1_OCTET_STRING *mac = NULL;
    PKCS12_get0_mac(&mac, NULL, NULL, NULL, p12); REQUIRE(mac && ASN1_STRING_length(mac) > 0);
    /* Mutate only this owned synthetic object's MAC, leaving DER parseability intact. */
    unsigned char *changed = (unsigned char *)ASN1_STRING_get0_data(mac);
    changed[0] ^= 1;
    REQUIRE(PKCS12_verify_mac(p12, password, -1) == 0); ERR_clear_error();
    PKCS12_free(p12); OPENSSL_free(der);
    puts(legacy ? "PASS legacy-pkcs12-roundtrip-wrong-password-tampered-mac" : "PASS modern-pkcs12-roundtrip-wrong-password-tampered-mac");
}
int main(void) {
    REQUIRE(OPENSSL_init_crypto(OPENSSL_INIT_NO_LOAD_CONFIG, NULL) == 1);
    printf("HEADER %s\n", OPENSSL_VERSION_TEXT);
    Dl_info info; REQUIRE(dladdr((void *)&OpenSSL_version, &info) && info.dli_fname);
    printf("IMAGE %s\nVERSION %s\n", info.dli_fname, OpenSSL_version(OPENSSL_VERSION));
    OSSL_PROVIDER *base = OSSL_PROVIDER_load(NULL, "default"); REQUIRE(base);
    OSSL_PROVIDER *legacy = OSSL_PROVIDER_load(NULL, "legacy"); REQUIRE(legacy);
    REQUIRE(OSSL_PROVIDER_available(NULL, "default") && OSSL_PROVIDER_available(NULL, "legacy"));
    provider_image(base, "default"); provider_image(legacy, "legacy");
    EVP_CIPHER *rc2 = EVP_CIPHER_fetch(NULL, "RC2-40-CBC", "provider=legacy"); REQUIRE(rc2); EVP_CIPHER_free(rc2);
    puts("PASS explicit-default-and-legacy-provider-activation");
    EVP_PKEY_CTX *keyContext = EVP_PKEY_CTX_new_id(EVP_PKEY_RSA, NULL); REQUIRE(keyContext);
    REQUIRE(EVP_PKEY_keygen_init(keyContext) == 1 && EVP_PKEY_CTX_set_rsa_keygen_bits(keyContext, 2048) == 1);
    EVP_PKEY *key = NULL; REQUIRE(EVP_PKEY_keygen(keyContext, &key) == 1); EVP_PKEY_CTX_free(keyContext);
    X509 *cert = X509_new(); REQUIRE(cert);
    REQUIRE(X509_set_version(cert, 2) == 1 && ASN1_INTEGER_set(X509_get_serialNumber(cert), 1) == 1);
    REQUIRE(X509_gmtime_adj(X509_getm_notBefore(cert), 0) && X509_gmtime_adj(X509_getm_notAfter(cert), 3600));
    REQUIRE(X509_set_pubkey(cert, key) == 1);
    X509_NAME *name = X509_get_subject_name(cert);
    REQUIRE(X509_NAME_add_entry_by_txt(name, "CN", MBSTRING_ASC, (const unsigned char *)"Synthetic qualification only", -1, -1, 0) == 1);
    REQUIRE(X509_set_issuer_name(cert, name) == 1 && X509_sign(cert, key, EVP_sha256()) > 0);
    roundtrip(key, cert, 0); roundtrip(key, cert, 1);
    const unsigned char message[] = "issue53 synthetic signature fixture";
    EVP_MD_CTX *ctx = EVP_MD_CTX_new(); REQUIRE(ctx);
    REQUIRE(EVP_DigestSignInit(ctx, NULL, EVP_sha256(), NULL, key) == 1);
    size_t length = 0; REQUIRE(EVP_DigestSign(ctx, NULL, &length, message, sizeof(message)-1) == 1);
    unsigned char *signature = OPENSSL_malloc(length); REQUIRE(signature);
    REQUIRE(EVP_DigestSign(ctx, signature, &length, message, sizeof(message)-1) == 1);
    save("message.bin", message, sizeof(message)-1); save("signature.bin", signature, length);
    FILE *publicKey = fopen("public.pem", "wb"); REQUIRE(publicKey);
    REQUIRE(PEM_write_PUBKEY(publicKey, key) == 1 && fclose(publicKey) == 0);
    unsigned char *publicDER = NULL;
    int publicLength = i2d_PublicKey(key, &publicDER); REQUIRE(publicLength > 0);
    save("public.der", publicDER, publicLength); OPENSSL_free(publicDER);
    OPENSSL_free(signature); EVP_MD_CTX_free(ctx); X509_free(cert); EVP_PKEY_free(key);
    OSSL_PROVIDER_unload(legacy); OSSL_PROVIDER_unload(base);
    puts("PASS synthetic-signature-produced");
    return 0;
}
