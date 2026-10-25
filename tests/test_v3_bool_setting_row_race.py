"""Exercise the production boolean row's async read/write ownership methods."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_v3_settings_effective_defaults import declaration

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
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
            source = HARNESS.replace("// GENERATIONS", generations).replace("// ROW_METHODS", methods)
            (path / "main.swift").write_text(source)
            compiled = subprocess.run([compiler, "-parse-as-library", str(path / "main.swift"), "-o", str(path / "probe")], capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(path / "probe")], capture_output=True, text=True, timeout=30)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn("V3_BOOL_SETTING_ROW_RACE_PASS", executed.stdout)


if __name__ == "__main__": unittest.main()
