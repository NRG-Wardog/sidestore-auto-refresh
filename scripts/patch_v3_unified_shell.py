#!/usr/bin/env python3
"""Install the v3 host-owned combined navigation shell.

The patch intentionally does not copy SideStore's database or preferences into
LiveContainer. Status is queried from the command service into an in-memory
projection; the old persistent snapshot publisher is retired.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

MARKER = "V3_UNIFIED_SHELL_V1_BEGIN"
TEMPLATE = Path(__file__).with_name("templates") / "v3_unified_shell.swift"
INTENT_TEMPLATE = Path(__file__).with_name("templates") / "v3_setup_intent.swift"


def die(message: str) -> None:
    raise SystemExit(f"patch_v3_unified_shell: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_host(root: Path) -> None:
    shared = root / "LiveContainerSwiftUI/Utilities/Shared.swift"
    text = shared.read_text(encoding="utf-8")
    if "case home" not in text:
        text = replace_once(text, "public enum LCTabIdentifier: Hashable {\n    case sources\n    case apps\n    case tweaks\n    case settings\n}",
                            "public enum LCTabIdentifier: Hashable {\n    case home\n    case sources\n    case apps\n    case refresh\n    case tweaks\n    case settings\n}", "tab identifiers")
        text = replace_once(text,
                            '    @Published var selectedTab: LCTabIdentifier = .apps',
                            '    @Published var selectedTab: LCTabIdentifier = LCLaunchTab.resolve(LCUtils.appGroupUserDefault.string(forKey: LCLaunchTab.storageKey)) == .apps ? .apps : .home',
                            "launch-tab startup preference")
        shared.write_text(text, encoding="utf-8")

    app = root / "LiveContainerSwiftUI/App/LiveContainerSwiftUIApp.swift"
    text = app.read_text(encoding="utf-8")
    if "V3UnifiedShell()" not in text:
        text = replace_once(text, "            LCTabView()", "            V3UnifiedShell()", "v3 application root")
        app.write_text(text, encoding="utf-8")

    shell = root / "LiveContainerSwiftUI/Views/V3UnifiedShell.swift"
    expected = TEMPLATE.read_text(encoding="utf-8")
    if shell.exists() and shell.read_text(encoding="utf-8") != expected:
        die("existing v3 shell differs from the current template")
    shell.write_text(expected, encoding="utf-8")

    intent = root / "LiveContainerSwiftUI/App/V3SetupAssistantIntent.swift"
    expected_intent = INTENT_TEMPLATE.read_text(encoding="utf-8")
    if intent.exists() and intent.read_text(encoding="utf-8") != expected_intent:
        die("existing v3 setup intent differs from the current template")
    intent.write_text(expected_intent, encoding="utf-8")

    settings = root / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"
    text = settings.read_text(encoding="utf-8")
    old = '''                if store == .SideStore {
                    Section {
                        NavigationLink { LCEmbeddedSideStoreRefreshView() } label: { Text("SideStore scheduled refresh") }
                    }
                }
'''
    replacement = '''                Section {
                    NavigationLink { LCEmbeddedSideStoreRefreshView() } label: { Text("Refresh, Schedule and History") }
                }
'''
    if old in text:
        settings.write_text(text.replace(old, replacement, 1), encoding="utf-8")
    elif replacement not in text:
        die("refresh settings anchor changed")

    app_list = root / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift"
    text = app_list.read_text(encoding="utf-8")
    launch_button = '''                ToolbarItem(placement: .topBarLeading) {
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
                
'''
    if launch_button in text:
        text = text.replace(launch_button, "                // V3_UNIFIED_SHELL_V1: SideStore is reached through unified tabs.\n\n", 1)
        app_list.write_text(text, encoding="utf-8")
    elif "V3_UNIFIED_SHELL_V1: SideStore is reached through unified tabs." not in text:
        die("legacy launch removal anchor changed")


def patch_embedded_status(root: Path) -> None:
    path = root / "AltStore/AppDelegate.swift"
    text = path.read_text(encoding="utf-8")
    marker = "V3_SIDESTORE_STATUS_SNAPSHOT_V1"
    if marker in text:
        return
    anchor = "                debugLog(\"Started DatabaseManager.\")\n"
    insertion = '''                debugLog("Started DatabaseManager.")
                // V3_SIDESTORE_STATUS_SNAPSHOT_V1: retired in favor of live XPC reads.
'''
    text = replace_once(text, anchor, insertion, "database startup status snapshot")
    path.write_text(text, encoding="utf-8")


def verify(live: Path, side: Path) -> None:
    required = (
        live / "LiveContainerSwiftUI/Views/V3UnifiedShell.swift",
        live / "LiveContainerSwiftUI/App/LiveContainerSwiftUIApp.swift",
        live / "LiveContainerSwiftUI/Utilities/Shared.swift",
    )
    if any(not p.exists() for p in required):
        die("v3 host files are missing")
    shell = required[0].read_text(encoding="utf-8")
    for token in (MARKER, "V3SideStoreStatusStore", "V3SourcesView", "LCEmbeddedSideStoreRefreshView", "LCTabIdentifier.settings",
                  "V3SignInView", "V3CertificatesView", "V3PromptSection", "V3PairingView", "V3AuthStore",
                  "V3SetupAssistantView", "V3SetupStore", "setupPresented"):
        if token not in shell:
            die(f"v3 shell is missing {token}")
    for forbidden in ("V3RemoteServiceView", "Self.presenter", "presentingViewController: Self.presenter"):
        if forbidden in shell:
            die(f"v3 shell still embeds SideStore UI: {forbidden}")
    intent = live / "LiveContainerSwiftUI/App/V3SetupAssistantIntent.swift"
    if not intent.exists() or "V3SetupAssistantIntent" not in intent.read_text(encoding="utf-8"):
        die("v3 setup intent is missing")
    if "V3UnifiedShell()" not in required[1].read_text(encoding="utf-8"):
        die("v3 shell is not the application root")
    app_list = (live / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift").read_text(encoding="utf-8")
    if "V3_UNIFIED_SHELL_V1: SideStore is reached through unified tabs." not in app_list:
        die("legacy SideStore launch button removal marker is missing from the Apps screen")
    if "V3_SIDESTORE_STATUS_SNAPSHOT_V1" not in (side / "AltStore/AppDelegate.swift").read_text(encoding="utf-8"):
        die("embedded SideStore snapshot retirement marker is missing")
    compiler = shutil.which("swiftc")
    if compiler:
        for path in (required[0], side / "AltStore/AppDelegate.swift"):
            subprocess.run([compiler, "-frontend", "-parse", str(path)], check=True)


def patch(live: Path, side: Path) -> None:
    # Validate the complete transaction on disposable copies before touching inputs.
    paths = (
        ("LiveContainerSwiftUI/Utilities/Shared.swift", "LiveContainerSwiftUI/App/LiveContainerSwiftUIApp.swift",
         "LiveContainerSwiftUI/App/V3SetupAssistantIntent.swift",
         "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift", "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift",
         "LiveContainerSwiftUI/Views/V3UnifiedShell.swift"),
        ("AltStore/AppDelegate.swift",))
    with tempfile.TemporaryDirectory(prefix="v3-shell-") as temporary:
        staged = (Path(temporary) / "live", Path(temporary) / "side")
        for original, destination, names in zip((live, side), staged, paths):
            for name in names:
                if (original / name).exists():
                    (destination / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original / name, destination / name)
        patch_host(staged[0])
        patch_embedded_status(staged[1])
        verify(*staged)
        for original, destination, names in zip((live, side), staged, paths):
            for name in names:
                (original / name).parent.mkdir(parents=True, exist_ok=True)
                (original / name).write_bytes((destination / name).read_bytes())


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: patch_v3_unified_shell.py <livecontainer-root> <embedded-sidestore-root>")
    from patch_v3_service import PINS
    for root, pin in zip(sys.argv[1:], PINS):
        if subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip() != pin:
            die("input revision does not match the combined source pin")
    patch(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    print("v3 unified shell patch applied and verified")


if __name__ == "__main__":
    main()
