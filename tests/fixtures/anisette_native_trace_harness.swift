import Foundation

typealias CStringPointer = UnsafeMutablePointer<CChar>
protocol AnisetteDataProvider {}
enum AnisetteError: Error {
    case loaderFailed(reason: String)
    case adiError(code: Int32, description: String)
}
struct AnisetteDataResponse {
    let values: [String: String]
    init(from values: [String: String]) throws { self.values = values }
}
struct Provider: AnisetteDataProvider {}
extension AnisetteDataProvider {
__PRODUCTION_RESPONSE_PARSER__
__PRODUCTION_JSON_PARSER__
}
__PRODUCTION_NATIVE_TRACE__

@main struct NativeTraceTests {
    static func parse(_ value: [String: String]) throws -> AnisetteDataResponse {
        let data = try JSONSerialization.data(withJSONObject: value)
        return try String(data: data, encoding: .utf8)!.withCString { pointer in
            try Provider().parseHeadersResponse(code: -45061,
                outPtr: UnsafeMutablePointer(mutating: pointer), providerName: "Synthetic")
        }
    }

    static func main() throws {
        let canary = "PASSWORD-TOKEN-UUID-BLOB-OTP-MID-PATH-CANARY"
        let valid = "setup.begin,file.open.ok,file.write.failed,native.otp.failed,cleanup.ok"
        let invalid = ["", ",native.otp.failed", "native.otp.failed,", "native.otp.failed,,cleanup.ok",
            "native.otp.failed," + canary, "native.otp.failed\n", "native.otp.failed☃",
            Array(repeating: "arguments.ok", count: 33).joined(separator: ","),
            String(repeating: "arguments.ok,", count: 100)]
        for trace in [valid] + invalid + Array(TemporaryAnisetteNativeTrace.allowed) {
            let response = try parse(["header": "SYNTHETIC", "v3_native_trace": trace])
            precondition(response.values == ["header": "SYNTHETIC"])
            do {
                _ = try parse(["error": "Stable error", "v3_native_trace": trace])
                preconditionFailure("Expected error")
            } catch AnisetteError.adiError(let code, let description) {
                let accepted = trace == valid || TemporaryAnisetteNativeTrace.allowed.contains(trace)
                let expected = TemporaryAnisetteNativeTrace.enabled && accepted
                    ? "Stable error [DEBUG_TEMPORARY_NATIVE_TRACE:\(trace)]" : "Stable error"
                precondition(code == -45061 && description == expected)
                precondition(!description.contains(canary))
            }
        }
        do {
            _ = try parse(["error": "Unchanged error"])
            preconditionFailure("Expected error")
        } catch AnisetteError.adiError(let code, let description) {
            precondition(code == -45061 && description == "Unchanged error")
        }
        print("NATIVE_SWIFT_TRACE_PASS")
    }
}
