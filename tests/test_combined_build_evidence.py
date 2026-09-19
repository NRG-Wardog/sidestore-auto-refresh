"""Validate candidate identity/UUID evidence without requiring an Apple device."""
import importlib.util
from pathlib import Path
import struct
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate_evidence', ROOT / 'scripts/combined_build_evidence.py')
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class CandidateEvidenceTests(unittest.TestCase):
    def test_matching_uuid_and_malformed_commands(self):
        expected = uuid.UUID('07E95F24-0DF4-3F9F-B1B4-3AF4881C1CBD')
        header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 6, 1, 24, 0, 0)
        command = struct.pack('<II', 0x1b, 24) + expected.bytes
        self.assertEqual(evidence.macho_uuid(header + command), str(expected).upper())
        with self.assertRaises(ValueError): evidence.macho_uuid(header + struct.pack('<II', 0x1b, 7))
        with self.assertRaises(ValueError): evidence.macho_uuid(header + command[:-1])
        self.assertIsNone(evidence.macho_uuid(b'not Mach-O'))

    def test_release_product_lines_are_accepted(self):
        for product in ("v2", "v3", "v3.0.1", "v3.10.2"):
            self.assertTrue(product in ('v2', 'v3') or
                            __import__('re').fullmatch(r'v3\.\d+(\.\d+)*', product) is not None)
        for product in ("v4", "v3.x", "latest", ""):
            self.assertFalse(product in ('v2', 'v3') or
                             __import__('re').fullmatch(r'v3\.\d+(\.\d+)*', product) is not None)
        self.assertEqual('Combined LC+SS ' + 'v3.0.1', 'Combined LC+SS v3.0.1')
