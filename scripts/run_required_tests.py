#!/usr/bin/env python3
"""Run unittest discovery and fail if any test is skipped unexpectedly.

The combined macOS build uses this runner so Swift/Xcode and pinned-source
tests cannot silently skip while the job remains green. Any accepted skip must
be listed by exact unittest ID and exact reason in the reviewed JSON allowlist.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import unittest
from typing import List, Optional, Set, Tuple


SkipRecord = Tuple[str, str]


class RequiredTextTestResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.skip_records: list[SkipRecord] = []

    def addSkip(self, test, reason):
        self.skip_records.append((test.id(), str(reason)))
        super().addSkip(test, reason)


class RequiredTextTestRunner(unittest.TextTestRunner):
    resultclass = RequiredTextTestResult


def load_allowlist(path: Path) -> set[SkipRecord]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("skip allowlist must be a JSON array")
    records: set[SkipRecord] = set()
    for index, item in enumerate(raw):
        if (not isinstance(item, dict) or set(item) != {"test_id", "reason"} or
                not isinstance(item["test_id"], str) or not item["test_id"].strip() or
                not isinstance(item["reason"], str) or not item["reason"].strip()):
            raise ValueError(f"allowlist entry {index} must contain non-empty test_id and reason strings")
        record = (item["test_id"], item["reason"])
        if record in records:
            raise ValueError(f"duplicate skip allowlist entry: {record[0]}")
        records.add(record)
    return records


def unexpected_skips(observed: List[SkipRecord], allowed: Set[SkipRecord]) -> List[SkipRecord]:
    """Return each skip not authorized by an exact test-ID/reason pair."""
    return sorted(set(observed) - allowed)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-directory", type=Path, required=True)
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--top-level-directory", type=Path)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--verbosity", type=int, default=2)
    args = parser.parse_args(argv)

    try:
        allowed = load_allowlist(args.allowlist)
        suite = unittest.defaultTestLoader.discover(
            str(args.start_directory), pattern=args.pattern,
            top_level_dir=str(args.top_level_directory) if args.top_level_directory else None)
    except (OSError, ValueError, ImportError) as error:
        print(f"required test runner setup failed: {error}", file=sys.stderr)
        return 2

    result = RequiredTextTestRunner(verbosity=args.verbosity).run(suite)
    unexpected = unexpected_skips(result.skip_records, allowed)
    if result.testsRun == 0:
        print("required test runner discovered zero tests", file=sys.stderr)
    print(f"Required skip gate: observed={len(result.skip_records)} allowlisted="
          f"{len(result.skip_records) - len(unexpected)} unexpected={len(unexpected)}")
    for test_id, reason in unexpected:
        print(f"UNEXPECTED SKIP: {test_id} :: {reason}", file=sys.stderr)

    if not result.wasSuccessful():
        return 1
    if result.testsRun == 0:
        return 2
    if unexpected:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
