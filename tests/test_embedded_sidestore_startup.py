"""Regression tests for LiveContainer embedded SideStore startup ordering."""

from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from patch_embedded_sidestore_startup import MARKER, patch


def upstream_roots():
    live = ROOT / ".audit" / "upstream" / "LiveContainer"
    side = ROOT / ".audit" / "upstream" / "SideStore"
    if not (live / "LiveContainer" / "LCBootstrap.m").is_file():
        raise unittest.SkipTest("Pinned LiveContainer source unavailable")
    if not (side / "AltStore" / "Core" / "Model" / "DatabaseManager" / "DatabaseManager.swift").is_file():
        raise unittest.SkipTest("Pinned embedded SideStore source unavailable")
    return live, side


class EmbeddedSideStoreStartupTests(unittest.TestCase):
    def setUp(self):
        live, side = upstream_roots()
        self.temp = tempfile.TemporaryDirectory(prefix="embedded-sidestore-startup-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.live = root / "LiveContainer"
        self.side = root / "SideStore"
        for source, target in (
            (live / "SideStoreSupport" / "SideStoreHooks.m", self.live / "SideStoreSupport" / "SideStoreHooks.m"),
            (live / "LiveContainer" / "LCBootstrap.m", self.live / "LiveContainer" / "LCBootstrap.m"),
            (side / "AltStore" / "Core" / "Model" / "DatabaseManager" / "DatabaseManager.swift",
             self.side / "AltStore" / "Core" / "Model" / "DatabaseManager" / "DatabaseManager.swift"),
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_patch_is_idempotent_and_installs_after_side_runtime_loads(self):
        before = {path: self.text(path) for path in self.temp_paths()}
        patch(self.live, self.side)
        first = {path: self.text(path) for path in self.temp_paths()}
        patch(self.live, self.side)
        self.assertEqual(first, {path: self.text(path) for path in self.temp_paths()})
        self.assertNotEqual(before, first)

        hooks = self.text(self.live / "SideStoreSupport" / "SideStoreHooks.m")
        self.assertIn(MARKER, hooks)
        self.assertIn("PrivClass(Source) == nil", hooks)
        self.assertIn("hooks_deferred", hooks)
        self.assertIn("static dispatch_once_t onceToken", hooks)

        bootstrap = self.text(self.live / "LiveContainer" / "LCBootstrap.m")
        self.assertIn(MARKER, bootstrap)
        self.assertIn('dlsym(sideStoreSupportHandle, "installSideStoreHooks")', bootstrap)
        self.assertLess(bootstrap.index('dlsym(sideStoreSupportHandle, "installSideStoreHooks")'),
                        bootstrap.index("[NSUserDefaults performSelector:@selector(initialize)]"))
        self.assertLess(bootstrap.index("appHandle ="),
                        bootstrap.index('dlsym(sideStoreSupportHandle, "installSideStoreHooks")'))

    def test_database_retry_reuses_attached_store_and_preserves_cause(self):
        patch(self.live, self.side)
        database = self.text(self.side / "AltStore" / "Core" / "Model" / "DatabaseManager" / "DatabaseManager.swift")
        self.assertIn(MARKER, database)
        self.assertIn("persistentStores.isEmpty", database)
        self.assertIn("reusing_attached_persistent_store_after_startup_failure", database)
        self.assertIn("Unable to read the active LiveContainer application bundle", database)
        self.assertIn("The active LiveContainer bundle has no readable provisioning profile", database)
        self.assertIn("main_bundle=", database)
        self.assertIn("active_bundle=", database)
        self.assertIn("profile_exists=", database)
        self.assertIn("app_group_path=", database)
        self.assertNotIn("guard let localAppBundle = ALTApplication(fileURL: Bundle.Info.activeBundleURL) else { return }", database)

    def temp_paths(self):
        return sorted(path for path in self.temp_paths_root().rglob("*") if path.is_file())

    def temp_paths_root(self):
        return Path(self.temp.name)


if __name__ == "__main__":
    unittest.main()
