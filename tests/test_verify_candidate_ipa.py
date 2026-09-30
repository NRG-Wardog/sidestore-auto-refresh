import importlib.util
import inspect
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import struct
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_ipa_signing import signing as inspect_signing
import patch_v3_service

spec = importlib.util.spec_from_file_location(
    "verify_candidate_ipa", ROOT / "scripts/verify_candidate_ipa.py")
verify_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_module)
evidence_spec = importlib.util.spec_from_file_location(
    "combined_build_evidence", ROOT / "scripts/combined_build_evidence.py")
evidence_module = importlib.util.module_from_spec(evidence_spec)
evidence_spec.loader.exec_module(evidence_module)


def thin_arm64_macho(image_uuid=b"0123456789abcdef", subtype=0, include_uuid=True):
    if len(image_uuid) != 16:
        raise ValueError("Mach-O UUID must contain 16 bytes")
    header = b"\xcf\xfa\xed\xfe" + struct.pack(
        "<7I", 0x0100000C, subtype, 6, int(include_uuid), 24 if include_uuid else 0, 0, 0)
    return header + (struct.pack("<II", 0x1B, 24) + image_uuid if include_uuid else b"")


def fat_macho(slice_bytes, offset=None, architecture=0x0100000C, subtype=0):
    if offset is None:
        offset = 28
    header = b"\xca\xfe\xba\xbe" + struct.pack(">I", 1)
    arch = struct.pack(">IIIII", architecture, subtype, offset, len(slice_bytes), 0)
    prefix = header + arch
    return prefix + bytes(max(0, offset - len(prefix))) + slice_bytes


def fat64_macho(slice_bytes, offset=40, align=0, reserved=0):
    header = b"\xca\xfe\xba\xbf" + struct.pack(">I", 1)
    arch = struct.pack(">IIQQII", 0x0100000C, 0, offset, len(slice_bytes), align, reserved)
    prefix = header + arch
    return prefix + bytes(max(0, offset - len(prefix))) + slice_bytes


def java_class_file():
    # Minimal structurally valid class: constant-pool Class entry, no members.
    return (b"\xca\xfe\xba\xbe" + struct.pack(">HHH", 0, 61, 3) +
            b"\x07\x00\x02\x01\x00\x01A" +
            struct.pack(">7H", 0x21, 1, 0, 0, 0, 0, 0))


def write_zip_with_central_entries(path, count):
    entry = (struct.pack(
        "<4s6H3I5H2I", b"PK\x01\x02", 20, 20, 0, 0, 0, 0,
        0, 0, 0, 1, 0, 0, 0, 0, 0, 0) + b"x")
    with path.open("wb") as archive:
        for _ in range(count):
            archive.write(entry)
        directory_size = count * len(entry)
        archive.write(struct.pack(
            "<4s4H2LH", b"PK\x05\x06", 0, 0, count, count,
            directory_size, 0, 0))


