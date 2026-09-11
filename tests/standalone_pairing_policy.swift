import Foundation

@main struct PairingPolicyCheck {
    static func main() throws {
        let lockdownKeys = ["WiFiMACAddress", "SystemBUID", "RootPrivateKey", "HostPrivateKey",
                            "HostID", "RootCertificate", "UDID", "EscrowBag",
                            "HostCertificate", "DeviceCertificate"]
        let remoteKeys = ["private_key", "public_key", "identifier"]
        func record(_ keys: [String]) -> [String: any Sendable] {
            var value: [String: any Sendable] = [:]
            for key in keys { value[key] = "synthetic-test-value" }
            return value
        }
        for (keys, expected) in [(lockdownKeys, PairingProtocol.lockdown),
                                 (remoteKeys, PairingProtocol.rppairing),
                                 (lockdownKeys + remoteKeys, PairingProtocol.lockdown)] {
            let mode = try PairingFileParser.validatePairingFile(from: record(keys))
            precondition(mode == expected)
            let xml = try PropertyListSerialization.data(fromPropertyList: record(keys), format: .xml, options: 0)
            let parsed = try PairingFileParser.parse(content: String(decoding: xml, as: UTF8.self))
            precondition(parsed.mode == expected)
            precondition(parsed.rawData == xml)
        }
        for invalid in [nil, [:], record(["HostID"])] as [[String: any Sendable]?] {
            do {
                _ = try PairingFileParser.validatePairingFile(from: invalid)
                fatalError("Incomplete pairing record accepted")
            } catch is PairingError {}
        }
        print("SideStore 0.7.0 pairing policy PASS")
    }
}
