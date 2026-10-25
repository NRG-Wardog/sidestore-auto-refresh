"""Read the same effective signing preferences as the retained upstream pipeline."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
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


if __name__ == "__main__":
    unittest.main()
