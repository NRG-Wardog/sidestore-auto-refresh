import Foundation

enum FixtureDecision: Equatable {
    case keepAll
    case remove(String)
}

@main
struct ExtensionRemovalHarness {
    static func main() async throws {
        var promptCalls = 0
        let noTargets: FixtureDecision = try await V3ExtensionRemovalPromptPolicy.decide(
            targetExtensions: Set<String>(), whenEmpty: .keepAll) {
                promptCalls += 1
                return .remove("unexpected")
            }
        precondition(noTargets == .keepAll && promptCalls == 0,
                     "zero target extensions must not present removal choices")

        let withTargets: FixtureDecision = try await V3ExtensionRemovalPromptPolicy.decide(
            targetExtensions: Set(["widget.extension"]), whenEmpty: .keepAll) {
                promptCalls += 1
                return .remove("widget.extension")
            }
        precondition(withTargets == .remove("widget.extension") && promptCalls == 1,
                     "non-empty extension choices must still ask the user")
        print("V3_ZERO_EXTENSION_PROMPT_PASS")
    }
}
