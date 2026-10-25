import Foundation

func require(_ value: @autoclosure () -> Bool, _ message: String) {
    if !value() { fatalError(message) }
}

enum TestFailure: Error { case save }

enum OperationError: Error {
    case forbidden(failureReason: String)
    case noSources
}

enum UIAlertActionStyle { case `default`, destructive }

final class UIAlertAction {
    init(title: String, style: UIAlertActionStyle) {}
}

final class UIViewController {
    private(set) var confirmationCount = 0
    func presentConfirmationAlert(title: String, message: String, primaryAction: UIAlertAction) async throws {
        confirmationCount += 1
    }
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

final class SourceRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var storedAdd: Source?
    private var storedRemove: Source?
    var added: Source? {
        lock.lock(); defer { lock.unlock() }
        return storedAdd
    }
    var removed: Source? {
        lock.lock(); defer { lock.unlock() }
        return storedRemove
    }
    func recordAdd(_ source: Source?) {
        lock.lock(); storedAdd = source; lock.unlock()
    }
    func recordRemove(_ source: Source?) {
        lock.lock(); storedRemove = source; lock.unlock()
    }
}

final class TestPersistentStore {
    static let shared = TestPersistentStore()
    var identifiers: [String] = []
    var failNextSave = false
}

final class NSManagedObjectContext {
    private let store: TestPersistentStore
    let isViewContext: Bool
    var rows: [Source]

    init(store: TestPersistentStore = .shared, isViewContext: Bool = false) {
        self.store = store
        self.isViewContext = isViewContext
        rows = []
        rows = store.identifiers.map { Source(identifier: $0, managedObjectContext: self) }
    }

    func save() throws {
        if store.failNextSave {
            store.failNextSave = false
            throw TestFailure.save
        }
        store.identifiers = rows.map(\.identifier)
        if !isViewContext {
            DatabaseManager.shared.viewContext.merge(identifiers: store.identifiers)
        }
    }

    func delete(_ source: Source) {
        rows.removeAll { $0.identifier == source.identifier }
    }

    func contains(_ identifier: String) -> Bool {
        store.identifiers.contains(identifier)
    }

    func merge(identifiers: [String]) {
        rows = identifiers.map { Source(identifier: $0, managedObjectContext: self) }
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
    let viewContext = NSManagedObjectContext(isViewContext: true)
}

@propertyWrapper @dynamicMemberLookup
struct AsyncManaged<Value> {
    var wrappedValue: Value
    init(wrappedValue: Value) { self.wrappedValue = wrappedValue }
    var projectedValue: AsyncManaged<Value> { self }
    func perform<Result>(_ work: (Value) -> Result) async -> Result { work(wrappedValue) }
    subscript<Member>(dynamicMember keyPath: KeyPath<Value, Member>) -> Member {
        wrappedValue[keyPath: keyPath]
    }
}

final class Source: NSObject {
    @objc let identifier: String
    let name: String
    weak var managedObjectContext: NSManagedObjectContext?
    static let altStoreIdentifier = "default-source"

    init(identifier: String, name: String = "Source", managedObjectContext: NSManagedObjectContext? = nil) {
        self.identifier = identifier
        self.name = name
        self.managedObjectContext = managedObjectContext
    }

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
        let source = Source(identifier: identifier, managedObjectContext: managedObjectContext)
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
        let sources = SourceRecorder()
        let addToken = NotificationCenter.default.addObserver(forName: AppManager.didAddSourceNotification,
                                                                object: nil, queue: nil) { note in
            adds.increment(); sources.recordAdd(note.object as? Source)
        }
        let removeToken = NotificationCenter.default.addObserver(forName: AppManager.didRemoveSourceNotification,
                                                                   object: nil, queue: nil) { note in
            removes.increment(); sources.recordRemove(note.object as? Source)
        }
        defer {
            NotificationCenter.default.removeObserver(addToken)
            NotificationCenter.default.removeObserver(removeToken)
        }

