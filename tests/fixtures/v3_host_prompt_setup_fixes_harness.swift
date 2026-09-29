enum V3JITLessReadiness: String, Equatable {
    case ready
    case unknown
}

@main
struct HostPromptSetupFixesHarness {
    static func main() throws {
        let options = ["keepAll", "remove:com.example.one", "removeAll"]
        precondition(!V3MultiSelectPromptAnswerPolicy.isMemberOption(
            kind: "extensions", optionID: "removeAll"),
            "Remove All is an action, not a selectable extension member")
        precondition(V3MultiSelectPromptAnswerPolicy.isMemberOption(
            kind: "extensions", optionID: "remove:com.example.one"),
            "individual extension choices remain selectable members")

        let removeAll = V3MultiSelectPromptAnswerPolicy.actionAnswer("removeAll",
            fields: ["ids": "stale", "serials": "stale"])
        precondition(removeAll["choice"] == "removeAll" &&
            removeAll["ids"] == nil && removeAll["serials"] == nil,
            "the host Remove All action sends the explicit action without member IDs")
        switch try V3PromptSelectionPolicy.extensions(choice: removeAll["choice"],
            submittedOptionIDs: removeAll["ids"], offeredBundleIDs: ["com.example.one"]) {
        case .removeAll: break
        default: preconditionFailure("host Remove All answer must reach the backend removeAll branch")
        }

        let keepAll = V3MultiSelectPromptAnswerPolicy.actionAnswer("keepAll", fields: [:])
        precondition(keepAll["choice"] == "keepAll" && keepAll["ids"] == nil,
            "Keep All remains a separate explicit action")
        let selected = V3MultiSelectPromptAnswerPolicy.selectedMembersAnswer(
            kind: "extensions", selectedIDs: ["remove:com.example.two", "remove:com.example.one"], fields: [:])
        precondition(selected["choice"] == "selected" &&
            selected["ids"] == "remove:com.example.one,remove:com.example.two",
            "selected extension members remain deterministically encoded")
        switch try V3PromptSelectionPolicy.extensions(choice: selected["choice"],
            submittedOptionIDs: selected["ids"],
            offeredBundleIDs: ["com.example.one", "com.example.two"]) {
        case .remove(let values):
            precondition(values == ["com.example.one", "com.example.two"])
        default: preconditionFailure("selected members must remain a subset removal")
        }

        let readyAtR = V3SetupReadinessObservation(readiness: .ready,
            sourceFactRevision: 40, activeCertificateAvailable: true)
        precondition(!V3SetupReadinessObservationPolicy.shouldFetchLocalReadiness(
            readyAtR, currentFactRevision: 40),
            "a snapshot remains reusable while its source revision is current")
        precondition(readyAtR.activeCertificateAvailable == true,
            "readiness and active-certificate availability travel together")

        // Capture a shared ready value, then invalidate before the post-await
        // reuse decision. The stale source revision must trigger a fresh read.
        var currentRevision: UInt64 = 40
        let captured = readyAtR
        currentRevision = 41
        precondition(V3SetupReadinessObservationPolicy.shouldFetchLocalReadiness(
            captured, currentFactRevision: currentRevision),
            "ready@R cannot be re-stamped after invalidation to R+1")

        // A fresh read starts under R+1 and is suspended. Invalidation to R+2
        // occurs before its result arrives, so the late value cannot publish.
        let freshReadRevision = currentRevision
        currentRevision = 42
        precondition(!V3SetupReadinessObservationPolicy.mayApplyFreshObservation(
            sourceFactRevision: freshReadRevision, currentFactRevision: currentRevision),
            "invalidation during a fresh health read discards its late result")
        precondition(V3SetupReadinessObservationPolicy.mayApplyFreshObservation(
            sourceFactRevision: currentRevision, currentFactRevision: currentRevision),
            "a fresh result can publish while its captured revision is still current")

        print("V3_HOST_PROMPT_SETUP_FIXES_PASS")
    }
}
