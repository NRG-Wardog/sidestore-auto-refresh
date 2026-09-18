import Foundation
import CoreFoundation

// V3_WIRE_CONTRACT_V1: shared source, compiled independently in each process.
enum V3WireContract {
    static let requestLimit = 16_384
    static let responseLimit = 4_194_304
    static let operations: Set<String> = ["snapshot", "catalog", "appIcon", "cancel", "refreshSources", "addSource",
        "removeSource", "beginSignIn", "signInState", "signInRespond", "cancelSignIn", "operationState", "operationRespond", "cancelOperation", "settingsPanelSnapshot", "settingsPanelCommand", "developerServicesSnapshot", "developerServicesCommand", "backupAccountExport", "backupAccountImportSharedFile", "signOut", "syncAppIDs", "clearCache", "setSetting", "install",
        "update", "activate", "deactivate", "remove", "delete", "backup", "restore", "jit", "refreshApp", "backupResult", "installURL", "installSharedIPA",
        "certificatesSnapshot", "activateLocalCertificate", "deleteLocalCertificate", "importPairingSharedFile"]

    static func decodeRequest(_ data: Data, now: Date = Date()) -> [String: Any]? {
        guard data.count <= requestLimit,
              let request = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              Set(request.keys).isSubset(of: ["version", "id", "operation", "target", "value", "deadline", "cursor", "payload"]),
              let version = request["version"] as? NSNumber, CFGetTypeID(version) != CFBooleanGetTypeID(),
              request["version"] as? Int == 1,
              let id = request["id"] as? String, UUID(uuidString: id) != nil,
              let operation = request["operation"] as? String, operations.contains(operation),
              let target = request["target"] as? String, target.utf8.count <= 4096,
              let deadline = request["deadline"] as? Date,
              deadline > now, deadline.timeIntervalSince(now) <= 610 else { return nil }
        if let value = request["value"] {
            guard operation == "setSetting", let number = value as? NSNumber,
                  CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        } else if operation == "setSetting" { return nil }
        if let cursor = request["cursor"] {
            guard operation == "catalog", let number = cursor as? NSNumber,
                  CFGetTypeID(number) != CFBooleanGetTypeID(),
                  let value = cursor as? Int, value >= 0, value <= 1_000_000 else { return nil }
        }
        if let payload = request["payload"] {
            guard ["signInRespond", "operationRespond", "settingsPanelCommand", "developerServicesCommand", "backupAccountExport", "backupAccountImportSharedFile"].contains(operation), let data = payload as? Data, data.count <= 32_768 else { return nil }
        } else if ["signInRespond", "operationRespond", "settingsPanelCommand", "developerServicesCommand", "backupAccountExport", "backupAccountImportSharedFile"].contains(operation) {
            return nil
        }
        return request
    }
}
