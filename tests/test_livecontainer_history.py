"""Execute the shipped Foundation-only history store, not a Python reimplementation."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/templates/livecontainer_refresh_settings.swift"

HARNESS = r'''
final class CountingDefaults: UserDefaults {
    var historyWrites = 0
    private var mutationDepth = 0
    override func set(_ value: Any?, forKey defaultName: String) {
        // Apple Foundation can implement removeObject by calling set(nil).
        // Count the store's outer API mutation once, not Foundation re-entry.
        if mutationDepth == 0 && defaultName == "liveContainerAutoRefreshHistory" { historyWrites += 1 }
        mutationDepth += 1
        defer { mutationDepth -= 1 }
        super.set(value, forKey: defaultName)
    }
    override func removeObject(forKey defaultName: String) {
        if mutationDepth == 0 && defaultName == "liveContainerAutoRefreshHistory" { historyWrites += 1 }
        mutationDepth += 1
        defer { mutationDepth -= 1 }
        super.removeObject(forKey: defaultName)
    }
}

@main
struct HistoryTests {
    @MainActor
    static func main() {
        let suite = "LiveContainerHistoryTests." + UUID().uuidString
        let defaults = CountingDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        typealias Store = LiveContainerRefreshHistoryStore
        let key = Store.key
        func row(_ id: String, _ result: String = "failure") -> [String: String] {
            ["id": id, "date": "2026-09-07T12:00:00Z", "source": "manual", "result": result, "detail": "test"]
        }
        func seed(_ values: [[String: String]]) {
            defaults.set(values, forKey: key)
            defaults.historyWrites = 0
        }
        func ids() -> [String] { Store.entries(in: defaults).map(\.id) }
        // This is the real scheduler's legacy append shape. It deliberately has
        // no UI ID yet; normalization must not confuse equal-looking events.
        func appendSchedulerEvent(_ detail: String) {
            var current = defaults.array(forKey: key) as? [[String: String]] ?? []
            current.insert(["date": "2026-09-07T12:00:00Z", "source": "background", "result": "failure", "detail": detail], at: 0)
            defaults.set(Array(current.prefix(50)), forKey: key)
        }
        let scenario = CommandLine.arguments[1]
        switch scenario {
        case "empty_noop":
            precondition(Store.entries(in: defaults).isEmpty)
            Store.delete(ids: [], in: defaults)
            Store.delete(ids: ["missing"], in: defaults)
            Store.clear(in: defaults)
            precondition(defaults.historyWrites == 0)
        case "migration":
            let legacy = ["date": "same", "source": "manual", "result": "failure", "extra": "preserved"]
            seed([legacy, legacy])
            let first = Store.entries(in: defaults)
            precondition(first.count == 2 && first[0].id != first[1].id)
            precondition(first.allSatisfy { $0.values["extra"] == "preserved" })
            precondition(defaults.historyWrites == 1)
            precondition(Store.entries(in: defaults) == first)
            let reopened = UserDefaults(suiteName: suite)!
            precondition(Store.entries(in: reopened) == first)
            precondition(defaults.historyWrites == 1)
        case "duplicate_ids":
            seed([row("a"), row("a"), row(""), ["result": "failure"]])
            let first = ids()
            precondition(first.count == 4 && Set(first).count == 4 && first[0] == "a")
            precondition(!first.contains(""))
            Store.delete(ids: [first[1]], in: defaults)
            precondition(ids() == [first[0], first[2], first[3]])
        case "selection_after_insert":
            seed([row("a"), row("b"), row("c")])
            let selected = Set(Store.entries(in: defaults).filter { $0.id != "b" }.map(\.id))
            appendSchedulerEvent("new-event")
            defaults.historyWrites = 0
            Store.delete(ids: selected, in: defaults)
            let current = Store.entries(in: defaults)
            precondition(current.count == 2 && current[0].values["detail"] == "new-event" && current[1].id == "b")
            precondition(defaults.historyWrites == 1)
        case "stale_swipe":
            seed([row("a"), row("b")])
            let swiped = Store.entries(in: defaults)[1].id
            appendSchedulerEvent("new-event")
            Store.delete(ids: [swiped], in: defaults)
            let current = Store.entries(in: defaults)
            precondition(current.count == 2 && current[0].values["detail"] == "new-event" && current[1].id == "a")
            defaults.historyWrites = 0
            Store.delete(ids: [swiped], in: defaults) // Duplicate callback is harmless.
            precondition(defaults.historyWrites == 0)
        case "clear_preserves_refresh":
            let baseline: [String: Any] = [
                "liveContainerAutoRefreshEnabled": true,
                "liveContainerAutoRefreshLastResult": "failure",
                "liveContainerAutoRefreshHealthState": "HOST_REFRESH_FAILED",
                "liveContainerAutoRefreshLastError": "pairing invalid",
                "liveContainerAutoRefreshTargetDeadline": Date(timeIntervalSince1970: 5000),
                "liveContainerAutoRefreshNextRetryAt": Date(timeIntervalSince1970: 4000),
                "liveContainerAutoRefreshHostHandoff": true,
                "liveContainerAutoRefreshVerification": ["run_id": "keep-me"]
            ]
            for (key, value) in baseline { defaults.set(value, forKey: key) }
            seed([row("a"), row("b")])
            Store.delete(ids: ["b"], in: defaults)
            Store.clear(in: defaults)
            precondition(defaults.object(forKey: key) == nil)
            for (key, expected) in baseline {
                precondition(NSDictionary(dictionary: [key: defaults.object(forKey: key)!]).isEqual(to: [key: expected]))
            }
            precondition(defaults.historyWrites == 2, "Expected two outer history mutations, got \(defaults.historyWrites)")
            Store.clear(in: defaults)
            precondition(defaults.historyWrites == 2, "Expected two outer history mutations, got \(defaults.historyWrites)")
            appendSchedulerEvent("after-clear")
            precondition(Store.entries(in: defaults).first?.values["detail"] == "after-clear")
        case "retention_and_reorder":
            seed((0..<50).map { row(String($0)) })
            precondition(Store.entries(in: defaults).count == 50)
            let selected = Set(["49", "10"])
            appendSchedulerEvent("newest") // Scheduler evicts 49, not 10.
            let remainingSelection = selected.intersection(Set(ids()))
            precondition(remainingSelection == ["10"])
            Store.delete(ids: selected, in: defaults)
            precondition(ids().count == 49 && !ids().contains("10") && ids().contains("48"))
            seed([row("c"), row("a"), row("b")])
            Store.delete(ids: ["a"], in: defaults)
            precondition(ids() == ["c", "b"])
        case "read_budget":
            seed((0..<50).map { row(String($0)) })
            for _ in 0..<100 { precondition(Store.entries(in: defaults).count == 50) }
            Store.delete(ids: [], in: defaults)
            Store.delete(ids: ["absent"], in: defaults)
            precondition(defaults.historyWrites == 0)
        default:
            fatalError("Unknown test scenario: \(scenario)")
        }
        print("HISTORY_TEST_PASSED=\(scenario)")
    }
}
'''


class HistorySourceTests(unittest.TestCase):
    def test_ui_uses_stable_ids_and_latest_store(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("ForEach(history)", text)
        self.assertIn("selectedHistoryIDs: Set<String>", text)
        self.assertIn("selectedHistoryIDs.formIntersection", text)
        self.assertNotIn("selectedHistoryIndexes", text)
        self.assertNotIn("history.prefix(20)", text)
        self.assertNotIn("history.remove(at:", text)
        self.assertNotIn("defaults.set(history", text)
        self.assertIn(".buttonStyle(.borderless)", text)
        self.assertIn(".disabled(selectedHistoryIDs.isEmpty)", text)
        self.assertIn(".confirmationDialog", text)
        self.assertIn("layoutDirection == .rightToLeft ? .trailing : .leading", text)
        self.assertIn("allowsFullSwipe: false", text)
        self.assertIn(".accessibilityAction(named: Text(\"Delete\"))", text)

    def test_shipped_view_parses(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable; native UI parse not verified")
        result = subprocess.run([compiler, "-frontend", "-parse", str(TEMPLATE)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


class HistoryExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("swiftc")
        if not compiler:
            raise unittest.SkipTest("swiftc unavailable; real history store not executed")
        cls.temp = tempfile.TemporaryDirectory(prefix="lc-history-tests-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = TEMPLATE.read_text(encoding="utf-8")
        start = source.index("// LC_REFRESH_HISTORY_STORE_V1_BEGIN")
        end = source.index("// LC_REFRESH_HISTORY_STORE_V1_END")
        file = Path(cls.temp.name) / "HistoryTests.swift"
        file.write_text("import Foundation\n" + source[start:end] + HARNESS, encoding="utf-8")
        cls.executable = Path(cls.temp.name) / "history-test"
        result = subprocess.run([compiler, "-swift-version", "5", "-parse-as-library", str(file), "-o", str(cls.executable)], capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise AssertionError(result.stderr)

    def check_scenario(self, scenario):
        result = subprocess.run([str(self.executable), scenario], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HISTORY_TEST_PASSED=" + scenario, result.stdout)

    def test_empty_and_missing_actions_are_noop(self):
        self.check_scenario("empty_noop")

    def test_legacy_identity_migration_survives_reopen(self):
        self.check_scenario("migration")

    def test_duplicate_ids_remain_individually_deletable(self):
        self.check_scenario("duplicate_ids")

    def test_selection_remains_correct_after_scheduler_insert(self):
        self.check_scenario("selection_after_insert")

    def test_stale_swipe_preserves_new_events(self):
        self.check_scenario("stale_swipe")

    def test_deletion_preserves_refresh_and_signing_state(self):
        self.check_scenario("clear_preserves_refresh")

    def test_retention_and_reordering_cannot_retarget_selection(self):
        self.check_scenario("retention_and_reorder")

    def test_repeated_reads_do_not_write(self):
        self.check_scenario("read_budget")


if __name__ == "__main__":
    unittest.main()
