"""Exercise current owner service boundaries, headless packaging and wire behavior."""
import json
import os
import plistlib
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.owner_source import owner_source, owner_root
from tests.support.product_sources import read_product_source, declaration_end
from scripts import headless_artifact_contract as contract
ROOT = Path(__file__).resolve().parents[1]

class OwnerFile:
    """Read-only path facade: every file is validated against the owner commit."""
    def __init__(self, owner, path=""): self.owner, self.path = owner, path
    def __truediv__(self, path): return OwnerFile(self.owner, str(Path(self.path) / path))
    def read_text(self, **kwargs): return owner_source(self.path, self.owner)
    def read_bytes(self): return self.read_text().encode("utf-8")


def owner_trees():
    return OwnerFile("LiveContainer"), OwnerFile("SideStore")


class ServiceOwnerTests(unittest.TestCase):
    @staticmethod
    def swift_declaration(source, signature):
        if source.count(signature) != 1:
            raise AssertionError("Missing or ambiguous production declaration: " + signature)
        start = source.index(signature)
        return source[start:declaration_end(source, start)]

    def test_connection_config_is_generated_as_backend_code_not_a_view_model(self):
        with tempfile.TemporaryDirectory() as name:
            roots = owner_trees()

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
            self.assertIn("get { UserDefaults.standard.useLocalVPN }", backend)
            self.assertIn("getConnectionMode: { config.connectionMode }", wrapper)


    def test_connection_config_settings_update_is_live_for_captured_minimuxer_binding(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; generated connection settings harness runs in macOS CI")
        with tempfile.TemporaryDirectory() as name:
            roots = owner_trees()

            side = roots[1]
            generated_config = (side / "SideStore/Core/DeviceApi/ConnectionConfig.swift").read_text(encoding="utf-8")
            wrapper = (side / "SideStore/Core/DeviceApi/MinimuxerWrapper.swift").read_text(encoding="utf-8")
            binding_match = re.search(r"getConnectionMode:\s*\{\s*([^{}]+?)\s*\}", wrapper)
            self.assertIsNotNone(binding_match, "the generated Minimuxer binding must expose its live getter")
            binding_expression = binding_match.group(1)
            self.assertEqual(binding_expression, "config.connectionMode")

            runtime = read_product_source("v3_headless_runtime.swift")
            declarations_start = runtime.index("    static let boolSettings: Set<String> =")
            declarations_end = runtime.index("    static func settingsGet()", declarations_start)
            settings_declarations = runtime[declarations_start:declarations_end]
            settings_set = self.swift_declaration(runtime, "    static func settingsSet(payload: [String: Any]) throws")

            wire = read_product_source("v3_wire_contract.swift")
            strict_bool = self.swift_declaration(wire, "    static func strictBool(_ value: Any?) -> Bool?")
            strict_int = self.swift_declaration(wire, "    static func strictInt(_ value: Any?) -> Int?")
            wire_defaults = f"enum V3WireContract {{\n{strict_bool}\n{strict_int}\n}}"

            defaults_file = owner_source("AltStore/Core/Extensions/UserDefaults+AltStore.swift", "SideStore")
            local_vpn_default = self.swift_declaration(defaults_file, "    @objc var useLocalVPN: Bool")

            config_without_module_import = generated_config.replace("import Minimuxer\n", "")
            harness = f'''import Foundation
import CoreFoundation

enum DeviceConnectionMode: Equatable {{ case localVPN, remoteServer }}
enum AppConstants {{
    enum Connection {{ static let defaultRemoteServerIP = "192.0.2.1" }}
    enum Proxy {{ static let address = "127.0.0.1"; static let defaultPort: UInt16 = 62078 }}
    enum Minimuxer {{ static let remotePairingPort: UInt16 = 62078; static let defaultTCPProbeTimeoutMs = 1000 }}
}}
enum V3SideStoreServiceError: Error {{ case invalidRequest }}
// The extension-choice preference is outside this connection-settings harness.
enum HarnessExtensionChoice {{ case promptUser, keepAll }}
extension UserDefaults {{
    var customizeAppExtensions: HarnessExtensionChoice {{
        get {{ .promptUser }}
        set {{ }}
    }}
}}
var remotePairingPortCache: UInt16 = 62078
var deviceProbeTimeoutCache = 1000
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


    def test_pipeline_bundle_prompt_is_headless(self):
        source = owner_source("SideStore/Handlers/PipelineHandler.swift", "SideStore")
        override = self.swift_declaration(source, "func resolveBundleIDOverride(initialBundleID: String)")
        self.assertIn("return (initialBundleID, true)", override)
        for removed in ("AppendTeamIDCheckboxView", "AppExtensionViewHostingController", "ReviewPermissionsViewController"):
            self.assertNotIn(removed, source)


    def test_app_manager_preserves_backend_activation_without_ui_wrapper(self):
        source = owner_source("AltStore/Managing Apps/AppManager.swift", "SideStore")
        for removed in ("func deactivateApps(for:", "self.deactivateApps(for:"):
            self.assertNotIn(removed, source)
        for retained in ("func deactivate(_ installedApp: InstalledApp", "performSingleOperation(.deactivate(installedApp)",
                         "func activate(_ installedApp: InstalledApp", "performSingleOperation(.activate(installedApp)"):
            self.assertIn(retained, source)
        runtime = read_product_source("v3_headless_runtime.swift")
        for command in ('case "activate": operation = .activate(app)', 'case "deactivate": operation = .deactivate(app)',
                        "AppManager.shared.pipelineRunner.performSingleOperation(operation"):
            self.assertIn(command, runtime)


    def test_persisted_side_sign_errors_drop_provider_text_from_core_data_history(self):
        generated_manager = owner_source("AltStore/Managing Apps/AppManager.swift", "SideStore")
        log_method = self.swift_declaration(generated_manager, "func log(_ error: Error")
        self.assertIn("V3PersistedErrorSanitizer.sanitize(error as NSError)", log_method)
        self.assertNotIn("sanitizedForSerialization()", log_method)
        self.assertIn("source.error = V3PersistedErrorSanitizer.sanitize(error as NSError)", generated_manager)
        self.assertIn("V3PersistedErrorSanitizer.sanitize(mergeError as NSError)", generated_manager)
        self.assertNotIn("source.error = error.sanitizedForSerialization()", generated_manager)
        self.assertNotIn("(mergeError as NSError).sanitizedForSerialization()", generated_manager)

        generated_history = owner_source("AltStore/Core/Model/RefreshAttempt.swift", "SideStore")
        self.assertIn("V3PersistedErrorSanitizer.refreshHistoryDescription(for: error)", generated_history)
        self.assertNotIn("error.localizedDescription", generated_history)

        generated_intent = owner_source("AltStore/Intents/App Intents/RefreshAllAppsIntent.swift", "SideStore")
        intent_error = self.swift_declaration(generated_intent, "class IntentError:")
        self.assertIn("V3PersistedErrorSanitizer.sanitize(error as NSError)", intent_error)
        self.assertIn('return "\\(self.localizedDescription)"', intent_error)

        merge_policy = owner_source("AltStore/Core/Model/MergePolicies/MergePolicy.swift", "SideStore")
        merge_serializer = self.swift_declaration(merge_policy, "func serialized(withFailure failure: String)")
        self.assertIn("self as NSError).withLocalizedFailure(failure).sanitizedForSerialization()", merge_serializer)
        self.assertIn("throw nsError", merge_policy)

        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; generated sanitizer execution runs in macOS CI")

        helper = self.swift_declaration(generated_manager, "enum V3PersistedErrorSanitizer")
        harness = '''import Foundation
import AppIntents
''' + helper + '''
''' + intent_error + '''

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
let shortcutError = IntentError(original)
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
precondition(shortcutError.localizedDescription == V3PersistedErrorSanitizer.safeDescription)
precondition(String(localized: shortcutError.localizedStringResource) == V3PersistedErrorSanitizer.safeDescription)
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
        lambda_show = lambda path: owner_source(path, "SideStore")
        boot_generated = owner_source("SideStore/AppBootManager.swift", "SideStore")
        for removed in ("needsPairingPrompt", "needsSideJITPrompt", "promptForPairing(",
                        "presentPairingFileAlert", "isSideJITServerDetected", "UIViewController", "import UIKit"):
            self.assertNotIn(removed, boot_generated)
        self.assertIn("public nonisolated func performBootSequence() async", boot_generated)
        self.assertIn("SideJITManager.shared.askForNetwork()", boot_generated)
        self.assertIn("PairingFileManager.shared.fetchPairingFile()", boot_generated)
        self.assertIn("V3_HEADLESS_BOOT_SIDEJIT_DETECTION_REMOVED_V1", boot_generated)

        jit_generated = owner_source("SideStore/Core/JIT/SideJITManager.swift", "SideStore")
        self.assertNotIn("@MainActor", jit_generated)
        for removed in ("presentJITPrompt", "isSideJITServerDetected", "UIAlertController", "UIViewController", "import UIKit"):
            self.assertNotIn(removed, jit_generated)
        for retained in ("public func resolveServerURL() async -> String", "public func askForNetwork() async",
                         "resolveAddressIfNeeded", "inet_pton"):
            self.assertIn(retained, jit_generated)
        enable_jit = lambda_show("SideStore/Core/Operations/StandaloneOperations/EnableJITOperation.swift")
        self.assertIn("SideJITManager.shared.resolveServerURL()", enable_jit)

        pairing_generated = owner_source("SideStore/Core/Pairing/PairingFileManager.swift", "SideStore")
        for removed in ("UIViewController", "UIDocumentPicker", "UIAlertController", "import UIKit", "importPairingFile(presentingVC:"):
            self.assertNotIn(removed, pairing_generated)
        runtime = read_product_source("v3_headless_runtime.swift")
        self.assertIn("PairingFileManager.shared.importPairingFile(contents: contents)", runtime)
        self.assertIn("PairingFileManager.shared.fetchPairingFile()", runtime)
        minimuxer = lambda_show("SideStore/Core/DeviceApi/MinimuxerWrapper.swift")
        self.assertIn("PairingFileManager.shared.pairingUDID", minimuxer)

        project = lambda_show("AltStore.xcodeproj/project.pbxproj")
        sidestore_group = project[project.index("A8EECF2A2F4B195000F2436D"):]
        self.assertIn("path = SideStore;", sidestore_group)
        side_target = project[project.index("BFD247692284B9A500981D42 /* SideStore */ = {"):]
        self.assertIn("A8EECF2A2F4B195000F2436D /* SideStore */", side_target)
        side_exceptions = project[project.index("A8EECF492F4B195000F2436D"):]
        side_members = side_exceptions[side_exceptions.index("membershipExceptions = ("):
                                      side_exceptions.index(");")]
        for path in ("AppBootManager.swift", "Core/JIT/SideJITManager.swift", "Core/Pairing/PairingFileManager.swift"):
            self.assertNotIn(f'"{path}"', side_members)
        generated_project = project
        altstore_exceptions = generated_project[generated_project.index("A8EEC8CB2F4B146B00F2436D"):]
        altstore_members = altstore_exceptions[altstore_exceptions.index("membershipExceptions = ("):
                                              altstore_exceptions.index(");")]
        self.assertIn('"LaunchViewController.swift"', altstore_members)


    def test_headless_service_template_does_not_import_swiftui(self):
        service_template = read_product_source("v3_sidestore_service.swift")
        self.assertNotIn("import SwiftUI", service_template)
        self.assertIn("private enum V3OperationRecoveryJournal", service_template)

    def test_legacy_nuke_cache_cleanup_is_file_scoped_and_repeatable(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; cache cleanup behavior runs in macOS CI")
        with tempfile.TemporaryDirectory() as name:
            roots = owner_trees()

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
        source = owner_root("SideStore")
        generated_project = owner_source("AltStore.xcodeproj/project.pbxproj", "SideStore")

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
                if "import Nuke" not in owner_source(path.relative_to(source).as_posix(), "SideStore"):
                    continue
                relative = path.relative_to(root).as_posix()
                if relative not in excluded and relative not in adapted:
                    unaccounted.append(f"{root_name}/{relative}")
        self.assertEqual(unaccounted, [],
            "every Nuke import must be removed from the headless target or replaced by a backend adapter")


    def test_backend_auth_pair_decisions_use_one_keychain_snapshot(self):
        runtime = read_product_source("v3_headless_runtime.swift")
        service = read_product_source("v3_sidestore_service.swift")
        availability = runtime[runtime.index("func canResumeProvisioning()"):
            runtime.index("    var sessions: [String: Session]", runtime.index("func canResumeProvisioning()"))]
        self.assertIn("let credentials = auth.authenticationSnapshot", availability)
        self.assertIn("hasTokenBackedRoute(", availability)
        self.assertIn("credentialRoutePresent: credentials?.isAuthenticated == true", availability)
        self.assertIn("currentAppleID: credentials?.appleIDEmailAddress", availability)
        begin = runtime[runtime.index("func begin(deadline: Date,"):
            runtime.index("    func poll(id: String)", runtime.index("func begin(deadline: Date,"))]
        self.assertIn("let authCredentials = AuthManager.shared.authenticationSnapshot", begin)
        self.assertIn("authCredentials?.appleIDEmailAddress", begin)
        self.assertNotIn("AuthManager.shared.currentAppleID", begin)
        expire = runtime[runtime.index("func expire(id: String)"):
            runtime.index("    @discardableResult\n    func cancel(id: String)", runtime.index("func expire(id: String)"))]
        self.assertEqual(expire.count("AuthManager.shared.authenticationSnapshot"), 1)
        status = service[service.index("private func snapshot()"):
            service.index("\n}", service.index("private func snapshot()"))]
        self.assertIn("let authCredentials = AuthManager.shared.authenticationSnapshot", status)
        self.assertNotIn("AuthManager.shared.currentAppleID", status)

    def test_external_url_logs_omit_sensitive_values(self):
        for path in ("AltStore/SceneDelegate.swift", "SideStore/DeepLinks/URLHandler.swift"):
            source = owner_source(path, "SideStore")
            logs = "\n".join(line for line in source.splitlines() if "debugLog(" in line)
            for unsafe in ("context.url)", "url.absoluteString)", "debugLog(finished)"):
                self.assertNotIn(unsafe, logs)


    def test_readiness_failure_bridge_has_one_canonical_combined_failure_adapter(self):
        helper = read_product_source("combined_refresh_handler.swift")
        self.assertEqual(helper.count(
            "func combinedFailure(id: String, operation overrideOperation: String? = nil) -> CombinedFailure"), 1)

    def test_service_marks_only_database_readiness_not_ready_as_transient(self):
        service_template = read_product_source("v3_sidestore_service.swift")
        policy = read_product_source("v3_behavioral_primitives.swift")
        self.assertEqual(service_template.count("V3ServiceReadinessRetryPolicy.retryable("), 2)
        self.assertIn('guard typedNotReady, operation == "snapshot", stage == .serviceReadiness', policy)
        self.assertIn("code == .notReady else { return nil }", policy)
        self.assertIn('"payload": ["readinessOnly": true]',
                      owner_source("SideStoreSupport/SideStore.swift"))
        self.assertIn('return ["ready": DatabaseManager.shared.isStarted]', service_template)

    def test_source_remove_and_pairing_import_accept_the_authoritative_response_snapshot(self):
        host = read_product_source("v3_unified_shell.swift")
        remove = host[host.index("private func confirmRemove(id: String) async"):
                      host.index("struct V3CatalogApp", host.index("private func confirmRemove(id: String) async"))]
        pairing = host[host.index("private func importFile(_ url: URL) async", host.index("struct V3PairingView")):
                        host.index("final class V3SettingsStore", host.index("struct V3PairingView"))]
        self.assertIn("status.finishDirectMutation(ticket: mutationTicket, reply: result)", remove)
        self.assertIn("status.finishDirectMutation(ticket: mutationTicket!, reply: result)", pairing)
        self.assertNotIn("status.reload()", remove)
        self.assertNotIn("status.reload()", pairing)

    def test_credentials_cross_the_command_channel_and_never_a_shared_keychain_group(self):
        """A re-signer grants the shared Keychain group to the root bundle only.

        The embedded SideStore runs in the service extension, so an answer routed
        through a shared access group could never be read back. It therefore
        travels in the request that already carries the prompt id, and no
        credential may be written to shared storage on the way.
        """
        host = read_product_source("v3_unified_shell.swift")
        service_template = read_product_source("v3_sidestore_service.swift")
        handoff = read_product_source("v3_secret_handoff.swift")
        shared_keychain = read_product_source("embedded_shared_keychain.swift")

        for operation in ("authRespond", "opAnswer"):
            self.assertIn(f'operation: "{operation}"', host)
            self.assertIn(f'payload: ["prompt": ', host)
        # The answer is the payload, not an opaque reference to one.
        self.assertIn('payload: ["prompt": promptID, "answer": answer]', host)
        self.assertIn('payload: ["prompt": id, "answer": answer]', host)
        self.assertIn('payload: ["answer": ["password": exportPassword]', host)
        self.assertIn('payload: ["answer": ["password": importPassword]', host)
        self.assertIn('payload["answer"] as? [String: String]', service_template)
        # No credential may be staged in shared storage on either side.
        for stale in ("V3SecretHandoff.storeStringDictionary", "V3SecretHandoff.storeString(",
                      "V3SecretHandoff.consumeStringDictionary", "V3SecretHandoff.consumeString(",
                      "V3SecretHandoff.discard("):
            self.assertNotIn(stale, host)
            self.assertNotIn(stale, service_template)
        self.assertNotIn("secretToken", host)
        self.assertNotIn("secretToken", service_template)
        # One-shot delivery is now the service's own prompt state, keyed by the
        # prompt id that rides in the same request.
        self.assertIn("V3HeadlessRuntime.shared.auth.respond(id: target, promptID: promptID, answer: answer)",
                      service_template)
        self.assertIn("V3HeadlessRuntime.shared.operations.answer(id: target, promptID: promptID, answer: answer)",
                      service_template)
        # The taxonomy and the Apple-blame guard stay: a transport failure must
        # never be reported as an Apple authentication failure.
        self.assertIn("keychainExplicitGroupUnauthorized", handoff)
        self.assertIn("errSecMissingEntitlement", handoff)
        self.assertIn("V3SecretHandoffFailurePolicy", service_template)
        self.assertIn("safeCause: .secretHandoffUnavailable", handoff)
        # SideStore persists into the shared group when the signer grants it, and
        # into its own entitled default group when the signer does not.
        self.assertIn("V3SecretHandoff.sharedKeychainAccessGroup()", shared_keychain)
        self.assertIn("V3SecretHandoff.processDefaultKeychainAccessGroup()", shared_keychain)
        self.assertNotIn("accessGroup: appGroup", shared_keychain)

    def test_sidesign_headers_travel_in_the_request_and_the_reply(self):
        """SideSign headers are configuration, not credentials.

        They were staged as one-time tokens in the shared Keychain group, which
        is unreadable from the service under any re-signer. They now travel as a
        bounded request field and come back as a bounded reply field.
        """
        wire = read_product_source("v3_wire_contract.swift")
        service = read_product_source("v3_sidestore_service.swift")
        runtime = read_product_source("v3_headless_runtime.swift")
        host = read_product_source("v3_unified_shell.swift")
        self.assertIn('case "sidesignSet":', wire)
        self.assertIn('Set(payload.keys) == Set(["config"])', wire)
        self.assertIn("V3BackendCommands.sidesignConfigText()", service)
        self.assertIn("V3BackendCommands.sidesignExportText()", service)
        self.assertIn("V3BackendCommands.sidesignSet(config: config)", service)
        self.assertIn('payload: ["config": submittedConfig]', host)
        self.assertIn("static func sidesignSet(config json: String)", runtime)
        self.assertIn("try await sidesignJSON()", runtime)
        for stale in ("sidesignConfigToken", "sidesignExportToken",
                      "V3SecretHandoff.storeString(config)", "V3SecretHandoff.consumeString(token)"):
            self.assertNotIn(stale, runtime)
            self.assertNotIn(stale, service)
            self.assertNotIn(stale, host)

    def test_anisette_server_selection_reloads_authoritative_active_state(self):
        host = read_product_source("v3_unified_shell.swift")
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
        handoff = read_product_source("v3_secret_handoff.swift")
        host = read_product_source("v3_unified_shell.swift")
        service_runtime = read_product_source("v3_headless_runtime.swift")
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
        self.assertIn("V3IPAStaging.sideStoreContainerRoot(selectedGroup: LCSharedUtils.appGroupID())", host)
        self.assertIn("V3IPAStaging.sideStoreContainerRoot()", service_runtime)
        self.assertNotIn("V3SharedFile.", host + service_runtime)

    def test_transport_log_templates_keep_dynamic_errors_paths_and_endpoints_out_of_logs(self):
        gateway = owner_source("DeviceGateway/idevice/IdeviceGateway.swift", "SideStore/Dependencies/minimuxer")
        for unsafe_log in (
            'TRANSPORT_CREATE_FAIL code=\\(code) subcode=\\(subCode) error=\\(message)',
            'selected_transport=FAILED_NO_VALID_TRANSPORT reason=\\(error.localizedDescription)',
            'FETCH_UDID_FAIL stage=transport reason=\\\\(error.localizedDescription)',
            'FETCH_UDID_FAIL stage=rsd_service reason=\\\\(error.localizedDescription)',
            'AFC_FILE_OPEN_START path=\\(path)',
            'SIDESTORE_INSTALL_REQUEST_START path=\\(path)',
        ):
            self.assertNotIn(unsafe_log, gateway)
        # Internal error messages remain available to the classifier; only the
        # user-copyable logger is normalized.
        self.assertIn('IPA install failed: \\(message)', gateway)

    def test_headless_app_delegate_removes_unowned_ipa_and_source_deep_link_lifecycle(self):
        patched = owner_source("AltStore/AppDelegate.swift", "SideStore")
        self.assertNotIn("pendingImportIPAURL", patched)
        self.assertNotIn("importAppDeepLinkNotification", patched)
        self.assertNotIn("addSourceDeepLinkNotification", patched)
        self.assertNotIn("    var window: UIWindow?", patched)
        self.assertNotIn("self.window?.tintColor", patched)
        self.assertIn("appBackupDidFinish", patched)

        generated_app = patched
        self.assertNotIn("UIStackView.appearance(whenContainedInInstancesOf:", generated_app)
        self.assertNotIn("stackViewAppearance.spacing", generated_app)
        self.assertNotIn("openPatreonSettingsDeepLinkNotification", generated_app)
        self.assertNotIn("setTintColor()", generated_app)

        scene_patched = owner_source("AltStore/SceneDelegate.swift", "SideStore")
        self.assertNotIn("pendingImportIPAURL", scene_patched)
        self.assertNotIn("    var window: UIWindow?", scene_patched)
        self.assertNotIn("exportPairingFile", scene_patched)


    def test_host_url_routing_has_no_legacy_helpers(self):
        for path in ("LiveContainerSwiftUI/Views/LCTabView.swift", "LiveContainerSwiftUI/Utilities/LCUtilsExtensions.swift",
                     "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift", "LiveContainerSwiftUI/Views/Settings/LCMultiLCManagementView.swift"):
            source = owner_source(path)
            self.assertNotIn("LCUtils.openSideStore(", source)
            self.assertNotIn("static func openSideStore(", source)
            self.assertNotIn("func dispatchURL(url: URL)", source)


    def test_app_manager_pipeline_factory_drops_presenter_closure(self):
        generated = owner_source("AltStore/Managing Apps/AppManager.swift", "SideStore")
        self.assertIn("return PipelineHandler()", generated)
        self.assertNotIn("presenterProvider:", generated)
        self.assertNotIn("isResignActive:", generated)
        self.assertNotIn("ResignAltStoreViewController", generated)


    def test_shared_secret_handoff_uses_public_keychain_group_probe(self):
        handoff = read_product_source("v3_secret_handoff.swift")
        self.assertNotIn("SecTaskCreateFromSelf", handoff)
        self.assertNotIn("SecTaskCopyValueForEntitlement", handoff)
        self.assertIn("SecItemAdd(item as CFDictionary, &result)", handoff)
        self.assertIn("let deleteStatus = SecItemDelete(deletion as CFDictionary)", handoff)
        self.assertIn("guard deleteStatus == errSecSuccess", handoff)
        self.assertIn("V3SharedKeychainAccessGroupPolicy.sharedGroup(fromDefaultGroup:", handoff)
        probe = handoff[handoff.index("private static func probeAccessGroup("):]
        self.assertIn("kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly", probe)

    def test_generated_service_uses_current_team_policy_and_pairing_module(self):
        service_source = read_product_source("v3_sidestore_service.swift")
        runtime = read_product_source("v3_headless_runtime.swift")
        primitives = read_product_source("v3_behavioral_primitives.swift")
        self.assertIn("storedTeamOwners: [], activeTeamIdentifier: storedTeam?.identifier", service_source)
        self.assertIn("requestedTeamIdentifier: candidate.identifier", service_source)
        self.assertNotIn("activeTeamMatches: true", service_source)
        self.assertIn("static func resolveColdTeamOwner(storedTeamOwners: [String]", primitives)
        self.assertIn("auth.team?.account?.appleID", runtime)
        self.assertIn("AuthManager.shared.team?.account?.appleID", runtime)
        self.assertIn("import MinimuxerCommon", runtime)

    def test_generated_log_formatter_redacts_urls_identifiers_and_provider_bodies(self):
        patched = owner_source("SideStore/Core/Logging/SideStoreLogging.swift", "SideStore")
        runtime = read_product_source("v3_headless_runtime.swift")
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
        generated = owner_source("SideStore/Core/Logging/SideStoreLogging.swift", "SideStore")
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
        source = read_product_source("v3_sidestore_service.swift")
        poll = source[source.index('case "authPoll":'):source.index('case "authRespond":')]
        self.assertIn("safeCause: .authSessionUnavailable", poll)
        self.assertIn("operation: \"signIn\"", poll)
        self.assertIn("stage: .authentication", poll)
        self.assertIn("retryable: false", poll)

    def test_typed_service_failure_is_correlated_to_reply_request_id(self):
        source = read_product_source("v3_sidestore_service.swift")
        receive = source[source.index("private func receive("):source.index("private func invalidRequestReply")]
        self.assertIn("structuredFailure.correlating(to: id).wire", receive)
        failure = read_product_source("combined_failure.swift")
        self.assertIn("public func correlating(to id: String)", failure)

    def test_prompt_session_history_has_no_removed_single_identifier_reference(self):
        runtime = read_product_source("v3_headless_runtime.swift")
        self.assertIsNone(re.search(r"\bsession\.acceptedPromptID\b", runtime))
        self.assertGreaterEqual(runtime.count("session.acceptedPromptIDs"), 4)

    def test_service_admission_uses_typed_busy_cause_policy(self):
        source = read_product_source("v3_sidestore_service.swift")
        self.assertIn("V3ServiceMutationBusyCausePolicy.safeCause", source)
        self.assertIn("authenticationActive: authenticationActive", source)
        self.assertIn("safeCause: .operationInProgress", source)
        self.assertIn("guard !V3HeadlessRuntime.shared.auth.hasActiveSession else { throw ServiceError.busy }", source)
        self.assertIn("responseCapacityAvailable: responseCapacityAvailable", source)
        primitives = read_product_source("v3_behavioral_primitives.swift")
        self.assertIn("case CombinedFailure.SafeCause.responseCapacityUnavailable.rawValue:", primitives)

    def test_successful_service_replies_are_not_reparsed_for_fallback_logs(self):
        service = read_product_source("v3_sidestore_service.swift")
        encode = service[service.index("private func encode(_ value:"):]
        encode = encode[:encode.index("private func run(")]
        self.assertIn("V3ResponseEncoder.encodeDetailed", encode)
        self.assertIn("encoded.fallbackToken", encode)
        self.assertNotIn("PropertyListSerialization.propertyList(from: data", encode)
        harness = (ROOT / "tests/fixtures/v3_catalog_response_encoding_harness.swift").read_text(encoding="utf-8")
        self.assertIn("detailedSuccess.fallbackToken == nil", harness)
        self.assertIn("detailedFailure.fallbackToken", harness)

    def test_inflight_request_id_replay_never_claims_operation_was_not_dispatched(self):
        service_source = read_product_source("v3_sidestore_service.swift")
        replay = service_source[service_source.index("guard tasks[id] == nil else {"):]
        replay = self.swift_declaration(replay, "guard tasks[id] == nil else")
        self.assertNotIn('"operationNotDispatched"', replay)
        admission = service_source[service_source.index("guard V3ServiceMutationAdmissionPolicy.admits"):]
        admission = admission[:admission.index("if mutation { mutationID = id }")]
        self.assertIn('response["operationNotDispatched"] = true', admission)


    def test_featured_sort_startup_skip_matches_exact_pin_and_keeps_backend_startup(self):
        patched = owner_source("AltStore/Core/Model/DatabaseManager/DatabaseManager.swift", "SideStore")
        self.assertEqual(patched.count("V3_HEADLESS_FEATURED_SORT_SKIP_V1"), 1)

        boot = self.swift_declaration(patched, "private func performStart() async throws")
        self.assertIn("try MaintenanceManager.shared.preparePersistentStoreURLs(in: self.persistentContainer)", boot)
        self.assertLess(boot.index("preparePersistentStoreURLs"), boot.index("loadPersistentStores"))
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


        # Startup migration is now checked against maintained owner sources in
        # test_embedded_sidestore_startup, not replayed through a retired generator.


    def test_pipeline_ui_extraction_preserves_no_presenter_safety_and_refresh_routing(self):
        with tempfile.TemporaryDirectory() as name:
            roots = owner_trees()

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
            self.assertNotIn("presenterProvider", pipeline)
            self.assertNotIn("activePresenter", pipeline)
            self.assertNotIn("UIAlertController", pipeline)
            self.assertNotIn("Finish Refresh", pipeline)
            self.assertIn("let isResignActive = false", pipeline)
            self.assertIn("func requestBackgroundSuspension() async", pipeline)
            self.assertIn("suspension is controlled by the host lifecycle", pipeline)
            self.assertIn("func suspendToHomeScreen() async", pipeline)
            self.assertIn("func isAppInForeground() async -> Bool", pipeline)


            pipeline_protocol = owner_source("SideStore/Core/Operations/PipelineExecutionHandler.swift", "SideStore")
            pipeline_runner = owner_source("SideStore/Core/Operations/PipelineRunner.swift", "SideStore")
            self.assertIn("var isResignActive: Bool { get }", pipeline_protocol)
            self.assertIn("handler.preflightChecksHandler.isResignActive == true", pipeline_runner)
            # PresenterProvider is declared in SideStore/Handlers, so it can only
            # be excluded through the SideStore target's own exception set.
            self.assertIn("Handlers/PresenterProvider.swift", contract.HEADLESS_SIDESTORE_HANDLER_UI_FILES)

            step_source = (side / "SideStore/Core/Operations/OperationStepDefinition.swift").read_text(encoding="utf-8")
            refresh_steps = step_source[step_source.index("static let refresh:"):
                                        step_source.index("static let activateLegacy:")]
            self.assertEqual(re.findall(r"PipelineExecutionStep\(\.(\w+),\s*(\d+)\)", refresh_steps), [
                ("updateAppCertificate", "5"),
                ("verifyCertificate", "10"),
                ("fetchProvisioningProfiles", "45"),
                ("cacheResignedMetadata", "1"),
                ("refreshApp", "39"),
            ])
            runner = (side / "SideStore/Core/Operations/PipelineRunner.swift").read_text(encoding="utf-8")
            permission_mode = runner[runner.index("let permissionReviewMode:"):runner.index("let permissionsMode =")]
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
            for path in contract.HEADLESS_SIDESTORE_PIPELINE_UI_FILES:
                self.assertIn(f'"{path}"', membership)
            side_store_module_exception = project[project.index("A8EECF492F4B195000F2436D"):]
            handler_start = side_store_module_exception.index("membershipExceptions = (")
            handler_end = side_store_module_exception.index(");", handler_start)
            handler_membership = side_store_module_exception[handler_start:handler_end]
            for path in contract.HEADLESS_SIDESTORE_HANDLER_UI_FILES:
                self.assertIn(f'"{path}"', handler_membership)

            intent = (side / "AltStore/Intents/App Intents/RefreshAllAppsIntent.swift").read_text(encoding="utf-8")
            widget = (side / "AltStore/Intents/App Intents/RefreshAllAppsWidgetIntent.swift").read_text(encoding="utf-8")
            self.assertIn("V3_SHORTCUT_GUEST_BACKEND_PIPELINE_V1", intent)
            self.assertIn("AppManager.shared.backgroundRefresh", intent)
            self.assertIn("RefreshAllAppsIntent(presentsNotifications: true)", widget)
            runtime = read_product_source("v3_headless_runtime.swift")
            self.assertIn('ask(kind: "bundleIDOverride"', runtime)
            self.assertIn('ask(kind: "appGroupMismatch"', runtime)


    def test_prepared_sidestore_has_no_automatic_storyboard_root_or_unused_starscream(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = owner_trees()

            side = roots[1]
            sign_in_path = side / "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift"
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
            for path in contract.HEADLESS_SIDESTORE_VIEW_FILES + contract.HEADLESS_SIDESTORE_AUX_UI_FILES:
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
            host_pipeline_handler = read_product_source("v3_headless_runtime.swift")
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
            blocked_ui_entry = self.swift_declaration(auth_manager, "func signIn(\n        presentingViewController:")
            self.assertIn('throw OperationError.invalidParameters("Sign in through LiveContainer', blocked_ui_entry)
            self.assertNotIn(".present(", blocked_ui_entry)
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
            self.assertIn("func importPairingFile(contents: String, preferred:", pairing_manager)
            self.assertIn("nonisolated var pairingUDID:", pairing_manager)
            self.assertIn("nonisolated func fetchPairingFile(preferred: PairingProtocol? = nil)", pairing_manager)
            self.assertIn("func savePairingFile(contents: String, preferred:", pairing_manager)
            self.assertNotIn("UIDocumentPicker", pairing_manager)
            self.assertIn("V3_TYPED_PAIRING_FAILURE_PROPAGATION_V1", app_manager)
            self.assertIn("V3HeadlessPairingFailure.tagIfInvalidPairing(error)", app_manager)
            service_template = read_product_source("v3_sidestore_service.swift")
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
            shortcut_request_policy = read_product_source("v3_behavioral_primitives.swift")
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
            pairing_view = read_product_source("v3_unified_shell.swift")
            pairing_view = pairing_view[pairing_view.index("struct V3PairingView:"):pairing_view.index("@MainActor\nfinal class V3SettingsStore")]
            self.assertIn("failure.recovery", pairing_view)
            self.assertIn("Text(failure.technicalDetails)", pairing_view)
            self.assertIn("Choose Pairing File Again", pairing_view)
            self.assertIn("V3PairingImportFailurePolicy.shouldOfferFileRetry(operation: failure.operation", pairing_view)
            self.assertIn("status.present(failure)", pairing_view)
            self.assertNotIn("self.fetchSources", app_delegate)
            self.assertIn("completionHandler(.noData)", app_delegate)
            scene_delegate = (side / "AltStore/SceneDelegate.swift").read_text(encoding="utf-8")
            self.assertNotIn("scene as? UIWindowScene", scene_delegate)
            self.assertNotIn("windowScene", scene_delegate)
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
            self.assertIn("LCAppGroupIdentityRules.h", (roots[0] / "LiveContainer/LCAppGroupSelectionPolicy.h").read_text())
            self.assertIn("!isLiveProcess && sideStoreExist", (roots[0] / "LiveContainer/LCBootstrap.m").read_text(encoding="utf-8"))
            for name in ("ShareExtension/ShareExtensionViewModel.swift", "LaunchAppExtension/LaunchAppExtension.swift"):
                self.assertNotIn('set("builtinSideStore", forKey: "LCLaunchExtensionBundleID")', (roots[0] / name).read_text(encoding="utf-8"))
            uninstall = (roots[1] / "SideStore/Core/Operations/PipelineOperations/UninstallAppOperation.swift").read_text(encoding="utf-8")
            self.assertIn("V3_DELETE_NATIVE_SUCCESS_EVIDENCE_V1", uninstall)
            self.assertIn("await handler.recordNativeUninstallSucceeded()", uninstall)
            sign_in = (roots[1] / "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift").read_text()
            self.assertIn("V3_PROVISIONING_RETRY_BYPASSES_CACHED_SIGNIN_V1", sign_in)
            self.assertIn("V3_AUTH_CREDENTIAL_TRANSACTION_V1", sign_in)
            transaction_start = sign_in.index("V3_AUTH_CREDENTIAL_TRANSACTION_V1")
            transaction_end = sign_in.index("return (account, session)", transaction_start)
            credential_transaction = sign_in[transaction_start:transaction_end]
            self.assertLess(credential_transaction.index("v3BeginIdentityTransition()"),
                            credential_transaction.index("writeAuthenticationCredentials"))
            self.assertLess(credential_transaction.index("writeAuthenticationCredentials"),
                            credential_transaction.index("AuthManager.shared.session = session"))
            completion = self.swift_declaration(credential_transaction, "defer {")
            self.assertIn("AuthManager.shared.v3CompleteIdentityTransition()", completion)
            self.assertIn("AuthManager.shared.v3CompleteOwnedODAIdentityTransition(reserved)", completion)
            self.assertIn("AuthManager.shared.v3CompleteIdentityTransitionReturningStamp()", completion)
            self.assertIn("Keychain.shared.writeAuthenticationCredentials(appleID: appleID, password: password, dsid: session.dsid, authToken: session.authToken,", sign_in)
            self.assertNotIn("AuthManager.shared.adsid = session.dsid", sign_in)
            self.assertNotIn("AuthManager.shared.xcodeToken = session.authToken", sign_in)
            self.assertNotIn("AuthManager.shared.currentAppleID = appleID", sign_in)
            self.assertNotIn("AuthManager.shared.password = password", sign_in)
            self.assertIn("V3ProvisioningResumeExecutionPolicy.mayUseCachedSignIn", sign_in)
            self.assertIn("handleSignInResult(.success(silentResult))", sign_in)
            self.assertIn("V3ProvisioningResumeUnavailableError()", sign_in)
            self.assertIn("if self.v3ForceProvisioningRetry {", sign_in)
            retry = sign_in[sign_in.index("if self.v3ForceProvisioningRetry {"):sign_in.index("} else if V3ProvisioningResumeExecutionPolicy")]
            self.assertIn("let account = team.account,", retry)
            self.assertIn("AuthManager.shared.v3IdentityIsStable", retry)
            self.assertIn("V3AuthIdentityBindingPolicy.hasUsableSession(", retry)
            self.assertIn("self.provisioningLoop(account: account, session: session", retry)
            self.assertIn("session.anisetteData = try await self.getAnisetteData()", retry)
            self.assertIn("AuthManager.shared.v3ReplaceSession(session)", retry)
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


    def test_generated_developer_portal_proxy_binds_session_and_team_owner(self):
        generated = owner_source("SideStore/Core/Auth/DeveloperPortalProxy.swift", "SideStore")
        self.assertEqual(generated.count("{"), generated.count("}"),
                         "generated pinned DeveloperPortalProxy must remain brace balanced")
        self.assertEqual(generated.count("V3_AUTH_IDENTITY_BOUND_DEVELOPER_PORTAL_V1"), 1)
        self.assertIn("import CoreData", generated)
        self.assertIn("sessionDSID: session.dsid", generated)
        self.assertIn("sessionXcodeToken: session.authToken", generated)
        self.assertIn("sessionXcodeToken: context.session.authToken", generated)
        self.assertIn("generationBefore: generation", generated)
        self.assertIn("cancelled: Task.isCancelled", generated)
        self.assertIn("requestedOwner: account.appleID", generated)
        self.assertIn("team.account?.appleID", generated)
        self.assertIn("$0.account?.appleID", generated)
        self.assertIn("#keyPath(Team.identifier)", generated)
        self.assertIn("resolveColdTeamOwner(", generated)
        self.assertIn("DatabaseManager.shared.activeTeam(in: context)?.identifier", generated)
        self.assertIn("DatabaseManager.shared.activeAccount(in: context)?.appleID", generated)
        self.assertIn("try context.setQueryGenerationFrom(.current)", generated)
        self.assertIn("private struct DatabaseTeamOwnershipSnapshot: Sendable", generated)
        self.assertNotIn("DatabaseManager.shared.activeTeam()", generated)
        self.assertNotIn("DatabaseManager.shared.activeAccount()", generated)
        self.assertLess(generated.index("if let directOwner { return directOwner }"),
                        generated.index("databaseOwnershipSnapshot(for: team.identifier)"))
        read_start = generated.index("private func databaseOwnershipSnapshot(for identifier:")
        read_end = generated.index("private func owner(for team:", read_start)
        ownership_read = generated[read_start:read_end]
        self.assertEqual(ownership_read.count("performBackgroundTask"), 1)
        self.assertLess(ownership_read.index("setQueryGenerationFrom(.current)"),
                        ownership_read.index("context.fetch(request)"))
        self.assertLess(ownership_read.index("context.fetch(request)"),
                        ownership_read.index("activeTeam(in: context)"))
        self.assertLess(ownership_read.index("activeTeam(in: context)"),
                        ownership_read.index("activeAccount(in: context)"))
        self.assertIn("DatabaseTeamOwnershipSnapshot(teamOwners: owners", ownership_read)
        self.assertIn("mayDispatchTeamRequest(", generated)
        self.assertNotIn("private static var teamOwners", generated,
                         "account-bound ALTTeam ownership and CoreData relation avoid a stale global team-ID map")

        fetch_teams = generated[generated.index("public func fetchTeams(for account:"):]
        fetch_teams = fetch_teams[:fetch_teams.index("\n    }")]
        self.assertLess(fetch_teams.index("mayFetchTeams("), fetch_teams.index("ALTAppleAPI.shared.fetchTeams"))
        self.assertLess(fetch_teams.index("verifyCurrent(context)"), fetch_teams.index("ALTAppleAPI.shared.fetchTeams"))
        self.assertLess(fetch_teams.index("ALTAppleAPI.shared.fetchTeams"), fetch_teams.rindex("verifyCurrent(context)"))

        base_proxy = generated[generated.index("public class DeveloperPortalProxy {"):
                               generated.index("class DeveloperPortalProxyWithAuth")]
        self.assertNotIn("getSession()", base_proxy)
        self.assertNotIn("getTeam(team)", base_proxy)
        method_starts = [i for i in range(len(base_proxy))
                         if base_proxy.startswith("    public func ", i)]
        method_starts.append(len(base_proxy))
        bound_calls = 0
        for start, end in zip(method_starts, method_starts[1:]):
            method = base_proxy[start:end]
            if "ALTAppleAPI.shared." not in method:
                continue
            if "fetchTeams(for account:" in method:
                self.assertLess(method.index("mayFetchTeams("), method.index("ALTAppleAPI.shared."))
                self.assertIn("session: context.session", method)
            elif "reconcilePendingCertificateCreation(" in method:
                self.assertLess(method.index("context.session.dsid == retained.ownerDSID"), method.index("ALTAppleAPI.shared."))
                self.assertLess(method.index("team.identifier == retained.teamID"), method.index("ALTAppleAPI.shared.fetchCertificates"))
                self.assertIn("session: context.session", method)
            else:
                self.assertLess(method.index("getBoundTeam(team, context: context)"),
                                method.index("ALTAppleAPI.shared."), method[:100])
                self.assertTrue("let session = context.session" in method or "session: context.session" in method, method[:100])
            bound_calls += method.count("ALTAppleAPI.shared.")
        self.assertGreaterEqual(bound_calls, 20, "every pinned team-scoped portal call must use a bound context")
        self.assertNotIn("debugLog", generated)
        self.assertNotIn("technicalDetails", generated)
        self.assertNotIn("appleIDEmailAddress)\n            debugLog", generated)

        updated_auth = owner_source("SideStore/Core/Auth/AuthManager.swift", "SideStore")
        self.assertIn("var v3IdentityGeneration: UInt64", updated_auth)
        self.assertIn("func v3BeginIdentityTransition()", updated_auth)
        self.assertIn("func v3CompleteIdentityTransition()", updated_auth)
        self.assertIn("private let v3IdentityStampState = V3AuthIdentityStampState()", updated_auth)
        self.assertIn("v3IdentityStampState.snapshot", updated_auth)
        self.assertIn("V3AuthSessionCoalescerKey.value(for: identityAtStart.stamp)", updated_auth)
        self.assertIn("v3InstallSessionIfCurrent", updated_auth)
        self.assertIn("credentialSnapshotAfter?.appleIDXcodeToken == xcodeToken", updated_auth)
        self.assertIn("v3CachedSessionMatchesCurrentRoute", updated_auth)
        for setter in ("appleIDEmailAddress", "appleIDPassword", "appleIDAdsid", "appleIDXcodeToken"):
            self.assertIn(f"defer {{ self.v3CompleteIdentityTransition() }}; Keychain.shared.{setter} = newValue",
                          updated_auth)
        self.assertIn("defer { self.v3CompleteIdentityTransition() }", updated_auth)


    def test_snapshot_separates_stored_credentials_from_bound_authenticated_state(self):
        source = read_product_source("v3_sidestore_service.swift")
        start = source.index("    private func snapshot() throws -> [String: Any] {")
        end = source.index("\n    }", source.index('"settings": [', start))
        snapshot = source[start:end]
        self.assertIn('"credentialRoutePresent": credentialRoutePresent', snapshot)
        self.assertIn("let authenticated = credentialRoutePresent &&", snapshot)
        self.assertIn("authCredentials?.appleIDAdsid?.isEmpty == false", snapshot)
        self.assertIn("authCredentials?.appleIDXcodeToken?.isEmpty == false", snapshot)
        self.assertIn("candidate.appleID", snapshot)
        self.assertIn("candidate.account?.appleID", snapshot)
        self.assertIn("resolveColdTeamOwner(", snapshot)
        self.assertIn("V3AuthIdentityBindingPolicy.mayUseTeam", snapshot)
        self.assertIn('"provisioningIncomplete": provisioningIncomplete', snapshot)
        self.assertIn('provisioningCompletion.status(', snapshot)
        self.assertNotIn('authenticated && activeAccount == nil', snapshot)
        self.assertIn("identityGenerationAtStart", snapshot)
        self.assertIn("identityReadStable", snapshot)
        self.assertIn("mayProjectIdentity", snapshot)

    def test_standalone_refresh_run_does_not_reuse_stale_scheduler_identity(self):
        refresh = read_product_source("combined_refresh_handler.swift")
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
        startup = owner_source("SideStoreSupport/SideStore.swift")
        self.assertNotIn("/*REFRESH_READINESS*/", startup)
        self.assertNotIn('let status = try await V3ServiceBridge.shared.request(operation: "snapshot")', refresh)
        self.assertIn("V3RefreshAdmissionLease.lifetime + 60", perform)
        bridge = owner_source("SideStoreSupport/SideStore.swift")
        self.assertIn("startScheduledRefresh(", bridge)
        self.assertIn("runID: runID.uuidString", bridge)
        scheduler = read_product_source("livecontainer_refresh_scheduler.swift")
        self.assertIn("LiveContainerRefreshBridge.refreshAllApps(runID: runID)", scheduler)
        self.assertIn("V3DirectRefreshRunClaimPolicy.isActive", scheduler)

    def test_refresh_terminal_intent_recovers_crash_after_active_release(self):
        scheduler = read_product_source("livecontainer_refresh_scheduler.swift")
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


    def test_owner_boundary(self):
        host = read_product_source("v3_unified_shell.swift")
        bridge = read_product_source("v3_service_bridge.swift")
        for token in ("CoreData", "NSManagedObject", "appleIDXcodeToken"):
            self.assertNotIn(token, host + bridge)
        self.assertIn("V3JITLessStatusReader", host)
        self.assertIn("livecontainer://jitless-setup", host)
        self.assertNotIn("syncJITLessCertificate", host)
        self.assertNotIn('account: "signingCertificate"', host)
        self.assertNotIn("writeJITLessCertificate", host)
        self.assertNotIn("v3SideStoreStatusSnapshot", host)
        self.assertIn("pending.removeValue", bridge)
        self.assertIn("CombinedFailure.uuidCorrelationMatches(responseID, expectedID: id)", bridge)

    def test_status_reply_freshness_is_bridge_owned_and_pre_dispatch(self):
        bridge = read_product_source("v3_service_bridge.swift")
        host = read_product_source("v3_unified_shell.swift")
        primitives = read_product_source("v3_behavioral_primitives.swift")
        request = bridge[bridge.index("public func request(operation:"):
                        bridge.index("public func disconnected()")]
        acquisition = bridge[bridge.index("private func acquireStatusLease"):
                             bridge.index("private func cancelStatusLeaseWaiter")]
        self.assertLess(request.index("acquireStatusLease(ownerID:"), request.index("try await connect()"))
        self.assertLess(acquisition.index("reserveMutationRevision()"),
                        acquisition.index("withTaskCancellationHandler"))
        self.assertIn("if !wasDispatched, let statusLeaseTicket", request)
        self.assertIn("completeStatusLease(statusLeaseTicket, outcome: .notDispatched)", request)
        self.assertLess(request.index("statusDispatchedRequestIDs.insert(id)"),
                        request.index("client.v3Execute(data)"))
        self.assertIn("return attachStatusReplyTicket(result, requestID: id)", request)
        self.assertIn("statusWriteAuthority.retireService()", bridge)
        complete = bridge[bridge.index("private func completeStatusLease"):
                          bridge.index("private func observeConnectedStatusService")]
        self.assertIn("if outcome != .outcomeUnknown || ticket.kind == .snapshot", complete)
        self.assertIn("replyCanReturn = pending[requestID] != nil", bridge)
        self.assertIn("resolveUnknownStatusOwner(ownerID)", bridge)
        self.assertIn(".outcomeUnknown", bridge[bridge.index('if ownerID == "request:\\(requestID)"'):])
        cancel_ack = bridge[bridge.index("private func cancelRemote"):
                            bridge.index("private func settle(")]
        self.assertNotIn("completeStatusLease", cancel_ack,
                         "a cancel ACK cannot release the original direct-write lease")
        self.assertNotIn("cancellationRecovery.removeValue", cancel_ack,
                         "an advisory ACK cannot cancel the original request's retirement timer")
        self.assertIn("func accept(_ snapshot: [String: Any]) -> Bool", host)
        self.assertIn("guard V3ServiceBridge.shared.statusReplyMayApply(snapshot)", host)
        self.assertIn("statusReplyMayApplyRecoveryEvidence(snapshot)", host)
        self.assertIn("unresolvedOperationRecovery = V3OperationRecoveryRecord", host)
        self.assertIn("recoveryJournalUnreadable", host)
        self.assertNotIn("stateReplyRevision", host)
        self.assertNotIn("V3StatusSnapshotRevisionPolicy", primitives + host)
        self.assertIn("statusWriteAuthority.hasUnresolvedMutation", bridge)
        self.assertIn("if kind == .mutation && statusWriteAuthority.hasUnresolvedMutation", bridge)
        for operation in ("refreshAdmissionBegin", "refreshAdmissionEnd", "refreshAdmissionReconcile"):
            self.assertIn(f'"{operation}"', bridge[bridge.index("private func operationSessionID"):])
        self.assertIn('"sidesignReset"', primitives)

    def test_status_authority_write_inventory_matches_wire_and_documents_file_exemptions(self):
        contract = read_product_source("v3_wire_contract.swift")
        primitives = read_product_source("v3_behavioral_primitives.swift")
        operation_block = contract[contract.index("static let operations"):
            contract.index("static let readOperations")]
        direct_writes = ("signOut", "accountImport", "syncAppIDs", "clearCache", "refreshSources", "jit",
            "certSetActive", "certDelete", "certRevoke", "certCreate", "sourceAddConfirmed",
            "sourceRemoveConfirmed", "pairingImportData", "settingsSet", "sidesignSet", "sidesignReset",
            "sidesignImport", "anisetteReset", "anisetteSync", "opRecoveryPrepare",
            "recoveryDiscardUnreadable")
        for operation in direct_writes:
            self.assertIn(f'"{operation}"', operation_block)
            self.assertIn(f'"{operation}"', primitives[
                primitives.index("static func directWriteOwnerID"):])
        for exempt in ("backupResult", "ipaCleanup"):
            self.assertIn(f'"{exempt}"', operation_block)
            self.assertIn(exempt, primitives[primitives.index("static func directWriteOwnerID"):])

    def test_headless_service_has_no_presentation(self):
        service = read_product_source("v3_sidestore_service.swift")
        runtime = read_product_source("v3_headless_runtime.swift")
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
        service = read_product_source("v3_sidestore_service.swift")
        receive = service[service.index("private func receive("):service.index("private func invalidRequestReply")]
        self.assertIn("safeCause: .sourceRemoveBusy", receive)
        self.assertIn("case .notReady = serviceError", receive)
        self.assertIn("case .notReady = headlessError", receive)
        self.assertIn("code: .notReady, id: id, retryable: true", receive)
        self.assertIn("safeCause: .sourceRemoveFailed", receive)

    def test_headless_operation_inventory(self):
        contract = read_product_source("v3_wire_contract.swift")
        service = read_product_source("v3_sidestore_service.swift")
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
                   "opAnswer", "opCancel", "certList", "certExportActive", "certSetActive", "certDelete",
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
        runtime = read_product_source("v3_headless_runtime.swift")
        host = read_product_source("v3_unified_shell.swift")
        kinds = set(re.findall(r'kind: "([a-zA-Z]+)"', runtime))
        expected = {"credentials", "twoFactor", "team", "accountRepair", "provisioningError",
                    "postAuth", "revocation", "resign", "anisetteOutdated", "bundleIDMismatch",
                    "permissions", "extensions", "unsupportedVersion", "bundleIDOverride",
                    "appGroupMismatch", "extensionSigningFallback"}
        self.assertEqual(kinds, expected)
        self.assertIn("V3PromptSection", host)
        self.assertIn("V3SignInView", host)


class RefreshAdmissionOwnerTests(unittest.TestCase):
    def test_native_refresh_contention_has_typed_busy_guidance(self):
        refresh = read_product_source("combined_refresh_handler.swift")
        perform = refresh[refresh.index("func performRefresh(identifier:"):]
        perform = perform[:perform.index("func releaseRefreshAdmission")]
        self.assertGreaterEqual(perform.count("safeCause: .operationInProgress"), 3)

    def test_operation_prompt_does_not_use_auth_only_revision_state(self):
        runtime = read_product_source("v3_headless_runtime.swift")
        auth = runtime[runtime.index("final class V3HeadlessAuthHandler:"):
                       runtime.index("final class V3HeadlessPipelineHandler:")]
        operation = runtime[runtime.index("final class V3HeadlessPipelineHandler:"):
                            runtime.index("// MARK: - Headless operation sessions")]
        self.assertIn("center.sessions[sessionID]?.revision += 1", auth)
        operation_prompt = operation[operation.index("private func ask(kind:"):]
        operation_prompt = operation_prompt[:operation_prompt.index("func resolveBundleIDMismatch")]
        self.assertNotIn(".revision", operation_prompt)

    def test_refresh_owner_brackets_direct_refresh_and_confirms_release(self):
        refresh = read_product_source("combined_refresh_handler.swift")
        bridge = read_product_source("v3_service_bridge.swift")
        service = read_product_source("v3_sidestore_service.swift")
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
        self.assertIn('payload: ["state": terminalState, "admissionID": admissionID]', refresh)
        self.assertIn('kind: "refreshAll"', service)
        self.assertIn("V3OperationRecoveryJournal.settleRefreshAdmission", service)
        self.assertIn("refreshAdmission.restoreLost(runID:", service)
        self.assertIn("releaseUnstartedRefreshAdmission(requestID: target)", service)
        self.assertIn("releaseUnstartedRefreshAdmission(requestID: id)", service)
        self.assertIn("refreshAdmission.requestID == requestID", service)
        self.assertIn("!refreshExecutionGate.hasDispatched(hostRunID: runID, hostAdmissionID: requestID)", service)
        cleanup = service[service.index("private func releaseUnstartedRefreshAdmission("):service.index("private func hostRefreshDefaults(")]
        self.assertLess(cleanup.index("clearPreparedRefreshAdmissionAfterRequestCancellation"), cleanup.index("refreshAdmission.release(requestID: requestID)"))
        self.assertIn('cancellationReply["refreshAdmissionReleased"] = true', service)
        self.assertIn("pendingRefreshAdmissionRequests", service)
        end_case = service[service.index('case "refreshAdmissionEnd":'):]
        self.assertIn("UUID(uuidString: target)", end_case)
        self.assertIn("V3CancellationRecoveryReplyPolicy.mayCancelRetirement", bridge)
        self.assertIn("V3RequestRetirementPolicy", bridge)

    def test_auth_begin_request_expiry_cancels_its_reserved_session(self):
        service = read_product_source("v3_sidestore_service.swift")
        runtime = read_product_source("v3_headless_runtime.swift")
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
        service = read_product_source("v3_sidestore_service.swift")
        budget = read_product_source("v3_wire_contract.swift")
        self.assertIn("V3MutationReplyCacheBudget.isControlReply(operation: operation)", service)
        self.assertIn("V3MutationReplyCacheBudget.shouldCacheResponse(operation: operation)", service)
        self.assertIn("if mutation && cacheResponse", service)
        self.assertIn("completedCacheBudget.canReserve(", service)
        self.assertIn("completedCacheBudget.record(encoded.count, controlResponse: controlReply)", service)
        for control in ("refreshAdmissionEnd", "authBegin", "authRetryProvisioning", "opStart"):
            self.assertIn(f'"{control}"', budget)
        self.assertIn('!["authRespond", "opAnswer", "certExportActive"].contains(operation)', budget)

    def test_active_certificate_export_uses_upstream_active_material_without_keychain_cache(self):
        service = read_product_source("v3_sidestore_service.swift")
        wire = read_product_source("v3_wire_contract.swift")
        shell_patch = owner_source("LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift")
        self.assertIn('case "certExportActive":', service)
        self.assertIn("CertificateManager.shared.activeCertificate", service)
        self.assertIn("active.p12Data", service)
        self.assertIn("active.password", service)
        self.assertIn('let password = active.password ?? ""', service)
        self.assertIn("teamRecord.account?.identifier == account.identifier", service)
        self.assertIn("V3AuthIdentityBindingPolicy.mayUseTeam(", service)
        self.assertIn("current.certificate.x509.data == der", service)
        self.assertIn("static let maximumP12Bytes = 1_048_576", service)
        self.assertIn("static let maximumPasswordBytes = 512", service)
        self.assertIn("!p12Data.isEmpty, p12Data.count <= maximumP12Bytes", service)
        self.assertIn('!["authRespond", "opAnswer", "certExportActive"].contains(operation)', wire)
        importer = shell_patch[shell_patch.index("V3_SERVICE_CERTIFICATE_EXPORT_V1"):]
        self.assertIn('operation: "certExportActive"', importer)
        self.assertIn("LCUtils.getCertTeamId(withKeyData: data, password: password) == team", importer)
        self.assertNotIn("!password.isEmpty", importer)
        self.assertIn('operation: "healthSnapshot"', importer)
        self.assertIn('"certExportActive"', wire[wire.index("static let readOperations"):])

    def test_request_deadline_task_is_cancelled_when_operation_settles(self):
        service = read_product_source("v3_sidestore_service.swift")
        self.assertIn("private var deadlineTasks: [String: Task<Void, Never>]", service)
        self.assertIn("deadlineTasks.removeValue(forKey: id)?.cancel()", service)
        self.assertIn("deadlineTasks[id] = Task { @MainActor in", service)


def wire_runtime_source():
    # The current wire schema validates bounded diagnostics using their real
    # shared model. Do not substitute permissive fixture sanitizers.
    return "\n".join((read_product_source("combined_failure.swift"), read_product_source("v3_wire_contract.swift")))


def startup_diagnostic_source():
    source = owner_source("SideStoreSupport/SideStore.swift", "LiveContainer")
    return ServiceOwnerTests.swift_declaration(source, "public struct V3StartupDiagnosticLog {")


def bridge_runtime_source():
    return "\n".join((
        (ROOT / "tests/fixtures/v3_bridge_harness.swift").read_text(),
        startup_diagnostic_source(),
        read_product_source("combined_failure.swift"),
        read_product_source("v3_behavioral_primitives.swift"),
        read_product_source("combined_service_connection.swift"),
        read_product_source("v3_wire_contract.swift"),
        read_product_source("v3_service_bridge.swift"),
    ))


class WireExecutionTests(unittest.TestCase):
    def test_wire_harness_includes_current_diagnostic_dependencies(self):
        source = wire_runtime_source()
        for name in ("V3TemporaryADIConsumption", "V3TemporaryAnisetteTrace", "V3LoginDiagnosticSnapshot"):
            self.assertIn("struct " + name, source)
        self.assertIn("public struct V3StartupDiagnosticLog", startup_diagnostic_source())
        assembled = bridge_runtime_source()
        self.assertIn(startup_diagnostic_source() + "\n" + read_product_source("combined_failure.swift"), assembled)
        self.assertNotIn("}import", assembled)

    def test_active_certificate_export_adapter_crosses_service_owned_group_boundary(self):
        service = read_product_source("v3_sidestore_service.swift")
        start = service.index("enum V3ActiveCertificateExportAdapter {")
        end = service.index("\n}\n", start) + 2
        adapter = service[start:end]
        command = service[service.index('case "certExportActive":'):service.index('case "certSetActive":')]
        self.assertIn("CertificateManager.shared.activeCertificate", command)
        self.assertIn("p12Data: active.p12Data, password: password", command)
        self.assertIn("V3AuthIdentityBindingPolicy.mayUseTeam", command)
        self.assertNotIn("debugLog", command)

        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; production adapter boundary harness runs in macOS CI")
        harness = r'''
@main struct ActiveCertificateExportHarness {
static func main() {
let activeP12 = Data([0, 1, 2, 3, 4])
        let team = "TEAM123456"
        let fingerprint = String(repeating: "a", count: 64)
        for activePassword in ["", "opaque-active-password"] {
            let reply = V3ActiveCertificateExportAdapter.response(p12Data: activeP12,
                password: activePassword, teamIdentifier: team, identitySHA256: fingerprint)!
            precondition(reply["data"] as? Data == activeP12)
            precondition(reply["password"] as? String == activePassword)
            precondition(reply["teamIdentifier"] as? String == team)
            precondition(reply["identitySHA256"] as? String == fingerprint)
        }
        precondition(V3ActiveCertificateExportAdapter.response(p12Data: Data(), password: "",
            teamIdentifier: team, identitySHA256: fingerprint) == nil)
        precondition(V3ActiveCertificateExportAdapter.response(p12Data: Data(repeating: 0,
            count: V3ActiveCertificateExportAdapter.maximumP12Bytes + 1), password: "",
            teamIdentifier: team, identitySHA256: fingerprint) == nil)
        precondition(V3ActiveCertificateExportAdapter.response(p12Data: activeP12,
            password: String(repeating: "p", count: V3ActiveCertificateExportAdapter.maximumPasswordBytes + 1),
    teamIdentifier: team, identitySHA256: fingerprint) == nil)
print("ACTIVE_CERTIFICATE_EXPORT_BOUNDARY_PASS")
}
}
'''
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / "main.swift"
            executable = Path(name) / "active-certificate-export"
            source.write_text("import Foundation\n" + adapter + "\n" + harness, encoding="utf-8")
            built = subprocess.run([compiler, "-parse-as-library", str(source), "-o", str(executable)],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ACTIVE_CERTIFICATE_EXPORT_BOUNDARY_PASS", result.stdout)

    def test_shipped_wire_policies_bind_request_ids_and_coredata_entity_targets(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text(wire_runtime_source() + r'''
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
        source = read_product_source("v3_sidestore_service.swift")
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

    def test_explicit_session_retirement_releases_only_its_status_owner(self):
        bridge = read_product_source("v3_service_bridge.swift")
        confirm = bridge[bridge.index("public func confirmUncertainOperationAfterDeviceCheck"):
                         bridge.index("public func retireReconciledRefreshService")]
        self.assertIn('reconcileStatusOwnerAfterAuthoritativeEvidence("operation:\\(sessionID)")', confirm)
        retire_operation = bridge[bridge.index("public func retireReconciledOperationService"):
                                  bridge.index("public func retireReconciledRefreshService")]
        self.assertIn('reconcileStatusOwnerAfterAuthoritativeEvidence("operation:\\(sessionID)")', retire_operation)
        refresh = bridge[bridge.index("public func retireReconciledRefreshService"):
                         bridge.index("public func forgetSettledOperationSession")]
        self.assertIn('reconcileStatusOwnerAfterAuthoritativeEvidence("refresh:\\(runID)")', refresh)
        disconnected = bridge[bridge.index("public func disconnected()"):
                              bridge.index("private func cancelRemote")]
        self.assertNotIn("reconcileStatusOwnerAfterAuthoritativeEvidence", disconnected)

    def test_shipped_bridge_lifecycle(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text(bridge_runtime_source())
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
            program.write_text(wire_runtime_source() + r'''
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
sideSignConfig["payload"] = ["config": "{\"anthropic\":\"value\"}"]
precondition(V3WireContract.decodeRequest(encode(sideSignConfig), now: now) != nil)
sideSignConfig["payload"] = ["secretToken": UUID().uuidString]
precondition(V3WireContract.decodeRequest(encode(sideSignConfig), now: now) == nil)
sideSignConfig["payload"] = ["config": "{\"Authorization\":\"Bearer SECRET\"}"]
precondition(V3WireContract.decodeRequest(encode(sideSignConfig), now: now) != nil,
    "a configured SideSign Authorization header is configuration, not a credential field")
sideSignConfig["payload"] = ["config": String(repeating: "x", count: 8193)]
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
            # Surface the child's own output: a trapping precondition reports
            # nothing under check=True, so a failure named itself nowhere.
            result = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)

    def test_headless_wire_contract_accepts_payload_and_session_ops(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text(wire_runtime_source() + r'''
let now = Date(timeIntervalSince1970: 100000)
func encode(_ value: [String: Any]) -> Data {
    try! PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
}
func base(_ operation: String) -> [String: Any] {
    ["version": 1, "id": UUID().uuidString, "operation": operation,
     "target": UUID().uuidString, "deadline": now.addingTimeInterval(30)]
}
for operation in ["authBegin", "authRetryProvisioning", "authPoll", "authRespond", "opStart", "opPoll", "opAnswer",
                  "certList", "certExportActive", "certRevoke", "devTeams", "sourcePreview", "sourceAddConfirmed",
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
        request["payload"] = ["prompt": "prompt-1", "answer": ["password": "pw"]]
    } else if operation == "accountExport" {
        request["target"] = ""
        request["payload"] = ["answer": ["password": "pw"], "includeApple": false]
    } else if operation == "accountImport" {
        request["payload"] = ["answer": ["password": "pw"]]
    } else if operation == "sourcePreview" || operation == "sourceAddConfirmed" {
        request["target"] = "https://example.invalid/source.json"
    } else if operation == "ipaActiveTokens" {
        request["target"] = ""
    } else if operation == "anisetteList" {
        request["target"] = ""
    } else if operation == "settingsSet" {
        request["target"] = ""
        request["payload"] = ["key": "isBackgroundRefreshEnabled", "type": "bool", "bool": false]
    } else if ["certList", "certExportActive", "devTeams", "settingsGet", "sidesignGet", "logTail", "healthSnapshot"].contains(operation) {
        request["target"] = ""
    } else {
        request.removeValue(forKey: "payload")
    }
    precondition(V3WireContract.decodeRequest(encode(request), now: now) != nil, operation)
    precondition(V3WireContract.readOperations.contains(operation) == ["authPoll", "opPoll", "certList", "certExportActive", "devTeams", "sourcePreview", "settingsGet", "anisetteList", "ipaActiveTokens", "sidesignGet", "logTail", "healthSnapshot"].contains(operation), operation)
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
// The answer is carried by the request. A verification code is the same class
// of short-lived single-use secret as the password and travels the same way.
for operation in ["authRespond", "opAnswer"] {
    var credentials = base(operation)
    credentials["payload"] = ["prompt": "p1", "answer": ["appleID": "user@example.com",
                                                          "password": "pw"]]
    precondition(V3WireContract.decodeRequest(encode(credentials), now: now) != nil, operation)
    var twoFactor = base(operation)
    twoFactor["payload"] = ["prompt": "p1", "answer": ["action": "code", "code": "123456"]]
    precondition(V3WireContract.decodeRequest(encode(twoFactor), now: now) != nil,
        "\(operation) carries a verification code the same way")
    // The carrier stays flat and bounded, and nothing else may ride with it.
    var nested = base(operation)
    nested["payload"] = ["prompt": "p1", "answer": ["nested": ["password": "pw"]]]
    precondition(V3WireContract.decodeRequest(encode(nested), now: now) == nil, operation)
    var oversized = base(operation)
    oversized["payload"] = ["prompt": "p1", "answer": ["password": String(repeating: "x", count: 4097)]]
    precondition(V3WireContract.decodeRequest(encode(oversized), now: now) == nil, operation)
    var extra = base(operation)
    extra["payload"] = ["prompt": "p1", "answer": ["password": "pw"], "password": "pw"]
    precondition(V3WireContract.decodeRequest(encode(extra), now: now) == nil, operation)
    var retired = base(operation)
    retired["payload"] = ["prompt": "p1", "secretToken": UUID().uuidString]
    precondition(V3WireContract.decodeRequest(encode(retired), now: now) == nil, operation)
}
var export = base("accountExport"); export["target"] = ""
export["payload"] = ["answer": ["password": "backup passphrase"], "includeApple": false]
precondition(V3WireContract.decodeRequest(encode(export), now: now) != nil)
export["payload"] = ["password": "backup passphrase", "includeApple": false]
precondition(V3WireContract.decodeRequest(encode(export), now: now) == nil)
var backupImport = base("accountImport"); backupImport["payload"] = ["answer": ["password": "pw"]]
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
        source = read_product_source("v3_headless_runtime.swift")
        gate = source[source.index("enum V3PromptAnswerDisposition:"):]
        gate = gate[:gate.index("\n@MainActor\nfinal class V3HeadlessRuntime")]
        program = "import Foundation\n" + gate + r'''
// Control only scheduling, not prompt state or continuation implementation.
// park removes its settled box when its resumed job runs. A duplicate is
// alreadySettled before that cleanup and unavailable afterward, so sleeps
// cannot establish the state required by either assertion.
@available(macOS 15.0, *)
final class PromptGateTestExecutor: TaskExecutor, @unchecked Sendable {
    private let lock = NSLock()
    private var jobs: [UnownedJob] = []

    func enqueue(_ job: consuming ExecutorJob) {
        lock.lock()
        jobs.append(UnownedJob(job))
        lock.unlock()
    }

    func runUntilIdle() {
        // Swift versions may enqueue an additional job at a continuation
        // boundary. Drain all runnable work instead of assuming one hop.
        for _ in 0..<100 {
            let job = lock.withLock { jobs.isEmpty ? nil : jobs.removeFirst() }
            guard let job else { return }
            job.runSynchronously(on: asUnownedTaskExecutor())
        }
        preconditionFailure("prompt task failed to suspend or complete")
    }
}

@available(macOS 15.0, *)
@main struct PromptGateTests {
    static func main() async throws {
        let center = V3PromptCenter()
        let executor = PromptGateTestExecutor()
        let first = Task.detached(executorPreference: executor) {
            try await center.park(promptID: "p1")
        }
        executor.runUntilIdle()
        precondition(center.pendingCount == 1, "first prompt must be parked before answering")
        precondition(center.answer(promptID: "p1", answer: ["choice": "proceed"]) == .accepted)
        precondition(center.answer(promptID: "p1", answer: ["choice": "proceed"]) == .alreadySettled,
                     "a duplicate before cleanup must be alreadySettled")
        precondition(center.answer(promptID: "missing", answer: [:]) == .unavailable)
        precondition(!center.cancel(promptID: "p1"), "an accepted answer must settle only once")
        executor.runUntilIdle()
        let firstAnswer = try await first.value
        precondition(firstAnswer["choice"] == "proceed")
        precondition(center.pendingCount == 0, "answered prompt must be removed after resumption")
        precondition(center.answer(promptID: "p1", answer: [:]) == .unavailable,
                     "a duplicate after cleanup must not recreate a completed prompt")

        let second = Task.detached(executorPreference: executor) {
            try await center.park(promptID: "p2")
        }
        executor.runUntilIdle()
        precondition(center.pendingCount == 1, "second prompt must be parked before cancellation")
        second.cancel()
        precondition(!center.cancel(promptID: "p2"), "task cancellation must settle only once")
        precondition(center.answer(promptID: "p2", answer: [:]) == .unavailable)
        executor.runUntilIdle()
        do {
            _ = try await second.value
            preconditionFailure("cancelled park resumed")
        } catch is CancellationError {}
        precondition(center.pendingCount == 0, "cancelled prompt must be removed after resumption")

        let third = Task.detached(executorPreference: executor) {
            try await center.park(promptID: "p3")
        }
        executor.runUntilIdle()
        precondition(center.pendingCount == 1, "third prompt must be parked before cancellation")
        third.cancel()
        executor.runUntilIdle()
        do {
            _ = try await third.value
            preconditionFailure("task cancel did not resume")
        } catch is CancellationError {}
        precondition(center.pendingCount == 0)

        // Cancellation before the task starts must not install a box at all.
        let fourth = Task.detached(executorPreference: executor) {
            try await center.park(promptID: "p4")
        }
        fourth.cancel()
        executor.runUntilIdle()
        do {
            _ = try await fourth.value
            preconditionFailure("pre-cancelled task parked")
        } catch is CancellationError {}
        precondition(center.pendingCount == 0)
        precondition(center.answer(promptID: "p4", answer: [:]) == .unavailable)
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
            for attempt in range(10):
                with self.subTest(attempt=attempt):
                    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("V3 prompt gate PASS", result.stdout)

            # Execute the same assertions against narrowly broken owner copies.
            # Controls must compile and fail at the intended invariant, rather
            # than turning a fixture/compiler problem into a false rejection.
            controls = (
                ("duplicate-settlement",
                 "guard boxes[promptID] === pending, case nil = pending.result else {",
                 "guard boxes[promptID] === pending else {",
                 "a duplicate before cleanup must be alreadySettled"),
                ("missing-cleanup",
                 "if boxes[promptID] === pending { boxes.removeValue(forKey: promptID) }",
                 "// Negative control: omit completed-prompt cleanup.",
                 "answered prompt must be removed after resumption"),
                ("missing-task-cancellation",
                 "self.settle(promptID: promptID, pending: pending, result: .failure(CancellationError()))",
                 "// Negative control: omit the task cancellation callback.",
                 "task cancellation must settle only once"),
            )
            for label, before, after, diagnostic in controls:
                with self.subTest(control=label):
                    self.assertEqual(gate.count(before), 1, label)
                    swift.write_text(program.replace(before, after, 1))
                    compiled = subprocess.run([compiler, "-parse-as-library", str(swift), "-o", str(executable)],
                                              capture_output=True, text=True)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0, label)
                    self.assertIn(diagnostic, result.stderr)


class GsaOwnerTests(unittest.TestCase):
    def test_gsa_connection_close_in_prepared_tree(self):
        text = owner_source("Sources/DeveloperPortal/Authentication.swift", "SideStore/Dependencies/SideSign")
        hits = [m.start() for m in re.finditer(r'"Connection": "close"', text)]
        self.assertEqual(len(hits), 2)
        enclosing = []
        for position in hits:
            before = text[:position]
            found = [m.group(1) for m in re.finditer(r"func\s+(\w+)\s*\(", before)][-1]
            enclosing.append(found)
        self.assertEqual(enclosing, ["sendObservedAuthenticationRequest", "makeTwoFactorAuthRequest"])
        self.assertEqual(len(re.findall(r"URLRequest\(", text)), 2)

    def test_no_builder_patch_modifies_sidesign_auth(self):
        scripts = (ROOT / "scripts").glob("*.py")
        for script in scripts:
            content = script.read_text(encoding="utf-8")
            self.assertNotIn("DeveloperPortal/Authentication", content)
        # A read-only provenance collector may reference SideSign source paths.
        # Its source-preservation behavior is exercised by the collector harness.

    def test_auth_is_single_flight_without_retry_loops(self):
        runtime = read_product_source("v3_headless_runtime.swift")
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
        host = read_product_source("v3_unified_shell.swift")
        self.assertEqual(host.count('"authBegin"'), 1)

    def test_shipped_failure_preservation(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            program = directory / "main.swift"
            program.write_text(read_product_source("combined_failure.swift") + "\n"
                               + 'let id = UUID().uuidString\nlet known = CombinedFailure(operation: "connect", stage: .serviceReadiness, code: .timedOut, id: id, retryable: true)\nlet kept = CombinedFailure.preserving(known, operation: "connect", stage: .serviceReadiness, id: id)\nprecondition(kept.stage == .serviceReadiness && kept.code == .timedOut)\nprecondition(kept.correlationID == id && kept.retryable == true)\nlet invalid = CombinedFailure(operation: "connect", stage: .serviceReadiness, code: .invalidResponse, id: id)\nlet keptInvalid = CombinedFailure.preserving(invalid, operation: "connect", stage: .command, id: UUID().uuidString)\nprecondition(keptInvalid.stage == .serviceReadiness && keptInvalid.code == .invalidResponse)\nprecondition(keptInvalid.correlationID == id)\nlet plain = NSError(domain: NSCocoaErrorDomain, code: 42)\nlet wrapped = CombinedFailure.preserving(plain, operation: "connect", stage: .serviceReadiness, code: .failed, id: id)\nprecondition(wrapped.stage == .serviceReadiness && wrapped.code == .failed)\nprecondition(wrapped.correlationID == id && wrapped.underlyingCode == 42)\nlet cancelled = CombinedFailure.preserving(CancellationError(), operation: "connect", stage: .serviceReadiness, id: id)\nprecondition(cancelled.code == .failed)\nprint("V3 failure preservation PASS")')
            executable = directory / "preserve-tests"
            compiled = subprocess.run([compiler, str(program), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3 failure preservation PASS", result.stdout)
