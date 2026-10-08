"""Exact, fail-closed prepared-source verification; never Apple or device IO."""
from pathlib import Path
import importlib.util
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepared_anisette_boundary", ROOT / "scripts/verify_prepared_anisette.py")
verifier = importlib.util.module_from_spec(SPEC)
# Match the standalone script's import path without relying on test runner cwd.
import sys
sys.path.insert(0, str(ROOT / "scripts"))
SPEC.loader.exec_module(verifier)


class PreparedAnisetteBoundaryTests(unittest.TestCase):
    def setUp(self):
        source = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
        if not source:
            self.skipTest("Pinned SideStore input required; supplied by combined macOS CI")
        self.temporary = tempfile.TemporaryDirectory(prefix="anisette-boundary-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "SideStore"
        subprocess.run(["git", "clone", "--shared", "--no-checkout", source, str(self.root)],
                       check=True, capture_output=True)
        # Detach exactly at the required object without checking out unrelated
        # files or dependencies; source checkout remains read-only.
        self.git("update-ref", "--no-deref", "HEAD", verifier.PIN)
        self.git("checkout", verifier.PIN, "--", verifier.DIRECTORY)
        self.paths = verifier.pinned_inventory(self.root)
        for relative in verifier.ANISETTE_PATHS:
            path = self.root / relative
            original = path.read_text()
            if relative == verifier.ANISETTE_PATHS[0]:
                prepared = verifier.patch_anisette_config(original)
            else:
                prepared = verifier.patch_anisette_provider(original, on_device=relative == verifier.ANISETTE_PATHS[1])
            path.write_text(prepared)

    def git(self, *arguments):
        return subprocess.check_output(["git", "-C", str(self.root), *arguments], stderr=subprocess.STDOUT)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in (self.root / verifier.DIRECTORY).rglob("*") if p.is_file()}

    def test_exact_transforms_pass_repeatedly_without_writes(self):
        before = self.snapshot()
        self.assertEqual(verifier.verify(self.root), len(self.paths))
        self.assertEqual(verifier.verify(self.root), len(self.paths))
        self.assertEqual(self.snapshot(), before)
        for relative in verifier.ANISETTE_PATHS:
            prepared = (self.root / relative).read_text()
            replay = (verifier.patch_anisette_config(prepared) if relative == verifier.ANISETTE_PATHS[0]
                      else verifier.patch_anisette_provider(prepared, on_device=relative == verifier.ANISETTE_PATHS[1]))
            self.assertEqual(replay, prepared)
        result = subprocess.run([sys.executable, str(ROOT / "scripts/verify_prepared_anisette.py"), str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ANISETTE_PINNED_BOUNDARY_PASS", result.stdout)

    def test_each_allowed_file_rejects_extra_edits_even_with_guard_markers(self):
        for relative in verifier.ANISETTE_PATHS:
            with self.subTest(relative=relative):
                path = self.root / relative
                prepared = path.read_bytes()
                path.write_bytes(prepared + b"\n// unexpected extra source change\n")
                with self.assertRaisesRegex(ValueError, "exact pinned transform"):
                    verifier.verify(self.root)
                self.assertEqual(path.read_bytes(), prepared + b"\n// unexpected extra source change\n")
                path.write_bytes(prepared)

    def test_each_allowed_file_rejects_missing_transform(self):
        for relative in verifier.ANISETTE_PATHS:
            with self.subTest(relative=relative):
                path = self.root / relative
                prepared = path.read_bytes()
                path.write_bytes(self.git("show", f"{verifier.PIN}:{relative}"))
                with self.assertRaisesRegex(ValueError, "exact pinned transform"):
                    verifier.verify(self.root)
                path.write_bytes(prepared)

    def test_every_unrelated_anisette_file_rejects_modification(self):
        untouched = set(self.paths) - set(verifier.ANISETTE_PATHS)
        self.assertTrue(untouched)
        for relative in untouched:
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_bytes()
                path.write_bytes(original + b"\n// unexpected unrelated edit\n")
                with self.assertRaisesRegex(ValueError, "exact pinned transform"):
                    verifier.verify(self.root)
                path.write_bytes(original)

    def test_added_untracked_staged_and_ignored_files_are_rejected(self):
        relative = verifier.DIRECTORY + "/Unexpected.swift"
        path = self.root / relative
        path.write_text("// extra source\n")
        with self.assertRaisesRegex(ValueError, "inventory drift"):
            verifier.verify(self.root)
        self.git("add", "--", relative)
        with self.assertRaisesRegex(ValueError, "inventory drift"):
            verifier.verify(self.root)
        self.git("reset", "--", relative)
        (self.root / ".git/info/exclude").write_text(relative + "\n")
        self.assertEqual(self.git("ls-files", "--others", "--exclude-standard", "--", relative), b"")
        with self.assertRaisesRegex(ValueError, "inventory drift"):
            verifier.verify(self.root)

    def test_missing_files_and_symbolic_links_are_rejected(self):
        for relative in self.paths:
            with self.subTest(relative=relative):
                path = self.root / relative
                saved = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(ValueError, "inventory drift"):
                    verifier.verify(self.root)
                external = Path(self.temporary.name) / "same-bytes.swift"
                external.write_bytes(saved)
                path.symlink_to(external)
                with self.assertRaisesRegex(ValueError, "file type"):
                    verifier.verify(self.root)
                path.unlink()
                path.write_bytes(saved)
        (self.root / verifier.DIRECTORY / "UnexpectedDirectory").symlink_to(self.temporary.name, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "directory link"):
            verifier.verify(self.root)

    def test_wrong_head_and_file_mode_are_rejected(self):
        relative = verifier.ANISETTE_PATHS[0]
        path = self.root / relative
        path.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "exact pinned transform"):
            verifier.verify(self.root)
        path.chmod(0o644)
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "Wrong input revision")
        with self.assertRaisesRegex(ValueError, "pinned embedded SideStore revision"):
            verifier.verify(self.root)


class PreparedAnisetteWorkflowTests(unittest.TestCase):
    def test_workflow_replaces_only_anisette_blanket_diff_and_keeps_other_boundaries(self):
        workflow = (ROOT / "migration/historical/livecontainer-build-141776ba.yml").read_text()
        step = workflow.split("      - name: Verify prepared authentication source boundaries", 1)[1].split("      - name:", 1)[0]
        self.assertIn("python3 builder/scripts/verify_prepared_anisette.py work/EmbeddedSideStore", step)
        self.assertNotIn("diff --exit-code -- SideStore/Core/Anisette", step)
        for required in ('test "$actual_auth" = "$expected_auth"',
                         'test "$actual_sidesign" = "$expected_sidesign"',
                         'test "$actual_side_logging" = "$expected_side_logging"',
                         'git -C "$SIDESTORE" diff --check',
                         'git -C "$SIDESIGN" diff --check',
                         'python3 builder/scripts/patch_sidesign_privacy.py "$SIDESIGN" "$SIDESTORE"',
                         'python3 builder/scripts/patch_sidesign_2fa_state.py "$SIDESIGN"',
                         'python3 builder/scripts/patch_combined_service_startup.py --portal "$SIDESIGN"'):
            self.assertIn(required, step)
        self.assertEqual(verifier.PIN, "ff25922e5c13ccfafd83bda5092910d848ebd409")


if __name__ == "__main__":
    unittest.main()
