import Foundation
import SwiftUI
import UIKit
import CoreFoundation
enum V3TwoFactorStep: String, Equatable {
    case chooseDeliveryMethod
    case choosePhoneNumber
    case deliveryRequested
    case enterVerificationCode
    case verifyingCode
    case completed
    case failed
    case cancelled

    var progressLabel: String? {
        switch self {
        case .choosePhoneNumber: return "Choose a phone number for this verification request..."
        case .deliveryRequested: return "Requesting verification..."
        case .verifyingCode: return "Verifying code..."
        default: return nil
        }
    }

    static func afterDeliveryChoice(_ method: String, phoneCount: Int) -> Self? {
        guard ["trustedDevice", "sms", "voice"].contains(method) else { return nil }
        return method == "sms" || method == "voice" ? (phoneCount > 1 ? .choosePhoneNumber : .deliveryRequested) : .deliveryRequested
    }

    static func afterDelivery(_ method: String) -> Self? {
        ["trustedDevice", "sms", "voice"].contains(method) ? .enterVerificationCode : nil
    }

    static func afterVerification(accepted: Bool) -> Self {
        accepted ? .completed : .enterVerificationCode
    }

    static var afterChangeMethod: Self { .chooseDeliveryMethod }
}
