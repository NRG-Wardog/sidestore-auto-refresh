"""Prove the C App Group harness can actually detect a broken rule set.

A behavioral harness that still prints its pass marker after the production rule
set has been broken certifies nothing: the previous harness compared one call
with itself and could not fail. Each mutation below breaks one rule in a
throwaway copy of the real header and requires the real harness to fail. The
repository is never modified.

This is the only executable coverage for the rule set that runs without a Swift
toolchain, so it is also the only thing standing between a renamed rule and a
green build.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "fixtures" / "v3_app_group_identity_rules_harness.c"
RULES = ROOT / "scripts" / "templates" / "LCAppGroupIdentityRules.h"
QUOTE = chr(39)
BACKSLASH = chr(92)

# (label, unique anchor line, replacement line)
MUTATIONS = (
    # LC_RULE_PACKAGED_FALLBACK_ONLY ordering: without the ranking the packaged
    # list is taken in Info.plist order and the host and service can disagree.
    ("packaged_order_loses_the_side_store_ranking",
     "if (LCAppGroupIsPackagedSideStoreGroup(candidate)) {",
     "if (candidate != NULL) {"),
    # LC_RULE_EXPLICIT_FAIL_CLOSED: without it an unusable runtime group silently
    # moves the shared store underneath the other process.
    ("unavailable_runtime_group_falls_through",
     "if (!runtimeGroupAvailable) {",
     "if (0) {"),
    # LC_RULE_GROUP_NO_SEPARATOR: an identifier is never a path.
    ("separator_is_accepted",
     "return value == '/' || value == " + QUOTE + BACKSLASH + BACKSLASH + QUOTE + ";",
     "return 0;"),
    # LC_RULE_GROUP_NO_TRAVERSAL, mid-string.
    ("traversal_is_accepted",
     "if (previous == (unsigned char)" + QUOTE + "." + QUOTE +
     " && current == (unsigned char)" + QUOTE + "." + QUOTE + ") {",
     "if (0) {"),
    # LC_RULE_GROUP_NO_TRAVERSAL, leading dot.
    ("leading_dot_is_accepted",
     "if ((unsigned char)bytes[0] == (unsigned char)" + QUOTE + "." + QUOTE + ") {",
     "if (0) {"),
    # LC_RULE_GROUP_BOUNDED_LENGTH: keeps an embedded payload out of a suite
    # name and a lock path.
    ("length_bound_is_removed",
     "length > (size_t)LC_APP_GROUP_IDENTIFIER_MAX_LENGTH) {",
     "length > (size_t)-1) {"),
    # LC_RULE_GROUP_VISIBLE_ASCII: a control character or whitespace in a
    # group identifier would reach a path and a suite name.
    ("non_visible_ascii_is_accepted",
     "return value >= 0x21u && value <= 0x7Eu;",
     "return value != 0;"),
    # LC_RULE_GROUP_NO_COLON: a suite name is never a URL scheme.
    ("colon_is_accepted",
     "return LCAppGroupRuleIsSeparator(value) || value == " + QUOTE + ":" + QUOTE + ";",
     "return LCAppGroupRuleIsSeparator(value);"),
)


def toolchain() -> str | None:
    return shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


def compile_and_run(workspace: Path) -> tuple[int, str]:
    binary = workspace / "harness"
    compiled = subprocess.run([toolchain(), "-std=c11", str(workspace / HARNESS.relative_to(ROOT)),
                               "-o", str(binary)], capture_output=True, text=True, timeout=180)
    if compiled.returncode:
        return compiled.returncode, "COMPILE FAILED: " + compiled.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout + result.stderr


class AppGroupRuleMutationTests(unittest.TestCase):
    def test_every_rule_mutation_is_detected_by_the_real_harness(self):
        if not toolchain():
            self.skipTest("no C toolchain available; mutation check runs in macOS CI")
        original = RULES.read_text(encoding="utf-8")
        for label, anchor, replacement in MUTATIONS:
            with self.subTest(mutation=label):
                self.assertEqual(original.count(anchor), 1,
                                 f"the mutation anchor for {label} is not unique in the rule set")
                with tempfile.TemporaryDirectory() as directory:
                    workspace = Path(directory)
                    # The harness includes the header as
                    # ../../scripts/templates/..., so reproduce that layout.
                    (workspace / "tests" / "fixtures").mkdir(parents=True)
                    (workspace / "scripts" / "templates").mkdir(parents=True)
                    shutil.copy(HARNESS, workspace / HARNESS.relative_to(ROOT))
                    staged = workspace / RULES.relative_to(ROOT)
                    shutil.copy(RULES, staged)
                    staged.write_text(original.replace(anchor, replacement), encoding="utf-8")
                    code, output = compile_and_run(workspace)
                    self.assertNotEqual(code, 0,
                                        f"the harness passed against a rule set with {label} broken")
                    self.assertNotIn("V3_APP_GROUP_IDENTITY_PASS", output,
                                     f"the harness still reported a pass with {label} broken")

    def test_the_unmutated_rule_set_passes(self):
        if not toolchain():
            self.skipTest("no C toolchain available; mutation check runs in macOS CI")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "tests" / "fixtures").mkdir(parents=True)
            (workspace / "scripts" / "templates").mkdir(parents=True)
            shutil.copy(HARNESS, workspace / HARNESS.relative_to(ROOT))
            shutil.copy(RULES, workspace / RULES.relative_to(ROOT))
            code, output = compile_and_run(workspace)
            self.assertEqual(code, 0, output)
            self.assertIn("V3_APP_GROUP_IDENTITY_PASS", output)


if __name__ == "__main__":
    unittest.main()
