#!/usr/bin/env python3
"""Install the v3 host-owned combined navigation shell.

The patch intentionally does not copy SideStore's database or preferences into
LiveContainer. Status is queried from the command service into an in-memory
projection; the old persistent snapshot publisher is retired.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

MARKER = "V3_UNIFIED_SHELL_V1_BEGIN"
TEMPLATE = Path(__file__).with_name("templates") / "v3_unified_shell.swift"
INTENT_TEMPLATE = Path(__file__).with_name("templates") / "v3_setup_intent.swift"
BEHAVIOR_TEMPLATE = Path(__file__).with_name("templates") / "v3_behavioral_primitives.swift"
IPA_STAGING_TEMPLATE = Path(__file__).with_name("templates") / "v3_ipa_staging.swift"
SECRET_HANDOFF_TEMPLATE = Path(__file__).with_name("templates") / "v3_secret_handoff.swift"
MANUAL_JITLESS_IMPORT_EVENT_MARKER = "V3_CANONICAL_JITLESS_MANUAL_IMPORT_EVENT_V1"
MANUAL_JITLESS_IMPORT_INVALIDATION_MARKER = "V3_CANONICAL_JITLESS_MANUAL_IMPORT_INVALIDATES_PENDING_V1"


def patch_manual_certificate_import_notification(text: str) -> str:
    """Invalidate JIT-Less observations after the canonical manual writer succeeds."""
    start_marker = "    func importCertificate() async {"
    end_marker = "    func importCertificateFromSideStore() async {"
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        die("manual certificate import function anchors changed")
    manual = text[start:end]

    invalidation_anchor = '        LCUtils.appGroupUserDefault.set(certificateData, forKey: "LCCertificateData")'
    invalidation = ("        // " + MANUAL_JITLESS_IMPORT_INVALIDATION_MARKER +
                    "\n        V3CertificateImportOwnership.invalidate()\n")
    if MANUAL_JITLESS_IMPORT_INVALIDATION_MARKER in manual:
        if manual.count(MANUAL_JITLESS_IMPORT_INVALIDATION_MARKER) != 1 or (invalidation + invalidation_anchor) not in manual:
            die("manual certificate import request invalidation marker or placement drifted")
    else:
        if manual.count(invalidation_anchor) != 1:
            die("manual certificate import data writer anchor changed")
        manual = manual.replace(invalidation_anchor, invalidation + invalidation_anchor, 1)

    anchor = '        UserDefaults.standard.set(LCSharedUtils.appGroupID(), forKey: "LCAppGroupID")'
    event = 'NotificationCenter.default.post(name: Notification.Name("V3CanonicalJITLessCertificateUpdated"), object: nil)'
    insertion = (anchor + '\n        // ' + MANUAL_JITLESS_IMPORT_EVENT_MARKER +
                 '\n        ' + event)
    if MANUAL_JITLESS_IMPORT_EVENT_MARKER in manual:
        if manual.count(MANUAL_JITLESS_IMPORT_EVENT_MARKER) != 1 or insertion not in manual:
            die("manual certificate import event marker or placement drifted")
        return text[:start] + manual + text[end:]
    if manual.count(anchor) != 1:
        die("manual certificate import app-group writer anchor changed")
    manual = manual.replace(anchor, insertion, 1)
    return text[:start] + manual + text[end:]

IMPORT_OWNERSHIP_SWIFT = '''    // V3_CERTIFICATE_IMPORT_OWNERSHIP_V1: persist only a short-lived opaque request id.
    private enum V3CertificateImportOwnership {
        private static let requestKey = "V3PendingCertificateImportRequestID"
        private static let expiryKey = "V3PendingCertificateImportExpiry"
        private static let lifetime: TimeInterval = 300
        private static let lock = NSLock()
        private static func invalidateLocked(_ defaults: UserDefaults) {
            defaults.removeObject(forKey: requestKey)
            defaults.removeObject(forKey: expiryKey)
        }
        private static func isActiveLocked(_ requestID: String, defaults: UserDefaults, now: Date) -> Bool {
            guard UUID(uuidString: requestID) != nil,
                  defaults.string(forKey: requestKey) == requestID,
                  let expiry = defaults.object(forKey: expiryKey) as? NSNumber else { return false }
            return expiry.doubleValue > now.timeIntervalSince1970
        }
        static func begin(defaults: UserDefaults = .standard, now: Date = Date()) -> String {
            lock.lock(); defer { lock.unlock() }
            let requestID = UUID().uuidString
            defaults.set(requestID, forKey: requestKey)
            defaults.set(now.addingTimeInterval(lifetime).timeIntervalSince1970, forKey: expiryKey)
            defaults.synchronize()
            return requestID
        }
        static func isActive(_ requestID: String, defaults: UserDefaults = .standard, now: Date = Date()) -> Bool {
            lock.lock(); defer { lock.unlock() }
            return isActiveLocked(requestID, defaults: defaults, now: now)
        }
        static func consume(_ requestID: String, defaults: UserDefaults = .standard, now: Date = Date()) -> Bool {
            lock.lock(); defer { lock.unlock() }
            guard isActiveLocked(requestID, defaults: defaults, now: now) else { return false }
            invalidateLocked(defaults)
            defaults.synchronize()
            return true
        }
        // Cancellation is scoped to the exact current request. A late cancel
        // from a superseded or expired prompt must not invalidate a newer import.
        @discardableResult
        static func cancel(_ requestID: String, defaults: UserDefaults = .standard, now: Date = Date()) -> Bool {
            lock.lock(); defer { lock.unlock() }
            guard isActiveLocked(requestID, defaults: defaults, now: now) else { return false }
            invalidateLocked(defaults)
            defaults.synchronize()
            return true
        }
        static func invalidate(defaults: UserDefaults = .standard) {
            lock.lock(); defer { lock.unlock() }
            invalidateLocked(defaults)
            defaults.synchronize()
        }
    }'''


def die(message: str) -> None:
    raise SystemExit(f"patch_v3_unified_shell: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_host(root: Path) -> None:
    shared = root / "LiveContainerSwiftUI/Utilities/Shared.swift"
    text = shared.read_text(encoding="utf-8")
    if "case home" not in text:
        text = replace_once(text, "public enum LCTabIdentifier: Hashable {\n    case sources\n    case apps\n    case tweaks\n    case settings\n}",
                            "public enum LCTabIdentifier: Hashable {\n    case home\n    case sources\n    case apps\n    case settings\n}", "tab identifiers")
        text = replace_once(text,
                            '    @Published var selectedTab: LCTabIdentifier = .apps',
                            '    @Published var selectedTab: LCTabIdentifier = LCLaunchTab.resolve(LCUtils.appGroupUserDefault.string(forKey: LCLaunchTab.storageKey)) == .apps ? .apps : .home',
                            "launch-tab startup preference")
        shared.write_text(text, encoding="utf-8")

    app = root / "LiveContainerSwiftUI/App/LiveContainerSwiftUIApp.swift"
    text = app.read_text(encoding="utf-8")
    if "V3UnifiedShell()" not in text:
        text = replace_once(text, "            LCTabView()", "            V3UnifiedShell()", "v3 application root")
        app.write_text(text, encoding="utf-8")

    shell = root / "LiveContainerSwiftUI/Views/V3UnifiedShell.swift"
    expected = (BEHAVIOR_TEMPLATE.read_text(encoding="utf-8") + "\n" +
                SECRET_HANDOFF_TEMPLATE.read_text(encoding="utf-8") + "\n" +
                IPA_STAGING_TEMPLATE.read_text(encoding="utf-8") + "\n" +
                TEMPLATE.read_text(encoding="utf-8"))
    if shell.exists() and shell.read_text(encoding="utf-8") != expected:
        die("existing v3 shell differs from the current template")
    shell.write_text(expected, encoding="utf-8")

    intent = root / "LiveContainerSwiftUI/App/V3SetupAssistantIntent.swift"
    expected_intent = INTENT_TEMPLATE.read_text(encoding="utf-8")
    if intent.exists() and intent.read_text(encoding="utf-8") != expected_intent:
        die("existing v3 setup intent differs from the current template")
    intent.write_text(expected_intent, encoding="utf-8")

    settings = root / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"
    text = settings.read_text(encoding="utf-8")
    text = patch_manual_certificate_import_notification(text)
    if "import SideStoreSupport\n" not in text:
        text = replace_once(text, "import Foundation\n",
                            "import Foundation\nimport SideStoreSupport\n",
                            "canonical certificate import service module")
    if "V3_SERVICE_CERTIFICATE_EXPORT_V1" not in text:
        start = text.index('    func importCertificateFromSideStore() async {')
        end = text.index('    func onSideStoreCertificateCallback(', start)
        replacement = '''    // V3_SERVICE_CERTIFICATE_EXPORT_V1: only the SideStore process can read its
    // active Keychain group. The returned PKCS#12 is transient and enters the
    // existing explicitly-confirmed, request-owned callback path.
    func importCertificateFromSideStore() async {
        // V3_SERVICE_CERTIFICATE_EXPORT_V1
        let requestID = V3CertificateImportOwnership.begin()
        if UserDefaults.sideStoreExist() {
            guard let accepted = await certificateImportFromBuiltInSideStoreAlert.open(), accepted else {
                _ = V3CertificateImportOwnership.cancel(requestID)
                return
            }
            guard V3CertificateImportOwnership.isActive(requestID) else { return }

            do {
                let reply = try await V3ServiceBridge.shared.request(operation: "certExportActive")
                guard V3CertificateImportOwnership.isActive(requestID),
                      Set(reply.keys) == Set(["data", "password", "teamIdentifier", "identitySHA256"]),
                      let data = reply["data"] as? Data, !data.isEmpty, data.count <= 1_048_576,
                      let password = reply["password"] as? String,
                      password.utf8.count <= 512,
                      let team = reply["teamIdentifier"] as? String,
                      !team.isEmpty, team.utf8.count <= 64,
                      let fingerprint = reply["identitySHA256"] as? String,
                      fingerprint.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                      LCUtils.getCertTeamId(withKeyData: data, password: password) == team else {
                    throw NSError(domain: "V3CertificateImport", code: 2)
                }
                let status = try await V3ServiceBridge.shared.request(operation: "healthSnapshot")
                guard V3CertificateImportOwnership.isActive(requestID),
                      let current = status["certificateState"] as? [String: Any],
                      V3ServiceBridge.strictBool(current["active"]) == true,
                      current["team"] as? String == team,
                      current["certificateIdentitySHA256"] as? String == fingerprint else {
                    throw NSError(domain: "V3CertificateImport", code: 3)
                }
                v3CompleteSideStoreCertificateImport(certificateData: data, password: password,
                    requestID: requestID)
            } catch {
                guard V3CertificateImportOwnership.cancel(requestID) else { return }
                errorInfo = "The active SideStore certificate could not be imported. Check Certificates and try again."
                errorShow = true
            }
        } else {
            _ = V3CertificateImportOwnership.cancel(requestID)
            errorInfo = "Embedded SideStore is unavailable in this LiveContainer build."
            errorShow = true
        }
    }

'''
        text = text[:start] + replacement + text[end:]
    if "V3_CANONICAL_JITLESS_ROUTE_V1" not in text:
        text = replace_once(
            text,
            '    @State private var certificateDataFound = false',
            '    @State private var certificateDataFound = false\n    @State private var v3OpenJITLessDiagnose = false // V3_CANONICAL_JITLESS_ROUTE_V1\n' + IMPORT_OWNERSHIP_SWIFT,
            "canonical JIT-Less diagnose route state")
        text = replace_once(
            text,
            '    func handleURL(url: URL) {\n        if url.host == "certificate" {',
            '    func handleURL(url: URL) {\n        if url.host == "jitless-setup" {\n            Task { await importCertificateFromSideStore() }\n            return\n        }\n        if url.host == "jitless-diagnose" {\n            v3OpenJITLessDiagnose = true\n            return\n        }\n        if url.host == "certificate" {',
            "canonical JIT-Less setup and diagnose deep links")
        text = replace_once(
            text,
            '        certificateDataFound = true\n    }',
            '        certificateDataFound = true\n        NotificationCenter.default.post(name: Notification.Name("V3CanonicalJITLessCertificateUpdated"), object: nil)\n    }',
            "canonical JIT-Less import completion event")
        callback_signature = '    func onSideStoreCertificateCallback(certificateData: Data, password: String) {'
        if callback_signature in text:
            text = replace_once(
                text,
                callback_signature,
                '    // Only an exact, live, one-use request may reach the existing three-key writer.\n'
                '    private func v3CompleteSideStoreCertificateImport(certificateData: Data, password: String, requestID: String) {\n'
                '        guard V3CertificateImportOwnership.consume(requestID) else { return }\n'
                '        onSideStoreCertificateCallback(certificateData: certificateData, password: password)\n'
                '    }\n'
                '    func onSideStoreCertificateCallback(certificateData: Data, password: String) {',
                "canonical certificate callback ownership gate")
        external_callback = '                onSideStoreCertificateCallback(certificateData: certData, password: password)'
        if external_callback in text:
            text = replace_once(
                text,
                external_callback,
                '                guard let requestID = queryItems["request_id"],\n'
                '                      V3CertificateImportOwnership.isActive(requestID) else { return }\n'
                '                v3CompleteSideStoreCertificateImport(certificateData: certData, password: password, requestID: requestID)',
                "canonical callback requires exact live request id")
        removal_anchor = '        LCUtils.appGroupUserDefault.set(nil, forKey: "LCCertificateData")'
        if removal_anchor in text:
            text = replace_once(
                text,
                removal_anchor,
                '        V3CertificateImportOwnership.invalidate()\n        LCUtils.appGroupUserDefault.set(nil, forKey: "LCCertificateData")',
                "invalidate certificate import ownership before removal")
        removal_tail = '        UserDefaults.standard.set(nil, forKey: "LCAppGroupID")\n    }'
        if removal_tail in text:
            text = replace_once(
                text,
                removal_tail,
                '        UserDefaults.standard.set(nil, forKey: "LCAppGroupID")\n        NotificationCenter.default.post(name: Notification.Name("V3CanonicalJITLessCertificateUpdated"), object: nil)\n    }',
                "certificate removal readiness invalidation")
    # The programmatic route is required, but a NavigationLink placed as a Form
    # child is a List row participant: SwiftUI still allocates a row and its
    # minimum height for it, so the user sees a blank cell. Upstream uses this
    # exact pattern inside a ScrollView, where there are no rows, which is why it
    # looked harmless there. The link is therefore attached as a background of
    # the Form, which is laid out outside the row structure entirely, so no row
    # and no accessibility element is produced. Guarded on its own marker so a
    # tree patched by an earlier revision is upgraded in place.
    if "V3_JITLESS_ROUTE_ROW_NEUTRALIZED_V1" not in text:
        text = replace_once(
            text,
            '            .navigationBarTitle("lc.tabView.settings".loc)',
            '            // V3_JITLESS_ROUTE_ROW_NEUTRALIZED_V1: a background is laid out\n'
            '            // outside the Form row structure, so this programmatic route cannot\n'
            '            // produce an empty Settings row at any text size or device width, and\n'
            '            // leaves no accessibility ghost element.\n'
            '            .background(\n'
            '                NavigationLink(destination: LCJITLessDiagnoseView(), isActive: $v3OpenJITLessDiagnose) { EmptyView() }\n'
            '                    .hidden()\n'
            '            )\n'
            '            .navigationBarTitle("lc.tabView.settings".loc)',
            "canonical JIT-Less diagnose navigation")
    settings.write_text(text, encoding="utf-8")
    old = '''                if store == .SideStore {
                    Section {
                        NavigationLink { LCEmbeddedSideStoreRefreshView() } label: { Text("SideStore scheduled refresh") }
                    }
                }
'''
    replacement = '''                Section {
                    NavigationLink { LCEmbeddedSideStoreRefreshView() } label: { Text("Refresh, Schedule and History") }
                }
'''
    if old in text:
        settings.write_text(text.replace(old, replacement, 1), encoding="utf-8")
    elif replacement not in text:
        die("refresh settings anchor changed")

    app_list = root / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift"
    text = app_list.read_text(encoding="utf-8")
    launch_button = '''                ToolbarItem(placement: .topBarLeading) {
                    if(UserDefaults.sideStoreExist()) {
                        Button {
                            LCUtils.openSideStore(delegate: self)
                        } label: {
                            IconImageView(icon: BuiltInSideStoreAppInfo.shared.iconIsDarkIcon(darkModeIcon))
                                .frame(width: UIFont.preferredFont(forTextStyle: .body).lineHeight, height: UIFont.preferredFont(forTextStyle: .body).lineHeight)

                        }
                    } else {
                        Button("Help", systemImage: "questionmark") {
                            helpPresent = true
                        }
                    }
                    

                }
                
'''
    if launch_button in text:
        text = text.replace(launch_button, "                // V3_UNIFIED_SHELL_V1: SideStore is reached through unified tabs.\n\n", 1)
        app_list.write_text(text, encoding="utf-8")
    elif "V3_UNIFIED_SHELL_V1: SideStore is reached through unified tabs." not in text:
        die("legacy launch removal anchor changed")


def patch_embedded_status(root: Path) -> None:
    path = root / "AltStore/AppDelegate.swift"
    text = path.read_text(encoding="utf-8")
    marker = "V3_SIDESTORE_STATUS_SNAPSHOT_V1"
    if marker in text:
        return
    anchor = "                debugLog(\"Started DatabaseManager.\")\n"
    insertion = '''                debugLog("Started DatabaseManager.")
                // V3_SIDESTORE_STATUS_SNAPSHOT_V1: retired in favor of live XPC reads.
'''
    text = replace_once(text, anchor, insertion, "database startup status snapshot")
    path.write_text(text, encoding="utf-8")


def exclude_legacy_sources_ui(root: Path) -> None:
    project = root / "LiveContainer.xcodeproj/project.pbxproj"
    text = project.read_text(encoding="utf-8")
    marker = "V3_LEGACY_SOURCES_UI_EXCLUDED_V1"
    expected_group = (
        '17413FB62D9C0BAE00F3F928 /* LiveContainerSwiftUI */ = '
        '{isa = PBXFileSystemSynchronizedRootGroup; explicitFileTypes = {}; '
        'explicitFolders = (); path = LiveContainerSwiftUI; sourceTree = "<group>"; };')
    excluded_group = (
        '17413FB62D9C0BAE00F3F928 /* LiveContainerSwiftUI */ = '
        '{isa = PBXFileSystemSynchronizedRootGroup; '
        'exceptions = (F3D3650F9A3C11B900000001 /* PBXFileSystemSynchronizedBuildFileExceptionSet */, ); '
        'explicitFileTypes = {}; explicitFolders = (); path = LiveContainerSwiftUI; sourceTree = "<group>"; };')
    exclusion = '''\t\tF3D3650F9A3C11B900000001 /* PBXFileSystemSynchronizedBuildFileExceptionSet */ = {
\t\t\tisa = PBXFileSystemSynchronizedBuildFileExceptionSet;
\t\t\tmembershipExceptions = (
\t\t\t\t"Views/LCAltStoreSourcesView.swift",
\t\t\t);
\t\t\ttarget = 17413FB42D9C0BAE00F3F928 /* LiveContainerSwiftUI */;
\t\t};
\t\t/* V3_LEGACY_SOURCES_UI_EXCLUDED_V1: V3SourcesView owns the visible Sources tab. */
'''
    if marker not in text:
        text = replace_once(text, expected_group, excluded_group, "legacy sources build membership")
        text = replace_once(text,
            "/* End PBXFileSystemSynchronizedBuildFileExceptionSet section */",
            exclusion + "/* End PBXFileSystemSynchronizedBuildFileExceptionSet section */",
            "legacy sources build exclusion")
        project.write_text(text, encoding="utf-8")
    elif excluded_group not in text or exclusion not in text:
        die("legacy sources build exclusion drifted")


def verify(live: Path, side: Path) -> None:
    required = (
        live / "LiveContainerSwiftUI/Views/V3UnifiedShell.swift",
        live / "LiveContainerSwiftUI/App/LiveContainerSwiftUIApp.swift",
        live / "LiveContainerSwiftUI/Utilities/Shared.swift",
    )
    if any(not p.exists() for p in required):
        die("v3 host files are missing")
    shell = required[0].read_text(encoding="utf-8")
    for token in (MARKER, "V3SideStoreStatusStore", "V3SourcesView", "LCEmbeddedSideStoreRefreshView", "LCTabIdentifier.settings",
                  "V3SignInView", "V3CertificatesView", "V3PromptSection", "V3PairingView", "V3AuthStore",
                  "V3SetupAssistantView", "V3SetupStore", "setupPresented", "V3JITLessStatusReader",
                  "pendingCanonicalJITLessSetup", "livecontainer://jitless-setup"):
        if token not in shell:
            die(f"v3 shell is missing {token}")
    for forbidden in ("V3RemoteServiceView", "Self.presenter", "presentingViewController: Self.presenter"):
        if forbidden in shell:
            die(f"v3 shell still embeds SideStore UI: {forbidden}")
    intent = live / "LiveContainerSwiftUI/App/V3SetupAssistantIntent.swift"
    if not intent.exists() or "V3SetupAssistantIntent" not in intent.read_text(encoding="utf-8"):
        die("v3 setup intent is missing")
    if "V3UnifiedShell()" not in required[1].read_text(encoding="utf-8"):
        die("v3 shell is not the application root")
    app_list = (live / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift").read_text(encoding="utf-8")
    if "V3_UNIFIED_SHELL_V1: SideStore is reached through unified tabs." not in app_list:
        die("legacy SideStore launch button removal marker is missing from the Apps screen")
    settings_source = (live / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift").read_text(encoding="utf-8")
    if "import SideStoreSupport\n" not in settings_source:
        die("canonical certificate importer cannot see its service module")
    for token in ("V3_CANONICAL_JITLESS_ROUTE_V1", "importCertificateFromSideStore()",
                  "v3OpenJITLessDiagnose = true", "V3CanonicalJITLessCertificateUpdated"):
        if token not in settings_source:
            die(f"canonical LiveContainer JIT-Less route is missing {token}")
    importer_start = settings_source.index("func importCertificateFromSideStore() async {")
    importer_end = settings_source.index("private func v3CompleteSideStoreCertificateImport", importer_start)
    importer = settings_source[importer_start:importer_end]
    for token in ("V3CertificateImportOwnership.begin()",
                  "V3CertificateImportOwnership.isActive(requestID)",
                  "V3CertificateImportOwnership.cancel(requestID)",
                  "V3_SERVICE_CERTIFICATE_EXPORT_V1",
                  'operation: "certExportActive"',
                  'Set(reply.keys) == Set(["data", "password", "teamIdentifier", "identitySHA256"])',
                  "data.count <= 1_048_576", "password.utf8.count <= 512",
                  "LCUtils.getCertTeamId(withKeyData: data, password: password) == team",
                  'operation: "healthSnapshot"',
                  'current["certificateIdentitySHA256"] as? String == fingerprint',
                  "v3CompleteSideStoreCertificateImport(certificateData: data, password: password,",
                  "Embedded SideStore is unavailable in this LiveContainer build."):
        if token not in importer:
            die(f"canonical JIT-Less importer is missing {token}")
    if any(token in importer for token in ("SecItemCopyMatching", "sharedKeychainAccessGroup", "kSecAttrAccessGroup")):
        die("canonical JIT-Less importer still reads a host Keychain group")
    if "storeScheme" in importer or "UIApplication.shared.open(url)" in importer:
        die("canonical combined JIT-Less importer still launches a second app")
    if "static func cancel(_ requestID:" not in settings_source:
        die("canonical certificate import owner lacks exact-request cancellation")
    if "V3_JITLESS_ROUTE_ROW_NEUTRALIZED_V1" not in settings_source:
        die("canonical JIT-Less route is not row-neutralized (an empty Settings row would render)")
    if "V3_SIDESTORE_STATUS_SNAPSHOT_V1" not in (side / "AltStore/AppDelegate.swift").read_text(encoding="utf-8"):
        die("embedded SideStore snapshot retirement marker is missing")
    project = (live / "LiveContainer.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
    if "V3_LEGACY_SOURCES_UI_EXCLUDED_V1" not in project or \
            '"Views/LCAltStoreSourcesView.swift"' not in project:
        die("legacy LiveContainer sources view remains in the production target")
    if '"LCAltStoreSourceURLs"' not in shell:
        die("legacy source URL migration read must survive UI exclusion")
    compiler = shutil.which("swiftc")
    if compiler:
        for path in (required[0], side / "AltStore/AppDelegate.swift"):
            subprocess.run([compiler, "-frontend", "-parse", str(path)], check=True)


def patch(live: Path, side: Path) -> None:
    # Validate the complete transaction on disposable copies before touching inputs.
    paths = (
        ("LiveContainer.xcodeproj/project.pbxproj",
         "LiveContainerSwiftUI/Utilities/Shared.swift", "LiveContainerSwiftUI/App/LiveContainerSwiftUIApp.swift",
         "LiveContainerSwiftUI/App/V3SetupAssistantIntent.swift",
         "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift", "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift",
         "LiveContainerSwiftUI/Views/V3UnifiedShell.swift"),
        ("AltStore/AppDelegate.swift",))
    with tempfile.TemporaryDirectory(prefix="v3-shell-") as temporary:
        staged = (Path(temporary) / "live", Path(temporary) / "side")
        for original, destination, names in zip((live, side), staged, paths):
            for name in names:
                if (original / name).exists():
                    (destination / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original / name, destination / name)
        patch_host(staged[0])
        exclude_legacy_sources_ui(staged[0])
        patch_embedded_status(staged[1])
        verify(*staged)
        for original, destination, names in zip((live, side), staged, paths):
            for name in names:
                (original / name).parent.mkdir(parents=True, exist_ok=True)
                (original / name).write_bytes((destination / name).read_bytes())


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: patch_v3_unified_shell.py <livecontainer-root> <embedded-sidestore-root>")
    from patch_v3_service import PINS
    for root, pin in zip(sys.argv[1:], PINS):
        if subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip() != pin:
            die("input revision does not match the combined source pin")
    patch(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    print("v3 unified shell patch applied and verified")


if __name__ == "__main__":
    main()
