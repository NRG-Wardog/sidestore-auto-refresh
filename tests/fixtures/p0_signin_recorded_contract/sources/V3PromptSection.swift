import Foundation
import SwiftUI
import UIKit
import CoreFoundation
struct V3PromptSection: View {
    let prompt: [String: Any]
    @Binding var isSubmitting: Bool
    var isSubmissionBlocked = false
    var previousFailureMessage = ""
    var previousFailureDetails = ""
    var supplementalContent: AnyView? = nil
    var cancellationTitle = "Cancel Sign In"
    var cancellationDisabled = false
    var onCancel: (() -> Void)? = nil
    let onAnswer: ([String: String]) -> Void
    @State private var fields: [String: String] = [:]
    @State private var selected: Set<String> = []
    @State private var copiedDetails = false
    @State private var repairURL: URL?
    private var kind: String { prompt["kind"] as? String ?? "" }
    private var title: String { prompt["title"] as? String ?? "Input Needed" }
    private var message: String { prompt["message"] as? String ?? "" }
    private var fieldDefs: [[String: String]] {
        (prompt["fields"] as? [[String: Any]] ?? []).compactMap { row in
            guard let key = row["key"] as? String else { return nil }
            return ["key": key, "label": row["label"] as? String ?? key,
                    "secure": row["secure"] as? String ?? "false",
                    "value": row["value"] as? String ?? ""]
        }
    }
    private var options: [[String: String]] {
        (prompt["options"] as? [[String: Any]] ?? []).compactMap { row in
            guard let id = row["id"] as? String else { return nil }
            return ["id": id, "label": row["label"] as? String ?? id]
        }
    }
    private var isMulti: Bool { kind == "extensions" || kind == "revocation" }
    private var deliveryOptions: [[String: String]] {
        options.filter { ["trustedDevice", "sms", "voice"].contains($0["id"] ?? "") }
    }
    private var phoneOptions: [[String: String]] {
        options.filter { ($0["id"] ?? "").hasPrefix("phone:") }
    }
    private var twoFactorStep: V3TwoFactorStep {
        let raw = fieldDefs.first(where: { $0["key"] == "step" })?["value"] ?? "chooseDeliveryMethod"
        return V3TwoFactorStep(rawValue: raw) ?? .chooseDeliveryMethod
    }
    var body: some View {
        Section(title) {
            if !previousFailureMessage.isEmpty {
                Text(previousFailureMessage)
                    .font(.footnote).foregroundColor(.orange)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("signin.prompt.previous-error")
                if !previousFailureDetails.isEmpty {
                    DisclosureGroup("Technical details") {
                        Text(previousFailureDetails)
                            .font(.caption).foregroundColor(.secondary)
                            .textSelection(.enabled)
                            .accessibilityIdentifier("signin.prompt.previous-error-body")
                    }
                    .accessibilityIdentifier("signin.prompt.previous-error-details")
                    Button("Copy Details") { UIPasteboard.general.string = previousFailureDetails }
                        .font(.caption)
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("signin.prompt.copy-details")
                        .accessibilityHint("Copy diagnostic details for this sign-in attempt.")
                }
            }
            if !message.isEmpty {
                Text(message)
                    .font(.footnote)
                    .foregroundColor(.secondary)
            }
            if let supplementalContent { supplementalContent }
            if kind == "twoFactor" {
                switch twoFactorStep {
                case .chooseDeliveryMethod:
                    Text("Choose how Apple sends your verification code.")
                        .font(.subheadline.weight(.semibold))
                    ForEach(deliveryOptions, id: \.self) { option in twoFactorOption(option) }
                    twoFactorCancelButton()
                case .choosePhoneNumber:
                    Text("Choose the phone number for this request.")
                        .font(.subheadline.weight(.semibold))
                    ForEach(phoneOptions, id: \.self) { option in twoFactorOption(option) }
                    Button("Change Verification Method", systemImage: "arrow.uturn.backward") {
                        var answer = fields
                        answer["action"] = "changeMethod"
                        answer["choice"] = "changeMethod"
                        respond(answer)
                    }
                    .disabled(isSubmitting)
                    twoFactorCancelButton()
                case .enterVerificationCode:
                    TextField("Verification code", text: binding("code"))
                        .keyboardType(.numberPad)
                        .textFieldStyle(.roundedBorder)
                        .textContentType(.oneTimeCode)
                    Button("Verify Code") {
                        var answer = fields
                        answer["choice"] = "code"
                        answer["action"] = "code"
                        respond(answer)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled((fields["code"] ?? "").count != 6 || isSubmitting || isSubmissionBlocked)
                    ForEach(options.filter { $0["id"] == "resend" }, id: \.self) { option in
                        twoFactorOption(option)
                    }
                    Button("Change Verification Method", systemImage: "arrow.uturn.backward") {
                        var answer = fields
                        answer["action"] = "changeMethod"
                        answer["choice"] = "changeMethod"
                        respond(answer)
                    }
                    .disabled(isSubmitting)
                    twoFactorCancelButton()
                case .verifyingCode:
                    ProgressView("Verifying code...")
                case .deliveryRequested:
                    ProgressView("Requesting verification...")
                case .completed:
                    Label("Verification complete", systemImage: "checkmark.circle.fill")
                        .foregroundColor(.green)
                case .failed:
                    Text("Verification could not continue. You can change method or cancel sign-in." + "\nError ID: SS-AUTH-D089")
                        .font(.footnote)
                    twoFactorCancelButton()
                case .cancelled:
                    Text("Sign-in was cancelled.").font(.footnote)
                }
            } else {
            ForEach(fieldDefs, id: \.self) { field in
                // The "technical" field is diagnostics-only output: it renders
                // as selectable caption text below, never as an editable field.
                if field["key"] == "step" || field["key"] == "mode" || field["key"] == "activeID" || field["key"] == "phoneID" || field["key"] == "url" || field["key"] == "urlToken" || field["key"] == "serials" || field["key"] == "technical" {
                    if field["key"] == "urlToken" {
                        if let repairURL {
                            Link("Open Apple Account Repair", destination: repairURL)
                                .font(.caption)
                        } else {
                            Text("Apple account repair link is unavailable.")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    } else if let value = field["value"], !value.isEmpty, field["key"] == "url" {
                        if let repairURL = V3AuthRepairURLPolicy.openableURL(value) {
                            Link("Open Apple Account Repair", destination: repairURL)
                                .font(.caption)
                        }
                        Text(value)
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .textSelection(.enabled)
                    }
                } else if field["secure"] == "true" {
                    SecureField(field["label"] ?? "", text: binding(field["key"] ?? ""))
                } else {
                    TextField(field["label"] ?? "", text: binding(field["key"] ?? ""))
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                }
            }
            if fieldDefs.count > 0 && options.isEmpty {
                Button("Submit") { submit(choice: "") }
                    .buttonStyle(.borderedProminent)
                    .disabled(isSubmitting)
            }
            // Safe technical diagnostics travel separately from the
            // user-facing message and can be copied without the prompt text.
            if let technical = fieldDefs.first(where: { $0["key"] == "technical" }),
               let value = technical["value"], !value.isEmpty {
                DisclosureGroup("Technical details") {
                    Text(value)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                }
                .font(.caption)
                .foregroundColor(.secondary)
                Button(copiedDetails ? "Copied" : "Copy Details") {
                    UIPasteboard.general.string = value
                    copiedDetails = true
                    Task {
                        try? await Task.sleep(nanoseconds: 2_000_000_000)
                        copiedDetails = false
                    }
                }
                .font(.caption)
            }
            if isMulti {
                ForEach(options.filter {
                    V3MultiSelectPromptAnswerPolicy.isMemberOption(
                        kind: kind, optionID: $0["id"] ?? "")
                }, id: \.self) { option in
                    Button {
                        toggle(option["id"] ?? "")
                    } label: {
                        HStack {
                            Image(systemName: selected.contains(option["id"] ?? "") ? "checkmark.circle.fill" : "circle")
                                .foregroundColor(.accentColor)
                            Text(option["label"] ?? "")
                        }
                    }
                    .disabled(isSubmitting || isSubmissionBlocked)
                }
                if kind == "revocation" {
                    Button("Keep Existing") {
                        respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("keep", fields: fields))
                    }
                        .disabled(isSubmitting || isSubmissionBlocked)
                } else {
                    if options.contains(where: { $0["id"] == "keepAllMainProfile" }) {
                        Button("Keep All (Use Main Profile)") {
                            respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("keepAllMainProfile", fields: fields))
                        }
                        .disabled(isSubmitting || isSubmissionBlocked)
                    }
                    Button("Keep All (Register Each Extension)") {
                        respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("keepAll", fields: fields))
                    }
                    .disabled(isSubmitting || isSubmissionBlocked)
                    Button("Remove All", role: .destructive) {
                        respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("removeAll", fields: fields))
                    }
                    .disabled(isSubmitting || isSubmissionBlocked)
                    if onCancel == nil && options.contains(where: { $0["id"] == "cancel" }) {
                        Button("Cancel", role: .cancel) {
                            respond(V3MultiSelectPromptAnswerPolicy.actionAnswer("cancel", fields: fields))
                        }
                        .disabled(isSubmitting)
                    }
                }
                Button(kind == "revocation" ? "Revoke Selected" : "Remove Selected", role: .destructive) {
                    respond(V3MultiSelectPromptAnswerPolicy.selectedMembersAnswer(
                        kind: kind, selectedIDs: selected, fields: fields))
                }
                .disabled(selected.isEmpty || isSubmitting || isSubmissionBlocked)
            } else {
                ForEach(options.filter { onCancel == nil || $0["id"] != "cancel" }, id: \.self) { option in
                    Button(option["label"] ?? "", role: (option["id"] == "cancel" || option["id"] == "deny") ? .cancel : .none) {
                        var answer = fields
                        answer["choice"] = option["id"] ?? ""
                        answer["action"] = option["id"] ?? ""
                        respond(answer)
                    }
                    .disabled(isSubmitting || (isSubmissionBlocked &&
                        !["cancel", "changeMethod"].contains(option["id"] ?? "")))
                }
            }
            }
            if let onCancel {
                Button(cancellationTitle, role: .cancel) { onCancel() }
                    .frame(minHeight: 44)
                    .disabled(cancellationDisabled)
                    .accessibilityIdentifier("signin.prompt.cancel")
            }
        }
        .onAppear {
            loadFields()
            Task { await loadRepairURL() }
        }
        .onChange(of: prompt["id"] as? String ?? "") { _ in
            loadFields()
            Task { await loadRepairURL() }
        }
    }
    private func loadFields() {
        fields = [:]
        selected = []
        for field in fieldDefs { fields[field["key"] ?? ""] = field["value"] ?? "" }
    }
    private func twoFactorOption(_ option: [String: String]) -> some View {
        Button {
            var answer = fields
            let id = option["id"] ?? ""
            answer["choice"] = id
            answer["action"] = id
            respond(answer)
        } label: {
            HStack {
                if (option["id"] ?? "").hasPrefix("phone:") {
                    Image(systemName: "phone.fill").foregroundColor(.accentColor)
                }
                Text(option["label"] ?? "")
                Spacer()
                Image(systemName: "chevron.right").font(.caption).foregroundColor(.secondary)
            }
        }
        .buttonStyle(.bordered)
        .disabled(isSubmitting || (isSubmissionBlocked &&
            !["cancel", "changeMethod"].contains(option["id"] ?? "")))
    }
    @ViewBuilder private func twoFactorCancelButton() -> some View {
        if onCancel == nil {
            Button("Cancel Sign In", role: .cancel) {
                respond(["action": "cancel", "choice": "cancel"])
            }
            .disabled(isSubmitting)
        }
    }
    private func binding(_ key: String) -> Binding<String> {
        Binding(get: { fields[key] ?? "" }, set: { fields[key] = $0 })
    }
    private func loadRepairURL() async {
        repairURL = nil
        guard kind == "accountRepair",
              let rawURL = fieldDefs.first(where: { $0["key"] == "url" })?["value"] else { return }
        repairURL = V3AuthRepairURLPolicy.openableURL(rawURL)
    }
    private func toggle(_ id: String) {
        if selected.contains(id) { selected.remove(id) } else { selected.insert(id) }
    }
    private func submit(choice: String) {
        var answer = fields
        answer["choice"] = choice
        respond(answer)
    }
    private func respond(_ answer: [String: String]) {
        // The view does not own this transition. Both call sites pass the parent
        // store's own submission flag as the binding, and the parent admits the
        // answer through that same flag: the auth store requires `!isSubmitting`
        // before it will dispatch, and the operation store sends opAnswer
        // unconditionally. Setting the flag here therefore ran admission against
        // state the tap itself had just created, so every answer was refused and
        // nothing was dispatched. This guard still stops a repeat tap within one
        // runloop, where the parent has not been able to update the binding yet;
        // the authoritative transition belongs to the parent alone.
        guard !isSubmitting else { return }
        onAnswer(answer)
    }
}
