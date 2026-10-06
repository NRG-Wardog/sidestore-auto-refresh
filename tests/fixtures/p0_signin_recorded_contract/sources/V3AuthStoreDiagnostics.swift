import Foundation
import SwiftUI
import UIKit
import CoreFoundation
extension V3AuthStore {
    static func failureMessage(from failure: [String: Any]) -> String {
        func messageWithoutDiagnosticCode() -> String {
        // The service classifies the real typed error into a display kind.
        // Only show password guidance for proven invalid credentials.
        switch failure["kind"] as? String {
        case "invalidCredentials": return "Apple did not accept the Apple ID or password. Check them and try again."
        case "appSpecificPasswordRequired": return "Apple requires an app-specific password for this authentication path."
        case "invalidCode": return "The verification code was not accepted. Enter a new code and try again."
        case "rateLimited": return "Too many authentication attempts. Apple is temporarily rate-limiting requests. Wait before trying again."
        case "serviceUnavailable": return "Apple's authentication service did not return a valid response. Try again later."
        case "anisetteIdentityStateInvalid": return LCAnisettePairError.safeMessage
        case "anisetteFailure", "anisette": return "Authentication could not obtain valid Anisette data."
        case "networkFailure", "network": return "Authentication could not reach the required service. Check the connection and try again."
        case "accountRepairRequired": return "Apple requires attention on this account before signing in."
        case "credentialStorage": return "Apple authentication succeeded, but the credentials could not be saved on this device. Reload Account & Signing and review Diagnostics before starting another sign-in."
        case "credentialStorageUncertain": return "Apple authentication succeeded, but the credential save result is uncertain. Reload Account & Signing to reconcile local storage before continuing."
        case "accountIdentityMismatch": return "Use the same Apple ID as the saved account. Reload status if the account changed."
        case "unknown": return "Sign-in failed before completion. Copy Details to help identify the cause."
        case nil: break
        default: break
        }
        let code = failure["code"] as? String ?? ""
        let stage = failure["stage"] as? String ?? ""
        switch code {
        case "invalidCredentials": return "Apple did not accept the Apple ID or password. Check them and try again."
        case "appSpecificPasswordRequired": return "Apple requires an app-specific password for this authentication path."
        case "rateLimited": return "Too many authentication attempts. Apple is temporarily rate-limiting requests. Wait before trying again."
        case "serviceUnavailable": return "Apple's authentication service is temporarily unavailable. Try again later."
        case "anisetteFailure": return "Authentication could not obtain valid Anisette data."
        case "networkFailure": return "Authentication could not reach the required service."
        case "accountRepairRequired": return "Account repair is required. Open the Apple Developer account to resolve."
        default:
            let messages = ["authentication": "Apple ID sign-in failed.",
                           "anisette": "Anisette authentication infrastructure failure.",
                           "network": "Network error during authentication.",
                           "accountRepair": "Account repair required."]
            return messages[stage] ?? "Apple ID sign-in failed."
        }

        }
        return V3AuthFailureDiagnosticsPolicy.display(messageWithoutDiagnosticCode(), failure: failure)
    }

    static func failureDetails(from failure: [String: Any]) -> String {
        V3AuthFailureDiagnosticsPolicy.render(failure,
            underlyingCode: V3ServiceBridge.strictInt(failure["underlyingCode"]),
            retryableValue: V3ServiceBridge.strictBool(failure["retryable"]))
    }
}
