// DEBUG/TEMPORARY: diagnostic metadata belongs to one response, never headers.
private enum TemporaryAnisetteNativeTrace {
    static let enabled = __TEMPORARY_TRACE_ENABLED__
    static let allowed: Set<String> = [__TEMPORARY_TRACE_TOKENS__]

    static func suffix(_ value: String?) -> String {
        guard enabled, let value, !value.isEmpty, value.utf8.count <= 1024,
              value.utf8.allSatisfy({ $0 < 128 }) else { return "" }
        let tokens = value.split(separator: ",", omittingEmptySubsequences: false)
        guard tokens.count <= 32,
              tokens.allSatisfy({ allowed.contains(String($0)) }) else { return "" }
        return " [DEBUG_TEMPORARY_NATIVE_TRACE:\(value)]"
    }
}
