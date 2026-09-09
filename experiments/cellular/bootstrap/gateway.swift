
    // CELLULAR_BOOTSTRAP_GATEWAY_V1. The entire read-only run owns ffiQueue.
    // Production batch acquisition and endpoint mutation use that same queue.
    public func cellularValidatePairing(_ content: String) async -> Bool {
        (try? await withFFIDispatch(on: ffiQueue) {
            guard let parsed = try? PairingFileParser.parse(content: content),
                  parsed.mode == .lockdown else { return false }
            var handle: OpaquePointer?
            let error = parsed.rawData.withUnsafeBytes { bytes in
                idevice_pairing_file_from_bytes(bytes.baseAddress?.assumingMemoryBound(to: UInt8.self),
                                               UInt(bytes.count), &handle)
            }
            defer { if let handle { idevice_pairing_file_free(handle) } }
            if let error { idevice_error_free(error); return false }
            return handle != nil
        }) ?? false
    }

    public func cellularReadOnlyProbe(_ content: String, discovery: CellularProbeDiscovery,
                                      expectedConfiguration: String?) async -> CellularProbeResult {
        do {
            return try await withFFIDispatch(on: ffiQueue) {
                var result = CellularProbeResult()
                guard self.batchCount == 0, self.adapter == nil, self.handshake == nil,
                      self.coreDeviceProvider == nil, !tunnel_heartbeat_is_active() else {
                    result.busy = true; result.failureStage = "TRANSPORT_BUSY"; return result
                }
                guard let parsed = try? PairingFileParser.parse(content: content),
                      parsed.mode == .lockdown else {
                    result.failureStage = "PAIRING_PARSE_FAILED"; return result
                }
                var pairing: OpaquePointer?
                let error = parsed.rawData.withUnsafeBytes { bytes in
                    idevice_pairing_file_from_bytes(bytes.baseAddress?.assumingMemoryBound(to: UInt8.self),
                                                   UInt(bytes.count), &pairing)
                }
                defer { if let pairing { idevice_pairing_file_free(pairing) } }
                if let error {
                    result.errorCode = error.pointee.code; idevice_error_free(error)
                    result.failureStage = "PAIRING_PARSE_FAILED"; return result
                }
                guard pairing != nil else { result.failureStage = "PAIRING_PARSE_FAILED"; return result }
                result.parsed = true
                guard !discovery.peers.isEmpty else { result.failureStage = "VPN_PEER_NOT_FOUND"; return result }
                guard discovery.peers.count <= 8 else { result.failureStage = "VPN_PEER_AMBIGUOUS"; return result }
                var reachable: [CellularProbePeer] = []
                var lastError: Int32 = 0
                for peer in discovery.peers {
                    let code = NetworkUtils.testTCPResult(ip: peer.ip, port: 62078, timeoutMs: 2000)
                    if code == 0 { reachable.append(peer) }
                    lastError = code
                }
                result.reachableCount = reachable.count
                guard reachable.count == 1 else {
                    if discovery.peers.count == 1 {
                        result.selectedPeer = discovery.peers[0]
                        result.errorCode = lastError
                        result.tcp = lastError == ECONNREFUSED ? .refused : (lastError == ETIMEDOUT ? .timeout : .fail)
                        result.failureStage = lastError == ECONNREFUSED ? "VPN_PEER_TCP_REFUSED" : "VPN_PEER_TCP_FAILED"
                    } else { result.failureStage = "VPN_PEER_AMBIGUOUS" }
                    return result
                }
                let peer = reachable[0]
                result.selectedPeer = peer; result.tcp = .pass
                if let expectedConfiguration, expectedConfiguration != peer.configuration {
                    result.configurationMismatch = true
                    result.failureStage = "BASELINE_MISMATCH_WIFI_REQUIRED"; return result
                }
                var provider: OpaquePointer?
                var providerError: UnsafeMutablePointer<IdeviceFfiError>?
                try "SideStore".withCString { label in
                    try self.withSockaddr(ip: peer.ip, port: 62078) { address, _ in
                        providerError = idevice_tcp_provider_new(address, pairing, label, &provider)
                    }
                }
                // The pinned provider takes ownership on success, not on invalid input.
                if let providerError {
                    result.errorCode = providerError.pointee.code; idevice_error_free(providerError)
                    result.failureStage = "PROVIDER_CREATE_FAILED"; return result
                }
                pairing = nil
                guard let provider else { result.failureStage = "PROVIDER_CREATE_FAILED"; return result }
                defer { idevice_provider_free(provider) }
                let authentication = cellular_lockdown_validate(provider, &result.errorCode)
                guard authentication == 0 else {
                    result.lockdownRejected = authentication == 2 || authentication == 4
                    result.failureStage = result.lockdownRejected ? "PAIRING_LOCKDOWN_REJECTED" : "LOCKDOWN_FAILED_PAIRING_UNVERIFIED"
                    return result
                }
                result.lockdownAccepted = true
                var adapter: OpaquePointer?
                var handshake: OpaquePointer?
                var stage: UInt32 = 0
                defer {
                    if let handshake { rsd_handshake_free(handshake) }
                    if let adapter { adapter_free(adapter) }
                    tunnel_heartbeat_stop()
                }
                let tunnelError = cellular_tunnel_create(provider, &adapter, &handshake, &stage)
                result.coreDevicePassed = stage >= 1
                result.rsdPassed = stage == 2 && adapter != nil && handshake != nil && tunnel_heartbeat_is_active()
                if let tunnelError {
                    result.errorCode = tunnelError.pointee.code; idevice_error_free(tunnelError)
                    result.rsdPassed = false
                }
                result.failureStage = result.rsdPassed ? "RSD_PASS" : (result.coreDevicePassed ? "RSD_FAILED" : "COREDEVICE_FAILED")
                return result
            }
        } catch {
            var result = CellularProbeResult()
            result.failureStage = "GATEWAY_OPERATION_FAILED"
            return result
        }
    }
