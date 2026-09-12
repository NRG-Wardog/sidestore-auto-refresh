"""Tests for Issue #17: App Layout Architecture.

Verifies:
- Idempotence on LiveContainer and SideStore
- Fail-closed behavior on altered/drifted anchors
- Preference persistence contract (list default, grid, compactList, showAppLabels)
- Accessibility label exposure when visual labels are hidden
- Safety boundaries: only presentation/settings files are modified
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = ROOT / "scripts" / "patch_app_layout.py"


def resolve_lc_source() -> Path | None:
    for candidate in [
        os.getenv("LIVE_CONTAINER_TEST_SOURCE"),
        ROOT / ".audit" / "upstream" / "LiveContainer",
        ROOT.parent / "LiveContainer",
    ]:
        if candidate and Path(candidate).is_dir() and (Path(candidate) / "LiveContainerSwiftUI").is_dir():
            return Path(candidate)
    return None


def resolve_sidestore_source() -> Path | None:
    for candidate in [
        os.getenv("SIDESTORE_TEST_SOURCE"),
        os.getenv("EMBEDDED_SIDESTORE_TEST_SOURCE"),
        ROOT / ".audit" / "upstream" / "SideStore",
        ROOT.parent / "SideStore-source-timepicker",
        ROOT.parent / "EmbeddedSideStore",
    ]:
        if candidate and Path(candidate).is_dir() and (Path(candidate) / "AltStore").is_dir():
            return Path(candidate)
    return None


def copy_source(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        symlinks=True,
        ignore_dangling_symlinks=True,
        ignore=shutil.ignore_patterns(".git", ".build", "build", "*.xcframework", "*.ipa"),
    )


class AppLayoutPatchTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATCH_SCRIPT.is_file(), f"Missing patch script at {PATCH_SCRIPT}")

    def test_livecontainer_patch_idempotence(self):
        lc_source = resolve_lc_source()
        if not lc_source:
            self.skipTest("LiveContainer test source not available")

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "LiveContainer"
            copy_source(lc_source, target)

            # First application
            proc1 = subprocess.run(
                [sys.executable, str(PATCH_SCRIPT), str(target)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc1.returncode, 0, f"First pass failed: {proc1.stderr}")
            first_state = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}

            # Second application (idempotence)
            proc2 = subprocess.run(
                [sys.executable, str(PATCH_SCRIPT), str(target)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc2.returncode, 0, f"Second pass failed: {proc2.stderr}")
            second_state = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}

            self.assertEqual(first_state, second_state, "LiveContainer patch is not idempotent")

    def test_sidestore_patch_idempotence(self):
        sidestore_source = resolve_sidestore_source()
        if not sidestore_source:
            self.skipTest("SideStore test source not available")

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "SideStore"
            copy_source(sidestore_source, target)

            # First application
            proc1 = subprocess.run(
                [sys.executable, str(PATCH_SCRIPT), str(target)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc1.returncode, 0, f"First pass failed: {proc1.stderr}")
            first_state = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}

            # Second application (idempotence)
            proc2 = subprocess.run(
                [sys.executable, str(PATCH_SCRIPT), str(target)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc2.returncode, 0, f"Second pass failed: {proc2.stderr}")
            second_state = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}

            self.assertEqual(first_state, second_state, "SideStore patch is not idempotent")

    def test_livecontainer_fail_closed_on_drifted_anchor(self):
        lc_source = resolve_lc_source()
        if not lc_source:
            self.skipTest("LiveContainer source unavailable")

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "LiveContainer"
            copy_source(lc_source, target)

            settings_path = target / "LiveContainerSwiftUI" / "Views" / "Settings" / "LCSettingsView.swift"
            settings_text = settings_path.read_text(encoding="utf-8")
            # Drift anchor
            settings_path.write_text(settings_text.replace("darkModeIcon", "driftedIcon"), encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(PATCH_SCRIPT), str(target)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0, "Patch should fail when anchor drifts")
            self.assertIn("LCSettingsView properties", proc.stderr)

    def test_sidestore_fail_closed_on_drifted_anchor(self):
        sidestore_source = resolve_sidestore_source()
        if not sidestore_source:
            self.skipTest("SideStore source unavailable")

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "SideStore"
            copy_source(sidestore_source, target)

            defaults_path = target / "AltStore" / "Core" / "Extensions" / "UserDefaults+AltStore.swift"
            defaults_text = defaults_path.read_text(encoding="utf-8")
            # Drift anchor
            defaults_path.write_text(defaults_text.replace("useOnDeviceAnisette", "driftedAnisette"), encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(PATCH_SCRIPT), str(target)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0, "Patch should fail when anchor drifts")
            self.assertIn("UserDefaults+AltStore properties", proc.stderr)

    def test_app_layout_preference_persistence_contract(self):
        # 1. Check AppLayoutStyle model enum contract
        model_template = (ROOT / "scripts" / "templates" / "livecontainer_app_layout_style.swift").read_text(encoding="utf-8")
        self.assertIn('case list = "list"', model_template)
        self.assertIn('case grid = "grid"', model_template)
        self.assertIn('case compactList = "compactList"', model_template)

        # 2. Check LiveContainer settings contract: default is .list, showAppLabels is true
        patch_script = (ROOT / "scripts" / "patch_app_layout.py").read_text(encoding="utf-8")
        self.assertIn('appLayoutStyle: AppLayoutStyle = .list', patch_script)
        self.assertIn('showAppLabels: Bool = true', patch_script)
        self.assertIn('LCAppLayoutStyle', patch_script)
        self.assertIn('LCShowAppLabels', patch_script)

        # 3. Check SideStore UserDefaults contract: default is "list", showAppLabels is true
        self.assertIn('UserDefaults.appLayoutStyle', patch_script)
        self.assertIn('UserDefaults.showAppLabels', patch_script)
        self.assertIn('appLayoutStyle = newValue', patch_script)
        self.assertIn('showAppLabels = newValue', patch_script)

    def test_accessibility_label_preserved_when_labels_hidden(self):
        # The UIKit grid control exposes the app name even when visual labels are hidden.
        grid_cell_template = (ROOT / "scripts" / "templates" / "livecontainer_grid_app_cell.swift").read_text(encoding="utf-8")
        self.assertIn("accessibilityLabel = model.displayName", grid_cell_template)
        self.assertIn("titleLabel.isHidden = !showLabels", grid_cell_template)

        # The grid is visual only: launch and every context-menu action come from
        # the established banner controller, including its confirmation and errors.
        self.assertIn("actionRouter.performPrimaryAction()", grid_cell_template)
        self.assertIn("actionRouter.makeContextMenu()", grid_cell_template)
        self.assertNotIn("delegate.removeApp", grid_cell_template)
        self.assertNotIn("appModel.runApp", grid_cell_template)

        # In SideStore AppBannerView, applyLayoutStyle must only toggle titleLabel.isHidden
        patch_script = (ROOT / "scripts" / "patch_app_layout.py").read_text(encoding="utf-8")
        self.assertIn("self.titleLabel?.isHidden = !showLabels", patch_script)

        # If a real SideStore checkout is present, verify accessibilityLabel exists on AppBannerView
        sidestore_source = resolve_sidestore_source()
        if sidestore_source:
            banner_file = sidestore_source / "AltStore" / "Components" / "AppBannerView.swift"
            if banner_file.is_file():
                banner_view = banner_file.read_text(encoding="utf-8")
                self.assertIn("self.accessibilityLabel = values.name", banner_view)
                self.assertIn("self.accessibilityView?.accessibilityLabel", banner_view)

    def test_safety_boundary_presentation_only(self):
        lc_source = resolve_lc_source()
        sidestore_source = resolve_sidestore_source()
        if not lc_source or not sidestore_source:
            self.skipTest("Upstream test sources unavailable for safety boundary verification")

        with tempfile.TemporaryDirectory() as td:
            lc = Path(td) / "LiveContainer"
            ss = Path(td) / "SideStore"
            copy_source(lc_source, lc)
            copy_source(sidestore_source, ss)

            proc = subprocess.run(
                [sys.executable, str(PATCH_SCRIPT), str(lc), str(ss)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0)

            # Assert forbidden files are untouched
            forbidden_patterns = [
                "minimuxer",
                "idevice",
                "jktcp",
                "LocalDevVPN",
                "CoreDevice",
                "BackgroundRefreshAppsOperation.swift",
                "ResignAppOperation.swift",
                "AuthenticationOperation.swift",
                "DatabaseManager.swift",
            ]

            for forbidden in forbidden_patterns:
                for target, upstream in [(lc, lc_source), (ss, sidestore_source)]:
                    for fpath in target.rglob(f"*{forbidden}*"):
                        if fpath.is_file():
                            rel = fpath.relative_to(target)
                            orig = upstream / rel
                            if orig.is_file():
                                self.assertEqual(
                                    fpath.read_bytes(),
                                    orig.read_bytes(),
                                    f"Protected source {rel} was unexpectedly modified",
                                )


if __name__ == "__main__":
    unittest.main()
