import Foundation
import SwiftUI
import UIKit
import CoreFoundation
enum V3AuthPromptFailurePolicy {
    static func applying(reply: [String: Any], current: [String: Any]?) -> [String: Any]? {
        (reply["previousFailure"] as? [String: Any]) ?? current
    }

    static func isVisible(_ failure: [String: Any]?, promptKind: String?) -> Bool {
        failure != nil && promptKind == "credentials"
    }

    static func clearingAfterSubmission(_ failure: [String: Any]?, promptKind: String?) -> [String: Any]? {
        promptKind == "credentials" ? nil : failure
    }

    static func clearingOnDismiss(_ failure: [String: Any]?) -> [String: Any]? { nil }
}
