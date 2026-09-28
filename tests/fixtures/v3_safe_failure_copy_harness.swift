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

        let privateNativeDomain = "com.private.user-7428391.endpoint"
        let privateNativeDescription = "private endpoint 7428391 could not be reached"
        let privateNativeFailure = CombinedFailure(operation: "command", stage: .command,
            code: .failed, id: UUID().uuidString,
            underlying: NSError(domain: privateNativeDomain, code: 7428391,
                userInfo: [NSLocalizedDescriptionKey: privateNativeDescription]),
            safeCause: .anisetteUnknownFailure)
        let privateNativeDetails = privateNativeFailure.technicalDetails
        let privateNativeWire = privateNativeFailure.wire
        let privateNativeEncoded = privateNativeFailure.encodedString
        let privateNativeDecoded = CombinedFailure.fromEncodedString(
            privateNativeEncoded, expectedID: privateNativeFailure.correlationID)
        precondition(privateNativeDetails.contains("underlying_domain=redacted") &&
                     privateNativeDetails.contains("underlying_code=unknown") &&
                     !privateNativeDetails.contains(privateNativeDomain) &&
                     !privateNativeDetails.contains(privateNativeDescription) &&
                     !privateNativeDetails.contains("7428391"),
                     "a typed failure must omit both untrusted native domain and code from copied details")
        precondition(privateNativeWire["underlyingDomain"] as? String == "redacted" &&
                     privateNativeWire["underlyingCode"] as? Int == 0 &&
                     privateNativeWire["safeCause"] as? String == "anisetteUnknownFailure" &&
                     !privateNativeEncoded.contains(privateNativeDomain) &&
                     !privateNativeEncoded.contains("7428391") &&
                     privateNativeDecoded?.underlyingDomain == "redacted" &&
                     privateNativeDecoded?.underlyingCode == 0 &&
                     privateNativeDecoded?.safeCause == .anisetteUnknownFailure &&
                     privateNativeDecoded?.technicalDetails.contains("underlying_code=unknown") == true,
                     "the structured envelope preserves the safe cause while omitting the private numeric code")

        let typed = CombinedFailure(operation: "command", stage: .network,
            code: .failed, id: UUID().uuidString,
            underlying: NSError(domain: NSURLErrorDomain, code: -1009),
            safeCause: .networkUnavailable)
        precondition(V3FailureGuidance.diagnostics(typed) == typed.technicalDetails,
                     "already structured CombinedFailure diagnostics must remain unchanged")
        precondition(typed.underlyingDomain == NSURLErrorDomain &&
                     typed.underlyingCode == -1009 &&
                     typed.wire["underlyingCode"] as? Int == -1009 &&
                     typed.technicalDetails.contains("underlying_code=-1009"),
                     "allowlisted native domains retain their useful numeric codes")
        let promptCorrelation = UUID().uuidString
        let privateProvisioningPrompt = CombinedFailure.provisioningRetryTechnicalDetails(
            for: NSError(domain: privateNativeDomain, code: 7428391,
                userInfo: [NSLocalizedDescriptionKey: privateNativeDescription]),
            correlationID: promptCorrelation)
        precondition(privateProvisioningPrompt.contains("domain=redacted") &&
                     privateProvisioningPrompt.contains("code=unknown") &&
                     privateProvisioningPrompt.contains("area=provisioning") &&
                     privateProvisioningPrompt.contains("correlation=\(promptCorrelation)") &&
                     !privateProvisioningPrompt.contains(privateNativeDomain) &&
                     !privateProvisioningPrompt.contains(privateNativeDescription) &&
                     !privateProvisioningPrompt.contains("7428391"),
                     "provisioning retry prompt diagnostics redact private native NSError fields")
        let allowedProvisioningPrompt = CombinedFailure.provisioningRetryTechnicalDetails(
            for: NSError(domain: NSURLErrorDomain, code: -1009),
            correlationID: promptCorrelation)
        precondition(allowedProvisioningPrompt.contains("domain=NSURLErrorDomain") &&
                     allowedProvisioningPrompt.contains("code=-1009") &&
                     allowedProvisioningPrompt.contains("area=provisioning"),
                     "allowlisted technical diagnostics remain useful in the provisioning prompt")

        let refreshRunID = UUID().uuidString
        let rawRefreshPayload: [String: Any] = [
            "liveContainerAutoRefreshVerification": [
                "version": 2,
                "schema": "LiveContainerRefreshManifestV2",
                "run_id": refreshRunID,
                "expected_ids": ["com.example.target"],
                "results": [[
                    "bundle_id": "com.example.target",
                    "success": false,
                    "error_domain": privateNativeDomain,
                    "error_code": 7428391,
                    "error": privateNativeDescription
                ]]
            ]
        ]
        let sanitizedRefreshPayload = CombinedVerification.sanitized(rawRefreshPayload, runID: refreshRunID)
        guard let sanitizedManifest = sanitizedRefreshPayload["liveContainerAutoRefreshVerification"] as? [String: Any],
              let sanitizedRows = sanitizedManifest["results"] as? [[String: Any]],
              let sanitizedRow = sanitizedRows.first,
              let sanitizedFailureWire = sanitizedRow["failure"] as? [String: Any],
              let sanitizedPlist = try? PropertyListSerialization.data(
                fromPropertyList: sanitizedRefreshPayload, format: .binary, options: 0),
              let sanitizedObject = try? PropertyListSerialization.propertyList(
                from: sanitizedPlist, format: nil) as? [String: Any],
              let decodedManifest = sanitizedObject["liveContainerAutoRefreshVerification"] as? [String: Any],
              let decodedRows = decodedManifest["results"] as? [[String: Any]],
              let decodedRow = decodedRows.first,
              let decodedFailureWire = decodedRow["failure"] as? [String: Any] else {
            preconditionFailure("sanitized refresh diagnostics must remain a valid property list")
        }
        let sanitizedRefreshError = sanitizedRow["error"] as? String ?? ""
        precondition(sanitizedRow["error_domain"] as? String == "redacted" &&
                     sanitizedRow["error_code"] as? Int == 0 &&
                     sanitizedFailureWire["underlyingDomain"] as? String == "redacted" &&
                     sanitizedFailureWire["underlyingCode"] as? Int == 0 &&
                     decodedRow["error_domain"] as? String == "redacted" &&
                     decodedRow["error_code"] as? Int == 0 &&
                     decodedFailureWire["underlyingDomain"] as? String == "redacted" &&
                     decodedFailureWire["underlyingCode"] as? Int == 0 &&
                     !sanitizedRefreshError.contains(privateNativeDomain) &&
                     !sanitizedRefreshError.contains(privateNativeDescription) &&
                     !sanitizedRefreshError.contains("7428391"),
                     "refresh manifest sanitization omits untrusted NSError fields before copy/export")

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
