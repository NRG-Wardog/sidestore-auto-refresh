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


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from owner_source import owner_source
MARKER = "EMBEDDED_SIDESTORE_STARTUP_FIX_V1"


def verify_database_retry(source):
    body = source.split('private func performStart() async throws', 1)[1].split('public func purgeLoggedErrors', 1)[0]
    guarded = 'if self.persistentContainer.persistentStoreCoordinator.persistentStores.isEmpty {\n            try await self.persistentContainer.loadPersistentStores()\n        } else {'
    if guarded not in body or body.count('try await self.persistentContainer.loadPersistentStores()') != 1:
        raise ValueError('retry must not reload an already attached persistent store')
    if 'try await self.prepareDatabase()' not in body:
        raise ValueError('retry must still prepare the database')


class EmbeddedSideStoreStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.live = Path(self.temp.name)/"LiveContainer"
        self.side = Path(self.temp.name)/"SideStore"
        for owner, root, paths in [
            ("LiveContainer", self.live, ["SideStoreSupport/SideStoreHooks.m", "SideStoreSupport/XPCClient.m", "LiveContainer/LCBootstrap.m"]),
            ("SideStore", self.side, ["SideStore/Core/Auth/AuthManager.swift", "AltStore/Core/Model/DatabaseManager/DatabaseManager.swift"])]:
            for path in paths:
                target = root/path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(owner_source(path, owner))

    def text(self, path):
        return path.read_text()

    def test_current_hooks_install_after_side_runtime_loads(self):
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
        self.assertIn("installSideStoreHooks();", xpc_client)
        self.assertIn("if(!NSUserDefaults.isLiveProcess) return;", xpc_client)
        self.assertIn("installSideStoreNotificationHooks();", xpc_client)
        self.assertLess(xpc_client.index("installSideStoreHooks();"),
                        xpc_client.index("if(!NSUserDefaults.isLiveProcess) return;"))
        self.assertLess(xpc_client.index("if(!NSUserDefaults.isLiveProcess) return;"),
                        xpc_client.index("installSideStoreNotificationHooks();"))
        auth = self.text(self.side / "SideStore/Core/Auth/AuthManager.swift")
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
        database = self.text(self.side / "AltStore" / "Core" / "Model" / "DatabaseManager" / "DatabaseManager.swift")
        self.assertIn(MARKER, database)
        self.assertIn("persistentStores.isEmpty", database)
        self.assertIn("reusing_attached_persistent_store_after_startup_failure", database)
        self.assertIn("Unable to read the active LiveContainer application bundle", database)
        self.assertIn("The active LiveContainer bundle has no readable provisioning profile", database)
        self.assertIn("private func performStart() async throws", database)
        self.assertIn("try MaintenanceManager.shared.preparePersistentStoreURLs(in: self.persistentContainer)", database)
        self.assertIn("try await self.prepareDatabase()", database)
        self.assertNotIn("guard let localAppBundle = ALTApplication(fileURL: Bundle.Info.activeBundleURL) else { return }", database)


    def test_retry_owner_negative_controls(self):
        database = self.text(self.side/"AltStore/Core/Model/DatabaseManager/DatabaseManager.swift")
        verify_database_retry(database)
        for broken in [database.replace('persistentStores.isEmpty', 'persistentStores.count > 0'),
                       database.replace('try await self.prepareDatabase()', '// removed preparation')]:
            with self.assertRaises(ValueError):
                verify_database_retry(broken)


if __name__ == "__main__":
    unittest.main()
