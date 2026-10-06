#!/usr/bin/env python3
"""Label pinned LC error presenters without reclassifying causes or rewriting prose.

IDs identify unknown failures at a presentation site, not their underlying cause.
Guest launch remains owned by patch_guest_return.py (SS-GUEST-EXIT/UNKNOWN).
Legacy sources/root are covered even when excluded or replaced by the V3 shell.
Existing provider prose is preserved for the separate wording/privacy work.
"""
from pathlib import Path
import subprocess
import sys

PIN = "12377cf3b91d51739a33f14a302e5f522b238593"
MARKER = "LC_NATIVE_ERROR_PRESENTERS_V1"
SWIFT_ROOT = "LiveContainerSwiftUI/"
# Finite registry: no identifiers are derived from error strings or user data.
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
TOUCHED_PATHS = tuple(SWIFT_ROOT + value[1] for value in SITES.values()) + (TWEAK_PATH,)
HELPER = '''
// LC_NATIVE_ERROR_PRESENTERS_V1: site identity only, with unknown underlying cause.
enum LCNativeErrorSite: String {
''' + "".join(f'    case {key} = "{value[0]}"\n' for key, value in SITES.items()) + r'''}
enum LCNativeErrorPresentation {
    static func message(_ original: String, site: LCNativeErrorSite) -> String {
        original + "\nError ID: " + site.rawValue
    }
    static func details(_ original: String, site: LCNativeErrorSite) -> String {
        let saved = Bundle.main.object(forInfoDictionaryKey: "LCBuilderCommit") as? String ?? ""
        let commit = saved.utf8.count == 40 && saved.range(of: "^[0-9a-fA-F]{40}$", options: .regularExpression) != nil
            ? saved.lowercased() : "unknown"
        return message(original, site: site) + "\nbuilder_commit=" + commit
    }
}
'''


TWEAK_INSERT = r'''
    // LC_NATIVE_ERROR_PRESENTERS_V1
    error = [NSString stringWithFormat:@"%@\nError ID: SS-NATIVE-TWEAK-LOAD", error];
    id saved = [NSBundle.mainBundle objectForInfoDictionaryKey:@"LCBuilderCommit"];
    NSString *commit = [saved isKindOfClass:NSString.class] && [saved length] == 40 &&
        [saved rangeOfString:@"^[0-9a-fA-F]{40}$" options:NSRegularExpressionSearch].location != NSNotFound
        ? [saved lowercaseString] : @"unknown";
    NSString *details = [NSString stringWithFormat:@"%@\nbuilder_commit=%@", error, commit];'''


def replace(text, old, new, count=1):
    """Fail closed for source drift, including partially applied patches."""
    if text.count(old) != count:
        raise ValueError(f"native presenter anchor drift: {old!r}")
    return text.replace(old, new)


def transform(path, text):
    if path == TWEAK_PATH:
        anchor = 'static void showDlerrAlert(NSString *error) {'
        text = replace(text, anchor, anchor + TWEAK_INSERT)
        return replace(text, 'UIPasteboard.generalPasteboard.string = error;',
                       'UIPasteboard.generalPasteboard.string = details;')
    site = next(key for key, (_, rel) in SITES.items() if SWIFT_ROOT + rel == path)
    if site == "banner":
        text = replace(text, '    func showError(_ message: String) {',
                       '    func showError(_ message: String) {\n        let details = LCNativeErrorPresentation.details(message, site: .banner)\n        let message = LCNativeErrorPresentation.message(message, site: .banner)')
        text = replace(text, 'UIPasteboard.general.string = message', 'UIPasteboard.general.string = details')
        return text + HELPER
    if site == "source":
        text = replace(text, 'Text(errorMessage ?? "")',
                       'Text(errorMessage.map { LCNativeErrorPresentation.message($0, site: .source) } ?? "")')
        text = replace(text, 'Text(error)', 'Text(LCNativeErrorPresentation.message(error, site: .source))')
    elif site == "root":
        # Only the error alert, never the independent crash-report sheet or its copy action.
        text = replace(text, '            Text(errorInfo)\n        }\n        .sheet(isPresented: $crashReportShow)',
                       '            Text(LCNativeErrorPresentation.message(errorInfo, site: .root))\n        }\n        .sheet(isPresented: $crashReportShow)')
        text = replace(text, '                copyError()\n            })',
                       '                UIPasteboard.general.string = LCNativeErrorPresentation.details(errorInfo, site: .root)\n            })')
    else:
        text = replace(text, 'Text(errorInfo)', f'Text(LCNativeErrorPresentation.message(errorInfo, site: .{site}))')
        if site == "appList":
            text = replace(text, 'UIPasteboard.general.string = errorInfo',
                           'UIPasteboard.general.string = LCNativeErrorPresentation.details(errorInfo, site: .appList)')
    return text + '\n// ' + MARKER + '\n'


def patch(root):
    # Prepare every file before writing any, so missing/drifted anchors fail atomically.
    prepared = {}
    for path in TOUCHED_PATHS:
        text = (root / path).read_text()
        if MARKER in text:
            # Exact reverse/reapply comparison guards against marker-only bypasses.
            verify_transformed(path, text)
        else:
            text = transform(path, text)
        prepared[path] = text
    for path, text in prepared.items():
        (root / path).write_text(text)


def verify_transformed(path, text):
    if text.count(MARKER) != 1:
        raise ValueError(f"native presenter marker drift: {path}")
    if path == TWEAK_PATH:
        original = replace(text, TWEAK_INSERT, '')
        original = replace(original, 'UIPasteboard.generalPasteboard.string = details;',
                           'UIPasteboard.generalPasteboard.string = error;')
    elif path.endswith('Utilities/ViewExtensions.swift'):
        original = replace(text, HELPER, '')
        original = replace(original, '    func showError(_ message: String) {\n        let details = LCNativeErrorPresentation.details(message, site: .banner)\n        let message = LCNativeErrorPresentation.message(message, site: .banner)', '    func showError(_ message: String) {')
        original = replace(original, 'UIPasteboard.general.string = details', 'UIPasteboard.general.string = message')
    else:
        original = replace(text, '\n// ' + MARKER + '\n', '')
        import re
        original = re.sub(r'LCNativeErrorPresentation.message\(errorInfo, site: \.[A-Za-z]+\)', 'errorInfo', original)
        original = original.replace('errorMessage.map { LCNativeErrorPresentation.message($0, site: .source) } ?? ""', 'errorMessage ?? ""')
        original = original.replace('LCNativeErrorPresentation.message(error, site: .source)', 'error')
        original = original.replace('LCNativeErrorPresentation.details(errorInfo, site: .appList)', 'errorInfo')
        original = original.replace('UIPasteboard.general.string = LCNativeErrorPresentation.details(errorInfo, site: .root)', 'copyError()')
    if transform(path, original) != text:
        raise ValueError(f"native presenter generated content drift: {path}")


if __name__ == '__main__':
    root = Path(sys.argv[1])
    if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() != PIN:
        raise SystemExit('native presenter source does not match the LC pin')
    patch(root)
    print('Native error presenters labeled and verified')
