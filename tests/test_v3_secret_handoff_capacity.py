"""Cross-process regression coverage for shared Keychain handoff admission."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/templates/v3_secret_handoff.swift"


def extract_type(source: str, declaration: str) -> str:
    start = source.index(declaration)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unterminated production declaration: " + declaration)


class SecretHandoffCapacityContractTests(unittest.TestCase):
    def test_store_cleanup_consume_and_discard_share_one_lock_contract(self):
        source = TEMPLATE.read_text(encoding="utf-8")
        admission = extract_type(source, "enum V3SecretHandoffStoreAdmission {")
        self.assertIn("V3AppGroupProcessLock.withLock", admission)
        self.assertLess(admission.index("V3AppGroupProcessLock.withLock"),
                        admission.index("liveItemCount()"))
        self.assertLess(admission.index("liveItemCount()"), admission.index("insert()"))

        store_start = source.index("    private static func store(_ payload: Data, kind: String) throws -> String {")
        store_end = source.index("\n    private static func consume(", store_start)
        store = source[store_start:store_end]
        self.assertIn("V3SecretHandoffStoreAdmission.add", store)
        self.assertLess(store.index("listedItems(group: group)"), store.index("removeExpiredItems(group: group"))
        self.assertLess(store.index("removeExpiredItems(group: group"), store.index("SecItemAdd("))
        self.assertIn("maximumOutstandingItems = 32", source)
        self.assertIn("maximumPayloadBytes = 64 * 1024", source)
        self.assertIn("payload.count <= V3SecretHandoffRecord.maximumPayloadBytes", store)

        cleanup_start = source.index("    static func cleanupExpiredItems() {")
        cleanup_end = source.index("\n    private static func store(", cleanup_start)
        cleanup = source[cleanup_start:cleanup_end]
        self.assertLess(cleanup.index("V3AppGroupProcessLock.withLock"), cleanup.index("listedItems(group: group)"))
        self.assertLess(cleanup.index("listedItems(group: group)"), cleanup.index("removeExpiredItems(group: group"))

        consume_start = source.index("    private static func consume(_ token: String, kind: String) throws -> Data {")
        consume_end = source.index("    private static func consumeLocked", consume_start)
        self.assertIn("V3AppGroupProcessLock.withLock", source[consume_start:consume_end])
        discard_start = source.index("    static func discard(_ token: String) {")
        discard_end = source.index("    static func cleanupExpiredItems()", discard_start)
        self.assertIn("V3AppGroupProcessLock.withLock", source[discard_start:discard_end])

    def test_concurrent_processes_share_capacity_and_consume_lock(self):
        if sys.platform != "darwin":
            self.skipTest("the production Darwin flock helper is exercised on macOS CI")
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")

        source = TEMPLATE.read_text(encoding="utf-8")
        program = "\n".join((
            "import Foundation",
            "import CoreFoundation",
            "import Darwin",
            "extension Bundle { var altstoreAppGroup: String? { nil } }",
            extract_type(source, "enum V3SecretHandoffError: Error, LocalizedError {"),
            extract_type(source, "enum V3AppGroupProcessLock {"),
            extract_type(source, "enum V3SecretHandoffRecord {"),
            extract_type(source, "enum V3SecretHandoffStoreAdmission {"),
            (ROOT / "tests/fixtures/v3_secret_handoff_capacity_process_harness.swift")
                .read_text(encoding="utf-8"),
        ))
        with tempfile.TemporaryDirectory(prefix="v3-secret-handoff-capacity-") as directory:
            swift = Path(directory) / "CapacityHarness.swift"
            executable = Path(directory) / "capacity-harness"
            swift.write_text(program, encoding="utf-8")
            built = subprocess.run([compiler, "-swift-version", "5", "-parse-as-library",
                                    str(swift), "-o", str(executable)],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(executable)], capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_SECRET_HANDOFF_CROSS_PROCESS_CAPACITY_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
