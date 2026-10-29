import Foundation
import CoreData
import ObjectiveC
import Darwin

func require(_ value: @autoclosure () -> Bool, _ message: String, code: Int = 1) throws {
    if !value() { throw NSError(domain: "SchemaQualification", code: code, userInfo: [NSLocalizedDescriptionKey: message]) }
}

// These generic classes intentionally do not execute application/keychain logic.
func registerFixtureClasses(_ model: NSManagedObjectModel) {
    for entity in model.entities {
        guard let name = entity.managedObjectClassName, NSClassFromString(name) == nil else { continue }
        if let type = objc_allocateClassPair(NSManagedObject.self, name, 0) { objc_registerClassPair(type) }
    }
}

final class MappingProbe {
    let managedObjectModel: NSManagedObjectModel
    init(_ model: NSManagedObjectModel) { managedObjectModel = model }
    func select(source: NSManagedObjectModel) -> (NSMigrationManager, NSMappingModel)? {
        var mapping: NSMappingModel?
        guard let manager = progressiveMigrationManager(forSourceModel: source, destinationModel: managedObjectModel,
                                                        configuration: nil, mappingModel: &mapping), let mapping else { return nil }
        return (manager, mapping)
    }
    // EXACT_PRODUCTION_MAPPING_METHODS
}

let testedEntities = ["Account", "Team", "InstalledApp", "InstalledExtension"]
func loadModel(_ directory: URL, _ version: String) throws -> NSManagedObjectModel {
    guard let model = NSManagedObjectModel(contentsOf: directory.appendingPathComponent("AltStore \(version).mom")) else {
        throw NSError(domain: "SchemaQualification", code: 2, userInfo: [NSLocalizedDescriptionKey: "Compiled original model missing"])
    }
    registerFixtureClasses(model)
    return model
}

func makeStore(_ model: NSManagedObjectModel, _ url: URL, readOnly: Bool, wal: Bool = false) throws -> (NSPersistentStoreCoordinator, NSPersistentStore, NSManagedObjectContext) {
    let coordinator = NSPersistentStoreCoordinator(managedObjectModel: model)
    var options: [AnyHashable: Any] = [NSMigratePersistentStoresAutomaticallyOption: false,
                                     NSInferMappingModelAutomaticallyOption: false,
                                     NSReadOnlyPersistentStoreOption: readOnly]
    if !readOnly { options[NSSQLitePragmasOption] = ["journal_mode": wal ? "WAL" : "DELETE", "wal_autocheckpoint": "0"] }
    let store = try coordinator.addPersistentStore(ofType: NSSQLiteStoreType, configurationName: nil, at: url, options: options)
    let context = NSManagedObjectContext(concurrencyType: .mainQueueConcurrencyType)
    context.persistentStoreCoordinator = coordinator
    return (coordinator, store, context)
}

func fillRequired(_ object: NSManagedObject) throws {
    for (name, attribute) in object.entity.attributesByName where !attribute.isOptional && !attribute.isTransient {
        if object.value(forKey: name) != nil { continue }
        let value: Any
        switch attribute.attributeType {
        case .stringAttributeType: value = "fixture-\(object.entity.name!)-\(name)"
        case .dateAttributeType: value = Date(timeIntervalSince1970: 1_700_000_000)
        case .booleanAttributeType: value = false
        case .integer16AttributeType, .integer32AttributeType, .integer64AttributeType: value = 1
        case .doubleAttributeType, .floatAttributeType: value = 1.25
        default: throw NSError(domain: "SchemaQualification", code: 3, userInfo: [NSLocalizedDescriptionKey: "Unexpected required fixture attribute"])
        }
        object.setValue(value, forKey: name)
    }
}

func seed(_ model: NSManagedObjectModel, _ path: URL, wal: Bool) throws {
    let (coordinator, store, context) = try makeStore(model, path, readOnly: false, wal: wal)
    var objects: [String: NSManagedObject] = [:]
    for name in testedEntities {
        let object = NSEntityDescription.insertNewObject(forEntityName: name, into: context)
        try fillRequired(object); objects[name] = object
    }
    objects["Account"]!.setValue(true, forKey: "isActiveAccount")
    objects["Team"]!.setValue(objects["Account"]!, forKey: "account")
    objects["InstalledApp"]!.setValue(objects["Team"]!, forKey: "team")
    objects["InstalledExtension"]!.setValue(objects["InstalledApp"]!, forKey: "parentApp")
    try context.save()
    objects["Account"]!.setValue("latest-committed-fixture-name", forKey: "firstName")
    try context.save()
    if wal {
        FileHandle.standardOutput.write(Data("WAL_WRITER_COMMITTED\n".utf8))
        _exit(0) // Deliberately leave committed WAL, with no live writer afterwards.
    }
    try coordinator.remove(store)
}

