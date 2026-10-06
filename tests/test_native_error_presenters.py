"""Exact pinned transforms for finite native error presentation IDs."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_presenters', ROOT / 'scripts/patch_native_error_presenters.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NativePresenterContractTests(unittest.TestCase):
    def test_finite_registry_and_unknown_semantics(self):
        codes = [value[0] for value in module.SITES.values()] + ['SS-NATIVE-TWEAK-LOAD']
        self.assertEqual(len(codes), len(set(codes)))
        for code in codes:
            self.assertRegex(code, r'^SS-NATIVE-[A-Z-]+$')
        self.assertNotIn('localizedDescription', module.HELPER)
        self.assertIn('saved.utf8.count == 40', module.HELPER)
        self.assertIn('site: LCNativeErrorSite', module.HELPER)
        self.assertIn('builder_commit=', module.HELPER)

    def test_workflow_and_provenance(self):
        workflow = (ROOT / '.github/workflows/livecontainer-build.yml').read_text()
        command = 'python3 builder/scripts/patch_native_error_presenters.py work/LiveContainer'
        self.assertEqual(workflow.count(command), 2)
        self.assertGreater(workflow.index(command), workflow.rindex('python3 builder/scripts/patch_v3_service.py work/LiveContainer'))
        evidence = (ROOT / 'scripts/combined_build_evidence.py').read_text()
        self.assertIn('*NATIVE_ERROR_SOURCE_PATHS', evidence)
        self.assertNotIn('CombinedFailure', module.HELPER)  # No SideStore/headless-only dependency.


class PinnedNativePresenterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value = os.environ.get('LIVE_CONTAINER_TEST_SOURCE')
        if not value:
            raise unittest.SkipTest('LIVE_CONTAINER_TEST_SOURCE is not set')
        cls.source = Path(value)
        pin = subprocess.check_output(['git', '-C', str(cls.source), 'rev-parse', 'HEAD'], text=True).strip()
        if pin != module.PIN:
            raise AssertionError('wrong LC source pin')
        cls.originals = {path: subprocess.check_output(['git', '-C', str(cls.source), 'show', 'HEAD:' + path], text=True)
                         for path in module.TOUCHED_PATHS}

    def fixture(self, directory):
        root = Path(directory)
        for path, text in self.originals.items():
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text)
        return root

    def test_exact_pinned_presenters_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            module.patch(root)
            first = {path: (root / path).read_text() for path in self.originals}
            module.patch(root)
            self.assertEqual(first, {path: (root / path).read_text() for path in first})
            for path, text in first.items():
                module.verify_transformed(path, text)
                # Raw upstream details are neither expanded nor reclassified here.
                self.assertEqual(text.count('localizedDescription'), self.originals[path].count('localizedDescription'))
                self.assertEqual(text.count('Text(successInfo)'), self.originals[path].count('Text(successInfo)'))
            root_view = first['LiveContainerSwiftUI/Views/LCTabView.swift']
            self.assertIn('                    Text(errorInfo)\n', root_view)  # Crash-report body untouched.
            self.assertIn('    func copyError() {\n        UIPasteboard.general.string = errorInfo', root_view)
            source = first['LiveContainerSwiftUI/Views/LCAltStoreSourcesView.swift']
            self.assertIn('errorMessage.map {', source)  # nil stays empty, not an error label.
            storage = first['LiveContainerSwiftUI/Views/Settings/DataManagement/LCStorageManagementSections.swift']
            self.assertIn('if let errorInfo {', storage)  # nil storage state remains unlabelled.
            if shutil.which('swiftc'):
                for path in first:
                    if path.endswith('.swift'):
                        subprocess.run(['swiftc', '-frontend', '-parse', str(root / path)], check=True)

    def test_composes_after_actual_shell_and_service_transforms(self):
        if not os.environ.get('EMBEDDED_SIDESTORE_TEST_SOURCE'):
            self.skipTest('EMBEDDED_SIDESTORE_TEST_SOURCE is not set')
        from test_v3_service import ServicePatchTests
        fixture = ServicePatchTests()
        with tempfile.TemporaryDirectory() as directory:
            roots = fixture.fixture(Path(directory))
            fixture.apply(roots)
            root = roots[0]
            for path, original in self.originals.items():
                destination = root / path
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(original)
            module.patch(root)
            first = {path: (root / path).read_bytes() for path in self.originals}
            module.patch(root)
            self.assertEqual(first, {path: (root / path).read_bytes() for path in first})
            self.assertIn('LCNativeErrorPresentation.message(errorInfo, site: .settings)',
                          (root / 'LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift').read_text())

    def test_drift_fails_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            last = root / module.TWEAK_PATH
            last.write_text(last.read_text().replace('static void showDlerrAlert', 'static void changedAlert'))
            before = {path: (root / path).read_bytes() for path in self.originals}
            with self.assertRaises(ValueError):
                module.patch(root)
            self.assertEqual(before, {path: (root / path).read_bytes() for path in before})

    def test_marker_does_not_hide_generated_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            module.patch(root)
            path = root / 'LiveContainerSwiftUI/Views/AppList/LCAppListView.swift'
            path.write_text(path.read_text().replace('site: .appList', 'site: .settings'))
            with self.assertRaises(ValueError):
                module.patch(root)


if __name__ == '__main__':
    unittest.main()
