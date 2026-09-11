#!/usr/bin/env python3
"""Automated CI Build Monitor and Failure Analyzer.

Monitors GitHub Actions workflow runs, streams real-time per-step status,
and performs automated root-cause failure analysis and artifact retrieval.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "NRG-Wardog/sidestore-auto-refresh"
DEFAULT_WORKFLOW = "livecontainer-build.yml"


@dataclass
class StepStatus:
    name: str
    status: str  # in_progress, completed, pending
    conclusion: str | None  # success, failure, skipped, cancelled, None
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class JobStatus:
    job_id: int
    name: str
    status: str
    conclusion: str | None
    steps: list[StepStatus] = field(default_factory=list)


@dataclass
class RunStatus:
    run_id: int
    status: str
    conclusion: str | None
    workflow_name: str
    branch: str
    url: str
    jobs: list[JobStatus] = field(default_factory=list)


def get_current_branch() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        branch = proc.stdout.strip()
        return branch if branch else None
    except Exception:
        return None


def run_gh_command(args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["gh"] + args,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "gh command not found"


def get_latest_run_id(repo: str, workflow: str, branch: str | None = None) -> int | None:
    args = ["run", "list", "--repo", repo, "--workflow", workflow, "--limit", "1", "--json", "databaseId,headBranch"]
    code, stdout, stderr = run_gh_command(args)
    if code == 0 and stdout.strip():
        try:
            data = json.loads(stdout)
            if data and isinstance(data, list):
                return int(data[0]["databaseId"])
        except Exception:
            pass

    # Fallback to GitHub REST API
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?per_page=1"
    if branch:
        url += f"&branch={branch}"
    req = urllib.request.Request(url, headers={"User-Agent": "CI-Monitor", "Accept": "application/vnd.github+json"})
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            runs = data.get("workflow_runs", [])
            if runs:
                return int(runs[0]["id"])
    except Exception:
        pass

    return None


def fetch_run_status(repo: str, run_id: int) -> RunStatus:
    # Query via gh run view --json
    args = [
        "run", "view", str(run_id),
        "--repo", repo,
        "--json", "databaseId,status,conclusion,workflowName,headBranch,url,jobs",
    ]
    code, stdout, _ = run_gh_command(args)
    if code == 0 and stdout.strip():
        data = json.loads(stdout)
        jobs: list[JobStatus] = []
        for j in data.get("jobs", []):
            steps = [
                StepStatus(
                    name=s.get("name", "unnamed"),
                    status=s.get("status", "pending"),
                    conclusion=s.get("conclusion"),
                    started_at=s.get("startedAt"),
                    completed_at=s.get("completedAt"),
                )
                for s in j.get("steps", [])
            ]
            jobs.append(
                JobStatus(
                    job_id=j.get("databaseId", 0),
                    name=j.get("name", "unnamed"),
                    status=j.get("status", "pending"),
                    conclusion=j.get("conclusion"),
                    steps=steps,
                )
            )
        return RunStatus(
            run_id=data.get("databaseId", run_id),
            status=data.get("status", "unknown"),
            conclusion=data.get("conclusion"),
            workflow_name=data.get("workflowName", "unknown"),
            branch=data.get("headBranch", "unknown"),
            url=data.get("url", f"https://github.com/{repo}/actions/runs/{run_id}"),
            jobs=jobs,
        )

    # API fallback
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    headers = {"User-Agent": "CI-Monitor", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}", headers=headers)
    with urllib.request.urlopen(req) as resp:
        run_data = json.loads(resp.read().decode("utf-8"))

    req_jobs = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs", headers=headers)
    with urllib.request.urlopen(req_jobs) as resp:
        jobs_data = json.loads(resp.read().decode("utf-8"))

    jobs_list: list[JobStatus] = []
    for j in jobs_data.get("jobs", []):
        steps = [
            StepStatus(
                name=s.get("name", "unnamed"),
                status=s.get("status", "pending"),
                conclusion=s.get("conclusion"),
                started_at=s.get("started_at"),
                completed_at=s.get("completed_at"),
            )
            for s in j.get("steps", [])
        ]
        jobs_list.append(
            JobStatus(
                job_id=j.get("id", 0),
                name=j.get("name", "unnamed"),
                status=j.get("status", "pending"),
                conclusion=j.get("conclusion"),
                steps=steps,
            )
        )

    return RunStatus(
        run_id=run_id,
        status=run_data.get("status", "unknown"),
        conclusion=run_data.get("conclusion"),
        workflow_name=run_data.get("name", "unknown"),
        branch=run_data.get("head_branch", "unknown"),
        url=run_data.get("html_url", f"https://github.com/{repo}/actions/runs/{run_id}"),
        jobs=jobs_list,
    )


def format_step_icon(status: str, conclusion: str | None) -> str:
    if conclusion == "success":
        return "[PASS]"
    if conclusion == "failure":
        return "[FAIL]"
    if conclusion == "skipped":
        return "[SKIP]"
    if conclusion == "cancelled":
        return "[CNCL]"
    if status == "in_progress":
        return "[WAIT]"
    return "[PEND]"


def display_run_summary(run: RunStatus) -> None:
    print(f"\n=======================================================")
    print(f" Workflow Run: {run.workflow_name} (#{run.run_id})")
    print(f" Branch:       {run.branch}")
    print(f" Status:       {run.status.upper()}" + (f" ({run.conclusion.upper()})" if run.conclusion else ""))
    print(f" URL:          {run.url}")
    print(f"=======================================================")
    for job in run.jobs:
        job_concl = f" ({job.conclusion})" if job.conclusion else ""
        print(f"\nJob: {job.name} [{job.status}{job_concl}] (ID: {job.job_id})")
        for step in job.steps:
            icon = format_step_icon(step.status, step.conclusion)
            print(f"  {icon} {step.name}")


def analyze_failures(repo: str, run_id: int, run: RunStatus) -> dict[str, Any]:
    analysis: dict[str, Any] = {
        "run_id": run_id,
        "failed_jobs": [],
        "root_causes": [],
    }

    for job in run.jobs:
        if job.conclusion != "failure":
            continue

        failed_step_names = [s.name for s in job.steps if s.conclusion == "failure"]
        job_info: dict[str, Any] = {
            "job_id": job.job_id,
            "job_name": job.name,
            "failed_steps": failed_step_names,
            "error_snippets": [],
        }

        # Fetch failed logs using gh CLI
        code, stdout, _ = run_gh_command(["run", "view", "--log-failed", "--job", str(job.job_id), "--repo", repo])
        log_text = stdout if code == 0 else ""

        # Pattern matching for common failure classes
        detected_causes = []

        if "expected 1 anchor, found" in log_text:
            m = re.findall(r"([^:\n]+): expected 1 anchor, found \d+", log_text)
            cause = f"Anchor drift in patch script: {', '.join(set(m)) if m else 'anchor mismatch'}"
            detected_causes.append(cause)

        if "shutil.Error:" in log_text or "No such file or directory" in log_text:
            m = re.findall(r"\[Errno 2\] No such file or directory: '([^']+)'", log_text)
            cause = f"Missing file or broken symlink: {', '.join(set(m)) if m else 'file not found'}"
            detected_causes.append(cause)

        if "FAILED (failures=" in log_text or "FAILED (errors=" in log_text:
            m = re.findall(r"(FAIL|ERROR): ([^\n]+)", log_text)
            failed_tests = [item[1] for item in m]
            cause = f"Unit test failures: {'; '.join(failed_tests[:5])}"
            detected_causes.append(cause)

        if "error: " in log_text or "swiftc:" in log_text:
            swift_errs = [line.strip() for line in log_text.splitlines() if "error:" in line and "warning:" not in line]
            if swift_errs:
                cause = f"Swift compilation error: {swift_errs[0]}"
                detected_causes.append(cause)

        if "error[E" in log_text or "cargo build" in log_text and "error" in log_text:
            cargo_errs = [line.strip() for line in log_text.splitlines() if "error[" in line or "error: could not compile" in line]
            if cargo_errs:
                cause = f"Rust compilation error: {cargo_errs[0]}"
                detected_causes.append(cause)

        if not detected_causes:
            detected_causes.append("General step execution error (inspect log lines for details)")

        job_info["detected_causes"] = detected_causes
        # Extract last 15 lines of relevant log context
        job_info["log_tail"] = [line for line in log_text.splitlines() if line.strip()][-15:]
        analysis["failed_jobs"].append(job_info)
        analysis["root_causes"].extend(detected_causes)

    return analysis


def download_artifacts(repo: str, run_id: int, dest_dir: Path) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    code, stdout, stderr = run_gh_command([
        "run", "download", str(run_id),
        "--repo", repo,
        "--dir", str(dest_dir),
    ])
    downloaded = []
    if code == 0:
        for p in dest_dir.rglob("*"):
            if p.is_file():
                downloaded.append(p)
    return downloaded


def monitor_run(
    repo: str,
    run_id: int,
    watch: bool = True,
    interval: int = 15,
    timeout: int = 1800,
    download_on_success: bool = False,
    output_dir: Path | None = None,
) -> int:
    start_time = time.time()
    last_printed_status = ""

    while True:
        try:
            status = fetch_run_status(repo, run_id)
        except Exception as e:
            print(f"Warning: Failed to fetch run status: {e}", file=sys.stderr)
            time.sleep(interval)
            continue

        current_summary = f"{status.status}:{status.conclusion}"
        if current_summary != last_printed_status or not watch:
            display_run_summary(status)
            last_printed_status = current_summary

        if status.status == "completed":
            if status.conclusion == "success":
                print(f"\nWorkflow run {run_id} SUCCEEDED!")
                if download_on_success:
                    target_dir = output_dir or (ROOT / "artifacts" / "builds" / str(run_id))
                    print(f"Downloading build artifacts to {target_dir}...")
                    files = download_artifacts(repo, run_id, target_dir)
                    print(f"Downloaded {len(files)} artifact files.")
                return 0
            else:
                print(f"\nWorkflow run {run_id} FAILED with conclusion: {status.conclusion}")
                print("\nInitiating automated root-cause failure analysis...")
                analysis = analyze_failures(repo, run_id, status)
                print("\n--- Failure Diagnostic Report ---")
                for job in analysis.get("failed_jobs", []):
                    print(f"\nJob: {job['job_name']} (Failed Steps: {', '.join(job['failed_steps'])})")
                    for cause in job.get("detected_causes", []):
                        print(f"  * Root Cause: {cause}")
                    print("  * Log Snippet:")
                    for line in job.get("log_tail", []):
                        print(f"    {line}")
                return 1

        if not watch:
            return 0

        elapsed = time.time() - start_time
        if elapsed > timeout:
            print(f"\nTimeout exceeded ({timeout}s) waiting for run {run_id} to finish.", file=sys.stderr)
            return 2

        time.sleep(interval)


def create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automated CI Build Monitor & Failure Analyzer")
    parser.add_argument("run_id", nargs="?", type=int, help="GitHub Actions run ID to monitor (defaults to latest)")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"GitHub repository (default: {DEFAULT_REPO})")
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW, help=f"Workflow file (default: {DEFAULT_WORKFLOW})")
    parser.add_argument("--branch", default=None, help="Branch name filter (default: current git branch)")
    parser.add_argument("--no-watch", dest="watch", action="store_false", help="Check status once without waiting")
    parser.add_argument("--interval", type=int, default=15, help="Poll interval in seconds (default: 15)")
    parser.add_argument("--timeout", type=int, default=1800, help="Max wait time in seconds (default: 1800)")
    parser.add_argument("--download-artifacts", action="store_true", help="Download artifacts on success")
    parser.add_argument("--dest", type=Path, default=None, help="Destination directory for downloaded artifacts")
    parser.add_argument("--json", action="store_true", help="Output raw JSON status")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return create_argument_parser().parse_args(argv)


def main() -> None:
    args = parse_args()

    run_id = args.run_id
    branch = args.branch or get_current_branch()

    if not run_id:
        print(f"Resolving latest workflow run for {args.repo} ({args.workflow}) on branch '{branch}'...")
        run_id = get_latest_run_id(args.repo, args.workflow, branch)
        if not run_id:
            print(f"Error: Could not locate any workflow run for {args.workflow}.", file=sys.stderr)
            sys.exit(1)
        print(f"Monitoring latest run ID: {run_id}")

    if args.json:
        status = fetch_run_status(args.repo, run_id)
        print(json.dumps(status.__dict__, default=lambda o: o.__dict__, indent=2))
        sys.exit(0 if status.conclusion == "success" else 1)

    exit_code = monitor_run(
        repo=args.repo,
        run_id=run_id,
        watch=args.watch,
        interval=args.interval,
        timeout=args.timeout,
        download_on_success=args.download_artifacts,
        output_dir=args.dest,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
