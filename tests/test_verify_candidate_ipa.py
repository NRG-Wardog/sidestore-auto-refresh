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
    def test_size_report_partitions_files_and_ranks_largest_members(self):
        files = {
            "Payload/LiveContainer.app/LiveContainer": b"h" * 100,
            "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore": b"s" * 50,
            "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Assets.car": b"a" * 30,
            "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/Base.lproj/Main.storyboardc/Info.plist": b"b" * 20,
            "Payload/LiveContainer.app/PlugIns/LiveProcess.appex/LiveProcess": b"p" * 10,
            "Payload/LiveContainer.app/Info.plist": b"i" * 5,
        }
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "candidate.ipa"
            with zipfile.ZipFile(ipa, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            with zipfile.ZipFile(ipa) as archive:
                report = verify_module.archive_size_report(
                    archive.infolist(),
                    {
                        "Payload/LiveContainer.app/LiveContainer",
                        "Payload/LiveContainer.app/Frameworks/SideStoreApp.framework/SideStore",
                        "Payload/LiveContainer.app/PlugIns/LiveProcess.appex/LiveProcess",
                    })
        breakdown = report["payload_breakdown_bytes"]
        self.assertEqual(report["uncompressed_bytes"], sum(map(len, files.values())))
        self.assertEqual(breakdown["executables"], 160)
        self.assertEqual(breakdown["Assets.car"], 30)
        self.assertEqual(breakdown["storyboards_and_nibs"], 20)
        self.assertEqual(breakdown["other_files"], 5)
        self.assertEqual(report["largest_files"][0]["uncompressed_bytes"], 100)
        self.assertEqual(len(report["largest_files"]), len(files))


if __name__ == "__main__":
    unittest.main()
