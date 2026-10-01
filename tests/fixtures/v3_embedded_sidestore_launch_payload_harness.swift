// LC_SERVICE_CONNECTION_V1: the dedicated embedded-SideStore launch must carry
// the host's validated App Group, because that service runs inside LiveProcess
// and every cross-process store resolves from there.
import Foundation

@main
struct EmbeddedSideStoreLaunchPayloadHarness {
    // The group iLoader creates for SpecialApp::SideStoreLc, and the pre-resign
    // names it leaves behind in an extension's Info.plist.
    static let resignedGroup = "group.com.SideStore.SideStore.TESTTEAM"
    static let stalePackaged = "group.com.SideStore.SideStore"
    static let legacyAltStore = "group.com.rileytestut.AltStore"
    static let staleAltStoreTeam = "group.com.rileytestut.AltStore.TESTTEAM"

    // What this process is entitled to after a re-sign: only suffixed names.
    static var entitled: Set<String> = [resignedGroup, staleAltStoreTeam]
    /// The main bundle after apply_special_app_behavior rewrote its ALTAppGroups.
    static let hostBundleInfo: [String: Any] = ["ALTAppGroups": [resignedGroup]]
    /// The extension bundle, which that same function never touches.
    static let extensionBundleInfo: [String: Any] = ["ALTAppGroups": [stalePackaged, legacyAltStore]]
    /// What LiveProcess publishes, once it has received and validated a payload.
    static var published: String?

    static let fileManager = ResignFileManager()

    final class ResignFileManager: FileManager {
        override func containerURL(forSecurityApplicationGroupIdentifier identifier: String) -> URL? {
            guard EmbeddedSideStoreLaunchPayloadHarness.entitled.contains(identifier) else { return nil }
            let root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
                .appendingPathComponent("resign-store", isDirectory: true)
            try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
            return root.appendingPathComponent(
                identifier.replacingOccurrences(of: "/", with: "_"), isDirectory: true)
        }
    }

    static func expect(_ condition: Bool, _ label: String) {
        if !condition {
            FileHandle.standardError.write(
                Data("V3_EMBEDDED_SIDESTORE_LAUNCH_FAIL \(label)\n".utf8))
            exit(1)
        }
    }

    /// The host resolves its own selection. It never reads what it published,
    /// because it is the process that publishes.
    static func hostResolve(_ selected: String?, bundleInfo: [String: Any]) -> V3SharedAppGroup.Identity? {
        V3SharedAppGroup.identity(selectedGroup: selected, inheritedGroup: nil, usesEnvironment: false,
                                  bundleInfo: bundleInfo) {
            fileManager.containerURL(forSecurityApplicationGroupIdentifier: $0)
        }
    }

    /// The service resolves only what LiveProcess handed it, then its own plist.
    static func serviceResolve(_ bundleInfo: [String: Any]) -> V3SharedAppGroup.Identity? {
        V3SharedAppGroup.identity(selectedGroup: nil, inheritedGroup: published, usesEnvironment: false,
                                  bundleInfo: bundleInfo) {
            fileManager.containerURL(forSecurityApplicationGroupIdentifier: $0)
        }
    }

    static func launch(_ selected: String?, bundleInfo: [String: Any]) -> [String: Any] {
        V3EmbeddedSideStoreLaunchPayload.userInfo(
            bookmark: Data([0x01, 0x02]), endpoint: "listener-endpoint",
            identity: hostResolve(selected, bundleInfo: bundleInfo))
    }

    static func forwarded(_ userInfo: [String: Any]) -> String? {
        userInfo[V3EmbeddedSideStoreLaunchPayload.appGroupKey] as? String
    }

