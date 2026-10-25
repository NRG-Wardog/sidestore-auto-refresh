import Foundation

enum PackageType {
    case ipa
    case app
}

enum OperationError: Error {
    case invalidApp(reason: String)
}

// This type only satisfies the unexecuted IPA branch for the .app fixture
// run. Match SideSign's pinned Archive.Reader API: goToNextFile() advances
// synchronously and reports whether another entry exists; it does not throw.
// The production AppManager parser is inserted verbatim below.
enum Archive {
    final class Reader {
        static func open(at url: URL) throws -> Reader { Reader() }
        func goToFirstFile() throws {}
        func currentFilename() throws -> String { "" }
        func readCurrentFile() throws -> Data { Data() }
        func goToNextFile() -> Bool { false }
    }
}

enum AppManager {
__PRODUCTION_METADATA_PARSER__
}

@main
struct InstallMetadataParserHarness {
    static func main() throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let appURL = root.appendingPathComponent("Example.app", isDirectory: true)
        try FileManager.default.createDirectory(at: appURL, withIntermediateDirectories: true)
        let plist: [String: Any] = [
            "CFBundleIdentifier": "  com.example.real-app  ",
            "CFBundleDisplayName": "Example Display Name",
            "CFBundleName": "Fallback Name"
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try data.write(to: appURL.appendingPathComponent("Info.plist"))

        let parsed = try AppManager.readAppMetadata(from: appURL, packageType: .app)
        precondition(parsed.bundleIdentifier == "com.example.real-app", "bundle ID trim/selection changed")
        precondition(parsed.name == "Example Display Name", "display name selection changed")

        let invalidURL = root.appendingPathComponent("Invalid.app", isDirectory: true)
        try FileManager.default.createDirectory(at: invalidURL, withIntermediateDirectories: true)
        let invalidData = try PropertyListSerialization.data(
            fromPropertyList: ["CFBundleIdentifier": "  "], format: .xml, options: 0)
        try invalidData.write(to: invalidURL.appendingPathComponent("Info.plist"))
        do {
            _ = try AppManager.readAppMetadata(from: invalidURL, packageType: .app)
            fatalError("empty bundle ID must remain invalid")
        } catch is OperationError {
            // Expected production parser rejection.
        }

        print("V3 PRODUCTION APPMANAGER METADATA PARSER PASS")
    }
}
