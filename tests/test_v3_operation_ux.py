"""Regression coverage for the v3 USER-FEEDBACK / OPERATION-STATE audit.

Every user-triggered asynchronous or mutating action must acknowledge,
show working state, reach a visible terminal result, guard duplicates,
and reload authoritative state. No silent dismissals, no dead Retry
buttons, no raw numeric error codes as user messages.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"


def shell():
    return SHELL.read_text(encoding="utf-8")


def runtime():
    return RUNTIME.read_text(encoding="utf-8")


def operation_sheet():
    text = shell()
    start = text.index("struct V3OperationSheet")
    end = text.index("struct V3PromptSection", start)
    return text[start:end]


def status_store():
    text = shell()
    start = text.index("final class V3SideStoreStatusStore")
    end = text.index("struct V3SideStoreApp", start)
    return text[start:end]


class SheetLifecycleTests(unittest.TestCase):
    def test_success_stays_visible_until_done(self):
        sheet = operation_sheet()
        apply = sheet[sheet.index("private func apply"):]
        completed = apply[apply.index('case "completed":'):]
        completed = completed[:completed.index("case ", 10)]
        self.assertIn("completed successfully", completed)
        self.assertNotIn("dismiss()", completed)

    def test_retry_resets_started_flag(self):
        sheet = operation_sheet()
        reset = sheet[sheet.index("private func reset"):]
        reset = reset[:reset.index("\n    }\n") + 6]
        self.assertIn("started = false", reset)
        self.assertIn("Retry", sheet)
        retry = sheet[sheet.index('"Retry"') - 400:sheet.index('"Retry"') + 200]
        self.assertIn("reset()", retry)
        self.assertIn("start()", retry)

    def test_preparing_state_before_session(self):
        sheet = operation_sheet()
        self.assertIn("Preparing...", sheet)

    def test_cancelled_is_terminal_and_visible(self):
        sheet = operation_sheet()
        apply = sheet[sheet.index("private func apply"):]
        cancelled = apply[apply.index('case "cancelled"'):]
        cancelled = cancelled[:cancelled.index("case ", 10)]
        self.assertIn("message =", cancelled)
        self.assertNotIn("dismiss()", cancelled)

    def test_single_opstart_driver(self):
        sheet = operation_sheet()
        self.assertEqual(sheet.count('operation: "opStart"'), 1)


class InstallHandoffTests(unittest.TestCase):
    def test_handoff_deferred_past_picker_dismissal(self):
        text = shell()
        fn = text[text.index("func stageSharedIPA"):]
        fn = fn[:fn.index("struct V3SideStoreApp")]
        self.assertIn("Task { @MainActor in", fn)
        self.assertIn('perform("installSharedIPA"', fn)

    def test_install_button_disabled_while_busy(self):
        text = shell()
        view = text[text.index("struct V3InstallButton"):]
        view = view[:view.index("\n}\n") + 3]
        self.assertIn("presentation != nil", view)


class StoreFeedbackTests(unittest.TestCase):
    def test_mutations_accept_snapshot_and_release_loading_first(self):
        store = status_store()
        for operation in ("signOut", "syncAppIDs", "clearCache", "refreshSources"):
            fn = store[store.index("func %s(" % operation):]
            fn = fn[:fn.index("\n    }\n") + 6]
            # Strip comments: only executable statements count here.
            code = "\n".join(line for line in fn.splitlines()
                             if not line.strip().startswith("//"))
            self.assertIn("accept(try await", code, operation)
            self.assertIn("loading = false", code, operation)
            # reload() must run after the busy state is released.
            self.assertLess(code.index("loading = false"), code.index("reload()"), operation)

    def test_terminal_notices(self):
        store = status_store()
        self.assertIn('"Signed out successfully."', store)
        self.assertIn('"App IDs synced."', store)
        self.assertIn('"Download cache cleared."', store)
        self.assertIn('"Sources updated."', store)
        self.assertIn('"JIT enabled."', store)
        self.assertIn("@Published var notice", store)

    def test_notice_alert_exists(self):
        self.assertIn("status.notice", shell())

    def test_sign_out_hidden_when_signed_out(self):
        text = shell()
        view = text[text.index("struct V3AccountSettings"):]
        view = view[:view.index('Section("Device")')]
        # Both gated blocks exist; each sits directly above its control.
        self.assertEqual(view.count("if !status.needsSignIn {"), 2)
        signout = view[view.index('"Sign Out"') - 500:view.index('"Sign Out"') + 100]
        self.assertIn("needsSignIn", signout)

    def test_no_duplicate_sign_in_actions(self):
        text = shell()
        view = text[text.index("struct V3AccountSettings"):]
        view = view[:view.index('Section("Device")')]
        self.assertNotIn("Sign In / Re-authenticate", view)
        self.assertIn('"Re-authenticate"', view)
        reauth = view[view.index('"Re-authenticate"') - 700:view.index('"Re-authenticate"') + 100]
        self.assertIn("needsSignIn", reauth)


class ProvisioningClassificationTests(unittest.TestCase):
    CASES = ("unknown", "invalidParameters", "incorrectCredentials", "noTeams",
             "appSpecificPasswordRequired", "invalidDeviceID",
             "deviceAlreadyRegistered", "invalidCertificateRequest",
             "certificateDoesNotExist", "invalidAppIDName",
             "invalidBundleIdentifier", "bundleIdentifierUnavailable",
             "appIDDoesNotExist", "maximumAppIDLimitReached", "invalidAppGroup",
             "appGroupDoesNotExist", "invalidProvisioningProfileIdentifier",
             "provisioningProfileDoesNotExist",
             "requiresTwoFactorAuthentication", "userCancelled",
             "incorrectVerificationCode", "authenticationHandshakeFailed",
             "invalidAnisetteData", "tooManyCertificates", "tooManyAttempts",
             "accountRepairRequired", "invalid2FAResponse")

    def guidance(self):
        text = runtime()
        fn = text[text.index("func v3ProvisioningGuidance"):]
        fn = fn[:fn.index("\n}\n") + 3]
        return fn

    def test_every_case_handled_explicitly(self):
        fn = self.guidance()
        for case in self.CASES:
            self.assertIn("case .%s" % case, fn, case)
        self.assertIn("@unknown default", fn)

    def test_no_numeric_code_classification(self):
        text = runtime()
        start = text.index("func resolveProvisioningError")
        # The resolver itself must not format domain/code into user text;
        # numeric codes live only in askProvisioningRetry's technical field.
        resolver = text[start:text.index("private func askProvisioningRetry", start)]
        self.assertNotIn("Provisioning reported an issue (", resolver)
        self.assertNotIn("native.domain", resolver)
        self.assertNotIn("native.code", resolver)

    def test_cancellation_never_prompts(self):
        text = runtime()
        fn = text[text.index("func resolveProvisioningError"):]
        fn = fn[:fn.index("private func askProvisioningRetry")]
        self.assertIn("if error is CancellationError { return .cancel }", fn)
        self.assertIn("case .userCancelled = portal { return .cancel }", fn)

    def test_key_messages(self):
        fn = self.guidance()
        self.assertIn("Apple did not accept the Apple ID or password.", fn)
        self.assertIn("No Apple Developer team is available", fn)
        self.assertIn("already registered with the selected developer team", fn)
        self.assertIn("reached its development certificate limit", fn)
        self.assertIn("reached its App ID limit", fn)
        self.assertIn("temporarily limiting", fn)
        self.assertIn("Valid Anisette data could not be obtained.", fn)
        self.assertIn("requires attention on this account", fn)
        self.assertIn("verification code was not accepted", fn)
        self.assertIn("Two-factor authentication is required", fn)

    def test_no_password_guidance_for_provisioning(self):
        # #31 stays intact: provisioning failures must not be collapsed into
        # wrong-password guidance unless the typed error proves credentials.
        fn = self.guidance()
        self.assertNotIn("Wrong password", fn)
        self.assertNotIn("Check the Apple ID and password", fn)

    def test_technical_details_travel_separately(self):
        text = runtime()
        fn = text[text.index("private func askProvisioningRetry"):]
        fn = fn[:fn.index("\n    }\n") + 6]
        self.assertIn("domain=", fn)
        self.assertIn("area=provisioning", fn)
        self.assertIn("correlation=", fn)
        self.assertIn('"technical"', fn)


class PromptTechnicalTests(unittest.TestCase):
    def test_technical_field_renders_as_caption_not_input(self):
        text = shell()
        section = text[text.index("struct V3PromptSection"):]
        section = section[:section.index("final class V3AuthStore")]
        self.assertIn('"technical"', section)
        self.assertIn("Technical details", section)
        self.assertIn("Copy Details", section)

    def test_technical_value_is_selectable(self):
        text = shell()
        self.assertIn(".textSelection(.enabled)", text)


class SourcesFeedbackTests(unittest.TestCase):
    def test_add_busy_and_success(self):
        text = shell()
        self.assertIn("addBusy", text)
        self.assertIn("Adding Source...", text)
        self.assertIn("Source added.", text)

    def test_remove_busy_and_success(self):
        text = shell()
        self.assertIn("removeBusy", text)
        self.assertIn("Removing source...", text)
        self.assertIn("Source removed.", text)


class CertificatesFeedbackTests(unittest.TestCase):
    def test_mutations_have_busy_disabled_success(self):
        text = shell()
        view = text[text.index("struct V3CertificatesView"):]
        view = view[:view.index("struct V3DeveloperServicesView")]
        self.assertIn("Working...", view)
        self.assertIn("Loading Portal Certificates...", view)
        self.assertIn("Active certificate updated.", view)
        self.assertIn("Certificate deleted.", view)
        self.assertIn("Certificate revoked.", view)
        self.assertIn("Certificate requested.", view)
        self.assertIn(".disabled(!busy.isEmpty)", view)


class SettingsRollbackTests(unittest.TestCase):
    def test_store_restores_previous_on_failure(self):
        text = shell()
        store = text[text.index("final class V3SettingsStore"):]
        store = store[:store.index("struct V3ToggleRow")]
        self.assertIn("let previous = bools[key]", store)
        self.assertIn("let previous = strings[key]", store)
        self.assertIn("let previous = ints[key]", store)
        self.assertIn("removeValue(forKey: key)", store)

    def test_bool_row_rolls_back_toggle(self):
        text = shell()
        row = text[text.index("struct V3BoolSettingRow"):]
        row = row[:row.index("private struct V3StatusStoreKey")]
        self.assertIn("value = !newValue", row)


class CopyFeedbackTests(unittest.TestCase):
    def test_copy_buttons_confirm(self):
        self.assertGreaterEqual(shell().count('"Copied"'), 3)


class ReloadLabelTests(unittest.TestCase):
    def test_reload_status_is_visible_text(self):
        text = shell()
        home = text[text.index("private struct V3HomeView"):]
        self.assertIn('Label("Reload Status"', home)


class MiscBusyStateTests(unittest.TestCase):
    def test_dev_reload_disabled_while_loading(self):
        text = shell()
        view = text[text.index("struct V3DeveloperServicesView"):]
        view = view[:view.index("struct V3FilePicker")]
        self.assertIn("Loading Developer Data...", view)
        self.assertIn(".disabled(loading)", view)

    def test_health_recheck_busy(self):
        text = shell()
        view = text[text.index("struct V3HealthView"):]
        view = view[:view.index("struct V3BackupsView")]
        self.assertIn("Checking...", view)
        self.assertIn(".disabled(checking)", view)

    def test_anisette_remote_busy_and_notice(self):
        text = shell()
        view = text[text.index("struct V3AnisetteView"):]
        view = view[:view.index("struct V3SideSignView")]
        self.assertIn("remoteBusy", view)
        self.assertIn("synced.", view)
        self.assertIn("reset.", view)

    def test_sidesign_busy_and_notice(self):
        text = shell()
        view = text[text.index("struct V3SideSignView"):]
        view = view[:view.index("struct V3CustomizationsView")]
        self.assertIn("Saving...", view)
        self.assertIn("saved.", view)
        self.assertIn("reset.", view)
        self.assertIn("imported.", view)
        self.assertIn(".disabled(busy)", view)

    def test_backups_busy(self):
        text = shell()
        view = text[text.index("struct V3BackupsView"):]
        view = view[:view.index("struct V3SideJITView")]
        self.assertIn("Exporting...", view)
        self.assertIn("Importing...", view)


if __name__ == "__main__":
    unittest.main()
