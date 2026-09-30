"""Regression tests for LiveContainer embedded SideStore startup ordering."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import re
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from patch_embedded_sidestore_startup import MARKER, patch
WORKFLOW = (ROOT / ".github/workflows/livecontainer-build.yml").read_text(encoding="utf-8")
LIVE_CONTAINER_REF = re.search(r"(?m)^  LIVE_CONTAINER_REF: ([0-9a-f]{40})$", WORKFLOW)[1]
EMBEDDED_SIDESTORE_REF = re.search(r"(?m)^  EMBEDDED_SIDESTORE_REF: ([0-9a-f]{40})$", WORKFLOW)[1]


def checkout_revision(root: Path) -> Optional[str]:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def upstream_roots():
    explicit_live = os.getenv("LIVE_CONTAINER_TEST_SOURCE")
    explicit_side = os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE")
    live = Path(explicit_live or ROOT / ".audit/upstream/LiveContainer")
    side = Path(explicit_side or ROOT / ".audit/upstream/SideStore")
    if not (live / "LiveContainer" / "LCBootstrap.m").is_file():
        raise unittest.SkipTest("Pinned LiveContainer source unavailable")
    if not (side / "AltStore" / "Core" / "Model" / "DatabaseManager" / "DatabaseManager.swift").is_file():
        raise unittest.SkipTest("Pinned embedded SideStore source unavailable")
    for root, expected, explicit, name in (
        (live, LIVE_CONTAINER_REF, explicit_live, "LiveContainer"),
        (side, EMBEDDED_SIDESTORE_REF, explicit_side, "embedded SideStore"),
    ):
        actual = checkout_revision(root)
        if actual != expected:
            message = f"{name} test source is {actual or 'not a Git checkout'}, expected pinned {expected}"
            if explicit:
                raise AssertionError(message)
            raise unittest.SkipTest(message)
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
            (side / "SideStore/Core/Auth/AuthManager.swift", self.side / "SideStore/Core/Auth/AuthManager.swift"),
            (live / "SideStoreSupport" / "SideStoreHooks.m", self.live / "SideStoreSupport" / "SideStoreHooks.m"),
            (live / "SideStoreSupport" / "XPCClient.m", self.live / "SideStoreSupport" / "XPCClient.m"),
            (live / "LiveContainer" / "LCBootstrap.m", self.live / "LiveContainer" / "LCBootstrap.m"),
            (side / "AltStore" / "Core" / "Model" / "DatabaseManager" / "DatabaseManager.swift",
             self.side / "AltStore" / "Core" / "Model" / "DatabaseManager" / "DatabaseManager.swift"),
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            source_root = side if side in source.parents else live
            target.write_bytes(subprocess.check_output([
                "git", "-C", str(source_root), "show", "HEAD:" + source.relative_to(source_root).as_posix()]))
        self.original_auth = self.text(self.side / "SideStore/Core/Auth/AuthManager.swift")
        self.original_xpc_client = self.text(self.live / "SideStoreSupport" / "XPCClient.m")

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
        self.assertIn("REMOVE_NON_LIVE_SIDESTORE_UI_V1", hooks)
        for removed_ui_symbol in (
            "SideStoreMyAppsViewController_orig_viewDidload",
            "SideStoreMyAppsViewController_hook_viewDidload",
            "SideStoreMyAppsViewController_hook_escapeButtonTapped",
            "SSVersionWindows",
            "SSSceneObserver",
            "SSInstallVersionWindow",
            "PassthroughWindow",
            "UISceneDidActivateNotification",
            "escapeButtonTapped:",
        ):
            self.assertNotIn(removed_ui_symbol, hooks)

        # These hooks feed SideStore identity/source behavior and the LiveProcess
        # refresh + notification bridge; the UI cleanup must leave them intact.
        for retained_backend_symbol in (
            "hook_appbundleIdentifier",
            "hook_storeAppBundleIdentifier",
            "hook_altstoreAppGroup",
            "hook_activeBundle",
            "hook_baseAltStoreAppGroupID",
            "SideStoreSource_hook_altStoreSourceURL",
            "installSideStoreNotificationHooks",
            "lc_addNotificationRequest:",
            "lc_removePendingNotificationRequestsWithIdentifiers:",
            "lc_getNotificationSettingsWithCompletionHandler:",
            "LiveProcessSideStoreHandler",
        ):
            self.assertIn(retained_backend_symbol, hooks)
        xpc_client = self.text(self.live / "SideStoreSupport" / "XPCClient.m")
        self.assertEqual(xpc_client, self.original_xpc_client)
        self.assertIn("installSideStoreHooks();", xpc_client)
        self.assertIn("if(!NSUserDefaults.isLiveProcess) return;", xpc_client)
        self.assertIn("installSideStoreNotificationHooks();", xpc_client)
        self.assertLess(xpc_client.index("installSideStoreHooks();"),
                        xpc_client.index("if(!NSUserDefaults.isLiveProcess) return;"))
        self.assertLess(xpc_client.index("if(!NSUserDefaults.isLiveProcess) return;"),
                        xpc_client.index("installSideStoreNotificationHooks();"))
        auth = self.text(self.side / "SideStore/Core/Auth/AuthManager.swift")
        self.assertEqual(auth, self.original_auth, "Never patch upstream authentication behavior")
        self.assertIn('coalesce(key: "apple_auth_session")', auth)
        self.assertNotIn('debugLog("[SAVED]', auth)
        for line in auth.splitlines():
            if 'debugLog(' in line and 'readback_matches=' in line:
                self.assertNotIn('newValue', line)

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
        self.assertIn("private func performStart() async throws", database)
        self.assertIn("try await self.migrateDatabaseToAppGroupIfNeeded()", database)
        self.assertIn("try await self.prepareDatabase()", database)
        self.assertNotIn("guard let localAppBundle = ALTApplication(fileURL: Bundle.Info.activeBundleURL) else { return }", database)

    def temp_paths(self):
        return sorted(path for path in self.temp_paths_root().rglob("*") if path.is_file())

    def temp_paths_root(self):
        return Path(self.temp.name)


if __name__ == "__main__":
    unittest.main()
