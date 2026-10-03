import Foundation

struct ALTTeam {
    let identifier: String
    let name: String
}

struct ALTApplication {
    let bundleIdentifier: String
    let name: String

    func dumpMachOInfo() -> String { "" }
}

struct ALTAppID {
    let bundleIdentifier: String
    let identifier = "test-app-id"
}

struct ALTProvisioningProfile {
    let bundleIdentifier: String
    let name: String
    let uuid: String
    let expirationDate = Date.distantFuture
}

enum OperationError: Error {
    case invalidApp(reason: String)
}

struct MockInstallContext {
    let targetBundleIdentifier: String
    let appendTeamID: Bool
}

final class MockManagedObjectContext {
    let installedApp: InstalledApp?

    init(installedApp: InstalledApp?) {
        self.installedApp = installedApp
    }
}

final class MockPersistentContainer {
    var installedApp: InstalledApp?

    func performBackgroundTask<Value>(
        _ body: (MockManagedObjectContext) -> Value
    ) async -> Value {
        body(MockManagedObjectContext(installedApp: installedApp))
    }
}

final class DatabaseManager {
    static let shared = DatabaseManager()
    let persistentContainer = MockPersistentContainer()
}

final class InstalledApp: NSObject {
    @objc let customBundleIdentifier: String?
    @objc let resignedBundleIdentifier: String
    let team: ALTTeam?
    static var lookupCount = 0

    init(customBundleIdentifier: String?, resignedBundleIdentifier: String, team: ALTTeam?) {
        self.customBundleIdentifier = customBundleIdentifier
        self.resignedBundleIdentifier = resignedBundleIdentifier
        self.team = team
    }

    static func first(satisfying predicate: NSPredicate,
                      in context: MockManagedObjectContext) -> InstalledApp? {
        lookupCount += 1
        guard let installedApp = context.installedApp,
              predicate.evaluate(with: installedApp) else { return nil }
        return installedApp
    }
}

enum BoundarySpy {
    static var operationRoles: [String] = []
    static var preferredParentMatches: [Bool] = []
}

// The production method only uses this wrapper to add per-request diagnostics
// to upstream ServerError values. Preserve the enclosed request exactly.
func lcProvisioningBundleRequest<Value>(role: String, originalBundleID: String,
                                       preferredParentMatch: Bool,
                                       operation: () async throws -> Value) async throws -> Value {
    _ = originalBundleID
    BoundarySpy.operationRoles.append(role)
    BoundarySpy.preferredParentMatches.append(preferredParentMatch)
    return try await operation()
}

final class DeveloperPortalProxy {
    static let shared = DeveloperPortalProxy()
    static let currentDeviceType = "iPhone"
    private(set) var downloadedBundleIDs: [String] = []

    func reset() { downloadedBundleIDs.removeAll() }

    func downloadProvisioningProfile(for appID: ALTAppID, deviceType: String,
                                     team: ALTTeam) async throws -> ALTProvisioningProfile {
        _ = deviceType
        _ = team
        downloadedBundleIDs.append(appID.bundleIdentifier)
        return ALTProvisioningProfile(bundleIdentifier: appID.bundleIdentifier,
                                      name: "profile", uuid: "profile-uuid")
    }
}

final class FetchProvisioningProfilesOperationHarness {
    let context: MockInstallContext
    private(set) var registeredBundleIDs: [String] = []
    private(set) var featureBundleIDs: [String] = []
    private(set) var appGroupBundleIDs: [String] = []

    init(context: MockInstallContext) {
        self.context = context
    }

    // {{GENERATED_PREFERRED_BUNDLE_LOOKUP}}

    func registerAppID(for targetAppBundle: ALTApplication, name: String,
                       bundleIdentifier: String, team: ALTTeam) async throws -> ALTAppID {
        _ = targetAppBundle
        _ = name
        _ = team
        registeredBundleIDs.append(bundleIdentifier)
        return ALTAppID(bundleIdentifier: bundleIdentifier)
    }

    func updateFeatures(for appID: ALTAppID, targetAppBundle: ALTApplication,
                        team: ALTTeam) async throws -> ALTAppID {
        _ = targetAppBundle
        _ = team
        featureBundleIDs.append(appID.bundleIdentifier)
        return appID
    }

    func updateAppGroups(for appID: ALTAppID, targetAppBundle: ALTApplication,
                         team: ALTTeam) async throws -> ALTAppID {
        _ = targetAppBundle
        _ = team
        appGroupBundleIDs.append(appID.bundleIdentifier)
        return appID
    }

