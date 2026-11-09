"""Finite source-evidence inventory for maintained native error presenters.

No source transformation: implementations belong to LiveContainer.
"""
SWIFT_ROOT = "LiveContainerSwiftUI/"
SITES = {
    "appList": ("SS-NATIVE-APP-LIST", "Views/AppList/LCAppListView.swift"),
    "banner": ("SS-NATIVE-BANNER", "Utilities/ViewExtensions.swift"),
    "appSettings": ("SS-NATIVE-APP-SETTINGS", "Views/AppList/AppSettings/LCAppSettingsView.swift"),
    "container": ("SS-NATIVE-CONTAINER", "Views/AppList/AppSettings/LCContainerView.swift"),
    "storage": ("SS-NATIVE-STORAGE", "Views/Settings/DataManagement/LCStorageManagementSections.swift"),
    "dataManagement": ("SS-NATIVE-DATA", "Views/Settings/DataManagement/LCDataManagementView.swift"),
    "settings": ("SS-NATIVE-SETTINGS", "Views/Settings/LCSettingsView.swift"),
    "signingDiagnostics": ("SS-NATIVE-SIGNING", "Views/Settings/LCJITLessDiagnoseView.swift"),
    "source": ("SS-NATIVE-SOURCE", "Views/LCAltStoreSourcesView.swift"),
    "tweaks": ("SS-NATIVE-TWEAKS", "Views/LCTweaksView.swift"),
    "webDownload": ("SS-NATIVE-DOWNLOAD", "Views/AppList/LCWebView.swift"),
    "root": ("SS-NATIVE-ROOT", "Views/LCTabView.swift"),
}
TWEAK_PATH = "TweakLoader/TweakLoader.m"
NATIVE_ERROR_SOURCE_PATHS = tuple(SWIFT_ROOT + value[1] for value in SITES.values()) + (TWEAK_PATH,)
