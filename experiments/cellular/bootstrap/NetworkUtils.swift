import Foundation
import Darwin

public enum NetworkUtils {
    public static func testTCP(ip: String, port: UInt16, timeoutMs: Int = MinimuxerConstants.defaultTCPProbeTimeoutMs) -> Bool {
        testTCPResult(ip: ip, port: port, timeoutMs: timeoutMs) == 0
    }

    // Shared by production readiness and diagnostics. Preserve SO_ERROR even
    // when poll reports POLLERR/HUP, which is how refusal may be delivered.
    public static func testTCPResult(ip: String, port: UInt16, timeoutMs: Int) -> Int32 {
        guard timeoutMs > 0, timeoutMs <= Int(Int32.max) else { return EINVAL }
        var v4 = sockaddr_in()
        var v6 = sockaddr_in6()
        let ipv6 = ip.contains(":")
        if ipv6 {
            guard inet_pton(AF_INET6, ip, &v6.sin6_addr) == 1 else { return EINVAL }
            v6.sin6_family = sa_family_t(AF_INET6); v6.sin6_port = port.bigEndian
            v6.sin6_len = UInt8(MemoryLayout<sockaddr_in6>.size)
        } else {
            guard inet_pton(AF_INET, ip, &v4.sin_addr) == 1 else { return EINVAL }
            v4.sin_family = sa_family_t(AF_INET); v4.sin_port = port.bigEndian
            v4.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        }
        let fd = socket(ipv6 ? AF_INET6 : AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return errno }
        defer { close(fd) }
        let flags = fcntl(fd, F_GETFL, 0)
        guard flags >= 0, fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0 else { return errno }
        let connected: Int32
        if ipv6 {
            connected = withUnsafePointer(to: &v6) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in6>.size))
                }
            }
        } else {
            connected = withUnsafePointer(to: &v4) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
        }
        if connected == 0 { return 0 }
        guard errno == EINPROGRESS else { return errno }
        var event = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
        let count = poll(&event, 1, Int32(timeoutMs))
        if count == 0 { return ETIMEDOUT }
        if count < 0 { return errno }
        var socketError: Int32 = 0
        var length = socklen_t(MemoryLayout<Int32>.size)
        guard getsockopt(fd, SOL_SOCKET, SO_ERROR, &socketError, &length) == 0 else { return errno }
        if socketError != 0 { return socketError }
        return event.revents & Int16(POLLOUT) != 0 ? 0 : ECONNABORTED
    }
}
