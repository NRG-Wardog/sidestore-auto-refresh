"""Regression coverage for the v3 host-owned navigation shell."""
from pathlib import Path
import importlib.util
import shutil
import tempfile
import unittest
from typing import Tuple

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v3_patch", ROOT / "scripts/patch_v3_unified_shell.py")
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)


def fixture(root: Path) -> Tuple[Path, Path]:
    live = root / "LiveContainer"
    side = root / "SideStore"
    (live / "LiveContainerSwiftUI/Utilities").mkdir(parents=True)
    (live / "LiveContainerSwiftUI/App").mkdir(parents=True)
    (live / "LiveContainerSwiftUI/Views/Settings").mkdir(parents=True)
    (side / "AltStore").mkdir(parents=True)
    (live / "LiveContainerSwiftUI/Utilities/Shared.swift").write_text("public enum LCTabIdentifier: Hashable {\n    case sources\n    case apps\n    case tweaks\n    case settings\n}\n\npublic struct SharedModel {\n    @Published var selectedTab: LCTabIdentifier = .apps\n}\n")
    (live / "LiveContainerSwiftUI/App/LiveContainerSwiftUIApp.swift").write_text("struct Root {\n            LCTabView()\n}\n")
    (live / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift").write_text('''struct Settings {
                if store == .SideStore {
                    Section {
                        NavigationLink { LCEmbeddedSideStoreRefreshView() } label: { Text("SideStore scheduled refresh") }
                    }
                }
}

struct LCTweaksView: View {
    var body: some View { Text("tweaks") }
}
''')
    (live / "LiveContainerSwiftUI/Views/AppList").mkdir(parents=True)
    (live / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift").write_text('''struct Apps {
                ToolbarItem(placement: .topBarLeading) {
                    if(UserDefaults.sideStoreExist()) {
                        Button {
                            LCUtils.openSideStore(delegate: self)
                        } label: {
                            IconImageView(icon: BuiltInSideStoreAppInfo.shared.iconIsDarkIcon(darkModeIcon))
                                .frame(width: UIFont.preferredFont(forTextStyle: .body).lineHeight, height: UIFont.preferredFont(forTextStyle: .body).lineHeight)

                        }
                    } else {
                        Button("Help", systemImage: "questionmark") {
                            helpPresent = true
                        }
                    }
                    

                }
                
}
''')
    (side / "AltStore/AppDelegate.swift").write_text('''func boot() {
                debugLog("Started DatabaseManager.")
}
''')
    return live, side


class V3UnifiedShellTests(unittest.TestCase):
    def test_navigation_anchor_drift_fails_without_partial_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live, side = fixture(root)
            path = live / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"
            path.write_text(path.read_text().replace("SideStore scheduled refresh", "Changed upstream refresh"))
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(SystemExit):
                patch.patch(live, side)
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_shell_is_idempotent_and_host_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            live, side = fixture(Path(directory))
            patch.patch(live, side)
            first = {p.relative_to(Path(directory)): p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}
            patch.patch(live, side)
            self.assertEqual(first, {p.relative_to(Path(directory)): p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()})
            shell = (live / "LiveContainerSwiftUI/Views/V3UnifiedShell.swift").read_text()
            self.assertIn("struct V3UnifiedShell", shell)
            self.assertIn(".tag(LCTabIdentifier.home)", shell)
            self.assertNotIn(".tag(LCTabIdentifier.refresh)", shell)
            for tab in ("home", "apps", "sources", "settings"):
                self.assertIn(f".tag(LCTabIdentifier.{tab})", shell)
            self.assertNotIn("sharedModel.selectedTab = .home", shell)
            self.assertIn("status.refreshPresented = true", shell)
            self.assertNotIn("LCUtils.openSideStore", shell)
            self.assertIn("V3_UNIFIED_SHELL_V1: SideStore is reached through unified tabs.", (live / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift").read_text())
            self.assertNotIn("SideStore scheduled refresh", (live / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift").read_text())
            self.assertIn("Refresh, Schedule and History", (live / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift").read_text())
            status = (side / "AltStore/AppDelegate.swift").read_text()
            self.assertIn("V3_SIDESTORE_STATUS_SNAPSHOT_V1", status)
            self.assertNotIn("appleIDPassword", status)
            self.assertNotIn("appleIDXcodeToken", status)

    def test_launch_tab_startup_preference_is_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            live, side = fixture(Path(directory))
            patch.patch(live, side)
            shared = (live / "LiveContainerSwiftUI/Utilities/Shared.swift").read_text()
            self.assertIn("LCLaunchTab.resolve(LCUtils.appGroupUserDefault.string(forKey: LCLaunchTab.storageKey)) == .apps ? .apps : .home", shared)

    def test_launch_tab_anchor_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live, side = fixture(root)
            path = live / "LiveContainerSwiftUI/Utilities/Shared.swift"
            path.write_text(path.read_text().replace(
                "    @Published var selectedTab: LCTabIdentifier = .apps",
                "    @Published var selectedTab: LCTabIdentifier = .tweaks"))
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(SystemExit):
                patch.patch(live, side)
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_files_install_is_separate_from_guest_import(self):
        source = (ROOT / "scripts/templates/v3_unified_shell.swift").read_text(encoding="utf-8")
        service = (ROOT / "scripts/templates/v3_sidestore_service.swift").read_text(encoding="utf-8")
        integration = (ROOT / "scripts/patch_v3_service.py").read_text(encoding="utf-8")
        self.assertIn('Button("Install / Sideload App")', source)
        self.assertIn('UIDocumentPickerViewController(forOpeningContentTypes:', source)
        self.assertIn('func documentPickerWasCancelled', source)
        self.assertIn('status.stageSharedIPA(url, title: "Install / Sideload App")', source)
        self.assertIn('perform("installSharedIPA", target: token', source)
        self.assertIn('installTarget = .url(url)', service)
        self.assertIn('AppManager.shared.install(installTarget', service)
        self.assertIn('group.cancel(); group.progress.cancel()', service)
        self.assertIn('status.accept(try await V3ServiceBridge.shared.request', source)
        self.assertIn('Button("Add to LiveContainer"', integration)
        self.assertIn('choosingIPA = true', integration)

    def test_template_parses_when_swift_is_available(self):
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("swiftc unavailable")
        result = __import__("subprocess").run([compiler, "-frontend", "-parse", str(ROOT / "scripts/templates/v3_unified_shell.swift")], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
