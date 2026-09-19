"""Exercise pinned patch transactions and the actual shipped wire decoder."""
import importlib.util
import os
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


class ServicePatchTests(unittest.TestCase):
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
            ["LiveContainer/LCBootstrap.m", "ShareExtension/ShareExtensionViewModel.swift", "LaunchAppExtension/LaunchAppExtension.swift"],
            ["AltStore/AppDelegate.swift", "AltStore/SceneDelegate.swift", "SideStore/Core/Operations/PipelineExecutor.swift"])
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

    def test_pinned_patch_replay_and_tamper(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            roots = self.fixture(directory)
            self.apply(roots)
            first = self.snapshot(directory)
            self.apply(roots)
            self.assertEqual(first, self.snapshot(directory))
            path = roots[0] / "SideStoreSupport/XPCClient.m"
            path.write_text(path.read_text() + "\n// unexpected drift\n")
            with self.assertRaises(SystemExit):
                self.apply(roots)
            self.assertNotIn("LCUtils.openSideStore", (roots[0] / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift").read_text())
            self.assertIn(".downloadAlert", (roots[0] / "LiveContainerSwiftUI/Views/LCTabView.swift").read_text())
            self.assertNotIn("LCUtils.openSideStore", (roots[0] / "LiveContainerSwiftUI/Views/Settings/LCMultiLCManagementView.swift").read_text(encoding="utf-8"))
            jit = (roots[0] / "LiveContainerSwiftUI/Utilities/LCUtilsExtensions.swift").read_text(encoding="utf-8")
            self.assertNotIn('sidestore://enable-jit', jit)
            self.assertIn('V3ServiceBridge.shared.request(operation: "jit"', jit)
            scene = (roots[0] / "MultitaskSupport/AppSceneViewController.m").read_text(encoding="utf-8")
            self.assertEqual(scene.count("UIKitFixesInit();"), 1)
            self.assertEqual(scene.count("V3InitializeUIKitFixes();"), 2)
            self.assertIn("dispatch_once(&onceToken, ^{ UIKitFixesInit(); });", scene)
            self.assertIn("!isLiveProcess && sideStoreExist", (roots[0] / "LiveContainer/LCBootstrap.m").read_text(encoding="utf-8"))
            for name in ("ShareExtension/ShareExtensionViewModel.swift", "LaunchAppExtension/LaunchAppExtension.swift"):
                self.assertNotIn('set("builtinSideStore", forKey: "LCLaunchExtensionBundleID")', (roots[0] / name).read_text(encoding="utf-8"))

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
        for token in ("CoreData", "Keychain", "NSManagedObject", "signingCertificatePassword", "appleIDXcodeToken"):
            self.assertNotIn(token, host + bridge)
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

    def test_headless_operation_inventory(self):
        contract = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        for removed in ("panel", "signIn", "install", "refreshApp", "addSource",
                        "removeSource", "importPairing", "update", "activate",
                        "deactivate", "remove", "delete", "backup", "restore",
                        "installURL", "installSharedIPA", "setSetting"):
            self.assertNotIn(f'"{removed}"', contract)
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


class WireExecutionTests(unittest.TestCase):
    def test_shipped_native_callback_settles_once(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable")
        source = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text()
        gate = source[source.index("final class V3ServiceCallbackGate:"):
                      source.index("// V3_NATIVE_CALLBACK_GATE_END")]
        callback = source[source.index("    private func callback("):
                          source.index("    private func snapshot()")]
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
var setting = valid; setting["operation"] = "settingsSet"; setting["target"] = "isBetaUpdatesEnabled"
setting["payload"] = ["key": "isBetaUpdatesEnabled", "type": "bool", "bool": true]
precondition(V3WireContract.decodeRequest(encode(setting), now: now) != nil)
setting["payload"] = ["key": "isBetaUpdatesEnabled", "type": "bool", "bool": 1]
precondition(V3WireContract.decodeRequest(encode(setting), now: now) != nil)
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
for operation in ["authBegin", "authPoll", "authRespond", "opStart", "opPoll", "opAnswer",
                  "certList", "certRevoke", "devTeams", "sourcePreview", "sourceAddConfirmed",
                  "pairingImportData", "settingsGet", "settingsSet", "anisetteList",
                  "sidesignGet", "logTail", "healthSnapshot", "accountExport", "accountImport"] {
    var request = base(operation)
    request["payload"] = ["kind": "install", "answer": ["choice": "proceed"]]
    precondition(V3WireContract.decodeRequest(encode(request), now: now) != nil, operation)
    precondition(V3WireContract.readOperations.contains(operation) == ["authPoll", "opPoll", "certList", "devTeams", "sourcePreview", "settingsGet", "anisetteList", "sidesignGet", "logTail", "healthSnapshot"].contains(operation), operation)
}
for removed in ["panel", "signIn", "install", "refreshApp", "addSource", "removeSource", "importPairing", "setSetting", "update", "activate", "deactivate", "remove", "delete", "backup", "restore", "installURL", "installSharedIPA"] {
    precondition(V3WireContract.decodeRequest(encode(base(removed)), now: now) == nil, removed)
}
var badPayload = base("opStart")
badPayload["payload"] = "not-a-dict"
precondition(V3WireContract.decodeRequest(encode(badPayload), now: now) == nil)
var legacyValue = base("snapshot")
legacyValue["value"] = true
precondition(V3WireContract.decodeRequest(encode(legacyValue), now: now) == nil)
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
        gate = source[source.index("final class V3PromptCenter {"):]
        gate = gate[:gate.index("\n}\n") + len("\n}\n")]
        program = "import Foundation\n" + gate + r'''
@main struct PromptGateTests {
    static func main() async throws {
        let center = V3PromptCenter()
        let first = Task { try await center.park(promptID: "p1") }
        try await Task.sleep(nanoseconds: 20_000_000)
        precondition(center.answer(promptID: "p1", answer: ["choice": "proceed"]))
        precondition(!center.answer(promptID: "p1", answer: ["choice": "proceed"]))
        precondition(!center.answer(promptID: "missing", answer: [:]))
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
            found = [m.group(2) for m in re.finditer(r"func\s+(\w+)\s*\(", before)][-1]
            enclosing.append(found)
        self.assertEqual(enclosing, ["sendAuthenticationRequest", "makeTwoFactorAuthRequest"])
        self.assertEqual(len(re.findall(r"URLRequest\(", text)), 2)

    def test_no_builder_patch_modifies_sidesign_auth(self):
        scripts = (ROOT / "scripts").glob("*.py")
        for script in scripts:
            content = script.read_text(encoding="utf-8")
            self.assertNotIn("DeveloperPortal/Authentication", content)
        medic = (ROOT / "scripts/combined_build_evidence.py").read_text(encoding="utf-8")
        self.assertNotIn("Dependencies/SideSign", medic)

    def test_auth_is_single_flight_without_retry_loops(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text(encoding="utf-8")
        self.assertIn("if let current = activeID { cancel(id: current) }", runtime)
        auth = runtime.split("final class V3OperationCenter")[0]
        self.assertNotIn("while ", auth)
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
