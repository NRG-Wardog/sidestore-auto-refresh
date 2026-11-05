#import "bridge.h"
@protocol V3CommandService
+ (void)execute:(NSData *)request reply:(void (^)(NSData *))reply;
+ (void)refreshForHost:(NSString *)runID
           admissionID:(NSString *)admissionID
              progress:(void (^)(double))progress
            completion:(void (^)(NSString * _Nullable, NSData * _Nullable))completion;
@end
void InvokeDirectHost(NSString *runID, NSString *admissionID,
                      void (^progress)(double),
                      void (^completion)(NSString * _Nullable, NSData * _Nullable)) {
    Class<V3CommandService> service = (Class<V3CommandService>)NSClassFromString(@"V3SideStoreService");
    NSCAssert(service && [(id)service respondsToSelector:@selector(refreshForHost:admissionID:progress:completion:)], @"Missing direct service ABI");
    [service refreshForHost:runID admissionID:admissionID progress:progress completion:completion];
}
