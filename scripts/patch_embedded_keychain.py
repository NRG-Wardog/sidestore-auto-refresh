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
IMPORT_EXPORT_HEADLESS_MARKER = "LC_HEADLESS_IMPORT_EXPORT_UI_REMOVED_V1"
TEMPLATE = Path(__file__).parent / "templates/embedded_shared_keychain.swift"
KEYCHAIN_ACCESS_ADAPTER = '''extension Keychain {
    func resolveAnisetteSnapshot() throws -> LCEmbeddedAnisetteSnapshot {
        try LCEmbeddedSharedKeychain.resolveAnisetteSnapshot(self.keychain)
    }
    func commitAnisetteBlob(_ blob: Data, snapshot: LCEmbeddedAnisetteSnapshot) throws {
        try LCEmbeddedSharedKeychain.commitAnisetteBlob(blob, snapshot: snapshot, client: self.keychain)
    }
    func validateAnisetteSnapshot(_ snapshot: LCEmbeddedAnisetteSnapshot) throws {
        try LCEmbeddedSharedKeychain.validateAnisetteSnapshot(snapshot, client: self.keychain)
    }
    func anisetteRecoveryCandidate(for snapshot: LCEmbeddedAnisetteSnapshot) throws -> LCAnisetteRecoveryCandidate? {
        try LCEmbeddedSharedKeychain.anisetteRecoveryCandidate(for: snapshot, client: self.keychain)
    }
    func commitAnisetteRecovery(_ proof: LCAnisetteRecoveryProof) throws -> LCEmbeddedAnisetteSnapshot {
        try LCEmbeddedSharedKeychain.commitAnisetteRecovery(proof, client: self.keychain)
    }
    func authenticationSnapshot() throws -> LCEmbeddedAuthenticationSnapshot? {
        try LCEmbeddedSharedKeychain.readAuthenticationSnapshot(self.keychain)
    }
    func writeAuthenticationCredentials(appleID: String, password: String,
                                        dsid: String, authToken: String) throws {
        try LCEmbeddedSharedKeychain.writeAuthenticationCredentials(
            appleID: appleID, password: password, dsid: dsid, authToken: authToken,
            client: self.keychain)
    }
    func authenticationCandidate() throws -> LCEmbeddedAuthenticationCandidate? {
        try LCEmbeddedSharedKeychain.readAuthenticationCandidate(self.keychain)
    }
    func writeVerifiedAuthentication(_ candidate: LCEmbeddedAuthenticationCandidate,
                                     appleID: String, dsid: String, authToken: String) throws {
        try LCEmbeddedSharedKeychain.writeAuthenticationCredentials(
            appleID: appleID, password: candidate.credentials.appleIDPassword,
            dsid: dsid, authToken: authToken, expectedCandidate: candidate, client: self.keychain)
    }
    func reconcileStorage() throws {
        try LCEmbeddedSharedKeychain.reconcileStorage(self.keychain) { data, password in
            try CertificateManager.parse(data, password: password).serialNumber
        }
    }
    func storageRequiresReconciliation() throws -> Bool {
        try LCEmbeddedSharedKeychain.storageRequiresReconciliation(self.keychain)
    }
    func signingCertificateSnapshot() throws -> LCEmbeddedSigningCertificateSnapshot? {
        try LCEmbeddedSharedKeychain.readSigningCertificateSnapshot(self.keychain)
    }
    func writeSigningCertificate(p12Data: Data?, password: String?, serial: String?,
                                 validate: (Data, String?) throws -> Void) throws {
        try LCEmbeddedSharedKeychain.writeSigningCertificate(p12Data: p12Data,
            password: password, serial: serial, client: self.keychain, validate: validate)
    }
    func clearSignInInfoChecked() throws {
        try LCEmbeddedSharedKeychain.clearSignInInfoChecked(self.keychain)
    }
    func embeddedAuthenticationFailure(_ error: Error) -> Error {
        if error is LCAnisettePairError { return error }
        return LCEmbeddedSharedKeychain.authenticationFailure(for: error)
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
                    "authSnapshot?.appleIDEmailAddress", "authSnapshot?.appleIDAdsid",
                    "Keychain.shared.authenticationSnapshot()",
                    "Keychain.shared.embeddedAuthenticationFailure(error)")
        if (not all(value in text for value in required)
                or text.count("Keychain.shared.authenticationSnapshot()") != 1
                or "auth.currentAppleID" in text or "auth.adsid" in text):
            raise ValueError("embedded keychain: background auth preflight snapshot is incomplete")
        return text
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines)
              if line == "        let auth = AuthManager.shared\n"]
    if len(starts) != 1:
        raise ValueError("embedded keychain: expected one generated background auth preflight")
    start = starts[0]
    expected = (
        "        let credentials = auth.authenticationSnapshot\n",
        "        let hasPasswordCredentials = ",
        "        let hasTokenCredentials = ",
        "        let hasReusableSession = ",
    )
    if (start + len(expected) >= len(lines)
            or lines[start + 1] != expected[0]
            or not lines[start + 2].startswith(expected[1])
            or not lines[start + 3].startswith(expected[2])
            or not lines[start + 4].startswith(expected[3])):
        raise ValueError("embedded keychain: changed generated background auth preflight anchor")
    debug_index = next((index for index in range(start + 5, len(lines))
                        if lines[index].startswith('        debugLog("[AUTO_REFRESH] AUTH_CREDENTIAL_VISIBILITY ')), None)
    if debug_index is None:
        raise ValueError("embedded keychain: generated background auth visibility log is missing")
    session_lines = lines[start + 4:debug_index]
    debug_line = lines[debug_index]
    replacement = [
        lines[start],
        *session_lines,
        "        // " + BACKGROUND_AUTH_SNAPSHOT_MARKER + ": each credential route uses one locked Keychain epoch.\n",
        "        let authSnapshot: LCEmbeddedAuthenticationSnapshot?\n",
        "        if hasReusableSession {\n",
        "            authSnapshot = nil\n",
        "        } else {\n",
        "            do {\n",
        "                authSnapshot = try Keychain.shared.authenticationSnapshot()\n",
        "            } catch {\n",
        "                let error = Keychain.shared.embeddedAuthenticationFailure(error)\n",
        '                debugLog("[AUTO_REFRESH] AUTH_PREFLIGHT_FAIL reason=keychain_access")\n',
        "                self.scheduleFinishedRefreshingNotification(for: .failure(error), delay: 0)\n",
        "                throw error\n",
        "            }\n",
        "        }\n",
        "        let hasPasswordCredentials = authSnapshot?.appleIDEmailAddress != nil && authSnapshot?.appleIDPassword != nil\n",
        "        let hasTokenCredentials = authSnapshot?.appleIDAdsid != nil && authSnapshot?.appleIDXcodeToken != nil\n",
        debug_line,
    ]
    lines[start:debug_index + 1] = replacement
    return "".join(lines)


def patch_sign_in_operation(text: str) -> str:
    if SIGN_IN_SNAPSHOT_MARKER in text:
        required = ("Keychain.shared.authenticationCandidate()", "LC_VERIFIED_LEGACY_AUTH_V1",
                    "Keychain.shared.writeVerifiedAuthentication", "credentials?.appleIDAdsid",
                    "credentials?.appleIDXcodeToken", "credentials?.appleIDEmailAddress",
                    "credentials?.appleIDPassword", "v3AuthenticationPhase(.accountLookup)",
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
        "        // LC_VERIFIED_LEGACY_AUTH_V1: routes are not yet bound identities.\n"
        "        let capturedStamp = AuthManager.shared.v3IdentityStamp\n"
        "        guard AuthManager.shared.v3IdentityIsStable else { throw OperationError.notAuthenticated }\n"
        "        let candidate: LCEmbeddedAuthenticationCandidate?\n"
        "        do { candidate = try Keychain.shared.authenticationCandidate() }\n"
        "        catch { throw v3AccountOperationFailure(error, step: .credentialCommit) }\n"
        "        let credentials = candidate?.credentials", 1)
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
    section = once(section,
        "                return try await AuthManager.shared.authenticateWithToken(",
        "                let (account, session) = try await v3AuthenticationPhase(.accountLookup) {\n"
        "                    try await AuthManager.shared.authenticateWithToken(")
    section = once(section,
        "                    xcodeVersion: xcodeVersion\n                )\n",
        "                    xcodeVersion: xcodeVersion\n                )\n                }\n"
        "                guard !self.isCancelled, !Task.isCancelled else { throw OperationError.cancelled }\n"
        "                guard let candidate, AuthManager.shared.v3IdentityIsStable,\n"
        "                      capturedStamp == AuthManager.shared.v3IdentityStamp,\n"
        "                      account.identifier == session.dsid,\n"
        "                      candidate.matchesVerifiedIdentity(appleID: account.appleID, dsid: session.dsid) else {\n"
        "                    throw v3AccountOperationFailure(NSError(domain: \"LiveContainerRefresh.Configuration\", code: 1008), step: .credentialCommit)\n"
        "                }\n"
        "                AuthManager.shared.v3BeginIdentityTransition()\n"
        "                defer { AuthManager.shared.v3CompleteIdentityTransition() }\n"
        "                do {\n"
        "                    try Keychain.shared.writeVerifiedAuthentication(candidate, appleID: account.appleID,\n"
        "                        dsid: session.dsid, authToken: session.authToken)\n"
        "                } catch { throw v3AccountOperationFailure(error, step: .credentialCommit) }\n"
        "                AuthManager.shared.session = session\n"
        "                return (account, session)\n")
    # Keep failure after Apple success visible and terminal. In particular, never
    # fall through to stored-password replay after an uncertain local commit.
    section = once(section,
        '            } catch {\n                self.debugLog("[SignInOperation] Token authentication failed: \\(error)")',
        '            } catch {\n'
        '                if error is V3AccountOperationError { throw error }\n'
        '                if let phase = error as? V3AuthenticationPhaseError, phase.underlying is LCAnisettePairError { throw error }\n'
        '                if self.isCancelled || Task.isCancelled || error is CancellationError { throw OperationError.cancelled }\n'
        '                self.debugLog("[V3_AUTH] saved_token_verification_failed")')
    section = once(section,
        "                return try await self.signIn(appleID: appleID, password: password)",
        "                return try await self.signIn(appleID: appleID, password: password,\n"
        "                    recoveryCandidate: candidate, capturedStamp: capturedStamp)")
    # patch_v3_service also installs this guard in new builds. Support its
    # presence without duplicating it, and reject a swallowed local failure.
    saved_catch = section.index('self.debugLog("[SignInOperation] Saved password authentication failed:')
    saved_prefix = section[:saved_catch]
    if "if error is V3AccountOperationError" not in saved_prefix[saved_prefix.rfind("} catch {"):]:
        section = section[:saved_catch] + "if error is V3AccountOperationError { throw error }\n                " + section[saved_catch:]
    saved_catch = section.index('self.debugLog("[SignInOperation] Saved password authentication failed:')
    section = section[:saved_catch] + "if let phase = error as? V3AuthenticationPhaseError, phase.underlying is LCAnisettePairError { throw error }\n                " + section[saved_catch:]
    text = text[:start] + section + text[end:]
    text = once(text,
        "    private func signIn(appleID: String, password: String) async throws -> (ALTAccount, ALTAppleAPISession) {",
        "    private func signIn(appleID: String, password: String,\n"
        "                        recoveryCandidate: LCEmbeddedAuthenticationCandidate? = nil,\n"
        "                        capturedStamp: String? = nil) async throws -> (ALTAccount, ALTAppleAPISession) {")
    transition = "        AuthManager.shared.v3BeginIdentityTransition()\n"
    # Restrict the anchor to interactive/password signIn; the token branch owns
    # its own transition above.
    start = text.index("    private func signIn(appleID: String, password: String,")
    head, body = text[:start], text[start:]
    body = once(body, transition,
        "        if let candidate = recoveryCandidate {\n"
        "            guard !self.isCancelled, !Task.isCancelled else { throw OperationError.cancelled }\n"
        "            guard AuthManager.shared.v3IdentityIsStable, capturedStamp == AuthManager.shared.v3IdentityStamp,\n"
        "                  account.identifier == session.dsid,\n"
        "                  candidate.matchesVerifiedIdentity(appleID: account.appleID, dsid: session.dsid) else {\n"
        "                throw v3AccountOperationFailure(NSError(domain: \"LiveContainerRefresh.Configuration\", code: 1008), step: .credentialCommit)\n"
        "            }\n"
        "        }\n" + transition)
    writer = "try Keychain.shared.writeAuthenticationCredentials(appleID: appleID, password: password, dsid: session.dsid, authToken: session.authToken)"
    body = once(body, writer,
        "if let candidate = recoveryCandidate {\n"
        "                do {\n"
        "                    try Keychain.shared.writeVerifiedAuthentication(candidate, appleID: account.appleID,\n"
        "                        dsid: session.dsid, authToken: session.authToken)\n"
        "                } catch { throw v3AccountOperationFailure(error, step: .credentialCommit) }\n"
        "            } else {\n                " + writer + "\n            }")
    return head + body


def patch_import_export(text: str) -> str:
    if IMPORT_EXPORT_HEADLESS_MARKER in text:
        required = (
            IMPORT_EXPORT_SNAPSHOT_MARKER,
            "public static func exportAccount(password: String, includeApplePassword: Bool)",
            "public static func importAccount(_ encryptedData: Data, filePassword: String)",
            "AES.GCM.seal(jsonData, using: key)",
            "AES.GCM.open(sealedBox, using: key)",
            "AuthManager.shared.authenticationSnapshot",
        )
        forbidden = (
            "UIDocumentPicker", "UIViewController", "DocumentPickerHandler",
            "documentPickerHandler", "AssociatedKeys", "importBackup(",
            "importBackupContents(", "renameBackupContents(", "getPreviousBackupURL(",
        )
        if (not all(token in text for token in required)
                or text.count("AuthManager.shared.authenticationSnapshot") < 2
                or any(token in text for token in forbidden)):
            raise ValueError("embedded keychain: headless ImportExport patch is incomplete")
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

    if IMPORT_EXPORT_SNAPSHOT_MARKER in text:
        if text.count("AuthManager.shared.authenticationSnapshot") < 2:
            raise ValueError("embedded keychain: ImportExport credential snapshot is incomplete")
    else:
        text = patch_export_segment(text, "    public static func exportAccount(",
        "    public static func importAccount(", "account backup")
        text = patch_export_segment(text, "    static func exportAccountJSON(",
        "    static func importAccountJSON(", "debug account backup")

    # SideStore's native settings UI uses this folder picker for local app-data
    # restoration. The embedded headless target restores pipeline data through
    # its own host staging path, and retains only encrypted account backups here.
    text = once(text, "@preconcurrency import UIKit\nimport SideSign\n",
                "import Foundation\nimport Security\nimport SideSign\n")
    text = once(text, '''    #if !os(tvOS)
    public static var documentPickerHandler: DocumentPickerHandler?
    #endif

