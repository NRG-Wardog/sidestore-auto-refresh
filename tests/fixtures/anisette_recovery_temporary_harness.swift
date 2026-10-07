import Foundation
import Darwin

struct ALTAnisetteData {}
struct AnisetteRequestHeaders {}
enum LCAnisetteRecoveryError: Error { case invalidNativeProof }
enum AnisetteConstants { enum Headers {
    static let oneTimePassword = "X-Apple-I-MD"
    static let machineID = "X-Apple-I-MD-M"
} }
enum TemporaryProbeState {
    static var root: URL?
    static var mode = "success"
    static var validated = false
}
enum IsolatedAnisetteOTPProvider {
    static func getExistingHeaders(libDir: URL, provisioningDir: URL, identifier: UUID,
        existingBlob: Data, headers: AnisetteRequestHeaders) async throws -> [String: String] {
        TemporaryProbeState.root = provisioningDir
        let attributes = try FileManager.default.attributesOfItem(atPath: provisioningDir.path)
        precondition((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o700)
        precondition(existingBlob == Data("synthetic-existing-blob".utf8))
        if TemporaryProbeState.mode == "throw" { throw NSError(domain: "fixture", code: 1) }
        if TemporaryProbeState.mode == "cancel" { throw CancellationError() }
        if TemporaryProbeState.mode == "cleanup" { try FileManager.default.removeItem(at: provisioningDir) }
        return [AnisetteConstants.Headers.oneTimePassword: Data("otp".utf8).base64EncodedString(),
                AnisetteConstants.Headers.machineID: Data("mid".utf8).base64EncodedString()]
    }
}
enum AnisetteDataManager {
    static func validateAndCreateAnisetteData(from headers: [String: String]) throws -> ALTAnisetteData {
        TemporaryProbeState.validated = true
        if TemporaryProbeState.mode == "invalid" { throw LCAnisetteRecoveryError.invalidNativeProof }
        return ALTAnisetteData()
    }
}

__PRODUCTION_TEMPORARY_PROBE__

@main struct TemporaryProbeHarness {
    static func main() async throws {
        for mode in ["success", "throw", "cancel", "invalid", "cleanup"] {
            TemporaryProbeState.mode = mode
            TemporaryProbeState.root = nil
            TemporaryProbeState.validated = false
            do {
                let result = try await LCAnisetteIsolatedProbe.run(
                    libraries: URL(fileURLWithPath: "/synthetic-libraries"), identifier: UUID(),
                    blob: Data("synthetic-existing-blob".utf8), headers: AnisetteRequestHeaders())
                precondition(mode == "success" && TemporaryProbeState.validated)
                precondition(result.oneTimePassword == Data("otp".utf8).base64EncodedString())
            } catch {
                precondition(mode != "success")
                if mode == "cancel" { precondition(error is CancellationError) }
                if mode == "cleanup" { precondition(error is LCAnisetteIsolatedProbe.LocalFailure) }
            }
            let root = TemporaryProbeState.root!
            precondition(!FileManager.default.fileExists(atPath: root.path))
        }
        print("TEMPORARY_PROBE_PASS")
    }
}
