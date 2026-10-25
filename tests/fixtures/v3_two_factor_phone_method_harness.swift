import Foundation

@main
struct V3TwoFactorPhoneMethodHarness {
    static func main() {
        typealias Policy = V3TwoFactorPhoneSelectionPolicy
        let phoneIDs = ["trusted-phone-1", "trusted-phone-2"]
        var deliveryRequests: [Policy.Decision] = []

        let back = Policy.resolve(method: "sms", action: "changeMethod", phoneIDs: phoneIDs)
        precondition(back == .changeMethod,
            "the phone-selection back action returns to the method-choice state")
        if case .requestSMS = back { deliveryRequests.append(back) }
        if case .requestVoice = back { deliveryRequests.append(back) }
        precondition(deliveryRequests.isEmpty,
            "changing method from phone selection dispatches no SMS or voice request")

        // Returning to method selection permits the user to choose Voice and
        // then select a number, producing exactly the newly chosen delivery.
        let nextChoice = Policy.resolve(method: "voice", action: "phone:trusted-phone-2",
                                        phoneIDs: phoneIDs)
        precondition(nextChoice == .requestVoice(phoneID: "trusted-phone-2"),
            "a new method and phone can be selected after backing out")
        deliveryRequests.append(nextChoice)
        precondition(deliveryRequests == [.requestVoice(phoneID: "trusted-phone-2")],
            "only the subsequent explicit selection issues a delivery request")

        precondition(Policy.resolve(method: "sms", action: "cancel", phoneIDs: phoneIDs) == .cancel,
            "Cancel Sign In remains available at phone selection")
        precondition(Policy.resolve(method: "sms", action: "phone:unknown",
                                    phoneIDs: phoneIDs) == .cancel,
            "an unoffered phone ID cannot produce a delivery request")

        print("V3_TWO_FACTOR_PHONE_METHOD_PASS")
    }
}
