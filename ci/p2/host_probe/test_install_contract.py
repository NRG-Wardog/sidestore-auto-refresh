"""Portable synthetic adversarial tests; never run the product or change it."""
import copy
import os
from pathlib import Path
import shutil
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch

import install_contract as contract


def thin_macho(filetype=contract.MH_DYLIB, *, cpu=0x100000c, subtype=0,
               platform=7, endian='<', bits=64, commands=None):
    """Tiny structural fixture only, never a runnable executable."""
    if commands is None:
        commands = [struct.pack(endian + '8I', 0x32, 32, platform, 0x0f0000, 0x1a0400, 1, 3, 0x04f20000)]
    command_bytes = b''.join(commands)
    fields = [0xfeedfacf if bits == 64 else 0xfeedface, cpu, subtype, filetype,
              len(commands), len(command_bytes), 0]
    if bits == 64:
        fields.append(0)
    return struct.pack(endian + str(len(fields)) + 'I', *fields) + command_bytes


def fat_macho(slices=None, *, endian='>', fat64=False):
    if slices is None:
        slices = [(0x100000c, 0, thin_macho()),
                  (0x1000007, 3, thin_macho(cpu=0x1000007, subtype=3))]
    record_size = 32 if fat64 else 20
    offset = (8 + len(slices) * record_size + 7) & ~7
    header = bytearray(struct.pack(endian + 'II', 0xcafebabf if fat64 else 0xcafebabe, len(slices)))
    payload = bytearray()
    for cpu, subtype, raw in slices:
        fields = [cpu, subtype, offset + len(payload), len(raw), 3]
        if fat64:
            fields.append(0)
        header += struct.pack(endian + ('IIQQII' if fat64 else 'IIIII'), *fields)
        payload += raw
        payload += b'\0' * ((-len(payload)) % 8)
    return bytes(header) + b'\0' * (offset - len(header)) + bytes(payload)


def replace_word(raw, offset, value, *, endian='<', width='I'):
    result = bytearray(raw)
    struct.pack_into(endian + width, result, offset, value)
    return bytes(result)


