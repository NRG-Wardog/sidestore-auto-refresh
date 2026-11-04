#import <Foundation/Foundation.h>
void InvokeDirectHost(NSString *runID,
                      void (^progress)(double),
                      void (^completion)(NSString * _Nullable, NSData * _Nullable));
