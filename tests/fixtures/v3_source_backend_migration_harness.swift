import Foundation

func require(_ value: @autoclosure () -> Bool, _ message: String) {
    if !value() { fatalError(message) }
}

enum TestFailure: Error { case save }

enum OperationError: Error {
    case forbidden(failureReason: String)
}

final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var storedValue = 0
    var value: Int {
        lock.lock(); defer { lock.unlock() }
        return storedValue
    }
    func increment() {
        lock.lock(); storedValue += 1; lock.unlock()
    }
}

final class TestPersistentStore {
    static let shared = TestPersistentStore()
    var rows: [Source] = []
    var failNextSave = false
}

final class NSManagedObjectContext {
    private let store: TestPersistentStore
    var rows: [Source]

    init(store: TestPersistentStore = .shared) {
        self.store = store
        rows = store.rows
    }

    func save() throws {
        if store.failNextSave {
            store.failNextSave = false
            throw TestFailure.save
        }
        store.rows = rows
    }

    func delete(_ source: Source) {
        rows.removeAll { $0.identifier == source.identifier }
    }

    func contains(_ identifier: String) -> Bool {
        store.rows.contains { $0.identifier == identifier }
    }

    func source(matching predicate: NSPredicate) -> Source? {
        let parts = predicate.predicateFormat.components(separatedBy: " == ")
        guard let raw = parts.last else { return nil }
        let identifier = raw.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
        return rows.first { $0.identifier == identifier }
    }
}

extension NSManagedObjectContext {
    func performAsync<T>(_ work: () throws -> T) async throws -> T { try work() }
}

final class TestPersistentContainer {
    func newBackgroundContext() -> NSManagedObjectContext { NSManagedObjectContext() }
}

final class DatabaseManager {
    static let shared = DatabaseManager()
    let persistentContainer = TestPersistentContainer()
    var viewContext: NSManagedObjectContext { NSManagedObjectContext() }
}

@dynamicMemberLookup
struct AsyncManaged<Value> {
    let wrappedValue: Value
    init(wrappedValue: Value) { self.wrappedValue = wrappedValue }
    subscript<Member>(dynamicMember keyPath: KeyPath<Value, Member>) -> Member {
        wrappedValue[keyPath: keyPath]
    }
}

final class Source: NSObject {
    @objc let identifier: String
    static let altStoreIdentifier = "default-source"

    init(identifier: String) { self.identifier = identifier }

    func isAdded() async throws -> Bool {
        let independentContext = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        return independentContext.contains(identifier)
    }

    static func first(satisfying predicate: NSPredicate, in context: NSManagedObjectContext) -> Source? {
        context.source(matching: predicate)
    }
}

final class AppManager {
    static let shared = AppManager()
    static let didAddSourceNotification = Notification.Name("source.added")
    static let didRemoveSourceNotification = Notification.Name("source.removed")

    func fetchSource(sourceURL: URL, managedObjectContext: NSManagedObjectContext) async throws -> Source {
        let identifier = sourceURL.lastPathComponent
        if let existing = managedObjectContext.rows.first(where: { $0.identifier == identifier }) {
            return existing
        }
        let source = Source(identifier: identifier)
        managedObjectContext.rows.append(source)
        return source
    }
}

extension AppManager {
__PRODUCTION_SOURCE_METHODS__
}

@main
struct SourceBackendMigrationHarness {
    static func main() async throws {
        let store = TestPersistentStore.shared
        let manager = AppManager.shared
        let adds = Counter()
        let removes = Counter()
        let addToken = NotificationCenter.default.addObserver(forName: AppManager.didAddSourceNotification,
                                                                object: nil, queue: nil) { _ in adds.increment() }
        let removeToken = NotificationCenter.default.addObserver(forName: AppManager.didRemoveSourceNotification,
                                                                   object: nil, queue: nil) { _ in removes.increment() }
        defer {
            NotificationCenter.default.removeObserver(addToken)
            NotificationCenter.default.removeObserver(removeToken)
        }

        let url = URL(string: "https://sources.example/alpha")!
        let added = try await manager.addConfirmed(sourceURL: url)
        require(added.identifier == "alpha" && !added.alreadyAdded, "new source did not report added")
        require(store.rows.map(\.identifier) == ["alpha"], "new source was not persisted exactly once")
        require(adds.value == 1, "successful add did not post exactly one notification")

        let duplicate = try await manager.addConfirmed(sourceURL: url)
        require(duplicate.alreadyAdded, "duplicate source did not report already added")
        require(store.rows.map(\.identifier) == ["alpha"], "duplicate created a second row")
        require(adds.value == 1, "duplicate posted an add notification")

        store.failNextSave = true
        do {
            _ = try await manager.addConfirmed(sourceURL: URL(string: "https://sources.example/save-fails")!)
            fatalError("add save failure was swallowed")
        } catch TestFailure.save {}
        require(!store.rows.contains { $0.identifier == "save-fails" }, "failed save persisted the source")
        require(adds.value == 1, "failed save posted an add notification")

        try await manager.removeConfirmed(identifier: "alpha")
        require(store.rows.isEmpty, "remove did not delete the source")
        require(removes.value == 1, "successful remove did not post exactly one notification")

        try await manager.removeConfirmed(identifier: "missing")
        require(removes.value == 2, "missing-source remove did not preserve upstream notification behavior")

        store.rows = [Source(identifier: Source.altStoreIdentifier)]
        do {
            try await manager.removeConfirmed(identifier: Source.altStoreIdentifier)
            fatalError("default source removal was allowed")
        } catch {}
        require(store.rows.map(\.identifier) == [Source.altStoreIdentifier], "default source was deleted")
        require(removes.value == 2, "default source removal posted a notification")

        store.rows = [Source(identifier: "remove-fails")]
        store.failNextSave = true
        do {
            try await manager.removeConfirmed(identifier: "remove-fails")
            fatalError("remove save failure was swallowed")
        } catch TestFailure.save {}
        require(store.rows.map(\.identifier) == ["remove-fails"], "failed remove changed persistence")
        require(removes.value == 2, "failed remove posted a notification")

        print("V3_SOURCE_BACKEND_MIGRATION_PASS")
    }
}
