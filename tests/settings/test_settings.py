"""Settings storage, upstream defaults, transport caches and async ownership.

These regressions exercise the retained patch templates. Swift and pinned-source
prerequisites remain explicit; source assertions do not claim native execution.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "scripts/templates/v3_headless_runtime.swift"

def declaration(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for end in range(opening, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise AssertionError(signature)

class SettingsPersistenceTests(unittest.TestCase):

    def test_v3_templates_use_app_group_store(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        for key in ("LCGridSize.storageKey", '"LCShowAppLabels"'):
            self.assertIn(key, shell)
        self.assertIn("store: LCUtils.appGroupUserDefault", shell)

    def test_no_patch_moves_guest_preferences(self):
        # Guest NSUserDefaults redirection belongs to upstream Tweaks.
        # No builder patch may retarget the guest preference container.
        import glob
        for path in glob.glob(str(ROOT / "scripts/patch_*.py")):
            text = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("NSUserDefaults.m", text)
            self.assertNotIn("LCSelectedLanguage", text)

    def test_no_cross_container_key_sharing(self):
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        # Host UI keys and guest container paths must stay separate.
        self.assertNotIn("Library/Preferences", shell)

    def test_guest_return_uses_shared_app_group_defaults(self):
        guest = (ROOT / "scripts/patch_guest_return.py").read_text(encoding="utf-8")
        # Host SwiftUI and LiveProcess Objective-C must use the same suite.
        self.assertIn("UserDefaults.lcShared()", guest)
        self.assertIn("NSUserDefaults.lcSharedDefaults", guest)
        self.assertNotIn("NSUserDefaults.lcUserDefaults", guest)
        self.assertIn("LCGuestReturnStartsCollapsed", guest)

    def test_no_standard_userdefaults_for_lc_settings(self):
        # LC settings must use LCUtils.appGroupUserDefault, not standard UserDefaults
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        for key in ("LCGridSize", "LCShowAppLabels", "LCHideCollapsedDock"):
            # Ensure they don't use UserDefaults.standard
            self.assertNotIn(f"UserDefaults.standard.{key}", shell)
            self.assertNotIn(f'UserDefaults.standard.string(forKey: "{key}")', shell)
            self.assertNotIn(f'UserDefaults.standard.bool(forKey: "{key}")', shell)

class EffectiveDefaultsTests(unittest.TestCase):
    def test_bridge_uses_upstream_computed_signing_preferences(self):
        getter = declaration(RUNTIME.read_text(), "    static func settingsGet()")
        for key in ("customizeAppExtensions", "autoFixAppGroupIDs"):
            self.assertIn(f'bools["{key}"] = UserDefaults.standard.{key}', getter)

    def test_generated_bridge_matches_unset_free_paid_and_explicit_preferences(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; effective-defaults harness runs in macOS CI")
        source_root = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.environ.get("SIDESTORE_TEST_SOURCE")
        if not source_root:
            self.skipTest("Pinned SideStore source unavailable")
        upstream = subprocess.check_output([
            "git", "-C", source_root, "show",
            "ff25922e5c13ccfafd83bda5092910d848ebd409:AltStore/Core/Extensions/UserDefaults+AltStore.swift"
        ], text=True)
        properties = "\n".join(declaration(upstream, signature) for signature in (
            "    var customizeAppExtensions: Bool", "    @objc(customizeAppExtensions) private var _customizeAppExtensions: Bool",
            "    var autoFixAppGroupIDs: Bool", "    @objc(autoFixAppGroupIDs) private var _autoFixAppGroupIDs: Bool"))
        runtime = RUNTIME.read_text()
        start = runtime.index("    static let boolSettings:")
        end = runtime.index("    static func settingsGet()", start)
        projection = runtime[start:end] + declaration(runtime, "    static func settingsGet()")
        harness = '''import Foundation
enum TeamType { case free, individual }
struct Team { let type: TeamType }
final class DatabaseManager {
    static let shared = DatabaseManager()
    var team: Team?
    func activeTeam() -> Team? { team }
}
final class WidgetDataManager {
    static let shared = WidgetDataManager()
    var isVerboseLoggingEnabled = false
}
extension UserDefaults {
''' + properties + '''
}
enum V3BackendCommands {
''' + projection + '''
}
@main struct Test {
    static func main() {
        let defaults = UserDefaults.standard
        for key in ["customizeAppExtensions", "autoFixAppGroupIDs"] { defaults.removeObject(forKey: key) }
        defer { for key in ["customizeAppExtensions", "autoFixAppGroupIDs"] { defaults.removeObject(forKey: key) } }
        func verify(_ extensions: Bool, _ groups: Bool) {
            let values = V3BackendCommands.settingsGet()["bools"] as! [String: Bool]
            precondition(values["customizeAppExtensions"] == extensions)
            precondition(values["autoFixAppGroupIDs"] == groups)
            precondition(values["customizeAppExtensions"] == defaults.customizeAppExtensions)
            precondition(values["autoFixAppGroupIDs"] == defaults.autoFixAppGroupIDs)
        }
        verify(true, true)
        DatabaseManager.shared.team = Team(type: .free)
        verify(true, true)
        DatabaseManager.shared.team = Team(type: .individual)
        verify(false, true)
        defaults.customizeAppExtensions = true
        defaults.autoFixAppGroupIDs = false
        verify(true, false)
        DatabaseManager.shared.team = Team(type: .free)
        defaults.customizeAppExtensions = false
        verify(false, false)
        print("V3_EFFECTIVE_SETTINGS_DEFAULTS_PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "main.swift").write_text(harness)
            compiled = subprocess.run([compiler, "-parse-as-library", str(path / "main.swift"), "-o", str(path / "probe")], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(path / "probe")], capture_output=True, text=True, timeout=30)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn("V3_EFFECTIVE_SETTINGS_DEFAULTS_PASS", executed.stdout)

class TransportSettingParityTests(unittest.TestCase):
    def test_integer_writes_validate_then_refresh_upstream_caches(self):
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text()
        setter = declaration(runtime, "    static func settingsSet(")
        integer = setter[setter.index("} else if intSettings.contains(key)"):]
        self.assertLess(integer.index("guard value >= 0"), integer.index("UserDefaults.standard.set"))
        self.assertIn('key != "remotePairingPortOverride" || value <= Int(UInt16.max)', integer)
        self.assertLess(integer.index("UserDefaults.standard.set"), integer.index("remotePairingPortCache ="))
        self.assertIn("AppConstants.Minimuxer.remotePairingPort", integer)
        self.assertIn("AppConstants.Minimuxer.defaultTCPProbeTimeoutMs", integer)
        self.assertNotIn("selectedGatewayBackendCache =", setter)
        self.assertNotIn("syncMinimuxerBackendFromUserDefaults()", setter)

    def test_real_settings_adapter_updates_real_upstream_caches(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; transport-settings harness runs in macOS CI")
        source_root = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or os.environ.get("SIDESTORE_TEST_SOURCE")
        if not source_root:
            self.skipTest("Pinned SideStore source unavailable")
        def upstream(path):
            return subprocess.check_output(["git", "-C", source_root, "show",
                "ff25922e5c13ccfafd83bda5092910d848ebd409:" + path], text=True)
        defaults = upstream("AltStore/Core/Extensions/UserDefaults+AltStore.swift")
        properties = "\n".join(declaration(defaults, "    @objc var " + signature) for signature in (
            "remotePairingPortOverride: Int", "deviceProbeTimeoutOverride: Int", "minimuxerGatewayBackend: String"))
        wrapper = upstream("SideStore/Core/DeviceApi/MinimuxerWrapper.swift")
        cache_start = wrapper.index("public var selectedGatewayBackendCache")
        cache_end = wrapper.index("var minimuxer: any MinimuxerFacade")
        caches = wrapper[cache_start:cache_end]
        runtime = (ROOT / "scripts/templates/v3_headless_runtime.swift").read_text()
        arrays = runtime[runtime.index("    static let boolSettings:"):runtime.index("    static func settingsGet()")]
        setter = declaration(runtime, "    static func settingsSet(")
        wire = (ROOT / "scripts/templates/v3_wire_contract.swift").read_text()
        strict = "\n".join(declaration(wire, "    static func " + name) for name in ("strictBool(", "strictInt("))
        text = '''import Foundation
import CoreFoundation
public enum GatewayBackend: String { case idevice, libimobiledevice }
public enum AppConstants {
    public enum Minimuxer { public static let remotePairingPort: UInt16 = 62078; public static let defaultTCPProbeTimeoutMs = 1000 }
}
enum V3SideStoreServiceError: Error { case invalidRequest }
final class WidgetDataManager { static let shared = WidgetDataManager(); var isVerboseLoggingEnabled = false }
extension UserDefaults {
''' + properties + '''
}
''' + caches + '''
enum V3WireContract {
''' + strict + '''
}
enum V3BackendCommands {
''' + arrays + setter + '''
}
@main struct Test {
    static func main() throws {
        let defaults = UserDefaults.standard
        let keys = ["remotePairingPortOverride", "deviceProbeTimeoutOverride", "minimuxerGatewayBackend"]
        for key in keys { defaults.removeObject(forKey: key) }
        defer { for key in keys { defaults.removeObject(forKey: key) } }
        syncMinimuxerBackendFromUserDefaults()
        precondition(remotePairingPortCache == AppConstants.Minimuxer.remotePairingPort)
        defaults.minimuxerGatewayBackend = GatewayBackend.libimobiledevice.rawValue
        precondition(selectedGatewayBackendCache == .idevice)
        try V3BackendCommands.settingsSet(payload: ["key": "remotePairingPortOverride", "int": 65535])
        precondition(selectedGatewayBackendCache == .idevice,
                     "An integer edit must not activate a backend change that requires restart")
        precondition(defaults.remotePairingPortOverride == 65535 && remotePairingPortCache == 65535)
        try V3BackendCommands.settingsSet(payload: ["key": "deviceProbeTimeoutOverride", "int": 2500])
        precondition(defaults.deviceProbeTimeoutOverride == 2500 && deviceProbeTimeoutCache == 2500)
        for bad in [-1, 65536, Int.max] {
            do {
                try V3BackendCommands.settingsSet(payload: ["key": "remotePairingPortOverride", "int": bad])
                fatalError("Invalid port must fail without changing storage or cache")
            } catch V3SideStoreServiceError.invalidRequest {}
            precondition(defaults.remotePairingPortOverride == 65535 && remotePairingPortCache == 65535)
        }
        do {
            try V3BackendCommands.settingsSet(payload: ["key": "deviceProbeTimeoutOverride", "int": -1])
            fatalError("Negative timeout must fail")
        } catch V3SideStoreServiceError.invalidRequest {}
        precondition(deviceProbeTimeoutCache == 2500)
        for key in ["remotePairingPortOverride", "deviceProbeTimeoutOverride"] {
            try V3BackendCommands.settingsSet(payload: ["key": key, "int": 0])
        }
        precondition(remotePairingPortCache == AppConstants.Minimuxer.remotePairingPort)
        precondition(deviceProbeTimeoutCache == AppConstants.Minimuxer.defaultTCPProbeTimeoutMs)
        print("V3_TRANSPORT_SETTINGS_PARITY_PASS")
    }
}
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "main.swift").write_text(text)
            compiled = subprocess.run([compiler, "-parse-as-library", str(path / "main.swift"), "-o", str(path / "probe")], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(path / "probe")], capture_output=True, text=True, timeout=30)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn("V3_TRANSPORT_SETTINGS_PARITY_PASS", executed.stdout)

SETTINGS_LOAD_HARNESS = r'''
import Foundation
import Combine

enum FixtureFailure: Error { case rejected }
enum V3FailureGuidance { static func message(_ error: Error) -> String { "read failed" } }
@MainActor final class V3ServiceBridge {
    static let shared = V3ServiceBridge()
    var reads: [CheckedContinuation<[String: Any], Error>] = []
    var readWaiter: CheckedContinuation<Void, Never>?
    var writes = 0
    var holdWrites = false
    var failNextWrite = false
    var heldWrites: [CheckedContinuation<Void, Never>] = []
    var writeWaiters: [(Int, CheckedContinuation<Void, Never>)] = []
    func request(operation: String, payload: [String: Any] = [:]) async throws -> [String: Any] {
        if operation == "settingsGet" {
            return try await withCheckedThrowingContinuation { continuation in
                reads.append(continuation)
                readWaiter?.resume(); readWaiter = nil
            }
        }
        precondition(operation == "settingsSet")
        writes += 1
        let ready = writeWaiters.filter { $0.0 <= writes }
        writeWaiters.removeAll { $0.0 <= writes }
        for (_, waiter) in ready { waiter.resume() }
        if failNextWrite { failNextWrite = false; throw FixtureFailure.rejected }
        if holdWrites { await withCheckedContinuation { heldWrites.append($0) } }
        return [:]
    }
    func acknowledgeDirectRecoveryAfterSuccess(_ reply: [String: Any], operation: String) async -> Bool { true }
    func waitForRead() async {
        if !reads.isEmpty { return }
        await withCheckedContinuation { readWaiter = $0 }
    }
    func waitForWrites(_ count: Int) async {
        if writes >= count { return }
        await withCheckedContinuation { writeWaiters.append((count, $0)) }
    }
    func completeRead(_ reply: [String: Any]) { reads.removeFirst().resume(returning: reply) }
    func releaseOneWrite() { heldWrites.removeFirst().resume() }
    func releaseWrites() {
        holdWrites = false
        let values = heldWrites; heldWrites = []
        for waiter in values { waiter.resume() }
    }
}

// PRODUCTION

// Test-only observation of actual completion ownership, not a timing sleep.
extension V3SettingsStore {
    func generation(_ key: String) -> UInt64 { writeGenerations.current(for: key) }
    func confirmedBool(_ key: String) -> Bool? { confirmedBools[key] }
    func awaitTicketSettlement(_ ticket: UInt64, key: String) async {
        while writeGenerations.isPending(ticket, for: key) { await Task.yield() }
    }
    func awaitWriteSettlement(_ key: String) async {
        while writeGenerations.isPending(writeGenerations.current(for: key), for: key) {
            await Task.yield()
        }
    }
}

@main struct Test {
    @MainActor static func main() async {
        let bridge = V3ServiceBridge.shared
        let store = V3SettingsStore()
        let initial = Task { await store.load() }
        await bridge.waitForRead()
        bridge.completeRead(["bools": ["flag": false, "unrelated": false],
                             "strings": ["text": "old"], "ints": ["port": 1]])
        await initial.value
        precondition(store.loaded && store.bools["flag"] == false)

        let oldRead = Task { await store.load() }
        await bridge.waitForRead()
        store.setBool("flag", true)
        await bridge.waitForWrites(1)
        await store.setStringAndWait("text", "new")
        store.setInt("port", 2)
        await bridge.waitForWrites(3)
        bridge.completeRead(["bools": ["flag": false, "unrelated": true],
                             "strings": ["text": "old", "server": "fresh"], "ints": ["port": 1]])
        await oldRead.value
        precondition(store.bools["flag"] == true, "Late read must preserve newer boolean write")
        precondition(store.strings["text"] == "new", "Late read must preserve newer string write")
        precondition(store.ints["port"] == 2, "Late read must preserve newer integer write")
        precondition(store.bools["unrelated"] == true && store.strings["server"] == "fresh",
                     "The same read must still refresh untouched keys")

        for key in ["flag", "text", "port"] { await store.awaitWriteSettlement(key) }
        for finishBeforeReadReturns in [true, false] {
            bridge.holdWrites = true
            let expectedWrites = bridge.writes + 3
            store.setBool("flag", true)
            let stringWrite = Task { await store.setStringAndWait("text", "pending-new") }
            store.setInt("port", 42)
            await bridge.waitForWrites(expectedWrites)
            let concurrentRead = Task { await store.load() }
            await bridge.waitForRead()
            if finishBeforeReadReturns {
                bridge.releaseWrites()
                await stringWrite.value
                for key in ["flag", "text", "port"] { await store.awaitWriteSettlement(key) }
            }
            bridge.completeRead(["bools": ["flag": false], "strings": ["text": "stale"], "ints": ["port": 1]])
            await concurrentRead.value
            precondition(store.bools["flag"] == true && store.strings["text"] == "pending-new" && store.ints["port"] == 42,
                         "Read started during writes must preserve them regardless of reply order")
            if !finishBeforeReadReturns {
                bridge.releaseWrites()
                await stringWrite.value
                for key in ["flag", "text", "port"] { await store.awaitWriteSettlement(key) }
            }
            precondition(store.bools["flag"] == true && store.strings["text"] == "pending-new" && store.ints["port"] == 42)
        }

        let freshRead = Task { await store.load() }
        await bridge.waitForRead()
        bridge.completeRead(["bools": ["flag": false], "strings": [:] as [String: String], "ints": ["port": 9]])
        await freshRead.value
        precondition(store.bools["flag"] == false && store.ints["port"] == 9,
                     "A read begun after writes settle remains authoritative")
        precondition(store.strings.isEmpty && store.bools["unrelated"] == nil,
                     "Untouched removed keys must not linger")

        // Backend order may differ from client generations: W2 commits first,
        // W1 commits last and its reply arrives while W2's reply is delayed.
        bridge.holdWrites = true
        let priorWrites = bridge.writes
        store.setBool("flag", false)
        let olderTicket = store.generation("flag")
        await bridge.waitForWrites(priorWrites + 1)
        store.setBool("flag", true)
        await bridge.waitForWrites(priorWrites + 2)
        bridge.releaseOneWrite()
        await store.awaitTicketSettlement(olderTicket, key: "flag")
        bridge.releaseWrites()
        await store.awaitWriteSettlement("flag")
        await bridge.waitForRead()
        bridge.completeRead(["bools": ["flag": false]])
        while store.bools["flag"] != false { await Task.yield() }
        precondition(store.confirmedBool("flag") == false,
                     "Deferred readback must reconcile display and rollback baseline to backend order")

        // An old failed write's readback must not overwrite a newer success
        // message/value while it is suspended waiting for that read.
        bridge.failNextWrite = true
        let failedWrite = Task { await store.setStringAndWait("text", "bad") }
        await bridge.waitForRead()
        await store.setStringAndWait("text", "latest")
        bridge.completeRead(["strings": ["text": "stale"]])
        await failedWrite.value
        precondition(store.strings["text"] == "latest" && store.message.isEmpty)
        // The obsolete failure may have committed before reporting an error;
        // drain the required final readback rather than abandoning its task.
        await bridge.waitForRead()
        bridge.completeRead(["strings": ["text": "latest"]])

        var generations = V3SettingsWriteGeneration()
        let captured = generations
        _ = generations.begin("edited")
        precondition(!generations.isUnchanged(since: captured))
        let confirmed = generations.mergingSnapshot(["edited": "stale", "other": "fresh"],
            into: ["edited": "confirmed"], captured: captured)
        precondition(confirmed == ["edited": "confirmed", "other": "fresh"],
                     "Rollback baselines must preserve newer confirmed writes too")
        print("V3_SETTINGS_LOAD_RACE_PASS")
    }
}
'''

class SettingsLoadRaceTests(unittest.TestCase):
    def test_store_captures_write_generations_before_bulk_read(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text()
        store = declaration(source, "final class V3SettingsStore:")
        load = declaration(store, "    func load() async")
        self.assertLess(load.index("let capturedWrites = writeGenerations"), load.index('request(operation: "settingsGet")'))
        self.assertEqual(load.count("mergingSnapshot("), 6)
        self.assertEqual(load.count("isUnchanged(since: capturedWrites)"), 2)
        self.assertEqual(store.count("defer { finishWrite(generation, key: key, type:"), 3)
        self.assertIn("pendingWriteReconciliation.insert(key)", store)
        self.assertIn("!writeGenerations.hasPendingWrites(for: key)", store)
        policy = declaration((ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text(), "struct V3SettingsWriteGeneration {")
        self.assertIn("pending[key] == nil && captured.pending[key] == nil", policy)

    def test_production_store_preserves_writes_and_merges_untouched_keys(self):
        compiler = shutil.which("swiftc")
        if sys.platform != "darwin" or not compiler:
            self.skipTest("Production settings store harness requires macOS Swift and Combine")
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text()
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text()
        production = declaration(primitives, "struct V3SettingsWriteGeneration {") + "\n@MainActor\n" + declaration(shell, "final class V3SettingsStore:")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "main.swift").write_text(SETTINGS_LOAD_HARNESS.replace("// PRODUCTION", production))
            compiled = subprocess.run([compiler, "-parse-as-library", str(path / "main.swift"), "-o", str(path / "probe")], capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(path / "probe")], capture_output=True, text=True, timeout=30)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn("V3_SETTINGS_LOAD_RACE_PASS", executed.stdout)

BOOL_ROW_HARNESS = r'''
import Foundation

enum ProbeError: Error { case failed }
@MainActor final class ProbeStatus {
    var settings: [String: Bool] = [:]
    var notice: String?
    var errors = 0
    var started: Set<UUID> = []
    var finished: Set<UUID> = []
    func beginDirectMutation() -> UUID {
        let ticket = UUID(); started.insert(ticket); return ticket
    }
    func finishDirectMutation(ticket: UUID, requestReload: Bool) {
        precondition(started.contains(ticket) && !finished.contains(ticket))
        finished.insert(ticket)
    }
    func reload() {}
    func present(_ error: Error) { errors += 1 }
}
@MainActor final class V3ServiceBridge {
    static let shared = V3ServiceBridge()
    var readCount = 0
    var writeCount = 0
    var reads: [Int: CheckedContinuation<[String: Any], Error>] = [:]
    var writes: [Int: CheckedContinuation<[String: Any], Error>] = [:]
    func request(operation: String, payload: [String: Any] = [:]) async throws -> [String: Any] {
        if operation == "settingsGet" {
            readCount += 1
            return try await withCheckedThrowingContinuation { reads[readCount] = $0 }
        }
        precondition(operation == "settingsSet")
        writeCount += 1
        return try await withCheckedThrowingContinuation { writes[writeCount] = $0 }
    }
    func acknowledgeDirectRecoveryAfterSuccess(_ reply: [String: Any], operation: String) async -> Bool { true }
    func waitForRead(_ id: Int) async { while reads[id] == nil { await Task.yield() } }
    func waitForWrite(_ id: Int) async { while writes[id] == nil { await Task.yield() } }
    func read(_ id: Int, value: Bool) { reads.removeValue(forKey: id)!.resume(returning: ["bools": ["flag": value]]) }
    func failRead(_ id: Int) { reads.removeValue(forKey: id)!.resume(throwing: ProbeError.failed) }
    func write(_ id: Int) { writes.removeValue(forKey: id)!.resume(returning: [:]) }
    func failWrite(_ id: Int) { writes.removeValue(forKey: id)!.resume(throwing: ProbeError.failed) }
}

// GENERATIONS

@MainActor final class RowProbe {
    let status = ProbeStatus()
    let key = "flag"
    var value = false
    var loaded = false
    var loadingRequest = false
    var writeGenerations = V3SettingsWriteGeneration()
    var confirmedValue: Bool?
    var pendingWriteReconciliation = false
    func beginLoad() async { await load() }
    @discardableResult func choose(_ next: Bool) -> UInt64 {
        value = next; save(next); return writeGenerations.current(for: key)
    }
    func waitForSettlement(_ generation: UInt64) async {
        while writeGenerations.isPending(generation, for: key) { await Task.yield() }
    }
    func waitForValue(_ expected: Bool) async { while value != expected { await Task.yield() } }

// ROW_METHODS
}

@main struct Test {
    @MainActor static func main() async {
        let bridge = V3ServiceBridge.shared
        let row = RowProbe()
        let initial = Task { await row.beginLoad() }
        await bridge.waitForRead(1); bridge.read(1, value: false); await initial.value
        precondition(row.loaded && !row.value)

        // A read started before a later successful write cannot replace it.
        let old = Task { await row.beginLoad() }
        await bridge.waitForRead(2)
        let first = row.choose(true)
        await bridge.waitForWrite(1); bridge.write(1); await row.waitForSettlement(first)
        bridge.read(2, value: false); await old.value
        precondition(row.value && row.confirmedValue == true)

        // A read started during a pending write is stale in either reply order.
        for writeFinishesFirst in [true, false] {
            let writeID = bridge.writeCount + 1
            let readID = bridge.readCount + 1
            let next = !row.value
            let generation = row.choose(next)
            await bridge.waitForWrite(writeID)
            let overlapping = Task { await row.beginLoad() }
            await bridge.waitForRead(readID)
            if writeFinishesFirst { bridge.write(writeID); await row.waitForSettlement(generation) }
            bridge.read(readID, value: !next); await overlapping.value
            precondition(row.value == next)
            if !writeFinishesFirst { bridge.write(writeID); await row.waitForSettlement(generation) }
            precondition(row.confirmedValue == next)
        }

        // An obsolete failed bulk read does not present an error after a write.
        let errorBaseline = row.status.errors
        let staleErrorID = bridge.readCount + 1
        let staleError = Task { await row.beginLoad() }
        await bridge.waitForRead(staleErrorID)
        let errorWriteID = bridge.writeCount + 1
        let successful = row.choose(true)
        await bridge.waitForWrite(errorWriteID); bridge.write(errorWriteID)
        await row.waitForSettlement(successful)
        bridge.failRead(staleErrorID); await staleError.value
        precondition(row.status.errors == errorBaseline && row.value)

        // Failed write + failed recovery uses the confirmed value, not a stale read.
        let failedID = bridge.writeCount + 1
        let failedGeneration = row.choose(false)
        await bridge.waitForWrite(failedID)
        let staleID = bridge.readCount + 1
        let staleLoad = Task { await row.beginLoad() }
        await bridge.waitForRead(staleID)
        let recoveryID = bridge.readCount + 1
        bridge.failWrite(failedID)
        await bridge.waitForRead(recoveryID); bridge.failRead(recoveryID)
        await row.waitForSettlement(failedGeneration)
        precondition(row.value && row.confirmedValue == true && row.status.errors == errorBaseline + 1)
        bridge.read(staleID, value: false); await staleLoad.value
        precondition(row.value && row.confirmedValue == true)

        // Older success during the current pending write waits for settlement.
        let aID = bridge.writeCount + 1
        let a = row.choose(false); await bridge.waitForWrite(aID)
        let bID = bridge.writeCount + 1
        let b = row.choose(true); await bridge.waitForWrite(bID)
        let reconciliationID = bridge.readCount + 1
        bridge.write(aID); await row.waitForSettlement(a)
        precondition(bridge.readCount < reconciliationID && row.value)
        bridge.write(bID); await row.waitForSettlement(b)
        await bridge.waitForRead(reconciliationID)
        bridge.read(reconciliationID, value: false); await row.waitForValue(false)
        precondition(row.confirmedValue == false)

        // Newest success first: do not start competing reads for two older writes.
        let cID = bridge.writeCount + 1
        let c = row.choose(false); await bridge.waitForWrite(cID)
        let dID = bridge.writeCount + 1
        let d = row.choose(false); await bridge.waitForWrite(dID)
        let eID = bridge.writeCount + 1
        let e = row.choose(true); await bridge.waitForWrite(eID)
        let finalReadID = bridge.readCount + 1
        bridge.write(eID); await row.waitForSettlement(e)
        bridge.write(cID); await row.waitForSettlement(c)
        precondition(bridge.readCount < finalReadID && row.value)
        bridge.write(dID); await row.waitForSettlement(d)
        await bridge.waitForRead(finalReadID)
        precondition(bridge.readCount == finalReadID)
        bridge.read(finalReadID, value: false); await row.waitForValue(false)

        // A failed old write's suspended recovery cannot report over a newer success.
        let oldFailureID = bridge.writeCount + 1
        let oldFailure = row.choose(true); await bridge.waitForWrite(oldFailureID)
        let oldRecoveryID = bridge.readCount + 1
        bridge.failWrite(oldFailureID); await bridge.waitForRead(oldRecoveryID)
        let newID = bridge.writeCount + 1
        let newer = row.choose(false); await bridge.waitForWrite(newID)
        bridge.write(newID); await row.waitForSettlement(newer)
        let errorsBeforeOldRecovery = row.status.errors
        let deferredID = bridge.readCount + 1
        bridge.read(oldRecoveryID, value: true); await row.waitForSettlement(oldFailure)
        await bridge.waitForRead(deferredID)
        precondition(!row.value && row.status.errors == errorsBeforeOldRecovery)
        bridge.read(deferredID, value: true); await row.waitForValue(true)

        // A quiescent read remains authoritative, and every mutation ticket balances.
        let freshID = bridge.readCount + 1
        let fresh = Task { await row.beginLoad() }
        await bridge.waitForRead(freshID); bridge.read(freshID, value: false); await fresh.value
        precondition(!row.value && row.confirmedValue == false)
        precondition(!row.writeGenerations.hasPendingWrites(for: row.key))
        precondition(row.status.started == row.status.finished)
        precondition(row.status.finished.count == bridge.writeCount)
        precondition(bridge.reads.isEmpty && bridge.writes.isEmpty)
        print("V3_BOOL_SETTING_ROW_RACE_PASS")
    }
}
'''

class BoolSettingRowRaceTests(unittest.TestCase):
    def test_row_guards_late_reads_and_balances_write_ownership(self):
        row = declaration((ROOT / "scripts/templates/v3_unified_shell.swift").read_text(), "struct V3BoolSettingRow: View {")
        load = declaration(row, "    private func load() async")
        save = declaration(row, "    private func save(_ newValue: Bool)")
        self.assertLess(load.index("let capturedWrites = writeGenerations"), load.index('request(operation: "settingsGet")'))
        self.assertEqual(load.count("isUnchanged(since: capturedWrites)"), 2)
        self.assertIn("defer { finishWrite(generation) }", save)
        self.assertEqual(save.count("status.finishDirectMutation(ticket: mutationTicket, requestReload: true)"), 2)
        self.assertEqual(save.count("guard writeGenerations.isCurrent(generation, for: key)"), 2)
        finish = declaration(row, "    private func finishWrite(_ generation: UInt64)")
        self.assertIn("writeGenerations.finish(generation, for: key)", finish)
        self.assertIn("!writeGenerations.hasPendingWrites(for: key)", finish)

    def test_production_row_methods_preserve_latest_values_and_reconcile_overlap(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift compiler unavailable; row-method harness runs in macOS CI")
        shell = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text()
        primitives = (ROOT / "scripts/templates/v3_behavioral_primitives.swift").read_text()
        row = declaration(shell, "struct V3BoolSettingRow: View {")
        methods = "\n".join(declaration(row, signature) for signature in (
            "    private func load() async", "    private func save(_ newValue: Bool)",
            "    private func finishWrite(_ generation: UInt64)",
            "    private func reloadAuthoritative(generation: UInt64) async -> Bool"))
        generations = declaration(primitives, "struct V3SettingsWriteGeneration {")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            source = BOOL_ROW_HARNESS.replace("// GENERATIONS", generations).replace("// ROW_METHODS", methods)
            (path / "main.swift").write_text(source)
            compiled = subprocess.run([compiler, "-parse-as-library", str(path / "main.swift"), "-o", str(path / "probe")], capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(path / "probe")], capture_output=True, text=True, timeout=30)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn("V3_BOOL_SETTING_ROW_RACE_PASS", executed.stdout)

if __name__ == "__main__":
    unittest.main()
