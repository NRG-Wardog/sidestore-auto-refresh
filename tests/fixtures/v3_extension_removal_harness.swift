import Foundation

enum FixtureDecision: Equatable {
    case keepAll
    case remove(String)
}

@main
struct ExtensionRemovalHarness {
    static func main() async throws {
        var promptCalls = 0
        let noExcess: FixtureDecision = try await V3ExtensionRemovalPromptPolicy.decide(
            excessExtensions: Set<String>(), whenEmpty: .keepAll) {
                promptCalls += 1
                return .remove("unexpected")
            }
        precondition(noExcess == .keepAll && promptCalls == 0,
                     "zero excess extensions must not present removal choices")

        let withExcess: FixtureDecision = try await V3ExtensionRemovalPromptPolicy.decide(
            excessExtensions: Set(["widget.extension"]), whenEmpty: .keepAll) {
                promptCalls += 1
                return .remove("widget.extension")
            }
        precondition(withExcess == .remove("widget.extension") && promptCalls == 1,
                     "non-empty extension choices must still ask the user")
        print("V3_ZERO_EXTENSION_PROMPT_PASS")
    }
}
