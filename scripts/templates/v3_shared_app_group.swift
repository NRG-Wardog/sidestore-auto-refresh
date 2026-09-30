import Foundation
#if canImport(Darwin)
import Darwin
#endif

/// The one runtime App Group identity used by every cross-process reader,
/// writer and lock in the combined build: IPA staging, the secret handoff
/// Keychain transaction lock, the service Keychain migration lock, the
/// operation recovery journal, and the cross-process refresh store.
///
/// LiveProcess publishes the group it validated from the host launch payload
/// and the host publishes the same key for itself, so the host and the service
/// resolve one identifier without sharing a symbol across targets. A packaged
/// Info.plist entitlement is only the fallback for a launch that published
/// nothing at all.
///
/// LC_APP_GROUP_RULE_SET_V1 in scripts/templates/LCAppGroupIdentityRules.h is
/// the same rule set in plain C, so it can be executed by a behavioral harness
/// on any toolchain. The two implementations cannot share a header across
/// targets; tests/test_v3_shared_app_group.py executes the C rules and fails
/// when this file drifts from them. Change both together.
enum V3SharedAppGroup {
    /// LC_RULE_PACKAGED_FALLBACK_ONLY: the packaged SideStore group, including
    /// the team-suffixed variants a re-signer writes, ranks first when no
    /// runtime group was published.
    static let packagedGroup = "group.com.SideStore.SideStore"
    /// LiveProcess validates the host-selected group against its own sandbox
    /// and publishes it here. The host publishes the same key for itself.
    static let runtimeGroupEnvironmentKey = "LC_V3_INHERITED_APP_GROUP"
    /// LC_RULE_GROUP_BOUNDED_LENGTH
    static let maximumIdentifierLength = 255

    enum Source: String, Equatable {
        /// The caller passed its own selected group.
        case supplied
        /// The group published by the host for this process.
        case inherited
        /// No runtime group was published; a packaged entitlement was used.
        case packaged
    }

    struct Identity: Equatable {
        let identifier: String
        let containerRoot: URL
        let source: Source
    }

    /// A typed, recoverable failure. Shared state is never substituted with a
    /// process-local store to hide this. The description is a defined safe
    /// sentence, never a provider string and never a private path.
    enum Unavailable: Error, Equatable, LocalizedError {
        case sharedStore

        var isRecoverable: Bool { true }
        var errorDescription: String? {
            "LiveContainer could not open the shared store it uses with the embedded SideStore service."
        }
    }

    /// LC_RULE_GROUP_VISIBLE_ASCII, LC_RULE_GROUP_NO_SEPARATOR,
    /// LC_RULE_GROUP_NO_COLON, LC_RULE_GROUP_NO_TRAVERSAL,
    /// LC_RULE_GROUP_BOUNDED_LENGTH. An App Group identifier is never a path
    /// and never an unbounded string, whatever produced it.
    static func wellFormedIdentifier(_ candidate: String?) -> String? {
        guard let candidate, !candidate.isEmpty,
              candidate.utf8.count <= maximumIdentifierLength else { return nil }
        var previous: UInt8 = 0
        for byte in candidate.utf8 {
            guard byte >= 0x21, byte <= 0x7E,
                  byte != UInt8(ascii: "/"), byte != UInt8(ascii: "\\"),
                  byte != UInt8(ascii: ":") else { return nil }
            if previous == UInt8(ascii: ".") && byte == UInt8(ascii: ".") { return nil }
            previous = byte
        }
        return candidate.utf8.first == UInt8(ascii: ".") ? nil : candidate
    }

    static func isPackagedSideStoreGroup(_ group: String) -> Bool {
        guard group == packagedGroup || group.hasPrefix(packagedGroup + ".") else { return false }
        let suffix = group.dropFirst(packagedGroup.count + 1)
        return !suffix.isEmpty && suffix.utf8.allSatisfy {
            (UInt8(ascii: "0")...UInt8(ascii: "9")).contains($0) ||
                (UInt8(ascii: "A")...UInt8(ascii: "Z")).contains($0) ||
                (UInt8(ascii: "a")...UInt8(ascii: "z")).contains($0)
        }
    }

    static func environmentGroup() -> String? {
        #if canImport(Darwin)
        guard let value = getenv(runtimeGroupEnvironmentKey) else { return nil }
        return String(cString: value)
        #else
        return nil
        #endif
    }

    /// Publish this process's own selection so the embedded service resolves the
    /// identical group. Only a group this process can actually open is published:
    /// if the service could not open what the host published, it would clear the
    /// key and choose its own packaged fallback, which is the split this exists to
    /// prevent. Publishing nothing leaves both processes on their packaged
    /// fallback, which LCAppGroupOrderPackaged ranks identically and which the
    /// packaging verifier constrains to groups both processes are entitled for.
    static func publishRuntimeGroup(_ group: String?) {
        #if canImport(Darwin)
        unsetenv(runtimeGroupEnvironmentKey)
        guard let resolved = runtimeIdentity(selectedGroup: group)?.identifier,
              let bytes = resolved.cString(using: .utf8) else { return }
        setenv(runtimeGroupEnvironmentKey, bytes, 1)
        #endif
    }

