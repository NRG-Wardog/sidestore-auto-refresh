#import "bridge.h"
@protocol V3CommandService
+ (void)execute:(NSData *)request reply:(void (^)(NSData *))reply;
+ (void)refreshForHost:(NSString *)runID
              progress:(void (^)(double))progress
            completion:(void (^)(NSString * _Nullable, NSData * _Nullable))completion;
@end
void InvokeDirectHost(NSString *runID,
                      void (^progress)(double),
                      void (^completion)(NSString * _Nullable, NSData * _Nullable)) {
    Class<V3CommandService> service = (Class<V3CommandService>)NSClassFromString(@"V3SideStoreService");
    NSCAssert(service && [(id)service respondsToSelector:@selector(refreshForHost:progress:completion:)], @"Missing direct service ABI");
    [service refreshForHost:runID progress:progress completion:completion];
}
