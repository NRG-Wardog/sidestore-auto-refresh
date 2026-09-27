import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "verify_candidate_ipa", ROOT / "scripts/verify_candidate_ipa.py")
verify_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_module)


class CandidateArchiveSizeReportTests(unittest.TestCase):
    def test_asset_catalog_rejects_removed_alternate_icons_and_keeps_primary_icon(self):
        good = [{"Name": "AppIcon"}, {"Name": "Classic"}, {"Name": "Modern"}, {"Name": "SettingsGear"}]
        report = verify_module.verify_side_store_assetutil_records(good)
        self.assertEqual(report["alternate_icon_sets"], "11 alternate app icons absent; Classic/Modern previews retained")
        self.assertEqual(report["appicon_named_asset_name_count"], 1)
        self.assertTrue(report["primary_app_icon_present"])
        with self.assertRaisesRegex(ValueError, "primary SideStore AppIcon is missing"):
            verify_module.verify_side_store_assetutil_records(
                [{"Name": "Classic"}, {"Name": "Modern"}])
        forbidden_names = sorted(verify_module.REMOVED_SIDESTORE_ICON_NAMES)
        for forbidden in forbidden_names:
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(ValueError, "alternate-icon assets remain"):
                    verify_module.verify_side_store_assetutil_records(good + [{"Name": forbidden}])

    def test_size_report_partitions_files_and_ranks_largest_members(self):
        files = {
            "Payload/LiveContainer.app/LiveContainer": b"h" * 100,
            "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore": b"s" * 50,
            "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Assets.car": b"a" * 30,
            "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Base.lproj/Main.storyboardc/Info.plist": b"b" * 20,
            "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideBackup.ipa": b"z" * 25,
            "Payload/LiveContainer.app/Frameworks/libswiftCore.dylib": b"w" * 15,
            "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Images/icon.png": b"p" * 12,
            "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Fonts/regular.ttf": b"f" * 9,
            "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Sounds/silence.m4a": b"m" * 8,
            "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/en.lproj/Localizable.strings": b"l" * 7,
            "Payload/LiveContainer.app/PlugIns/LiveProcess.appex/LiveProcess": b"p" * 10,
            "Payload/LiveContainer.app/Info.plist": b"i" * 5,
        }
        files.update({
            f"Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Other/file-{index}.dat":
                bytes([index]) * (index % 4 + 1)
            for index in range(20)
        })
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "candidate.ipa"
            with zipfile.ZipFile(ipa, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            with zipfile.ZipFile(ipa) as archive:
                expected_compressed_bytes = sum(
                    info.compress_size for info in archive.infolist() if not info.is_dir())
                report = verify_module.archive_size_report(
                    archive.infolist(),
                    {
                        "Payload/LiveContainer.app/LiveContainer",
                        "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore",
                        "Payload/LiveContainer.app/PlugIns/LiveProcess.appex/LiveProcess",
                    })
        breakdown = report["payload_breakdown_bytes"]
        self.assertEqual(report["uncompressed_bytes"], sum(map(len, files.values())))
        self.assertEqual(report["file_count"], len(files))
        self.assertEqual(sum(breakdown.values()), report["uncompressed_bytes"])
        self.assertEqual(report["zip_member_bytes"], expected_compressed_bytes)
        self.assertEqual(breakdown["executables"], 160)
        self.assertEqual(breakdown["nested_archives"], 25)
        self.assertEqual(breakdown["swift_runtime_dylibs"], 15)
        self.assertEqual(breakdown["Assets.car"], 30)
        self.assertEqual(breakdown["storyboards_and_nibs"], 20)
        self.assertEqual(breakdown["images"], 12)
        self.assertEqual(breakdown["fonts"], 9)
        self.assertEqual(breakdown["audio_and_video"], 8)
        self.assertEqual(breakdown["localizations"], 7)
        self.assertEqual(breakdown["metadata_and_signing"], 5)
        self.assertEqual(breakdown["framework_payload_excluding_executables"], 50)
        self.assertEqual(breakdown["other_files"], 0)
        expected_largest = sorted(files, key=lambda path: (-len(files[path]), path))[:20]
        self.assertEqual([item["path"] for item in report["largest_files"]], expected_largest)
        self.assertEqual(len(report["largest_files"]), 20)
        self.assertIn("inclusive_parent_bundles", report["bundle_totals_semantics"])
        self.assertEqual(report["bundle_totals_bytes"]["Payload/LiveContainer.app/Frameworks/SideStoreApp.framework"],
                         sum(len(value) for path, value in files.items()
                             if "/Frameworks/SideStoreApp.framework/" in path))

    def test_side_store_package_rejects_legacy_ui_and_audio_members(self):
        prefix = "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework"
        forbidden = [
            prefix + "/Main.storyboardc/Info.plist",
            prefix + "/Legacy.nib/keyedobjects.nib",
            prefix + "/Views/OldView.xib",
            prefix + "/Resources/Silence.m4a",
        ]
        self.assertEqual(verify_module.find_legacy_side_store_resources(prefix, forbidden),
                         sorted(forbidden))
        self.assertEqual(verify_module.find_legacy_side_store_resources(
            prefix, [prefix + "/SideStore", prefix + "/Assets.car"]), [])


if __name__ == "__main__":
    unittest.main()
