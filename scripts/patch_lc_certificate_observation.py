"""Observe public certificate facts through LC's existing ZSign parser.

No certificate import, password handling policy, persistence or signing engine is
reimplemented. InitSimple owns parsing; X509_digest observes its leaf certificate.
"""
from pathlib import Path
import subprocess
import sys

PIN = "12377cf3b91d51739a33f14a302e5f522b238593"
PATHS = ("ZSign/zsigner.h", "ZSign/zsign.mm",
         "LiveContainerSwiftUI/Utilities/LCUtils.h",
         "LiveContainerSwiftUI/Utilities/LCUtils.m")
SIGNER_DECL = "+ (NSDictionary<NSString *, NSString *> * _Nullable)certificateFactsWithCert:(NSData *)cert pass:(NSString *)pass;"
UTIL_DECL = "+ (NSDictionary<NSString *, NSString *> * _Nullable)certificateFactsWithKeyData:(NSData *)keyData password:(NSString *)password NS_SWIFT_NAME(certificateFacts(withKeyData:password:));"
SIGNER_METHOD = '''// V3_CANONICAL_CERTIFICATE_FACTS_V1: observation only; use LC's native parser.
+ (NSDictionary<NSString *, NSString *> *)certificateFactsWithCert:(NSData *)cert pass:(NSString *)pass {
    if (![cert isKindOfClass:[NSData class]] || ![pass isKindOfClass:[NSString class]] || cert.length == 0 || cert.length > 2147483647U) return nil;
    ZSignAsset asset;
    struct Cleanup {
        ZSignAsset &asset;
        ~Cleanup() {
            X509_free((X509 *)asset.m_x509Cert);
            EVP_PKEY_free((EVP_PKEY *)asset.m_evpPKey);
            asset.m_x509Cert = nullptr;
            asset.m_evpPKey = nullptr;
        }
    } cleanup{asset};
    const char *passwordBytes = pass.UTF8String;
    if (!passwordBytes || !asset.InitSimple(cert.bytes, (int)cert.length, nil, 0, string(passwordBytes)) ||
        !asset.m_x509Cert || asset.m_strTeamId.empty()) return nil;
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    if (X509_digest((X509 *)asset.m_x509Cert, EVP_sha256(), digest, &length) != 1 || length != 32) return nil;
    NSMutableString *fingerprint = [NSMutableString stringWithCapacity:64];
    for (unsigned int i = 0; i < length; ++i) [fingerprint appendFormat:@"%02x", digest[i]];
    NSString *team = [NSString stringWithUTF8String:asset.m_strTeamId.c_str()];
    if (!team) return nil;
    return @{@"teamIdentifier": team, @"identitySHA256": fingerprint};
}

'''
UTIL_METHOD = '''// V3_CANONICAL_CERTIFICATE_FACTS_V1: no secret material leaves the native parser.
+ (NSDictionary<NSString *, NSString *> *)certificateFactsWithKeyData:(NSData *)keyData password:(NSString *)password {
    NSError *error = nil;
    [self loadStoreFrameworksWithError2:&error];
    Class signer = NSClassFromString(@"ZSigner");
    if (error || !signer || ![signer respondsToSelector:@selector(certificateFactsWithCert:pass:)]) return nil;
    return [signer certificateFactsWithCert:keyData pass:password];
}

'''


VALIDATION_ORIGINAL = ''' + (int)validateCertificateWithCompletionHandler:(void(^)(int status, NSDate *expirationDate, NSString *organizationalUnitName, NSString *error))completionHandler {'''.lstrip()
VALIDATION_PINNED = '+ (int)validateCertificateWithCompletionHandler:(void(^)(int status, NSDate *expirationDate, NSString *organizationalUnitName, NSString *error))completionHandler {\n    NSError *error;\n    NSData *certData = [LCUtils certificateData];\n    if (error) {\n        return -6;\n    }\n    [self loadStoreFrameworksWithError2:&error];\n    int ans = [NSClassFromString(@"ZSigner") checkCert:certData pass:[LCSharedUtils certificatePassword] completionHandler:completionHandler];\n    return ans;\n}'
VALIDATION_BODY = '''// V3_CANONICAL_CERTIFICATE_VALIDATION_CALLBACK_V1
+ (int)validateCertificateWithCompletionHandler:(void(^)(int status, NSDate *expirationDate, NSString *organizationalUnitName, NSString *error))completionHandler {
    NSError *error = nil;
    NSData *certData = [LCUtils certificateData];
    NSString *password = [LCSharedUtils certificatePassword];
    if (![certData isKindOfClass:[NSData class]] || ![password isKindOfClass:[NSString class]]) {
        completionHandler(2, nil, nil, @"LiveContainer's certificate data or password is unavailable.");
        return -6;
    }
    [self loadStoreFrameworksWithError2:&error];
    Class signer = NSClassFromString(@"ZSigner");
    if (error || !signer || ![signer respondsToSelector:@selector(checkCert:pass:completionHandler:)]) {
        completionHandler(2, nil, nil, @"LiveContainer's certificate validator is unavailable.");
        return -6;
    }
    return [signer checkCert:certData pass:password completionHandler:completionHandler];
}'''


def patch_validation(text):
    if text.count(VALIDATION_BODY) == 1:
        return text
    if "V3_CANONICAL_CERTIFICATE_VALIDATION_CALLBACK_V1" in text:
        raise ValueError("native certificate validation patch is incomplete or duplicated")
    start = text.index(VALIDATION_ORIGINAL)
    end = text.index("\n}\n", start) + len("\n}")
    old = text[start:end]
    if old != VALIDATION_PINNED:
        raise ValueError("pinned native validation callback changed")
    return text[:start] + VALIDATION_BODY + text[end:]


def insert(text, anchor, addition):
    if text.count(addition) == 1:
        return text
    if text.count(addition) > 1:
        raise ValueError("duplicate certificate observation implementation")
    if "certificateFactsWith" in text:
        raise ValueError("certificate observation patch is incomplete or changed")
    if text.count(anchor) != 1:
        raise ValueError("pinned certificate observation anchor changed")
    return text.replace(anchor, addition + anchor, 1)


def transform(sources):
    return {
        PATHS[0]: insert(sources[PATHS[0]], "+ (NSString*)getTeamIdWithCert:", SIGNER_DECL + "\n"),
        PATHS[1]: insert(sources[PATHS[1]], "+ (NSString*)getTeamIdWithCert:", SIGNER_METHOD),
        PATHS[2]: insert(sources[PATHS[2]], "+ (NSString*)getCertTeamIdWithKeyData:", UTIL_DECL + "\n"),
        PATHS[3]: patch_validation(insert(sources[PATHS[3]], "+ (NSString*)getCertTeamIdWithKeyData:", UTIL_METHOD)),
    }


def patch(root):
    actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if actual != PIN:
        raise ValueError("certificate observation requires the pinned LiveContainer revision")
    # Validate all four files before any source mutation.
    original = {name: (root / name).read_text(encoding="utf-8") for name in PATHS}
    prepared = transform(original)
    if transform(prepared) != prepared:
        raise ValueError("certificate observation patch is not idempotent")
    for name, text in prepared.items():
        if text != original[name]:
            (root / name).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_lc_certificate_observation.py <pinned-livecontainer-root>")
    patch(Path(sys.argv[1]))
