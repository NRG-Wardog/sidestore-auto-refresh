enum RefreshIntentStartFailure: Error {
    case factoryFailed
}

@main
struct RefreshIntentStartHarness {
    static func main() async throws {
        let failed = Task {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                let operation = V3RefreshIntentStartPolicy.create({ () -> String in
                    throw RefreshIntentStartFailure.factoryFailed
                }, continuation: continuation)
                precondition(operation == nil)
            }
        }
        do {
            try await failed.value
            preconditionFailure("a factory failure returned success")
        } catch RefreshIntentStartFailure.factoryFailed {}

        let succeeded = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let operation = V3RefreshIntentStartPolicy.create({ "refresh-operation" }, continuation: continuation)
            precondition(operation == "refresh-operation")
            continuation.resume()
        }
        _ = succeeded
        print("V3_REFRESH_INTENT_START_PASS")
    }
}
