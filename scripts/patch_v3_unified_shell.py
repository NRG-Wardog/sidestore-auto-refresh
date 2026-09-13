#!/usr/bin/env python3
"""Install the v3 host-owned combined navigation shell.

The patch intentionally does not copy SideStore's database or preferences into
LiveContainer. The small status snapshot is a read-only, bounded UI cache that
the embedded SideStore publishes after its database has started.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

MARKER = "V3_UNIFIED_SHELL_V1_BEGIN"
TEMPLATE = Path(__file__).with_name("templates") / "v3_unified_shell.swift"


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

    settings = root / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"
    text = settings.read_text(encoding="utf-8")
    old = '''                if store == .SideStore {
                    Section {
                        NavigationLink { LCEmbeddedSideStoreRefreshView() } label: { Text("SideStore scheduled refresh") }
                    }
                }
'''
    if old in text:
        settings.write_text(text.replace(old, "", 1), encoding="utf-8")

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


def patch_embedded_status(root: Path) -> None:
    path = root / "AltStore/AppDelegate.swift"
    text = path.read_text(encoding="utf-8")
    marker = "V3_SIDESTORE_STATUS_SNAPSHOT_V1"
    if marker in text:
        return
    anchor = "                debugLog(\"Started DatabaseManager.\")\n"
    insertion = '''                debugLog("Started DatabaseManager.")
                // V3_SIDESTORE_STATUS_SNAPSHOT_V1: publish display-only status.
                // Credentials, certificates, tokens, and database objects never leave SideStore.
                if let defaults = UserDefaults(suiteName: "group.com.SideStore.SideStore") {
                    let account = DatabaseManager.shared.activeAccount()?.appleID ?? "Not signed in"
                    let installedApps = InstalledApp.all(in: DatabaseManager.shared.viewContext)
                    let sourceRequest = NSFetchRequest<Source>(entityName: "Source")
                    let sources = (try? DatabaseManager.shared.viewContext.fetch(sourceRequest)) ?? []
                    let sourceRows: [[String: Any]] = sources.prefix(100).map { source in
                        ["identifier": source.identifier,
                         "name": source.name,
                         "subtitle": source.subtitle ?? "",
                         "url": source.sourceURL.absoluteString,
                         "appCount": source.apps.count]
                    }
                    let appRows: [[String: Any]] = installedApps.prefix(100).map { app in
                        ["bundleID": app.bundleIdentifier,
                         "name": app.name,
                         "version": app.version,
                         "isActive": app.isActive,
                         "expirationDate": app.expirationDate,
                         "hasUpdate": app.hasUpdate,
                         "certificateStatus": app.certificateStatusRaw ?? "valid"]
                    }
                    defaults.set(["account": account,
                                  "signing": DatabaseManager.shared.activeTeam() == nil ? "No active team" : "Ready",
                                  "installedAppCount": installedApps.count,
                                  "installedApps": appRows,
                                  "sources": sourceRows,
                                  "updatedAt": Date()], forKey: "v3SideStoreStatusSnapshot")
                }
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
    for token in (MARKER, "V3SideStoreStatusStore", "V3SourcesView", "LCEmbeddedSideStoreRefreshView", "LCTabIdentifier.refresh"):
        if token not in shell:
            die(f"v3 shell is missing {token}")
    if "V3UnifiedShell()" not in required[1].read_text(encoding="utf-8"):
        die("v3 shell is not the application root")
    app_list = (live / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift").read_text(encoding="utf-8")
    if "V3_UNIFIED_SHELL_V1: SideStore is reached through unified tabs." not in app_list:
        die("legacy SideStore launch button removal marker is missing from the Apps screen")
    if "V3_SIDESTORE_STATUS_SNAPSHOT_V1" not in (side / "AltStore/AppDelegate.swift").read_text(encoding="utf-8"):
        die("embedded SideStore status publisher is missing")
    compiler = shutil.which("swiftc")
    if compiler:
        for path in (required[0], side / "AltStore/AppDelegate.swift"):
            subprocess.run([compiler, "-frontend", "-parse", str(path)], check=True)


def patch(live: Path, side: Path) -> None:
    patch_host(live)
    patch_embedded_status(side)
    verify(live, side)


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: patch_v3_unified_shell.py <livecontainer-root> <embedded-sidestore-root>")
    patch(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    print("v3 unified shell patch applied and verified")


if __name__ == "__main__":
    main()
