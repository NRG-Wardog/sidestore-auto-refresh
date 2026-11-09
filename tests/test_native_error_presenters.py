"""Maintained presenter source contracts and executable Swift helper controls.

Source routing checks do not qualify UIKit/SwiftUI interaction on a device.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from native_error_evidence import SITES, SWIFT_ROOT, TWEAK_PATH, NATIVE_ERROR_SOURCE_PATHS
from owner_source import owner_source


def helper():
    text = owner_source(SWIFT_ROOT+'Utilities/ViewExtensions.swift')
    return text[text.index('enum LCNativeErrorSite:'):]


def verify_routes(sources):
    for site, (_, path) in SITES.items():
        text = sources[SWIFT_ROOT+path]
        if f'appendingDiagnostic(LCNativeErrorSite.{site}.rawValue)' not in text:
            raise ValueError('missing display site '+site)
        if not re.search(r'LCNativeErrorPresentation.details\([^\n]+, site: \.'+site+r'\)', text):
            raise ValueError('missing copy site '+site)
    tweak = sources[TWEAK_PATH]
    for token in ['Error ID: SS-NATIVE-TWEAK-LOAD', 'UIPasteboard.generalPasteboard.string = details;',
                  '[saved isKindOfClass:NSString.class]', '[saved length] == 40',
                  '^[0-9a-fA-F]{40}$', '[saved lowercaseString]', '@"unknown"']:
        if token not in tweak:
            raise ValueError('missing tweak contract '+token)


def verify_crash_copy(root):
    crash = root.split('.sheet(isPresented: $crashReportShow)', 1)[1].split('.onAppear', 1)[0]
    if 'copyError()' not in crash:
        raise ValueError('crash copy action disconnected')
    body = root.split('func copyError() {', 1)[1].split('}', 1)[0].strip()
    if body != 'UIPasteboard.general.string = errorInfo':
        raise ValueError('crash copy must preserve raw technical report without diagnostic label')


class NativePresenterOwnerTests(unittest.TestCase):
    def test_registry_and_helper_boundary(self):
        source = helper()
        self.assertEqual(dict(re.findall(r'case (\w+) = "([^"]+)"', source)),
                         {key: value[0] for key, value in SITES.items()})
        self.assertEqual(len(set(v[0] for v in SITES.values())), len(SITES))
        for token in ['localizedDescription', 'CombinedFailure']:
            self.assertNotIn(token, source)
        for token in ['saved.utf8.count == 40', '^[0-9a-fA-F]{40}$',
                      'saved.lowercased() : "unknown"', 'builder_commit=']:
            self.assertIn(token, source)
        self.assertEqual(len(NATIVE_ERROR_SOURCE_PATHS), 13)
        for name in ['combined_build_evidence.py', 'verify_candidate_ipa.py']:
            self.assertIn('*NATIVE_ERROR_SOURCE_PATHS', (ROOT/'scripts'/name).read_text())

    def test_current_routes_and_negative_controls(self):
        sources = {path: owner_source(path) for path in NATIVE_ERROR_SOURCE_PATHS}
        verify_routes(sources)
        for site, (_, path) in SITES.items():
            path = SWIFT_ROOT+path
            changed = dict(sources)
            changed[path] = changed[path].replace(f'LCNativeErrorSite.{site}.rawValue', 'LCNativeErrorSite.wrong.rawValue')
            with self.assertRaises(ValueError):
                verify_routes(changed)
            changed = dict(sources)
            changed[path] = changed[path].replace(f'site: .{site})', 'site: .wrong)')
            with self.assertRaises(ValueError):
                verify_routes(changed)
        changed = dict(sources)
        changed[TWEAK_PATH] = changed[TWEAK_PATH].replace('generalPasteboard.string = details;', 'generalPasteboard.string = error;')
        with self.assertRaises(ValueError):
            verify_routes(changed)

    def test_optional_errors_and_crash_report_are_separate(self):
        sources = owner_source(SWIFT_ROOT+'Views/LCAltStoreSourcesView.swift')
        self.assertIn('if let errorDisplay { V3LocalizedText(errorDisplay.appendingDiagnostic', sources)
        storage = owner_source(SWIFT_ROOT+'Views/Settings/DataManagement/LCStorageManagementSections.swift')
        self.assertIn('if let errorInfo {', storage)
        root = owner_source(SWIFT_ROOT+'Views/LCTabView.swift')
        crash = root.split('.sheet(isPresented: $crashReportShow)', 1)[1].split('.onAppear', 1)[0]
        self.assertIn('Text(verbatim: errorInfo)', crash)
        self.assertNotIn('LCNativeErrorPresentation', crash)
        verify_crash_copy(root)
        for changed in [root.replace('copyError()', 'wrongCopy()'),
                        root.replace('UIPasteboard.general.string = errorInfo',
                                     'UIPasteboard.general.string = LCNativeErrorPresentation.details(errorInfo, site: .root)')]:
            with self.assertRaises(ValueError):
                verify_crash_copy(changed)


class NativePresenterRuntimeTests(unittest.TestCase):
    def test_real_helper_and_negative_control(self):
        swift = shutil.which('swiftc')
        if not swift:
            raise RuntimeError('swiftc is required for maintained presenter runtime tests')
        harness = '\n'.join(
            f'precondition(LCNativeErrorPresentation.message("raw provider text", site: .{key}) == "raw provider text\\nError ID: {value[0]}")'
            for key, value in SITES.items())
        harness += '\nprecondition(LCNativeErrorPresentation.details("", site: .banner) == "\\nError ID: SS-NATIVE-BANNER\\nbuilder_commit=unknown")\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for broken in [False, True]:
                source = helper()
                if broken:
                    source = source.replace('site.rawValue', '"SS-NATIVE-WRONG"')
                (path/'main.swift').write_text('import Foundation\n'+source+'\n'+harness)
                subprocess.run([swift, str(path/'main.swift'), '-o', str(path/'probe')], check=True, capture_output=True, timeout=60)
                result = subprocess.run([str(path/'probe')], capture_output=True, timeout=15)
                self.assertEqual(result.returncode == 0, not broken)

    def test_all_maintained_swift_presenters_parse(self):
        swift = shutil.which('swiftc')
        if not swift:
            raise RuntimeError('swiftc is required for presenter syntax tests')
        with tempfile.TemporaryDirectory() as directory:
            for path in NATIVE_ERROR_SOURCE_PATHS:
                if not path.endswith('.swift'):
                    continue
                target = Path(directory)/Path(path).name
                target.write_text(owner_source(path))
                subprocess.run([swift, '-frontend', '-parse', str(target)], check=True, capture_output=True, timeout=60)


if __name__ == '__main__':
    unittest.main()
