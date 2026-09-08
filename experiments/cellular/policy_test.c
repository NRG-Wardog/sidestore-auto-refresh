#include "ProbePolicy.h"
#include "ProbeRoutes.h"
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
    assert(probe_detect_mode(1, 1) == 1);
    assert(probe_detect_mode(0, 1) == 2);
    assert(probe_detect_mode(-1, 1) == 0);
    assert(probe_detect_mode(0, 0) == 0);
    assert(probe_detect_mode(0, -1) == 0);
    assert(probe_detect_mode(1, -1) == 1);
    assert(!probe_unicast(0));
    assert(!probe_unicast(0x7f000001));
    assert(!probe_unicast(0xe0010101));
    assert(!probe_unicast(0xefffffff));
    assert(probe_unicast(0x0a0000f1));

    uint8_t message[sizeof(ProbeRouteHeader) + 32] = {0};
    ProbeRouteHeader h = {.length = sizeof(message), .version = 5, .index = 7,
                          .flags = 5, .addresses = 3};
    memcpy(message, &h, sizeof(h));
    uint8_t *dst = message + sizeof(h), *gateway = dst + 16;
    dst[0] = gateway[0] = 16;
    dst[1] = gateway[1] = 2;
    dst[4] = gateway[4] = 10;
    dst[7] = 241;
    gateway[7] = 240;
    uint32_t peers[8];
    assert(probe_route_peers(message, sizeof(message), 7, peers, 8) == 2);
    assert(peers[0] == 0x0a0000f0 && peers[1] == 0x0a0000f1);
    assert(probe_route_peers(message, sizeof(message), 8, peers, 8) == 0);
    for (size_t n = 1; n < sizeof(message); n++) {
        assert(probe_route_peers(message, n, 7, peers, 8) == -1);
    }
    dst[0] = 255;
    assert(probe_route_peers(message, sizeof(message), 7, peers, 8) == -1);
    dst[0] = 16;
    h.flags = 0;
    memcpy(message, &h, sizeof(h));
    assert(probe_route_peers(message, sizeof(message), 7, peers, 8) == 0);
    h.flags = 1; // A subnet destination must not become a host candidate.
    memcpy(message, &h, sizeof(h));
    assert(probe_route_peers(message, sizeof(message), 7, peers, 8) == 1);
    h.addresses = 5;
    h.flags = 1;
    memcpy(message, &h, sizeof(h));
    memset(gateway + 4, 255, 4); // /32 netmask without an RTF_HOST flag.
    gateway[1] = 0;
    assert(probe_route_peers(message, sizeof(message), 7, peers, 8) == 1);
    assert(peers[0] == 0x0a0000f1);
    memset(gateway + 4, 0, 4);
    assert(probe_route_peers(message, sizeof(message), 7, peers, 8) == 0);
    h.addresses = 3;
    h.flags = 5;
    gateway[1] = 2;
    gateway[4] = 10;
    gateway[7] = 241; // Duplicate peer is tested only once.
    memcpy(message, &h, sizeof(h));
    assert(probe_route_peers(message, sizeof(message), 7, peers, 8) == 1);
    return 0;
}
