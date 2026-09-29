#!/usr/bin/env python3
"""Explicit, shared Keychain namespace for combined SideStore UI and LiveProcess.

Run AFTER background and startup patches. No new entitlements, IDs, transport,
plaintext credential storage, or guard bypass. Existing secrets migrate only
within the exact SideStore service and already authorized access groups.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys

MARKER = "LC_EMBEDDED_SHARED_KEYCHAIN_V1"
AUTH_MANAGER_MARKER = "LC_AUTH_CREDENTIAL_SNAPSHOT_V1"
AUTH_SESSION_SNAPSHOT_MARKER = "LC_AUTHENTICATED_SESSION_SNAPSHOT_V1"
BACKGROUND_AUTH_SNAPSHOT_MARKER = "LC_AUTO_REFRESH_CREDENTIAL_SNAPSHOT_V1"
BACKGROUND_AUTH_MISSING_MARKER = "LC_AUTH_CREDENTIALS_MISSING_V1"
SIGN_IN_SNAPSHOT_MARKER = "LC_SIGNIN_CREDENTIAL_SNAPSHOT_V1"
IMPORT_EXPORT_SNAPSHOT_MARKER = "LC_IMPORT_EXPORT_CREDENTIAL_SNAPSHOT_V1"
TEMPLATE = Path(__file__).parent / "templates/embedded_shared_keychain.swift"
KEYCHAIN_ACCESS_ADAPTER = '''extension Keychain {
    func authenticationSnapshot() throws -> LCEmbeddedAuthenticationSnapshot? {
        try LCEmbeddedSharedKeychain.readAuthenticationSnapshot(self.keychain)
    }
    func writeAuthenticationCredentials(appleID: String, password: String,
                                        dsid: String, authToken: String) throws {
        try LCEmbeddedSharedKeychain.writeAuthenticationCredentials(
            appleID: appleID, password: password, dsid: dsid, authToken: authToken,
            client: self.keychain)
    }
    func clearSignInInfoChecked() throws {
        try LCEmbeddedSharedKeychain.clearSignInInfoChecked(self.keychain)
    }
    func embeddedAuthenticationFailure(_ error: Error) -> NSError {
        LCEmbeddedSharedKeychain.authenticationFailure(for: error)
    }
}'''


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"embedded keychain: changed upstream anchor {old[:100]!r}")
    return text.replace(old, new, 1)


def patch_auth_manager(text: str) -> str:
    if AUTH_MANAGER_MARKER in text:
        if ("Keychain.shared.authenticationSnapshot()" not in text or
                AUTH_SESSION_SNAPSHOT_MARKER not in text or
                "try Keychain.shared.authenticationSnapshot()" not in text or
                "credentialSnapshot?.appleIDAdsid" not in text or
                "credentialSnapshot?.appleIDXcodeToken" not in text or
                "Keychain.shared.embeddedAuthenticationFailure(error)" not in text):
            raise ValueError("embedded keychain: auth snapshot adapter is missing")
        start = text.index("public func getAuthenticatedSession()")
        end = text.index("\n    }", start)
        session = text[start:end]
        if "self.adsid" in session or "self.xcodeToken" in session:
            raise ValueError("embedded keychain: authenticated session still combines independent getters")
        return text
    old = '''    public var isAuthenticated: Bool {
        let hasEmail = Keychain.shared.appleIDEmailAddress != nil
        let hasPassword = Keychain.shared.appleIDPassword != nil
        let hasToken = Keychain.shared.appleIDXcodeToken != nil
        return hasEmail && (hasPassword || hasToken)
    }'''
    new = '''    // LC_AUTH_CREDENTIAL_SNAPSHOT_V1
    var authenticationSnapshot: LCEmbeddedAuthenticationSnapshot? {
        try? Keychain.shared.authenticationSnapshot()
    }

    public var isAuthenticated: Bool {
        authenticationSnapshot?.isAuthenticated ?? false
    }'''
    text = once(text, old, new)
    text = once(text, '''    public var hasStoredPassword: Bool {
        return Keychain.shared.appleIDPassword != nil
    }''', '''    public var hasStoredPassword: Bool {
        return authenticationSnapshot?.hasPasswordCredentials ?? false
    }''')
    text = once(text, '''    public var hasStoredXcodeToken: Bool {
        return Keychain.shared.appleIDXcodeToken != nil
    }''', '''    public var hasStoredXcodeToken: Bool {
        return authenticationSnapshot?.hasTokenCredentials ?? false
    }''')
    text = once(text,
        '''            guard let adsid = self.adsid,                           // directory services id
                  let xcodeToken = self.xcodeToken else             // xcode token
            {''',
        "            // " + AUTH_SESSION_SNAPSHOT_MARKER + "\n"
        "            let credentialSnapshot: LCEmbeddedAuthenticationSnapshot?\n"
        "            do { credentialSnapshot = try Keychain.shared.authenticationSnapshot() }\n"
        "            catch { throw Keychain.shared.embeddedAuthenticationFailure(error) }\n"
        "            guard let adsid = credentialSnapshot?.appleIDAdsid,\n"
        "                  let xcodeToken = credentialSnapshot?.appleIDXcodeToken else {")
    return text


def patch_background_auth_snapshot(text: str) -> str:
    marker = "// " + BACKGROUND_AUTH_SNAPSHOT_MARKER
    if marker in text:
        required = ("authSnapshot?.appleIDPassword", "authSnapshot?.appleIDXcodeToken",
                    "Keychain.shared.authenticationSnapshot()",
                    "Keychain.shared.embeddedAuthenticationFailure(error)")
        if not all(value in text for value in required) or "auth.currentAppleID" in text or "auth.adsid" in text:
            raise ValueError("embedded keychain: background auth preflight snapshot is incomplete")
        return text
    old = '''        let auth = AuthManager.shared
        let credentials = auth.authenticationSnapshot
        let hasPasswordCredentials = credentials?.appleIDEmailAddress != nil && credentials?.appleIDPassword != nil
        let hasTokenCredentials = credentials?.appleIDAdsid != nil && credentials?.appleIDXcodeToken != nil
        let hasReusableSession = auth.session != nil && auth.team != nil && CertificateManager.shared.activeCertificate != nil
        debugLog("[AUTO_REFRESH] AUTH_CREDENTIAL_VISIBILITY password_path=\\(hasPasswordCredentials) token_path=\\(hasTokenCredentials) session_path=\\(hasReusableSession)")
'''
    new = '''        // ''' + BACKGROUND_AUTH_SNAPSHOT_MARKER + ''': each credential route uses one locked Keychain epoch.
        let auth = AuthManager.shared
        let hasReusableSession = auth.v3CachedSessionMatchesCurrentRoute(auth.session) &&
            auth.team != nil && CertificateManager.shared.activeCertificate != nil
        let authSnapshot: LCEmbeddedAuthenticationSnapshot?
        if hasReusableSession {
            authSnapshot = nil
        } else {
            do {
                authSnapshot = try Keychain.shared.authenticationSnapshot()
            } catch {
                let error = Keychain.shared.embeddedAuthenticationFailure(error)
                debugLog("[AUTO_REFRESH] AUTH_PREFLIGHT_FAIL reason=keychain_access")
                self.scheduleFinishedRefreshingNotification(for: .failure(error), delay: 0)
                throw error
            }
        }
        let hasPasswordCredentials = authSnapshot?.appleIDEmailAddress != nil && authSnapshot?.appleIDPassword != nil
        let hasTokenCredentials = authSnapshot?.appleIDAdsid != nil && authSnapshot?.appleIDXcodeToken != nil
        debugLog("[AUTO_REFRESH] AUTH_CREDENTIAL_VISIBILITY password_path=\\(hasPasswordCredentials) token_path=\\(hasTokenCredentials) session_path=\\(hasReusableSession)")
'''
    return once(text, old, new)


def patch_sign_in_operation(text: str) -> str:
    if SIGN_IN_SNAPSHOT_MARKER in text:
        required = ("AuthManager.shared.authenticationSnapshot", "credentials?.appleIDAdsid",
                    "credentials?.appleIDXcodeToken", "credentials?.appleIDEmailAddress",
                    "credentials?.appleIDPassword",
                    "V3_AUTH_FAILURE_PRESERVES_ACCOUNT_STATE_V1")
        if not all(token in text for token in required):
            raise ValueError("embedded keychain: SignInOperation credential snapshot or non-destructive failure contract is incomplete")
        marker = text.index("V3_AUTH_FAILURE_PRESERVES_ACCOUNT_STATE_V1")
        terminal = text.index("try? await self.finalizeAuthentication", marker)
        destructive = ("signOut", "clearSignInInfo", "clearActiveCertificate",
                       "deactivateActiveAccountAndTeam")
        if any(value in text[marker:terminal] for value in destructive):
            raise ValueError("embedded keychain: failed sign-in catch still performs destructive sign-out")
        return text
    if "V3_AUTH_FAILURE_PRESERVES_ACCOUNT_STATE_V1" not in text:
        raise ValueError("embedded keychain: SignInOperation lacks non-destructive failure contract")

    start = text.index("    private func silentSignIn() async throws -> (ALTAccount, ALTAppleAPISession)? {")
    end = text.index("\n    private func authenticationLoop()", start)
    section = text[start:end]
    section = section.replace(
        "    private func silentSignIn() async throws -> (ALTAccount, ALTAppleAPISession)? {",
        "    private func silentSignIn() async throws -> (ALTAccount, ALTAppleAPISession)? {\n"
        "        // LC_SIGNIN_CREDENTIAL_SNAPSHOT_V1\n"
        "        let credentials = AuthManager.shared.authenticationSnapshot", 1)
    token_pattern = re.compile(
        r'        if let adsid = AuthManager\.shared\.adsid,\s*'
        r'let xcodeToken = AuthManager\.shared\.xcodeToken\s*\{')
    section, token_count = token_pattern.subn(
        '        if let adsid = credentials?.appleIDAdsid,\n'
        '           let xcodeToken = credentials?.appleIDXcodeToken {', section, count=1)
    password_pattern = re.compile(
        r'        if let appleID = AuthManager\.shared\.currentAppleID,\s*'
        r'let password = AuthManager\.shared\.password\s*\{')
    section, password_count = password_pattern.subn(
        '        if let appleID = credentials?.appleIDEmailAddress,\n'
        '           let password = credentials?.appleIDPassword {', section, count=1)
    if token_count != 1 or password_count != 1:
        raise ValueError("embedded keychain: SignInOperation silent credential pairs changed")
    return text[:start] + section + text[end:]


def patch_import_export(text: str) -> str:
    if IMPORT_EXPORT_SNAPSHOT_MARKER in text:
        if text.count("AuthManager.shared.authenticationSnapshot") < 2:
            raise ValueError("embedded keychain: ImportExport credential snapshot is incomplete")
        return text

    def patch_export_segment(source: str, start_anchor: str, end_anchor: str,
                             label: str) -> str:
        start = source.index(start_anchor)
        end = source.index(end_anchor, start)
        segment = source[start:end]
        segment = once(segment, "        guard let email = AuthManager.shared.currentAppleID,",
            "        // LC_IMPORT_EXPORT_CREDENTIAL_SNAPSHOT_V1\n"
            "        let authSnapshot = AuthManager.shared.authenticationSnapshot\n"
            "        guard let email = authSnapshot?.appleIDEmailAddress,")
        segment, count = re.subn(r"AuthManager\.shared\.password", "authSnapshot?.appleIDPassword", segment)
        expected = 2 if label == "account backup" else 1
        if count != expected:
            raise ValueError(f"embedded keychain: {label} password snapshot anchors changed ({count})")
        return source[:start] + segment + source[end:]

    text = patch_export_segment(text, "    public static func exportAccount(",
        "    public static func importAccount(", "account backup")
    text = patch_export_segment(text, "    static func exportAccountJSON(",
        "    static func importAccountJSON(", "debug account backup")
    return text


def patch(root: Path) -> None:
    path = root / "AltStore/Core/Components/Keychain.swift"
    operation = root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
    auth_manager = root / "SideStore/Core/Auth/AuthManager.swift"
    sign_in = root / "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift"
    import_export = root / "SideStore/Utils/importexport/ImportExport.swift"
    original = path.read_text(encoding="utf-8")
    text = original
    auth_text = auth_manager.read_text(encoding="utf-8")
    sign_in_text = sign_in.read_text(encoding="utf-8")
    import_export_text = import_export.read_text(encoding="utf-8")
    helper = TEMPLATE.read_text(encoding="utf-8")
    if MARKER not in text:
        text = once(text, "import Foundation\n", "import Foundation\nimport Security\n#if canImport(Darwin)\nimport Darwin\n#elseif canImport(Glibc)\nimport Glibc\n#endif\n")
        text = once(text, "KeychainAccess.Keychain(service: Bundle.Info.appbundleIdentifier)\n                                            .accessibility(.afterFirstUnlock)\n                                            .synchronizable(true)",
                    "LCEmbeddedSharedKeychain.makeClient()")
        text = once(text, "case is Data.Type: return try? Keychain.shared.keychain.getData(self.key) as? Value",
                    "case is Data.Type: return LCEmbeddedSharedKeychain.read(self.key, client: Keychain.shared.keychain) as? Value")
        text = once(text, "case is String.Type: return try? Keychain.shared.keychain.getString(self.key) as? Value",
                    "case is String.Type: return LCEmbeddedSharedKeychain.readString(self.key, client: Keychain.shared.keychain) as? Value")
        text = once(text, "case is Data.Type: Keychain.shared.keychain[data: self.key] = newValue as? Data",
                    "case is Data.Type: LCEmbeddedSharedKeychain.write(self.key, data: newValue as? Data, client: Keychain.shared.keychain)")
        text = once(text, "case is String.Type: Keychain.shared.keychain[self.key] = newValue as? String",
                    "case is String.Type: LCEmbeddedSharedKeychain.write(self.key, data: (newValue as? String).map { Data($0.utf8) }, client: Keychain.shared.keychain)")
        text = once(text, "        self.migrateLegacyKeychainItems()", "        LCEmbeddedSharedKeychain.prepare(self.keychain)\n        if LCEmbeddedSharedKeychain.isReady(self.keychain) { self.migrateLegacyKeychainItems() }")
        text = once(text, 'get { try? self.keychain.getData("importedCert_" + serial) }',
                    'get { LCEmbeddedSharedKeychain.read("importedCert_" + serial, client: self.keychain) }')
        text = once(text, '''            if let data = newValue {
                try? self.keychain.set(data, key: "importedCert_" + serial)
            } else {
                try? self.keychain.remove("importedCert_" + serial)
            }''', '            LCEmbeddedSharedKeychain.write("importedCert_" + serial, data: newValue, client: self.keychain)')
        text = once(text, "        try? self.keychain.removeAll()", "        LCEmbeddedSharedKeychain.clearAll(self.keychain)")
        text += "\n" + helper + "\n" + KEYCHAIN_ACCESS_ADAPTER + "\n"
    elif (helper not in text or "func writeAuthenticationCredentials(appleID: String, password: String," not in text or
          "func authenticationSnapshot() throws -> LCEmbeddedAuthenticationSnapshot?" not in text or
          "func embeddedAuthenticationFailure(_ error: Error) -> NSError" not in text):
        missing = [name for name, present in (
            ("helper", helper in text),
            ("bulk auth writer", "func writeAuthenticationCredentials(appleID: String, password: String," in text),
            ("auth snapshot bridge", "func authenticationSnapshot() throws -> LCEmbeddedAuthenticationSnapshot?" in text),
            ("error-aware auth failure bridge", "func embeddedAuthenticationFailure(_ error: Error) -> NSError" in text),
        ) if not present]
        raise ValueError("outdated shared keychain patch: missing " + ", ".join(missing))
    op = patch_background_auth_snapshot(operation.read_text(encoding="utf-8"))
    missing_marker = "// " + BACKGROUND_AUTH_MISSING_MARKER
    if missing_marker in op:
        if ("code: 1004" not in op or "The refresh process cannot access saved sign-in credentials" not in op or
                "Keychain.shared.embeddedAuthenticationFailure()" in op):
            raise ValueError("embedded keychain: missing-credentials failure classification drifted")
    else:
        start = op.index("        guard hasPasswordCredentials || hasTokenCredentials || hasReusableSession else {")
        end = op.index('            debugLog("[AUTO_REFRESH] AUTH_PREFLIGHT_FAIL', start)
        # Keep the guard, logging, failure notification, and throwing behavior.
        replacement = (
            "        guard hasPasswordCredentials || hasTokenCredentials || hasReusableSession else {\n"
            "            // " + BACKGROUND_AUTH_MISSING_MARKER + ": key access failures are classified at the throwing read.\n"
            '            let error = NSError(domain: "com.SideStore.Authentication", code: 1004,\n'
            '                userInfo: [NSLocalizedDescriptionKey: "The refresh process cannot access saved sign-in credentials or a reusable session. Open SideStore to check your account."])\n'
        )
        op = op[:start] + replacement + op[end:]
    assert "guard hasPasswordCredentials || hasTokenCredentials || hasReusableSession else" in op
    assert 'throw error' in op
    auth_text = patch_auth_manager(auth_text)
    sign_in_text = patch_sign_in_operation(sign_in_text)
    import_export_text = patch_import_export(import_export_text)
    # Both transforms are validated before writing either file.
    path.write_text(text, encoding="utf-8")
    operation.write_text(op, encoding="utf-8")
    auth_manager.write_text(auth_text, encoding="utf-8")
    sign_in.write_text(sign_in_text, encoding="utf-8")
    import_export.write_text(import_export_text, encoding="utf-8")
    if compiler := shutil.which("swiftc"):
        for file in (path, operation, auth_manager, sign_in, import_export):
            subprocess.run([compiler, "-frontend", "-parse", str(file)], check=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_embedded_keychain.py <embedded-sidestore-root>")
    patch(Path(sys.argv[1]))
