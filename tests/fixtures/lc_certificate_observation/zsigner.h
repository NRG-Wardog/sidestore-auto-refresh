@interface ZSigner : NSObject
+ (int)checkCert:(NSData *)cert pass:(NSString *)pass completionHandler:(void(^)(int status, NSDate *expirationDate, NSString *organizationalUnitName, NSString *error))completionHandler;
+ (NSString*)getTeamIdWithCert:(NSData *)cert pass:(NSString *)pass;
@end
