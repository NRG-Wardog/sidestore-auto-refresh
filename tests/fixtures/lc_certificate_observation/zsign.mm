#include "openssl.h"
#import "zsigner.h"
@implementation ZSigner
+ (NSString*)getTeamIdWithCert:(NSData *)cert pass:(NSString *)pass {
    string strPassword;

    const char* strPKeyFileData = (const char*)[cert bytes];

    strPassword = [pass cStringUsingEncoding:NSUTF8StringEncoding];
    
    ZLog::logs.clear();

    __block ZSignAsset zSignAsset;
    
    if (!zSignAsset.InitSimple(strPKeyFileData, (int)[cert length], nil, 0, strPassword)) {
        ZLog::logs.clear();
        return nil;
    }
    NSString* teamId = [NSString stringWithUTF8String:zSignAsset.m_strTeamId.c_str()];
    return teamId;
}
@end
