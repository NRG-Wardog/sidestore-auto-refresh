#include "ProbePolicy.h"
#include <assert.h>
int main(void) {
    assert(probe_path_allowed(true, 0, 1));
    assert(!probe_path_allowed(true, 1, 1));
    assert(!probe_path_allowed(true, -1, 1));
    assert(!probe_path_allowed(true, 0, -1));
    assert(!probe_path_allowed(true, 0, 0));
    assert(probe_path_allowed(false, 1, -1));
    assert(!probe_path_allowed(false, 0, 1));
    assert(probe_result_valid(true, true, true, false));
    assert(!probe_result_valid(false, true, true, false));
    assert(!probe_result_valid(true, true, false, false));
    assert(!probe_result_valid(true, false, true, false));
    assert(!probe_result_valid(true, true, true, true));
    return 0;
}