''', "")
    text = once(text, "class ImportExport {\n", "class ImportExport {\n    // " + IMPORT_EXPORT_HEADLESS_MARKER + ": account backup encryption remains available to the host pipeline.\n")
    picker_start = text.index("    public static func getPreviousBackupURL(")
    picker_end = text.index("\n}\n\n#if DEBUG", picker_start)
    text = text[:picker_start] + text[picker_end:]
    text = once(text, '''
#if !os(tvOS)
private struct AssociatedKeys {
    static var documentPickerHandler: UInt8 = 0
}


class DocumentPickerHandler: NSObject, UIDocumentPickerDelegate {
    private let completion: (URL?) -> Void

    init(completion: @escaping (URL?) -> Void) {
        self.completion = completion
    }

    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        completion(urls.first)
    }

    func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
        completion(nil)
    }
}
#endif
''', "\n")
    # Removing the trailing picker block leaves the file's own terminator
    # followed by a blank line, which git diff --check rejects as a new blank
    # line at EOF. End the file with exactly one newline, as upstream does.
    text = text.rstrip("\n") + "\n"
    if (IMPORT_EXPORT_HEADLESS_MARKER not in text
            or "public static func exportAccount(" not in text
            or "public static func importAccount(" not in text
            or any(token in text for token in (
                "UIDocumentPicker", "UIViewController", "DocumentPickerHandler",
                "documentPickerHandler", "AssociatedKeys", "importBackup(",
                "importBackupContents(", "renameBackupContents(", "getPreviousBackupURL(",
            ))):
        raise ValueError("embedded keychain: ImportExport folder picker removal is partial")
    return text


def patch_certificate_manager(text: str) -> str:
    marker = "LC_VERIFIED_ACTIVE_CERTIFICATE_V1"
    if marker in text:
        required = ("try Keychain.shared.writeSigningCertificate", "try Keychain.shared.signingCertificateSnapshot()",
                    "try Self.parse(storedData, password: storedPassword)", "self.recordCertificateMetadata(cert)")
        if any(value not in text for value in required):
            raise ValueError("embedded keychain: verified certificate persistence patch is incomplete")
        return text
    start = text.index("    @discardableResult\n    public func loadActiveCertificate()")
    end = text.index("\n    public func getPassword(for cert:", start)
    text = text[:start] + '''    // LC_VERIFIED_ACTIVE_CERTIFICATE_V1
    @discardableResult
    public func loadActiveCertificate() throws -> ActiveSigningCertificate? {
        do {
            guard let stored = try Keychain.shared.signingCertificateSnapshot() else {
                self.activeCertificate = nil
                return nil
            }
            let cert = try Self.parse(stored.p12Data, password: stored.password)
            let active = ActiveSigningCertificate(certificate: cert, p12Data: stored.p12Data, password: stored.password)
            self.activeCertificate = active
            return active
        } catch {
            self.activeCertificate = nil
            throw error
        }
    }