class ExcludedSideStorePipelineUITests(unittest.TestCase):
    def test_generated_backend_connection_type_is_not_mistaken_for_removed_swiftui_model(self):
        relative = "Views/Settings/Advanced/Connection/ConnectionConfig.swift"
        tracked = "SideStore/" + relative
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            retired = root / tracked
            backend = root / "SideStore/Core/DeviceApi/ConnectionConfig.swift"
            retired.parent.mkdir(parents=True)
            backend.parent.mkdir(parents=True)
            retired.write_text("// V3_HEADLESS_CONNECTION_CONFIG_MOVED_V1: transport settings now live "
                               "in Core/DeviceApi/ConnectionConfig.swift.\n", encoding="utf-8")
            backend.write_text(patch_v3_service.HEADLESS_BACKEND_CONNECTION_CONFIG + "\n",
                               encoding="utf-8")

            def pinned_source(command, **kwargs):
                if "ls-tree" in command:
                    return tracked + "\n"
                if "show" in command:
                    return "final class ConnectionConfig: ObservableObject {}\n"
                raise AssertionError(command)

            with mock.patch.object(verify_module.subprocess, "check_output", side_effect=pinned_source):
                symbols = verify_module.excluded_side_store_view_type_names(
                    root, (relative,), source_ref="pinned-revision")
                self.assertNotIn("ConnectionConfig", symbols)
                verify_module.verify_no_excluded_side_store_ui(
                    b"$s9SideStore16ConnectionConfigC", symbols)
                legacy_member = b"$s9SideStore16ConnectionConfigC20formattedTunnelIfaceSSSgvg"
                self.assertIn("ConnectionConfig.formattedTunnelIface",
                              verify_module.find_legacy_side_store_ui_symbols(legacy_member))
                with self.assertRaisesRegex(ValueError, "ConnectionConfig.formattedTunnelIface"):
                    verify_module.verify_no_excluded_side_store_ui(
                        b"$s9SideStore16ConnectionConfigC\x00" + legacy_member, symbols)
                backend.write_text("import SwiftUI\nfinal class ConnectionConfig {}\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "generated backend ConnectionConfig differs"):
                    verify_module.excluded_side_store_view_type_names(
                        root, (relative,), source_ref="pinned-revision")
                backend.unlink()
                symbols = verify_module.excluded_side_store_view_type_names(
                    root, (relative,), source_ref="pinned-revision")
                self.assertIn("ConnectionConfig", symbols)

    def test_pipeline_ui_types_are_discovered_from_production_patch_file_list(self):
        side_files = (patch_v3_service.HEADLESS_SIDESTORE_VIEW_FILES +
                      patch_v3_service.HEADLESS_SIDESTORE_AUX_UI_FILES)
        pipeline_files = patch_v3_service.HEADLESS_SIDESTORE_PIPELINE_UI_FILES
        source_by_path = {
            "AltStore/Managing Apps/AppExtensionView.swift": "struct AppExtensionView {}",
            "AltStore/Permissions/ReviewPermissionsViewController.swift":
                "final class ReviewPermissionsViewController {}",
        }
        tracked = (["SideStore/" + relative for relative in side_files] +
                   ["AltStore/" + relative for relative in pipeline_files])

        def git_output(command, **kwargs):
            if command[3:5] == ["ls-tree", "-r"]:
                self.assertEqual(command[8:], ["SideStore", "AltStore"])
                return "\n".join(tracked)
            if command[3] == "show":
                path = command[4].split(":", 1)[1]
                return source_by_path.get(path, "")
            raise AssertionError(f"unexpected git invocation: {command}")

        with mock.patch.object(verify_module.subprocess, "check_output", side_effect=git_output):
            found = verify_module.excluded_side_store_view_type_names(
                Path("unused"), side_files, source_ref="pinned-revision",
                additional_source_roots={"AltStore": pipeline_files})

        self.assertEqual(found, ["AppExtensionView", "ReviewPermissionsViewController"])

    def test_exact_pinned_source_inventory_resolves_both_pipeline_ui_files(self):
        side_source_value = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not side_source_value:
            self.skipTest("Set EMBEDDED_SIDESTORE_TEST_SOURCE to the exact pinned SideStore checkout")
        side_source = Path(side_source_value)
        pinned = verify_module.SOURCE_PINS[1]
        actual = subprocess.check_output(
            ["git", "-C", str(side_source), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(actual, pinned, "test checkout must match the exact embedded SideStore pin")
        found = verify_module.excluded_side_store_view_type_names(
            side_source,
            patch_v3_service.HEADLESS_SIDESTORE_VIEW_FILES +
            patch_v3_service.HEADLESS_SIDESTORE_AUX_UI_FILES,
            source_ref=pinned,
            additional_source_roots={
                "AltStore": patch_v3_service.HEADLESS_SIDESTORE_PIPELINE_UI_FILES})
        self.assertIn("AppExtensionView", found)
        self.assertIn("ReviewPermissionsViewController", found)

    def test_production_verifier_rejects_each_excluded_pipeline_ui_symbol_in_ipa(self):
        forbidden_symbols = ("AppExtensionView", "ReviewPermissionsViewController")
        product = "v3.0.3-rc"
        builder_commit = "a" * 40
        run_url = "https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/123"
        host_info = {
            "LCProductLine": "Combined LC+SS " + product,
            "LCBuilderCommit": builder_commit,
            "CFBundleURLTypes": [{"CFBundleURLSchemes": sorted(verify_module.REQUIRED_SCHEMES)}],
            "BGTaskSchedulerPermittedIdentifiers": sorted(verify_module.REQUIRED_BACKGROUND_IDS),
            "UIBackgroundModes": sorted(verify_module.REQUIRED_BACKGROUND_MODES),
        }
        side_info = {
            "CFBundleExecutable": "SideStore",
            "LCProductLine": "Combined LC+SS " + product,
            "LCBuilderCommit": builder_commit,
            "LCBuildRunURL": run_url,
        }
        bundles = {
            verify_module.BASE: {"info": host_info},
            verify_module.BASE + "/Frameworks/SideStoreApp.framework": {"info": side_info},
        }
        for framework in verify_module.REQUIRED_FRAMEWORKS:
            path = verify_module.BASE + "/Frameworks/" + framework
            bundles.setdefault(path, {
                "info": {"CFBundleExecutable": framework[:-len(".framework")]},
                "executable_present": True,
            })
        bundles[verify_module.BASE + "/Frameworks/SideStoreApp.framework"].update(
            {"executable_present": True})
        ipa_info_path = verify_module.BASE + "/Info.plist"
        side_store_path = (verify_module.BASE + "/Frameworks/SideStoreApp.framework/SideStore")
        host_code_path = (verify_module.BASE +
                          "/Frameworks/LiveContainerSwiftUI.framework/LiveContainerSwiftUI")

        with tempfile.TemporaryDirectory() as directory:
            clean_ipa = Path(directory) / "candidate.ipa"
            with zipfile.ZipFile(clean_ipa, "w") as archive:
                archive.writestr(ipa_info_path, plistlib.dumps(host_info))
                archive.writestr(side_store_path,
                                 thin_arm64_macho() +
                                 b"com.kdt.livecontainer.v3-secret-handoff")
                archive.writestr(host_code_path,
                                 thin_arm64_macho() +
                                 b"com.kdt.livecontainer.v3-secret-handoff")
            with zipfile.ZipFile(clean_ipa) as archive:
                clean_framework_executable = archive.read(side_store_path)
            verify_module.verify_no_excluded_side_store_ui(
                clean_framework_executable, list(forbidden_symbols))

        for symbol in forbidden_symbols:
            with self.subTest(symbol=symbol), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ipa = root / "candidate.ipa"
                side_source = root / "side-source"
                side_source.mkdir()
                encoded_symbol = f"$s9SideStore{len(symbol)}{symbol}C".encode("utf-8")
                with zipfile.ZipFile(ipa, "w") as archive:
                    archive.writestr(ipa_info_path, plistlib.dumps(host_info))
                    archive.writestr(side_store_path,
                                     thin_arm64_macho() +
                                     b"com.kdt.livecontainer.v3-secret-handoff\x00" +
                                     encoded_symbol)
                    archive.writestr(host_code_path,
                                     thin_arm64_macho() +
                                     b"com.kdt.livecontainer.v3-secret-handoff")

                with mock.patch.object(verify_module, "inventory", return_value={"bundles": bundles}), \
                        mock.patch.object(verify_module.subprocess, "check_output",
                                          return_value=verify_module.SOURCE_PINS[1]), \
                        mock.patch.object(verify_module, "excluded_side_store_view_type_names",
                                          return_value=[symbol]) as inventory_check:
                    with self.assertRaisesRegex(ValueError,
                                                "embedded SideStore still contains excluded presenter UI"):
                        verify_module.verify(ipa, root / "unused-provenance.json", product,
                                             side_source=side_source)

                passed_files = inventory_check.call_args.args[1]
                self.assertEqual(passed_files,
                                 patch_v3_service.HEADLESS_SIDESTORE_VIEW_FILES +
                                 patch_v3_service.HEADLESS_SIDESTORE_AUX_UI_FILES)
                self.assertEqual(inventory_check.call_args.kwargs["additional_source_roots"], {
                    "AltStore": patch_v3_service.HEADLESS_SIDESTORE_PIPELINE_UI_FILES})


class CandidateArchiveSizeReportTests(unittest.TestCase):
    def test_pinned_headless_ui_symbol_inventory_includes_replaced_bundle_checkbox(self):
        source_value = (os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE") or
                        os.environ.get("SIDESTORE_TEST_SOURCE"))
        if not source_value:
            self.skipTest("pinned embedded SideStore source is supplied by macOS CI")
        source = Path(source_value)
        self.assertTrue(source.is_dir(), "configured pinned SideStore source checkout must exist")
        symbols = verify_module.excluded_side_store_view_type_names(
            source, source_ref=verify_module.SOURCE_PINS[1])
        self.assertIn("AppendTeamIDCheckboxView", symbols)

    def test_all_macho_members_including_standalone_dylibs_are_architecture_checked(self):
        arm64 = thin_arm64_macho()
        files = {
            "Payload/LiveContainer.app/LiveContainer": arm64,
            "Payload/LiveContainer.app/Frameworks/ZSign.dylib": arm64,
            "Payload/LiveContainer.app/Frameworks/TweakLoader.dylib": arm64,
            "Payload/LiveContainer.app/Frameworks/libswiftCore.dylib": arm64,
            "SwiftSupport/Unexpected.dylib": arm64,
            "Payload/LiveContainer.app/Resources/Example.class":
                java_class_file(),
            "Payload/LiveContainer.app/Resources/Example.resource": java_class_file(),
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
                    "Payload/LiveContainer.app/Resources/Example.resource",
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
        with self.assertRaisesRegex(ValueError, "subtype"):
            verify_module.architectures(fat_macho(thin_arm64_macho(subtype=2), subtype=0))
        with self.assertRaisesRegex(ValueError, "missing an LC_UUID"):
            verify_module.architectures(fat_macho(thin_arm64_macho(include_uuid=False)))
        second = thin_arm64_macho(b"fedcba9876543210")
        first = thin_arm64_macho()
        second_offset = 48 + len(first)
        duplicate = (b"\xca\xfe\xba\xbe" + struct.pack(">I", 2) +
                    struct.pack(">IIIII", 0x0100000C, 0, 48, len(first), 0) +
                    struct.pack(">IIIII", 0x0100000C, 0, second_offset, len(second), 0) +
                    first + second)
        with self.assertRaisesRegex(ValueError, "duplicate CPU subtype"):
            verify_module.architectures(duplicate)
        self.assertEqual(verify_module.macho_cpu_subtypes(thin_arm64_macho(subtype=2)), {"arm64": 2})
        with self.assertRaisesRegex(ValueError, "expected ARM64_ALL"):
            verify_module.require_arm64_all_image(thin_arm64_macho(subtype=2))
        arm64e_fat = fat_macho(thin_arm64_macho(subtype=2), subtype=2)
        with self.assertRaisesRegex(ValueError, "expected ARM64_ALL"):
            verify_module.require_arm64_all_image(arm64e_fat)

    def test_fat64_alignment_and_reserved_fields_are_validated(self):
        image = thin_arm64_macho()
        valid = fat64_macho(image, offset=40, align=3)
        self.assertEqual(verify_module.require_arm64_all_image(valid), {"arm64": "30313233-3435-3637-3839-616263646566"})
        with self.assertRaisesRegex(ValueError, "violates its alignment"):
            verify_module.require_arm64_all_image(fat64_macho(image, offset=41, align=3))
        with self.assertRaisesRegex(ValueError, "alignment exponent"):
            verify_module.require_arm64_all_image(fat64_macho(image, offset=40, align=64))
        with self.assertRaisesRegex(ValueError, "reserved field"):
            verify_module.require_arm64_all_image(fat64_macho(image, offset=40, reserved=1))

    def test_lc_uuid_command_must_have_exactly_24_bytes(self):
        malformed = (b"\xcf\xfa\xed\xfe" + struct.pack(
            "<7I", 0x0100000C, 0, 6, 1, 28, 0, 0) +
            struct.pack("<II", 0x1B, 28) + b"0123456789abcdef" + b"JUNK")
        with self.assertRaisesRegex(ValueError, "invalid Mach-O UUID command"):
            verify_module.require_arm64_all_image(malformed)

    def test_inventory_rejects_huge_macho_command_count_without_stalling(self):
        malformed = (b"\xcf\xfa\xed\xfe" + struct.pack(
            "<7I", 0x0100000C, 0, 6, 0xFFFFFFFF, 8, 0, 0) +
            struct.pack("<II", 0, 0))
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "huge-ncmds.ipa"
            with zipfile.ZipFile(ipa, "w") as archive:
                archive.writestr("Payload/Test.app/Info.plist", plistlib.dumps({"CFBundleExecutable": "Run"}))
                archive.writestr("Payload/Test.app/Run", malformed)
            child = (
                "import sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, str(Path.cwd() / 'scripts'))\n"
                "from audit_ipa_signing import inventory\n"
                "try:\n    inventory(Path(sys.argv[1]))\n"
                "except ValueError as error:\n    print(error)\n"
                "else:\n    raise SystemExit('malformed executable was accepted')\n"
            )
            try:
                result = subprocess.run(
                    [sys.executable, "-c", child, str(ipa)], cwd=ROOT,
                    capture_output=True, text=True, timeout=5, check=False)
            except subprocess.TimeoutExpired:
                self.fail("inventory stalled on an untrusted Mach-O command count")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("load-command count exceeds", result.stdout)

    def test_signing_inventory_rejects_nonprogress_and_out_of_bounds_commands(self):
        cases = (
            (0, "invalid Mach-O load-command size"),
            (16, "invalid Mach-O load-command size"),
        )
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "bad-load-command.ipa"
            for command_size, expected_error in cases:
                malformed = (b"\xcf\xfa\xed\xfe" + struct.pack(
                    "<7I", 0x0100000C, 0, 6, 1, 8, 0, 0) +
                    struct.pack("<II", 0, command_size))
                with zipfile.ZipFile(ipa, "w") as archive:
                    archive.writestr("Payload/Test.app/Info.plist", plistlib.dumps({"CFBundleExecutable": "Run"}))
                    archive.writestr("Payload/Test.app/Run", malformed)
                with self.subTest(command_size=command_size), self.assertRaisesRegex(ValueError, expected_error):
                    verify_module.inventory(ipa)

    def test_signing_inventory_accepts_valid_thin_fat32_and_fat64_images(self):
        thin = thin_arm64_macho()
        expected = {"signature_present": False, "xml_entitlements": None}
        self.assertEqual(inspect_signing(thin), expected)
        self.assertEqual(inspect_signing(fat_macho(thin)), [expected])
        self.assertEqual(inspect_signing(fat64_macho(thin)), [expected])

    def test_archive_preflight_limits_reject_metadata_before_member_decompression(self):
        limits = {
            "compressed_ipa_bytes": 100,
            "member_count": 2,
            "total_uncompressed_bytes": 100,
            "member_uncompressed_bytes": 80,
            "compression_ratio": 10,
        }

        class MetadataOnlyArchive:
            def __init__(self, infos):
                self.infos = infos
                self.testzip_called = False

            def infolist(self):
                return self.infos

            def testzip(self):
                self.testzip_called = True
                raise AssertionError("preflight must reject before decompression")

        cases = (
            (101, [], "compressed IPA exceeds"),
            (50, [zipfile.ZipInfo(str(index)) for index in range(3)], "member-count limit"),
            (50, [self._zip_info("large", 81, 20)], "member exceeds"),
            (50, [self._zip_info("total-a", 60, 30), self._zip_info("total-b", 60, 30)],
             "total expanded-size limit"),
            (50, [self._zip_info("ratio", 50, 1)], "compression-ratio limit"),
        )
        for ipa_size, infos, message in cases:
            with self.subTest(message=message):
                archive = MetadataOnlyArchive(infos)
                with self.assertRaisesRegex(ValueError, message):
                    verify_module.preflight_archive(archive, ipa_size, limits)
                self.assertFalse(archive.testzip_called)

    @staticmethod
    def _zip_info(name, expanded_size, compressed_size):
        info = zipfile.ZipInfo(name)
        info.file_size = expanded_size
        info.compress_size = compressed_size
        return info

    def test_archive_preflight_accepts_normal_zip_and_runs_crc_check(self):
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "normal.ipa"
            with zipfile.ZipFile(ipa, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("Payload/LiveContainer.app/Info.plist", plistlib.dumps({"CFBundleExecutable": "LiveContainer"}))
                archive.writestr("Payload/LiveContainer.app/LiveContainer", thin_arm64_macho())
                archive.writestr("Payload/LiveContainer.app/Frameworks/", b"")
                symlink = zipfile.ZipInfo(
                    "Payload/LiveContainer.app/Frameworks/Example.framework/Versions/Current")
                symlink.create_system = 3
                symlink.external_attr = 0o120777 << 16
                archive.writestr(symlink, "A")
            with zipfile.ZipFile(ipa) as archive:
                infos = verify_module.preflight_archive(archive, ipa.stat().st_size)
            self.assertEqual(len(infos), 4)
            self.assertTrue(any(info.is_dir() for info in infos), "ordinary directory entries remain valid")
            self.assertEqual(infos[-1].external_attr >> 16 & 0o170000, 0o120000)
            self.assertEqual(verify_module.sha256_file(ipa), hashlib.sha256(ipa.read_bytes()).hexdigest())
            self.assertEqual(verify_module.preflight_zip_directory(ipa, ipa.stat().st_size), 4)

    def test_verify_rejects_high_count_eocd_before_zipfile_constructor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ipa = root / "high-count.ipa"
            write_zip_with_central_entries(
                ipa, verify_module.DEFAULT_ARCHIVE_LIMITS["member_count"] + 1)
            side_source = root / "pinned-source"
            side_source.mkdir()
            with mock.patch.object(
                    verify_module.subprocess, "check_output",
                    return_value=verify_module.SOURCE_PINS[1]), \
                    mock.patch.object(verify_module.zipfile, "ZipFile") as constructor:
                with self.assertRaisesRegex(ValueError, "member-count limit"):
                    verify_module.verify(ipa, root / "provenance.json", "v3",
                                         side_source=side_source)
                constructor.assert_not_called()

    def test_verify_rejects_oversized_central_directory_before_zipfile_constructor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ipa = root / "oversized-central-directory.ipa"
            write_zip_with_central_entries(ipa, 1)
            data = bytearray(ipa.read_bytes())
            directory_size_offset = len(data) - 22 + 12
            struct.pack_into("<I", data, directory_size_offset,
                verify_module.DEFAULT_ARCHIVE_LIMITS["central_directory_bytes"] + 1)
            ipa.write_bytes(data)
            self.assertLess(ipa.stat().st_size, verify_module.DEFAULT_ARCHIVE_LIMITS["compressed_ipa_bytes"])
            side_source = root / "pinned-source"
            side_source.mkdir()
            with mock.patch.object(
                    verify_module.subprocess, "check_output",
                    return_value=verify_module.SOURCE_PINS[1]), \
                    mock.patch.object(verify_module.zipfile, "ZipFile") as constructor:
                with self.assertRaisesRegex(ValueError, "central directory exceeds the configured size limit"):
                    verify_module.verify(ipa, root / "provenance.json", "v3",
                                         side_source=side_source)
                constructor.assert_not_called()

    def test_underreported_eocd_count_reaches_final_directory_mismatch_check(self):
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "count-mismatch.ipa"
            write_zip_with_central_entries(ipa, 3)
            data = bytearray(ipa.read_bytes())
            struct.pack_into("<HH", data, len(data) - 14, 2, 2)
            ipa.write_bytes(data)
            limits = dict(verify_module.DEFAULT_ARCHIVE_LIMITS, member_count=4)
            with self.assertRaisesRegex(ValueError, "central-directory count or size does not match"):
                verify_module.preflight_zip_directory(ipa, ipa.stat().st_size, limits)

    def test_sfx_prefix_keeps_a_normal_archive_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ordinary = root / "ordinary.ipa"
            sfx = root / "self-extracting.ipa"
            with zipfile.ZipFile(ordinary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("Payload/LiveContainer.app/", b"")
                archive.writestr("Payload/LiveContainer.app/Info.plist", plistlib.dumps({"CFBundleExecutable": "LiveContainer"}))
                archive.writestr("Payload/LiveContainer.app/LiveContainer", thin_arm64_macho())
            sfx.write_bytes(b"ordinary SFX stub\x00" + ordinary.read_bytes())
            self.assertEqual(verify_module.preflight_zip_directory(sfx, sfx.stat().st_size), 3)
            with zipfile.ZipFile(sfx) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read("Payload/LiveContainer.app/Info.plist"),
                                 plistlib.dumps({"CFBundleExecutable": "LiveContainer"}))

    def test_zip64_extra_field_is_rejected_by_central_directory_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "zip64-extra.ipa"
            info = zipfile.ZipInfo("entry")
            info.extra = struct.pack("<HHQ", 0x0001, 8, 0)
            with zipfile.ZipFile(ipa, "w") as archive:
                archive.writestr(info, b"x")
            with self.assertRaisesRegex(ValueError, "ZIP64 archives are unsupported"):
                verify_module.preflight_zip_directory(ipa, ipa.stat().st_size)

    def test_malformed_local_header_offset_is_rejected_before_payload_use(self):
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "bad-local-offset.ipa"
            with zipfile.ZipFile(ipa, "w") as archive:
                archive.writestr("entry", b"payload")
            data = bytearray(ipa.read_bytes())
            central_offset = data.index(b"PK\x01\x02")
            struct.pack_into("<I", data, central_offset + 42, len(data) + 100)
            ipa.write_bytes(data)
            with zipfile.ZipFile(ipa) as archive:
                with self.assertRaisesRegex(ValueError, "corrupt IPA member: entry"):
                    verify_module.preflight_archive(archive, ipa.stat().st_size)

    def test_preflight_explicitly_rejects_zip64_eocd_sentinels(self):
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "zip64-sentinel.ipa"
            ipa.write_bytes(struct.pack(
                "<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0, 0, 0))
            with self.assertRaisesRegex(ValueError, "ZIP64 archives are unsupported"):
                verify_module.preflight_zip_directory(ipa, ipa.stat().st_size)

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
            embedded = root / "embedded-generated" / "SideStore" / "Core" / "Operations"
            embedded.mkdir(parents=True)
            embedded_file = embedded / "PipelineRunner.swift"
            embedded_file.write_text("struct CandidatePipeline {}", encoding="utf-8")
            hashes = {}
            for name in verify_module.REQUIRED_GENERATED_HOST_SOURCES:
                path = root / "generated" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists(): path.write_text("generated " + name, encoding="utf-8")
                hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            for name in verify_module.REQUIRED_GENERATED_EMBEDDED_SOURCES:
                path = root / "embedded-generated" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists(): path.write_text("generated " + name, encoding="utf-8")
                hashes["embedded/" + name] = hashlib.sha256(path.read_bytes()).hexdigest()
            verify_module.verify_generated_source_evidence(root, hashes)
            incomplete = dict(hashes)
            incomplete.pop("LiveContainerSwiftUI/Views/V3UnifiedShell.swift")
            with self.assertRaisesRegex(ValueError, "inventory mismatch"):
                verify_module.verify_generated_source_evidence(root, incomplete)
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                wrong_hashes = dict(hashes)
                wrong_hashes["LiveContainerSwiftUI/Views/V3UnifiedShell.swift"] = "0" * 64
                verify_module.verify_generated_source_evidence(root, wrong_hashes)
            host_symbols = root / "host" / "SideStoreSupport.framework.dSYM" / "Contents" / "Resources" / "DWARF"
            host_symbols.mkdir(parents=True)
            (host_symbols / "SideStoreSupport").write_bytes(image)
            decoy = root / "host" / "unrelated" / "SideStoreSupport"
            decoy.parent.mkdir(parents=True)
            decoy.write_bytes(image)
            self.assertEqual(verify_module.preserved_dsym_uuids(root, {image_uuid}),
                             {"SideStoreSupport": image_uuid})
            uuids, hashes = verify_module.preserved_dsym_evidence(root, {image_uuid})
            dwarf_relative = "host/SideStoreSupport.framework.dSYM/Contents/Resources/DWARF/SideStoreSupport"
            self.assertEqual(hashes[dwarf_relative], hashlib.sha256(image).hexdigest())

    def test_class_extension_does_not_hide_malformed_cafebabe_mach_magic(self):
        malformed = b"\xca\xfe\xba\xbe" + bytes(20)
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "not-a-class.ipa"
            with zipfile.ZipFile(ipa, "w") as archive:
                archive.writestr("Payload/LiveContainer.app/Resources/Bad.class", malformed)
            with zipfile.ZipFile(ipa) as archive:
                self.assertIn("Payload/LiveContainer.app/Resources/Bad.class",
                              verify_module.mach_o_paths(archive, archive.infolist()))
        self.assertFalse(verify_module.is_java_class_file(malformed))
        self.assertFalse(verify_module.is_java_class_file(java_class_file()[:-1]))

    def test_collector_inventory_matches_verifier_inventory(self):
        self.assertEqual(set(evidence_module.HOST_SOURCE_PATHS + evidence_module.V3_HOST_SOURCE_PATHS),
                         verify_module.REQUIRED_GENERATED_HOST_SOURCES)
        self.assertEqual(set(evidence_module.EMBEDDED_SOURCE_PATHS),
                         verify_module.REQUIRED_GENERATED_EMBEDDED_SOURCES)

    def test_collect_is_reproducible_and_removes_stale_evidence_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ipa = root / "candidate.ipa"
            output = root / "evidence"
            host_build = root / "host-build"
            side_build = root / "side-build"
            host_build.mkdir()
            side_build.mkdir()
            commit = "a" * 40
            run_url = "https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/123"
            identity = {"LCProductLine": "Combined LC+SS v3.0.3-rc",
                        "LCBuilderCommit": commit, "LCBuildRunURL": run_url}
            info = plistlib.dumps(identity)
            support = thin_arm64_macho(b"0123456789abcdef") + b"LCFAILURE1:"
            side_store = (thin_arm64_macho(b"fedcba9876543210") + b"LCStructuredFailureStageV1" +
                          b"UNIQUE_DEVICE_ID_QUERY_FAIL" + b"lc_stage=uniqueDeviceID")
            with zipfile.ZipFile(ipa, "w") as archive:
                archive.writestr("Payload/LiveContainer.app/Info.plist", info)
                archive.writestr("Payload/LiveContainer.app/Frameworks/SideStoreSupport.framework/SideStoreSupport", support)
                archive.writestr("Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore", side_store)
            for base, paths in ((host_build, evidence_module.HOST_SOURCE_PATHS + evidence_module.V3_HOST_SOURCE_PATHS),
                                (side_build, evidence_module.EMBEDDED_SOURCE_PATHS)):
                for name in paths:
                    target = base / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("source " + name, encoding="utf-8")
            dwarf = host_build / "SideStoreSupport.framework.dSYM" / "Contents" / "Resources" / "DWARF" / "SideStoreSupport"
            dwarf.parent.mkdir(parents=True)
            dwarf.write_bytes(support)
            env_keys = ("GITHUB_SHA", "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "LIVE_CONTAINER_REF",
                        "EMBEDDED_SIDESTORE_REF", "MINIMUXER_REF", "SIDESIGN_REF", "SIDESIGN_GSA_FIX",
                        "IDEVICE_REF", "JKTCP_REF")
            saved_env = {key: os.environ.get(key) for key in env_keys}
            old_argv = sys.argv
            try:
                os.environ.update({
                    "GITHUB_SHA": commit, "GITHUB_REPOSITORY": "NRG-Wardog/sidestore-auto-refresh",
                    "GITHUB_RUN_ID": "123", **{key: "b" * 40 for key in env_keys[3:]},
                })
                argv = ["combined_build_evidence.py", "collect", "--product", "v3.0.3-rc",
                        "--ipa", str(ipa), "--output", str(output), "--source", str(host_build),
                        "--side-source", str(side_build), str(host_build), str(side_build)]
                sys.argv = argv
                evidence_module.main()
                first = {path.relative_to(output).as_posix(): path.read_bytes()
                         for path in output.rglob("*") if path.is_file()}
                (output / "generated" / "stale.swift").write_text("stale", encoding="utf-8")
                evidence_module.main()
                second = {path.relative_to(output).as_posix(): path.read_bytes()
                          for path in output.rglob("*") if path.is_file()}
                self.assertEqual(first, second)
                self.assertNotIn("generated/stale.swift", second)
                provenance = json.loads(second["candidate-provenance.json"])
                self.assertEqual(provenance["framework_cpu_subtypes"], {
                    "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore": 0,
                    "Payload/LiveContainer.app/Frameworks/SideStoreSupport.framework/SideStoreSupport": 0,
                })
                self.assertEqual(provenance["generated_source_sha256"].keys(),
                                 set(evidence_module.HOST_SOURCE_PATHS + evidence_module.V3_HOST_SOURCE_PATHS) |
                                 {"embedded/" + name for name in evidence_module.EMBEDDED_SOURCE_PATHS})
            finally:
                sys.argv = old_argv
                for key, value in saved_env.items():
                    if value is None: os.environ.pop(key, None)
                    else: os.environ[key] = value

    def test_host_and_liveprocess_require_both_shared_app_groups(self):
        required = verify_module.REQUIRED_LIVECONTAINER_GROUPS
        self.assertTrue(verify_module.has_required_livecontainer_groups(required))
        self.assertFalse(verify_module.has_required_livecontainer_groups(
            {verify_module.REQUIRED_GROUP}),
            "a SideStore-only entitlement cannot preserve an AltStore-origin LC container selection")

    def test_exact_runtime_service_process_must_share_every_selectable_group(self):
        groups = sorted(verify_module.REQUIRED_LIVECONTAINER_GROUPS)
        self.assertEqual(verify_module.verify_service_app_group_ownership(
            groups, groups, [verify_module.REQUIRED_GROUP],
            [verify_module.REQUIRED_GROUP]), groups)
        with self.assertRaisesRegex(ValueError, "runtime configured App Group"):
            verify_module.verify_service_app_group_ownership(
                groups, groups, ["group.example.unentitled"],
                [verify_module.REQUIRED_GROUP])
        with self.assertRaisesRegex(ValueError, "differ from LiveProcess"):
            verify_module.verify_service_app_group_ownership(
                groups + ["group.example.hostOnly"], groups,
                [verify_module.REQUIRED_GROUP], [verify_module.REQUIRED_GROUP])
        with self.assertRaisesRegex(ValueError, "shared App Groups"):
            verify_module.verify_service_app_group_ownership(
                groups, [verify_module.REQUIRED_GROUP], [verify_module.REQUIRED_GROUP],
                [verify_module.REQUIRED_GROUP])

    def test_the_service_packaged_fallback_must_be_entitled_in_both_processes(self):
        # The host forwards its selection, so the service usually inherits it.
        # A launch that publishes nothing instead falls back to each process's
        # own Info.plist list, which is Bundle.main inside LiveProcess: an
        # unentitled or absent list there would split the shared store exactly
        # when the forwarded key is missing.
        groups = sorted(verify_module.REQUIRED_LIVECONTAINER_GROUPS)
        self.assertEqual(verify_module.verify_service_app_group_ownership(
            groups, groups, [verify_module.REQUIRED_GROUP],
            [verify_module.REQUIRED_GROUP]), groups)
        with self.assertRaisesRegex(ValueError, "LiveProcess packaged App Group fallback"):
            verify_module.verify_service_app_group_ownership(
                groups, groups, [verify_module.REQUIRED_GROUP], [])
        with self.assertRaisesRegex(ValueError, "LiveProcess packaged App Group fallback"):
            verify_module.verify_service_app_group_ownership(
                groups, groups, [verify_module.REQUIRED_GROUP],
                ["group.example.packagedOnly"])
        # The verification must read the extension's own plist, not the host's.
        source = inspect.getsource(verify_module.verify)
        self.assertIn('(live_process.get("info") or {}).get("ALTAppGroups", [])', source)

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
