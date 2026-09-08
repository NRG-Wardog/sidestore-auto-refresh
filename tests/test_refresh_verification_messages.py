from pathlib import Path
import unittest


class VerificationMessagesTests(unittest.TestCase):
    def test_missing_mismatched_and_empty_results_are_distinct(self):
        source = (Path(__file__).resolve().parents[1] / "scripts/templates/livecontainer_refresh_scheduler.swift").read_text()
        self.assertNotIn("verification_manifest_missing_or_wrong_run", source)
        for code in ("manifest_missing", "run_mismatch", "results_empty"):
            self.assertIn("VERIFICATION_FAILED reason=" + code, source)
        self.assertIn("Refresh is unconfirmed", source)
        self.assertIn("different refresh attempt", source)
        self.assertIn("No successful refresh was confirmed", source)


if __name__ == "__main__":
    unittest.main()
