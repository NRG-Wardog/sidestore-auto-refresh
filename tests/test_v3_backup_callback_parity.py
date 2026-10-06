"""Backup control ownership, failure return routing and pinned producer patches."""
from pathlib import Path
import importlib.util
import ast
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "scripts/templates"
spec = importlib.util.spec_from_file_location("backup_service_patch", ROOT / "scripts/patch_v3_service.py")
PATCH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PATCH)


def declaration(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if not depth:
                return source[start:index + 1]
    raise AssertionError("unterminated declaration")


def pinned_source(relative):
    root = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.environ.get("SIDESTORE_TEST_SOURCE")
    if not root:
        raise unittest.SkipTest("pinned SideStore source unavailable")
    return subprocess.check_output(["git", "-C", root, "show", PATCH.PINS[1] + ":" + relative], text=True)


class BackupCallbackParityTests(unittest.TestCase):
    def test_producer_preserves_callback_correlation_and_failure_destination(self):
        source = pinned_source("SideBackup/SideBackupApp.swift")
        patched = PATCH.headless_sidebackup_response(source)
        self.assertEqual(PATCH.headless_sidebackup_response(patched), patched)
        self.assertIn('components.queryItems = callbackItems', patched)
        self.assertIn('let queryTargetBundleID = queryItems["targetBundleID"]', patched)
        self.assertIn('var callbackItems = (components.queryItems ?? []).filter', patched)
        self.assertNotIn('"errorDomain": error.domain', patched)
        self.assertNotIn('return URL: \\(responseURL.absoluteString)', patched)
        project = pinned_source("AltStore.xcodeproj/project.pbxproj")
        self.assertIn('target = BF58047A246A28F7008AE704 /* SideBackup */', project)
        self.assertIn('make -B clean-sidebackup copy-sidebackup ipa-sidebackup', project)

    def test_pipeline_patch_binds_observer_and_keeps_external_cancellation_unknown(self):
        original = pinned_source("SideStore/Core/Operations/PipelineOperations/PerformBackupRestoreOperation.swift")
        patched = PATCH.headless_backup_operation(original)
        self.assertEqual(PATCH.headless_backup_operation(patched), patched)
        self.assertLess(patched.index('beginBackupCallback(action:'), patched.index('let backupRespObs'))
        self.assertLess(patched.index('let backupRespObs'), patched.index('let openedSuccessfully'))
        self.assertIn('callback?.queryItems ?? []', patched)
        self.assertIn('notification.userInfo?["v3BackupNonce"] as? String != callback.nonce', patched)
        self.assertIn('if callback == nil {', patched)
        self.assertIn('headlessHandler.endBackupCallback(callback)', patched)
        self.assertEqual(patched.count("!handler.backupCallbackMayOpen()"), 2)
        self.assertNotIn('openURL.absoluteString), returnURL:', patched)
        self.assertNotIn('UserInfo: \\(String(describing: notification.userInfo))', patched)
        runtime = (TEMPLATES / "v3_headless_runtime.swift").read_text()
        cancellation = declaration(runtime, '    func cancelAndWait(id: String, knownStarted:')
        self.assertIn('if settledSession.backupCallback != nil, task != nil', cancellation)
        self.assertLess(cancellation.index('return poll(id: id)'), cancellation.index('await task.value'))
        self.assertNotIn('mutationRegistry.finish', cancellation)
        shell = (TEMPLATES / "v3_unified_shell.swift").read_text()
        host_cancel = declaration(shell, '    private func cancelAttempt()')
        self.assertIn('state = "reconciling"', host_cancel)
        self.assertIn('needsDeviceConfirmation = true', host_cancel)
        self.assertIn('backend_settled=no outcome=unknown', host_cancel)
        self.assertIn('backing up, restoring, or deleting', shell)


    def test_both_entrypoints_require_pending_nonce_and_keep_owner(self):
        service = (TEMPLATES / "v3_sidestore_service.swift").read_text()
        bridge = (TEMPLATES / "v3_service_bridge.swift").read_text()
        local_url = (TEMPLATES / "v3_headless_url_handler.swift").read_text()
        self.assertIn('operations.ownsBackupCallback($0)', service)
        self.assertEqual(service.count('backupCallbackControl: backupCallbackControl'), 2)
        self.assertIn('operations.acceptBackupCallback(result)', service)
        self.assertIn('operations.acceptBackupCallback(result)', local_url)
        self.assertNotIn('NotificationCenter.default.post', local_url)
        runtime = (TEMPLATES / "v3_headless_runtime.swift").read_text()
        accept = declaration(runtime, '    func acceptBackupCallback(')
        self.assertIn('try? V3OperationRecoveryJournal.current()', accept)
        self.assertIn('recovery.phase == .dispatched', accept)
        self.assertLess(accept.index('V3OperationRecoveryJournal.current()'), accept.index('endBackupCallback'))

        submit = declaration(bridge, '    public func submitBackupCallback(')
        self.assertLess(submit.index('operation: "opPoll"'), submit.index('operation: "backupResult"'))
        self.assertIn('V3BackupCallbackIdentity(state["backupCallback"]) == result.identity', submit)
        self.assertIn('backupSessionServiceIDs[session]', submit)
        self.assertIn('if operation == "backupResult", !scopedBackupCallback', bridge)
        self.assertIn('backupCallbackBindings[target] == $0.identity', bridge)
        self.assertIn('let scopedSessionControl = scopedBackupCallback ||', bridge)
        session_lookup = declaration(bridge, '    private func operationSessionID(')
        self.assertIn('"backupResult"', session_lookup)

        wire = (TEMPLATES / "v3_wire_contract.swift").read_text()
        reads = wire[wire.index('static let readOperations'):wire.index('static func decodeRequest')]
        self.assertNotIn('"backupResult"', reads)

    def test_backup_producer_and_consumer_are_in_exact_build_evidence(self):
        inventories = []
        for filename, variable in (("combined_build_evidence.py", "EMBEDDED_SOURCE_PATHS"),
                                   ("verify_candidate_ipa.py", "REQUIRED_GENERATED_EMBEDDED_SOURCES")):
            tree = ast.parse((ROOT / "scripts" / filename).read_text())
            value = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == variable for target in node.targets))
            inventories.append(set(ast.literal_eval(value)))
        for expected in ("SideStore/Core/Operations/PipelineOperations/PerformBackupRestoreOperation.swift",
                         "SideBackup/SideBackupApp.swift"):
            self.assertTrue(all(expected in inventory for inventory in inventories))
        self.assertEqual(*inventories)

    def test_each_supported_pipeline_has_one_external_step(self):
        source = pinned_source("SideStore/Core/Operations/OperationStepDefinition.swift")
        for kind, step in (("backup", "backupAppData"), ("deactivate", "backupAppData"),
                           ("activate", "restoreAppData"), ("restore", "restoreAppData")):
            pipeline = source.split("static let " + kind + ": [PipelineExecutionStep] = [", 1)[1].split("\n    ]", 1)[0]
            self.assertEqual(pipeline.count("PipelineExecutionStep(." + step + ","), 1)
            self.assertEqual(pipeline.count("PipelineExecutionStep(.backupAppData,") +
                             pipeline.count("PipelineExecutionStep(.restoreAppData,"), 1)

    def test_production_callback_harness(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; production callback harness runs in macOS CI")
        primitives = (TEMPLATES / "v3_behavioral_primitives.swift").read_text()
        runtime = (TEMPLATES / "v3_headless_runtime.swift").read_text()
        producer = PATCH.headless_sidebackup_response(pinned_source("SideBackup/SideBackupApp.swift"))
        callback_response = producer[producer.index('        var callbackItems ='):producer.index('\n        guard let responseURL', producer.index('        var callbackItems ='))]
        declarations = "\n".join(declaration(primitives, signature) for signature in (
            'struct V3BackupCallbackIdentity:', 'struct V3BackupCallbackResult:',
            'enum V3ServiceMutationAdmissionPolicy', 'struct V3ServiceRecoveryAdmissionDecision:',
            'enum V3ServiceRecoveryAdmissionPolicy', 'enum V3OperationSessionCorrelationPolicy',
            'enum V3RequestRetirementPolicy'))
        owner_methods = "\n".join(declaration(runtime, signature) for signature in (
            '    func beginBackupCallback(id:', '    func endBackupCallback(_ identity:',
            '    func ownsBackupCallback(_ result:', '    func acceptBackupCallback(_ result:'))
        harness = HARNESS.replace('// PRODUCTION_DECLARATIONS', declarations).replace('// OWNER_METHODS', owner_methods).replace('// PRODUCER_RESPONSE', callback_response)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'BackupCallback.swift'
            executable = Path(directory) / 'backup-callback'
            source.write_text(harness)
            subprocess.run([compiler, '-parse-as-library', str(source), '-o', str(executable)], check=True, capture_output=True, text=True)
            completed = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn('backup callback production harness passed', completed.stdout)


HARNESS = r'''
import Foundation
import CoreFoundation

enum V3SideStoreServiceError: Error { case invalidRequest }
enum V3AuthSessionAdmissionPolicy {
    static func mayStartNewSession(hasActiveSession: Bool) -> Bool { !hasActiveSession }
}
struct V3OperationRecoveryRecord {
    enum Phase { case prepared, dispatched }
    let sessionID: String
    let kind: String
    let phase: Phase
}
enum V3OperationRecoveryJournal {
    static var record: V3OperationRecoveryRecord?
    static var readFails = false
    static func current() throws -> V3OperationRecoveryRecord? {
        if readFails { throw V3SideStoreServiceError.invalidRequest }
        return record
    }
}
enum AppDelegate {
    static let appBackupDidFinish = Notification.Name("backup-result-fixture")
    static let appBackupResultKey = "result"
}
// PRODUCTION_DECLARATIONS

@MainActor
final class Owner {
    struct Terminal { var isEmpty = true; var isCancellationRequested = false }
    struct Session { var kind: String; var terminal = Terminal(); var backupCallback: V3BackupCallbackIdentity? }
    struct Registry { var activeID: String? }
    var sessions: [String: Session] = [:]
    var mutationRegistry = Registry()
    // OWNER_METHODS
}
final class ObserverCapture: @unchecked Sendable {
    private let lock = NSLock()
    var count = 0
    var lastNonce: String?
    var wasSuccessful = false
    func receive(_ notification: Notification) {
        lock.lock(); defer { lock.unlock() }
        count += 1
        lastNonce = notification.userInfo?["v3BackupNonce"] as? String
        if case .success? = notification.userInfo?[AppDelegate.appBackupResultKey] as? Result<Void, Error> {
            wasSuccessful = true
        } else { wasSuccessful = false }
    }
}

func sideBackupResponse(returnURL: URL, result: Result<Void, Error>) -> URL {
    var components = URLComponents(url: returnURL, resolvingAgainstBaseURL: false)!
    // PRODUCER_RESPONSE
    return components.url!
}

@main
struct Harness {
    @MainActor
    static func main() throws {
        let session = UUID().uuidString
        let other = UUID().uuidString
        let owner = Owner()
        owner.mutationRegistry.activeID = session
        owner.sessions[session] = Owner.Session(kind: "backup")
        V3OperationRecoveryJournal.record = V3OperationRecoveryRecord(sessionID: session, kind: "backup", phase: .dispatched)
        let binding = try owner.beginBackupCallback(id: session, action: "backup")
        let capture = ObserverCapture()
        let observer = NotificationCenter.default.addObserver(forName: AppDelegate.appBackupDidFinish,
            object: nil, queue: nil) { capture.receive($0) }
        defer { NotificationCenter.default.removeObserver(observer) }

        var returnComponents = URLComponents(string: "sidestore://appBackupResponse")!
        returnComponents.queryItems = [URLQueryItem(name: "targetBundleID", value: "com.fixture.host")] + binding.queryItems
        let returnURL = returnComponents.url!
        let success = sideBackupResponse(returnURL: returnURL, result: .success(()))
        let failure = sideBackupResponse(returnURL: returnURL, result: .failure(NSError(domain: "private-account", code: 123,
            userInfo: [NSLocalizedDescriptionKey: "private diagnostic text"])))
        let successResult = V3BackupCallbackResult(url: success, expectedTargetBundleID: "com.fixture.host")!
        let failureResult = V3BackupCallbackResult(url: failure, expectedTargetBundleID: "com.fixture.host")!
        precondition(successResult.identity == binding && successResult.succeeded)
        precondition(failureResult.identity == binding && !failureResult.succeeded)
        precondition(!failure.absoluteString.contains("private"))
        precondition(URLComponents(url: failure, resolvingAgainstBaseURL: false)!.queryItems!.first { $0.name == "targetBundleID" }!.value == "com.fixture.host")
        precondition(V3BackupCallbackResult(url: success, expectedTargetBundleID: "wrong.host") == nil)

        for invalid in ["sidestore://appBackupResponse/success", success.absoluteString + "&v3Nonce=" + binding.nonce,
                        success.absoluteString + "&unknown=value", success.absoluteString + "#fragment",
                        success.absoluteString.replacingOccurrences(of: "/success", with: "/unknown"),
                        success.absoluteString.replacingOccurrences(of: "sidestore://", with: "https://")] {
            precondition(V3BackupCallbackResult(url: URL(string: invalid)!, expectedTargetBundleID: "com.fixture.host") == nil)
        }
        precondition(V3BackupCallbackResult(session: "success", payload: successResult.payload) == nil)
        var badPayload = successResult.payload; badPayload["extra"] = "field"
        precondition(V3BackupCallbackResult(session: session, payload: badPayload) == nil)
        badPayload = successResult.payload; badPayload["nonce"] = UUID().uuidString
        let stale = V3BackupCallbackResult(session: session, payload: badPayload)!
        precondition(!owner.ownsBackupCallback(stale) && !owner.acceptBackupCallback(stale))
        precondition(!owner.acceptBackupCallback(V3BackupCallbackResult(session: other, payload: successResult.payload)!))
        badPayload = successResult.payload; badPayload["action"] = "restore"
        precondition(!owner.acceptBackupCallback(V3BackupCallbackResult(session: session, payload: badPayload)!))
        precondition(capture.count == 0)

        func checkGate(_ bound: Bool, expectedBlocked: Bool) {
            let conflict = V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
                operation: "backupResult", target: session, activeOperationID: session, backupCallbackControl: bound)
            let recovery = V3OperationRecoveryRecord(sessionID: session, kind: "backup", phase: .dispatched)
            let decision = V3ServiceRecoveryAdmissionPolicy.decide(operation: "backupResult", target: session,
                payload: successResult.payload, operationSessionID: session, recovery: recovery,
                recoveryReadFailed: false, refreshOwnerLost: false, backupCallbackControl: bound)
            precondition(conflict == expectedBlocked && decision.blocksMutation == expectedBlocked)
            precondition(V3ServiceMutationAdmissionPolicy.admits(isMutation: true,
                anotherMutationActive: conflict || decision.blocksMutation,
                authenticationActive: false, isAuthContinuation: false,
                responseCapacityAvailable: true) == !expectedBlocked)
        }
        checkGate(false, expectedBlocked: true)
        checkGate(owner.ownsBackupCallback(successResult), expectedBlocked: false)
        precondition(V3ServiceMutationAdmissionPolicy.hasConflictingOperationMutation(
            operation: "backupResult", target: other, activeOperationID: session, backupCallbackControl: true))
        let blocked = V3ServiceRecoveryAdmissionPolicy.decide(operation: "backupResult", target: session,
            payload: successResult.payload, operationSessionID: session,
            recovery: V3OperationRecoveryRecord(sessionID: other, kind: "backup", phase: .dispatched),
            recoveryReadFailed: false, refreshOwnerLost: false, backupCallbackControl: true)
        precondition(blocked.blocksMutation)
        precondition(!V3RequestRetirementPolicy.shouldRetireServiceIfRequestStaysPending("backupResult"))

        // Local URL delivery must satisfy the same durable recovery owner as XPC.
        V3OperationRecoveryJournal.record = nil
        precondition(!owner.acceptBackupCallback(successResult))
        V3OperationRecoveryJournal.record = V3OperationRecoveryRecord(sessionID: other, kind: "backup", phase: .dispatched)
        precondition(!owner.acceptBackupCallback(successResult))
        V3OperationRecoveryJournal.record = V3OperationRecoveryRecord(sessionID: session, kind: "backup", phase: .prepared)
        precondition(!owner.acceptBackupCallback(successResult))
        V3OperationRecoveryJournal.record = V3OperationRecoveryRecord(sessionID: session, kind: "deactivate", phase: .dispatched)
        precondition(!owner.acceptBackupCallback(successResult))
        V3OperationRecoveryJournal.record = V3OperationRecoveryRecord(sessionID: session, kind: "backup", phase: .dispatched)
        V3OperationRecoveryJournal.readFails = true
        precondition(!owner.acceptBackupCallback(successResult))
        V3OperationRecoveryJournal.readFails = false
        precondition(owner.ownsBackupCallback(successResult) && capture.count == 0)

        // A requested cancellation does not establish external copy completion.
        owner.sessions[session]!.terminal.isCancellationRequested = true
        precondition(owner.ownsBackupCallback(successResult))
        precondition(owner.mutationRegistry.activeID == session && capture.count == 0)
        precondition(owner.acceptBackupCallback(successResult))
        precondition(owner.mutationRegistry.activeID == session)
        precondition(capture.count == 1 && capture.lastNonce == binding.nonce && capture.wasSuccessful)
        precondition(!owner.acceptBackupCallback(successResult) && capture.count == 1)
        precondition(owner.sessions[session]!.backupCallback == nil)

        for (kind, action) in [("backup", "backup"), ("deactivate", "backup"), ("activate", "restore"), ("restore", "restore")] {
            owner.sessions[session] = Owner.Session(kind: kind)
            V3OperationRecoveryJournal.record = V3OperationRecoveryRecord(sessionID: session, kind: kind, phase: .dispatched)
            let next = try owner.beginBackupCallback(id: session, action: action)
            let failed = V3BackupCallbackResult(session: session, payload: ["nonce": next.nonce, "action": action, "result": "failure"])!
            precondition(owner.acceptBackupCallback(failed) && !capture.wasSuccessful)
            precondition(owner.mutationRegistry.activeID == session)
            owner.endBackupCallback(binding) // Old cleanup cannot clear another step.
        }
        owner.sessions[session] = Owner.Session(kind: "delete")
        do { _ = try owner.beginBackupCallback(id: session, action: "backup"); fatalError("unrelated operation admitted") }
        catch V3SideStoreServiceError.invalidRequest {}
        owner.sessions[session] = Owner.Session(kind: "backup")
        let final = try owner.beginBackupCallback(id: session, action: "backup")
        owner.endBackupCallback(binding)
        precondition(owner.sessions[session]!.backupCallback == final)
        owner.endBackupCallback(final)
        precondition(owner.sessions[session]!.backupCallback == nil)
        print("backup callback production harness passed")
    }
}
'''
