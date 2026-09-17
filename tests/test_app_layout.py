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
import re
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
    if (src / "LiveContainerSwiftUI").is_dir():
        # The patch only reads revision metadata. Reuse the pinned fixture's
        # object database without copying it or mutating its working tree.
        git_dir = subprocess.check_output(["git", "-C", str(src), "rev-parse", "--absolute-git-dir"], text=True).strip()
        (dst / ".git").write_text("gitdir: " + git_dir + "\n", encoding="utf-8")


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

    def test_livecontainer_replay_rejects_generated_grid_drift(self):
        source = resolve_lc_source()
        if not source:
            self.skipTest("LiveContainer source unavailable")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "LiveContainer"
            copy_source(source, target)
            first = subprocess.run([sys.executable, str(PATCH_SCRIPT), str(target)], capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            grid = target / "LiveContainerSwiftUI/Views/AppList/LCGridAppCell.swift"
            grid.write_text(grid.read_text(encoding="utf-8") + "\n// Unexpected downstream mutation\n", encoding="utf-8")
            changed = grid.read_bytes()
            replay = subprocess.run([sys.executable, str(PATCH_SCRIPT), str(target)], capture_output=True, text=True)
            self.assertNotEqual(replay.returncode, 0)
            self.assertIn("replay drift", replay.stderr)
            self.assertEqual(grid.read_bytes(), changed, "Replay must not erase unexpected downstream changes")

    def test_livecontainer_failed_patch_is_transactional(self):
        source = resolve_lc_source()
        if not source:
            self.skipTest("LiveContainer source unavailable")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "LiveContainer"
            copy_source(source, target)
            controller = target / "LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBannerViewController.swift"
            controller.write_text(controller.read_text(encoding="utf-8").replace("traitCollection: traitCollection", "traitCollection: unexpectedTraits"), encoding="utf-8")
            before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*.swift")}
            failed = subprocess.run([sys.executable, str(PATCH_SCRIPT), str(target)], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("compact controller sizing", failed.stderr)
            after = {p.relative_to(target): p.read_bytes() for p in target.rglob("*.swift")}
            self.assertEqual(before, after, "A late anchor mismatch must not partially apply layout changes")

    def test_livecontainer_unversioned_input_is_rejected(self):
        source = resolve_lc_source()
        if not source:
            self.skipTest("LiveContainer source unavailable")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "LiveContainer"
            copy_source(source, target)
            (target / ".git").unlink()
            failed = subprocess.run([sys.executable, str(PATCH_SCRIPT), str(target)], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("requires a versioned checkout", failed.stderr)
            self.assertFalse((target / "LiveContainerSwiftUI/Views/AppList/LCGridAppCell.swift").exists())

    def test_livecontainer_wrong_revision_is_rejected(self):
        source = resolve_lc_source()
        if not source:
            self.skipTest("LiveContainer source unavailable")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "LiveContainer"
            copy_source(source, target)
            wrong_git = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--absolute-git-dir"], text=True).strip()
            (target / ".git").write_text("gitdir: " + wrong_git + "\n", encoding="utf-8")
            failed = subprocess.run([sys.executable, str(PATCH_SCRIPT), str(target)], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("revision mismatch", failed.stderr)

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

    def test_grid_explicit_size_category_reaches_creation_and_update(self):
        text = (ROOT / "scripts/templates/livecontainer_grid_app_cell.swift").read_text(encoding="utf-8")
        self.assertIn(r"@Environment(\.sizeCategory) private var sizeCategory", text)
        for method in ("makeUIViewController", "updateUIViewController"):
            with self.subTest(method=method):
                body = text.split(f"func {method}(", 1)[1].split("\n    }", 1)[0]
                self.assertIn("sizeCategory: Self.uiContentSizeCategory(sizeCategory)", body)
        controller = text.split("final class LCGridAppCellViewController:", 1)[1].split("private final class LCGridAppCellView:", 1)[0]
        initializer = controller.split("    init(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("sizeCategory: UIContentSizeCategory)", initializer)
        self.assertIn("gridView = LCGridAppCellView(sizeCategory: sizeCategory)", initializer)
        self.assertRegex(initializer, r"update\(model: configuration\.model,[^\n]+sizeCategory: sizeCategory\)")
        update = controller.split("    func update(", 1)[1].split("\n    }", 1)[0]
        self.assertIn("sizeCategory: UIContentSizeCategory)", update)
        self.assertRegex(update, r"gridView\.update\(model: model,[^\n]+sizeCategory: sizeCategory\)")
        self.assertLess(update.index("gridView.update("), update.index("preferredContentSize = fittingSize(width: nil)"))
        self.assertIn("height: gridView.intrinsicContentSize.height", controller)

    def test_grid_stored_category_is_the_only_font_scaling_authority(self):
        text = (ROOT / "scripts/templates/livecontainer_grid_app_cell.swift").read_text(encoding="utf-8")
        view = text.split("private final class LCGridAppCellView:", 1)[1]
        self.assertIn("private var sizeCategory: UIContentSizeCategory", view)
        initializer = view.split("    init(sizeCategory: UIContentSizeCategory)", 1)[1].split("\n    }", 1)[0]
        update = view.split("    func update(", 1)[1].split("\n    }", 1)[0]
        for name, body in (("creation", initializer), ("update", update)):
            with self.subTest(path=name):
                self.assertLess(body.index("self.sizeCategory = sizeCategory"), body.index("updateMetrics()"))
        self.assertIn("sizeCategory: UIContentSizeCategory)", update)
        metrics = view.split("    private func updateMetrics() {", 1)[1].split("\n    }", 1)[0]
        self.assertIn("UITraitCollection(preferredContentSizeCategory: sizeCategory)", metrics)
        self.assertIn("UIFontMetrics(forTextStyle: .caption1).scaledFont(", metrics)
        self.assertIn("compatibleWith: traits)", metrics)
        self.assertLess(metrics.index("titleLabel.font ="), metrics.index("invalidateIntrinsicContentSize()"))
        self.assertIn("setNeedsLayout()", metrics)
        self.assertEqual(text.count("titleLabel.font ="), 1)
        self.assertIn("titleLabel.adjustsFontForContentSizeCategory = false", view)
        self.assertNotIn("titleLabel.adjustsFontForContentSizeCategory = true", text)
        self.assertNotIn("traitCollectionDidChange", text)
        self.assertNotIn(".traitCollection", text)
        self.assertNotIn("updateMetrics(for:", text)
        self.assertIn("titleLabel.isHidden ? 0 : Self.labelSpacing + ceil(titleLabel.font.lineHeight * 2)", view)
        self.assertIn("height: Self.topInset + iconSide + labelHeight + Self.bottomInset", view)

    def test_grid_size_category_bridge_covers_all_dynamic_type_sizes(self):
        text = (ROOT / "scripts/templates/livecontainer_grid_app_cell.swift").read_text(encoding="utf-8")
        for category in (
            "extraSmall", "small", "medium", "large", "extraLarge", "extraExtraLarge", "extraExtraExtraLarge",
            "accessibilityMedium", "accessibilityLarge", "accessibilityExtraLarge",
            "accessibilityExtraExtraLarge", "accessibilityExtraExtraExtraLarge",
        ):
            with self.subTest(category=category):
                self.assertIn(f"case .{category}: return .{category}", text)
        self.assertIn("@unknown default: return .large", text)

    def test_grid_fallback_harness_size_that_fits_anchor_is_preserved(self):
        text = (ROOT / "scripts/templates/livecontainer_grid_app_cell.swift").read_text(encoding="utf-8")
        pattern = r"    @available\(iOS 16\.0, \*\)\n    func sizeThatFits\([^\n]+\n        uiViewController\.fittingSize\(width: proposal\.width\)\n    }\n"
        fallback, count = re.subn(pattern, "", text)
        self.assertEqual(count, 1)
        self.assertNotIn("func sizeThatFits(", fallback)
        self.assertIn("preferredContentSize = fittingSize(width: nil)", fallback)
        self.assertIn("height: gridView.intrinsicContentSize.height", fallback)

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