''' + text[end:]
    start = text.index("    public func setActiveCertificate(_ cert:")
    end = text.index("    // MARK: - Certificate Encoding Helpers", start)
    text = text[:start] + '''    public func setActiveCertificate(_ cert: ALTCertificate?) throws {
        do {
            if let cert {
                let password = getPassword(for: cert)
                let p12Data = try Self.convert(cert, password: password)
                try Keychain.shared.writeSigningCertificate(p12Data: p12Data, password: password,
                    serial: cert.serialNumber) { storedData, storedPassword in
                    let parsed = try Self.parse(storedData, password: storedPassword)
                    guard parsed.serialNumber == cert.serialNumber else {
                        throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                    }
                }
                self.recordCertificateMetadata(cert)
                self.activeCertificate = ActiveSigningCertificate(certificate: cert, p12Data: p12Data, password: password)
            } else {
                try Keychain.shared.writeSigningCertificate(p12Data: nil, password: nil, serial: nil) { _, _ in }
                self.activeCertificate = nil
            }
        } catch {
            // Preserve the last proven certificate on a verified rollback;
            // an uncertain transaction must not remain advertised in memory.
            let native = error as NSError
            if native.domain == "com.SideStore.Keychain" && native.code == 1010 {
                self.activeCertificate = nil
            }
            throw error
        }
    }

    public func clearActiveCertificate() {
        do { try self.setActiveCertificate(nil) }
        catch { debugLog("[LC_KEYCHAIN] certificate_clear_failed") }
    }