    static func main() {
        V3SharedAppGroup.containerFileManager = fileManager

        // 1. The dedicated launch forwards the host's selected group, and the
        //    rest of the payload is unchanged.
        let identity = hostResolve(resignedGroup, bundleInfo: hostBundleInfo)
        expect(identity != nil, "the host resolves its own selected group")
        expect(identity?.source == .supplied, "an explicitly selected group is the supplied source")
        let payload = launch(resignedGroup, bundleInfo: hostBundleInfo)
        expect(payload["selected"] as? String == "builtinSideStore", "the payload still selects builtinSideStore")
        expect((payload["bookmarks"] as? [Data])?.count == 1, "the payload still carries the bookmark")
        expect(payload["endpoint"] as? String == "listener-endpoint", "the payload still carries the endpoint")
        expect(forwarded(payload) == resignedGroup, "the dedicated launch forwards the selected group")
        // The production entry point the handler actually calls agrees.
        expect(V3SharedAppGroup.runtimeIdentity(selectedGroup: resignedGroup)?.identifier == resignedGroup,
               "the production resolver returns the same identity the payload carries")

        // 2. A SideStore team-suffixed group is forwarded verbatim.
        let otherTeam = "group.com.SideStore.SideStore.OTHERTM"
        entitled.insert(otherTeam)
        expect(forwarded(launch(otherTeam, bundleInfo: hostBundleInfo)) == otherTeam,
               "a different team-suffixed group is forwarded verbatim")
        entitled.remove(otherTeam)

        // 3. A legitimate AltStore-owned group is not rejected for lacking the
        //    SideStore name.
        expect(forwarded(launch(staleAltStoreTeam, bundleInfo: hostBundleInfo)) == staleAltStoreTeam,
               "an AltStore-owned selected group is forwarded")
        expect(hostResolve(staleAltStoreTeam, bundleInfo: hostBundleInfo)?.source == .supplied,
               "an AltStore-owned selection stays the supplied source")

        // 4/5. LiveProcess publishes the inherited group, and the service resolves
        //      it as inherited with the same identifier.
        published = forwarded(payload)
        expect(published == resignedGroup, "LiveProcess publishes exactly what the host forwarded")
        let service = serviceResolve(extensionBundleInfo)
        expect(service != nil, "the service resolves a group from the inherited key alone")
        expect(service?.source == .inherited, "the service reports the inherited source")
        expect(service?.identifier == resignedGroup, "the service resolves the host's group, not its own list")
        expect(service?.identifier == forwarded(payload),
               "what the host forwards is exactly what the service resolves")
        published = nil

        // 6. A missing or unopenable selected group fails closed: no key is
        //    forwarded, so LiveProcess inherits nothing rather than a store of
        //    another process's choosing.
        expect(forwarded(launch(nil, bundleInfo: hostBundleInfo)) == nil,
               "an unresolved selection forwards no group")
        expect(hostResolve("group.com.example.unentitled", bundleInfo: hostBundleInfo) == nil,
               "an unopenable selected group resolves to nothing")
        expect(forwarded(launch("group.com.example.unentitled", bundleInfo: hostBundleInfo)) == nil,
               "an unopenable selected group forwards no group")
        expect(hostResolve("group.com.SideStore.SideStore/../other", bundleInfo: hostBundleInfo) == nil,
               "a path-shaped selection resolves to nothing")

        // 7. The packaged fallback is used only when nothing was published. In the
        //    re-signed extension neither pre-resign name is entitled.
        expect(serviceResolve(extensionBundleInfo) == nil,
               "no published group plus an unentitled packaged list is unavailable")
        entitled.insert(stalePackaged)
        expect(serviceResolve(extensionBundleInfo)?.identifier == stalePackaged,
               "with nothing published the packaged fallback is ranked")
        entitled.remove(stalePackaged)

        // 8. A stale Info.plist list cannot override a valid inherited group.
        published = resignedGroup
        expect(serviceResolve(extensionBundleInfo)?.identifier == resignedGroup,
               "an inherited runtime group outranks a conflicting packaged list")
        expect(serviceResolve(["ALTAppGroups": [resignedGroup]])?.identifier == resignedGroup,
               "an inherited runtime group outranks even a packaged list that would open")
        expect(serviceResolve(extensionBundleInfo)?.source == .inherited,
               "the stale list never becomes the reported source")

        // 9. The re-sign scenario end to end. This is the reported failure: the
        //    main bundle's ALTAppGroups was rewritten to the team group,
        //    LiveProcess is entitled to the team group, LiveProcess's Info.plist
        //    still lists the pre-resign names, and the handoff still resolves the
        //    new group.
        let launchAfterResign = launch(resignedGroup, bundleInfo: hostBundleInfo)
        expect(forwarded(launchAfterResign) == resignedGroup, "the dedicated launch forwards the team group")
        published = forwarded(launchAfterResign)
        let serviceAfterResign = serviceResolve(extensionBundleInfo)
        expect(serviceAfterResign?.identifier == resignedGroup,
               "the service resolves the re-signed group, not the stale packaged one")
        expect(serviceAfterResign?.source == .inherited, "the service attributes the group to the handoff")
        expect(serviceAfterResign?.containerRoot.lastPathComponent.contains(resignedGroup) == true,
               "the service container is the one the team group resolves to")

        // Without the handoff the same scenario is exactly the reported failure.
        published = nil
        expect(serviceResolve(extensionBundleInfo) == nil,
               "without the handoff the service cannot open any store after a re-sign")

        print("V3_EMBEDDED_SIDESTORE_LAUNCH_PASS")
    }
}