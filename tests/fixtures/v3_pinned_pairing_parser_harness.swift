import Foundation

@main
struct PinnedPairingParserHarness {
    static func main() throws {
        func plistString(_ value: Any) throws -> String {
            let data = try PropertyListSerialization.data(fromPropertyList: value,
                format: .xml, options: 0)
            return String(data: data, encoding: .utf8)!
        }

        let lockdown: [String: Any] = [
            "WiFiMACAddress": "00:11:22:33:44:55", "SystemBUID": "system-buid",
            "RootPrivateKey": Data([1]), "HostPrivateKey": Data([2]), "HostID": "host-id",
            "RootCertificate": Data([3]), "UDID": "device-id", "EscrowBag": Data([4]),
            "HostCertificate": Data([5]), "DeviceCertificate": Data([6])
        ]
        let lockdownParsed = try PairingFileParser.parse(content: plistString(lockdown))
        precondition(lockdownParsed.mode == .lockdown,
            "the pinned parser accepts the complete Lockdown pairing shape")

        let remotePairing: [String: Any] = [
            "private_key": Data([1]), "public_key": Data([2]), "identifier": "device-id"
        ]
        let remoteParsed = try PairingFileParser.parse(content: plistString(remotePairing))
        precondition(remoteParsed.mode == .rppairing,
            "the pinned parser accepts the supported RemotePairing shape")

        for invalid in [["unrelated": "valid plist"], ["HostID": "incomplete"]] {
            do {
                _ = try PairingFileParser.parse(content: plistString(invalid))
                preconditionFailure("a valid plist without required pairing fields was accepted")
            } catch is PairingError { }
        }
        do {
            _ = try PairingFileParser.parse(content: "not a property list")
            preconditionFailure("malformed plist bytes were accepted as a pairing file")
        } catch is PairingError { }
        print("V3_PINNED_PAIRING_PARSER_PASS")
    }
}
