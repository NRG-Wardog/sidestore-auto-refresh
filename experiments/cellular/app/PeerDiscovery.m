#import "PeerDiscovery.h"
#include "ProbeRoutes.h"
#include <arpa/inet.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <sys/sysctl.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

static NSString *IPString(uint32_t ip) {
    struct in_addr address = {.s_addr = htonl(ip)};
    char text[INET_ADDRSTRLEN];
    return inet_ntop(AF_INET, &address, text, sizeof(text)) ? [NSString stringWithUTF8String:text] : nil;
}

NSArray<NSString *> *ProbePeerCandidates(NSString **reason) {
    struct ifaddrs *list = NULL;
    if (getifaddrs(&list) != 0) { *reason = @"interface_snapshot_failed"; return @[]; }
    NSMutableSet<NSNumber *> *locals = [NSMutableSet set];
    NSMutableSet<NSNumber *> *indexes = [NSMutableSet set];
    NSMutableOrderedSet<NSNumber *> *p2p = [NSMutableOrderedSet orderedSet];
    for (struct ifaddrs *p = list; p; p = p->ifa_next) {
        if (!p->ifa_addr || !p->ifa_name || p->ifa_addr->sa_family != AF_INET || !(p->ifa_flags & IFF_UP)) continue;
        uint32_t local = ntohl(((struct sockaddr_in *)p->ifa_addr)->sin_addr.s_addr);
        [locals addObject:@(local)];
        if (strncmp(p->ifa_name, "utun", 4) != 0) continue;
        unsigned index = if_nametoindex(p->ifa_name);
        if (!index || index > UINT16_MAX) continue;
        [indexes addObject:@(index)];
        if ((p->ifa_flags & IFF_POINTOPOINT) && p->ifa_dstaddr && p->ifa_dstaddr->sa_family == AF_INET) {
            uint32_t ip = ntohl(((struct sockaddr_in *)p->ifa_dstaddr)->sin_addr.s_addr);
            if (probe_unicast(ip)) [p2p addObject:@(ip)];
        }
    }
    freeifaddrs(list);
    if (!indexes.count) { *reason = @"no_active_ipv4_utun"; return @[]; }
    NSMutableOrderedSet<NSNumber *> *candidates = [NSMutableOrderedSet orderedSet];
    // NET_RT_DUMP=1. Query only IPv4 routes. No private VPN configuration access.
    int mib[] = {CTL_NET, PF_ROUTE, 0, AF_INET, 1, 0};
    size_t length = 0;
    BOOL routesOK = sysctl(mib, 6, NULL, &length, NULL, 0) == 0 && length > 0 && length <= 2 * 1024 * 1024;
    if (routesOK) {
        NSMutableData *data = [NSMutableData dataWithLength:length];
        routesOK = sysctl(mib, 6, data.mutableBytes, &length, NULL, 0) == 0 && length <= data.length;
        if (routesOK) {
            for (NSNumber *index in [indexes.allObjects sortedArrayUsingSelector:@selector(compare:)]) {
                uint32_t peers[32];
                int count = probe_route_peers(data.bytes, length, index.unsignedShortValue, peers, 32);
                if (count < 0) { routesOK = NO; [candidates removeAllObjects]; break; }
                for (int i = 0; i < count; i++) [candidates addObject:@(peers[i])];
            }
        }
    }
    [candidates addObjectsFromArray:p2p.array];
    NSMutableArray<NSString *> *result = [NSMutableArray array];
    for (NSNumber *candidate in candidates) {
        if ([locals containsObject:candidate]) continue;
        NSString *text = IPString(candidate.unsignedIntValue);
        if (text) [result addObject:text];
    }
    // Refuse a broad/ambiguous route set instead of scanning a network.
    if (result.count > 8) { *reason = @"too_many_tunnel_peers_use_override"; return @[]; }
    *reason = result.count ? (routesOK ? @"tunnel_routes_and_p2p" : @"p2p_only_route_snapshot_unavailable") : @"no_tunnel_peer_route";
    return result;
}

BOOL ProbeTCPPeer(NSString *peer, int *failure) {
    struct sockaddr_in address = {0};
    address.sin_len = sizeof(address);
    address.sin_family = AF_INET;
    address.sin_port = htons(62078);
    if (inet_pton(AF_INET, peer.UTF8String, &address.sin_addr) != 1) { *failure = EINVAL; return NO; }
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { *failure = errno; return NO; }
    int original = fcntl(fd, F_GETFL, 0);
    if (original < 0 || fcntl(fd, F_SETFL, original | O_NONBLOCK) != 0) {
        *failure = errno; close(fd); return NO;
    }
    int connected = connect(fd, (struct sockaddr *)&address, sizeof(address));
    int result = connected == 0 ? 0 : errno;
    if (result == EINPROGRESS) {
        if (fd >= FD_SETSIZE) { *failure = EMFILE; close(fd); return NO; }
        fd_set writable;
        FD_ZERO(&writable);
        FD_SET(fd, &writable);
        struct timeval timeout = {.tv_sec = 2, .tv_usec = 0};
        int ready = select(fd + 1, NULL, &writable, NULL, &timeout);
        if (ready > 0) {
            socklen_t size = sizeof(result);
            if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &result, &size) != 0) result = errno;
        } else result = ready == 0 ? ETIMEDOUT : errno;
    }
    close(fd);
    *failure = result;
    return result == 0;
}