func identifier(_ object: NSManagedObject) -> String {
    let key = object.entity.attributesByName["identifier"] != nil ? "identifier" : "bundleIdentifier"
    return object.entity.name! + ":" + String(describing: object.value(forKey: key)!)
}
func scalar(_ value: Any?) throws -> Any {
    guard let value else { return NSNull() }
    if let date = value as? Date { return ["date": date.timeIntervalSince1970] }
    if let number = value as? NSNumber { return number }
    if let string = value as? String { return string }
    throw NSError(domain: "SchemaQualification", code: 4, userInfo: [NSLocalizedDescriptionKey: "Unsupported snapshot value"])
}
func snapshot(_ model: NSManagedObjectModel, _ path: URL, includeFingerprint: Bool = false) throws -> Data {
    let (coordinator, store, context) = try makeStore(model, path, readOnly: true)
    defer { try? coordinator.remove(store) }
    var rows: [[String: Any]] = []
    for name in testedEntities {
        let objects = try context.fetch(NSFetchRequest<NSManagedObject>(entityName: name))
        try require(objects.count == 1, "Fixture entity count changed")
        for object in objects {
            var row: [String: Any] = ["entity": name, "id": identifier(object)]
            for (key, attr) in object.entity.attributesByName where !attr.isTransient {
                if key == "appBundleFingerprint" {
                    try require(object.value(forKey: key) == nil, "Added optional attribute not nil")
                    if !includeFingerprint { continue }
                }
                row[key] = try scalar(object.value(forKey: key))
            }
            for (key, relation) in object.entity.relationshipsByName {
                if relation.isToMany {
                    let values = (object.value(forKey: key) as? NSSet)?.allObjects as? [NSManagedObject] ?? []
                    row["relationship:" + key] = values.map(identifier).sorted()
                } else {
                    row["relationship:" + key] = (object.value(forKey: key) as? NSManagedObject).map(identifier) ?? "nil"
                }
            }
            rows.append(row)
        }
    }
    try require(rows.first(where: { ($0["entity"] as? String) == "Account" })?["firstName"] as? String == "latest-committed-fixture-name", "Latest committed WAL record lost")
    return try JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
}

func migrate(_ sourceModel: NSManagedObjectModel, _ destinationModel: NSManagedObjectModel, source: URL, destination: URL) throws {
    for suffix in ["", "-wal", "-shm"] {
        try require(!FileManager.default.fileExists(atPath: destination.path + suffix), "Destination store/sidecar conflict", code: 11)
    }
    let options: [AnyHashable: Any] = [NSReadOnlyPersistentStoreOption: true]
    let metadata = try NSPersistentStoreCoordinator.metadataForPersistentStore(ofType: NSSQLiteStoreType, at: source, options: options)
    try require(sourceModel.isConfiguration(withName: nil, compatibleWithStoreMetadata: metadata), "Unrecognized source model", code: 12)
    let before = try snapshot(sourceModel, source)
    guard let (manager, mapping) = MappingProbe(destinationModel).select(source: sourceModel) else {
        throw NSError(domain: "SchemaQualification", code: 5, userInfo: [NSLocalizedDescriptionKey: "No mapping"])
    }
    print("SOURCE_COMPATIBLE_MAPPING_SELECTED_MIGRATION_STARTED")
    try manager.migrateStore(from: source, sourceType: NSSQLiteStoreType, options: options, with: mapping,
                             toDestinationURL: destination, destinationType: NSSQLiteStoreType,
                             destinationOptions: [NSSQLitePragmasOption: ["journal_mode": "DELETE"]])
    let afterMetadata = try NSPersistentStoreCoordinator.metadataForPersistentStore(ofType: NSSQLiteStoreType, at: destination, options: options)
    try require(destinationModel.isConfiguration(withName: nil, compatibleWithStoreMetadata: afterMetadata), "Target metadata mismatch")
    let after = try snapshot(destinationModel, destination)
    try require(before == after, "Attributes or relationships changed")
    print("REAL_COREDATA_MIGRATION_PASS")
}

func run() throws {
    let arguments = CommandLine.arguments
    try require(arguments.count >= 4, "Expected mode, models, source arguments")
    let mode = arguments[1], models = URL(fileURLWithPath: arguments[2]), source = URL(fileURLWithPath: arguments[3])
    let old = try loadModel(models, "17_6"), new = try loadModel(models, "17_7")
    switch mode {
    case "seed-delete": try seed(old, source, wal: false)
    case "seed-wal": try seed(old, source, wal: true)
    case "seed-unknown":
        old.entitiesByName["Account"]!.versionHashModifier = "unknown-fixture-schema"
        try seed(old, source, wal: false)
    case "migrate":
        try require(arguments.count == 5, "Expected destination")
        try migrate(old, new, source: source, destination: URL(fileURLWithPath: arguments[4]))
    case "mapping-negative":
        let attribute = NSAttributeDescription(); attribute.name = "requiredWithoutDefault"
        attribute.attributeType = .stringAttributeType; attribute.isOptional = false
        let entity = new.entitiesByName["InstalledApp"]!; entity.properties = entity.properties + [attribute]
        try require(arguments.count == 5, "Expected isolated negative destination")
        if let (manager, mapping) = MappingProbe(new).select(source: old) {
            var rejected = false
            do {
                try manager.migrateStore(from: source, sourceType: NSSQLiteStoreType,
                    options: [NSReadOnlyPersistentStoreOption: true], with: mapping,
                    toDestinationURL: URL(fileURLWithPath: arguments[4]), destinationType: NSSQLiteStoreType,
                    destinationOptions: [NSSQLitePragmasOption: ["journal_mode": "DELETE"]])
            } catch { rejected = true }
            try require(rejected, "Required no-default attribute unexpectedly migrated")
            print("REQUIRED_ATTRIBUTE_MIGRATION_REJECTED")
        } else {
            print("UNSUPPORTED_MAPPING_REJECTED")
        }
    default: throw NSError(domain: "SchemaQualification", code: 6, userInfo: [NSLocalizedDescriptionKey: "Unknown mode"])
    }
}
do { try run() } catch {
    // Fixtures contain synthetic records only. Never log arbitrary user store errors.
    FileHandle.standardError.write(Data("SCHEMA_QUALIFICATION_REJECTED \((error as NSError).domain) \((error as NSError).code)\n".utf8))
    exit(1)
}
