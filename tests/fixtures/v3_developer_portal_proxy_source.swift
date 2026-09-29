public class DeveloperPortalProxy {
    public static let shared: DeveloperPortalProxy = DeveloperPortalProxyWithAuth()
    fileprivate init() {}

    private func getSession() async throws -> ALTAppleAPISession {
        try await AuthManager.shared.getAuthenticatedSession()
    }

    private func getTeam(_ team: ALTTeam? = nil) async throws -> ALTTeam {
        if let team { return team }
        return try await AuthManager.shared.getAuthenticatedTeam()
    }

    public func fetchTeams(for account: ALTAccount) async throws -> [ALTTeam] {
        let session = try await self.getSession()
        return try await ALTAppleAPI.shared.fetchTeams(for: account, session: session)
    }

    public func fetchCertificates(team: ALTTeam? = nil) async throws -> [ALTX509Certificate] {
        let session = try await self.getSession()
        let team = try await self.getTeam(team)
        return try await ALTAppleAPI.shared.fetchCertificates(for: team, session: session)
    }

    public func fetchDevices(for team: ALTTeam? = nil) async throws -> [ALTDevice] {
        let session = try await self.getSession()
        let team = try await self.getTeam(team)
        return try await ALTAppleAPI.shared.fetchDevices(for: team, session: session)
    }

    public func fetchAppIDs(team: ALTTeam? = nil) async throws -> [ALTAppID] {
        let session = try await self.getSession()
        let team = try await self.getTeam(team)
        return try await ALTAppleAPI.shared.fetchAppIDs(for: team, session: session)
    }
}
