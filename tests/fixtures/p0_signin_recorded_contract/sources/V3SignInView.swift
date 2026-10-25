import Foundation
import SwiftUI
import UIKit
import CoreFoundation
// Exact production body/properties, with injected fixture-owned service state.
// accountContent is empty ONLY for unsigned-in credentials with no recovery,
// cancellation-in-progress, progress message, or account diagnostics content.
// No layout claims after Cancel: only callback and button-disabled state are tested.
// This does not cover signed-in, terminal, recovery or cancellation-pending layout.
@MainActor struct V3SignInView: View {
    @ObservedObject var auth: V3AuthStore
    @EnvironmentObject private var status: FixtureStatusStore
    private var accountContent: some View { EmptyView() }
    var body: some View {
        List {
            if shouldShowAccountSection {
                Section("Apple ID") {
                    accountContent
                }
            }
            if let prompt = auth.prompt {
                V3PromptSection(prompt: prompt, isSubmitting: $auth.promptSubmitting,
                    isSubmissionBlocked: auth.promptResponseBlocked && (prompt["kind"] as? String == "twoFactor"),
                    previousFailureMessage: promptFailureMessage,
                    previousFailureDetails: promptFailureDetails,
                    supplementalContent: AnyView(accountContent),
                    cancellationTitle: auth.isCancelling ? "Cancelling..." :
                        (auth.cancellationWasAttempted ? "Retry Cancellation" : "Cancel Sign In"),
                    cancellationDisabled: auth.isCancelling,
                    onCancel: { auth.cancel() }) { answer in
                    auth.answer(promptID: prompt["id"] as? String ?? "", answer: answer)
                }

            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Sign In")
        .task { await auth.reconcile() }
        .onChange(of: auth.isSignedIn) { isSignedIn in
            guard isSignedIn else { return }
            // The app-owned root observer invalidates certificate-derived
            // readiness from correlated auth terminal events. This snapshot
            // updates the account presentation only.
            status.reload()
        }
        .onDisappear {
            auth.cancel()
            auth.clearPreviousFailure()
            // The root auth-event observer owns certificate-readiness
            // invalidation even after this presentation disappears.
            status.reload()
        }
    }

    private var shouldShowAccountSection: Bool { auth.prompt == nil }

    private var visiblePromptFailure: [String: Any]? {
        V3AuthPromptFailurePolicy.isVisible(auth.previousFailure,
            promptKind: auth.prompt?["kind"] as? String) ? auth.previousFailure : nil
    }

    private var promptFailureMessage: String {
        visiblePromptFailure.map { V3AuthStore.failureMessage(from: $0) } ?? ""
    }

    private var promptFailureDetails: String {
        visiblePromptFailure.map { V3AuthStore.failureDetails(from: $0) } ?? ""
    }
}