''' + text[end:]
    start = text.index("    public func saveCertificate(_ cert:")
    end = text.index("    public func saveX509Certificate(", start)
    segment = text[start:end]
    metadata_start = segment.index("        let serials = getImportedCertificateSerials()")
    metadata = segment[metadata_start:]
    segment = segment[:metadata_start] + "        self.recordCertificateMetadata(cert)\n    }\n\n" + \
        "    private func recordCertificateMetadata(_ cert: ALTCertificate) {\n" + metadata
    text = text[:start] + segment + text[end:]
    return text



ANISETTE_PAIR_MARKER = "LC_ANISETTE_PAIR_PRECONDITION_V1"
ANISETTE_PATHS = (
    "SideStore/Core/Anisette/AnisetteConfigManager.swift",
    "SideStore/Core/Anisette/OnDeviceAnisetteManager.swift",
    "SideStore/Core/Anisette/AnisetteProvider.swift",
)


def patch_anisette_config(text: str) -> str:
    replacement = '''    // LC_ANISETTE_PAIR_PRECONDITION_V1
    public func resolveDeviceIdentifier() throws -> UUID {
        try resolveAnisetteSnapshot().identifier
    }

    func resolveAnisetteSnapshot() throws -> LCEmbeddedAnisetteSnapshot {
        try Keychain.shared.resolveAnisetteSnapshot()
    }

    func commitAnisetteBlob(_ blob: Data, snapshot: LCEmbeddedAnisetteSnapshot) throws {
        try Keychain.shared.commitAnisetteBlob(blob, snapshot: snapshot)
    }'''
    if ANISETTE_PAIR_MARKER in text:
        if replacement not in text:
            raise ValueError("embedded anisette: changed resolver precondition")
        return text
    old = '''    public func resolveDeviceIdentifier() -> UUID {
        if let storedId = anisetteIdentifier, !storedId.isEmpty {
            if let parsed = UUID(uuidString: storedId) {
                return parsed
            }
            if let data = Data(base64Encoded: storedId), data.count == 16 {
                let uuid = data.withUnsafeBytes { UUID(uuid: $0.load(as: uuid_t.self)) }
                return uuid
            }
        }
        let generated = UUID()
        anisetteIdentifier = generated.uuidString
        return generated
    }'''
    return once(text, old, replacement)


def patch_anisette_legacy_recovery(text: str) -> str:
    support = (Path(__file__).parent / "templates/anisette_legacy_recovery.swift").read_text()
    marker = "LC_ANISETTE_VERIFIED_LEGACY_RECOVERY_V1"
    if marker in text:
        if support.strip() not in text or "return try await recoverVerifiedLegacyIdentity(" not in text:
            raise ValueError("embedded anisette: legacy recovery adapter drift")
        return text
    old = """        let (anisetteData, newAdiPb) = try await provider.fetchAnisetteData(
            mode: mode,
            identifier: identifierUUID,
            existingAdiBlob: existingAdiPbData,
            headers: headers
        )"""
    new = """        let primary: (data: ALTAnisetteData, newAdiBlob: Data?)
        do {
            primary = try await provider.fetchAnisetteData(
                mode: mode, identifier: identifierUUID,
                existingAdiBlob: existingAdiPbData, headers: headers)
        } catch {
            return try await recoverVerifiedLegacyIdentity(
                after: error, snapshot: anisetteSnapshot, headers: headers)
        }
        let (anisetteData, newAdiPb) = primary"""
    text = once(text, old, new)
    text = once(text, "import Foundation", "import Foundation\nimport Darwin")
    return text.rstrip() + "\n\n" + support


def patch_anisette_provider(text: str, *, on_device: bool) -> str:
    if ANISETTE_PAIR_MARKER in text:
        if ("let anisetteSnapshot = try await AnisetteConfigManager.shared.resolveAnisetteSnapshot()" not in text or
                "try await AnisetteConfigManager.shared.commitAnisetteBlob(freshBlob, snapshot: anisetteSnapshot)" not in text or
                "AnisetteConfigManager.shared.anisetteAdiBlob" in text or
                "AnisetteConfigManager.shared.resolveDeviceIdentifier()" in text):
            raise ValueError("embedded anisette: provider does not consume one guarded snapshot")
        return patch_anisette_legacy_recovery(text) if on_device else text
    if on_device:
        old = '''        let identifierUUID = await AnisetteConfigManager.shared.resolveDeviceIdentifier()

        var existingAdiPbData: Data? = nil
        if let base64Blob = AnisetteConfigManager.shared.anisetteAdiBlob,
           let decoded = Data(base64Encoded: base64Blob, options: .ignoreUnknownCharacters),
           !decoded.isEmpty {
            existingAdiPbData = decoded
            debugLog("[OnDeviceAnisetteManager] [Fetch] Reusing existing adi.pb from Keychain (\\(decoded.count) bytes)")
        } else {
            debugLog("[OnDeviceAnisetteManager] [Fetch] No existing adi.pb in Keychain -> local in-memory provisioning will be performed")
        }'''
        new = '''        // LC_ANISETTE_PAIR_PRECONDITION_V1
        let anisetteSnapshot = try await AnisetteConfigManager.shared.resolveAnisetteSnapshot()
        let identifierUUID = anisetteSnapshot.identifier
        let existingAdiPbData = anisetteSnapshot.adiBlob'''
    else:
        old = '''        let existingBlob = AnisetteConfigManager.shared.anisetteAdiBlob.flatMap { Data(base64Encoded: $0) }
        let identifier = await AnisetteConfigManager.shared.resolveDeviceIdentifier()'''
        new = '''        // LC_ANISETTE_PAIR_PRECONDITION_V1
        let anisetteSnapshot = try await AnisetteConfigManager.shared.resolveAnisetteSnapshot()
        let existingBlob = anisetteSnapshot.adiBlob
        let identifier = anisetteSnapshot.identifier'''
    text = once(text, old, new)
    text = once(text, "AnisetteConfigManager.shared.anisetteAdiBlob = freshBlob.base64EncodedString()",
        "try await AnisetteConfigManager.shared.commitAnisetteBlob(freshBlob, snapshot: anisetteSnapshot)")
    return patch_anisette_legacy_recovery(text) if on_device else text


def patch(root: Path) -> None:
    path = root / "AltStore/Core/Components/Keychain.swift"
    operation = root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
    auth_manager = root / "SideStore/Core/Auth/AuthManager.swift"
    sign_in = root / "SideStore/Core/Operations/StandaloneOperations/SignInOperation.swift"
    import_export = root / "SideStore/Utils/importexport/ImportExport.swift"
    certificate_manager = root / "SideStore/Core/Certificates/CertificateManager.swift"
    original = path.read_text(encoding="utf-8")
    text = original
    auth_text = auth_manager.read_text(encoding="utf-8")
    sign_in_text = sign_in.read_text(encoding="utf-8")
    import_export_text = import_export.read_text(encoding="utf-8")
    certificate_text = patch_certificate_manager(certificate_manager.read_text(encoding="utf-8"))
    anisette_paths = [root / relative for relative in ANISETTE_PATHS]
    anisette_texts = [patch_anisette_config(anisette_paths[0].read_text(encoding="utf-8")),
        patch_anisette_provider(anisette_paths[1].read_text(encoding="utf-8"), on_device=True),
        patch_anisette_provider(anisette_paths[2].read_text(encoding="utf-8"), on_device=False)]
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
        text = once(text, '''            try self.keychain.set(p12Data, key: signingCertificateKey)
            try self.keychain.set("", key: "signingCertificatePassword")''', '''            try LCEmbeddedSharedKeychain.writeSigningCertificate(p12Data: p12Data, password: nil,
                serial: cert.serialNumber, client: self.keychain) { storedData, _ in
                let parsed = try ALTCertificate(p12Data: storedData)
                guard parsed.serialNumber == cert.serialNumber else {
                    throw NSError(domain: "com.SideStore.Keychain", code: 1009)
                }
            }''')
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
          "func embeddedAuthenticationFailure(_ error: Error) -> Error" not in text):
        missing = [name for name, present in (
            ("helper", helper in text),
            ("bulk auth writer", "func writeAuthenticationCredentials(appleID: String, password: String," in text),
            ("auth snapshot bridge", "func authenticationSnapshot() throws -> LCEmbeddedAuthenticationSnapshot?" in text),
            ("error-aware auth failure bridge", "func embeddedAuthenticationFailure(_ error: Error) -> Error" in text),
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
    certificate_manager.write_text(certificate_text, encoding="utf-8")
    for anisette_path, anisette_text in zip(anisette_paths, anisette_texts):
        anisette_path.write_text(anisette_text, encoding="utf-8")
    if compiler := shutil.which("swiftc"):
        for file in (path, operation, auth_manager, sign_in, import_export, certificate_manager, *anisette_paths):
            subprocess.run([compiler, "-frontend", "-parse", str(file)], check=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_embedded_keychain.py <embedded-sidestore-root>")
    patch(Path(sys.argv[1]))
