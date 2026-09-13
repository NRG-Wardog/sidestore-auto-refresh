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