class MachOTests(unittest.TestCase):
    def test_all_thin_widths_and_endiannesses(self):
        for bits in (32, 64):
            for endian in ('<', '>'):
                with self.subTest(bits=bits, endian=endian):
                    row = contract.parse_macho(thin_macho(bits=bits, endian=endian))['slices'][0]
                    self.assertEqual(row['fileType'], 6)
                    self.assertEqual(row['bits'], bits)
                    self.assertEqual(row['buildVersion']['platform'], 7)

    def test_fat32_and_fat64_both_endiannesses(self):
        for fat64 in (False, True):
            for endian in ('<', '>'):
                with self.subTest(fat64=fat64, endian=endian):
                    rows = contract.parse_macho(fat_macho(fat64=fat64, endian=endian))['slices']
                    self.assertEqual([r['cpuType'] for r in rows], [0x100000c, 0x1000007])

    def test_empty_unknown_and_truncated_thin(self):
        raw = thin_macho()
        for value in (b'', b'nope', raw[:4], raw[:27], raw[:31], raw[:-1]):
            with self.subTest(value=value), self.assertRaises(contract.ContractError):
                contract.parse_macho(value)

    def test_counts_sizes_alignment_and_region_coverage(self):
        raw = thin_macho()
        for offset, value in ((16, 0), (16, 2), (16, 0xffffffff), (20, 0),
                              (20, 24), (20, 0xffffffff), (28, 1),
                              (36, 0), (36, 7), (36, 28), (36, 40), (36, 0xffffffff)):
            with self.subTest(offset=offset, value=value), self.assertRaises(contract.ContractError):
                contract.parse_macho(replace_word(raw, offset, value))
        # Unclaimed bytes are within sizeofcmds but outside ncmds.
        extra = replace_word(raw + b'\0' * 8, 20, 40)
        with self.assertRaisesRegex(contract.ContractError, 'count/size'):
            contract.parse_macho(extra)

    def test_platform_missing_legacy_duplicate_wrong_and_tool_count(self):
        version = thin_macho()[32:]
        other = struct.pack('<6I', 0x1b, 24, 0, 0, 0, 0)
        legacy = struct.pack('<4I', 0x25, 16, 0x0f0000, 0x1a0400)
        values = [thin_macho(platform=2), thin_macho(platform=0), thin_macho(commands=[other]),
                  thin_macho(commands=[version, version]), thin_macho(commands=[legacy]),
                  thin_macho(commands=[version, legacy]), replace_word(thin_macho(), 52, 0xffffffff)]
        for value in values:
            with self.subTest(value=value), self.assertRaises(contract.ContractError):
                contract.parse_macho(value)
        duplicate_tools = struct.pack('<10I', 0x32, 40, 7, 0, 0, 2, 3, 1, 3, 2)
        with self.assertRaisesRegex(contract.ContractError, 'Duplicate'):
            contract.parse_macho(thin_macho(commands=[duplicate_tools]))

    def test_unknown_and_malformed_known_commands(self):
        version = thin_macho()[32:]
        bad_commands = [struct.pack('<II', 0x12345678, 8),
                        struct.pack('<II', 0x2, 8),
                        struct.pack('<4I', 0x1d, 16, 10000, 1),
                        struct.pack('<6I', 0x2, 24, 10000, 1, 0, 0),
                        struct.pack('<6I', 0xc, 24, 24, 0, 0, 0),
                        struct.pack('<8I', 0xc, 32, 32, 0, 0, 0, 0, 0),
                        struct.pack('<6I', 0xc, 32, 24, 0, 0, 0) + b'no-null!',
                        struct.pack('<6I', 0xc, 32, 24, 0, 0, 0) + b'abc\0BAD!',
                        struct.pack('<18I', 0x19, 72, *([0] * 14), 1, 0)]
        for command in bad_commands:
            with self.subTest(command=command), self.assertRaises(contract.ContractError):
                contract.parse_macho(thin_macho(commands=[version, command]))

    def test_truncated_fat_records_empty_or_impossible_count(self):
        raw = fat_macho()
        for value in (raw[:4], raw[:7], raw[:27], replace_word(raw, 4, 0, endian='>'),
                      replace_word(raw, 4, 0xffffffff, endian='>')):
            with self.subTest(value=value), self.assertRaises(contract.ContractError):
                contract.parse_macho(value)

    def test_fat_header_overlap_slice_overlap_range_alignment_and_cpu(self):
        raw = fat_macho()
        # fat_arch fields: CPU at 8, subtype 12, offset 16, size 20, align 24;
        # second record starts at 28, with offset 36.
        first_offset = struct.unpack_from('>I', raw, 16)[0]
        cases = [(16, 8), (36, first_offset), (20, 0), (20, 0xffffffff),
                 (16, len(raw) + 8), (16, first_offset + 1), (24, 32),
                 (8, 12), (12, 9)]
        for offset, value in cases:
            with self.subTest(offset=offset, value=value), self.assertRaises(contract.ContractError):
                contract.parse_macho(replace_word(raw, offset, value, endian='>'))

    def test_fat_duplicate_architecture_nested_container_reserved64_and_second_slice_truncation(self):
        duplicate = fat_macho([(0x100000c, 0, thin_macho()), (0x100000c, 0, thin_macho())])
        nested = fat_macho([(0x100000c, 0, fat_macho())])
        reserved = replace_word(fat_macho(fat64=True), 36, 1, endian='>')
        for raw in (duplicate, nested, reserved, fat_macho()[:-1]):
            with self.subTest(raw=raw), self.assertRaises(contract.ContractError):
                contract.parse_macho(raw)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.built = self.root / 'built.app'
        self.installed = self.root / 'installed.app'
        self.built.mkdir()
        for name in contract.ALLOWED_DYLIB_PATHS:
            self.put(self.built, name, thin_macho(), 0o755)
        self.put(self.built, 'LiveContainer', thin_macho(2), 0o755)
        self.put(self.built, 'Info.plist', b'synthetic metadata', 0o644)
        self.put(self.built, 'Resources/data', b'resource bytes', 0o644)
        (self.built / 'resource-link').symlink_to('Resources/data')
        shutil.copytree(self.built, self.installed, symlinks=True)
        self.library = sorted(contract.ALLOWED_DYLIB_PATHS)[0]

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def put(root, name, raw, mode):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        # Modes only on throwaway synthetic test fixtures, never real products.
        path.chmod(mode)

    def verify(self, built_inventory=None, installed_inventory=None):
        return contract.verify_installation_identity(
            self.built, self.installed,
            contract.bundle_inventory(self.built) if built_inventory is None else built_inventory,
            contract.bundle_inventory(self.installed) if installed_inventory is None else installed_inventory)

    def test_exact_equality_and_precise_receipt(self):
        receipt = self.verify()
        self.assertEqual(receipt['modeTransitions'], [])
        self.assertEqual(receipt['pathCount'], 11)
        self.assertEqual(receipt['symlinkCount'], 1)
        self.assertEqual(len(receipt['machO']), 8)
        self.assertEqual(len(receipt['builtInventorySHA256']), 64)
        self.assertTrue(receipt['literalBytesEqual'])
        self.assertTrue(receipt['observationsStable'])

    def test_all_seven_and_partial_observed_transitions_allowed(self):
        for name in sorted(contract.ALLOWED_DYLIB_PATHS):
            (self.installed / name).chmod(0o644)
            receipt = self.verify()
            self.assertEqual(receipt['modeTransitions'][-1]['fileType'], 6)
        self.assertEqual({r['path'] for r in receipt['modeTransitions']}, contract.ALLOWED_DYLIB_PATHS)

    def test_full_permission_mode_is_inventory_bound(self):
        original = contract.bundle_inventory(self.installed)
        (self.installed / 'Resources/data').chmod(0o640)
        with self.assertRaisesRegex(contract.ContractError, 'does not bind'):
            self.verify(installed_inventory=original)

    def test_other_permission_transitions_and_special_bits_rejected(self):
        for mode in (0o744, 0o754, 0o654, 0o640, 0o444, 0o600, 0o777, 0o1755, 0o2755, 0o4755, 0o4644):
            (self.installed / self.library).chmod(mode)
            with self.subTest(mode=oct(mode)), self.assertRaisesRegex(contract.ContractError, 'full-mode transition'):
                self.verify()

    def test_reverse_transition_rejected(self):
        (self.built / self.library).chmod(0o644)
        with self.assertRaisesRegex(contract.ContractError, 'full-mode transition'):
            self.verify()

    def test_unlisted_dylib_cannot_change_modes(self):
        for root in (self.built, self.installed):
            self.put(root, 'Frameworks/Unreviewed.dylib', thin_macho(), 0o755)
        (self.installed / 'Frameworks/Unreviewed.dylib').chmod(0o644)
        with self.assertRaisesRegex(contract.ContractError, 'full-mode transition'):
            self.verify()

    def test_lookalike_path_is_not_allowlisted(self):
        for root in (self.built, self.installed):
            self.put(root, self.library + '.other', thin_macho(), 0o755)
        (self.installed / (self.library + '.other')).chmod(0o644)
        with self.assertRaisesRegex(contract.ContractError, 'full-mode transition'):
            self.verify()

    def test_main_must_remain_0755_and_execute_in_every_slice(self):
        for mode in (0o644, 0o754, 0o4755):
            for root in (self.built, self.installed):
                (root / 'LiveContainer').chmod(mode)
            with self.subTest(mode=mode), self.assertRaisesRegex(contract.ContractError, 'Main executable'):
                self.verify()
        for raw in (thin_macho(6), fat_macho([(0x100000c, 0, thin_macho(2)),
                                             (0x1000007, 3, thin_macho(6, cpu=0x1000007, subtype=3))])):
            for root in (self.built, self.installed):
                self.put(root, 'LiveContainer', raw, 0o755)
            with self.assertRaisesRegex(contract.ContractError, 'file type'):
                self.verify()

    def test_each_mandatory_library_parsed_even_with_equal_modes(self):
        for name in sorted(contract.ALLOWED_DYLIB_PATHS):
            for root in (self.built, self.installed):
                self.put(root, name, thin_macho(2), 0o755)
            with self.subTest(name=name), self.assertRaisesRegex(contract.ContractError, 'file type'):
                self.verify()
            for root in (self.built, self.installed):
                self.put(root, name, thin_macho(), 0o755)

    def test_mixed_fat_filetypes_wrong_second_platform_or_malformed_second_slice_fail(self):
        cases = [fat_macho([(0x100000c, 0, thin_macho()),
                            (0x1000007, 3, thin_macho(2, cpu=0x1000007, subtype=3))]),
                 fat_macho([(0x100000c, 0, thin_macho()),
                            (0x1000007, 3, thin_macho(cpu=0x1000007, subtype=3, platform=2))]),
                 fat_macho([(0x100000c, 0, thin_macho()), (0x1000007, 3, b'no Mach-O')])]
        for raw in cases:
            for root in (self.built, self.installed):
                self.put(root, self.library, raw, 0o755)
            (self.installed / self.library).chmod(0o644)
            with self.subTest(raw=raw), self.assertRaises(contract.ContractError):
                self.verify()

    def test_exact_same_valid_fat_dylib_is_accepted(self):
        for root in (self.built, self.installed):
            self.put(root, self.library, fat_macho(), 0o755)
        (self.installed / self.library).chmod(0o644)
        self.assertEqual(self.verify()['modeTransitions'][0]['sliceCount'], 2)

    def test_all_required_paths_present(self):
        for root in (self.built, self.installed):
            (root / self.library).unlink()
        with self.assertRaisesRegex(contract.ContractError, 'Required product'):
            self.verify()

    def test_added_and_removed_installed_paths_rejected(self):
        extra = self.installed / 'extra'
        extra.write_text('extra')
        with self.assertRaisesRegex(contract.ContractError, 'path set'):
            self.verify()
        extra.unlink()
        (self.installed / 'Info.plist').unlink()
        with self.assertRaisesRegex(contract.ContractError, 'path set'):
            self.verify()

    def test_type_substitution_and_link_target_changes_rejected(self):
        path = self.installed / 'Resources/data'
        path.unlink()
        path.symlink_to('../Info.plist')
        with self.assertRaisesRegex(contract.ContractError, 'type differs'):
            self.verify()
        path.unlink()
        self.put(self.installed, 'Resources/data', b'resource bytes', 0o644)
        link = self.installed / 'resource-link'
        link.unlink(); link.symlink_to('Info.plist')
        with self.assertRaisesRegex(contract.ContractError, 'literal bytes'):
            self.verify()

    def test_mandatory_binary_cannot_be_link_even_when_both_inventories_agree(self):
        for root in (self.built, self.installed):
            (root / self.library).unlink()
            (root / self.library).symlink_to('../../LiveContainer')
        with self.assertRaisesRegex(contract.ContractError, 'regular file'):
            self.verify()

    def test_stale_and_forged_hash_or_size_rejected(self):
        before = contract.bundle_inventory(self.installed)
        (self.installed / self.library).write_bytes(thin_macho() + b'new bytes')
        with self.assertRaisesRegex(contract.ContractError, 'does not bind'):
            self.verify(installed_inventory=before)
        for field, value in (('sha256', '0' * 64), ('bytes', 1)):
            forged = contract.bundle_inventory(self.installed)
            forged[self.library][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(contract.ContractError, 'does not bind'):
                self.verify(installed_inventory=forged)

    def test_recomputed_changed_content_still_rejected(self):
        for raw in (thin_macho() + b'changed', thin_macho(cpu=0x1000007), thin_macho(platform=2)):
            (self.installed / self.library).write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaisesRegex(contract.ContractError, 'literal bytes'):
                self.verify()

    def test_lying_hash_cannot_replace_literal_comparison(self):
        # Even a deliberately stubbed digest collision cannot bless unequal bytes.
        (self.installed / 'Resources/data').write_bytes(b'resource bytEs')
        with patch.object(contract, '_digest', return_value='0' * 64):
            with self.assertRaisesRegex(contract.ContractError, 'literal bytes'):
                self.verify()

    def test_strict_inventory_schema_boolean_modes_counts_hashes_and_unknown_fields(self):
        original = contract.bundle_inventory(self.built)
        for field, value in (('mode', True), ('mode', 0o100755), ('mode', -1), ('mode', '0755'),
                             ('bytes', True), ('bytes', 1.0), ('bytes', -1), ('sha256', 'bad'),
                             ('sha256', 'A' * 64), ('executable', 1), ('type', 'file'), ('unknown', 1)):
            bad = copy.deepcopy(original)
            bad[self.library][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(contract.ContractError):
                self.verify(built_inventory=bad)
        bad = copy.deepcopy(original); del bad[self.library]['mode']
        with self.assertRaises(contract.ContractError):
            self.verify(built_inventory=bad)

    def test_path_ambiguity_and_ancestors_cannot_be_inventory_entries(self):
        original = contract.bundle_inventory(self.built)
        for name in ('', '/absolute', '../escape', 'a//b', 'a/../b', './x', 'a\\b', 'nul\0x'):
            bad = copy.deepcopy(original); bad[name] = bad.pop('Info.plist')
            with self.subTest(name=name), self.assertRaises(contract.ContractError):
                self.verify(built_inventory=bad)

    def test_raw_link_bytes_hash_and_length_are_bound(self):
        row = contract.bundle_inventory(self.built)['resource-link']
        self.assertEqual(row['bytes'], len(b'Resources/data'))
        self.assertEqual(row['mode'], stat.S_IMODE((self.built / 'resource-link').lstat().st_mode))
        for field, value in (('bytes', 999), ('sha256', '0' * 64), ('link', 'Info.plist')):
            bad = contract.bundle_inventory(self.built); bad['resource-link'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(contract.ContractError, 'raw link binding'):
                self.verify(built_inventory=bad)

    def test_escaping_missing_cyclic_link_and_linked_parent_rejected(self):
        path = self.installed / 'resource-link'
        for target in ('/etc/hosts', '../outside', 'missing', 'resource-link', '.'):
            path.unlink(); path.symlink_to(target)
            with self.subTest(target=target), self.assertRaises(contract.ContractError):
                contract.bundle_inventory(self.installed)
        path.unlink()
        shutil.rmtree(self.installed / 'Resources')
        (self.installed / 'Resources').symlink_to(self.built / 'Resources')
        with self.assertRaises(contract.ContractError):
            contract.bundle_inventory(self.installed)

    def test_supported_contained_parent_relative_link(self):
        for root in (self.built, self.installed):
            (root / 'Resources/link').symlink_to('../Info.plist')
        self.assertEqual(self.verify()['symlinkCount'], 2)

    def test_linked_root_rejected(self):
        link = self.root / 'linked.app'; link.symlink_to(self.installed, target_is_directory=True)
        with self.assertRaisesRegex(contract.ContractError, 'linked bundle root'):
            contract.bundle_inventory(link)

    def test_unsupported_fifo_rejected_without_opening_it(self):
        os.mkfifo(self.installed / 'fifo')
        with self.assertRaisesRegex(contract.ContractError, 'Unsupported filesystem'):
            contract.bundle_inventory(self.installed)

    def test_added_empty_directory_rejected(self):
        (self.installed / 'extra-empty').mkdir()
        with self.assertRaisesRegex(contract.ContractError, 'directory path set'):
            self.verify()

    def test_changed_snapshot_after_parsing_rejected(self):
        original = contract.parse_macho
        changed = False
        def parse_and_mutate(raw):
            nonlocal changed
            result = original(raw)
            if not changed:
                (self.installed / 'Resources/data').write_bytes(b'changed mid-check')
                changed = True
            return result
        with patch.object(contract, 'parse_macho', side_effect=parse_and_mutate):
            with self.assertRaisesRegex(contract.ContractError, 'changed during installation'):
                self.verify()

    def test_same_content_replacement_inode_detected(self):
        original = contract.parse_macho
        changed = False
        def parse_and_replace(raw):
            nonlocal changed
            result = original(raw)
            if not changed:
                path = self.installed / 'Resources/data'
                replacement = self.installed / 'Resources/replacement'
                replacement.write_bytes(path.read_bytes()); replacement.chmod(0o644)
                replacement.replace(path)
                changed = True
            return result
        with patch.object(contract, 'parse_macho', side_effect=parse_and_replace):
            with self.assertRaisesRegex(contract.ContractError, 'changed during installation'):
                self.verify()


if __name__ == '__main__':
    unittest.main()
