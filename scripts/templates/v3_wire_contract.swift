import Foundation
import CoreFoundation

// V3_WIRE_CONTRACT_V1: shared source, compiled independently in each process.
// V3_HEADLESS_CONTRACT_V2: SideStore is a headless backend. All presentation
// decisions cross as data (prompts/confirmations); no remote UI is addressed.
enum V3WireContract {
    static let requestLimit = 16_384
    static let responseLimit = 4_194_304
    static let operations: Set<String> = ["snapshot", "catalog", "appIcon", "cancel", "refreshSources",
        "signOut", "syncAppIDs", "clearCache", "jit", "backupResult",
        "authBegin", "authPoll", "authRespond", "authCancel",
        "opStart", "opPoll", "opAnswer", "opCancel",
        "certList", "certSetActive", "certDelete", "certPortalList", "certRevoke", "certCreate",
        "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles",
        "sourcePreview", "sourceAddConfirmed", "sourceRemoveConfirmed",
        "pairingImportData", "settingsGet", "settingsSet",
        "anisetteList", "anisetteReset", "anisetteSync",
        "sidesignGet", "sidesignSet", "sidesignReset", "sidesignImport", "sidesignExport",
        "logTail", "healthSnapshot", "accountExport", "accountImport"]
    static let readOperations: Set<String> = ["snapshot", "catalog", "appIcon",
        "authPoll", "opPoll", "certList", "certPortalList",
        "devTeams", "devDevices", "devAppIDs", "devGroups", "devProfiles",
        "sourcePreview", "settingsGet",
        "anisetteList", "sidesignGet", "sidesignExport", "logTail", "healthSnapshot"]

    static func decodeRequest(_ data: Data, now: Date = Date()) -> [String: Any]? {
        guard data.count <= requestLimit,
              let request = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              Set(request.keys).isSubset(of: ["version", "id", "operation", "target", "deadline", "cursor", "payload"]),
              let version = request["version"] as? NSNumber, CFGetTypeID(version) != CFBooleanGetTypeID(),
              request["version"] as? Int == 1,
              let id = request["id"] as? String, UUID(uuidString: id) != nil,
              let operation = request["operation"] as? String, operations.contains(operation),
              let target = request["target"] as? String, target.utf8.count <= 4096,
              let deadline = request["deadline"] as? Date,
              deadline > now, deadline.timeIntervalSince(now) <= 610 else { return nil }
        if request["value"] != nil { return nil }
        if let cursor = request["cursor"] {
            guard operation == "catalog", let number = cursor as? NSNumber,
                  CFGetTypeID(number) != CFBooleanGetTypeID(),
                  let value = cursor as? Int, value >= 0, value <= 1_000_000 else { return nil }
        }
        if let payload = request["payload"] {
            guard payload as? [String: Any] != nil else { return nil }
        }
        return request
    }
}
