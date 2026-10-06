#!/usr/bin/env python3
"""Select an explicit CI lane; unknown triggers/inputs fail closed."""
from __future__ import annotations

import os
from pathlib import Path

AUDIT_BRANCH = "refs/heads/fix/v3.1.0-audit"
# Complete acceptance is the default audit-push lane. Diagnostic mode remains
# an explicit manual selection and cannot satisfy preflight or release gates.
AUDIT_PUSH_LANE = "preflight"
RELEASE_PUSH_BRANCHES = {
    "refs/heads/fix/combined-refresh-build-and-runtime",
    "refs/heads/fix/v3.0.3-auth-errors",
}


def select_lane(event: str, ref: str, mode: str) -> str:
    if event == "push":
        if mode:
            raise ValueError("Push events cannot supply a manual lane")
        if ref == AUDIT_BRANCH:
            if AUDIT_PUSH_LANE not in ("preflight", "input-diagnostic"):
                raise ValueError("Invalid audit push lane")
            return AUDIT_PUSH_LANE
        if ref in RELEASE_PUSH_BRANCHES:
            return "release"
        raise ValueError("Unsupported push branch")
    if event == "workflow_dispatch":
        if not any(ref.startswith(prefix) and len(ref) > len(prefix)
                   for prefix in ("refs/heads/", "refs/tags/")):
            raise ValueError("Manual workflow requires an explicit Git ref")
        # An empty input preserves the registered default-branch dispatch form,
        # which may not yet expose the new choice on the selected audit branch.
        if mode in ("", "release"):
            return "release"
        if mode in ("preflight", "input-diagnostic"):
            return mode
        raise ValueError("Unknown manual lane")
    raise ValueError("Unsupported workflow event")


def main() -> None:
    lane = select_lane(os.environ.get("CI_EVENT_NAME", ""),
                       os.environ.get("CI_REF", ""), os.environ.get("CI_MODE", ""))
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        raise ValueError("GitHub output file is required")
    with Path(output).open("a", encoding="utf-8") as stream:
        stream.write(f"lane={lane}\n")
    print(f"Selected {lane} lane")


if __name__ == "__main__":
    main()
