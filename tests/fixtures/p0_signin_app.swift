import Foundation
import SwiftUI
import UIKit
import Combine

// Passive fixture telemetry. Never reads or changes entered text or focus.
enum P0FixtureFocusIdentity {
    static func identify(placeholder: String?, secure: Bool) -> String {
        guard let placeholder else { return "other" }
        switch (placeholder, secure) {
        case ("Apple ID", false): return "username"
        case ("Password", true): return "password"
        default: return "other"
        }
    }
}

@MainActor
final class P0FixtureFocusObserver: ObservableObject {
    @Published private(set) var identity = "none"
    private weak var activeField: UITextField?
    func began(_ notification: Notification) {
        guard let field = notification.object as? UITextField else {
            activeField = nil; identity = "other"; return
        }
        activeField = field
        identity = P0FixtureFocusIdentity.identify(placeholder: field.placeholder, secure: field.isSecureTextEntry)
    }
    func ended(_ notification: Notification) {
        guard let field = notification.object as? UITextField, field === activeField else { return }
        activeField = nil
        identity = "none"
    }
}

// Deterministic, unsigned-in credentials fixture. No real service, account,
// keychain, network, or cancellation operation runs in this process.
@MainActor final class FixtureStatusStore: ObservableObject {
    func reload() {}
}

@MainActor final class V3AuthStore: ObservableObject {
    static let username = "p0-user@example.invalid"
    static let password = "p0-synthetic-password"
    static let fixtureFailure: [String: Any] = [
        "kind": "unknown", "stage": "authentication", "code": "failed",
        "correlationID": "00000000-0000-0000-0000-000000000025",
        "underlyingDomain": "redacted", "underlyingCode": 0,
        "sourceStep": "authenticate",
        "signingContext": ["typed_error": "unknownAccountFailure"],
    ]
    @Published var prompt: [String: Any]? = [
        "id": "p0-synthetic-credentials", "kind": "credentials",
        "title": "Apple ID Sign In", "message": "Enter the Apple ID and password used for signing.",
        "fields": [["key": "appleID", "label": "Apple ID", "secure": "false", "value": ""],
                   ["key": "password", "label": "Password", "secure": "true"]],
    ]
    @Published var previousFailure: [String: Any]? = V3AuthStore.fixtureFailure
    @Published var promptSubmitting = false
    @Published var cancelCount = 0
    @Published var cancelledWhileSubmitting = false
    @Published var answerCount = 0
    @Published var clipboard = ""
    let state = "awaitingPrompt"
    let promptResponseBlocked = false
    @Published var isCancelling = false
    let cancellationWasAttempted = false
    let isSignedIn = false
    func reconcile() async {}
    func clearPreviousFailure() { previousFailure = nil }
    func answer(promptID: String, answer: [String: String]) {
        guard !promptSubmitting, promptID == "p0-synthetic-credentials",
              answer["appleID"] == Self.username, answer["password"] == Self.password else { return }
        answerCount += 1
        promptSubmitting = true
        // Match the production credentials admission transition before testing
        // Cancel while submission is active; no request is dispatched.
        previousFailure = V3AuthPromptFailurePolicy.clearingAfterSubmission(
            previousFailure, promptKind: prompt?["kind"] as? String)
    }
    func cancel() {
        cancelCount += 1
        cancelledWhileSubmitting = promptSubmitting
        promptSubmitting = false
        isCancelling = true
        // Record routing and cancellation button disabling only. Keeping the deterministic prompt avoids claiming
        // to execute the production cancellation/reconciliation state machine.
    }
    func observeClipboard() {
        let current = UIPasteboard.general.string ?? ""
        if clipboard != current { clipboard = current }
    }
}

@MainActor struct P0SignInScreen: View {
    @StateObject private var auth = V3AuthStore()
    @StateObject private var status = FixtureStatusStore()
    @StateObject private var focus = P0FixtureFocusObserver()
    let width: CGFloat
    let largest: Bool
    var body: some View {
        NavigationView {
            V3SignInView(auth: auth).environmentObject(status)
                .safeAreaInset(edge: .bottom) {
                    // Fixture-only telemetry is outside the extracted controls.
                    // Never synthesizes taps, writes pasteboard content or layout.
                    HStack(spacing: 6) {
                        Text("P0 fixture").accessibilityIdentifier("p0-ready")
                            .accessibilityValue(focus.identity)
                        Text("Clipboard").accessibilityIdentifier("p0-clipboard")
                            .accessibilityValue(auth.clipboard)
                        Text("Expected").accessibilityIdentifier("p0-expected")
                            .accessibilityValue(V3AuthStore.failureDetails(from: V3AuthStore.fixtureFailure))
                        Text(String(auth.cancelCount)).accessibilityIdentifier("p0-cancel-count")
                        Text(auth.cancelledWhileSubmitting ? "yes" : "no")
                            .accessibilityIdentifier("p0-cancelled-submission")
                        Text(String(auth.answerCount)).accessibilityIdentifier("p0-answer-count")
                        Text("Prior failure").accessibilityIdentifier("p0-prior-failure")
                            .accessibilityValue(auth.previousFailure == nil ? "cleared" : "present")
                    }
                    .font(.system(size: 9)).environment(\.sizeCategory, .large)
                    .frame(maxWidth: .infinity).frame(height: 24)
                    .background(Color(.systemBackground))
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier("p0-telemetry")
                }
        }
        .navigationViewStyle(.stack)
        .environment(\.sizeCategory, largest ? .accessibilityExtraExtraExtraLarge : .large)
        .frame(width: width)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("p0-viewport")
        .frame(maxWidth: .infinity, alignment: .leading)
        .onReceive(NotificationCenter.default.publisher(for: UITextField.textDidBeginEditingNotification)) { focus.began($0) }
        .onReceive(NotificationCenter.default.publisher(for: UITextField.textDidEndEditingNotification)) { focus.ended($0) }
        .onReceive(NotificationCenter.default.publisher(for: UIPasteboard.changedNotification)) { _ in
            auth.observeClipboard()
        }
    }
}

@main @MainActor struct P0SignInApp: App {
    init() { UIPasteboard.general.string = "" }
    var body: some Scene {
        WindowGroup {
            GeometryReader { geometry in
                P0SignInScreen(width: min(320, geometry.size.width),
                    largest: ProcessInfo.processInfo.arguments.contains("--largest"))
            }
            .ignoresSafeArea(.container, edges: .horizontal)
        }
    }
}
