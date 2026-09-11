#!/usr/bin/env python3
"""Build-time deterministic semantic patch for App Layout architecture (Issue #17).

Provides List (default), Grid, and Compact List layout options with
Show app labels toggle in Grid mode, persisted in UserDefaults.
Only presentation and settings layers are modified.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

TEMPLATES = Path(__file__).resolve().parent / "templates"
MARKER_LIVE_CONTAINER = "// LC_APP_LAYOUT_PATCH_V1"
MARKER_SIDESTORE = "// SIDESTORE_APP_LAYOUT_PATCH_V1"


def template(name: str) -> str:
    path = TEMPLATES / name
    if not path.is_file():
        die(f"template not found: {name}")
    return path.read_text(encoding="utf-8")


def die(message: str) -> None:
    raise SystemExit(f"patch_app_layout: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"{label}: expected 1 anchor, found {count}")
    return text.replace(old, new, 1)


def patch_livecontainer_model(root: Path) -> None:
    models_dir = root / "LiveContainerSwiftUI" / "Models"
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / "AppLayoutStyle.swift"
    target.write_text(template("livecontainer_app_layout_style.swift"), encoding="utf-8")


def patch_livecontainer_grid_cell(root: Path) -> None:
    views_dir = root / "LiveContainerSwiftUI" / "Views" / "AppList"
    views_dir.mkdir(parents=True, exist_ok=True)
    target = views_dir / "LCGridAppCell.swift"
    target.write_text(template("livecontainer_grid_app_cell.swift"), encoding="utf-8")


def patch_livecontainer_settings(root: Path) -> None:
    path = root / "LiveContainerSwiftUI" / "Views" / "Settings" / "LCSettingsView.swift"
    text = path.read_text(encoding="utf-8")
    if MARKER_LIVE_CONTAINER in text:
        return

    prop_anchor = '@AppStorage("darkModeIcon", store: LCUtils.appGroupUserDefault) var darkModeIcon = false'
    prop_addition = (
        '\n    @AppStorage("LCAppLayoutStyle", store: LCUtils.appGroupUserDefault) private var appLayoutStyle: AppLayoutStyle = .list\n'
        '    @AppStorage("LCShowAppLabels", store: LCUtils.appGroupUserDefault) private var showAppLabels: Bool = true'
    )
    text = replace_once(text, prop_anchor, prop_anchor + prop_addition, "LCSettingsView properties")

    section_anchor = '                Section{\n                    Toggle(isOn: $dynamicColors) {'
    section_replacement = (
        '                Section{\n'
        '                    ' + MARKER_LIVE_CONTAINER + '\n'
        '                    Picker(selection: $appLayoutStyle) {\n'
        '                        ForEach(AppLayoutStyle.allCases) { style in\n'
        '                            Text(style.displayName).tag(style)\n'
        '                        }\n'
        '                    } label: {\n'
        '                        Text("App Layout")\n'
        '                    }\n'
        '                    if appLayoutStyle == .grid {\n'
        '                        Toggle(isOn: $showAppLabels) {\n'
        '                            Text("Show app labels")\n'
        '                        }\n'
        '                    }\n'
        '                    Toggle(isOn: $dynamicColors) {'
    )
    text = replace_once(text, section_anchor, section_replacement, "LCSettingsView interface section")
    path.write_text(text, encoding="utf-8")


def patch_livecontainer_banner_view(root: Path) -> None:
    # 1. LCAppBannerView.swift
    banner_view_path = root / "LiveContainerSwiftUI" / "Views" / "AppList" / "LCAppBanner" / "LCAppBannerView.swift"
    text = banner_view_path.read_text(encoding="utf-8")
    if "compactBannerHeight" not in text:
        anchor = "final class LCAppBannerRootView: UIView {\n    static let bannerHeight: CGFloat = 88"
        replacement = (
            "final class LCAppBannerRootView: UIView {\n"
            "    static let bannerHeight: CGFloat = 88\n"
            "    static let compactBannerHeight: CGFloat = 56"
        )
        text = replace_once(text, anchor, replacement, "LCAppBannerRootView compact height")
        banner_view_path.write_text(text, encoding="utf-8")

    # 2. LCAppBannerViewController.swift
    banner_vc_path = root / "LiveContainerSwiftUI" / "Views" / "AppList" / "LCAppBanner" / "LCAppBannerViewController.swift"
    vc_text = banner_vc_path.read_text(encoding="utf-8")
    if "layoutStyle: AppLayoutStyle" not in vc_text:
        cfg_anchor = "struct LCAppBannerConfiguration {\n    let model: LCAppModel\n    let dynamicColors: Bool\n    let darkModeIcon: Bool\n}"
        cfg_replacement = (
            "struct LCAppBannerConfiguration {\n"
            "    let model: LCAppModel\n"
            "    let dynamicColors: Bool\n"
            "    let darkModeIcon: Bool\n"
            "    var layoutStyle: AppLayoutStyle = .list\n"
            "}"
        )
        vc_text = replace_once(vc_text, cfg_anchor, cfg_replacement, "LCAppBannerConfiguration layoutStyle")

        pref_anchor = "preferredContentSize = CGSize(width: 0, height: LCAppBannerRootView.bannerHeight)"
        pref_replacement = "preferredContentSize = CGSize(width: 0, height: configuration.layoutStyle == .compactList ? LCAppBannerRootView.compactBannerHeight : LCAppBannerRootView.bannerHeight)"
        vc_text = replace_once(vc_text, pref_anchor, pref_replacement, "LCAppBannerViewController preferredContentSize")

        update_anchor = (
            "    func update(\n"
            "        model: LCAppModel,\n"
            "        dynamicColors: Bool,\n"
            "        darkModeIcon: Bool\n"
            "    ) {\n"
            "        loadViewIfNeeded()\n"
            "        configuration = LCAppBannerConfiguration(\n"
            "            model: model,\n"
            "            dynamicColors: dynamicColors,\n"
            "            darkModeIcon: darkModeIcon\n"
            "        )\n"
            "        refreshView()\n"
            "    }"
        )
        update_replacement = (
            "    func update(\n"
            "        model: LCAppModel,\n"
            "        dynamicColors: Bool,\n"
            "        darkModeIcon: Bool,\n"
            "        layoutStyle: AppLayoutStyle = .list\n"
            "    ) {\n"
            "        loadViewIfNeeded()\n"
            "        configuration = LCAppBannerConfiguration(\n"
            "            model: model,\n"
            "            dynamicColors: dynamicColors,\n"
            "            darkModeIcon: darkModeIcon,\n"
            "            layoutStyle: layoutStyle\n"
            "        )\n"
            "        refreshView()\n"
            "    }"
        )
        vc_text = replace_once(vc_text, update_anchor, update_replacement, "LCAppBannerViewController update")
        banner_vc_path.write_text(vc_text, encoding="utf-8")

    # 3. LCAppBanner.swift
    banner_rep_path = root / "LiveContainerSwiftUI" / "Views" / "AppList" / "LCAppBanner" / "LCAppBanner.swift"
    rep_text = banner_rep_path.read_text(encoding="utf-8")
    if "var layoutStyle: AppLayoutStyle" not in rep_text:
        init_anchor = (
            "    init(appModel: LCAppModel, delegate: LCAppBannerDelegate) {\n"
            "        _model = ObservedObject(wrappedValue: appModel)\n"
            "        self.delegate = delegate\n"
            "    }"
        )
        init_replacement = (
            "    var layoutStyle: AppLayoutStyle = .list\n\n"
            "    init(appModel: LCAppModel, delegate: LCAppBannerDelegate, layoutStyle: AppLayoutStyle = .list) {\n"
            "        _model = ObservedObject(wrappedValue: appModel)\n"
            "        self.delegate = delegate\n"
            "        self.layoutStyle = layoutStyle\n"
            "    }"
        )
        rep_text = replace_once(rep_text, init_anchor, init_replacement, "LCAppBanner init")

        make_anchor = "let viewController = LCAppBannerViewController(delegate: delegate, config: LCAppBannerConfiguration(model: model, dynamicColors: dynamicColors, darkModeIcon: darkModeIcon))"
        make_replacement = "let viewController = LCAppBannerViewController(delegate: delegate, config: LCAppBannerConfiguration(model: model, dynamicColors: dynamicColors, darkModeIcon: darkModeIcon, layoutStyle: layoutStyle))"
        rep_text = replace_once(rep_text, make_anchor, make_replacement, "LCAppBanner makeUIViewController")

        update_anchor = (
            "        viewController.update(\n"
            "            model: model,\n"
            "            dynamicColors: dynamicColors,\n"
            "            darkModeIcon: darkModeIcon\n"
            "        )"
        )
        update_replacement = (
            "        viewController.update(\n"
            "            model: model,\n"
            "            dynamicColors: dynamicColors,\n"
            "            darkModeIcon: darkModeIcon,\n"
            "            layoutStyle: layoutStyle\n"
            "        )"
        )
        rep_text = replace_once(rep_text, update_anchor, update_replacement, "LCAppBanner updateUIViewController")

        size_anchor = "return CGSize(width: width, height: LCAppBannerRootView.bannerHeight)"
        size_replacement = "let height = layoutStyle == .compactList ? LCAppBannerRootView.compactBannerHeight : LCAppBannerRootView.bannerHeight\n        return CGSize(width: width, height: height)"
        rep_text = replace_once(rep_text, size_anchor, size_replacement, "LCAppBanner sizeThatFits")
        banner_rep_path.write_text(rep_text, encoding="utf-8")


def patch_livecontainer_app_list_view(root: Path) -> None:
    path = root / "LiveContainerSwiftUI" / "Views" / "AppList" / "LCAppListView.swift"
    text = path.read_text(encoding="utf-8")
    if MARKER_LIVE_CONTAINER in text:
        return

    prop_anchor = "    @EnvironmentObject private var sharedAppSortManager : LCAppSortManager\n"
    render_block = (
        "    @EnvironmentObject private var sharedAppSortManager : LCAppSortManager\n"
        "    " + MARKER_LIVE_CONTAINER + "\n"
        "    @AppStorage(\"LCAppLayoutStyle\", store: LCUtils.appGroupUserDefault) private var appLayoutStyle: AppLayoutStyle = .list\n"
        "    @AppStorage(\"LCShowAppLabels\", store: LCUtils.appGroupUserDefault) private var showAppLabels: Bool = true\n\n"
        "    private let gridColumns = [\n"
        "        GridItem(.adaptive(minimum: 76, maximum: 100), spacing: 16, alignment: .top)\n"
        "    ]\n\n"
        "    @ViewBuilder\n"
        "    private func renderAppCollection(apps: [LCAppModel]) -> some View {\n"
        "        switch appLayoutStyle {\n"
        "        case .list:\n"
        "            LazyVStack {\n"
        "                ForEach(apps, id: \\.self) { app in\n"
        "                    LCAppBanner(appModel: app, delegate: self, layoutStyle: .list)\n"
        "                }\n"
        "                .transition(.scale)\n"
        "            }\n"
        "        case .compactList:\n"
        "            LazyVStack(spacing: 8) {\n"
        "                ForEach(apps, id: \\.self) { app in\n"
        "                    LCAppBanner(appModel: app, delegate: self, layoutStyle: .compactList)\n"
        "                }\n"
        "                .transition(.scale)\n"
        "            }\n"
        "        case .grid:\n"
        "            LazyVGrid(columns: gridColumns, spacing: 16) {\n"
        "                ForEach(apps, id: \\.self) { app in\n"
        "                    LCGridAppCell(appModel: app, delegate: self, showLabels: showAppLabels)\n"
        "                }\n"
        "                .transition(.scale)\n"
        "            }\n"
        "        }\n"
        "    }\n"
    )
    text = replace_once(text, prop_anchor, render_block, "LCAppListView render block insertion")

    body_list_anchor = (
        "                LazyVStack {\n"
        "                    ForEach(filteredApps, id: \\.self) { app in\n"
        "                        LCAppBanner(appModel: app, delegate: self)\n"
        "                    }\n"
        "                    .transition(.scale)\n"
        "                }\n"
        "                .padding()\n"
        "                .animation(searchContext.isTyping ? nil : .easeInOut, value: filteredApps)"
    )
    body_list_replacement = (
        "                renderAppCollection(apps: filteredApps)\n"
        "                .padding()\n"
        "                .animation(searchContext.isTyping ? nil : .easeInOut, value: filteredApps)"
    )
    text = replace_once(text, body_list_anchor, body_list_replacement, "LCAppListView main render call")

    hidden_apps_anchor = (
        "                                ForEach(filteredHiddenApps, id: \\.self) { app in\n"
        "                                    LCAppBanner(appModel: app, delegate: self)\n"
        "                                }\n"
        "                                .transition(.scale)"
    )
    hidden_apps_replacement = (
        "                                renderAppCollection(apps: filteredHiddenApps)"
    )
    text = replace_once(text, hidden_apps_anchor, hidden_apps_replacement, "LCAppListView hidden apps render call")
    path.write_text(text, encoding="utf-8")


def patch_livecontainer(root: Path) -> None:
    patch_livecontainer_model(root)
    patch_livecontainer_grid_cell(root)
    patch_livecontainer_settings(root)
    patch_livecontainer_banner_view(root)
    patch_livecontainer_app_list_view(root)


def patch_sidestore_defaults(root: Path) -> None:
    path = root / "AltStore" / "Core" / "Extensions" / "UserDefaults+AltStore.swift"
    text = path.read_text(encoding="utf-8")
    if MARKER_SIDESTORE in text:
        return

    prop_anchor = (
        "    @objc var useOnDeviceAnisette: Bool {\n"
        "        get { self.bool(forKey: #function) }\n"
        "        set { self.set(newValue, forKey: #function) }\n"
        "    }"
    )
    prop_replacement = (
        prop_anchor + "\n"
        "    " + MARKER_SIDESTORE + "\n"
        "    @objc var appLayoutStyle: String {\n"
        "        get { self.string(forKey: #function) ?? \"list\" }\n"
        "        set { self.set(newValue, forKey: #function) }\n"
        "    }\n"
        "    @objc var showAppLabels: Bool {\n"
        "        get { (self.object(forKey: #function) as? Bool) ?? true }\n"
        "        set { self.set(newValue, forKey: #function) }\n"
        "    }"
    )
    text = replace_once(text, prop_anchor, prop_replacement, "UserDefaults+AltStore properties")

    reg_anchor = "            #keyPath(UserDefaults.useOnDeviceAnisette): true,"
    reg_replacement = (
        reg_anchor + "\n"
        "            #keyPath(UserDefaults.appLayoutStyle): \"list\",\n"
        "            #keyPath(UserDefaults.showAppLabels): true,"
    )
    text = replace_once(text, reg_anchor, reg_replacement, "UserDefaults+AltStore default values")
    path.write_text(text, encoding="utf-8")


def patch_sidestore_customizations_view(root: Path) -> None:
    path = root / "SideStore" / "Views" / "Settings" / "Advanced" / "UserCustomizations" / "UserCustomizationsView.swift"
    text = path.read_text(encoding="utf-8")
    if MARKER_SIDESTORE in text:
        return

    state_anchor = "    @State private var useOnDeviceAnisette: Bool = UserDefaults.standard.useOnDeviceAnisette"
    state_replacement = (
        state_anchor + "\n"
        "    @State private var appLayoutStyle: String = UserDefaults.standard.appLayoutStyle\n"
        "    @State private var showAppLabels: Bool = UserDefaults.standard.showAppLabels"
    )
    text = replace_once(text, state_anchor, state_replacement, "UserCustomizationsView state vars")

    section_anchor = "                // Section 0: APPEARANCE & THEMES"
    section_block = (
        "                // Section: INTERFACE\n"
        "                " + MARKER_SIDESTORE + "\n"
        "                VStack(alignment: .leading, spacing: 8) {\n"
        "                    Text(\"INTERFACE\")\n"
        "                        .font(.system(size: 13, weight: .semibold))\n"
        "                        .foregroundColor(Color.white.opacity(0.6))\n"
        "                        .padding(.horizontal, 16)\n\n"
        "                    VStack(spacing: 0) {\n"
        "                        HStack {\n"
        "                            Text(\"App Layout\")\n"
        "                                .font(.system(size: 17, weight: .bold))\n"
        "                                .foregroundColor(.white)\n"
        "                            Spacer()\n"
        "                            Picker(\"App Layout\", selection: Binding(\n"
        "                                get: { appLayoutStyle },\n"
        "                                set: { newValue in\n"
        "                                    appLayoutStyle = newValue\n"
        "                                    UserDefaults.standard.appLayoutStyle = newValue\n"
        "                                }\n"
        "                            )) {\n"
        "                                Text(\"List\").tag(\"list\")\n"
        "                                Text(\"Grid\").tag(\"grid\")\n"
        "                                Text(\"Compact List\").tag(\"compactList\")\n"
        "                            }\n"
        "                            .pickerStyle(.menu)\n"
        "                            .tint(.white.opacity(0.7))\n"
        "                        }\n"
        "                        .padding(.horizontal, 16)\n"
        "                        .padding(.vertical, 10)\n"
        "                        .frame(minHeight: 50)\n\n"
        "                        if appLayoutStyle == \"grid\" {\n"
        "                            divider\n"
        "                            toggleRow(\n"
        "                                title: \"Show App Labels\",\n"
        "                                subtitle: \"Display app names below icons in grid view\",\n"
        "                                isOn: Binding(\n"
        "                                    get: { showAppLabels },\n"
        "                                    set: { newValue in\n"
        "                                        showAppLabels = newValue\n"
        "                                        UserDefaults.standard.showAppLabels = newValue\n"
        "                                    }\n"
        "                                )\n"
        "                            )\n"
        "                        }\n"
        "                    }\n"
        "                    .background(Color.settingsRowBackground)\n"
        "                    .cornerRadius(14)\n"
        "                }\n\n"
        "                // Section 0: APPEARANCE & THEMES"
    )
    text = replace_once(text, section_anchor, section_block, "UserCustomizationsView interface section")
    path.write_text(text, encoding="utf-8")


def patch_sidestore_app_banner(root: Path) -> None:
    path = root / "AltStore" / "Components" / "AppBannerView.swift"
    text = path.read_text(encoding="utf-8")
    if "compactHeight" not in text:
        height_anchor = "extension AppBannerView\n{\n    static let standardHeight = 88.0"
        height_replacement = (
            "extension AppBannerView\n"
            "{\n"
            "    static let standardHeight = 88.0\n"
            "    static let compactHeight = 56.0"
        )
        text = replace_once(text, height_anchor, height_replacement, "AppBannerView compactHeight")

        method_anchor = "private extension AppBannerView"
        method_addition = (
            "extension AppBannerView\n"
            "{\n"
            "    func applyLayoutStyle(_ style: String, showLabels: Bool = true)\n"
            "    {\n"
            "        if style == \"compactList\"\n"
            "        {\n"
            "            self.iconImageViewHeightConstraint?.constant = 40\n"
            "            self.subtitleLabel?.isHidden = true\n"
            "            self.stackView?.spacing = 6\n"
            "        }\n"
            "        else if style == \"grid\"\n"
            "        {\n"
            "            self.iconImageViewHeightConstraint?.constant = 50\n"
            "            self.subtitleLabel?.isHidden = true\n"
            "            self.button?.isHidden = true\n"
            "            self.titleLabel?.isHidden = !showLabels\n"
            "        }\n"
            "        else\n"
            "        {\n"
            "            self.iconImageViewHeightConstraint?.constant = 60\n"
            "            self.subtitleLabel?.isHidden = false\n"
            "            self.button?.isHidden = false\n"
            "            self.titleLabel?.isHidden = false\n"
            "            self.stackView?.spacing = 10\n"
            "        }\n"
            "    }\n"
            "}\n\n"
            "private extension AppBannerView"
        )
        text = replace_once(text, method_anchor, method_addition, "AppBannerView applyLayoutStyle")
        path.write_text(text, encoding="utf-8")


def patch_sidestore_my_apps(root: Path) -> None:
    path = root / "AltStore" / "My Apps" / "MyAppsViewController.swift"
    text = path.read_text(encoding="utf-8")
    if MARKER_SIDESTORE in text:
        return

    size_anchor = (
        "        case .activeApps, .inactiveApps:\n"
        "            return CGSize(width: collectionView.bounds.width, height: 88)"
    )
    size_replacement = (
        "        case .activeApps, .inactiveApps:\n"
        "            " + MARKER_SIDESTORE + "\n"
        "            let style = UserDefaults.standard.appLayoutStyle\n"
        "            if style == \"compactList\" {\n"
        "                return CGSize(width: collectionView.bounds.width, height: 56)\n"
        "            } else if style == \"grid\" {\n"
        "                let columns: CGFloat = 4\n"
        "                let spacing: CGFloat = 10\n"
        "                let totalSpacing = spacing * (columns + 1)\n"
        "                let itemWidth = floor((collectionView.bounds.width - totalSpacing) / columns)\n"
        "                let showLabels = UserDefaults.standard.showAppLabels\n"
        "                let itemHeight = showLabels ? itemWidth + 28 : itemWidth + 8\n"
        "                return CGSize(width: itemWidth, height: itemHeight)\n"
        "            } else {\n"
        "                return CGSize(width: collectionView.bounds.width, height: 88)\n"
        "            }"
    )
    text = replace_once(text, size_anchor, size_replacement, "MyAppsViewController sizeForItemAt")

    active_anchor = "            cell.bannerView.button.accessibilityLabel = String(format: NSLocalizedString(\"Refresh %@\", comment: \"\"), installedApp.name)\n"
    active_addition = (
        "            cell.bannerView.button.accessibilityLabel = String(format: NSLocalizedString(\"Refresh %@\", comment: \"\"), installedApp.name)\n"
        "            cell.bannerView.applyLayoutStyle(UserDefaults.standard.appLayoutStyle, showLabels: UserDefaults.standard.showAppLabels)\n"
    )
    text = replace_once(text, active_anchor, active_addition, "MyAppsViewController active cell style")

    inactive_anchor = "            cell.bannerView.button.accessibilityLabel = String(format: NSLocalizedString(\"Activate %@\", comment: \"\"), installedApp.name)\n"
    inactive_addition = (
        "            cell.bannerView.button.accessibilityLabel = String(format: NSLocalizedString(\"Activate %@\", comment: \"\"), installedApp.name)\n"
        "            cell.bannerView.applyLayoutStyle(UserDefaults.standard.appLayoutStyle, showLabels: UserDefaults.standard.showAppLabels)\n"
    )
    text = replace_once(text, inactive_anchor, inactive_addition, "MyAppsViewController inactive cell style")
    path.write_text(text, encoding="utf-8")


def patch_sidestore(root: Path) -> None:
    patch_sidestore_defaults(root)
    patch_sidestore_customizations_view(root)
    patch_sidestore_app_banner(root)
    patch_sidestore_my_apps(root)


def verify_livecontainer(root: Path) -> None:
    settings = (root / "LiveContainerSwiftUI" / "Views" / "Settings" / "LCSettingsView.swift").read_text(encoding="utf-8")
    app_list = (root / "LiveContainerSwiftUI" / "Views" / "AppList" / "LCAppListView.swift").read_text(encoding="utf-8")
    banner = (root / "LiveContainerSwiftUI" / "Views" / "AppList" / "LCAppBanner" / "LCAppBanner.swift").read_text(encoding="utf-8")
    grid_cell = (root / "LiveContainerSwiftUI" / "Views" / "AppList" / "LCGridAppCell.swift").read_text(encoding="utf-8")
    model = (root / "LiveContainerSwiftUI" / "Models" / "AppLayoutStyle.swift").read_text(encoding="utf-8")

    if MARKER_LIVE_CONTAINER not in settings:
        die("LiveContainer settings marker missing")
    if MARKER_LIVE_CONTAINER not in app_list:
        die("LiveContainer app list marker missing")
    if "layoutStyle" not in banner:
        die("LiveContainer app banner layoutStyle missing")
    if "accessibilityLabel(appModel.displayName)" not in grid_cell:
        die("LiveContainer grid cell accessibilityLabel missing")
    if "case compactList = \"compactList\"" not in model:
        die("LiveContainer AppLayoutStyle compactList case missing")

    compiler = shutil.which("swiftc")
    if compiler:
        for path in (
            root / "LiveContainerSwiftUI" / "Models" / "AppLayoutStyle.swift",
            root / "LiveContainerSwiftUI" / "Views" / "Settings" / "LCSettingsView.swift",
            root / "LiveContainerSwiftUI" / "Views" / "AppList" / "LCAppListView.swift",
            root / "LiveContainerSwiftUI" / "Views" / "AppList" / "LCGridAppCell.swift",
        ):
            subprocess.run([compiler, "-frontend", "-parse", str(path)], check=True)


def verify_sidestore(root: Path) -> None:
    defaults = (root / "AltStore" / "Core" / "Extensions" / "UserDefaults+AltStore.swift").read_text(encoding="utf-8")
    settings = (root / "SideStore" / "Views" / "Settings" / "Advanced" / "UserCustomizations" / "UserCustomizationsView.swift").read_text(encoding="utf-8")
    banner = (root / "AltStore" / "Components" / "AppBannerView.swift").read_text(encoding="utf-8")
    my_apps = (root / "AltStore" / "My Apps" / "MyAppsViewController.swift").read_text(encoding="utf-8")

    if MARKER_SIDESTORE not in defaults:
        die("SideStore defaults marker missing")
    if MARKER_SIDESTORE not in settings:
        die("SideStore UserCustomizationsView marker missing")
    if "applyLayoutStyle" not in banner:
        die("SideStore AppBannerView applyLayoutStyle missing")
    if MARKER_SIDESTORE not in my_apps:
        die("SideStore MyAppsViewController marker missing")

    compiler = shutil.which("swiftc")
    if compiler:
        for path in (
            root / "AltStore" / "Core" / "Extensions" / "UserDefaults+AltStore.swift",
            root / "SideStore" / "Views" / "Settings" / "Advanced" / "UserCustomizations" / "UserCustomizationsView.swift",
            root / "AltStore" / "Components" / "AppBannerView.swift",
            root / "AltStore" / "My Apps" / "MyAppsViewController.swift",
        ):
            subprocess.run([compiler, "-frontend", "-parse", str(path)], check=True)


def identify_target(path: Path) -> str:
    if (path / "LiveContainer.xcodeproj").exists() or (path / "LiveContainerSwiftUI").is_dir():
        return "livecontainer"
    if (path / "AltStore.xcodeproj").is_dir() or (path / "SideStore").is_dir():
        return "sidestore"
    die(f"unknown target checkout at {path}: neither LiveContainer nor SideStore detected")


def main() -> None:
    if len(sys.argv) < 2:
        die("usage: patch_app_layout.py <checkout-path> [<checkout-path-2> ...]")

    for arg in sys.argv[1:]:
        target_path = Path(arg).resolve()
        target_type = identify_target(target_path)
        if target_type == "livecontainer":
            patch_livecontainer(target_path)
            verify_livecontainer(target_path)
            print(f"Applied and verified App Layout patch on LiveContainer: {target_path}")
        elif target_type == "sidestore":
            patch_sidestore(target_path)
            verify_sidestore(target_path)
            print(f"Applied and verified App Layout patch on SideStore: {target_path}")


if __name__ == "__main__":
    main()
