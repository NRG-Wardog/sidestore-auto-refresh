import Foundation
import SwiftUI
import UIKit
import CoreFoundation
enum V3AuthFailureDiagnosticsPolicy {
    static func provisioning(reply: [String: Any], message: String, technical: String) -> (message: String, technical: String) {
        var evidence = (reply["failure"] as? [String: Any]) ?? ["stage": "provisioning", "code": "failed"]
        if let kind = reply["failureKind"] as? String { evidence["kind"] = kind }
        let canonical = "diagnostic_code=\(diagnosticCode(for: evidence)) builder_commit=\(V3DiagnosticBuild.commit)"
        // Preserve existing safe technical evidence, explicitly naming its
        // structured category separately from the presentation category.
        let underlying = technical.replacingOccurrences(of: "diagnostic_code=", with: "underlying_diagnostic_code=")
            .replacingOccurrences(of: "builder_commit=", with: "underlying_builder_commit=")
        return (display(message, failure: evidence), canonical + (underlying.isEmpty ? "" : "\n" + underlying))
    }

    static func display(_ message: String, failure: [String: Any]) -> String {
        // Replace our previous decoration only. Never inspect prose to infer a
        // cause; the canonical ID comes solely from the finite envelope fields.
        let prose = message.components(separatedBy: "\n").compactMap { line -> String? in
            let prefix = "Error ID: "
            guard line.hasPrefix(prefix + "SS-") else { return line }
            // Older call sites may append recovery prose after the ID token.
            // Remove only the decoration token, never the recovery instructions.
            let trailing = line.dropFirst(prefix.count).drop(while: { !$0.isWhitespace })
                .trimmingCharacters(in: .whitespaces)
            return trailing.isEmpty ? nil : trailing
        }.joined(separator: "\n")
        return prose + "\nError ID: " + diagnosticCode(for: failure)
    }

    static func diagnosticCode(for failure: [String: Any]) -> String {
        let stage = (failure["stage"] as? String).flatMap(CombinedFailure.Stage.init(rawValue:)) ?? .authentication
        let code = (failure["code"] as? String).flatMap(CombinedFailure.Code.init(rawValue:)) ?? .failed
        let step = (failure["sourceStep"] as? String).flatMap(CombinedFailure.SourceStep.init(rawValue:))
        let cause = (failure["safeCause"] as? String).flatMap(CombinedFailure.SafeCause.init(rawValue:))
        let fields = (failure["signingContext"] as? [String: String]).flatMap(CombinedFailure.validatedSigningContext) ?? [:]
        let failureCode = CombinedFailure(operation: "signIn", stage: stage, code: code,
            id: "00000000-0000-0000-0000-000000000000", safeCause: cause, sourceStep: step,
            signingContext: fields).diagnosticCode
        let kind = failure["kind"] as? String ?? failure["code"] as? String ?? "unknown"
        let kindToken: String
        switch kind {
        case "unknown": kindToken = "A00"
        case "invalidCredentials": kindToken = "A01"
        case "appSpecificPasswordRequired": kindToken = "A02"
        case "invalidCode": kindToken = "A03"
        case "rateLimited": kindToken = "A04"
        case "serviceUnavailable": kindToken = "A05"
        case "anisetteFailure", "anisette": kindToken = "A06"
        case "networkFailure", "network": kindToken = "A07"
        case "accountRepairRequired": kindToken = "A08"
        case "credentialStorage": kindToken = "A09"
        case "credentialStorageUncertain": kindToken = "A10"
        case "accountIdentityMismatch": kindToken = "A11"
        case "anisetteIdentityStateInvalid": kindToken = "A12"
        default: kindToken = "A00"
        }
        return failureCode + "-" + kindToken
    }
    static func shouldShowTerminalDetails(state: String, hasPrompt: Bool,
                                          hasFailure: Bool) -> Bool {
        hasFailure && !hasPrompt && ["failed", "timedOut", "promptExpired", "resultUnknown"]
            .contains(state)
    }

    static func render(_ failure: [String: Any], underlyingCode: Int?,
                       retryableValue: Bool?) -> String {
        let kind = failure["kind"] as? String ?? ""
        let stage = failure["stage"] as? String ?? ""
        let code = failure["code"] as? String ?? ""
        let correlation = failure["correlationID"] as? String ?? ""
        let underlyingDomain = failure["underlyingDomain"] as? String ?? ""
        let codeText = underlyingCode.map(String.init) ?? "unknown"
        let retryableText = retryableValue.map { $0 ? "yes" : "no" } ?? "unknown"
        let step = (failure["sourceStep"] as? String).flatMap(CombinedFailure.SourceStep.init(rawValue:))?.rawValue ?? "unknown"
        let fields = (failure["signingContext"] as? [String: String]).flatMap(CombinedFailure.validatedSigningContext) ?? [:]
        let accountDetails = " source_step=\(step) typed_error=\(fields["typed_error"] ?? "unknown") server_code=\(fields["server_code"] ?? "unknown") http_status=\(fields["http_status"] ?? "unavailable")"
        return "diagnostic_code=\(diagnosticCode(for: failure)) builder_commit=\(V3DiagnosticBuild.commit) kind=\(kind) stage=\(stage) code=\(code) correlation=\(correlation) underlying=\(underlyingDomain)/\(codeText) retryable=\(retryableText)" + accountDetails
    }
}
