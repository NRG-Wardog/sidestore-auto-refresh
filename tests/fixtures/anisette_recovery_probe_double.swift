// Native VM is the only mocked recovery boundary in this ODA integration test.
enum AnisetteKit {
    enum AnisetteError: Error { case adiError(code: Int32, description: String) }
}
enum LCAnisetteIsolatedProbe {
    enum LocalFailure: Error { case temporaryStorage }
    static var calls = 0
    static var action: ((UUID, Data) throws -> (ALTAnisetteData, String, String))?
    static func run(libraries: URL, identifier: UUID, blob: Data,
                    headers: AnisetteRequestHeaders) async throws
        -> (result: ALTAnisetteData, oneTimePassword: String, machineID: String) {
        calls += 1
        guard let action else { throw LocalFailure.temporaryStorage }
        let value = try action(identifier, blob)
        return (value.0, value.1, value.2)
    }
}
