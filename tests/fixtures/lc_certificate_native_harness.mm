#import <Foundation/Foundation.h>
#import <dispatch/dispatch.h>
#import <objc/runtime.h>
#include <cstdio>
#include <openssl/evp.h>
#include <openssl/x509.h>
#include "openssl.h"

static BOOL gClassAvailable = YES;
static BOOL gLoaderFails = NO;
static int gCheckCertCalls = 0;
static int gCheckCertReturn = 1;
static NSData *gCertificateData = nil;
static NSString *gPassword = nil;
static dispatch_semaphore_t gCheckCertCallbackRelease = nil;
static dispatch_semaphore_t gCheckCertCallbackDone = nil;

static Class TestClassFromString(NSString *name) {
    if (!gClassAvailable || ![name isEqualToString:@"ZSigner"]) return Nil;
    return objc_getClass("ZSigner");
}
#undef NSClassFromString
#define NSClassFromString(name) TestClassFromString(name)

@interface LCSharedUtils : NSObject
+ (NSString *)certificatePassword;
@end

@interface ZSigner : NSObject
$SIGNER_FACTS_DECLARATION$
$CHECK_CERT_DECLARATION$
@end

@interface LCUtils : NSObject
$LCUTILS_FACTS_DECLARATION$
$VALIDATION_DECLARATION$
+ (NSData *)certificateData;
+ (BOOL)loadStoreFrameworksWithError2:(NSError **)error;
@end

@implementation LCSharedUtils
+ (NSString *)certificatePassword { return gPassword; }
@end

@implementation ZSigner
$SIGNER_FACTS_METHOD$

// External validation-engine boundary only. Certificate-facts assertions above
// call the real pinned ZSignAsset::InitSimple implementation and real OpenSSL.
+ (int)checkCert:(NSData *)cert pass:(NSString *)pass
completionHandler:(void (^)(int, NSDate *, NSString *, NSString *))completionHandler {
    ++gCheckCertCalls;
    const int returnedStatus = gCheckCertReturn;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
        dispatch_semaphore_wait(gCheckCertCallbackRelease, DISPATCH_TIME_FOREVER);
        completionHandler(returnedStatus, nil, @"TEAM123456", nil);
        dispatch_semaphore_signal(gCheckCertCallbackDone);
    });
    return returnedStatus;
}
@end

@implementation LCUtils
+ (NSData *)certificateData { return gCertificateData; }
+ (BOOL)loadStoreFrameworksWithError2:(NSError **)error {
    if (gLoaderFails) {
        if (error) *error = [NSError errorWithDomain:@"TestLoader" code:17 userInfo:nil];
        return NO;
    }
    if (error) *error = nil;
    return YES;
}
$LCUTILS_FACTS_METHOD$

$LCUTILS_VALIDATION_METHOD$
@end