    func debugLog(_ message: String) { _ = message }
    func verboseLog(_ message: String) { _ = message }

    // {{PINNED_PROVISION_AND_FETCH_PROFILE}}

    // {{GENERATED_PROVISION_AND_FETCH_PROFILE}}
}

@main
enum ProvisioningExtensionIdentityHarness {
    static let mainBundleID = "com.spotify.client"
    static let teamID = "TEAM42"
    static let extensionSuffixes = [
        "widgetnowplaying",
        "intents",
        "notificationcontent",
        "OpenSpotify.Extension",
        "notification"
    ]

    static func reset(installedApp: InstalledApp?, contextTarget: String,
                      appendTeamID: Bool) -> FetchProvisioningProfilesOperationHarness {
        DatabaseManager.shared.persistentContainer.installedApp = installedApp
        InstalledApp.lookupCount = 0
        DeveloperPortalProxy.shared.reset()
        BoundarySpy.operationRoles = []
        BoundarySpy.preferredParentMatches = []
        return FetchProvisioningProfilesOperationHarness(
            context: MockInstallContext(targetBundleIdentifier: contextTarget,
                                        appendTeamID: appendTeamID))
    }

    static func runGeneratedPipeline(contextTarget: String, appendTeamID: Bool,
                                     installedApp: InstalledApp?, expectedParentID: String,
                                     expectPreferredMatch: Bool) async throws {
        let operation = reset(installedApp: installedApp, contextTarget: contextTarget,
                              appendTeamID: appendTeamID)
        let team = ALTTeam(identifier: teamID, name: "Personal Team")
        let parent = ALTApplication(bundleIdentifier: mainBundleID, name: "Spotify")
        var profiles: [String] = []
        let mainProfile = try await operation.provisionAndFetchProfileGenerated(
            for: parent, parentAppBundle: nil, team: team)
        profiles.append(mainProfile.bundleIdentifier)

        for suffix in extensionSuffixes {
            let child = ALTApplication(bundleIdentifier: mainBundleID + "." + suffix,
                                       name: "Spotify " + suffix)
            let profile = try await operation.provisionAndFetchProfileGenerated(
                for: child, parentAppBundle: parent, team: team)
            profiles.append(profile.bundleIdentifier)
        }

        let expectedBundleIDs = [expectedParentID] + extensionSuffixes.map { expectedParentID + "." + $0 }
        precondition(operation.registeredBundleIDs == expectedBundleIDs,
                     "App IDs must use the main ID and each child's exact suffix")
        precondition(operation.featureBundleIDs == expectedBundleIDs,
                     "feature updates must target the matching app ID for each bundle")
        precondition(operation.appGroupBundleIDs == expectedBundleIDs,
                     "group assignment must target the matching app ID for each bundle")
        precondition(DeveloperPortalProxy.shared.downloadedBundleIDs == expectedBundleIDs,
                     "profile downloads must target the matching app ID for each bundle")
        precondition(profiles == expectedBundleIDs,
                     "returned profiles must correspond to the main and child bundle IDs")
        precondition(BoundarySpy.operationRoles == ["main"] + extensionSuffixes.map { _ in "extension" },
                     "per-call error facts must retain main/extension roles")
        precondition(BoundarySpy.preferredParentMatches == Array(repeating: expectPreferredMatch, count: 6),
                     "per-call facts must describe the actual preferred-parent lookup")
    }

    static func runPinnedRegression(contextTarget: String, installedApp: InstalledApp?,
                                    expectedParentID: String,
                                    expectPreferredParentMatch: Bool) async throws {
        let operation = reset(installedApp: installedApp, contextTarget: contextTarget,
                              appendTeamID: true)
        let team = ALTTeam(identifier: teamID, name: "Personal Team")
        let parent = ALTApplication(bundleIdentifier: mainBundleID, name: "Spotify")
        _ = try await operation.provisionAndFetchProfilePinned(
            for: parent, parentAppBundle: nil, team: team)
        for suffix in extensionSuffixes {
            let child = ALTApplication(bundleIdentifier: mainBundleID + "." + suffix,
                                       name: "Spotify " + suffix)
            _ = try await operation.provisionAndFetchProfilePinned(
                for: child, parentAppBundle: parent, team: team)
        }
        let expectedChildIDs = extensionSuffixes.map { expectedParentID + "." + $0 }
        precondition(operation.registeredBundleIDs.first == expectedParentID,
                     "pinned main App ID behavior should remain the baseline")
        let pinnedChildIDs = Array(operation.registeredBundleIDs.dropFirst())
        if expectPreferredParentMatch {
            precondition(pinnedChildIDs == Array(repeating: expectedParentID,
                                                 count: extensionSuffixes.count),
                         "the test must reproduce the pinned preferred-parent extension-ID defect")
            precondition(pinnedChildIDs != expectedChildIDs,
                         "the pinned implementation should fail the child suffix identity invariant")
        } else {
            precondition(pinnedChildIDs == expectedChildIDs,
                         "without a saved parent match, the pinned fallback already preserves child suffixes")
        }
    }

