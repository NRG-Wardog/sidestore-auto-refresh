"""Unit tests for the automated CI build monitor tool."""
import unittest
from unittest.mock import patch
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import monitor_ci_build as monitor


class MonitorCIBaseTests(unittest.TestCase):
    def test_parse_args_defaults(self):
        args = monitor.parse_args([])
        self.assertIsNone(args.run_id)
        self.assertEqual(args.repo, monitor.DEFAULT_REPO)
        self.assertEqual(args.workflow, monitor.DEFAULT_WORKFLOW)
        self.assertEqual(args.interval, 15)
        self.assertFalse(args.download_artifacts)
        self.assertTrue(args.watch)

    def test_parse_args_explicit_run_id(self):
        args = monitor.parse_args(["987654321", "--no-watch", "--download-artifacts"])
        self.assertEqual(args.run_id, 987654321)
        self.assertFalse(args.watch)
        self.assertTrue(args.download_artifacts)

    def test_format_step_icon(self):
        self.assertEqual(monitor.format_step_icon("completed", "success"), "[PASS]")
        self.assertEqual(monitor.format_step_icon("completed", "failure"), "[FAIL]")
        self.assertEqual(monitor.format_step_icon("completed", "skipped"), "[SKIP]")
        self.assertEqual(monitor.format_step_icon("completed", "cancelled"), "[CNCL]")
        self.assertEqual(monitor.format_step_icon("in_progress", None), "[WAIT]")
        self.assertEqual(monitor.format_step_icon("pending", None), "[PEND]")

    def test_analyze_failures_anchor_drift(self):
        job = monitor.JobStatus(
            job_id=101,
            name="build-job",
            status="completed",
            conclusion="failure",
            steps=[
                monitor.StepStatus(name="Patch Swift", status="completed", conclusion="failure"),
            ],
        )
        run = monitor.RunStatus(
            run_id=555,
            status="completed",
            conclusion="failure",
            workflow_name="CI",
            branch="feature",
            url="https://github.com/...",
            jobs=[job],
        )

        fake_log = "patch_app_layout.py: expected 1 anchor, found 0\nTraceback (most recent call last):\n"
        with patch("monitor_ci_build.run_gh_command", return_value=(0, fake_log, "")):
            analysis = monitor.analyze_failures("test/repo", run.run_id, run)
            self.assertTrue(any("Anchor drift" in c for c in analysis["root_causes"]))

    def test_analyze_failures_missing_file(self):
        job = monitor.JobStatus(
            job_id=102,
            name="package-job",
            status="completed",
            conclusion="failure",
            steps=[
                monitor.StepStatus(name="Package IPA", status="completed", conclusion="failure"),
            ],
        )
        run = monitor.RunStatus(
            run_id=556,
            status="completed",
            conclusion="failure",
            workflow_name="CI",
            branch="feature",
            url="https://github.com/...",
            jobs=[job],
        )

        fake_log = "FileNotFoundError: [Errno 2] No such file or directory: 'SideBackup.ipa'\n"
        with patch("monitor_ci_build.run_gh_command", return_value=(0, fake_log, "")):
            analysis = monitor.analyze_failures("test/repo", run.run_id, run)
            self.assertTrue(any("Missing file or broken symlink" in c for c in analysis["root_causes"]))

    def test_analyze_failures_test_failures(self):
        job = monitor.JobStatus(
            job_id=103,
            name="test-job",
            status="completed",
            conclusion="failure",
            steps=[
                monitor.StepStatus(name="Run tests", status="completed", conclusion="failure"),
            ],
        )
        run = monitor.RunStatus(
            run_id=557,
            status="completed",
            conclusion="failure",
            workflow_name="CI",
            branch="feature",
            url="https://github.com/...",
            jobs=[job],
        )

        fake_log = "FAIL: test_feature (tests.test_sample.SampleTest)\nFAILED (failures=1)\n"
        with patch("monitor_ci_build.run_gh_command", return_value=(0, fake_log, "")):
            analysis = monitor.analyze_failures("test/repo", run.run_id, run)
            self.assertTrue(any("Unit test failures" in c for c in analysis["root_causes"]))

    def test_analyze_failures_swift_rust_errors(self):
        job = monitor.JobStatus(
            job_id=104,
            name="compile-job",
            status="completed",
            conclusion="failure",
            steps=[
                monitor.StepStatus(name="Build Swift", status="completed", conclusion="failure"),
            ],
        )
        run = monitor.RunStatus(
            run_id=558,
            status="completed",
            conclusion="failure",
            workflow_name="CI",
            branch="feature",
            url="https://github.com/...",
            jobs=[job],
        )

        fake_log = "Sources/AppLayout.swift:10:5: error: cannot find 'InvalidSymbol' in scope\n"
        with patch("monitor_ci_build.run_gh_command", return_value=(0, fake_log, "")):
            analysis = monitor.analyze_failures("test/repo", run.run_id, run)
            self.assertTrue(any("Swift compilation error" in c for c in analysis["root_causes"]))


if __name__ == "__main__":
    unittest.main()
