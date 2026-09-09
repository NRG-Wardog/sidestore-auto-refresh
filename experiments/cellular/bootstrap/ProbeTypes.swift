import Foundation

public struct CellularProbePeer: Sendable, Equatable {
    public let tunnel: String
    public let ip: String
    public let configuration: String
    public init(tunnel: String, ip: String, configuration: String) {
        self.tunnel = tunnel; self.ip = ip; self.configuration = configuration
    }
}

public struct CellularProbeDiscovery: Sendable {
    public let interfaceCount: Int
    public let peers: [CellularProbePeer]
    public init(interfaceCount: Int, peers: [CellularProbePeer]) {
        self.interfaceCount = interfaceCount; self.peers = peers
    }
}

public struct CellularProbeResult: Sendable {
    public var parsed = false
    public var busy = false
    public var configurationMismatch = false
    public var selectedPeer: CellularProbePeer?
    public var reachableCount = 0
    public var tcp: CellularBootstrapState.Check = .notAttempted
    public var lockdownAccepted = false
    public var lockdownRejected = false
    public var coreDevicePassed = false
    public var rsdPassed = false
    public var errorCode: Int32 = 0
    public var failureStage = "NOT_ATTEMPTED"
    public init() {}
}