static BOOL require(BOOL condition, NSString *message) {
    if (condition) return YES;
    fprintf(stderr, "FAIL: %s\n", message.UTF8String);
    return NO;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 7) return 90;
        NSString *validOnePath = [NSString stringWithUTF8String:argv[1]];
        NSString *validTwoPath = [NSString stringWithUTF8String:argv[2]];
        NSString *malformedPath = [NSString stringWithUTF8String:argv[3]];
        NSString *expectedTeam = [NSString stringWithUTF8String:argv[4]];
        NSString *expectedDigestOne = [NSString stringWithUTF8String:argv[5]];
        NSString *expectedDigestTwo = [NSString stringWithUTF8String:argv[6]];
        NSData *first = [NSData dataWithContentsOfFile:validOnePath];
        NSData *second = [NSData dataWithContentsOfFile:validTwoPath];
        NSData *malformed = [NSData dataWithContentsOfFile:malformedPath];
        gPassword = @"native-fixture-pass";

        NSDictionary<NSString *, NSString *> *factsOne =
            [LCUtils certificateFactsWithKeyData:first password:gPassword];
        if (!require(factsOne != nil, @"valid P12 must be accepted by upstream InitSimple")) return 1;
        if (!require([factsOne[@"teamIdentifier"] isEqualToString:expectedTeam],
                     @"team must come from upstream ZSignAsset")) return 2;
        if (!require([factsOne[@"identitySHA256"] isEqualToString:expectedDigestOne],
                     @"leaf DER SHA-256 must match OpenSSL DER output")) return 3;
        if (!require(factsOne.count == 2 && factsOne[@"password"] == nil && factsOne[@"p12Data"] == nil,
                     @"facts contain only public team and digest")) return 4;

        NSDictionary<NSString *, NSString *> *factsTwo =
            [LCUtils certificateFactsWithKeyData:second password:gPassword];
        if (!require(factsTwo != nil, @"second valid P12 must be accepted")) return 5;
        if (!require([factsTwo[@"teamIdentifier"] isEqualToString:expectedTeam],
                     @"same-team certificate must preserve team")) return 6;
        if (!require([factsTwo[@"identitySHA256"] isEqualToString:expectedDigestTwo] &&
                     ![factsTwo[@"identitySHA256"] isEqualToString:factsOne[@"identitySHA256"]],
                     @"different certificate DER must produce a different digest")) return 7;

        if (!require([LCUtils certificateFactsWithKeyData:first password:@"wrong-pass"] == nil,
                     @"wrong P12 password must produce no facts")) return 8;
        if (!require([LCUtils certificateFactsWithKeyData:malformed password:gPassword] == nil,
                     @"malformed bytes must produce no facts")) return 9;

        // Missing ZSign class and framework loader failure each call the actual
        // validation wrapper's completion exactly once and report no success.
        gCertificateData = first;
        gClassAvailable = NO;
        if (!require([LCUtils certificateFactsWithKeyData:first password:gPassword] == nil,
                     @"missing native class must not fabricate certificate facts")) return 10;
        __block int missingClassCalls = 0;
        __block int missingClassStatus = -99;
        int missingClassReturn = [LCUtils validateCertificateWithCompletionHandler:
            ^(int status, NSDate *, NSString *, NSString *) {
                ++missingClassCalls; missingClassStatus = status;
            }];
        if (!require(missingClassReturn == -6 && missingClassCalls == 1 && missingClassStatus == 2,
                     @"missing native class must callback once with unavailable and return -6")) return 10;

        // Missing inputs are rejected by the actual wrapper before loader/class
        // or validator dispatch; both public callback paths must settle once.
        gClassAvailable = YES;
        gCheckCertCalls = 0;
        gCertificateData = nil;
        gPassword = @"native-fixture-pass";
        __block int nilDataCalls = 0;
        __block int nilDataStatus = -99;
        int nilDataReturn = [LCUtils validateCertificateWithCompletionHandler:
            ^(int status, NSDate *, NSString *, NSString *) {
                ++nilDataCalls; nilDataStatus = status;
            }];
        if (!require(nilDataReturn == -6 && nilDataCalls == 1 && nilDataStatus == 2 &&
                     gCheckCertCalls == 0,
                     @"nil certificate must callback once without calling ZSigner validation")) return 14;

        gCertificateData = first;
        gPassword = nil;
        __block int nilPasswordCalls = 0;
        __block int nilPasswordStatus = -99;
        int nilPasswordReturn = [LCUtils validateCertificateWithCompletionHandler:
            ^(int status, NSDate *, NSString *, NSString *) {
                ++nilPasswordCalls; nilPasswordStatus = status;
            }];
        if (!require(nilPasswordReturn == -6 && nilPasswordCalls == 1 && nilPasswordStatus == 2 &&
                     gCheckCertCalls == 0,
                     @"nil password must callback once without calling ZSigner validation")) return 15;

        gClassAvailable = YES;
        gLoaderFails = YES;
        gCertificateData = first;
        gPassword = @"native-fixture-pass";
        __block int loaderCalls = 0;
        __block int loaderStatus = -99;
        int loaderReturn = [LCUtils validateCertificateWithCompletionHandler:
            ^(int status, NSDate *, NSString *, NSString *) {
                ++loaderCalls; loaderStatus = status;
            }];
        if (!require(loaderReturn == -6 && loaderCalls == 1 && loaderStatus == 2,
                     @"native framework load failure must callback once with unavailable and return -6")) return 11;

        gLoaderFails = NO;
        gCheckCertCalls = 0;
        gCheckCertCallbackRelease = dispatch_semaphore_create(0);
        gCheckCertCallbackDone = dispatch_semaphore_create(0);
        __block int validationCalls = 0;
        __block int validationStatus = -99;
        int validationReturn = [LCUtils validateCertificateWithCompletionHandler:
            ^(int status, NSDate *, NSString *, NSString *) {
                ++validationCalls; validationStatus = status;
            }];
        if (!require(validationReturn == 1 && gCheckCertCalls == 1,
                     @"wrapper must return the existing ZSigner validation result")) return 12;
        // Keep the mocked checker callback parked until the production wrapper
        // has returned, then wait on a distinct completion signal.
        dispatch_semaphore_signal(gCheckCertCallbackRelease);
        if (dispatch_semaphore_wait(gCheckCertCallbackDone,
                dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC)) != 0 ||
            !require(validationCalls == 1 && validationStatus == 1,
                     @"asynchronous native validation callback must pass through exactly once")) return 13;

        puts("LC_CERTIFICATE_FACTS_NATIVE_PASS");
        return 0;
    }
}
