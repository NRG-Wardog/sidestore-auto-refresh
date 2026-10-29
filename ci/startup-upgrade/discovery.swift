import Foundation
import CoreData
import Darwin

func modelResourceCount(_ bundle: Bundle) throws -> Int {
    var enumerationFailed = false
    guard let root = bundle.resourceURL,
          let entries = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil,
              options: [], errorHandler: { _, _ in enumerationFailed = true; return false }) else {
        throw NSError(domain: "SchemaDiscovery", code: 3)
    }
    var count = 0
    for case let url as URL in entries {
        if ["momd", "mom", "omo", "xcdatamodeld", "xcdatamodel"].contains(url.pathExtension) { count += 1 }
    }
    if enumerationFailed { throw NSError(domain: "SchemaDiscovery", code: 4) }
    return count
}

func run() throws {
    let args = CommandLine.arguments
    guard args.count == 6 else { fatalError("expected framework, executable, metadata store, isolated host, unknown store") }
    let framework = URL(fileURLWithPath: args[1]).resolvingSymlinksInPath()
    let executable = URL(fileURLWithPath: args[2])
    let isolatedHost = URL(fileURLWithPath: args[4]).resolvingSymlinksInPath()
    let metadata = try NSPersistentStoreCoordinator.metadataForPersistentStore(ofType: NSSQLiteStoreType,
        at: URL(fileURLWithPath: args[3]), options: [NSReadOnlyPersistentStoreOption: true])
    let unknownMetadata = try NSPersistentStoreCoordinator.metadataForPersistentStore(ofType: NSSQLiteStoreType,
        at: URL(fileURLWithPath: args[5]), options: [NSReadOnlyPersistentStoreOption: true])
    let mainResourceCountBefore = try modelResourceCount(Bundle.main)
    // This negative executes before dlopen and before creating the supplied Bundle.
    // Compiled fixture models are outside this executable's real .app bundle.
    let beforeFramework = NSManagedObjectModel.mergedModel(from: Bundle.allBundles, forStoreMetadata: metadata)
    guard let handle = dlopen(executable.path, RTLD_NOW | RTLD_LOCAL) else {
        throw NSError(domain: "SchemaDiscovery", code: 1)
    }
    // Intentionally do not dlclose while Objective-C classes remain registered.
    _ = handle
    guard let anchor = NSClassFromString("SchemaBundleAnchor") else {
        throw NSError(domain: "SchemaDiscovery", code: 2)
    }
    let owner = Bundle(for: anchor)
    let baseline = NSManagedObjectModel.mergedModel(from: Bundle.allBundles, forStoreMetadata: metadata)
    let explicit = NSManagedObjectModel.mergedModel(from: [owner], forStoreMetadata: metadata)
    let supplied = Bundle(url: framework)
    let suppliedModel = supplied.flatMap { NSManagedObjectModel.mergedModel(from: [$0], forStoreMetadata: metadata) }
    let unknownModel = NSManagedObjectModel.mergedModel(from: [owner], forStoreMetadata: unknownMetadata)
    func same(_ bundle: Bundle) -> Bool { bundle.bundleURL.resolvingSymlinksInPath() == framework }
    let report: [String: Any] = [
        "schema": "coredata-owned-framework-discovery-v2",
        "main_bundle_is_isolated_host": Bundle.main.bundleURL.resolvingSymlinksInPath() == isolatedHost,
        "main_bundle_model_resources_before": mainResourceCountBefore,
        "main_bundle_model_resources_after": try modelResourceCount(Bundle.main),
        "source_model_found_before_framework_load": beforeFramework != nil,
        "unknown_source_model_found_in_explicit_owner": unknownModel != nil,
        "owner_is_requested_framework": same(owner),
        "owner_package_type": owner.object(forInfoDictionaryKey: "CFBundlePackageType") as? String ?? "missing",
        "main_is_requested_framework": same(Bundle.main),
        "all_bundles_contains_owner": Bundle.allBundles.contains(where: same),
        "all_frameworks_contains_owner": Bundle.allFrameworks.contains(where: same),
        "baseline_source_model_found": baseline != nil,
        "explicit_owner_source_model_found": explicit != nil,
        "explicit_supplied_source_model_found": suppliedModel != nil,
        "explicit_supplied_model_compatible": suppliedModel?.isConfiguration(withName: nil, compatibleWithStoreMetadata: metadata) ?? false,
        "baseline_model_compatible": baseline?.isConfiguration(withName: nil, compatibleWithStoreMetadata: metadata) ?? false,
        "explicit_owner_model_compatible": explicit?.isConfiguration(withName: nil, compatibleWithStoreMetadata: metadata) ?? false,
        "physical_lc_bootstrap_executed": false,
        "limitation": "Isolated macOS host and source-owned loaded framework, not LC private guest bootstrap or an installed device store"
    ]
    let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys, .prettyPrinted])
    FileHandle.standardOutput.write(data); print("")
}
do { try run() } catch {
    FileHandle.standardError.write(Data("DISCOVERY_FIXTURE_FAILED \((error as NSError).code)\n".utf8)); exit(1)
}
