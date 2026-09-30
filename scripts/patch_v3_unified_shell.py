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
                            "public enum LCTabIdentifier: Hashable {\n    case home\n    case sources\n    case apps\n    case refresh\n    case tweaks\n    case settings\n}", "tab identifiers")
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
    if "V3_SHARED_KEYCHAIN_GROUP_SCOPE_V1" not in text:
        if "import Security" not in text:
            text = replace_once(text, "import Foundation\n", "import Foundation\nimport Security\n",
                "canonical JIT-Less keychain query Security import")
        text = replace_once(text,
            '    func importCertificateFromSideStore() async {\n'
            '        if UserDefaults.sideStoreExist() {\n'
            '            if let ans = await certificateImportFromBuiltInSideStoreAlert.open(), ans {\n'
            '                let query: [String: Any] = [',
            '    // V3_SHARED_KEYCHAIN_GROUP_SCOPE_V1: select the current embedded SideStore\n'
            '    // credential group explicitly; stale legacy-group copies must not win a\n'
            '    // kSecMatchLimitOne query after migration.\n'
            '    private func v3SharedSideStoreKeychainAccessGroup() -> String? {\n'
            '        return try? V3SecretHandoff.sharedKeychainAccessGroup()\n'
            '    }\n'
            '    func importCertificateFromSideStore() async {\n'
            '        if UserDefaults.sideStoreExist() {\n'
            '            if let ans = await certificateImportFromBuiltInSideStoreAlert.open(), ans {\n'
            '                guard let sharedKeychainGroup = v3SharedSideStoreKeychainAccessGroup() else {\n'
            '                    errorInfo = "The shared SideStore signing certificate is unavailable in this app build."\n'
            '                    errorShow = true\n'
            '                    return\n'
            '                }\n'
            '                let query: [String: Any] = [',
            "canonical built-in certificate query scope")
        old_group_query = (
            '                    kSecAttrService as String: "com.kdt.livecontainer",\n'
            '                    kSecAttrSynchronizable as String: kSecAttrSynchronizableAny')
        new_group_query = (
            '                    kSecAttrService as String: "com.kdt.livecontainer",\n'
            '                    kSecAttrAccessGroup as String: sharedKeychainGroup,\n'
            '                    kSecAttrSynchronizable as String: kSecAttrSynchronizableAny')
        if text.count(old_group_query) != 2:
            die("canonical built-in certificate importer: expected certificate and password queries")
        text = text.replace(old_group_query, new_group_query)
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
        text = replace_once(
            text,
            '    func importCertificateFromSideStore() async {\n        if UserDefaults.sideStoreExist() {',
            '    func importCertificateFromSideStore() async {\n'
            '        let requestID = V3CertificateImportOwnership.begin()\n'
            '        if UserDefaults.sideStoreExist() {',
            "canonical certificate import request ownership")
        text = replace_once(
            text,
            '            if let ans = await certificateImportFromBuiltInSideStoreAlert.open(), ans {\n                guard let sharedKeychainGroup = v3SharedSideStoreKeychainAccessGroup() else {',
            '            if let ans = await certificateImportFromBuiltInSideStoreAlert.open(), ans {\n                guard V3CertificateImportOwnership.isActive(requestID) else { return }\n                guard let sharedKeychainGroup = v3SharedSideStoreKeychainAccessGroup() else {',
            "canonical built-in import ownership after await")
        built_in_callback = '                onSideStoreCertificateCallback(certificateData: data, password: password)\n'
        if built_in_callback in text:
            text = replace_once(
                text,
                built_in_callback,
                '                v3CompleteSideStoreCertificateImport(certificateData: data, password: password, requestID: requestID)\n',
                "canonical built-in import completion ownership")
        cancellation_suffix = (
            ' else {\n'
            '                // A decline or dismissal cancels this exact prompt.\n'
            '                _ = V3CertificateImportOwnership.cancel(requestID)\n'
            '                return\n'
            '            }\n'
            '        }\n'
            '        // A missing embedded SideStore is an invalid combined product.\n'
            '        _ = V3CertificateImportOwnership.cancel(requestID)\n'
            '        errorInfo = "Embedded SideStore is unavailable in this LiveContainer build."\n'
            '        errorShow = true')
        with_return = '                return\n            }\n        }'
        without_return = '            }\n        }'
        if text.count(with_return) == 1:
            text = text.replace(
                with_return,
                '                return\n            }' + cancellation_suffix,
                1)
        elif text.count(without_return) == 1:
            text = text.replace(
                without_return,
                '            }' + cancellation_suffix,
                1)
        else:
            die("canonical built-in confirmation cancellation anchor changed")
        fallback_start = '        let storeScheme'
        fallback_end = '        await UIApplication.shared.open(url)\n'
        if text.count(fallback_start) == 1 and text.count(fallback_end) == 1:
            start = text.index(fallback_start)
            end = text.index(fallback_end, start) + len(fallback_end)
            text = text[:start] + text[end:]
        elif text.count(fallback_start) != 0 or text.count(fallback_end) != 0:
            die("canonical combined JIT-Less importer fallback anchors changed")
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
        external_callback = '                onSideStoreCertificateCallback(certificateData: certData, password: password)'
        if external_callback in text:
            text = replace_once(
                text,
                external_callback,
                '                guard let requestID = queryItems["request_id"],\n                      V3CertificateImportOwnership.isActive(requestID) else { return }\n                v3CompleteSideStoreCertificateImport(certificateData: certData, password: password, requestID: requestID)',
                "canonical callback requires exact live request id")
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
    for token in ("V3_CANONICAL_JITLESS_ROUTE_V1", "importCertificateFromSideStore()",
                  "v3OpenJITLessDiagnose = true", "V3CanonicalJITLessCertificateUpdated"):
        if token not in settings_source:
            die(f"canonical LiveContainer JIT-Less route is missing {token}")
    for token in ("V3_SHARED_KEYCHAIN_GROUP_SCOPE_V1",
                  "V3SecretHandoff.sharedKeychainAccessGroup()",
                  "kSecAttrAccessGroup as String: sharedKeychainGroup"):
        if token not in settings_source:
            die(f"canonical JIT-Less importer is missing {token}")
    if settings_source.count("kSecAttrAccessGroup as String: sharedKeychainGroup") != 2:
        die("canonical JIT-Less importer must scope both certificate and password queries")
    importer_start = settings_source.index("func importCertificateFromSideStore() async {")
    importer_end = settings_source.index("private func v3CompleteSideStoreCertificateImport", importer_start)
    importer = settings_source[importer_start:importer_end]
    for token in ("V3CertificateImportOwnership.begin()",
                  "V3CertificateImportOwnership.isActive(requestID)",
                  "V3CertificateImportOwnership.cancel(requestID)",
                  "Embedded SideStore is unavailable in this LiveContainer build."):
        if token not in importer:
            die(f"canonical JIT-Less importer is missing cancellation contract {token}")
    if "storeScheme" in importer or "UIApplication.shared.open(url)" in importer:
        die("canonical combined JIT-Less importer still launches a second app")
    if "static func cancel(_ requestID:" not in settings_source:
        die("canonical certificate import owner lacks exact-request cancellation")
    if "V3_JITLESS_ROUTE_ROW_NEUTRALIZED_V1" not in settings_source:
        die("canonical JIT-Less route is not row-neutralized (an empty Settings row would render)")
    if "V3_SIDESTORE_STATUS_SNAPSHOT_V1" not in (side / "AltStore/AppDelegate.swift").read_text(encoding="utf-8"):
        die("embedded SideStore snapshot retirement marker is missing")
    compiler = shutil.which("swiftc")
    if compiler:
        for path in (required[0], side / "AltStore/AppDelegate.swift"):
            subprocess.run([compiler, "-frontend", "-parse", str(path)], check=True)


def patch(live: Path, side: Path) -> None:
    # Validate the complete transaction on disposable copies before touching inputs.
    paths = (
        ("LiveContainerSwiftUI/Utilities/Shared.swift", "LiveContainerSwiftUI/App/LiveContainerSwiftUIApp.swift",
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