    /// Resolve the one authoritative identity.
    ///
    /// LC_RULE_EXPLICIT_WINS: a supplied or inherited runtime group is
    /// authoritative for its own name. A legitimate AltStore-owned group that
    /// LiveContainer selected is accepted; it is not rejected for lacking the
    /// SideStore name.
    /// LC_RULE_EXPLICIT_FAIL_CLOSED: if that group is malformed or cannot be
    /// opened, this returns nil. Falling back to the packaged entitlement would
    /// move the shared store underneath the other process.
    /// LC_RULE_PACKAGED_FALLBACK_ONLY: with no runtime group at all, the
    /// packaged entitlement is the only fallback.
    static func identity(selectedGroup: String? = nil,
                         inheritedGroup: String? = nil,
                         usesEnvironment: Bool = true,
                         bundleInfo: [String: Any],
                         resolveContainer: (String) -> URL?) -> Identity? {
        let supplied = selectedGroup.flatMap { $0.isEmpty ? nil : $0 }
        let inherited = inheritedGroup ?? (usesEnvironment ? environmentGroup() : nil)
        if let authoritative = supplied ?? inherited {
            guard let identifier = wellFormedIdentifier(authoritative),
                  let containerRoot = resolveContainer(identifier) else { return nil }
            return Identity(identifier: identifier, containerRoot: containerRoot,
                            source: supplied != nil ? .supplied : .inherited)
        }
        let configured = (bundleInfo["ALTAppGroups"] as? [String]) ??
            (bundleInfo["ALTAppGroups"] as? String).map { [$0] } ?? []
        let wellFormed = configured.compactMap(wellFormedIdentifier)
        // LC_RULE_PACKAGED_FALLBACK_ONLY, in the same two steps the C rule set
        // uses: the rule set's order, then the first entry this process can
        // actually open. A packaged list is a preference, not proof of
        // entitlement, so an unopenable top entry falls through to the next one.
        let ordered = wellFormed.filter(isPackagedSideStoreGroup) +
            wellFormed.filter { !isPackagedSideStoreGroup($0) }
        for identifier in ordered {
            if let containerRoot = resolveContainer(identifier) {
                return Identity(identifier: identifier, containerRoot: containerRoot, source: .packaged)
            }
        }
        return nil
    }

    static func runtimeIdentity(selectedGroup: String? = nil, bundle: Bundle = .main,
                                fileManager: FileManager = .default) -> Identity? {
        identity(selectedGroup: selectedGroup, bundleInfo: bundle.infoDictionary ?? [:]) {
            fileManager.containerURL(forSecurityApplicationGroupIdentifier: $0)
        }
    }

    /// The cross-process UserDefaults suite for this runtime group. Returns nil
    /// rather than a process-local store: a caller that needs shared state must
    /// produce a typed recoverable failure rather than silently writing to a
    /// private store the other process cannot read.
    static func sharedUserDefaults(selectedGroup: String? = nil, bundle: Bundle = .main) -> UserDefaults? {
        guard let identity = runtimeIdentity(selectedGroup: selectedGroup, bundle: bundle) else { return nil }
        return UserDefaults(suiteName: identity.identifier)
    }

    static func requireSharedUserDefaults(selectedGroup: String? = nil,
                                          bundle: Bundle = .main) throws -> UserDefaults {
        guard let shared = sharedUserDefaults(selectedGroup: selectedGroup, bundle: bundle) else {
            throw Unavailable.sharedStore
        }
        return shared
    }

    /// A private store used only when no shared store exists, so a failing launch
    /// can still render without its process-local values being mistaken for the
    /// cross-process state they stand in for. A unique suite name cannot collide
    /// with a real store and no other process can open it, so it is never an App
    /// Group suite. Callers must still refuse to run cross-process work while the
    /// shared store is unavailable, which is what `requireSharedStore` and
    /// `requireSharedUserDefaults` are for.
    ///
    /// The trailing `.standard` is an absolute last resort for the case where even
    /// a unique suite cannot be created. It is not a supported state and nothing
    /// treats it as a shared store.
    static func quarantinedUserDefaults() -> UserDefaults {
        let unique = "com.kdt.livecontainer.v3.quarantined-shared-store.\(UUID().uuidString)"
        return UserDefaults(suiteName: unique) ?? UserDefaults.standard
    }
}

/// The one cross-process refresh store: the host scheduler, the refresh settings
/// screen, the host Home banner and the Setup assistant all read and write these
/// keys, and the embedded service and the background run read and write the same
/// ones. It is deliberately not MainActor-isolated so SwiftUI property wrappers
/// can bind to it during view construction.
enum V3SharedRefreshStore {
    static let isAvailable = V3SharedAppGroup.sharedUserDefaults() != nil
    static let defaults: UserDefaults = V3SharedAppGroup.sharedUserDefaults()
        ?? V3SharedAppGroup.quarantinedUserDefaults()
    static let unavailableMessage = "LiveContainer could not open its shared refresh store, so scheduled refresh state is unavailable in this launch. Refresh All still works."
}
