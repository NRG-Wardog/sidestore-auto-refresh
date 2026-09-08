#import <Foundation/Foundation.h>

// Fresh, bounded discovery per user-requested test. No saved endpoint fallback.
NSArray<NSString *> *ProbePeerCandidates(NSString **reason);
BOOL ProbeTCPPeer(NSString *peer, int *failure);
