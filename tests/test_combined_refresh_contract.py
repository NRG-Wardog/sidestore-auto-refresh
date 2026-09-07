from pathlib import Path
import importlib.util
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("combined_contract", ROOT / "scripts/patch_combined_refresh_contract.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


class CombinedRefreshContractTests(unittest.TestCase):
    def test_handoff_uses_host_run_and_manifest_counts_expected_apps(self):
        source = r'''
    private func automaticRefreshDefaults() -> UserDefaults { .standard }
    private func persistAutomaticHostHandoff() {
        defaults.set(refreshIdentifier, forKey: "liveContainerAutoRefreshHostHandoffRunID")
        debugLog("[AUTO_REFRESH] HOST_REFRESH_HANDOFF_STARTED run_id=\\(refreshIdentifier)")
    }
    private func persistAutomaticRefreshVerification() {
        defaults.set(["version": 1, "date": Date(), "results": []], forKey: "manifest")
    }
    private func startListeningForRunningApps() {}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "SideStore/Core/Operations/StandaloneOperations/BackgroundRefreshAppsOperation.swift"
            file.parent.mkdir(parents=True)
            file.write_text(source)
            patch.patch(root)
            result = file.read_text()
            self.assertIn('"expected_ids": installedApps.map', result)
            self.assertIn('"version": 2', result)
            self.assertIn('"schema": "LiveContainerRefreshManifestV2"', result)
            self.assertIn('defaults.string(forKey: "liveContainerAutoRefreshExpectedRunID") ?? refreshIdentifier', result)
            self.assertNotIn(r"\\(refreshIdentifier)", result)
            patch.patch(root)
            self.assertEqual(result, file.read_text())


if __name__ == "__main__":
    unittest.main()
