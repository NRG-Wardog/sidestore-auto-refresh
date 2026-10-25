#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "idevice.h"

#define CHECK_FUNCTION(name, expected_type) \
    _Static_assert(__builtin_types_compatible_p(__typeof__(&(name)), expected_type), \
                   #name " signature mismatch")

CHECK_FUNCTION(pairable_host_prepare,
    struct IdeviceFfiError *(*)(const char *, const char *, bool,
        struct PairableHostHandle **, char **, uint8_t **, uintptr_t *, uint8_t *));
CHECK_FUNCTION(pairable_host_accept_fd,
    struct IdeviceFfiError *(*)(struct PairableHostHandle *, int32_t,
        void (*)(const char *, void *), void *, struct RpPairingPeerDeviceC **,
        struct RpPairingFileHandle **));
CHECK_FUNCTION(pairable_host_free, void (*)(struct PairableHostHandle *));
CHECK_FUNCTION(rppairing_peer_device_free, void (*)(struct RpPairingPeerDeviceC *));
CHECK_FUNCTION(tunnel_create_rppairing_with_options,
    struct IdeviceFfiError *(*)(const idevice_sockaddr *, idevice_socklen_t,
        const char *, struct RpPairingFileHandle *, bool,
        const char *(*)(void *), void *, struct AdapterHandle **,
        struct RsdHandshakeHandle **));
CHECK_FUNCTION(rppairing_pair_network,
    struct IdeviceFfiError *(*)(const idevice_sockaddr *, idevice_socklen_t,
        const char *, struct RpPairingFileHandle *, const char *(*)(void *),
        void *, struct RpPairingPeerDeviceC **));

_Static_assert(sizeof(void *) == 8, "This contract is for the arm64 iOS build");
_Static_assert(offsetof(struct IdeviceFfiError, code) == 0, "error code layout");
_Static_assert(offsetof(struct IdeviceFfiError, sub_code) == 4, "error sub-code layout");
_Static_assert(offsetof(struct IdeviceFfiError, message) == 8, "error message layout");
_Static_assert(sizeof(struct IdeviceFfiError) == 16, "error record size");
_Static_assert(offsetof(struct RpPairingPeerDeviceC, account_id) == 0, "peer account layout");
_Static_assert(offsetof(struct RpPairingPeerDeviceC, alt_irk) == 8, "peer altIRK layout");
_Static_assert(sizeof(((struct RpPairingPeerDeviceC *)0)->alt_irk) == 16, "peer altIRK size");
_Static_assert(offsetof(struct RpPairingPeerDeviceC, model) == 24, "peer model layout");
_Static_assert(offsetof(struct RpPairingPeerDeviceC, name) == 32, "peer name layout");
_Static_assert(offsetof(struct RpPairingPeerDeviceC, udid) == 40, "peer UDID layout");
_Static_assert(sizeof(struct RpPairingPeerDeviceC) == 48, "peer record size");
