import Foundation

@main
struct IPAStagingHarness {
    static func main() throws {
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        let pickedDirectory = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        try fm.createDirectory(at: pickedDirectory, withIntermediateDirectories: true)
        defer {
            try? fm.removeItem(at: root)
            try? fm.removeItem(at: pickedDirectory)
        }

        // The asCopy picker URL disappears after the immediate staging copy.
        let picked = pickedDirectory.appendingPathComponent("known-valid.ipa")
        let bytes = Data([0x50, 0x4b, 0x03, 0x04, 0x01, 0x02, 0x03])
        try bytes.write(to: picked)
        let durableToken = try V3IPAStaging.stage(sourceURL: picked, containerRoot: root)
        try fm.removeItem(at: picked)
        let durable = try V3IPAStaging.resolve(token: durableToken, containerRoot: root)
        let stagedBytes = try Data(contentsOf: durable)
        precondition(stagedBytes == bytes)

        // Invalid archives keep an honest pre-install classification.
        let invalidToken = try V3IPAStaging.stage(sourceURL: durable, containerRoot: root)
        do {
            _ = try V3IPAStaging.inspect(token: invalidToken, containerRoot: root) { _ -> String in
                throw NSError(domain: "ArchiveFixture", code: 1)
            }
            preconditionFailure("invalid IPA was accepted")
        } catch let error as CombinedIPAFileError {
            precondition(error.problem == .invalidPackage)
        }

        // A failed attempt can retry with the same staged bytes; cleanup occurs
        // only when the attempt lifecycle is finally acknowledged.
        let retryBytes = try Data(contentsOf: V3IPAStaging.resolve(token: invalidToken, containerRoot: root))
        precondition(retryBytes == bytes)
        try V3IPAStaging.cleanup(token: invalidToken, containerRoot: root)
        do {
            _ = try V3IPAStaging.resolve(token: invalidToken, containerRoot: root)
            preconditionFailure("cleaned staged file still resolved")
        } catch let error as CombinedIPAFileError {
            precondition(error.problem == .missingFile)
        }

        let empty = pickedDirectory.appendingPathComponent("empty.ipa")
        try Data().write(to: empty)
        do {
            _ = try V3IPAStaging.stage(sourceURL: empty, containerRoot: root)
            preconditionFailure("zero-byte IPA was staged")
        } catch let error as CombinedIPAFileError {
            precondition(error.problem == .emptyFile)
        }

        let successToken = try V3IPAStaging.stage(sourceURL: durable, containerRoot: root)
        try V3IPAStaging.cleanup(token: successToken, containerRoot: root)
        let cancelledToken = try V3IPAStaging.stage(sourceURL: durable, containerRoot: root)
        try V3IPAStaging.cleanup(token: cancelledToken, containerRoot: root)

        let sibling = root.appendingPathComponent("keep.txt")
        try Data("keep".utf8).write(to: sibling)
        for invalid in ["../keep.txt", "/tmp/keep.txt", "not-a-uuid", UUID().uuidString.uppercased()] {
            do {
                _ = try V3IPAStaging.resolve(token: invalid, containerRoot: root)
                preconditionFailure("invalid token resolved: \(invalid)")
            } catch let error as CombinedIPAFileError {
                precondition(error.problem == .invalidToken)
            }
        }
        let siblingContents = try String(contentsOf: sibling, encoding: .utf8)
        precondition(siblingContents == "keep",
                     "invalid token traversed outside staging")
        print("V3_IPA_STAGING_PASS")
    }
}
