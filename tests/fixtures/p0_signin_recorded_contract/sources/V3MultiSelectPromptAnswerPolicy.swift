import Foundation
import SwiftUI
import UIKit
import CoreFoundation
enum V3MultiSelectPromptAnswerPolicy {
    static func isMemberOption(kind: String, optionID: String) -> Bool {
        if ["keep", "keepAll"].contains(optionID) { return false }
        if kind == "extensions" && ["keepAllMainProfile", "removeAll", "cancel"].contains(optionID) { return false }
        return true
    }

    static func actionAnswer(_ actionID: String, fields: [String: String]) -> [String: String] {
        var answer = fields
        answer["choice"] = actionID
        answer.removeValue(forKey: "ids")
        answer.removeValue(forKey: "serials")
        return answer
    }

    static func selectedMembersAnswer(kind: String, selectedIDs: Set<String>,
                                      fields: [String: String]) -> [String: String] {
        var answer = fields
        answer["choice"] = kind == "revocation" ? "revoke" : "selected"
        answer["ids"] = selectedIDs.sorted().joined(separator: ",")
        answer["serials"] = selectedIDs.sorted().joined(separator: ",")
        return answer
    }
}
