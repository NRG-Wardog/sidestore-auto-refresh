"""An old bulk settings read cannot overwrite a newer user write."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from test_v3_settings_effective_defaults import declaration

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
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
            (path / "main.swift").write_text(HARNESS.replace("// PRODUCTION", production))
            compiled = subprocess.run([compiler, "-parse-as-library", str(path / "main.swift"), "-o", str(path / "probe")], capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(path / "probe")], capture_output=True, text=True, timeout=30)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn("V3_SETTINGS_LOAD_RACE_PASS", executed.stdout)


if __name__ == "__main__": unittest.main()
