import CoreData
import Foundation

@objc(OwnershipAccount)
final class Account: NSManagedObject {
    @NSManaged var appleID: String
    @NSManaged var isActiveAccount: Bool
}

@objc(OwnershipTeam)
final class Team: NSManagedObject {
    @NSManaged var identifier: String
    @NSManaged var isActiveTeam: Bool
    @NSManaged var account: Account?
}

final class DatabaseManager {
    static let shared = DatabaseManager()
    let persistentContainer: NSPersistentContainer
    private init() { persistentContainer = Self.makeContainer() }

    func activeTeam(in context: NSManagedObjectContext) -> Team? {
        precondition(context.concurrencyType == .privateQueueConcurrencyType)
        let request = NSFetchRequest<Team>(entityName: "Team")
        request.predicate = NSPredicate(format: "isActiveTeam == YES")
        request.fetchLimit = 1
        return try? context.fetch(request).first
    }

    func activeAccount(in context: NSManagedObjectContext) -> Account? {
        precondition(context.concurrencyType == .privateQueueConcurrencyType)
        let request = NSFetchRequest<Account>(entityName: "Account")
        request.predicate = NSPredicate(format: "isActiveAccount == YES")
        request.fetchLimit = 1
        return try? context.fetch(request).first
    }

    private static func makeContainer() -> NSPersistentContainer {
        let account = NSEntityDescription()
        account.name = "Account"
        account.managedObjectClassName = NSStringFromClass(Account.self)
        let appleID = NSAttributeDescription()
        appleID.name = "appleID"
        appleID.attributeType = .stringAttributeType
        appleID.isOptional = false
        let activeAccount = NSAttributeDescription()
        activeAccount.name = "isActiveAccount"
        activeAccount.attributeType = .booleanAttributeType
        activeAccount.isOptional = false
        account.properties = [appleID, activeAccount]

        let team = NSEntityDescription()
        team.name = "Team"
        team.managedObjectClassName = NSStringFromClass(Team.self)
        let identifier = NSAttributeDescription()
        identifier.name = "identifier"
        identifier.attributeType = .stringAttributeType
        identifier.isOptional = false
        let activeTeam = NSAttributeDescription()
        activeTeam.name = "isActiveTeam"
        activeTeam.attributeType = .booleanAttributeType
        activeTeam.isOptional = false
        let owner = NSRelationshipDescription()
        owner.name = "account"
        owner.destinationEntity = account
        owner.minCount = 0
        owner.maxCount = 1
        owner.deleteRule = .nullifyDeleteRule
        owner.isOptional = true
        team.properties = [identifier, activeTeam, owner]

        let model = NSManagedObjectModel()
        model.entities = [account, team]
        let container = NSPersistentContainer(name: "OwnershipSnapshot", managedObjectModel: model)
        let store = NSPersistentStoreDescription()
        store.type = NSInMemoryStoreType
        container.persistentStoreDescriptions = [store]
        return container
    }
}

final class DeveloperPortalProxy {}

extension DeveloperPortalProxy {
    /*GENERATED_OWNER_SNAPSHOT*/

    func readOwnerSnapshot(for identifier: String) async throws
        -> (teamOwners: [String], activeTeamIdentifier: String?, activeAccountOwner: String?) {
        let snapshot = try await databaseOwnershipSnapshot(for: identifier)
        return (snapshot.teamOwners, snapshot.activeTeamIdentifier, snapshot.activeAccountOwner)
    }
}

@main
struct AuthIdentityCoreDataHarness {
    static func main() async throws {
        let container = DatabaseManager.shared.persistentContainer
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            container.loadPersistentStores { _, error in
                if let error { continuation.resume(throwing: error) }
                else { continuation.resume() }
            }
        }
        let context = container.viewContext
        var accountAID: NSManagedObjectID!
        var accountBID: NSManagedObjectID!
        try context.performAndWait {
            let accountA = Account(context: context)
            accountA.appleID = "a@example.com"
            accountA.isActiveAccount = true
            let accountB = Account(context: context)
            accountB.appleID = "b@example.com"
            accountB.isActiveAccount = false
            let shared = Team(context: context)
            shared.identifier = "shared-team"
            shared.isActiveTeam = true
            shared.account = accountA
            let other = Team(context: context)
            other.identifier = "team-b"
            other.isActiveTeam = false
            other.account = accountB
            accountAID = accountA.objectID
            accountBID = accountB.objectID
            try context.save()
        }

        let proxy = DeveloperPortalProxy()
        let aa = try await proxy.readOwnerSnapshot(for: "shared-team")
        let ownerAA = V3AuthIdentityBindingPolicy.resolveColdTeamOwner(
            storedTeamOwners: aa.teamOwners, activeTeamIdentifier: aa.activeTeamIdentifier,
            requestedTeamIdentifier: "shared-team", activeAccountOwner: aa.activeAccountOwner,
            sessionOwner: "a@example.com")
        precondition(V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: "a@example.com", teamOwner: ownerAA),
                     "active A/A snapshot remains bound to A")

        try context.performAndWait {
            let accountA = try context.existingObject(with: accountAID) as! Account
            let accountB = try context.existingObject(with: accountBID) as! Account
            accountA.isActiveAccount = false
            accountB.isActiveAccount = true
            try context.save()
        }
        let sharedB = try await proxy.readOwnerSnapshot(for: "shared-team")
        precondition(sharedB.teamOwners == ["a@example.com"] &&
                     sharedB.activeTeamIdentifier == "shared-team" &&
                     sharedB.activeAccountOwner == "b@example.com",
                     "one background read returns stale Team.account A with active Account B")
        let sharedBOwner = V3AuthIdentityBindingPolicy.resolveColdTeamOwner(
            storedTeamOwners: sharedB.teamOwners, activeTeamIdentifier: sharedB.activeTeamIdentifier,
            requestedTeamIdentifier: "shared-team", activeAccountOwner: sharedB.activeAccountOwner,
            sessionOwner: "b@example.com")
        precondition(V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: "b@example.com", teamOwner: sharedBOwner),
                     "active B binds a shared team whose stored relation remains A")

        try context.performAndWait {
            let accountA = try context.existingObject(with: accountAID) as! Account
            let accountB = try context.existingObject(with: accountBID) as! Account
            accountA.isActiveAccount = true
            accountB.isActiveAccount = false
            try context.save()
        }
        let stillA = try await proxy.readOwnerSnapshot(for: "shared-team")
        let stillAOwner = V3AuthIdentityBindingPolicy.resolveColdTeamOwner(
            storedTeamOwners: stillA.teamOwners, activeTeamIdentifier: stillA.activeTeamIdentifier,
            requestedTeamIdentifier: "shared-team", activeAccountOwner: stillA.activeAccountOwner,
            sessionOwner: "b@example.com")
        precondition(!V3AuthIdentityBindingPolicy.mayUseTeam(sessionOwner: "b@example.com", teamOwner: stillAOwner),
                     "A's active account/team snapshot cannot authorize B credentials after B's fetch failed")

        let mixedOwner = V3AuthIdentityBindingPolicy.resolveColdTeamOwner(
            storedTeamOwners: ["a@example.com", "b@example.com"],
            activeTeamIdentifier: "team-a", requestedTeamIdentifier: "team-b",
            activeAccountOwner: "b@example.com", sessionOwner: "b@example.com")
        precondition(mixedOwner == nil,
                     "a conflicting owner set and nonmatching active team cannot borrow active B")
        print("V3_AUTH_IDENTITY_COREDATA_PASS")
    }
}
