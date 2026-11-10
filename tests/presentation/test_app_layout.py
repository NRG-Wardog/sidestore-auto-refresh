"""Maintained App Layout presentation contracts; no historical patch replay.

Static accessibility/dynamic-type contracts and Swift syntax checks are not a
visual rendering claim. Rendering sources are read from the current production owner.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from tests.owner_source import owner_source
from tests.support.product_sources import read_product_source

ROOT = Path(__file__).resolve().parents[2]
LC_PATHS = {
    "grid": "LiveContainerSwiftUI/Views/AppList/LCGridAppCell.swift",
    "model": "LiveContainerSwiftUI/Models/AppLayoutStyle.swift",
    "settings": "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift",
    "apps": "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift",
    "banner": "LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBanner.swift",
    "controller": "LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBannerViewController.swift",
}
SIDE_BANNER = "AltStore/Components/AppBannerView.swift"


class AppLayoutOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {name: owner_source(path) for name, path in LC_PATHS.items()}
        cls.side_banner = owner_source(SIDE_BANNER, owner="SideStore")

    def test_current_grid_preferences_use_shared_runtime_group(self):
        for name in ("settings", "apps"):
            source = self.sources[name]
            self.assertIn('@AppStorage(LCGridSize.storageKey, store: LCUtils.appGroupUserDefault) private var gridSize: LCGridSize = .medium', source)
            self.assertIn('@AppStorage("LCShowAppLabels", store: LCUtils.appGroupUserDefault) private var showAppLabels: Bool = true', source)
            self.assertNotIn("LCAppLayoutStyle", source)
        self.assertIn("showLabels: showAppLabels, gridSize: gridSize", self.sources["apps"])
        for source in self.sources.values():
            self.assertNotIn('UserDefaults(suiteName: "group.', source)

    def test_rendering_inputs_match_the_production_owner(self):
        for name, filename in (("grid", "livecontainer_grid_app_cell.swift"),
                               ("model", "livecontainer_app_layout_style.swift")):
            self.assertEqual(read_product_source(filename), self.sources[name])
        for style in ("list", "grid", "compactList"):
            self.assertIn(f'case {style} = "{style}"', self.sources["model"])

    def test_accessibility_and_action_routing_remain_intact(self):
        source = self.sources["grid"]
        for token in ("accessibilityLabel = model.displayName", "titleLabel.isHidden = !showLabels",
                      "actionRouter.performPrimaryAction()", "actionRouter.makeContextMenu()"):
            self.assertIn(token, source)
        self.assertNotIn("delegate.removeApp", source)
        self.assertNotIn("appModel.runApp", source)
        self.assertIn("self.accessibilityLabel = values.name", self.side_banner)
        self.assertIn("self.accessibilityView?.accessibilityLabel", self.side_banner)

    def test_theme_and_banner_configuration_keep_explicit_shared_preferences(self):
        for name in ("grid", "settings", "banner"):
            for key in ("dynamicColors", "darkModeIcon"):
                self.assertIn(f'@AppStorage("{key}", store: LCUtils.appGroupUserDefault)', self.sources[name])
        for token in ("struct LCAppBannerConfiguration", "let dynamicColors: Bool", "let darkModeIcon: Bool", "layoutStyle: AppLayoutStyle"):
            self.assertIn(token, self.sources["controller"])
        for token in ("dynamicColors: dynamicColors", "darkModeIcon: darkModeIcon", "layoutStyle: layoutStyle"):
            self.assertIn(token, self.sources["banner"])

    def test_grid_explicit_size_category_reaches_creation_and_update(self):
        text = self.sources["grid"]
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
        text = self.sources["grid"]
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
        text = self.sources["grid"]
        for category in (
            "extraSmall", "small", "medium", "large", "extraLarge", "extraExtraLarge", "extraExtraExtraLarge",
            "accessibilityMedium", "accessibilityLarge", "accessibilityExtraLarge",
            "accessibilityExtraExtraLarge", "accessibilityExtraExtraExtraLarge",
        ):
            with self.subTest(category=category):
                self.assertIn(f"case .{category}: return .{category}", text)
        self.assertIn("@unknown default: return .large", text)

    def test_grid_fallback_harness_size_that_fits_anchor_is_preserved(self):
        text = self.sources["grid"]
        pattern = r"    @available\(iOS 16\.0, \*\)\n    func sizeThatFits\([^\n]+\n        uiViewController\.fittingSize\(width: proposal\.width\)\n    }\n"
        fallback, count = re.subn(pattern, "", text)
        self.assertEqual(count, 1)
        self.assertNotIn("func sizeThatFits(", fallback)
        self.assertIn("preferredContentSize = fittingSize(width: nil)", fallback)
        self.assertIn("height: gridView.intrinsicContentSize.height", fallback)


class AppLayoutOwnerSyntaxTests(unittest.TestCase):
    def test_current_presentation_files_parse_with_swift(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            raise RuntimeError("Swift compiler required for owner presentation syntax tests")
        sources = [owner_source(path) for path in LC_PATHS.values()]
        sources.append(owner_source(SIDE_BANNER, owner="SideStore"))
        with tempfile.TemporaryDirectory() as directory:
            for index, source in enumerate(sources):
                path = Path(directory)/f"source{index}.swift"
                path.write_text(source)
                result = subprocess.run([compiler, "-frontend", "-parse", str(path)],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
