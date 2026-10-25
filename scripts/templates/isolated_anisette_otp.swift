// V3_ISOLATED_ANISETTE_OTP_V1. Existing identity/blob only; no provisioning API.
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

public enum IsolatedAnisetteOTPProvider {
    /// provisioningDir must be a caller-owned temporary directory.
    /// The original UUID and blob are never persisted or replaced by this API.
    public static func getExistingHeaders(
        libDir: URL, provisioningDir: URL, identifier: UUID, existingBlob: Data,
        headers: AnisetteRequestHeaders? = nil
    ) async throws -> [String: String] {
        guard !existingBlob.isEmpty, existingBlob.count <= 1_048_576,
              libDir.isFileURL, provisioningDir.isFileURL else {
            throw AnisetteError.invalidArgument
        }
        try Task.checkCancellation()
        #if canImport(Darwin) || canImport(Glibc)
        // The upstream memory-storage cleanup removes its UUID directory.
        // Give it a private, exclusively-created subroot that this call owns.
        var pattern = Array(provisioningDir.appendingPathComponent("isolated-otp-XXXXXX").path.utf8CString)
        let ownedRoot = try pattern.withUnsafeMutableBufferPointer { buffer -> URL in
            guard let created = mkdtemp(buffer.baseAddress!) else {
                throw AnisetteError.adiError(code: -6, description: "Isolated OTP staging failed")
            }
            return URL(fileURLWithPath: String(cString: created), isDirectory: true)
        }
        var cleaned = false
        defer { if !cleaned { try? FileManager.default.removeItem(at: ownedRoot) } }
        let client = try AnisetteClient(provisioningDir: ownedRoot,
            provider: IsolatedExistingBlobProvider(), libraryDirectoryResolver: { libDir })
        let result = try await client.getAnisetteData(identifier: identifier,
            storage: .memory(existingBlob: existingBlob), headers: headers)
        do {
            try FileManager.default.removeItem(at: ownedRoot)
            cleaned = true
        } catch {
            throw AnisetteError.adiError(code: -6, description: "Isolated OTP cleanup failed")
        }
        try Task.checkCancellation()
        guard result.newBlob == nil else { throw AnisetteError.invalidArgument }
        return result.headers
        #else
        throw AnisetteError.invalidArgument
        #endif
    }
}

private struct IsolatedExistingBlobProvider: AnisetteDataProvider {
    var requiresLocalLibraries: Bool { true }

    func getAnisetteHeaders(libDir: String, provisioningDir: String,
                           identifier: [UInt8], adiPb: [UInt8]) throws -> AnisetteDataResponse {
        guard identifier.count == 16, !adiPb.isEmpty, adiPb.count <= 1_048_576 else {
            throw AnisetteError.invalidArgument
        }
        var outPtr: CStringPointer? = nil
        let code = get_anisette_headers_isolated_uc(libDir, provisioningDir,
            identifier, adiPb, UInt32(adiPb.count), &outPtr)
        defer { if let outPtr { free_c_string(outPtr) } }
        return try parseHeadersResponse(code: code, outPtr: outPtr, providerName: "Isolated OTP")
    }

    func startProvision(libDir: String, provisioningDir: String, identifier: [UInt8],
                        spim: [UInt8]) throws -> (cpim: Data, session: UInt32) {
        throw AnisetteError.invalidArgument
    }

    func endProvision(libDir: String, provisioningDir: String, identifier: [UInt8],
                      session: UInt32, ptm: [UInt8], tk: [UInt8]) throws -> Data {
        throw AnisetteError.invalidArgument
    }
}
