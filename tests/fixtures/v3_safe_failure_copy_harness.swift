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

        let noUnderlyingFailure = CombinedFailure(operation: "command", stage: .command,
            code: .failed, id: UUID().uuidString)
        precondition(noUnderlyingFailure.underlyingDomain == "none" && noUnderlyingFailure.underlyingCode == 0 &&
                     noUnderlyingFailure.wire["underlyingDomain"] as? String == "none" &&
                     noUnderlyingFailure.wire["underlyingCode"] as? Int == 0,
                     "the reserved no-underlying sentinel must remain the canonical none/0 pair")

        let sentinelPrivateDescription = "sentinel-collision-private-7428391"
        let sentinelFailure = CombinedFailure(operation: "command", stage: .command,
            code: .failed, id: UUID().uuidString,
            underlying: NSError(domain: "none", code: 7428391,
                userInfo: [NSLocalizedDescriptionKey: sentinelPrivateDescription]))
        let sentinelDetails = sentinelFailure.technicalDetails
        let sentinelWire = sentinelFailure.wire
        let sentinelEncoded = sentinelFailure.encodedString
        let sentinelDecoded = CombinedFailure.fromEncodedString(
            sentinelEncoded, expectedID: sentinelFailure.correlationID)
        precondition(sentinelFailure.underlyingDomain == "redacted" && sentinelFailure.underlyingCode == 0 &&
                     sentinelDetails.contains("underlying_domain=redacted") &&
                     sentinelDetails.contains("underlying_code=unknown") &&
                     !sentinelDetails.contains("7428391") && !sentinelDetails.contains(sentinelPrivateDescription),
                     "NSError domain none cannot carry a distinctive private numeric code")
        precondition(sentinelWire["underlyingDomain"] as? String == "redacted" &&
                     sentinelWire["underlyingCode"] as? Int == 0 &&
                     sentinelDecoded?.underlyingDomain == "redacted" &&
                     sentinelDecoded?.underlyingCode == 0 &&
                     !sentinelEncoded.contains("7428391"),
                     "initializer, wire, and encoded roundtrip must canonicalize the reserved-domain collision")
        let sentinelNativeDiagnostics = V3FailureGuidance.diagnostics(
            NSError(domain: "none", code: 7428391,
                userInfo: [NSLocalizedDescriptionKey: sentinelPrivateDescription]))
        let sentinelPromptDiagnostics = CombinedFailure.provisioningRetryTechnicalDetails(
            for: NSError(domain: "none", code: 7428391), correlationID: UUID().uuidString)
        precondition(sentinelNativeDiagnostics.contains("underlying_domain=redacted") &&
                     sentinelNativeDiagnostics.contains("underlying_code=unknown") &&
                     !sentinelNativeDiagnostics.contains("7428391") &&
                     sentinelPromptDiagnostics.contains("domain=redacted") &&
                     sentinelPromptDiagnostics.contains("code=unknown") &&
                     !sentinelPromptDiagnostics.contains("7428391"),
                     "none/nonzero sentinels must not expose numeric codes through any copyable diagnostic path")

        let collisionID = UUID().uuidString
        let encodedCollision: [String: Any] = [
            "version": 1, "operation": "command", "stage": "command", "code": "failed",
            "correlationID": collisionID, "underlyingDomain": "none", "underlyingCode": 7428391
        ]
        let collisionData = try! PropertyListSerialization.data(
            fromPropertyList: encodedCollision, format: .binary, options: 0)
        let collisionText = "LCFAILURE1:" + collisionData.base64EncodedString()
        let collisionDecoded = CombinedFailure.fromEncodedString(collisionText, expectedID: collisionID)
        precondition(collisionDecoded?.underlyingDomain == "redacted" &&
                     collisionDecoded?.underlyingCode == 0 &&
                     collisionDecoded?.technicalDetails.contains("underlying_code=unknown") == true &&
                     collisionDecoded?.technicalDetails.contains("7428391") == false &&
                     collisionDecoded?.wire["underlyingDomain"] as? String == "redacted" &&
                     collisionDecoded?.wire["underlyingCode"] as? Int == 0,
                     "decoder must canonicalize malicious none/nonzero envelopes before diagnostics")

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

        let sentinelRunID = UUID().uuidString
        let hostileSentinelWire: [String: Any] = [
            "version": 1, "operation": "refresh", "stage": "refreshVerification", "code": "failed",
            "correlationID": sentinelRunID, "underlyingDomain": "none", "underlyingCode": 7428391
        ]
        let hostileSentinelManifest: [String: Any] = [
            "liveContainerAutoRefreshVerification": [
                "version": 2,
                "schema": "LiveContainerRefreshManifestV2",
                "run_id": sentinelRunID,
                "expected_ids": ["com.example.sentinel"],
                "results": [[
                    "bundle_id": "com.example.sentinel",
                    "success": false,
                    "error_domain": "none",
                    "error_code": 7428391,
                    "error": "none sentinel collision 7428391",
                    "failure": hostileSentinelWire
                ]]
            ]
        ]
        let hostileManifestData = try! PropertyListSerialization.data(
            fromPropertyList: hostileSentinelManifest, format: .binary, options: 0)
        let hostileManifestObject = try! PropertyListSerialization.propertyList(
            from: hostileManifestData, format: nil) as! [String: Any]
        let safeSentinelRefresh = CombinedVerification.sanitized(hostileManifestObject, runID: sentinelRunID)
        guard let safeSentinelManifest = safeSentinelRefresh["liveContainerAutoRefreshVerification"] as? [String: Any],
              let safeSentinelRows = safeSentinelManifest["results"] as? [[String: Any]],
              let safeSentinelRow = safeSentinelRows.first,
              let safeSentinelFailure = safeSentinelRow["failure"] as? [String: Any],
              let safeSentinelData = try? PropertyListSerialization.data(
                fromPropertyList: safeSentinelRefresh, format: .binary, options: 0),
              let safeSentinelObject = try? PropertyListSerialization.propertyList(
                from: safeSentinelData, format: nil) as? [String: Any],
              let decodedSentinelManifest = safeSentinelObject["liveContainerAutoRefreshVerification"] as? [String: Any],
              let decodedSentinelRows = decodedSentinelManifest["results"] as? [[String: Any]],
              let decodedSentinelRow = decodedSentinelRows.first,
              let decodedSentinelFailure = decodedSentinelRow["failure"] as? [String: Any] else {
            preconditionFailure("sentinel-collision refresh failures must remain a valid property-list manifest")
        }
        let sentinelRefreshError = safeSentinelRow["error"] as? String ?? ""
        let decodedSentinelError = decodedSentinelRow["error"] as? String ?? ""
        precondition(safeSentinelRow["error_domain"] as? String == "redacted" &&
                     safeSentinelRow["error_code"] as? Int == 0 &&
                     safeSentinelFailure["underlyingDomain"] as? String == "redacted" &&
                     safeSentinelFailure["underlyingCode"] as? Int == 0 &&
                     decodedSentinelRow["error_domain"] as? String == "redacted" &&
                     decodedSentinelRow["error_code"] as? Int == 0 &&
                     decodedSentinelFailure["underlyingDomain"] as? String == "redacted" &&
                     decodedSentinelFailure["underlyingCode"] as? Int == 0 &&
                     sentinelRefreshError.contains("underlying_domain=redacted") &&
                     sentinelRefreshError.contains("underlying_code=unknown") &&
                     decodedSentinelError.contains("underlying_code=unknown") &&
                     !sentinelRefreshError.contains("7428391") &&
                     !decodedSentinelError.contains("7428391"),
                     "refresh sanitization and plist roundtrip must redact none/nonzero sentinel collisions")

        let sideJITFailure = V3SideJITReachabilityFeedback.unreachable
        precondition(sideJITFailure ==
            "The SideJIT server could not be reached. Check its address and network, then try again." + "\nError ID: SS-NET-D061")
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
