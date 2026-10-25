@main struct PromptTransportTests {
    @MainActor static func main() async throws {
        let handler = RefreshHandler.shared
        for scenario in ["current", "replacement", "disconnect", "sessionCancellation", "unknownSession", "cancelledBeforeDispatch"] {
            handler.client = FakeClient()
            handler.v3ServiceIdentity = UUID()
            let client = handler.client!
            let bridge = V3ServiceBridge(readTimeout: 2, commandTimeout: 2)
            let session = UUID().uuidString
            _ = try await bridge.request(operation: "authBegin", target: session,
                payload: ["session": session, "sessionDeadline": Date().addingTimeInterval(120)])
            switch scenario {
            case "replacement": handler.v3ServiceIdentity = UUID()
            case "disconnect": bridge.disconnected()
            case "sessionCancellation": _ = try await bridge.request(operation: "authCancel", target: session)
            default: break
            }
            let target = scenario == "unknownSession" ? UUID().uuidString : session
            let task = Task { @MainActor in
                try await bridge.request(operation: "authRespond", target: target,
                    payload: ["prompt": UUID().uuidString, "answer": ["action": "code", "code": "123456"]])
            }
            if scenario == "cancelledBeforeDispatch" { task.cancel() }
            do {
                let reply = try await task.value
                precondition(scenario == "current" && reply["session"] as? String == session,
                    "only the creating service instance may receive the answer")
            } catch {
                precondition(scenario != "current", "first answer must reach the existing service")
                if let failure = error as? CombinedFailure {
                    precondition(failure.stage == .xpcConnection,
                        "transport ownership failure cannot become an Apple password error")
                }
            }
            precondition(client.operations.filter { $0 == "authRespond" }.count == (scenario == "current" ? 1 : 0),
                "a stale, disconnected, unknown, or cancelled answer must never dispatch")
        }

        // Let the real bridge dispatch, then retire the process while its reply
        // is withheld at the external transport boundary. A replacement never
        // gets a replay and an old callback cannot report successful delivery.
        for mode in ["disconnect", "taskCancellation", "sessionCancellation"] {
            handler.client = FakeClient(); handler.v3ServiceIdentity = UUID()
            let oldClient = handler.client!
            let bridge = V3ServiceBridge(readTimeout: 2, commandTimeout: 2)
            let session = UUID().uuidString
            _ = try await bridge.request(operation: "authBegin", target: session,
                payload: ["session": session, "sessionDeadline": Date().addingTimeInterval(120)])
            oldClient.hold = true
            let answer = Task { @MainActor in
                try await bridge.request(operation: "authRespond", target: session,
                    payload: ["prompt": UUID().uuidString, "answer": ["action": "code", "code": "123456"]])
            }
            for _ in 0..<10000 {
                if oldClient.operations.contains("authRespond") { break }
                await Task.yield()
            }
            precondition(oldClient.operations.filter { $0 == "authRespond" }.count == 1)
            if mode == "taskCancellation" { answer.cancel() }
            if mode == "sessionCancellation" {
                oldClient.hold = false
                _ = try await bridge.request(operation: "authCancel", target: session)
            } else {
                bridge.disconnected()
                handler.client = FakeClient(); handler.v3ServiceIdentity = UUID()
            }
            let callbacks = oldClient.replies; oldClient.replies.removeAll()
            callbacks.forEach { $0() }
            do { _ = try await answer.value; fatalError("retired answer reply must not win") }
            catch {}
            precondition(handler.client!.operations.filter { $0 == "authRespond" }.count ==
                         (mode == "sessionCancellation" ? 1 : 0), "interactive answers are never replayed")
        }
        print("V3_PROMPT_TRANSPORT_PASS")
    }
}
