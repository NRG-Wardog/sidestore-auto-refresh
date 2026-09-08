#ifndef PROBE_ROUTES_H
#define PROBE_ROUTES_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdbool.h>

// Darwin NET_RT_DUMP ABI, also used by pinned minimuxer NetworkIfaceScanner.
// Parse copied headers and bounded sockaddr records, never unaligned pointers.
typedef struct {
    uint16_t length;
    uint8_t version, type;
    uint16_t index;
    int32_t flags, addresses, pid, sequence, error, use;
    uint32_t inits, metrics[14];
} ProbeRouteHeader;
_Static_assert(sizeof(ProbeRouteHeader) == 92, "Unexpected Darwin route ABI");

static inline bool probe_unicast(uint32_t ip) {
    return ip != 0 && (ip >> 24) != 0 && (ip >> 24) != 127 && (ip >> 24) < 224;
}
static inline uint32_t probe_ipv4_bytes(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static inline void probe_add_peer(uint32_t ip, uint32_t *out, size_t *count, size_t cap) {
    if (!probe_unicast(ip)) return;
    for (size_t i = 0; i < *count; i++) if (out[i] == ip) return;
    if (*count < cap) out[(*count)++] = ip;
}

// Return -1 on malformed input. Only up routes of the requested interface count.
// Route gateways first, host (/32) destinations second. Never enumerate subnets.
static inline int probe_route_peers(const uint8_t *data, size_t length, uint16_t index,
                                   uint32_t *out, size_t capacity) {
    size_t offset = 0, count = 0;
    while (offset < length) {
        ProbeRouteHeader h;
        if (length - offset < sizeof(h)) return -1;
        memcpy(&h, data + offset, sizeof(h));
        if (h.length < sizeof(h) || h.length > length - offset || h.version != 5) return -1;
        size_t pos = sizeof(h);
        uint32_t dst = 0, gateway = 0, mask = 0;
        for (unsigned i = 0; i < 8; i++) {
            if (!(h.addresses & (1 << i))) continue;
            if (pos >= h.length) return -1;
            const uint8_t *sa = data + offset + pos;
            size_t size = sa[0], aligned = size ? (size + 3) & ~(size_t)3 : 4;
            if (aligned > h.length - pos) return -1;
            if (size >= 8 && (sa[1] == 2 || i == 2)) {
                uint32_t ip = probe_ipv4_bytes(sa + 4);
                if (i == 0) dst = ip;
                if (i == 1) gateway = ip;
                if (i == 2) mask = ip;
            }
            pos += aligned;
        }
        if (h.index == index && (h.flags & 1) && h.error == 0) {
            probe_add_peer(gateway, out, &count, capacity);
            if ((h.flags & 4) || mask == UINT32_MAX) probe_add_peer(dst, out, &count, capacity);
            if (count == capacity) return -1;
        }
        offset += h.length;
    }
    return (int)count;
}
#endif
