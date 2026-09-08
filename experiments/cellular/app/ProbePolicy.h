#ifndef PROBE_POLICY_H
#define PROBE_POLICY_H
#include <stdbool.h>

// -1 means that Network.framework did not provide a conclusive snapshot.
static inline bool probe_path_allowed(bool cellular, int wifi, int mobile) {
    return cellular ? wifi == 0 && mobile == 1 : wifi == 1;
}
static inline bool probe_result_valid(bool browse, bool before, bool after, bool interrupted) {
    return browse && before && after && !interrupted;
}
// 0=unknown, 1=Wi-Fi baseline, 2=cellular. Never infer cellular from missing Wi-Fi alone.
static inline int probe_detect_mode(int wifi, int mobile) {
    return wifi == 1 ? 1 : wifi == 0 && mobile == 1 ? 2 : 0;
}
#endif