    static func runInvalidChildGuard(contextTarget: String) async throws {
        let operation = reset(installedApp: nil, contextTarget: contextTarget, appendTeamID: true)
        let team = ALTTeam(identifier: teamID, name: "Personal Team")
        let parent = ALTApplication(bundleIdentifier: mainBundleID, name: "Spotify")
        let invalidChild = ALTApplication(bundleIdentifier: "com.other.extension", name: "Invalid")
        do {
            _ = try await operation.provisionAndFetchProfileGenerated(
                for: invalidChild, parentAppBundle: parent, team: team)
            fatalError("an extension outside the parent namespace must be rejected")
        } catch OperationError.invalidApp {
            precondition(operation.registeredBundleIDs.isEmpty &&
                         operation.featureBundleIDs.isEmpty &&
                         operation.appGroupBundleIDs.isEmpty &&
                         DeveloperPortalProxy.shared.downloadedBundleIDs.isEmpty,
                         "invalid child bundle ID must fail before any provisioning API boundary")
        }
    }

    static func main() async throws {
        let personalTeam = ALTTeam(identifier: teamID, name: "Personal Team")

        // No Core Data match: appending the team ID preserves every real Spotify
        // child suffix; appendTeamID=false preserves the supplied custom parent.
        try await runGeneratedPipeline(contextTarget: mainBundleID, appendTeamID: true,
            installedApp: nil, expectedParentID: mainBundleID + "." + teamID,
            expectPreferredMatch: false)
        try await runPinnedRegression(contextTarget: mainBundleID,
            installedApp: InstalledApp(customBundleIdentifier: nil,
                resignedBundleIdentifier: "com.other.app." + teamID, team: personalTeam),
            expectedParentID: mainBundleID + "." + teamID,
            expectPreferredParentMatch: false)
        // A normal previously managed app is not enough: its resigned ID does
        // not match the unsuffixed input in the actual lookup predicate.
        let normalSavedApp = InstalledApp(customBundleIdentifier: nil,
            resignedBundleIdentifier: mainBundleID + "." + teamID, team: personalTeam)
        try await runGeneratedPipeline(contextTarget: mainBundleID, appendTeamID: true,
            installedApp: normalSavedApp, expectedParentID: mainBundleID + "." + teamID,
            expectPreferredMatch: false)
        try await runPinnedRegression(contextTarget: mainBundleID,
            installedApp: normalSavedApp, expectedParentID: mainBundleID + "." + teamID,
            expectPreferredParentMatch: false)

        try await runGeneratedPipeline(contextTarget: "com.example.customspotify", appendTeamID: false,
            installedApp: nil, expectedParentID: "com.example.customspotify",
            expectPreferredMatch: false)
        try await runPinnedRegression(contextTarget: "com.example.customspotify",
            installedApp: InstalledApp(customBundleIdentifier: nil,
                resignedBundleIdentifier: "com.other.app." + teamID, team: personalTeam),
            expectedParentID: "com.example.customspotify",
            expectPreferredParentMatch: false)

        // A saved same-team custom parent matches the exact InstalledApp query.
        // The generated method keeps that parent ID and appends each IPA suffix.
        let savedCustomParent = "com.example.customspotify"
        let preferredParent = savedCustomParent + "." + teamID
        let savedApp = InstalledApp(customBundleIdentifier: savedCustomParent,
            resignedBundleIdentifier: preferredParent, team: personalTeam)
        try await runGeneratedPipeline(contextTarget: savedCustomParent, appendTeamID: true,
            installedApp: savedApp, expectedParentID: preferredParent,
            expectPreferredMatch: true)
        try await runGeneratedPipeline(contextTarget: savedCustomParent, appendTeamID: false,
            installedApp: savedApp, expectedParentID: preferredParent,
            expectPreferredMatch: true)
        try await runPinnedRegression(contextTarget: savedCustomParent,
            installedApp: savedApp, expectedParentID: preferredParent,
            expectPreferredParentMatch: true)

        try await runInvalidChildGuard(contextTarget: mainBundleID)
        print("V3_PROVISIONING_EXTENSION_IDENTITY_PASS")
    }
}
