@main
struct V3SafeFailureCopyHarness {
    static func main() {
        let allowedDomain = NSError(domain: NSURLErrorDomain, code: -1009)
        let allowedDetails = V3FailureGuidance.diagnostics(allowedDomain)
        precondition(allowedDetails.contains("underlying_domain=NSURLErrorDomain"),
                     "an allowlisted native domain should remain useful in copied diagnostics")
        precondition(allowedDetails.contains("underlying_code=-1009"),
                     "an allowlisted native code should remain available for support")

        let privateDomain = "https://private.example/token-should-not-leak"
        let privateDescription = "Request to https://private.example/user-token failed"
        let untyped = NSError(domain: privateDomain, code: 4865,
            userInfo: [NSLocalizedDescriptionKey: privateDescription])
        let redactedDetails = V3FailureGuidance.diagnostics(untyped)
        precondition(redactedDetails.contains("underlying_domain=redacted"),
                     "unapproved domains must be redacted in copied diagnostics")
        precondition(redactedDetails.contains("underlying_code=unknown"),
                     "a numeric code without an approved domain must not be copied")
        precondition(!redactedDetails.contains(privateDomain) &&
                     !redactedDetails.contains(privateDescription) &&
                     !redactedDetails.contains("4865"),
                     "untrusted NSError fields must not cross the copy boundary")

        let typed = CombinedFailure(operation: "command", stage: .network,
            code: .failed, id: UUID().uuidString,
            underlying: NSError(domain: NSURLErrorDomain, code: -1009),
            safeCause: .networkUnavailable)
        precondition(V3FailureGuidance.diagnostics(typed) == typed.technicalDetails,
                     "already structured CombinedFailure diagnostics must remain unchanged")

        let sideJITFailure = V3SideJITReachabilityFeedback.unreachable
        precondition(sideJITFailure ==
            "The SideJIT server could not be reached. Check its address and network, then try again.")
        precondition(!sideJITFailure.contains(privateDomain) &&
                     !sideJITFailure.contains(privateDescription) &&
                     !sideJITFailure.contains("NSError"))
        precondition(V3SideJITReachabilityFeedback.reachable(httpStatusCode: 503) ==
                     "Reachable (HTTP 503).")
        precondition(V3SideJITReachabilityFeedback.reachable(httpStatusCode: nil) == "Reachable.")

        let cancelledAnisette = CombinedFailure(operation: "anisetteSync", stage: .command,
            code: .cancelled, id: UUID().uuidString, retryable: false)
        precondition(cancelledAnisette.operation == "anisetteSync",
                     "typed Anisette operation identity must survive failure normalization")
        let cancellationMessage = V3AnisetteFailureGuidance.message(cancelledAnisette) ?? ""
        precondition(cancellationMessage.contains("request was cancelled"))
        precondition(cancellationMessage.contains("check the current state"))
        precondition(!cancellationMessage.localizedCaseInsensitiveContains("LocalDevVPN") &&
                     !cancellationMessage.localizedCaseInsensitiveContains("reconnect"))

        let anisetteNetworkFailure = CombinedFailure(operation: "anisetteSync", stage: .network,
            code: .failed, id: UUID().uuidString, safeCause: .networkTimedOut)
        let networkMessage = V3AnisetteFailureGuidance.message(anisetteNetworkFailure) ?? ""
        precondition(networkMessage.contains("configured Anisette server"))
        precondition(networkMessage.contains("does not show that LocalDevVPN is unavailable"))
        print("V3_SAFE_FAILURE_COPY_PASS")
    }
}
