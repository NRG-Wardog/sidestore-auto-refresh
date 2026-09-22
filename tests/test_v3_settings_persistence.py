"""Regression coverage for v3.0.3 settings persistence (issue #34).

LiveContainer preferences must live in the shared app-group store so they
survive backgrounding, termination, and relaunch. Guest preferences are
redirected by the upstream NSUserDefaults hook; repo patches must not move
them into a shared container.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SettingsPersistenceTests(unittest.TestCase):
    def test_dock_preference_uses_app_group_store(self):
        source = (ROOT / "scripts/patch_multitask_dock.py").read_text(encoding="utf-8")
        self.assertIn("LCUtils.appGroupUserDefault", source)
        self.assertIn("LCMultitaskDockStartsCollapsed", source)
        # The persisted default is applied with a readback, not assumed.
        self.assertIn('bool(forKey:', source)

    def test_dock_preference_applied_once(self):
        source = (ROOT / "scripts/patch_multitask_dock.py").read_text(encoding="utf-8")
        # Applied once at creation; layout/rotation must never reset it.
        self.assertIn("MULTITASK_DOCK_START_COLLAPSED_V1", source)
        self.assertIn("override init()", source)
        self.assertNotIn("layoutSubviews", source)
        self.assertNotIn("viewWillAppear", source)

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

    def test_settings_roundtrip_contract(self):
        # Documents the device acceptance contract: write -> relaunch -> read.
        # LC settings keys covered by this line:
        for key in ("LCMultitaskDockStartsCollapsed", "LCHideCollapsedDock",
                    "LCGridSize.storageKey", '"LCShowAppLabels"'):
            found = key in (ROOT / "scripts/patch_multitask_dock.py").read_text(encoding="utf-8") \
                or key in (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
            self.assertTrue(found, key)


if __name__ == "__main__":
    unittest.main()
