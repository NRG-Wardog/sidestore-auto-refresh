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

        // The host's cached LiveContainer data group can remain AltStore-owned
        // during migration. IPA staging must still select the packaged
        // SideStore group that the embedded service opens from its own bundle.
        let cachedLiveContainerGroup = "group.com.rileytestut.AltStore"
        var resolvedGroup: String?
        let hostInfo: [String: Any] = ["ALTAppGroups": [V3IPAStaging.sideStoreAppGroupIdentifier]]
        let hostStagingRoot = V3IPAStaging.sideStoreContainerRoot(bundleInfo: hostInfo) { group in
            resolvedGroup = group
            return root
        }
        let serviceInfo: [String: Any] = ["ALTAppGroups": V3IPAStaging.sideStoreAppGroupIdentifier]
        let serviceStagingRoot = V3IPAStaging.sideStoreContainerRoot(bundleInfo: serviceInfo) { group in
            precondition(group == resolvedGroup,
                "host and service must resolve the same canonical IPA staging group")
            return root
        }
        let multiGroupServiceRoot = V3IPAStaging.sideStoreContainerRoot(bundleInfo: [
            "ALTAppGroups": [cachedLiveContainerGroup, V3IPAStaging.sideStoreAppGroupIdentifier]
        ]) { group in
            precondition(group == resolvedGroup,
                "the resolver selects SideStore from a multi-group service entitlement list")
            return root
        }
        precondition(cachedLiveContainerGroup != resolvedGroup && hostStagingRoot == serviceStagingRoot &&
                     serviceStagingRoot == multiGroupServiceRoot,
            "an AltStore-origin user-data cache must not split host staging from SideStore's service container")
        precondition(V3IPAStaging.sideStoreContainerRoot(bundleInfo: ["ALTAppGroups": [cachedLiveContainerGroup]],
            resolveContainer: { _ in root }) == nil,
            "the IPA staging resolver must reject a non-SideStore group")

        let signedGroup = "group.com.SideStore.SideStore.TESTTEAM"
        var selectedGroups: [String] = []
        let signedHostRoot = V3IPAStaging.sideStoreContainerRoot(bundleInfo:
            ["ALTAppGroups": [signedGroup]], selectedGroup: signedGroup) { group in
                selectedGroups.append(group); return group == signedGroup ? root : nil
            }
        let signedServiceRoot = V3IPAStaging.sideStoreContainerRoot(bundleInfo:
            ["ALTAppGroups": [V3IPAStaging.sideStoreAppGroupIdentifier]],
            selectedGroup: signedGroup) { group in
                selectedGroups.append(group); return group == signedGroup ? root : nil
            }
        precondition(signedHostRoot == root && signedServiceRoot == root &&
                     selectedGroups == [signedGroup, signedGroup],
            "re-signed host and embedded service must stage and resolve in one entitled group")
        let fallbackSignedRoot = V3IPAStaging.sideStoreContainerRoot(bundleInfo:
            ["ALTAppGroups": [signedGroup]], resolveContainer: { $0 == signedGroup ? root : nil })
        precondition(fallbackSignedRoot == root,
            "a signer-rewritten ALTAppGroups value remains usable without a launch handoff")
        selectedGroups.removeAll()
        let inheritedAltRoot = V3IPAStaging.sideStoreContainerRoot(bundleInfo:
            ["ALTAppGroups": [signedGroup]], selectedGroup: cachedLiveContainerGroup) { group in
                selectedGroups.append(group); return group == cachedLiveContainerGroup ? root : nil
            }
        precondition(inheritedAltRoot == root && selectedGroups == [cachedLiveContainerGroup],
            "an explicitly validated LC group wins over a conflicting Info.plist fallback")

        // The asCopy picker URL disappears after the immediate staging copy.
        let picked = pickedDirectory.appendingPathComponent("known-valid.ipa")
        let bytes = Data([0x50, 0x4b, 0x03, 0x04, 0x01, 0x02, 0x03])
        try bytes.write(to: picked)
        let durableToken = try V3IPAStaging.stage(sourceURL: picked, containerRoot: hostStagingRoot!)
        try fm.removeItem(at: picked)
        let durable = try V3IPAStaging.resolve(token: durableToken, containerRoot: serviceStagingRoot!)
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

        let staging = V3IPAStaging.stagingDirectory(containerRoot: root)
        let cleanupNow = Date()
        let stale = staging.appendingPathComponent(UUID().uuidString.lowercased() + ".ipa")
        let activeToken = UUID().uuidString.lowercased()
        let active = staging.appendingPathComponent(activeToken + ".ipa")
        let recent = staging.appendingPathComponent(UUID().uuidString.lowercased() + ".ipa")
        let unrelated = staging.appendingPathComponent("notes.ipa")
        try bytes.write(to: stale)
        try bytes.write(to: active)
        try bytes.write(to: recent)
        try bytes.write(to: unrelated)
        let oldDate = cleanupNow.addingTimeInterval(-V3IPAStaging.orphanRetention - 1)
        try fm.setAttributes([.modificationDate: oldDate], ofItemAtPath: active.path)
        try fm.setAttributes([.modificationDate: oldDate],
                             ofItemAtPath: stale.path)
        let removedOrphans = try V3IPAStaging.cleanupOrphans(containerRoot: root,
            preservingTokens: [activeToken], now: cleanupNow)
        precondition(removedOrphans == 1,
                     "startup cleanup removes only stale canonical IPA files without active service leases")
        precondition(!fm.fileExists(atPath: stale.path) && fm.fileExists(atPath: active.path) &&
                     fm.fileExists(atPath: recent.path) && fm.fileExists(atPath: unrelated.path),
                     "active leases, recent staged files, and unrelated entries remain untouched")

        let oldSource = pickedDirectory.appendingPathComponent("old-source-mtime.ipa")
        try bytes.write(to: oldSource)
        try fm.setAttributes([.modificationDate: oldDate], ofItemAtPath: oldSource.path)
        let freshlyStaged = try V3IPAStaging.stage(sourceURL: oldSource, containerRoot: root)
        let stagedModificationDate = try V3IPAStaging.resolve(token: freshlyStaged,
            containerRoot: root).resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate!
        precondition(cleanupNow.timeIntervalSince(stagedModificationDate) < V3IPAStaging.orphanRetention,
                     "staging resets the file age so an old source IPA is not pruned immediately")
        print("V3_IPA_STAGING_PASS")
    }
}
