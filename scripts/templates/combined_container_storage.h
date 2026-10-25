#import <Foundation/Foundation.h>

// LC_SERVICE_STORAGE_V1: shared by legacy bootstrap and service preparation.
// Only create missing directories. Never replace, migrate, remove, or reset existing contents.
static inline BOOL LCPrepareContainerDirectories(NSString *home, NSError **error) {
    NSFileManager *fm = NSFileManager.defaultManager;
    for (NSString *relative in @[@"Library/Caches", @"Library/Cookies", @"Documents", @"SystemData"]) {
        NSString *path = [home stringByAppendingPathComponent:relative];
        BOOL directory = NO;
        if ([fm fileExistsAtPath:path isDirectory:&directory]) {
            if (directory) continue;
            if (error) *error = [NSError errorWithDomain:NSCocoaErrorDomain code:NSFileWriteFileExistsError userInfo:nil];
            return NO;
        }
        if (![fm createDirectoryAtPath:path withIntermediateDirectories:YES attributes:nil error:error]) return NO;
    }
    return YES;
}
