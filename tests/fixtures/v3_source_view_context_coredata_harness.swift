import CoreData
import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError(message) }
}

extension NSManagedObjectContext {
    func performAsync<T>(_ work: @escaping () -> T) async -> T {
        await withCheckedContinuation { continuation in
            perform { continuation.resume(returning: work()) }
        }
    }

    func performAsync<T>(_ work: @escaping () throws -> T) async throws -> T {
        try await withCheckedThrowingContinuation { continuation in
            perform {
                do { continuation.resume(returning: try work()) }
                catch { continuation.resume(throwing: error) }
            }
        }
    }
}

@dynamicMemberLookup
struct AsyncManaged<Value> {
    let wrappedValue: Value
    private let managedObjectContext: NSManagedObjectContext?

    init(wrappedValue: Value) {
        self.wrappedValue = wrappedValue
        managedObjectContext = (wrappedValue as? NSManagedObject)?.managedObjectContext
    }

    func perform<Result>(_ work: @escaping (Value) -> Result) async -> Result {
        guard let managedObjectContext else { return work(wrappedValue) }
        return await managedObjectContext.performAsync { work(wrappedValue) }
    }

    subscript<Member>(dynamicMember keyPath: KeyPath<Value, Member>) -> Member {
        get async { await perform { $0[keyPath: keyPath] } }
    }
}

@objc(Source)
final class Source: NSManagedObject {
    @NSManaged var identifier: String
    static let altStoreIdentifier = "default-source"

    static func first(satisfying predicate: NSPredicate, in context: NSManagedObjectContext) -> Source? {
        let request = NSFetchRequest<Source>(entityName: "Source")
        request.fetchLimit = 1
        request.predicate = predicate
        return try? context.fetch(request).first
    }

    func isAdded() async throws -> Bool {
        let identifier = await AsyncManaged(wrappedValue: self).identifier
        let context = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        return try await context.performAsync {
            let request = NSFetchRequest<NSFetchRequestResult>(entityName: "Source")
            request.predicate = NSPredicate(format: "%K == %@", #keyPath(Source.identifier), identifier)
            return try context.count(for: request) > 0
        }
    }
}

final class DatabaseManager {
    static let shared = DatabaseManager()
    let persistentContainer: NSPersistentContainer
    var viewContext: NSManagedObjectContext { persistentContainer.viewContext }

    private init() {
        let identifier = NSAttributeDescription()
        identifier.name = "identifier"
        identifier.attributeType = .stringAttributeType
        identifier.isOptional = false

        let source = NSEntityDescription()
        source.name = "Source"
        source.managedObjectClassName = NSStringFromClass(Source.self)
        source.properties = [identifier]

        let model = NSManagedObjectModel()
        model.entities = [source]
        persistentContainer = NSPersistentContainer(name: "SourceCoreDataHarness", managedObjectModel: model)
        let store = NSPersistentStoreDescription()
        store.type = NSInMemoryStoreType
        store.shouldAddStoreAsynchronously = false
        persistentContainer.persistentStoreDescriptions = [store]

        let loaded = DispatchSemaphore(value: 0)
        let loadResult = StoreLoadResult()
        persistentContainer.loadPersistentStores { _, error in
            loadResult.record(error)
            loaded.signal()
        }
        loaded.wait()
        if let error = loadResult.error { fatalError("in-memory Core Data store failed to load: \(error)") }
        // The regression specifically exercises a fetch against the persistent store
        // without relying on save-notification merging into the view context.
        persistentContainer.viewContext.automaticallyMergesChangesFromParent = false
    }
}

enum OperationError: Error { case noSources }

final class StoreLoadResult: @unchecked Sendable {
    private let lock = NSLock()
    private var storedError: Error?
    func record(_ error: Error?) {
        lock.lock(); storedError = error; lock.unlock()
    }
    var error: Error? {
        lock.lock(); defer { lock.unlock() }
        return storedError
    }
}

final class AppManager {
    static let didAddSourceNotification = Notification.Name("source.added")

    func persistForTest(_ source: Source, in context: NSManagedObjectContext) async throws -> (identifier: String, alreadyAdded: Bool) {
        try await persistConfirmedSource(source, in: context, notificationSource: nil)
    }

__PRODUCTION_PERSIST_HELPER__
}

final class NotificationCapture: @unchecked Sendable {
    private let lock = NSLock()
    private var storedSource: Source?
    private var storedCount = 0

    func record(_ notification: Notification) {
        lock.lock(); defer { lock.unlock() }
        storedCount += 1
        storedSource = notification.object as? Source
    }

    var result: (Source?, Int) {
        lock.lock(); defer { lock.unlock() }
        return (storedSource, storedCount)
    }
}

@main
struct SourceViewContextCoreDataHarness {
    static func main() async throws {
        let database = DatabaseManager.shared
        let manager = AppManager()
        let identifier = "com.example.coredata-source"
        let request = NSFetchRequest<NSFetchRequestResult>(entityName: "Source")
        request.predicate = NSPredicate(format: "%K == %@", #keyPath(Source.identifier), identifier)

        let initialCount = try database.viewContext.performAndWait {
            try database.viewContext.count(for: request)
        }
        require(initialCount == 0, "viewContext was not empty before the background save")
        require(!database.viewContext.automaticallyMergesChangesFromParent,
                "the harness must not rely on automatic save merging")

        let background = database.persistentContainer.newBackgroundContext()
        let inserted = try await background.performAsync { () throws -> Source in
            let source = NSEntityDescription.insertNewObject(forEntityName: "Source", into: background) as! Source
            source.identifier = identifier
            require(source.objectID.isTemporaryID, "test source should begin with a temporary object ID")
            return source
        }
        let capture = NotificationCapture()
        let token = NotificationCenter.default.addObserver(forName: AppManager.didAddSourceNotification,
                                                            object: nil, queue: nil) { capture.record($0) }
        defer { NotificationCenter.default.removeObserver(token) }

        let outcome = try await manager.persistForTest(inserted, in: background)
        require(outcome.identifier == identifier && !outcome.alreadyAdded,
                "production persistence helper did not report a new source")
        let savedObjectFacts = await background.performAsync {
            (inserted.objectID, inserted.objectID.isTemporaryID)
        }
        require(!savedObjectFacts.1, "background source did not receive a permanent object ID after save")

        let (notifiedSource, notificationCount) = capture.result
        guard let notifiedSource else { fatalError("production helper posted no Source object") }
        require(notificationCount == 1, "production helper must post exactly one add notification")
        let notificationFacts = database.viewContext.performAndWait {
            (notifiedSource.managedObjectContext === database.viewContext,
             notifiedSource.objectID.isTemporaryID,
             notifiedSource.objectID == savedObjectFacts.0)
        }
        require(notificationFacts.0, "notification Source is not owned by viewContext")
        require(!notificationFacts.1, "notification Source still has a temporary object ID")
        require(notificationFacts.2, "viewContext resolved a different Core Data object than the saved Source")

        let verificationContext = database.persistentContainer.newBackgroundContext()
        let savedCount = try await verificationContext.performAsync {
            try verificationContext.count(for: request)
        }
        require(savedCount == 1, "a third Core Data context did not observe exactly one saved source")
        print("V3_SOURCE_VIEW_CONTEXT_COREDATA_PASS")
    }
}
