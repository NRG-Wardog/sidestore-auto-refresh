
    // Uses the exact production scanner and candidate extraction, but does not
    // mutate saved VPN configuration or select a first responder.
    func cellularProbeDiscovery() -> CellularProbeDiscovery {
        let tunnels = NetworkIfaceScanner.scan(quiet: true)
            .compactMap { $0 as? TunnelNetInfo }
            .filter { $0.tunnelType == .utun && !$0.interfaceAddresses.v4.isEmpty && $0.interfaceAddresses.v6.isEmpty }
            .sorted { $0.name < $1.name }
        var peers: [CellularProbePeer] = []
        for tunnel in tunnels {
            let addresses = tunnel.interfaceAddresses.v4.map { "\($0.host)/\($0.mask)" }.sorted().joined(separator: ",")
            for candidate in resolveCandidatePeers(for: tunnel) {
                let configuration = "resolver=pinned-minimuxer-v1;local=\(addresses);peer=\(candidate.ip);mask=\(candidate.mask ?? "");port=62078"
                let peer = CellularProbePeer(tunnel: tunnel.name, ip: candidate.ip, configuration: configuration)
                if !peers.contains(peer) { peers.append(peer) }
            }
        }
        return CellularProbeDiscovery(interfaceCount: tunnels.count, peers: peers)
    }
