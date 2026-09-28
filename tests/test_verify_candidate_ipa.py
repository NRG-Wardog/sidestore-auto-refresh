import importlib.util
import hashlib
from pathlib import Path
import sys
import tempfile
import struct
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "verify_candidate_ipa", ROOT / "scripts/verify_candidate_ipa.py")
verify_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_module)


def thin_arm64_macho(image_uuid=b"0123456789abcdef"):
    if len(image_uuid) != 16:
        raise ValueError("Mach-O UUID must contain 16 bytes")
    header = b"\xcf\xfa\xed\xfe" + struct.pack(
        "<7I", 0x0100000C, 0, 6, 1, 24, 0, 0)
    return header + struct.pack("<II", 0x1B, 24) + image_uuid


def fat_macho(slice_bytes, offset=None, architecture=0x0100000C):
    if offset is None:
        offset = 28
    header = b"\xca\xfe\xba\xbe" + struct.pack(">I", 1)
    arch = struct.pack(">IIIII", architecture, 0, offset, len(slice_bytes), 0)
    prefix = header + arch
    return prefix + bytes(max(0, offset - len(prefix))) + slice_bytes


class CandidateArchiveSizeReportTests(unittest.TestCase):
    def test_all_macho_members_including_standalone_dylibs_are_architecture_checked(self):
        arm64 = thin_arm64_macho()
        files = {
            "Payload/LiveContainer.app/LiveContainer": arm64,
            "Payload/LiveContainer.app/Frameworks/ZSign.dylib": arm64,
            "Payload/LiveContainer.app/Frameworks/TweakLoader.dylib": arm64,
            "Payload/LiveContainer.app/Frameworks/libswiftCore.dylib": arm64,
            "SwiftSupport/Unexpected.dylib": arm64,
            "Payload/LiveContainer.app/Resources/Example.class":
                b"\xca\xfe\xba\xbe" + struct.pack(">HHH", 0, 61, 1),
            "Payload/LiveContainer.app/Assets.car": b"not-a-macho",
        }
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "macho-scan.ipa"
            with zipfile.ZipFile(ipa, "w") as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            with zipfile.ZipFile(ipa) as archive:
                paths = verify_module.mach_o_paths(archive, archive.infolist())
                self.assertEqual(paths, set(files) - {
                    "Payload/LiveContainer.app/Assets.car",
                    "Payload/LiveContainer.app/Resources/Example.class",
                })
                for path in paths:
                    self.assertIn("arm64", verify_module.architectures(archive.read(path)), path)
                    self.assertTrue(verify_module.macho_uuids(archive.read(path)), path)
                report = verify_module.archive_size_report(archive.infolist(), paths)
        breakdown = report["payload_breakdown_bytes"]
        self.assertEqual(breakdown["executables"], len(arm64) * 4)
        self.assertEqual(breakdown["swift_runtime_dylibs"], len(arm64))

    def test_malformed_or_truncated_macho_headers_and_fat_slices_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "truncated Mach-O header"):
            verify_module.architectures(b"\xcf\xfa\xed\xfe" + struct.pack("<I", 0x0100000C))
        bad_command = bytearray(thin_arm64_macho())
        struct.pack_into("<I", bad_command, 36, 4)
        with self.assertRaisesRegex(ValueError, "invalid Mach-O load-command size"):
            verify_module.architectures(bytes(bad_command))
        valid_fat = fat_macho(thin_arm64_macho())
        self.assertEqual(verify_module.architectures(valid_fat), {"arm64"})
        out_of_range = b"\xca\xfe\xba\xbe" + struct.pack(">I", 1) + struct.pack(
            ">IIIII", 0x0100000C, 0, 4096, len(thin_arm64_macho()), 0)
        with self.assertRaisesRegex(ValueError, "outside the file"):
            verify_module.architectures(out_of_range)
        with self.assertRaisesRegex(ValueError, "does not match"):
            verify_module.architectures(fat_macho(thin_arm64_macho(), architecture=0x01000007))

    def test_provenance_run_url_must_match_exact_github_actions_repo_and_shape(self):
        good = "https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/36372125879"
        self.assertTrue(verify_module.is_github_actions_run_url(good))
        self.assertFalse(verify_module.is_github_actions_run_url("https://github.com/"))
        self.assertFalse(verify_module.is_github_actions_run_url(
            "https://github.com/other/repo/actions/runs/36372125879"))
        self.assertFalse(verify_module.is_github_actions_run_url(good + "?query=1"))

    def test_duplicate_zip_members_are_rejected_instead_of_last_entry_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "duplicates.ipa"
            with zipfile.ZipFile(ipa, "w") as archive:
                archive.writestr("Payload/LiveContainer.app/Example.dylib", b"x86-first")
                archive.writestr("Payload/LiveContainer.app/Example.dylib", b"arm64-second")
            with zipfile.ZipFile(ipa) as archive:
                with self.assertRaisesRegex(ValueError, "duplicate ZIP member"):
                    verify_module.require_unique_archive_member_names(archive.infolist())

    def test_generated_source_hashes_and_preserved_dsym_uuid_are_verified(self):
        image = thin_arm64_macho()
        image_uuid = verify_module.macho_uuids(image)["arm64"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = root / "generated" / "LiveContainerSwiftUI" / "Views"
            generated.mkdir(parents=True)
            generated_file = generated / "V3UnifiedShell.swift"
            generated_file.write_text("struct CandidateShell {}", encoding="utf-8")
            generated_hash = hashlib.sha256(generated_file.read_bytes()).hexdigest()
            embedded = root / "embedded-generated" / "SideStore" / "Core" / "Operations"
            embedded.mkdir(parents=True)
            embedded_file = embedded / "PipelineRunner.swift"
            embedded_file.write_text("struct CandidatePipeline {}", encoding="utf-8")
            embedded_hash = hashlib.sha256(embedded_file.read_bytes()).hexdigest()
            verify_module.verify_generated_source_evidence(root, {
                "LiveContainerSwiftUI/Views/V3UnifiedShell.swift": generated_hash,
                "embedded/SideStore/Core/Operations/PipelineRunner.swift": embedded_hash,
            })
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                verify_module.verify_generated_source_evidence(root, {
                    "LiveContainerSwiftUI/Views/V3UnifiedShell.swift": "0" * 64,
                })
            host_symbols = root / "host" / "SideStoreSupport.framework.dSYM" / "Contents" / "Resources" / "DWARF"
            host_symbols.mkdir(parents=True)
            (host_symbols / "SideStoreSupport").write_bytes(image)
            self.assertEqual(verify_module.preserved_dsym_uuids(root, {image_uuid}),
                             {"SideStoreSupport": image_uuid})

    def test_host_and_liveprocess_require_both_shared_app_groups(self):
        required = verify_module.REQUIRED_LIVECONTAINER_GROUPS
        self.assertTrue(verify_module.has_required_livecontainer_groups(required))
        self.assertFalse(verify_module.has_required_livecontainer_groups(
            {verify_module.REQUIRED_GROUP}),
            "a SideStore-only entitlement cannot preserve an AltStore-origin LC container selection")

    def test_host_and_liveprocess_must_share_the_dedicated_keychain_handoff_group(self):
        group = "AAAAA11111.com.kdt.livecontainer.shared"
        self.assertEqual(verify_module.verify_shared_secret_handoff_group([group], [group]), group)
        with self.assertRaisesRegex(ValueError, "dedicated entitled Keychain group"):
            verify_module.verify_shared_secret_handoff_group(
                ["group.com.SideStore.SideStore"], [group])

    def test_asset_catalog_rejects_removed_alternate_icons_and_keeps_primary_icon(self):
        good = [{"Name": "AppIcon"}, {"Name": "Classic"}, {"Name": "Modern"}, {"Name": "SettingsGear"}]
        report = verify_module.verify_side_store_assetutil_records(good)
        self.assertEqual(report["alternate_icon_sets"], "11 alternate app icons absent; Classic/Modern previews retained")
        self.assertEqual(report["appicon_named_asset_name_count"], 1)
        self.assertTrue(report["primary_app_icon_present"])
        self.assertEqual(verify_module.side_store_primary_icon_report(report), {
            "assets_car_record_present": True, "named_appicon_asset_count": 1})
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

    def test_side_store_package_rejects_legacy_intent_resources_and_code(self):
        prefix = "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework"
        forbidden = [
            prefix + "/Metadata.appintents/root.ssu.yaml",
            prefix + "/ViewApp.intentdefinition",
            prefix + "/Intents.intentdefinition",
        ]
        self.assertEqual(verify_module.find_legacy_side_store_resources(prefix, forbidden),
                         sorted(forbidden))
        executable = b"SideStore\x00RefreshAllAppsIntent\x00ShortcutsProvider\x00IntentHandler\x00"
        self.assertEqual(verify_module.find_legacy_side_store_intent_symbols(executable),
                         ["IntentHandler"])
        self.assertEqual(verify_module.find_legacy_side_store_intent_info_keys({
            "INIntentsSupported": ["RefreshAllIntent"],
            "NSUserActivityTypes": ["com.example.legacy"],
        }), ["INIntentsSupported", "NSUserActivityTypes"])
        self.assertEqual(verify_module.find_legacy_side_store_intent_info_keys({}), [])
        retained_ui_markers = ("ResignAltStoreViewController", "NewsCollectionViewCell", "AppIDsViewController")
        encoded_ui_markers = b"\x00".join(
            f"$s9SideStore{len(symbol)}{symbol}C".encode("utf-8") for symbol in retained_ui_markers)
        self.assertEqual(verify_module.find_legacy_side_store_ui_symbols(encoded_ui_markers),
                         list(retained_ui_markers))
        excluded_ui_symbols = (
            "SourceComponents", "SourceHeaderView", "AppInfoView", "CertificatesView",
            "DeveloperServicesView", "HealthCheckView", "StorageExplorerView",
            "AuthenticationViewController", "InstructionsViewController",
            "SelectTeamViewController", "MyAppsViewController", "SettingsViewController",
            "LaunchViewController", "HeaderContentViewController", "NavigationBarAppearance",
            "AddSourceViewController", "AltAppIconsViewController", "PatreonViewController",
            "LicensesViewController", "RefreshAttemptsViewController", "ErrorDetailsViewController",
            "ErrorLogTableViewCell", "ErrorLogViewController", "InstalledAppsCollectionHeaderView",
            "UpdateCollectionViewCell",
        )
        encoded_symbols = b"\x00".join(
            f"$s9SideStore{len(symbol)}{symbol}V".encode("utf-8")
            for symbol in excluded_ui_symbols)
        self.assertEqual(set(verify_module.find_legacy_side_store_ui_symbols(encoded_symbols)),
                         set(excluded_ui_symbols))
        self.assertEqual(verify_module.find_legacy_side_store_ui_symbols(b"SideStore"), [])
        framework_collisions = (b"UIActivityViewController UIDocumentPickerViewController "
            b"UINavigationBarAppearance UITabBarController Nuke.RoundedCorners")
        self.assertEqual(verify_module.find_legacy_side_store_ui_symbols(framework_collisions), [])
        self.assertEqual(verify_module.missing_excluded_ui_symbols(
            b"$s9SideStore13RoundedCornerV", ["RoundedCorner"]), ["RoundedCorner"])
        self.assertEqual(verify_module.missing_excluded_ui_symbols(
            b"Nuke.RoundedCorners", ["RoundedCorner"]), [])

    def test_host_background_configuration_requires_processing_and_fetch(self):
        self.assertEqual(verify_module.missing_required_background_modes({
            "UIBackgroundModes": ["processing", "fetch"]}), [])
        self.assertEqual(verify_module.missing_required_background_modes({
            "UIBackgroundModes": ["processing"]}), ["fetch"])
        self.assertEqual(verify_module.missing_required_background_modes({}),
                         ["fetch", "processing"])

    def test_livecontainer_shared_requires_prepared_dead10cc_patch_marker(self):
        marker = verify_module.REQUIRED_DEAD10CC_MARKER
        self.assertTrue(verify_module.has_required_dead10cc_marker(b"MachO\x00" + marker))
        self.assertFalse(verify_module.has_required_dead10cc_marker(b"MachO"))


if __name__ == "__main__":
    unittest.main()