        let url = URL(string: "https://sources.example/alpha")!
        let added = try await manager.addConfirmed(sourceURL: url)
        require(added.identifier == "alpha" && !added.alreadyAdded, "new source did not report added")
        require(store.identifiers == ["alpha"], "new source was not persisted exactly once")
        require(adds.value == 1, "successful add did not post exactly one notification")
        require(sources.added?.identifier == "alpha", "add notification omitted its Source object")
        require(sources.added?.managedObjectContext === DatabaseManager.shared.viewContext,
                "headless add notification object did not come from viewContext")

        let duplicate = try await manager.addConfirmed(sourceURL: url)
        require(duplicate.alreadyAdded, "duplicate source did not report already added")
        require(store.identifiers == ["alpha"], "duplicate created a second row")
        require(adds.value == 1, "duplicate posted an add notification")

        store.failNextSave = true
        do {
            _ = try await manager.addConfirmed(sourceURL: URL(string: "https://sources.example/save-fails")!)
            fatalError("add save failure was swallowed")
        } catch TestFailure.save {}
        require(!store.identifiers.contains("save-fails"), "failed save persisted the source")
        require(adds.value == 1, "failed save posted an add notification")

        try await manager.removeConfirmed(identifier: "alpha")
        require(store.identifiers.isEmpty, "remove did not delete the source")
        require(removes.value == 1, "successful remove did not post exactly one notification")
        require(sources.removed?.identifier == "alpha", "remove notification omitted its Source object")
        require(sources.removed?.managedObjectContext === DatabaseManager.shared.viewContext,
                "remove notification object did not come from viewContext")

        try await manager.removeConfirmed(identifier: "missing-without-object")
        require(removes.value == 1, "missing remove posted a nil notification object")
        DatabaseManager.shared.viewContext.rows.append(
            Source(identifier: "missing", managedObjectContext: DatabaseManager.shared.viewContext))
        try await manager.removeConfirmed(identifier: "missing")
        require(removes.value == 2, "missing-source remove did not post with its available Source object")
        require(sources.removed?.identifier == "missing" &&
                sources.removed?.managedObjectContext === DatabaseManager.shared.viewContext,
                "missing-source notification used an unsafe object")

        store.identifiers = []
        DatabaseManager.shared.viewContext.merge(identifiers: [])
        do {
            try await manager.removeConfirmed(identifier: Source.altStoreIdentifier)
            fatalError("backend allowed removal of an absent default-source identifier")
        } catch {}
        require(removes.value == 2, "absent default-source removal emitted a notification")

        store.identifiers = [Source.altStoreIdentifier]
        DatabaseManager.shared.viewContext.merge(identifiers: store.identifiers)
        let confirmationController = UIViewController()
        let defaultSource = Source(identifier: Source.altStoreIdentifier, name: "Default",
                                   managedObjectContext: DatabaseManager.shared.viewContext)
        do {
            try await manager.remove(defaultSource, presentingViewController: confirmationController)
            fatalError("UI allowed removal of the default source")
        } catch {}
        require(confirmationController.confirmationCount == 0,
                "UI presented removal confirmation for the default source")
        do {
            try await manager.removeConfirmed(identifier: Source.altStoreIdentifier)
            fatalError("default source removal was allowed")
        } catch {}
        require(store.identifiers == [Source.altStoreIdentifier], "default source was deleted")
        require(removes.value == 2, "default source removal posted a notification")

        store.identifiers = ["remove-fails"]
        DatabaseManager.shared.viewContext.merge(identifiers: store.identifiers)
        store.failNextSave = true
        do {
            try await manager.removeConfirmed(identifier: "remove-fails")
            fatalError("remove save failure was swallowed")
        } catch TestFailure.save {}
        require(store.identifiers == ["remove-fails"], "failed remove changed persistence")
        require(removes.value == 2, "failed remove posted a notification")

        print("V3_SOURCE_BACKEND_MIGRATION_PASS")
    }
}
