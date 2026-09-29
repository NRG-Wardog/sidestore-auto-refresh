"""Exercise pinned patch transactions and the actual shipped wire decoder."""
import importlib.util
import hashlib
import json
import os
import plistlib
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch as mock

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


service = module("patch_v3_service")
shell = module("patch_v3_unified_shell")
refresh = module("patch_livecontainer_autorefresh")
results = module("patch_refresh_result_bridge")
embedded_keychain = module("patch_embedded_keychain")


class ServicePatchTests(unittest.TestCase):
    @staticmethod
    def swift_declaration(source, signature):
        start = source.index(signature)
        opening = source.index("{", start)
        depth = 0
        for index in range(opening, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    return source[start:index + 1]
        raise AssertionError(f"unterminated Swift declaration: {signature}")

    def test_connection_config_is_generated_as_backend_code_not_a_view_model(self):
        with tempfile.TemporaryDirectory() as name:
            roots = self.fixture(Path(name))
            self.apply(roots)
            side = roots[1]
            project = (side / "AltStore.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
            side_anchor = project.index("A8EECF492F4B195000F2436D")
            member_start = project.index("membershipExceptions = (", side_anchor)
            member_end = project.index(");", member_start)
            members = project[member_start:member_end]
            self.assertIn('"Views/Settings/Advanced/Connection/ConnectionConfig.swift"', members)
            self.assertNotIn('"Core/DeviceApi/ConnectionConfig.swift"', members)

            retired = (side / "SideStore/Views/Settings/Advanced/Connection/ConnectionConfig.swift").read_text(encoding="utf-8")
            backend = (side / "SideStore/Core/DeviceApi/ConnectionConfig.swift").read_text(encoding="utf-8")
            wrapper = (side / "SideStore/Core/DeviceApi/MinimuxerWrapper.swift").read_text(encoding="utf-8")
            self.assertIn("V3_HEADLESS_CONNECTION_CONFIG_MOVED_V1", retired)
            self.assertNotIn("SwiftUI", retired)
            self.assertIn("V3_HEADLESS_BACKEND_CONNECTION_CONFIG_V1", backend)
            for ui_symbol in ("SwiftUI", "Combine", "ObservableObject", "@Published", "ActiveState", "formattedTunnel"):
                self.assertNotIn(ui_symbol, backend)
            side_source = Path(os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.environ.get("SIDESTORE_TEST_SOURCE"))
            pinned_model = (side_source / "SideStore/Views/Settings/Advanced/Connection/ConnectionConfig.swift").read_text(encoding="utf-8")
            pinned_extensions = pinned_model[pinned_model.index("extension UserDefaults {"):].strip()
            generated_extensions = backend[backend.index("extension UserDefaults {"):].strip()
            self.assertEqual(generated_extensions, pinned_extensions,
                             "move the pinned defaults key and port-validation semantics without alteration")
            self.assertIn("get { UserDefaults.standard.useLocalVPN }", backend)
            self.assertIn("getConnectionMode: { config.connectionMode }", wrapper)

    def test_connection_config_settings_update_is_live_for_captured_minimuxer_binding(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; generated connection settings harness runs in macOS CI")
        with tempfile.TemporaryDirectory() as name:
            roots = self.fixture(Path(name))
            self.apply(roots)
            side = roots[1]
            generated_config = (side / "SideStore/Core/DeviceApi/ConnectionConfig.swift").read_text(encoding="utf-8")
            wrapper = (side / "SideStore/Core/DeviceApi/MinimuxerWrapper.swift").read_text(encoding="utf-8")
            binding_match = re.search(r"getConnectionMode:\s*\{\s*([^{}]+?)\s*\}", wrapper)
            self.assertIsNotNone(binding_match, "the generated Minimuxer binding must expose its live getter")
            binding_expression = binding_match.group(1)
            self.assertEqual(binding_expression, "config.connectionMode")

            runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
            declarations_start = runtime.index("    static let boolSettings: Set<String> =")
            declarations_end = runtime.index("    static func settingsGet()", declarations_start)
            settings_declarations = runtime[declarations_start:declarations_end]
            settings_set = self.swift_declaration(runtime, "    static func settingsSet(payload: [String: Any]) throws")

            wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
            strict_bool = self.swift_declaration(wire, "    static func strictBool(_ value: Any?) -> Bool?")
            strict_int = self.swift_declaration(wire, "    static func strictInt(_ value: Any?) -> Int?")
            wire_defaults = f"enum V3WireContract {{\n{strict_bool}\n{strict_int}\n}}"

            side_source = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.environ.get("SIDESTORE_TEST_SOURCE")
            if not side_source:
                self.skipTest("pinned SideStore source unavailable")
            defaults_source = Path(side_source)
            defaults_file = subprocess.check_output([
                "git", "-C", str(defaults_source), "show",
                service.PINS[1] + ":AltStore/Core/Extensions/UserDefaults+AltStore.swift"],
                text=True, encoding="utf-8")
            local_vpn_default = self.swift_declaration(defaults_file, "    @objc var useLocalVPN: Bool")

            config_without_module_import = generated_config.replace("import Minimuxer\n", "")
            harness = f'''import Foundation
import CoreFoundation

enum DeviceConnectionMode: Equatable {{ case localVPN, remoteServer }}
enum AppConstants {{
    enum Connection {{ static let defaultRemoteServerIP = "192.0.2.1" }}
    enum Proxy {{ static let address = "127.0.0.1"; static let defaultPort: UInt16 = 62078 }}
}}
enum V3SideStoreServiceError: Error {{ case invalidRequest }}
final class WidgetDataManager {{
    static let shared = WidgetDataManager()
    var isVerboseLoggingEnabled = false
}}
extension UserDefaults {{
{local_vpn_default}
}}
{wire_defaults}
enum V3BackendCommands {{
{settings_declarations}
{settings_set}
}}

@main struct LiveConnectionSettingsHarness {{
    static func main() throws {{
        let defaults = UserDefaults.standard
        for key in ["TunnelOverridePeerIp", "RemoteServerIp", "WireGuardServerHost", "WireGuardServerPort"] {{
            defaults.removeObject(forKey: key)
        }}
        defaults.set(false, forKey: "useLocalVPN")
        let config = ConnectionConfig.shared
        precondition(config.overrideTunnelPeerIp.isEmpty)
        precondition(config.remoteServerIp == "192.0.2.1")
        precondition(config.wireguardServerHost == "127.0.0.1")
        precondition(config.wireguardServerPort == 62078)
        let getConnectionMode: () -> DeviceConnectionMode = {{ {binding_expression} }}
        precondition(getConnectionMode() == .remoteServer, "initial persisted mode must be observed")

        try V3BackendCommands.settingsSet(payload: ["key": "useLocalVPN", "bool": true])
        precondition(defaults.bool(forKey: "useLocalVPN"), "settingsSet must persist host writes")
        precondition(getConnectionMode() == .localVPN,
                     "an already-captured Minimuxer binding must observe the host's latest mode")

        let reconstructed = ConnectionConfig()
        precondition(reconstructed.useLocalVPN && reconstructed.connectionMode == .localVPN,
                     "reconstructed backend config must read the persisted mode")
        reconstructed.remoteServerIp = "198.51.100.9"
        reconstructed.overrideTunnelPeerIp = "198.51.100.11"
        reconstructed.wireguardServerHost = "198.51.100.10"
        reconstructed.wireguardServerPort = 62079
        let afterReconstruction = ConnectionConfig()
        precondition(afterReconstruction.remoteServerIp == "198.51.100.9")
        precondition(afterReconstruction.overrideTunnelPeerIp == "198.51.100.11")
        precondition(afterReconstruction.wireguardServerHost == "198.51.100.10")
        precondition(afterReconstruction.wireguardServerPort == 62079)
        print("V3_LIVE_CONNECTION_SETTINGS_PASS")
    }}
}}
'''
            program = config_without_module_import + "\n" + harness
            source = Path(name) / "main.swift"
            executable = Path(name) / "live-connection-settings"
            source.write_text(program, encoding="utf-8")
            compiled = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)],
                capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_LIVE_CONNECTION_SETTINGS_PASS", result.stdout)

    def test_legacy_pipeline_bundle_prompt_is_headless_and_idempotent(self):
        side_source = Path(os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or ROOT / ".audit/v3-side-upstream")
        source_path = side_source / "SideStore/Handlers/PipelineHandler.swift"
        if not source_path.is_file():
            self.skipTest("Pinned SideStore PipelineHandler source unavailable")
        source = source_path.read_text(encoding="utf-8")
        patched = service.headless_pipeline_handler(source)
        self.assertIn("V3_HEADLESS_BUNDLE_ID_PROMPT_V1", patched)
        self.assertIn("return (initialBundleID, true)", patched)
        self.assertNotIn("AppExtensionViewHostingController", patched)
        self.assertNotIn("ReviewPermissionsViewController", patched)
        component_path = "SideStore/Views/Components/CustomAppIDAlertViewController.swift"
        self.assertIn(component_path.split("/", 1)[1], service.HEADLESS_SIDESTORE_VIEW_FILES)
        component_source = subprocess.check_output(
            ["git", "-C", str(side_source), "show",
             service.PINS[1] + ":" + component_path], text=True, encoding="utf-8")
        self.assertIn("class AppendTeamIDCheckboxView", component_source)
        references = subprocess.check_output(
            ["git", "-C", str(side_source), "grep", "-n", "-F", "AppendTeamIDCheckboxView",
             service.PINS[1], "--", "*.swift"], text=True, encoding="utf-8").splitlines()
        self.assertEqual([line.split(":", 2)[1] for line in references], [
            "SideStore/Handlers/PipelineHandler.swift",
            component_path,
        ])
        pinned_project = subprocess.check_output(
            ["git", "-C", str(side_source), "show",
             service.PINS[1] + ":AltStore.xcodeproj/project.pbxproj"],
            text=True, encoding="utf-8")
        synced_group = pinned_project[pinned_project.index("A8EECF2A2F4B195000F2436D"):]
        self.assertIn("path = SideStore;", synced_group)
        sidestore_target = pinned_project[pinned_project.index("BFD247692284B9A500981D42 /* SideStore */ = {"):]
        self.assertIn("A8EECF2A2F4B195000F2436D /* SideStore */", sidestore_target)
        original_exceptions = pinned_project[pinned_project.index("A8EECF492F4B195000F2436D"):]
        original_membership = original_exceptions[
            original_exceptions.index("membershipExceptions = ("):
            original_exceptions.index(");")]
        self.assertNotIn('"Views/Components/CustomAppIDAlertViewController.swift"', original_membership)
        original_override = self.swift_declaration(
            source, "func resolveBundleIDOverride(initialBundleID: String)")
        patched_override = self.swift_declaration(
            patched, "func resolveBundleIDOverride(initialBundleID: String)")
        self.assertIn("AppendTeamIDCheckboxView", original_override)
        self.assertNotIn("AppendTeamIDCheckboxView", patched_override)
        self.assertEqual(service.headless_pipeline_handler(patched), patched)

    def test_app_manager_deactivate_app_limit_wrapper_is_ui_only_and_excluded(self):
        side_source = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not side_source:
            self.skipTest("Set EMBEDDED_SIDESTORE_TEST_SOURCE to the pinned source checkout")
        app_manager_path = "AltStore/Managing Apps/AppManager.swift"
        my_apps_path = "AltStore/My Apps/MyAppsViewController.swift"
        caller_matches = subprocess.check_output(
            ["git", "-C", side_source, "grep", "-n", "-F", "deactivateApps(",
             service.PINS[1], "--", "*.swift"], text=True, encoding="utf-8").splitlines()
        matching_paths = [line.split(":", 2)[1] for line in caller_matches]
        self.assertEqual(matching_paths, [app_manager_path, app_manager_path, my_apps_path])

        app_manager = subprocess.check_output(
            ["git", "-C", side_source, "show", service.PINS[1] + ":" + app_manager_path],
            text=True, encoding="utf-8")
        generated_app_manager = service.headless_app_manager_ui(app_manager)
        self.assertIn("V3_HEADLESS_APP_MANAGER_DEACTIVATE_APPLIMIT_WRAPPER_REMOVED_V1", generated_app_manager)
        self.assertNotIn("func deactivateApps(for:", generated_app_manager)
        self.assertNotIn("self.deactivateApps(for:", generated_app_manager)
        self.assertIn("func deactivate(_ installedApp: InstalledApp", generated_app_manager)
        self.assertIn("performSingleOperation(.deactivate(installedApp)", generated_app_manager)
        self.assertIn("func activate(_ installedApp: InstalledApp", generated_app_manager)
        self.assertIn("performSingleOperation(.activate(installedApp)", generated_app_manager)
        self.assertEqual(service.headless_app_manager_ui(generated_app_manager), generated_app_manager)

        project = subprocess.check_output(
            ["git", "-C", side_source, "show", service.PINS[1] + ":AltStore.xcodeproj/project.pbxproj"],
            text=True, encoding="utf-8")
        altstore_group = project[project.index("A8EEC8412F4B146A00F2436D"):]
        self.assertIn("path = AltStore;", altstore_group)
        sidestore_target = project[project.index("BFD247692284B9A500981D42 /* SideStore */ = {"):]
        self.assertIn("A8EEC8412F4B146A00F2436D /* AltStore */", sidestore_target)
        source_exceptions = project[project.index("A8EEC8CB2F4B146B00F2436D"):]
        source_members = source_exceptions[source_exceptions.index("membershipExceptions = ("):
                                          source_exceptions.index(");")]
        self.assertNotIn('"Managing Apps/AppManager.swift"', source_members)
        self.assertNotIn('"My Apps/MyAppsViewController.swift"', source_members)

        generated_project = service.headless_project(project)
        generated_exception = generated_project[generated_project.index("A8EEC8CB2F4B146B00F2436D"):]
        generated_members = generated_exception[generated_exception.index("membershipExceptions = ("):
                                                generated_exception.index(");")]
        self.assertIn('"My Apps/MyAppsViewController.swift"', generated_members)
        self.assertNotIn('"Managing Apps/AppManager.swift"', generated_members)
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        self.assertIn('case "activate": operation = .activate(app)', runtime)
        self.assertIn('case "deactivate": operation = .deactivate(app)', runtime)
        self.assertIn("AppManager.shared.pipelineRunner.performSingleOperation(operation", runtime)

    def test_persisted_side_sign_errors_drop_provider_text_from_core_data_history(self):
        side_source = Path(os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE") or ROOT / ".audit/v3-side-upstream")
        manager_path = "AltStore/Managing Apps/AppManager.swift"
        manager = subprocess.check_output(
            ["git", "-C", str(side_source), "show", service.PINS[1] + ":" + manager_path],
            text=True, encoding="utf-8")
        generated_manager = service.headless_app_manager_ui(manager)
        self.assertEqual(service.headless_app_manager_ui(generated_manager), generated_manager)
        log_method = self.swift_declaration(generated_manager, "func log(_ error: Error")
        self.assertIn("V3PersistedErrorSanitizer.sanitize(error as NSError)", log_method)
        self.assertNotIn("sanitizedForSerialization()", log_method)

        history_path = "AltStore/Core/Model/RefreshAttempt.swift"
        history = subprocess.check_output(
            ["git", "-C", str(side_source), "show", service.PINS[1] + ":" + history_path],
            text=True, encoding="utf-8")
        generated_history = service.headless_refresh_attempt_error_privacy(history)
        self.assertEqual(service.headless_refresh_attempt_error_privacy(generated_history), generated_history)
        self.assertIn("V3PersistedErrorSanitizer.refreshHistoryDescription(for: error)", generated_history)
        self.assertNotIn("error.localizedDescription", generated_history)

        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; generated sanitizer execution runs in macOS CI")

        helper = self.swift_declaration(generated_manager, "enum V3PersistedErrorSanitizer")
        harness = '''import Foundation
''' + helper + '''

let secret = "SECRET_RAW_2FA_OR_PROVIDER_RESPONSE"
let nested = NSError(domain: "SideSignErrorDomain", code: 7, userInfo: [NSLocalizedDescriptionKey: secret])
let original = NSError(domain: "SideSignErrorDomain", code: -1005, userInfo: [
    NSLocalizedDescriptionKey: secret,
    NSLocalizedFailureReasonErrorKey: secret,
    NSDebugDescriptionErrorKey: secret,
    "rawProviderBody": secret,
    NSUnderlyingErrorKey: nested
])
let stored = V3PersistedErrorSanitizer.sanitize(original)
let unknownDomain = V3PersistedErrorSanitizer.sanitize(
    NSError(domain: "private-" + secret, code: 901, userInfo: [NSLocalizedDescriptionKey: secret])
)
let persisted: [String: Any] = [
    "domain": stored.domain,
    "code": stored.code,
    "userInfo": stored.userInfo,
    "refreshHistory": V3PersistedErrorSanitizer.refreshHistoryDescription(for: original),
    "unknownDomain": unknownDomain.domain,
    "unknownCode": unknownDomain.code,
    "unknownUserInfo": unknownDomain.userInfo
]
let data = try PropertyListSerialization.data(fromPropertyList: persisted, format: .xml, options: 0)
let decoded = try PropertyListSerialization.propertyList(from: data, options: [], format: nil) as! [String: Any]
let decodedInfo = decoded["userInfo"] as! [String: Any]
precondition(decoded["domain"] as? String == "SideSignErrorDomain")
precondition((decoded["code"] as? Int) == -1005)
precondition(Set(decodedInfo.keys) == Set([NSLocalizedDescriptionKey]))
precondition(decodedInfo[NSLocalizedDescriptionKey] as? String == V3PersistedErrorSanitizer.safeDescription)
precondition((decoded["refreshHistory"] as? String) == V3PersistedErrorSanitizer.safeDescription)
precondition(decoded["unknownDomain"] as? String == "V3RedactedErrorDomain")
precondition((decoded["unknownCode"] as? Int) == 0)
precondition(Set((decoded["unknownUserInfo"] as! [String: Any]).keys) == Set([NSLocalizedDescriptionKey]))
precondition(!String(data: data, encoding: .utf8)!.contains(secret))
print("persisted provider error text redacted")
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "PersistedErrorPrivacyHarness.swift"
            executable = Path(directory) / "persisted-error-privacy-harness"
            source.write_text(harness, encoding="utf-8")
            subprocess.run([compiler, str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
            completed = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn("persisted provider error text redacted", completed.stdout)

    def test_pairing_and_sidejit_presenters_are_unreferenced_in_headless_target(self):
        side_source = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not side_source:
            self.skipTest("Set EMBEDDED_SIDESTORE_TEST_SOURCE to the pinned source checkout")
        ref = service.PINS[1]

        def pinned_matches(token):
            output = subprocess.check_output(
                ["git", "-C", side_source, "grep", "-n", "-F", token, ref, "--", "*.swift"],
                text=True, encoding="utf-8")
            return [line.split(":", 2)[1] for line in output.splitlines()]

        launch = "AltStore/LaunchViewController.swift"
        boot_path = "SideStore/AppBootManager.swift"
        jit_path = "SideStore/Core/JIT/SideJITManager.swift"
        pairing_path = "SideStore/Core/Pairing/PairingFileManager.swift"
        self.assertEqual(set(pinned_matches("promptForPairing(")), {launch, boot_path})
        self.assertEqual(set(pinned_matches("needsPairingPrompt")), {launch, boot_path})
        self.assertEqual(set(pinned_matches("needsSideJITPrompt")), {launch, boot_path})
        self.assertEqual(set(pinned_matches("presentJITPrompt(")), {launch, jit_path})
        self.assertEqual(set(pinned_matches("isSideJITServerDetected(")), {boot_path, jit_path})
        self.assertEqual(set(pinned_matches("presentPairingFileAlert(")), {boot_path, pairing_path})
        self.assertEqual(set(pinned_matches("showPairingWarningAndProceed(")), {pairing_path})
        self.assertEqual(set(pinned_matches("importPairingFile(presentingVC:")), {pairing_path})

        def show(path):
            return subprocess.check_output(["git", "-C", side_source, "show", f"{ref}:{path}"],
                                           text=True, encoding="utf-8")

        boot_original = show(boot_path)
        boot_generated = service.headless_app_boot_manager(boot_original)
        self.assertEqual(service.headless_app_boot_manager(boot_generated), boot_generated)
        self.assertNotIn("@MainActor", boot_generated)
        for removed in ("needsPairingPrompt", "needsSideJITPrompt", "promptForPairing(",
                        "presentPairingFileAlert", "isSideJITServerDetected", "UIViewController", "import UIKit"):
            self.assertNotIn(removed, boot_generated)
        original_start = self.swift_declaration(boot_original, "public nonisolated func startMinimuxer(")
        expected_start = "\n".join(
            line for line in original_start.splitlines() if "self.needsPairingPrompt =" not in line)
        actual_start = self.swift_declaration(boot_generated, "public nonisolated func startMinimuxer(")
        self.assertEqual(actual_start, expected_start)
        self.assertIn("public nonisolated func performBootSequence() async", boot_generated)
        self.assertIn("SideJITManager.shared.askForNetwork()", boot_generated)
        self.assertIn("PairingFileManager.shared.fetchPairingFile()", boot_generated)
        self.assertIn("V3_HEADLESS_BOOT_SIDEJIT_DETECTION_REMOVED_V1", boot_generated)

        jit_original = show(jit_path)
        jit_generated = service.headless_sidejit_manager(jit_original)
        self.assertEqual(service.headless_sidejit_manager(jit_generated), jit_generated)
        self.assertNotIn("@MainActor", jit_generated)
        for removed in ("presentJITPrompt", "isSideJITServerDetected", "UIAlertController", "UIViewController", "import UIKit"):
            self.assertNotIn(removed, jit_generated)
        for retained in ("public func resolveServerURL() async -> String", "public func askForNetwork() async",
                         "resolveAddressIfNeeded", "inet_pton"):
            self.assertIn(retained, jit_generated)
        enable_jit = show("SideStore/Core/Operations/StandaloneOperations/EnableJITOperation.swift")
        self.assertIn("SideJITManager.shared.resolveServerURL()", enable_jit)

        pairing_original = show(pairing_path)
        pairing_generated = service.headless_pairing_file_manager(pairing_original)
        self.assertEqual(service.headless_pairing_file_manager(pairing_generated), pairing_generated)
        for removed in ("UIViewController", "UIDocumentPicker", "UIAlertController", "UTType",
                        "UniformTypeIdentifiers", "import UIKit", "importPairingFile(presentingVC:"):
            self.assertNotIn(removed, pairing_generated)
        for signature in ("nonisolated var pairingUDID:", "nonisolated func fetchPairingFile()",
                          "func savePairingFile(contents: String)"):
            self.assertEqual(self.swift_declaration(pairing_original, signature),
                             self.swift_declaration(pairing_generated, signature))
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        self.assertIn("PairingFileManager.shared.savePairingFile(contents: contents)", runtime)
        self.assertIn("PairingFileManager.shared.fetchPairingFile()", runtime)
        minimuxer = show("SideStore/Core/DeviceApi/MinimuxerWrapper.swift")
        self.assertIn("PairingFileManager.shared.pairingUDID", minimuxer)

        project = show("AltStore.xcodeproj/project.pbxproj")
        sidestore_group = project[project.index("A8EECF2A2F4B195000F2436D"):]
        self.assertIn("path = SideStore;", sidestore_group)
        side_target = project[project.index("BFD247692284B9A500981D42 /* SideStore */ = {"):]
        self.assertIn("A8EECF2A2F4B195000F2436D /* SideStore */", side_target)
        side_exceptions = project[project.index("A8EECF492F4B195000F2436D"):]
        side_members = side_exceptions[side_exceptions.index("membershipExceptions = ("):
                                      side_exceptions.index(");")]
        for path in ("AppBootManager.swift", "Core/JIT/SideJITManager.swift", "Core/Pairing/PairingFileManager.swift"):
            self.assertNotIn(f'"{path}"', side_members)
        generated_project = service.headless_project(project)
        altstore_exceptions = generated_project[generated_project.index("A8EEC8CB2F4B146B00F2436D"):]
        altstore_members = altstore_exceptions[altstore_exceptions.index("membershipExceptions = ("):
                                              altstore_exceptions.index(");")]
        self.assertIn('"LaunchViewController.swift"', altstore_members)

    def test_function_removal_consumes_only_its_actor_attribute(self):
        source = '''@MainActor
func removePresenter() { }

@MainActor
func retainedActorMethod() { }
'''
        generated = service.remove_swift_function_with_actor(
            source, "func removePresenter()", "V3_REMOVED_PRESENTER", "actor removal regression")
        self.assertEqual(generated.count("@MainActor"), 1)
        self.assertIn("// V3_REMOVED_PRESENTER", generated)
        self.assertIn("@MainActor\nfunc retainedActorMethod()", generated)

    def test_log_formatter_patch_replaces_the_complete_final_swift_function(self):
        source = "import Foundation\npublic func formatLogMessage(_ message: String) -> String { return message }\n"
        patched = service.headless_safe_log_format(source)
        self.assertEqual(service.headless_safe_log_format(patched), patched)
        self.assertEqual(patched.count("V3_SAFE_LOG_FORMAT_V1"), 1)
        self.assertNotIn("return message", patched)
        with self.assertRaises(SystemExit):
            service.headless_safe_log_format(source + "func requiredBackendHelper() {}\n")

    def test_headless_service_template_does_not_import_swiftui(self):
        service_template = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        self.assertNotIn("import SwiftUI", service_template)
        self.assertIn("private enum V3OperationRecoveryJournal", service_template)

    def test_legacy_nuke_cache_cleanup_is_file_scoped_and_repeatable(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; cache cleanup behavior runs in macOS CI")
        with tempfile.TemporaryDirectory() as name:
            roots = self.fixture(Path(name))
            self.apply(roots)
            clear_source = (roots[1] / "SideStore/Core/Operations/StandaloneOperations/ClearAppCacheOperation.swift").read_text()
            start = clear_source.index("// V3_LEGACY_IMAGE_CACHE_CLEANUP_V1")
            end = clear_source.index("\nstruct BatchError", start)
            helper = clear_source[start:end]
            harness = '''
import Foundation
@main struct LegacyImageCacheCleanupHarness {
    static func main() throws {
        let manager = FileManager.default
        let root = manager.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        let cache = root.appendingPathComponent("io.sidestore.Nuke", isDirectory: true)
        let neighbor = root.appendingPathComponent("keep-me", isDirectory: true)
        try manager.createDirectory(at: cache, withIntermediateDirectories: true)
        try manager.createDirectory(at: neighbor, withIntermediateDirectories: true)
        try Data([1]).write(to: cache.appendingPathComponent("entry"))
        try Data([2]).write(to: neighbor.appendingPathComponent("entry"))
        try V3LegacyImageCacheCleanup.clear(cachesDirectory: root, fileManager: manager)
        precondition(!manager.fileExists(atPath: cache.path))
        precondition(manager.fileExists(atPath: neighbor.appendingPathComponent("entry").path))
        try V3LegacyImageCacheCleanup.clear(cachesDirectory: root, fileManager: manager)
        try V3LegacyImageCacheCleanup.clear(cachesDirectory: nil, fileManager: manager)
        try? manager.removeItem(at: root)
        print("V3_LEGACY_IMAGE_CACHE_CLEANUP_PASS")
    }
}
'''
            with tempfile.TemporaryDirectory() as build:
                source = Path(build) / "main.swift"
                executable = Path(build) / "cache-cleanup"
                source.write_text("import Foundation\n" + helper + "\n" + harness, encoding="utf-8")
                compiled = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)],
                    capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("V3_LEGACY_IMAGE_CACHE_CLEANUP_PASS", result.stdout)

    def test_every_pinned_nuke_import_is_excluded_or_headless_adapted(self):
        source_value = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.getenv("SIDESTORE_TEST_SOURCE")
        if not source_value:
            self.skipTest("pinned SideStore source unavailable")
        source = Path(source_value)
        project_text = (source / "AltStore.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
        generated_project = service.headless_project(project_text)

        def excluded_members(anchor):
            anchor_start = generated_project.index(anchor)
            member_start = generated_project.index("membershipExceptions = (", anchor_start)
            member_end = generated_project.index(");", member_start)
            return set(re.findall(r'"([^"\\]+)"', generated_project[member_start:member_end]))

        app_excluded = excluded_members("A8EEC8CB2F4B146B00F2436D")
        side_excluded = excluded_members("A8EECF492F4B195000F2436D")
        unaccounted = []
        for root_name, excluded, adapted in (
            ("AltStore", app_excluded, {"AppDelegate.swift"}),
            ("SideStore", side_excluded, {"Core/Operations/StandaloneOperations/ClearAppCacheOperation.swift"}),
        ):
            root = source / root_name
            for path in root.rglob("*.swift"):
                if "import Nuke" not in path.read_text(encoding="utf-8"):
                    continue
                relative = path.relative_to(root).as_posix()
                if relative not in excluded and relative not in adapted:
                    unaccounted.append(f"{root_name}/{relative}")
        self.assertEqual(unaccounted, [],
            "every Nuke import must be removed from the headless target or replaced by a backend adapter")

    def test_backend_auth_pair_decisions_use_one_keychain_snapshot(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        availability = runtime[runtime.index("func canResumeProvisioning()"):
            runtime.index("    var sessions: [String: Session]", runtime.index("func canResumeProvisioning()"))]
        self.assertIn("let credentials = AuthManager.shared.authenticationSnapshot", availability)
        self.assertIn("authenticated: credentials?.isAuthenticated == true", availability)
        self.assertIn("currentAppleID: credentials?.appleIDEmailAddress", availability)
        begin = runtime[runtime.index("func begin(deadline: Date,"):
            runtime.index("    func poll(id: String)", runtime.index("func begin(deadline: Date,"))]
        self.assertIn("let authCredentials = mode == .resumeProvisioning", begin)
        self.assertIn("authCredentials?.appleIDEmailAddress", begin)
        self.assertNotIn("AuthManager.shared.currentAppleID", begin)
        expire = runtime[runtime.index("func expire(id: String)"):
            runtime.index("    @discardableResult\n    func cancel(id: String)", runtime.index("func expire(id: String)"))]
        self.assertEqual(expire.count("AuthManager.shared.authenticationSnapshot"), 1)
        status = service[service.index("private func snapshot()"):
            service.index("\n}", service.index("private func snapshot()"))]
        self.assertIn("let authCredentials = AuthManager.shared.authenticationSnapshot", status)
        self.assertNotIn("AuthManager.shared.currentAppleID", status)

    def test_external_url_log_redaction_is_idempotent_and_omits_sensitive_values(self):
        scene = '\n'.join((
            'debugLog("[SceneDelegate] scene(_:openURLContexts:) called with URL: \\(context.url)")',
            'debugLog("[SceneDelegate] open(_:) called with URL: \\(context.url)")',
            'debugLog(finished)',
        ))
        url_handler = '\n'.join((
            'debugLog("[URLHandler] handle(_:) called with URL: \\(url.absoluteString)")',
            'debugLog("[URLHandler] Failed to parse URLComponents for \\(url)")',
            'debugLog("[URLHandler] Matched host: \\(host), path: \\(url.path.lowercased())")',
        ))
        for relative, original in (("AltStore/SceneDelegate.swift", scene),
                                   ("SideStore/DeepLinks/URLHandler.swift", url_handler)):
            redacted = service.redact_external_url_logs(original, relative)
            self.assertIn("V3_EXTERNAL_URL_LOG_REDACTION_V1", redacted)
            self.assertEqual(service.redact_external_url_logs(redacted, relative), redacted)
            self.assertNotIn("context.url)", redacted)
            self.assertNotIn("url.absoluteString)", redacted)
            self.assertNotIn("debugLog(finished)", redacted)

    def test_readiness_failure_bridge_has_one_canonical_combined_failure_adapter(self):
        helper = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        self.assertEqual(helper.count(
            "func combinedFailure(id: String, operation overrideOperation: String? = nil) -> CombinedFailure"), 1)

    def test_service_marks_only_database_readiness_not_ready_as_transient(self):
        service_template = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        policy = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        self.assertEqual(service_template.count("V3ServiceReadinessRetryPolicy.retryable("), 2)
        self.assertIn('guard typedNotReady, operation == "snapshot", stage == .serviceReadiness', policy)
        self.assertIn("code == .notReady else { return nil }", policy)
        self.assertIn('"payload": ["readinessOnly": true]',
                      (ROOT / "scripts/patch_combined_service_startup.py").read_text(encoding="utf-8"))
        self.assertIn('return ["ready": DatabaseManager.shared.isStarted]', service_template)

    def test_source_remove_and_pairing_import_accept_the_authoritative_response_snapshot(self):
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        remove = host[host.index("private func confirmRemove(id: String) async"):
                      host.index("struct V3CatalogApp", host.index("private func confirmRemove(id: String) async"))]
        pairing = host[host.index("private func importFile(_ url: URL) async", host.index("struct V3PairingView")):
                        host.index("final class V3SettingsStore", host.index("struct V3PairingView"))]
        self.assertIn("status.accept(result)", remove)
        self.assertIn("status.accept(result)", pairing)
        self.assertNotIn("status.reload()", remove)
        self.assertNotIn("status.reload()", pairing)

    def test_credentials_codes_and_backup_passphrases_use_secure_handoff_tokens(self):
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        service_template = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        handoff = (ROOT / "scripts/templates/v3_secret_handoff.swift").read_text(encoding="utf-8")
        for operation in ("authRespond", "opAnswer"):
            self.assertIn(f'operation: "{operation}"', host)
            self.assertNotIn('"answer": answer', host)
        self.assertIn("V3SecretHandoff.storeStringDictionary(answer)", host)
        self.assertIn("V3SecretHandoff.storeString(exportPassword)", host)
        self.assertIn("V3SecretHandoff.storeString(importPassword)", host)
        self.assertNotIn('payload: ["password": exportPassword', host)
        self.assertNotIn('payload: ["password": importPassword', host)
        self.assertIn("V3SecretHandoff.consumeStringDictionary(secretToken)", service_template)
        self.assertIn("V3SecretHandoff.consumeString(secretToken)", service_template)
        self.assertNotIn('payload["password"]', service_template)
        self.assertNotIn('payload["answer"]', service_template)
        self.assertIn("kSecAttrAccessibleWhenUnlockedThisDeviceOnly", handoff)
        self.assertIn("V3SecretHandoffRecord.lifetime", handoff)
        self.assertIn("hasSuffix(\".com.kdt.livecontainer.shared\")", handoff)
        shared_keychain = (ROOT / "scripts/templates/embedded_shared_keychain.swift").read_text(encoding="utf-8")
        self.assertIn("V3SecretHandoff.sharedKeychainAccessGroup()", shared_keychain)
        self.assertNotIn("accessGroup: appGroup", shared_keychain)

    def test_sidesign_headers_use_one_time_secret_tokens_not_raw_json(self):
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertIn('Set(payload.keys) == Set(["secretToken"])', wire)
        self.assertIn("V3SecretHandoff.consumeString(secretToken)", service)
        self.assertIn("V3BackendCommands.sidesignConfigToken()", service)
        self.assertIn("V3BackendCommands.sidesignExportToken()", service)
        self.assertIn('payload: ["secretToken": secretToken]', host)
        self.assertIn("V3SecretHandoff.storeString(config)", host)
        self.assertIn("let config = try await sidesignJSON()", runtime)
        self.assertIn("return try V3SecretHandoff.storeString(config)", runtime)
        self.assertNotIn('payload: ["config": config]', host)

    def test_anisette_server_selection_reloads_authoritative_active_state(self):
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        start = host.index("struct V3AnisetteView")
        end = host.index("struct V3SideSignView", start)
        view = host[start:end]
        selection = view[view.index('Button("Use This Server")'):]
        selection = selection[:selection.index("\n                        }")]
        self.assertIn('await store.setStringAndWait("menuAnisetteURL", server.address)', selection)
        self.assertIn("await reload()", selection)
        self.assertLess(selection.index("await store.setStringAndWait"),
                        selection.index("await reload()"))
        self.assertIn("if !store.message.isEmpty", selection)

    def test_shared_file_staging_is_purpose_scoped_bounded_and_expiring(self):
        handoff = (ROOT / "scripts/templates/v3_secret_handoff.swift").read_text(encoding="utf-8")
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        service_runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        for purpose in ("pairing", "sidesign", "accountImport"):
            self.assertIn(f'purpose: "{purpose}"', host)
            self.assertIn(f'purpose: "{purpose}"', service_runtime)
        self.assertIn("static let lifetime: TimeInterval = 60 * 60", handoff)
        self.assertIn("static let maximumPendingFiles = 16", handoff)
        self.assertIn("static let maximumPendingStoredBytes = 16_777_216", handoff)
        shared_files = handoff[handoff.index("enum V3SharedFileRecord {"):handoff.index("\nenum V3SecretHandoff {")]
        self.assertIn("removeLegacyDefaultsRecords", shared_files)
        self.assertNotIn("defaults.set(", shared_files)
        self.assertNotIn("set(payload", shared_files)
        self.assertNotIn("V3IPAStaging", shared_files)
        self.assertIn("V3IPAStaging.sideStoreContainerRoot()", host)
        self.assertIn("V3IPAStaging.sideStoreContainerRoot()", service_runtime)
        self.assertNotIn("V3SharedFile.", host + service_runtime)

    def test_transport_log_templates_keep_dynamic_errors_paths_and_endpoints_out_of_logs(self):
        gateway = (ROOT / "scripts/patch_sidestore_integration.py").read_text(encoding="utf-8")
        rust = (ROOT / "scripts/patch_coredevice_idevice.py").read_text(encoding="utf-8")
        for unsafe_log in (
            'TRANSPORT_CREATE_FAIL code=\\(code) subcode=\\(subCode) error=\\(message)',
            'selected_transport=FAILED_NO_VALID_TRANSPORT reason=\\(error.localizedDescription)',
            'FETCH_UDID_FAIL stage=transport reason=\\\\(error.localizedDescription)',
            'FETCH_UDID_FAIL stage=rsd_service reason=\\\\(error.localizedDescription)',
            'AFC_FILE_OPEN_START path=\\(path)',
            'SIDESTORE_INSTALL_REQUEST_START path=\\(path)',
            'HEARTBEAT_CONNECT_FAIL error={error}',
            'HEARTBEAT_POLO_FAIL error={error}',
            'HEARTBEAT_MARCO_FAIL error={error}',
        ):
            self.assertNotIn(unsafe_log, gateway + rust)
        # Internal error messages remain available to the classifier; only the
        # user-copyable logger is normalized.
        self.assertIn('IPA install failed: \\(message)', gateway)

    def test_headless_app_delegate_removes_unowned_ipa_and_source_deep_link_lifecycle(self):
        side = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        source = (Path(side) if side else ROOT / ".audit/v3-side-upstream") / "AltStore/AppDelegate.swift"
        if not source.is_file():
            self.skipTest("Pinned SideStore AppDelegate source unavailable")
        original = source.read_text(encoding="utf-8")
        patched = service.headless_app_open(original)
        self.assertNotIn("pendingImportIPAURL", patched)
        self.assertNotIn("importAppDeepLinkNotification", patched)
        self.assertNotIn("addSourceDeepLinkNotification", patched)
        self.assertNotIn("    var window: UIWindow?", patched)
        self.assertNotIn("self.window?.tintColor", patched)
        self.assertIn("appBackupDidFinish", patched)
        self.assertEqual(service.headless_app_open(patched), patched)
        scene = (Path(side) if side else ROOT / ".audit/v3-side-upstream") / "AltStore/SceneDelegate.swift"
        scene_patched = service.headless_scene_open(scene.read_text(encoding="utf-8"))
        self.assertNotIn("pendingImportIPAURL", scene_patched)
        self.assertNotIn("    var window: UIWindow?", scene_patched)
        self.assertNotIn("exportPairingFile", scene_patched)
        self.assertEqual(service.headless_scene_open(scene_patched), scene_patched)

    def test_generated_log_formatter_redacts_urls_identifiers_and_provider_bodies(self):
        side = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        source = (Path(side) if side else ROOT / ".audit/v3-side-upstream") / "SideStore/Core/Logging/SideStoreLogging.swift"
        if not source.is_file():
            self.skipTest("Pinned SideStore logging source unavailable")
        patched = service.headless_safe_log_format(source.read_text(encoding="utf-8"))
        self.assertEqual(service.headless_safe_log_format(patched), patched)
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        self.assertIn("return [\"tail\": formatLogMessage(rawTail)]", runtime)
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; generated formatter execution is a macOS CI check")
        with tempfile.TemporaryDirectory() as name:
            program = Path(name) / "main.swift"
            program.write_text(patched + "\n" +
                (ROOT / "tests/fixtures/v3_log_privacy_harness.swift").read_text(encoding="utf-8"),
                encoding="utf-8")
            executable = Path(name) / "log-privacy-tests"
            compiled = subprocess.run([compiler, "-parse-as-library", str(program), "-o", str(executable)],
                capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_LOG_PRIVACY_PASS", result.stdout)

    def test_generated_log_formatter_redacts_quoted_privacy_aliases(self):
        source = "public func formatLogMessage(_ message: String) -> String { return message }\n"
        generated = service.headless_safe_log_format(source)
        self.assertEqual(service.headless_safe_log_format(generated), generated)
        self.assertIn("session(?:_id)?", generated)
        self.assertIn("request_id", generated)
        self.assertIn("correlationID", generated)
        self.assertIn("authToken", generated)
        self.assertIn("xcodeToken", generated)
        self.assertIn("secret", generated)
        self.assertIn("credential", generated)
        self.assertIn("[redacted UUID]", generated)
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; generated formatter execution is a macOS CI check")
        harness = (ROOT / "tests/fixtures/v3_log_privacy_harness.swift").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as name:
            program = Path(name) / "main.swift"
            program.write_text("import Foundation\n" + generated + "\n" + harness, encoding="utf-8")
            executable = Path(name) / "log-alias-privacy-tests"
            compiled = subprocess.run([compiler, "-parse-as-library", str(program), "-o", str(executable)],
                capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_LOG_PRIVACY_PASS", result.stdout)

    def test_missing_auth_poll_session_is_typed_as_session_unavailable(self):
        source = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        poll = source[source.index('case "authPoll":'):source.index('case "authRespond":')]
        self.assertIn("safeCause: .authSessionUnavailable", poll)
        self.assertIn("operation: \"signIn\"", poll)
        self.assertIn("stage: .authentication", poll)
        self.assertIn("retryable: false", poll)

    def test_typed_service_failure_is_correlated_to_reply_request_id(self):
        source = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        receive = source[source.index("private func receive("):source.index("private func invalidRequestReply")]
        self.assertIn("structuredFailure.correlating(to: id).wire", receive)
        failure = (ROOT / "scripts/templates/combined_failure.swift").read_text(encoding="utf-8")
        self.assertIn("public func correlating(to id: String)", failure)

    def test_prompt_session_history_has_no_removed_single_identifier_reference(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bsession\.acceptedPromptID\b", runtime))
        self.assertGreaterEqual(runtime.count("session.acceptedPromptIDs"), 4)

    def test_service_admission_uses_typed_busy_cause_policy(self):
        source = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        self.assertIn("V3ServiceMutationBusyCausePolicy.safeCause", source)
        self.assertIn("authenticationActive: authenticationActive", source)
        self.assertIn("safeCause: .operationInProgress", source)
        self.assertIn("guard !V3HeadlessRuntime.shared.auth.hasActiveSession else { throw ServiceError.busy }", source)
        self.assertIn("responseCapacityAvailable: responseCapacityAvailable", source)
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
        self.assertIn("case CombinedFailure.SafeCause.responseCapacityUnavailable.rawValue:", primitives)

    def test_successful_service_replies_are_not_reparsed_for_fallback_logs(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        encode = service[service.index("private func encode(_ value:"):]
        encode = encode[:encode.index("private func run(")]
        self.assertIn("V3ResponseEncoder.encodeDetailed", encode)
        self.assertIn("encoded.fallbackToken", encode)
        self.assertNotIn("PropertyListSerialization.propertyList(from: data", encode)
        harness = (ROOT / "tests/fixtures/v3_catalog_response_encoding_harness.swift").read_text(encoding="utf-8")
        self.assertIn("detailedSuccess.fallbackToken == nil", harness)
        self.assertIn("detailedFailure.fallbackToken", harness)

    def test_inflight_request_id_replay_never_claims_operation_was_not_dispatched(self):
        service_source = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        replay = service_source[service_source.index("guard tasks[id] == nil else {"):]
        replay = replay[:replay.index("let mutation =")]
        self.assertNotIn('"operationNotDispatched"', replay)
        admission = service_source[service_source.index("guard V3ServiceMutationAdmissionPolicy.admits"):]
        admission = admission[:admission.index("if mutation { mutationID = id }")]
        self.assertIn('response["operationNotDispatched"] = true', admission)

    def fixture(self, directory):
        live_source = os.getenv("LIVE_CONTAINER_TEST_SOURCE")
        side_source = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not live_source or not side_source:
            self.skipTest("Set pinned source environment variables")
        roots = (directory / "live", directory / "side")
        files = (
            ["SideStoreSupport/" + name for name in ("XPCServer.h", "XPCServer.m", "XPCClient.m", "SideStore.swift", "SideStoreClient.swift")] +
            ["LiveContainerSwiftUI/" + name for name in ("Views/LCTabView.swift", "Views/AppList/LCAppListView.swift",
             "Views/Settings/LCSettingsView.swift", "Views/Settings/LCMultiLCManagementView.swift",
             "Utilities/Shared.swift", "Utilities/LCUtilsExtensions.swift", "App/LiveContainerSwiftUIApp.swift", "App/AppDelegate.swift")] +
            ["MultitaskSupport/AppSceneViewController." + suffix for suffix in ("h", "m")] +
            ["LiveContainer/LCBootstrap.m", "LiveContainer/LCSharedUtils.m", "LiveProcess/main.m",
             "ShareExtension/ShareExtensionViewModel.swift", "LaunchAppExtension/LaunchAppExtension.swift"],
            ["AltStore/AppDelegate.swift", "AltStore/SceneDelegate.swift",
             "AltStore/Managing Apps/AppManager.swift",
             "AltStore/Core/Model/DatabaseManager/DatabaseManager.swift",
             "AltStore/Core/Components/Keychain.swift",
             "SideStore/AppBootManager.swift",
             "SideStore/Core/JIT/SideJITManager.swift",
             "SideStore/Core/Pairing/PairingFileManager.swift",
             "SideStore/Handlers/PipelineHandler.swift",
             "SideStore/Views/Settings/Advanced/Connection/ConnectionConfig.swift",
             "AltStore/Settings/AnisetteServerList.swift",
             "SideStore/Core/DeviceApi/MinimuxerWrapper.swift",
             "AltStore/Authentication/AuthenticationViewController.swift",
             "AltStore/Authentication/InstructionsViewController.swift",
             "AltStore/Authentication/ResignAltStoreViewController.swift",
             "AltStore/Authentication/SelectTeamViewController.swift",
             "SideStore/DeepLinks/URLHandler.swift",
             "SideStore/Core/Auth/AuthManager.swift", "SideStore/Handlers/SignInFlowHandler.swift",
             "SideStore/Core/Operations/PipelineExecutor.swift",
             "SideStore/Core/Operations/PipelineRunner.swift",
             "SideStore/Core/Operations/OperationStepDefinition.swift",
             "SideStore/Core/Operations/PipelineOperations/VerifyAppOperation.swift",
             "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift",
             "AltStore/Core/Model/RefreshAttempt.swift",
             "SideStore/Core/Operations/StandaloneOperations/ClearAppCacheOperation.swift",
             "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift",
             "SideStore/Core/Operations/PipelineOperations/UninstallAppOperation.swift",
             "SideStore/Utils/importexport/ImportExport.swift",
             "SideStore/Core/Logging/SideStoreLogging.swift",
             "AltStore/My Apps/MyAppsViewController.swift",
             "AltStore/Intents/App Intents/RefreshAllAppsIntent.swift",
             "AltStore/Intents/App Intents/AppShortcuts.swift",
             "AltStore/Intents/App Intents/RefreshAllAppsWidgetIntent.swift",
             "AltStore/Info.plist", "AltStore.xcodeproj/project.pbxproj",
             "AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"])
        for source, root, pin, names in zip((live_source, side_source), roots, service.PINS, files):
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(subprocess.check_output(["git", "-C", source, "show", pin + ":" + name]))
        refresh.patch_support(roots[0])
        refresh.patch_host_delegate(roots[0])
        refresh.patch_settings(roots[0])
        results.patch(roots[0])
        shell.patch(*roots)
        return roots

    def apply(self, roots):
        def revision(args, **kwargs):
            return service.PINS[0 if str(roots[0]) == args[2] else 1]
        with mock.object(service.subprocess, "check_output", side_effect=revision):
            service.patch(*roots)

    def snapshot(self, directory):
        return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}

    def test_featured_sort_startup_skip_matches_exact_pin_and_keeps_backend_startup(self):
        side_source = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.getenv("SIDESTORE_TEST_SOURCE")
        if not side_source:
            self.skipTest("Set EMBEDDED_SIDESTORE_TEST_SOURCE to the pinned SideStore checkout")
        relative = "AltStore/Core/Model/DatabaseManager/DatabaseManager.swift"
        source = subprocess.check_output(
            ["git", "-C", side_source, "show", service.PINS[1] + ":" + relative],
            text=True, encoding="utf-8")
        patched = service.headless_featured_sort_startup(source)
        self.assertEqual(service.headless_featured_sort_startup(patched), patched)
        self.assertEqual(patched.count("V3_HEADLESS_FEATURED_SORT_SKIP_V1"), 1)

        boot = self.swift_declaration(patched, "private func performStart() async throws")
        self.assertIn("try await self.migrateDatabaseToAppGroupIfNeeded()", boot)
        self.assertIn("try await self.persistentContainer.loadPersistentStores()", boot)
        self.assertIn("try await self.prepareDatabase()", boot)
        preparation = self.swift_declaration(patched, "private func prepareDatabase() async throws")
        self.assertNotIn("updateFeaturedSortIDs()", preparation)
        self.assertIn("try context.save()", preparation,
                      "the required self-app metadata write remains in startup")
        self.assertEqual(patched.count("updateFeaturedSortIDs()"), 1,
                         "the now-unreferenced legacy method declaration remains available")
        updater = self.swift_declaration(patched, "public func updateFeaturedSortIDs() async")
        self.assertEqual(updater.count("context.fetch(fetchRequest)"), 2)
        self.assertEqual(updater.count("try context.save()"), 2)
        self.assertEqual(updater.count("UUID().uuidString"), 2)

        startup = module("patch_embedded_sidestore_startup")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            database_path = roots[1] / relative
            startup.patch_database(database_path)
            self.apply(roots)
            generated = database_path.read_text(encoding="utf-8")
            generated_boot = self.swift_declaration(generated, "private func performStart() async throws")
            self.assertIn("persistentStoreCoordinator.persistentStores.isEmpty", generated_boot)
            self.assertIn("try await self.migrateDatabaseToAppGroupIfNeeded()", generated_boot)
            self.assertIn("try await self.prepareDatabase()", generated_boot)
            generated_preparation = self.swift_declaration(
                generated, "private func prepareDatabase() async throws")
            self.assertIn("V3_HEADLESS_FEATURED_SORT_SKIP_V1", generated_preparation)
            self.assertNotIn("updateFeaturedSortIDs()", generated_preparation)
            first = self.snapshot(directory)
            self.apply(roots)
            self.assertEqual(first, self.snapshot(directory),
                             "generated database startup output must remain stable on replay")

    def test_featured_sort_anchor_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            database = roots[1] / "AltStore/Core/Model/DatabaseManager/DatabaseManager.swift"
            database.write_text(database.read_text(encoding="utf-8").replace(
                "await self.updateFeaturedSortIDs()", "await self.updateLegacySortIDs()"), encoding="utf-8")
            before = self.snapshot(directory)
            with self.assertRaises(SystemExit):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory),
                             "pinned updater anchor drift must fail before any patch writes")

    def test_pipeline_ui_extraction_preserves_no_presenter_safety_and_refresh_routing(self):
        with tempfile.TemporaryDirectory() as name:
            roots = self.fixture(Path(name))
            self.apply(roots)
            side = roots[1]
            pipeline = (side / "SideStore/Handlers/PipelineHandler.swift").read_text(encoding="utf-8")
            for controller in ("AppExtensionViewHostingController", "ReviewPermissionsViewController"):
                self.assertNotIn(controller, pipeline)
            self.assertIn("return false", pipeline[pipeline.index("func resolveBundleIDMismatch("):
                                                       pipeline.index("func reviewPermissions(")])
            self.assertIn(
                'throw OperationError.invalidOperationContext("PipelineHandler: Cannot review permissions because presenting view controller is unavailable")',
                pipeline)
            self.assertIn("return .keepAll(useMainProfile: false)", pipeline)
            self.assertIn("return false", pipeline[pipeline.index("func resolveUnsupportediOSVersion("):])
            self.assertIn("return (initialBundleID, true)", pipeline)
            self.assertIn("return .correctAndProceed(correctedGroup)", pipeline)
            self.assertEqual(service.headless_pipeline_handler(pipeline), pipeline)

            step_source = (side / "SideStore/Core/Operations/OperationStepDefinition.swift").read_text(encoding="utf-8")
            refresh_steps = step_source[step_source.index("static let refresh:"):
                                        step_source.index("static let activateLegacy:")]
            self.assertEqual(re.findall(r"PipelineExecutionStep\(\.(\w+),\s*(\d+)\)", refresh_steps), [
                ("updateAppCertificate", "5"),
                ("verifyCertificate", "10"),
                ("fetchProvisioningProfiles", "45"),
                ("refreshApp", "40"),
            ])
            runner = (side / "SideStore/Core/Operations/PipelineRunner.swift").read_text(encoding="utf-8")
            permission_mode = runner[runner.index("let permissionReviewMode:"):runner.index("let operationProgress =")]
            self.assertRegex(permission_mode, r"default:\s*permissionReviewMode\s*=\s*\.none")
            self.assertNotIn("case .refresh:", permission_mode)
            self.assertNotIn(".verifyApp", refresh_steps)
            self.assertNotIn(".removeAppExtensions", refresh_steps)
            verify = (side / "SideStore/Core/Operations/PipelineOperations/VerifyAppOperation.swift").read_text(encoding="utf-8")
            self.assertIn("guard self.permissionsMode != .none else { return }", verify)
            self.assertIn("handler.reviewPermissions", verify)

            project = (side / "AltStore.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
            side_store_target_exception = project[project.index("A8EEC8CB2F4B146B00F2436D"):]
            member_start = side_store_target_exception.index("membershipExceptions = (")
            member_end = side_store_target_exception.index(");", member_start)
            membership = side_store_target_exception[member_start:member_end]
            for path in service.HEADLESS_SIDESTORE_PIPELINE_UI_FILES:
                self.assertIn(f'"{path}"', membership)

            intent = (side / "AltStore/Intents/App Intents/RefreshAllAppsIntent.swift").read_text(encoding="utf-8")
            widget = (side / "AltStore/Intents/App Intents/RefreshAllAppsWidgetIntent.swift").read_text(encoding="utf-8")
            self.assertIn("V3_SHORTCUT_GUEST_BACKEND_PIPELINE_V1", intent)
            self.assertIn("AppManager.shared.backgroundRefresh", intent)
            self.assertIn("RefreshAllAppsIntent(presentsNotifications: true)", widget)
            runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
            self.assertIn('ask(kind: "bundleIDOverride"', runtime)
            self.assertIn('ask(kind: "appGroupMismatch"', runtime)

    def test_v31_generated_tree_fails_closed_without_mutation(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            manifest = roots[0] / ".v3-command-patch.json"
            prior = json.loads(manifest.read_text(encoding="utf-8"))
            prior["patchVersion"] = 31
            manifest.write_text(json.dumps(prior, indent=2) + "\n", encoding="utf-8")
            before = self.snapshot(directory)
            with self.assertRaisesRegex(SystemExit, f"prepared patch version 31 cannot be migrated safely to v{service.PATCH_VERSION}"):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory))

    def test_v32_generated_tree_fails_closed_without_mutation(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            manifest = roots[0] / ".v3-command-patch.json"
            prior = json.loads(manifest.read_text(encoding="utf-8"))
            prior["patchVersion"] = 32
            manifest.write_text(json.dumps(prior, indent=2) + "\n", encoding="utf-8")
            before = self.snapshot(directory)
            with self.assertRaisesRegex(SystemExit, f"prepared patch version 32 cannot be migrated safely to v{service.PATCH_VERSION}"):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory))

    def test_v33_generated_tree_fails_closed_without_mutation(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            manifest = roots[0] / ".v3-command-patch.json"
            prior = json.loads(manifest.read_text(encoding="utf-8"))
            prior["patchVersion"] = 33
            manifest.write_text(json.dumps(prior, indent=2) + "\n", encoding="utf-8")
            before = self.snapshot(directory)
            with self.assertRaisesRegex(SystemExit, f"prepared patch version 33 cannot be migrated safely to v{service.PATCH_VERSION}"):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory))

    def test_v34_generated_tree_fails_closed_without_mutation(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            manifest = roots[0] / ".v3-command-patch.json"
            prior = json.loads(manifest.read_text(encoding="utf-8"))
            prior["patchVersion"] = 34
            manifest.write_text(json.dumps(prior, indent=2) + "\n", encoding="utf-8")
            before = self.snapshot(directory)
            with self.assertRaisesRegex(SystemExit, f"prepared patch version 34 cannot be migrated safely to v{service.PATCH_VERSION}"):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory))

    def test_pinned_patch_replay_and_tamper(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            first = self.snapshot(directory)
            project = (roots[1] / "AltStore.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
            exception_anchor = project.index("A8EEC8CB2F4B146B00F2436D")
            member_start = project.index("membershipExceptions = (", exception_anchor)
            member_end = project.index(");", member_start)
            self.assertIn('"My Apps/MyAppsViewController.swift"', project[member_start:member_end])
            self.assertIn('"Components/HeaderContentViewController.swift"', project[member_start:member_end])
            self.assertIn('"Components/NavigationBar.swift"', project[member_start:member_end])
            self.assertIn('"Extensions/INInteraction+AltStore.swift"', project[member_start:member_end])
            manager = (roots[1] / "AltStore/Managing Apps/AppManager.swift").read_text(encoding="utf-8")
            self.assertIn("isResignActive: false,", manager)
            self.assertIn("presenterProvider:", manager)
            self.assertNotIn("isResignActive: false //", manager,
                             "the generated backend call must preserve the argument separator")
            self.apply(roots)
            self.assertEqual(first, self.snapshot(directory))
            path = roots[0] / "SideStoreSupport/XPCClient.m"
            path.write_text(path.read_text() + "\n// unexpected drift\n")
            with self.assertRaises(SystemExit):
                self.apply(roots)
            self.assertNotIn("LCUtils.openSideStore", (roots[0] / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift").read_text())
            self.assertIn(".downloadAlert", (roots[0] / "LiveContainerSwiftUI/Views/LCTabView.swift").read_text())
            self.assertNotIn("LCUtils.openSideStore", (roots[0] / "LiveContainerSwiftUI/Views/Settings/LCMultiLCManagementView.swift").read_text(encoding="utf-8"))

    def test_v30_manifest_fails_closed_without_mutation(self):
        side_source = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not side_source:
            self.skipTest("Set EMBEDDED_SIDESTORE_TEST_SOURCE to the pinned SideStore checkout")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            side = roots[1]
            model_relative = service.HEADLESS_ANISETTE_MODELS_SOURCE
            model_path = side / model_relative
            self.assertTrue(model_path.is_file())
            model_path.unlink()
            project_path = side / "AltStore.xcodeproj/project.pbxproj"
            current_project = project_path.read_text(encoding="utf-8")
            exclusion = '"Settings/AnisetteServerList.swift"'
            exclusion_lines = [line for line in current_project.splitlines(keepends=True) if exclusion in line]
            self.assertEqual(len(exclusion_lines), 1)
            v30_project = current_project.replace(exclusion_lines[0], "", 1)
            project_path.write_bytes(v30_project.encode("utf-8"))

            manifest_path = roots[0] / ".v3-command-patch.json"
            v30_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(v30_manifest["patchVersion"], service.PATCH_VERSION)
            v30_manifest["patchVersion"] = 30
            v30_manifest["templates"].pop(service.HEADLESS_ANISETTE_MODELS_MANIFEST_KEY)
            v30_manifest["files"] = [record for record in v30_manifest["files"] if record[1] != model_relative]
            project_records = [record for record in v30_manifest["files"]
                               if record[0] == 1 and record[1] == "AltStore.xcodeproj/project.pbxproj"]
            self.assertEqual(len(project_records), 1)
            project_records[0][2] = hashlib.sha256(v30_project.encode("utf-8")).hexdigest()
            manifest_path.write_text(json.dumps(v30_manifest, indent=2) + "\n", encoding="utf-8")

            before = self.snapshot(directory)
            with self.assertRaisesRegex(SystemExit, f"prepared patch version 30 cannot be migrated safely to v{service.PATCH_VERSION}"):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory),
                             "the real v30 output shape must fail closed without partial migration")

    def test_v29_manifest_fails_closed_with_clean_checkout_recovery(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            manifest_path = roots[0] / ".v3-command-patch.json"
            legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
            legacy["patchVersion"] = 29
            legacy["templates"].pop(service.BACKEND_CONNECTION_CONFIG_MANIFEST_KEY)
            legacy["templates"].pop(service.HEADLESS_ANISETTE_MODELS_MANIFEST_KEY)
            manifest_path.write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")
            before = self.snapshot(directory)
            with self.assertRaisesRegex(SystemExit, f"prepared patch version 29 cannot be migrated safely to v{service.PATCH_VERSION}.*discard generated work directories"):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory), "unsupported v29 manifests must fail without mutation")

    def test_unknown_patch_manifest_version_fails_closed_without_mutation(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            manifest_path = roots[0] / ".v3-command-patch.json"
            unknown = json.loads(manifest_path.read_text(encoding="utf-8"))
            unknown["patchVersion"] = 999
            manifest_path.write_text(json.dumps(unknown, indent=2) + "\n", encoding="utf-8")
            before = self.snapshot(directory)
            with self.assertRaisesRegex(SystemExit, f"prepared patch version 999 cannot be migrated safely to v{service.PATCH_VERSION}"):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory), "unknown patch versions must fail without mutation")

    def test_v35_manifest_fails_closed_without_mutation(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            manifest_path = roots[0] / ".v3-command-patch.json"
            prior = json.loads(manifest_path.read_text(encoding="utf-8"))
            prior["patchVersion"] = 35
            manifest_path.write_text(json.dumps(prior, indent=2) + "\n", encoding="utf-8")
            before = self.snapshot(directory)
            with self.assertRaisesRegex(SystemExit, f"prepared patch version 35 cannot be migrated safely to v{service.PATCH_VERSION}"):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory),
                             "v35 generated trees must be discarded without partial migration")

    def test_pinned_headless_ui_adapter_verifier_rejects_drift(self):
        side_source = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not side_source:
            self.skipTest("Set EMBEDDED_SIDESTORE_TEST_SOURCE to the pinned source checkout")
        real_check_output = service.subprocess.check_output

        def read_pinned_source(arguments, **kwargs):
            # The production verifier uses `git show` and `git grep` against
            # the pinned worktree. Tests run it over a temporary patched copy,
            # so redirect only Git's repository root while preserving the
            # exact pinned command and revision.
            if len(arguments) > 4 and arguments[3] == "show":
                return real_check_output(["git", "-C", side_source, *arguments[3:]],
                                         text=True, encoding="utf-8")
            if len(arguments) > 3 and arguments[3] == "grep":
                return real_check_output(["git", "-C", side_source, *arguments[3:]],
                                         text=True, encoding="utf-8")
            return real_check_output(arguments, **kwargs)

        with tempfile.TemporaryDirectory() as name:
            roots = self.fixture(Path(name))
            self.apply(roots)
            module("patch_background_automation").patch_background_operation(roots[1])
            embedded_keychain.patch(roots[1])
            with mock.object(service.subprocess, "check_output", side_effect=read_pinned_source):
                service.verify_headless_ui_adapters(roots[1], service.PINS[1])
                manager = roots[1] / "AltStore/Managing Apps/AppManager.swift"
                manager.write_text(manager.read_text(encoding="utf-8") + "\n// drift\n", encoding="utf-8")
                with self.assertRaises(SystemExit):
                    service.verify_headless_ui_adapters(roots[1], service.PINS[1])

    def test_prepared_sidestore_has_no_automatic_storyboard_root_or_unused_starscream(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            side = roots[1]
            sign_in_path = side / "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift"
            sign_in_path.write_text(embedded_keychain.patch_sign_in_operation(sign_in_path.read_text(encoding="utf-8")),
                                    encoding="utf-8")
            info = plistlib.loads((side / "AltStore/Info.plist").read_bytes())
            self.assertNotIn("UIMainStoryboardFile", info)
            self.assertNotIn("UIBackgroundModes", info)
            icons = info.get("CFBundleIcons", {})
            self.assertIsInstance(icons.get("CFBundlePrimaryIcon"), dict)
            self.assertNotIn("CFBundleAlternateIcons", icons)
            scene_configs = info["UIApplicationSceneManifest"]["UISceneConfigurations"]
            for configurations in scene_configs.values():
                for configuration in configurations:
                    self.assertNotIn("UISceneStoryboardFile", configuration)
                    self.assertNotIn("UILaunchStoryboardName", configuration)
            self.assertNotIn("UILaunchStoryboardName", info)
            self.assertNotIn("INIntentsSupported", info)
            self.assertNotIn("NSUserActivityTypes", info)
            project = (side / "AltStore.xcodeproj/project.pbxproj").read_text()
            self.assertNotIn("Starscream", project)
            self.assertNotIn("MarkdownKit", project)
            self.assertNotIn("Nuke", project)
            app_delegate = (side / "AltStore/AppDelegate.swift").read_text()
            self.assertIn("V3_HEADLESS_IMAGE_PIPELINE_REMOVED_V1", app_delegate)
            self.assertNotIn("import Nuke", app_delegate)
            self.assertNotIn("prepareImageCache", app_delegate)
            cache_operation = (side / "SideStore/Core/Operations/StandaloneOperations/ClearAppCacheOperation.swift").read_text()
            self.assertIn("V3_LEGACY_IMAGE_CACHE_CLEANUP_V1", cache_operation)
            self.assertNotIn("import Nuke", cache_operation)
            self.assertNotIn("ImagePipeline", cache_operation)
            for widget_edge in ("BF989175250AABF4002ACF50", "BF989176250AABF4002ACF50",
                                "BF989177250AABF4002ACF50", "BF98917B250AABF4002ACF50"):
                self.assertIn(widget_edge, project,
                              "the SideStore widget must remain available for host widget repackaging")
            self.assertIn("BF989166250AABF3002ACF50 /* AltWidgetExtension */", project,
                          "keep the upstream widget target available for standalone SideStore")
            self.assertIn("0ED4AEC92E6DDB2A0039E2C0 /* PBXTargetDependency */", project,
                          "the SideBackup backend dependency remains in the project")
            self.assertNotIn("C0DE00000000000000000001", project)
            exception_anchor = project.index("A8EEC8CB2F4B146B00F2436D")
            member_start = project.index("membershipExceptions = (", exception_anchor)
            member_end = project.index(");", member_start)
            membership = project[member_start:member_end]
            self.assertIn('"Components/AppBannerView.swift"', membership)
            self.assertIn('"Components/AppBannerCollectionViewCell.swift"', membership)
            for required_host_intent_adapter in (
                '"Intents/App Intents/AppShortcuts.swift"',
                '"Intents/App Intents/RefreshAllAppsIntent.swift"',
                '"Intents/App Intents/RefreshAllAppsWidgetIntent.swift"',
            ):
                self.assertNotIn(required_host_intent_adapter, membership,
                                 "host App Intents metadata requires these UI-free backend adapters")
            self.assertNotIn('"Intents/Legacy/Intents.intentdefinition"', membership,
                             "the intent schema is staged into the host package then removed from the backend")
            removed_ui_resources = (
                '"iOS/LaunchScreen.storyboard"', '"iOS/Main.storyboard"',
                '"tvOS/Main.storyboard"',
                '"Browse/BrowseViewController.swift"',
                '"Browse/FeaturedViewController.swift"', '"LaunchViewController.swift"',
                '"Browse/FeaturedComponents.swift"',
                '"Browse/ScreenshotCollectionViewCell.swift"',
                '"News/NewsViewController.swift"',
                '"TabBarController.swift"',
                '"Components/ForwardingNavigationController.swift"',
                '"Components/HeaderContentViewController.swift"',
                '"Components/NavigationBar.swift"',
                '"App Detail/AppContentViewController.swift"',
                '"App Detail/AppContentViewControllerCells.swift"',
                '"App Detail/AppDetailCollectionViewController.swift"',
                '"App Detail/AppPermissionsCard.swift"',
                '"App Detail/AppViewController.swift"',
                '"App Detail/Screenshots/AppScreenshotsViewController.swift"',
                '"App Detail/Screenshots/PreviewAppScreenshotsViewController.swift"',
                '"App Detail/Screenshots/AppScreenshotCollectionViewCell.swift"',
                '"Components/AppCardCollectionViewCell.swift"',
                '"App IDs/AppIDsViewController.swift"',
                '"News/NewsCollectionViewCell.swift"',
                '"Authentication/tvOS/Authentication.storyboard"',
                '"Authentication/ResignAltStoreViewController.swift"',
                '"Core/Intents/ViewAppIntentHandler.swift"',
                '"Intents/Legacy/IntentHandler.swift"',
                '"My Apps/MyAppsViewController.swift"',
                '"My Apps/tvOS/InstalledAppsCollectionHeaderView.xib"',
                '"My Apps/tvOS/UpdateCollectionViewCell.xib"',
                '"My Apps/MyAppsComponents.swift"',
                '"My Apps/InstalledAppsCollectionHeaderView.swift"',
                '"My Apps/UpdateCollectionViewCell.swift"',
                '"Authentication/Authentication.storyboard"', '"Settings/Settings.storyboard"',
                '"Sources/Sources.storyboard"', '"Components/AppBannerView.xib"',
                '"Components/tvOS/AppBannerView.xib"',
                '"Sources/AddSourceViewController.swift"', '"Sources/tvOS/Sources.storyboard"',
                '"News/tvOS/NewsCollectionViewCell.xib"',
                '"Settings/tvOS/Settings.storyboard"',
                '"Settings/tvOS/AboutPatreonHeaderView.xib"',
                '"Settings/tvOS/SettingsHeaderFooterView.xib"',
                '"Sources/Components/tvOS/SourceHeaderView.xib"',
                '"Sources/Components/SourceComponents.swift"',
                '"Sources/Components/SourceHeaderView.swift"',
                '"Sources/Components/AddSourceTextFieldCell.swift"',
                '"Sources/SourcesViewController.swift"',
                '"Sources/SourceDetailViewController.swift"',
                '"Sources/SourceDetailContentViewController.swift"',
                '"Extensions/INInteraction+AltStore.swift"',
                '"My Apps/InstalledAppsCollectionHeaderView.xib"', '"My Apps/UpdateCollectionViewCell.xib"',
                '"News/NewsCollectionViewCell.xib"', '"Settings/AboutPatreonHeaderView.xib"',
                '"Settings/SettingsHeaderFooterView.xib"', '"Sources/Components/SourceHeaderView.xib"',
                '"Settings/PatreonViewController.swift"', '"Settings/LicensesViewController.swift"',
                '"Settings/SettingsViewController.swift"',
                '"Settings/SettingsHeaderFooterView.swift"',
                '"Settings/InsetGroupTableViewCell.swift"',
                '"Settings/RefreshAttemptsViewController.swift"',
                '"Settings/Error Log/ErrorDetailsViewController.swift"',
                '"Settings/Error Log/ErrorLogTableViewCell.swift"',
                '"Settings/Error Log/ErrorLogViewController.swift"',
                '"Components/BackgroundTaskManager.swift"', '"Resources/Silence.m4a"',
                '"Settings/AltAppIconsViewController.swift"', '"Resources/AltIcons.plist"',
                '"Resources/Icons.xcassets/Modern/BlueIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/DarkIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/HoneydewIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/PrideIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/SandyIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/SkyIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/SnowIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/StarburstIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/StormIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/VistaIcon.appiconset"',
                '"Resources/Icons.xcassets/Modern/WinterIcon.appiconset"')
            for path in removed_ui_resources:
                self.assertIn(path, membership)
            self.assertNotIn('"Resources/Icons.xcassets/AppIcon.appiconset"', membership,
                             "the primary SideStore app icon remains part of the backend bundle")
            self.assertNotIn('"Resources/Icons.xcassets/Classic"', membership,
                             "the runtime-selected Classic preview images remain available")
            self.assertNotIn('"Resources/Icons.xcassets/Modern"', membership,
                             "the runtime-selected Modern preview images remain available")
            self.assertNotIn("ASSETCATALOG_COMPILER_INCLUDE_ALL_APPICON_ASSETS = YES", project)
            self.assertEqual(project.count("ASSETCATALOG_COMPILER_INCLUDE_ALL_APPICON_ASSETS = NO"), 2)
            side_exception_anchor = project.index("A8EECF492F4B195000F2436D")
            side_member_start = project.index("membershipExceptions = (", side_exception_anchor)
            side_member_end = project.index(");", side_member_start)
            side_membership = project[side_member_start:side_member_end]
            for path in service.HEADLESS_SIDESTORE_VIEW_FILES + service.HEADLESS_SIDESTORE_AUX_UI_FILES:
                self.assertIn(f'"{path}"', side_membership)
            self.assertIn('"Views/Settings/Advanced/Connection/ConnectionConfig.swift"', side_membership)
            for excluded_presentation in (
                '"Views/Settings/Advanced/CacheMgmt/CacheManagementView.swift"',
                '"Views/Settings/Advanced/CacheMgmt/CacheViewModel.swift"',
            ):
                self.assertIn(excluded_presentation, side_membership)
            self.assertIn('"Views/Components/CustomAppIDAlertViewController.swift"', side_membership)
            pipeline_handler = (side / "SideStore/Handlers/PipelineHandler.swift").read_text(encoding="utf-8")
            self.assertIn("V3_HEADLESS_BUNDLE_ID_PROMPT_V1", pipeline_handler)
            self.assertNotIn("AppendTeamIDCheckboxView", pipeline_handler)
            self.assertIn("return (initialBundleID, true)", pipeline_handler)
            host_pipeline_handler = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
            self.assertIn('ask(kind: "bundleIDOverride"', host_pipeline_handler)
            self.assertIn('"key": "appendTeamID"', host_pipeline_handler)
            self.assertIn('answer["appendTeamID"] != "false"', host_pipeline_handler)
            legacy_connection_config = (side / "SideStore/Views/Settings/Advanced/Connection/ConnectionConfig.swift").read_text(encoding="utf-8")
            backend_connection_config = (side / "SideStore/Core/DeviceApi/ConnectionConfig.swift").read_text(encoding="utf-8")
            self.assertIn("V3_HEADLESS_CONNECTION_CONFIG_MOVED_V1", legacy_connection_config)
            self.assertNotIn("SwiftUI", legacy_connection_config)
            self.assertIn("V3_HEADLESS_BACKEND_CONNECTION_CONFIG_V1", backend_connection_config)
            self.assertNotIn("SwiftUI", backend_connection_config)
            self.assertNotIn("Combine", backend_connection_config)
            self.assertNotIn("ObservableObject", backend_connection_config)
            self.assertNotIn("@Published", backend_connection_config)
            for accessor in ("tunnelOverridePeerIp", "remoteServerIp", "wireGuardServerHost", "wireGuardServerPort",
                             "@objc(wireGuardServerPort)", "val > 0 && val <= 65535"):
                self.assertIn(accessor, backend_connection_config)
            self.assertIn("get { UserDefaults.standard.useLocalVPN }", backend_connection_config)
            self.assertIn("var connectionMode: DeviceConnectionMode", backend_connection_config)
            minimuxer_wrapper = (side / "SideStore/Core/DeviceApi/MinimuxerWrapper.swift").read_text(encoding="utf-8")
            self.assertIn("getConnectionMode: { config.connectionMode }", minimuxer_wrapper)
            self.assertNotIn("Views/Settings/Advanced/Connection/ConnectionConfig.swift", minimuxer_wrapper)
            app_delegate = (side / "AltStore/AppDelegate.swift").read_text()
            self.assertNotIn("import Intents", app_delegate)
            self.assertNotIn("handlerFor intent: INIntent", app_delegate)
            self.assertNotIn("ViewAppIntentHandler()", app_delegate)
            self.assertIn("case .invalidPairingFile(_) = operationError", app_delegate)
            self.assertIn("V3HeadlessPairingFailure.tagIfInvalidPairing", app_delegate)
            auth_manager = (side / "SideStore/Core/Auth/AuthManager.swift").read_text()
            self.assertNotIn("SignInFlowHandler", auth_manager)
            self.assertNotIn("UIViewController", auth_manager)
            self.assertNotIn("import UIKit", auth_manager)
            app_manager = (side / "AltStore/Managing Apps/AppManager.swift").read_text(encoding="utf-8")
            self.assertNotIn("func signIn(presentingViewController:", app_manager)
            self.assertNotIn("import Intents", app_manager)
            self.assertNotIn("ResignAltStoreViewController", app_manager)
            self.assertIn("V3_HEADLESS_APP_MANAGER_SIGNIN_REMOVED_V1", app_manager)
            self.assertIn("V3_HEADLESS_APP_MANAGER_DEACTIVATE_APPLIMIT_WRAPPER_REMOVED_V1", app_manager)
            self.assertNotIn("func deactivateApps(for:", app_manager)
            self.assertNotIn("self.deactivateApps(for:", app_manager)
            self.assertIn("func deactivate(_ installedApp: InstalledApp", app_manager)
            self.assertIn("performSingleOperation(.deactivate(installedApp)", app_manager)
            app_boot = (side / "SideStore/AppBootManager.swift").read_text(encoding="utf-8")
            self.assertIn("V3_HEADLESS_BOOT_UI_STATE_REMOVED_V1", app_boot)
            self.assertNotIn("promptForPairing(", app_boot)
            self.assertNotIn("needsPairingPrompt", app_boot)
            self.assertNotIn("needsSideJITPrompt", app_boot)
            self.assertIn("SideJITManager.shared.askForNetwork()", app_boot)
            sidejit_manager = (side / "SideStore/Core/JIT/SideJITManager.swift").read_text(encoding="utf-8")
            self.assertIn("V3_HEADLESS_SIDEJIT_PROMPT_REMOVED_V1", sidejit_manager)
            self.assertIn("public func resolveServerURL() async -> String", sidejit_manager)
            self.assertIn("public func askForNetwork() async", sidejit_manager)
            self.assertNotIn("presentJITPrompt", sidejit_manager)
            pairing_manager = (side / "SideStore/Core/Pairing/PairingFileManager.swift").read_text(encoding="utf-8")
            self.assertIn("V3_HEADLESS_PAIRING_FILE_UI_REMOVED_V1", pairing_manager)
            self.assertIn("nonisolated var pairingUDID:", pairing_manager)
            self.assertIn("nonisolated func fetchPairingFile()", pairing_manager)
            self.assertIn("func savePairingFile(contents: String)", pairing_manager)
            self.assertNotIn("UIDocumentPicker", pairing_manager)
            self.assertIn("V3_TYPED_PAIRING_FAILURE_PROPAGATION_V1", app_manager)
            self.assertIn("V3HeadlessPairingFailure.tagIfInvalidPairing(error)", app_manager)
            service_template = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
            self.assertIn("V3HeadlessPairingFailure.tagIfInvalidPairing(error)", service_template)
            minimuxer_wrapper = (side / "SideStore/Core/DeviceApi/MinimuxerWrapper.swift").read_text(encoding="utf-8")
            self.assertIn("case .invalidPairing(_, let reason)", minimuxer_wrapper)
            self.assertIn("return .invalidPairingFile(reason: reason)", minimuxer_wrapper)
            self.assertNotIn("prepareForBackgroundFetch", app_delegate)
            self.assertNotIn("requestAuthorization(options: [.alert, .badge, .sound])", app_delegate,
                             "the embedded backend must not request a second app's notification permission")
            self.assertNotIn("BackgroundTaskManager.shared", app_delegate)
            self.assertNotIn("AppManager.shared.backgroundRefresh", app_delegate)
            refresh_intent_source = (side / "AltStore/Intents/App Intents/RefreshAllAppsIntent.swift").read_text(encoding="utf-8")
            shortcuts_source = (side / "AltStore/Intents/App Intents/AppShortcuts.swift").read_text(encoding="utf-8")
            self.assertIn("struct RefreshAllAppsIntent", refresh_intent_source)
            self.assertNotIn("struct InstallIPAIntent", refresh_intent_source)
            self.assertNotIn("AppManager.shared.install(.url", refresh_intent_source)
            self.assertIn("V3_SHORTCUT_GUEST_BACKEND_PIPELINE_V1", refresh_intent_source)
            self.assertIn("AppManager.shared.backgroundRefresh", refresh_intent_source)
            self.assertIn("V3RefreshIntentStartPolicy.create", refresh_intent_source)
            self.assertIn("classify: V3HeadlessPairingFailure.tagIfInvalidPairing", refresh_intent_source)
            self.assertNotIn("try? AppManager.shared.backgroundRefresh", refresh_intent_source)
            self.assertIn("throw V3HeadlessPairingFailure.tagIfInvalidPairing(error)", refresh_intent_source)
            self.assertIn("IntentError(V3HeadlessPairingFailure.tagIfInvalidPairing(error))", refresh_intent_source)
            self.assertIn("DatabaseManager.shared.start()", refresh_intent_source)
            self.assertIn("ProgressReportingIntent", refresh_intent_source)
            self.assertIn("operationActor", refresh_intent_source)
            self.assertNotIn('Notification.Name("LiveContainerAutoRefreshRunNow")', refresh_intent_source)
            shortcut_request_policy = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(encoding="utf-8")
            self.assertIn('origin: "manualUnknown"', shortcut_request_policy)
            self.assertIn("static var openAppWhenRun = true", refresh_intent_source)
            widget_intent_source = (side / "AltStore/Intents/App Intents/RefreshAllAppsWidgetIntent.swift").read_text(encoding="utf-8")
            self.assertIn("ProgressReportingIntent", widget_intent_source)
            self.assertIn("RefreshAllAppsIntent(presentsNotifications: true)", widget_intent_source)
            support = (roots[0] / "SideStoreSupport/SideStore.swift").read_text(encoding="utf-8")
            self.assertIn("V3ShortcutRefreshRequest.make()", support)
            self.assertIn('Notification.Name("LiveContainerAutoRefreshRunNow")', support)
            intent_helper = support[support.index("func performIntentRefresh("):support.index("class RefreshHandler")]
            self.assertNotIn("RefreshHandler.shared.startRefresh(identifier: identifier", intent_helper)
            self.assertIn("Refresh All was requested in LiveContainer", support)
            self.assertIn('Notification.Name("LiveContainerAutoRefreshRunNow")', support)
            self.assertIn('origin: "manualUnknown"', shortcut_request_policy)
            self.assertIn("AppShortcut(intent: RefreshAllAppsIntent()", shortcuts_source)
            self.assertNotIn("InstallIPAIntent", shortcuts_source)
            widget_intent_source = (side / "AltStore/Intents/App Intents/RefreshAllAppsWidgetIntent.swift").read_text(encoding="utf-8")
            self.assertIn("V3_SHORTCUT_WIDGET_BACKEND_FORWARD_V1", widget_intent_source)
            self.assertIn("RefreshAllAppsIntent(presentsNotifications: true)", widget_intent_source)
            self.assertIn("ProgressReportingIntent", widget_intent_source)
            self.assertNotIn('debugLog("Failed to refresh apps via widget. \\(error)")', widget_intent_source)
            self.assertIn('[V3_WIDGET_REFRESH] failed', widget_intent_source)
            self.assertIn("throw error", widget_intent_source)
            pairing_view = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
            pairing_view = pairing_view[pairing_view.index("struct V3PairingView:"):pairing_view.index("@MainActor\nfinal class V3SettingsStore")]
            self.assertIn("failure.recovery", pairing_view)
            self.assertIn("Text(failure.technicalDetails)", pairing_view)
            self.assertIn("Choose Pairing File Again", pairing_view)
            self.assertIn("V3PairingImportFailurePolicy.shouldOfferFileRetry(operation: failure.operation", pairing_view)
            self.assertIn("status.present(failure)", pairing_view)
            self.assertNotIn("self.fetchSources", app_delegate)
            self.assertIn("completionHandler(.noData)", app_delegate)
            scene_delegate = (side / "AltStore/SceneDelegate.swift").read_text(encoding="utf-8")
            url_handler = (side / "SideStore/DeepLinks/URLHandler.swift").read_text(encoding="utf-8")
            for generated in (app_delegate, scene_delegate, url_handler):
                for line in generated.splitlines():
                    if "debugLog(" in line:
                        self.assertNotIn("url.absoluteString", line)
                        self.assertNotIn("context.url", line)
                        self.assertNotIn("debugLog(finished)", line)
            self.assertIn("V3_HEADLESS_EXTERNAL_CALLBACKS_V3", url_handler)
            self.assertNotIn("PairingFileManager.shared.fetchPairingFile()", url_handler)
            self.assertNotIn("V3PairingCallbackPolicy", url_handler)
            self.assertNotIn('debugLog("[URLHandler] handle(_:) called with URL: \\(url.absoluteString)")', url_handler)
            resolved = json.loads((side / "AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved").read_text())
            self.assertNotIn("starscream", [pin["identity"] for pin in resolved["pins"]])
            self.assertNotIn("markdownkit", [pin["identity"] for pin in resolved["pins"]])
            self.assertNotIn("nuke", [pin["identity"] for pin in resolved["pins"]])
            jit = (roots[0] / "LiveContainerSwiftUI/Utilities/LCUtilsExtensions.swift").read_text(encoding="utf-8")
            self.assertNotIn('sidestore://enable-jit', jit)
            self.assertIn('V3ServiceBridge.shared.request(operation: "jit"', jit)
            scene = (roots[0] / "MultitaskSupport/AppSceneViewController.m").read_text(encoding="utf-8")
            self.assertEqual(scene.count("UIKitFixesInit();"), 1)
            self.assertEqual(scene.count("V3InitializeUIKitFixes();"), 2)
            self.assertIn("dispatch_once(&onceToken, ^{ UIKitFixesInit(); });", scene)
            self.assertIn('forKey:@"lcAppGroupID"', scene)
            live_process = (roots[0] / "LiveProcess/main.m").read_text(encoding="utf-8")
            self.assertIn('forKey:@"LCInheritedAppGroupID"', live_process)
            shared_utils = (roots[0] / "LiveContainer/LCSharedUtils.m").read_text(encoding="utf-8")
            self.assertLess(shared_utils.index('objectForKey:@"LCInheritedAppGroupID"'),
                            shared_utils.index("NSArray* possibleAppGroups"))
            self.assertTrue((roots[0] / "LiveContainer/LCAppGroupSelectionPolicy.h").exists())
            self.assertIn("!isLiveProcess && sideStoreExist", (roots[0] / "LiveContainer/LCBootstrap.m").read_text(encoding="utf-8"))
            for name in ("ShareExtension/ShareExtensionViewModel.swift", "LaunchAppExtension/LaunchAppExtension.swift"):
                self.assertNotIn('set("builtinSideStore", forKey: "LCLaunchExtensionBundleID")', (roots[0] / name).read_text(encoding="utf-8"))
            uninstall = (roots[1] / "SideStore/Core/Operations/PipelineOperations/UninstallAppOperation.swift").read_text(encoding="utf-8")
            self.assertIn("V3_DELETE_NATIVE_SUCCESS_EVIDENCE_V1", uninstall)
            self.assertIn("await handler.recordNativeUninstallSucceeded()", uninstall)
            sign_in = (roots[1] / "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift").read_text()
            self.assertIn("V3_PROVISIONING_RETRY_BYPASSES_CACHED_SIGNIN_V1", sign_in)
            self.assertIn("V3_AUTH_CREDENTIAL_TRANSACTION_V1", sign_in)
            self.assertIn("Keychain.shared.writeAuthenticationCredentials(appleID: appleID, password: password, dsid: session.dsid, authToken: session.authToken)", sign_in)
            self.assertNotIn("AuthManager.shared.adsid = session.dsid", sign_in)
            self.assertNotIn("AuthManager.shared.xcodeToken = session.authToken", sign_in)
            self.assertNotIn("AuthManager.shared.currentAppleID = appleID", sign_in)
            self.assertNotIn("AuthManager.shared.password = password", sign_in)
            self.assertIn("V3ProvisioningResumeExecutionPolicy.mayUseCachedSignIn", sign_in)
            self.assertIn("handleSignInResult(.success(silentResult))", sign_in)
            self.assertIn("V3ProvisioningResumeUnavailableError()", sign_in)
            self.assertIn("if self.v3ForceProvisioningRetry {", sign_in)
            self.assertIn("let account = team.account else", sign_in)
            retry = sign_in[sign_in.index("if self.v3ForceProvisioningRetry {"):sign_in.index("} else if V3ProvisioningResumeExecutionPolicy")]
            self.assertIn("self.provisioningLoop(account: account, session: session", retry)
            self.assertIn("session.anisetteData = try await self.getAnisetteData()", retry)
            self.assertIn("AuthManager.shared.session = session", retry)
            self.assertNotIn("silentSignIn()", retry,
                             "provisioning retry must reuse the authenticated session without reauthentication")
            self.assertIn("retryCredentials: (String, String)?", sign_in)
            self.assertIn("V3TwoFactorRetryPolicy.shouldReuseCredentialsForCodeRetry", sign_in)
            self.assertIn("v3ClassifyAuthError(error) == nil", sign_in)
            failure_marker = "V3_AUTH_FAILURE_PRESERVES_ACCOUNT_STATE_V1"
            self.assertIn(failure_marker, sign_in)
            failure_catch_end = sign_in.index("throw error", sign_in.index(failure_marker)) + len("throw error")
            failure_catch = sign_in[sign_in.index(failure_marker):failure_catch_end]
            for destructive_side_effect in (
                "AuthManager.shared.signOut()",
                "Keychain.shared.clearSignInInfo",
                "clearActiveCertificate",
                "deactivateActiveAccountAndTeam",
            ):
                self.assertNotIn(destructive_side_effect, failure_catch)
            start_authentication = sign_in.index("private func startAuthentication")
            self.assertLess(sign_in.index("handleSignInResult(.success(silentResult))", start_authentication),
                            sign_in.index("self.provisioningLoop(", start_authentication))

    def test_workflow_verifies_exact_pinned_signin_and_headless_adapter_patches(self):
        workflow = (ROOT / ".github/workflows/livecontainer-build.yml").read_text(encoding="utf-8")
        self.assertIn("SideStore/Core/Anisette", workflow)
        self.assertIn("AltStore/Managing Apps/AppManager.swift", workflow)
        self.assertIn("--verify-headless-ui-adapters", workflow)
        self.assertIn("--verify-sign-in-operation", workflow)
        self.assertIn('"$EMBEDDED_SIDESTORE_REF"', workflow)
        patcher = (ROOT / "scripts/patch_v3_service.py").read_text(encoding="utf-8")
        self.assertIn('"--verify-headless-ui-adapters"', patcher)
        self.assertIn('git", "-C", str(side), "show", f"{pinned_ref}:{relative}"', patcher)
        self.assertIn("actual != expected", patcher)

    def test_standalone_refresh_run_does_not_reuse_stale_scheduler_identity(self):
        refresh = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        self.assertIn("V3RefreshRunIdentitySelection.select(", refresh)
        self.assertIn("schedulerRunID: schedulerRunID", refresh)
        self.assertIn('forKey: "liveContainerAutoRefreshActiveRunID"', refresh)
        self.assertIn("if !selectedRun.schedulerOwned", refresh)
        self.assertIn('removeObject(forKey: "liveContainerAutoRefreshExpectedRunID")', refresh)
        self.assertIn("V3DirectRefreshRunClaimPolicy.defaultsKey", refresh)
        self.assertIn("V3DirectRefreshRunClaimPolicy.isActive", refresh)
        self.assertIn("V3DirectRefreshPreflightPolicy.isBlocked", refresh)
        self.assertIn('forKey: "liveContainerAutoRefreshHostHandoff"', refresh)
        self.assertIn('forKey: "liveContainerAutoRefreshUncertainMutationRunID"', refresh)
        perform = refresh[refresh.index("private func performRefresh(identifier:"):]
        perform = perform[:perform.index("private func releaseRefreshAdmission")]
        preflight_positions = [index for index in range(len(perform))
            if perform.startswith("V3DirectRefreshPreflightPolicy.isBlocked", index)]
        self.assertEqual(len(preflight_positions), 2)
        self.assertLess(preflight_positions[0], perform.index("try await ensureServiceConnected()"))
        self.assertLess(perform.index("try await ensureServiceConnected()"), preflight_positions[1])
        self.assertLess(preflight_positions[1], perform.index("let token = UUID()"))
        self.assertLess(perform.index("try await ensureServiceConnected()"), perform.index("v3RefreshToken = token"))
        self.assertLess(perform.index("v3RefreshToken = token"), perform.index("sharedDefaults.set([\"run_id\": directClaimID"))
        admission = perform.index('operation: "refreshAdmissionBegin"')
        renewal = perform.index("sharedDefaults.set([\"run_id\": directClaimID", admission)
        self.assertLess(admission, renewal)
        startup = (ROOT / "scripts/patch_combined_service_startup.py").read_text(encoding="utf-8")
        self.assertIn('handler = handler.replace("/*REFRESH_READINESS*/", "")', startup)
        self.assertNotIn('let status = try await V3ServiceBridge.shared.request(operation: "snapshot")', refresh)
        self.assertIn("V3RefreshAdmissionLease.lifetime + 60", perform)
        bridge = (ROOT / "scripts/patch_livecontainer_autorefresh.py").read_text(encoding="utf-8")
        self.assertIn("startScheduledRefresh(", bridge)
        self.assertIn("runID: runID.uuidString", bridge)
        scheduler = (ROOT / "scripts/templates/livecontainer_refresh_scheduler.swift").read_text(encoding="utf-8")
        self.assertIn("LiveContainerRefreshBridge.refreshAllApps(runID: runID)", scheduler)
        self.assertIn("V3DirectRefreshRunClaimPolicy.isActive", scheduler)

    def test_refresh_terminal_intent_recovers_crash_after_active_release(self):
        scheduler = (ROOT / "scripts/templates/livecontainer_refresh_scheduler.swift").read_text(encoding="utf-8")
        verified = scheduler[scheduler.index("private static func markVerified"):scheduler.index("private static func markFailed")]
        failed = scheduler[scheduler.index("private static func markFailed"):scheduler.index("private static func verifyPendingHostHandoff")]
        self.assertLess(verified.index('runRecord["terminal_intent"] = "verified"'), verified.index("endRun("))
        self.assertLess(verified.index("endRun("), verified.index('runRecord["state"] = "completed"'))
        self.assertLess(failed.index('runRecord["terminal_intent"] = "failed"'), failed.index("endRun("))
        self.assertLess(failed.index("endRun("), failed.index('runRecord["state"] = "failed"'))
        self.assertIn("recoverOrphanedRunLedger()", scheduler)
        self.assertIn("V3RefreshTerminalRecoveryPolicy.action", scheduler)
        self.assertIn("private static func terminalManifestSummary", scheduler)
        self.assertIn('"requested_count": (manifest["requested_ids"] as? [String] ?? []).count', scheduler)
        self.assertIn('runRecord.removeValue(forKey: "manifest")', scheduler)
        self.assertIn('["completed", "failed"].contains(currentState)', scheduler)
        host_handoff = scheduler[scheduler.index("private static func verifyPendingHostHandoff"):
                                 scheduler.index("private static func recoverOrphanedRunLedger")]
        self.assertIn('health: "HOST_REFRESH_FAILED"', host_handoff)
        self.assertIn('result: "host_refresh_failed"', host_handoff)
        self.assertIn("markFailed(runID: runID", host_handoff)

    def test_service_and_startup_adapters_compose_on_pinned_sources(self):
        startup = module("patch_combined_service_startup")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name); roots = self.fixture(directory); self.apply(roots)
            with mock.object(startup.subprocess, "check_output", side_effect=lambda args, **kw: service.PINS[0 if args[2] == str(roots[0]) else 1]):
                startup.patch(*roots, "v3")
                first = self.snapshot(directory); startup.patch(*roots, "v3")
                self.assertEqual(first, self.snapshot(directory))
            source = (roots[0] / "SideStoreSupport/SideStore.swift").read_text(encoding="utf-8")
            self.assertNotIn("__v3_connect", source)
            self.assertNotIn("bookmarkForURL(sideStoreHomeURL)!", source)
            client = (roots[0] / "SideStoreSupport/SideStoreClient.swift").read_text(encoding="utf-8")
            self.assertIn("CombinedVerification.sanitized(payload", client)
            self.assertNotIn("reportRefreshResult(error.localizedDescription", client)

    def test_anchor_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            path = roots[1] / "AltStore/SceneDelegate.swift"
            path.write_text(path.read_text().replace("guard let _ = (scene as? UIWindowScene)", "guard let changed = (scene as? UIWindowScene)"))
            before = self.snapshot(directory)
            with self.assertRaises(SystemExit):
                self.apply(roots)
            self.assertEqual(before, self.snapshot(directory))

    def test_wrong_revision_writes_nothing(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            with mock.object(service.subprocess, "check_output", return_value="unknown"):
                with self.assertRaises(SystemExit):
                    service.patch(directory, directory)
            self.assertEqual({}, self.snapshot(directory))

    def test_owner_boundary(self):
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        for token in ("CoreData", "NSManagedObject", "appleIDXcodeToken"):
            self.assertNotIn(token, host + bridge)
        self.assertIn("V3JITLessStatusReader", host)
        self.assertIn("livecontainer://jitless-setup", host)
        self.assertNotIn("syncJITLessCertificate", host)
        self.assertNotIn('account: "signingCertificate"', host)
        self.assertNotIn("writeJITLessCertificate", host)
        self.assertNotIn("v3SideStoreStatusSnapshot", host)
        self.assertIn("pending.removeValue", bridge)
        self.assertIn("decoded[\"id\"] as? String == id", bridge)

    def test_headless_service_has_no_presentation(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        for token in ("Self.presenter", "presentingViewController:", "UIHostingController",
                      "UINavigationController(rootViewController", "CertificatesView(",
                      "DeveloperServicesView(", "importPairingFile(presentingVC",
                      "presentConfirmationAlert", "V3RemoteServiceView", "serviceWindow",
                      "makeKeyAndVisible", "AppManager.shared.signIn(presentingViewController",
                      "AuthManager.shared.signIn(presentingViewController"):
            self.assertNotIn(token, service + runtime)
        self.assertNotIn("present(", service + runtime)
        self.assertNotIn("dismiss(", service + runtime)

    def test_source_remove_preserves_busy_and_service_readiness_causes(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        receive = service[service.index("private func receive("):service.index("private func invalidRequestReply")]
        self.assertIn("safeCause: .sourceRemoveBusy", receive)
        self.assertIn("case .notReady = serviceError", receive)
        self.assertIn("case .notReady = headlessError", receive)
        self.assertIn("code: .notReady, id: id, retryable: true", receive)
        self.assertIn("safeCause: .sourceRemoveFailed", receive)

    def test_headless_operation_inventory(self):
        contract = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        operations = contract[contract.index("static let operations"):
                              contract.index("static let readOperations")]
        for removed in ("panel", "signIn", "install", "refreshApp", "addSource",
                        "removeSource", "importPairing", "update", "activate",
                        "deactivate", "remove", "delete", "backup", "restore",
                        "installURL", "setSetting"):
            self.assertNotIn(f'"{removed}"', operations)
        self.assertNotIn('"installSharedIPA"', operations,
            "installSharedIPA is a payload kind behind opStart, not a wire operation")
        for op in ("authBegin", "authPoll", "authRespond", "authCancel", "opStart", "opPoll",
                   "opAnswer", "opCancel", "certList", "certSetActive", "certDelete",
                   "certPortalList", "certRevoke", "certCreate", "devTeams", "devDevices",
                   "devAppIDs", "devGroups", "devProfiles", "sourcePreview", "sourceAddConfirmed",
                   "sourceRemoveConfirmed", "pairingImportData", "settingsGet", "settingsSet",
                   "anisetteList", "anisetteReset", "anisetteSync", "sidesignGet", "sidesignSet",
                   "sidesignReset", "sidesignImport", "sidesignExport", "logTail",
                   "healthSnapshot", "accountExport", "accountImport"):
            self.assertIn(f'"{op}"', contract)
            self.assertIn(f'case "{op}"', service)
        self.assertIn('"payload"', contract)

    def test_prompt_kinds_are_closed_set(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        kinds = set(re.findall(r'kind: "([a-zA-Z]+)"', runtime))
        expected = {"credentials", "twoFactor", "team", "accountRepair", "provisioningError",
                    "postAuth", "revocation", "resign", "anisetteOutdated", "bundleIDMismatch",
                    "permissions", "extensions", "unsupportedVersion", "bundleIDOverride",
                    "appGroupMismatch"}
        self.assertEqual(kinds, expected)
        self.assertIn("V3PromptSection", host)
        self.assertIn("V3SignInView", host)


class RefreshAdmissionTemplateTests(unittest.TestCase):
    def test_native_refresh_contention_has_typed_busy_guidance(self):
        refresh = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        perform = refresh[refresh.index("func performRefresh(identifier:"):]
        perform = perform[:perform.index("func releaseRefreshAdmission")]
        self.assertGreaterEqual(perform.count("safeCause: .operationInProgress"), 3)

    def test_operation_prompt_does_not_use_auth_only_revision_state(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        auth = runtime[runtime.index("final class V3HeadlessAuthHandler:"):
                       runtime.index("final class V3HeadlessPipelineHandler:")]
        operation = runtime[runtime.index("final class V3HeadlessPipelineHandler:"):
                            runtime.index("// MARK: - Headless operation sessions")]
        self.assertIn("center.sessions[sessionID]?.revision += 1", auth)
        operation_prompt = operation[operation.index("private func ask(kind:"):]
        operation_prompt = operation_prompt[:operation_prompt.index("func resolveBundleIDMismatch")]
        self.assertNotIn(".revision", operation_prompt)

    def test_refresh_owner_brackets_direct_refresh_and_confirms_release(self):
        refresh = (ROOT / "scripts/templates/combined_refresh_handler.swift").read_text(encoding="utf-8")
        bridge = (ROOT / "scripts/templates/v3_service_bridge.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        begin = refresh.index('operation: "refreshAdmissionBegin"')
        dispatch = refresh.index("client.refreshAllApps(", begin)
        release = refresh.index("await releaseRefreshAdmission(run,", dispatch)
        self.assertLess(refresh.index("v3RefreshAdmissionRunID = run"), begin)
        self.assertLess(begin, dispatch)
        self.assertLess(dispatch, release)
        self.assertIn("v3RefreshAdmissionRunID", bridge)
        self.assertIn("ownsRefreshAdmissionControl", bridge)
        self.assertIn('reply["runID"] as? String == runID', refresh)
        self.assertIn('strictBool(reply["released"]) == true', refresh)
        self.assertIn("self.v3_stopService()", refresh)
        self.assertIn("v3RefreshDispatchedRunID", refresh)
        self.assertIn('payload: ["state": terminalState]', refresh)
        self.assertIn('kind: "refreshAll"', service)
        self.assertIn("V3OperationRecoveryJournal.settleRefreshAdmission", service)
        self.assertIn("refreshAdmission.restoreLost(runID:", service)
        self.assertIn("refreshAdmission.release(requestID: target)", service)
        self.assertIn('cancellationReply["refreshAdmissionReleased"] = true', service)
        self.assertIn("pendingRefreshAdmissionRequests", service)
        end_case = service[service.index('case "refreshAdmissionEnd":'):]
        self.assertIn("UUID(uuidString: target)", end_case)
        self.assertIn("V3CancellationRecoveryReplyPolicy.mayCancelRetirement", bridge)
        self.assertIn("V3RequestRetirementPolicy", bridge)

    def test_auth_begin_request_expiry_cancels_its_reserved_session(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        self.assertIn("pendingAuthStartSessions[id] = session", service)
        self.assertIn("pendingAuthStartSessions[id] = nil", service)
        self.assertIn("auth.cancelBeforeBegin(id: session)", service)
        self.assertIn("operations.activeMutationID", service)
        self.assertIn("hasConflictingOperationMutation", service)
        self.assertIn("operations.activeMutationID != nil || refreshAdmission.isActive", service)
        self.assertIn("V3MutationReplyCacheBudget.responseCountLimit(isControlResponse: controlReply)", service)
        self.assertIn("V3MutationReplyCacheBudget.minimumReplyBytesToAdmit(operation: operation)", service)
        self.assertIn("!cacheResponse ||", service)
        self.assertIn("completedCacheBudget.remove(byteCount)", service)
        self.assertIn('["opStart", "authBegin", "authRetryProvisioning"].contains(operation)', service)
        begin = runtime[runtime.index("func begin(deadline: Date, mode: BeginMode = .interactive,"):]
        begin = begin[:begin.index("    func run(id: String)")]
        self.assertIn("let requestExpired = Task.isCancelled", begin)
        self.assertIn("requestCancelled: requestExpired", begin)

    def test_terminal_reply_cache_reserves_capacity_for_user_continuations(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        budget = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        self.assertIn("V3MutationReplyCacheBudget.isControlReply(operation: operation)", service)
        self.assertIn("V3MutationReplyCacheBudget.shouldCacheResponse(operation: operation)", service)
        self.assertIn("if mutation && cacheResponse", service)
        self.assertIn("completedCacheBudget.canReserve(", service)
        self.assertIn("completedCacheBudget.record(encoded.count, controlResponse: controlReply)", service)
        for control in ("refreshAdmissionEnd", "authBegin", "authRetryProvisioning", "opStart"):
            self.assertIn(f'"{control}"', budget)
        self.assertIn('!["authRespond", "opAnswer"].contains(operation)', budget)

    def test_request_deadline_task_is_cancelled_when_operation_settles(self):
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        self.assertIn("private var deadlineTasks: [String: Task<Void, Never>]", service)
        self.assertIn("deadlineTasks.removeValue(forKey: id)?.cancel()", service)
        self.assertIn("deadlineTasks[id] = Task { @MainActor in", service)


class WireExecutionTests(unittest.TestCase):
    def test_shipped_wire_policies_bind_request_ids_and_coredata_entity_targets(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "scripts/templates/v3_wire_contract.swift").read_text() + r'''
let now = Date(timeIntervalSince1970: 100000)
let storeID = UUID().uuidString
let installed = "x-coredata://\(storeID)/InstalledApp/p1"
let storeApp = "x-coredata://\(storeID)/StoreApp/p2"
let session = UUID().uuidString
func encode(_ value: [String: Any]) -> Data {
    try! PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
}
func request(_ operation: String, target: String, payload: [String: Any]? = nil) -> [String: Any] {
    var result: [String: Any] = ["version": 1, "id": UUID().uuidString, "operation": operation,
        "target": target, "deadline": now.addingTimeInterval(30)]
    if let payload { result["payload"] = payload }
    return result
}
precondition(V3WireContract.decodeRequest(encode(request("appIcon", target: installed)), now: now) != nil)
precondition(V3WireContract.decodeRequest(encode(request("appIcon", target: storeApp)), now: now) == nil)
precondition(V3WireContract.decodeRequest(encode(request("jit", target: installed)), now: now) != nil)
precondition(V3WireContract.decodeRequest(encode(request("jit", target: storeApp)), now: now) == nil)
let install = request("opStart", target: "", payload: ["kind": "install", "target": storeApp, "session": session])
precondition(V3WireContract.decodeRequest(encode(install), now: now) != nil)
var wrongInstallEntity = install
wrongInstallEntity["payload"] = ["kind": "install", "target": installed, "session": session]
precondition(V3WireContract.decodeRequest(encode(wrongInstallEntity), now: now) == nil)
for kind in ["update", "refreshApp", "activate", "deactivate", "remove", "delete", "backup", "restore"] {
    let valid = request("opStart", target: "", payload: ["kind": kind, "target": installed, "session": session])
    precondition(V3WireContract.decodeRequest(encode(valid), now: now) != nil, kind)
    var wrong = valid
    wrong["payload"] = ["kind": kind, "target": storeApp, "session": session]
    precondition(V3WireContract.decodeRequest(encode(wrong), now: now) == nil, kind)
}
let dispatched = Data("original-dispatched-opStart".utf8)
let conflictingBytes = Data("same-id-different-command".utf8)
let dispatchedFingerprint = V3RequestReplayPolicy.fingerprint(dispatched)
precondition(V3RequestReplayPolicy.matchesInFlight(cachedFingerprint: dispatchedFingerprint,
    incomingRequestData: dispatched))
precondition(V3RequestReplayPolicy.isIdentifierCollision(cachedFingerprint: dispatchedFingerprint,
    incomingRequestData: conflictingBytes))
precondition(!V3RequestReplayPolicy.mayClaimNotDispatched(operation: "opStart", identifierCollision: true))
precondition(V3RequestReplayPolicy.mayClaimNotDispatched(operation: "opStart", identifierCollision: false))
precondition(!V3RequestReplayPolicy.mayClaimNotDispatched(operation: "snapshot", identifierCollision: false))
let cancelID = UUID().uuidString
let cancel = ["version": 1, "id": cancelID, "operation": "cancel", "target": UUID().uuidString,
    "deadline": now.addingTimeInterval(30), "payload": ["scope": "request"]] as [String: Any]
let exactCancelReplay = encode(cancel)
let cancelFingerprint = V3RequestReplayPolicy.fingerprint(exactCancelReplay)
precondition(V3RequestReplayPolicy.requiresCompletedReply(operation: "cancel"))
precondition(V3RequestReplayPolicy.requiresCompletedReply(operation: "authCancel"))
precondition(V3RequestReplayPolicy.requiresCompletedReply(operation: "opCancel"))
precondition(V3RequestReplayPolicy.matches(cachedFingerprint: cancelFingerprint,
    incomingRequestData: exactCancelReplay), "exact cancel replay keeps its completed reply")
var changedCancel = cancel
changedCancel["target"] = UUID().uuidString
precondition(V3RequestReplayPolicy.isIdentifierCollision(cachedFingerprint: cancelFingerprint,
    incomingRequestData: encode(changedCancel)), "same cancel ID with another target is a collision")
changedCancel = cancel
changedCancel["payload"] = ["scope": "auth"]
precondition(V3RequestReplayPolicy.isIdentifierCollision(cachedFingerprint: cancelFingerprint,
    incomingRequestData: encode(changedCancel)), "same cancel ID with another scope is a collision")
precondition(!V3RequestReplayPolicy.mayClaimNotDispatched(operation: "cancel", identifierCollision: true))
print("V3 request identity and Core Data target policies PASS")
''')
            executable = directory / "wire-policy-tests"
            compiled = subprocess.run([compiler, str(program), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 request identity and Core Data target policies PASS", result.stdout)

    def test_shipped_native_callback_settles_once(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        source = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text()
        gate = source[source.index("final class V3ServiceCallbackGate:"):
                      source.index("// V3_NATIVE_CALLBACK_GATE_END")]
        callback_start = source.index("    private func callback(")
        callback_open = source.index("{", callback_start)
        depth = 1
        callback_end = callback_open + 1
        while depth:
            depth += (source[callback_end] == "{") - (source[callback_end] == "}")
            callback_end += 1
        callback = source[callback_start:callback_end]
        callback = callback.replace("private func callback", "func callback")
        program = "import Foundation\n" + gate + "\nstruct Adapter {\n" + callback + "}\n" + r'''
enum Failure: Error { case native }
@main struct CallbackTests {
    static func main() async throws {
        let adapter = Adapter()
        // Executes the production callback adapter, not a model of the gate.
        try await adapter.callback { done in
            done(.success(()))
            done(.failure(Failure.native))
            done(.success(()))
        }
        do {
            try await adapter.callback { done in
                done(.failure(Failure.native))
                done(.success(()))
            }
            preconditionFailure("native failure was lost")
        } catch Failure.native {}
        for _ in 0..<100 {
            try await adapter.callback { done in
                DispatchQueue.concurrentPerform(iterations: 16) { _ in done(.success(())) }
            }
        }
        // A cancelled task must keep awaiting the native terminal callback. Releasing
        // the continuation on cancellation would free the service mutation gate early.
        let nativeFinished = DispatchSemaphore(value: 0)
        let task = Task {
            try await adapter.callback { done in
                DispatchQueue.global().asyncAfter(deadline: .now() + 0.03) {
                    nativeFinished.signal()
                    done(.success(()))
                    done(.failure(Failure.native)) // Late callback is ignored.
                }
            }
        }
        task.cancel()
        try await task.value
        precondition(nativeFinished.wait(timeout: .now()) == .success)
        print("V3 native callback exactly-once PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            swift = directory / "main.swift"
            swift.write_text(program)
            executable = directory / "callback-tests"
            compiled = subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 native callback exactly-once PASS", result.stdout)

    def test_shipped_bridge_lifecycle(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "tests/fixtures/v3_bridge_harness.swift").read_text() +
                               (ROOT / "scripts/templates/combined_failure.swift").read_text() +
                               (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text() +
                               (ROOT / "scripts/templates/combined_service_connection.swift").read_text() +
                               (ROOT / "scripts/templates/v3_wire_contract.swift").read_text() +
                               (ROOT / "scripts/templates/v3_service_bridge.swift").read_text())
            executable = directory / "bridge-tests"
            compiled = subprocess.run([compiler, "-parse-as-library", str(program), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 lifecycle PASS", result.stdout)

    def test_shipped_decoder_rejects_secrets_stale_and_malformed_requests(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "scripts/templates/v3_wire_contract.swift").read_text() + r'''
let now = Date(timeIntervalSince1970: 100000)
let valid: [String: Any] = ["version": 1, "id": UUID().uuidString, "operation": "snapshot",
                          "target": "", "deadline": now.addingTimeInterval(30)]
func encode(_ value: [String: Any]) -> Data {
    try! PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
}
precondition(V3WireContract.decodeRequest(encode(valid), now: now) != nil)
var booleanVersion = valid; booleanVersion["version"] = true
precondition(V3WireContract.decodeRequest(encode(booleanVersion), now: now) == nil)
var page = valid; page["operation"] = "catalog"; page["cursor"] = 50
precondition(V3WireContract.decodeRequest(encode(page), now: now) != nil)
for cursor in [-1, 1_000_001, true, "50", 1.5] as [Any] {
    page["cursor"] = cursor
    precondition(V3WireContract.decodeRequest(encode(page), now: now) == nil)
}
var nonCatalog = valid; nonCatalog["cursor"] = 0
precondition(V3WireContract.decodeRequest(encode(nonCatalog), now: now) == nil)
for (key, value) in [("password", "secret"), ("token", "secret"), ("certificate", "secret"),
                     ("operation", "arbitrarySelector"), ("id", "bad"), ("target", String(repeating: "a", count: 4097))] {
    var request = valid
    request[key] = value
    precondition(V3WireContract.decodeRequest(encode(request), now: now) == nil)
}
for date in [now.addingTimeInterval(-1), now, now.addingTimeInterval(611)] {
    var request = valid; request["deadline"] = date
    precondition(V3WireContract.decodeRequest(encode(request), now: now) == nil)
}
var setting = valid; setting["operation"] = "settingsSet"; setting["target"] = ""
setting["payload"] = ["key": "isBetaUpdatesEnabled", "type": "bool", "bool": true]
precondition(V3WireContract.decodeRequest(encode(setting), now: now) != nil)
setting["payload"] = ["key": "isBetaUpdatesEnabled", "type": "bool", "bool": 1]
precondition(V3WireContract.decodeRequest(encode(setting), now: now) == nil)
var sideSignConfig = valid; sideSignConfig["operation"] = "sidesignSet"
sideSignConfig["target"] = ""
sideSignConfig["payload"] = ["secretToken": UUID().uuidString]
precondition(V3WireContract.decodeRequest(encode(sideSignConfig), now: now) != nil)
sideSignConfig["payload"] = ["config": "{\"Authorization\":\"Bearer SECRET\"}"]
precondition(V3WireContract.decodeRequest(encode(sideSignConfig), now: now) == nil)
var legacySetting = valid; legacySetting["operation"] = "setSetting"; legacySetting["target"] = "betaUpdates"
legacySetting["value"] = true
precondition(V3WireContract.decodeRequest(encode(legacySetting), now: now) == nil)
precondition(V3WireContract.decodeRequest(Data(repeating: 0, count: 16385), now: now) == nil)
precondition(V3WireContract.decodeRequest(Data([1, 2, 3]), now: now) == nil)
print("V3 wire contract PASS")
''')
            executable = directory / "wire-tests"
            subprocess.run([compiler, str(program), "-o", str(executable)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn("PASS", result.stdout)

    def test_headless_wire_contract_accepts_payload_and_session_ops(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "scripts/templates/v3_wire_contract.swift").read_text() + r'''
let now = Date(timeIntervalSince1970: 100000)
func encode(_ value: [String: Any]) -> Data {
    try! PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
}
func base(_ operation: String) -> [String: Any] {
    ["version": 1, "id": UUID().uuidString, "operation": operation,
     "target": UUID().uuidString, "deadline": now.addingTimeInterval(30)]
}
for operation in ["authBegin", "authRetryProvisioning", "authPoll", "authRespond", "opStart", "opPoll", "opAnswer",
                  "certList", "certRevoke", "devTeams", "sourcePreview", "sourceAddConfirmed",
                  "pairingImportData", "settingsGet", "settingsSet", "anisetteList", "ipaActiveTokens",
                  "sidesignGet", "logTail", "healthSnapshot", "accountExport", "accountImport"] {
    var request = base(operation)
    if operation == "authBegin" || operation == "authRetryProvisioning" {
        let session = UUID().uuidString
        request["target"] = session
        request["payload"] = ["session": session, "sessionDeadline": now.addingTimeInterval(600)]
    } else if operation == "opStart" {
        request["target"] = ""
        request["payload"] = ["kind": "installSharedIPA", "target": UUID().uuidString.lowercased(),
                              "session": UUID().uuidString]
    } else if operation == "authRespond" || operation == "opAnswer" {
        request["payload"] = ["prompt": "prompt-1", "secretToken": UUID().uuidString]
    } else if operation == "accountExport" {
        request["target"] = ""
        request["payload"] = ["secretToken": UUID().uuidString, "includeApple": false]
    } else if operation == "accountImport" {
        request["payload"] = ["secretToken": UUID().uuidString]
    } else if operation == "sourcePreview" || operation == "sourceAddConfirmed" {
        request["target"] = "https://example.invalid/source.json"
    } else if operation == "ipaActiveTokens" {
        request["target"] = ""
    } else if operation == "anisetteList" {
        request["target"] = ""
    } else if operation == "settingsSet" {
        request["target"] = ""
        request["payload"] = ["key": "isBackgroundRefreshEnabled", "type": "bool", "bool": false]
    } else if ["certList", "devTeams", "settingsGet", "sidesignGet", "logTail", "healthSnapshot"].contains(operation) {
        request["target"] = ""
    } else {
        request.removeValue(forKey: "payload")
    }
    precondition(V3WireContract.decodeRequest(encode(request), now: now) != nil, operation)
    precondition(V3WireContract.readOperations.contains(operation) == ["authPoll", "opPoll", "certList", "devTeams", "sourcePreview", "settingsGet", "anisetteList", "ipaActiveTokens", "sidesignGet", "logTail", "healthSnapshot"].contains(operation), operation)
}
let leasedToken = UUID().uuidString.lowercased()
let tokenReply: [String: Any] = ["version": 1, "id": UUID().uuidString,
    "ok": true, "result": ["tokens": [leasedToken]]]
let encodedReply = try PropertyListSerialization.data(fromPropertyList: tokenReply, format: .binary, options: 0)
let decodedReply = try PropertyListSerialization.propertyList(from: encodedReply, format: nil) as! [String: Any]
let decodedTokens = ((decodedReply["result"] as! [String: Any])["tokens"] as! [String])
precondition(decodedTokens == [leasedToken], "the staged IPA lease list survives a property-list reply round trip")
for removed in ["panel", "signIn", "install", "refreshApp", "addSource", "removeSource", "importPairing", "setSetting", "update", "activate", "deactivate", "remove", "delete", "backup", "restore", "installURL", "installSharedIPA"] {
    precondition(V3WireContract.decodeRequest(encode(base(removed)), now: now) == nil, removed)
}
var badPayload = base("opStart")
badPayload["target"] = ""
badPayload["payload"] = "not-a-dict"
precondition(V3WireContract.decodeRequest(encode(badPayload), now: now) == nil)
var unknownAuthField = base("authBegin")
let authSession = UUID().uuidString
unknownAuthField["target"] = authSession
unknownAuthField["payload"] = ["session": authSession, "sessionDeadline": now.addingTimeInterval(600),
                                "client_secret": "must-not-cross-the-wire"]
precondition(V3WireContract.decodeRequest(encode(unknownAuthField), now: now) == nil,
    "authBegin must reject unknown secret-bearing fields")
var validCancel = base("cancel")
validCancel["payload"] = ["scope": "operation"]
precondition(V3WireContract.decodeRequest(encode(validCancel), now: now) != nil)
var missingCancelScope = base("cancel")
missingCancelScope["payload"] = [:]
precondition(V3WireContract.decodeRequest(encode(missingCancelScope), now: now) == nil)
var invalidCancelScope = base("cancel")
invalidCancelScope["payload"] = ["scope": "auth-or-operation"]
precondition(V3WireContract.decodeRequest(encode(invalidCancelScope), now: now) == nil)
var validOperationCancel = base("opCancel")
validOperationCancel["payload"] = ["knownStarted": true]
precondition(V3WireContract.decodeRequest(encode(validOperationCancel), now: now) != nil,
    "the actual host cancellation request includes its knownStarted field")
validOperationCancel["payload"] = ["knownStarted": NSNumber(value: 1)]
precondition(V3WireContract.decodeRequest(encode(validOperationCancel), now: now) == nil,
    "operation cancellation requires a plist Boolean")
var legacyValue = base("snapshot")
legacyValue["target"] = ""
legacyValue["value"] = true
precondition(V3WireContract.decodeRequest(encode(legacyValue), now: now) == nil)
for operation in ["authRespond", "opAnswer"] {
    var safe = base(operation); safe["payload"] = ["prompt": "p1", "secretToken": UUID().uuidString]
    precondition(V3WireContract.decodeRequest(encode(safe), now: now) != nil, operation)
    var raw = base(operation); raw["payload"] = ["prompt": "p1", "answer": ["verificationCode": "123456"]]
    precondition(V3WireContract.decodeRequest(encode(raw), now: now) == nil, operation)
}
var export = base("accountExport"); export["target"] = ""
export["payload"] = ["secretToken": UUID().uuidString, "includeApple": false]
precondition(V3WireContract.decodeRequest(encode(export), now: now) != nil)
export["payload"] = ["password": "backup passphrase", "includeApple": false]
precondition(V3WireContract.decodeRequest(encode(export), now: now) == nil)
var backupImport = base("accountImport"); backupImport["payload"] = ["secretToken": UUID().uuidString]
precondition(V3WireContract.decodeRequest(encode(backupImport), now: now) != nil)
backupImport["payload"] = ["password": "backup passphrase"]
precondition(V3WireContract.decodeRequest(encode(backupImport), now: now) == nil)
print("V3 headless wire contract PASS")
''')
            executable = directory / "headless-wire-tests"
            compiled = subprocess.run([compiler, str(program), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 headless wire contract PASS", result.stdout)

    def test_shipped_prompt_gate_parks_and_resumes_once(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        source = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text()
        gate = source[source.index("enum V3PromptAnswerDisposition:"):]
        gate = gate[:gate.index("\n@MainActor\nfinal class V3HeadlessRuntime")]
        program = "import Foundation\n" + gate + r'''
@main struct PromptGateTests {
    static func main() async throws {
        let center = V3PromptCenter()
        let first = Task { try await center.park(promptID: "p1") }
        try await Task.sleep(nanoseconds: 20_000_000)
        precondition(center.answer(promptID: "p1", answer: ["choice": "proceed"]) == .accepted)
        precondition(center.answer(promptID: "p1", answer: ["choice": "proceed"]) == .alreadySettled)
        precondition(center.answer(promptID: "missing", answer: [:]) == .unavailable)
        let firstAnswer = try await first.value
        precondition(firstAnswer["choice"] == "proceed")
        let second = Task { try await center.park(promptID: "p2") }
        try await Task.sleep(nanoseconds: 10_000_000)
        second.cancel()
        do {
            _ = try await second.value
            preconditionFailure("cancelled park resumed")
        } catch is CancellationError {}
        let third = Task { try await center.park(promptID: "p3") }
        try await Task.sleep(nanoseconds: 10_000_000)
        third.cancel()
        do {
            _ = try await third.value
            preconditionFailure("task cancel did not resume")
        } catch is CancellationError {}
        print("V3 prompt gate PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            swift = directory / "main.swift"
            swift.write_text(program)
            executable = directory / "prompt-gate-tests"
            compiled = subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 prompt gate PASS", result.stdout)


class GsaPreparedTreeTests(unittest.TestCase):
    def test_gsa_connection_close_in_prepared_tree(self):
        side = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not side:
            self.skipTest("Set EMBEDDED_SIDESTORE_TEST_SOURCE to the pinned source checkout")
        auth = Path(side) / "Dependencies/SideSign/Sources/DeveloperPortal/Authentication.swift"
        text = auth.read_text(encoding="utf-8")
        hits = [m.start() for m in re.finditer(r'"Connection": "close"', text)]
        self.assertEqual(len(hits), 2)
        enclosing = []
        for position in hits:
            before = text[:position]
            found = [m.group(1) for m in re.finditer(r"func\s+(\w+)\s*\(", before)][-1]
            enclosing.append(found)
        self.assertEqual(enclosing, ["sendAuthenticationRequest", "makeTwoFactorAuthRequest"])
        self.assertEqual(len(re.findall(r"URLRequest\(", text)), 2)

    def test_no_builder_patch_modifies_sidesign_auth(self):
        scripts = (ROOT / "scripts").glob("*.py")
        for script in scripts:
            content = script.read_text(encoding="utf-8")
            if script.name == "patch_sidesign_privacy.py":
                self.assertIn('LOGGING = Path("Sources/Logging.swift")', content)
                self.assertNotIn("Authentication.swift", content)
                continue
            if script.name == "patch_sidesign_2fa_state.py":
                self.assertIn("Authentication.swift", content)
                self.assertIn("DeveloperPortalAPI.swift", content)
                continue
            self.assertNotIn("DeveloperPortal/Authentication", content)
        medic = (ROOT / "scripts/combined_build_evidence.py").read_text(encoding="utf-8")
        self.assertNotIn("Dependencies/SideSign", medic)

    def test_auth_is_single_flight_without_retry_loops(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        self.assertIn("let previousID = activeID", runtime)
        self.assertIn("mayLaunchCreatedSession", runtime)
        self.assertIn("if let oldTask { await oldTask.value }", runtime)
        auth = runtime.split("final class V3OperationCenter")[0]
        self.assertIn("func cancelAndWait(id: String) async -> Bool", auth)
        self.assertIn("func cancelAndWait(id: String) async -> Bool", runtime)
        auth = runtime.split("final class V3OperationCenter")[0]
        self.assertNotRegex(auth, r"(?m)^\s*while\s")
        for marker in ("[V3_AUTH] BEGIN", "[V3_AUTH] PROMPT", "[V3_AUTH] TERMINAL",
                       "[V3_AUTH] CANCEL", "[V3_OP] PROMPT", "[V3_OP] TERMINAL"):
            self.assertIn(marker, runtime)

    def test_host_starts_auth_only_from_user_flow(self):
        host = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        self.assertEqual(host.count('"authBegin"'), 1)

    def test_shipped_failure_preservation(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text((ROOT / "scripts/templates/combined_failure.swift").read_text() + "\n"
                               + 'let id = UUID().uuidString\nlet known = CombinedFailure(operation: "connect", stage: .serviceReadiness, code: .timedOut, id: id, retryable: true)\nlet kept = CombinedFailure.preserving(known, operation: "connect", stage: .serviceReadiness, id: id)\nprecondition(kept.stage == .serviceReadiness && kept.code == .timedOut)\nprecondition(kept.correlationID == id && kept.retryable == true)\nlet invalid = CombinedFailure(operation: "connect", stage: .serviceReadiness, code: .invalidResponse, id: id)\nlet keptInvalid = CombinedFailure.preserving(invalid, operation: "connect", stage: .command, id: UUID().uuidString)\nprecondition(keptInvalid.stage == .serviceReadiness && keptInvalid.code == .invalidResponse)\nprecondition(keptInvalid.correlationID == id)\nlet plain = NSError(domain: NSCocoaErrorDomain, code: 42)\nlet wrapped = CombinedFailure.preserving(plain, operation: "connect", stage: .serviceReadiness, code: .failed, id: id)\nprecondition(wrapped.stage == .serviceReadiness && wrapped.code == .failed)\nprecondition(wrapped.correlationID == id && wrapped.underlyingCode == 42)\nlet cancelled = CombinedFailure.preserving(CancellationError(), operation: "connect", stage: .serviceReadiness, id: id)\nprecondition(cancelled.code == .failed)\nprint("V3 failure preservation PASS")')
            executable = directory / "preserve-tests"
            compiled = subprocess.run([compiler, str(program), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 failure preservation PASS", result.stdout)
