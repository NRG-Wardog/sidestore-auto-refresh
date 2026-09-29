"""Exercise SideSign editor ownership and bind the policy to the real view."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIMITIVES = ROOT / "scripts/templates/v3_behavioral_primitives.swift"
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
HARNESS = ROOT / "tests/fixtures/v3_sidesign_reload_owner_harness.swift"


def extract_swift_declaration(source: str, declaration: str) -> str:
    start = source.find(declaration)
    if start < 0:
        raise AssertionError(f"production declaration not found: {declaration}")
    opening = source.find("{", start)
    if opening < 0:
        raise AssertionError(f"declaration has no body: {declaration}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated declaration: {declaration}")


class SideSignReloadOwnerTests(unittest.TestCase):
    def test_production_owner_policy_interleavings_execute(self):
        swiftc = shutil.which("swiftc")
        if sys.platform != "darwin" or not swiftc:
            self.skipTest("Swift behavioral harness requires macOS CI with swiftc")

        primitives = PRIMITIVES.read_text(encoding="utf-8")
        declarations = [
            extract_swift_declaration(primitives, "struct V3AsyncRequestOwner:"),
            extract_swift_declaration(primitives, "struct V3AsyncRequestOwnerState:"),
        ]
        harness = HARNESS.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            main = directory / "main.swift"
            executable = directory / "sidesign-reload-owner"
            main.write_text("import Foundation\n" + "\n".join(declarations) + "\n" + harness,
                            encoding="utf-8")
            compiled = subprocess.run([swiftc, "-parse-as-library", str(main), "-o", str(executable)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("V3_SIDESIGN_RELOAD_OWNER_PASS", result.stdout)

    def test_side_sign_view_binds_reload_and_mutations_to_editor_owner(self):
        shell = SHELL.read_text(encoding="utf-8")
        view_start = shell.index("struct V3SideSignView: View {")
        view_end = shell.index("\nstruct V3ShareBox", view_start)
        view = shell[view_start:view_end]

        self.assertIn(".onChange(of: config) { _ in invalidateConfigRequestOwner() }", view)
        self.assertIn("editorVisible = false", view)
        self.assertIn("invalidateConfigRequestOwner()", view)
        for method in ["reload", "save", "remote", "importFile", "exportConfig"]:
            body = extract_swift_declaration(view, f"private func {method}(")
            self.assertIn('configRequestOwners.begin(bindingID: "sidesign-config-editor")', body)
            self.assertIn("mayApply(owner, capturedEditorRevision: capturedRevision)", body)
        self.assertIn("config == capturedConfig", view)
        self.assertIn("config == submittedConfig", view)
        for method in ["save", "remote", "importFile"]:
            body = extract_swift_declaration(view, f"private func {method}(")
            self.assertIn("reportStaleMutationIfDraftChanged", body,
                "a successful dispatched mutation must disclose that the backend changed after a newer edit")

    def test_anisette_list_reply_cannot_overwrite_newer_reset_or_sync(self):
        shell = SHELL.read_text(encoding="utf-8")
        start = shell.index("struct V3AnisetteView: View {")
        end = shell.index("\nstruct V3SideSignView", start)
        view = shell[start:end]
        self.assertIn(".onDisappear { serverRequestOwners.invalidate() }", view)
        for method in ["reload", "remote"]:
            body = extract_swift_declaration(view, f"private func {method}(")
            self.assertIn("serverRequestOwners.begin(bindingID: \"anisette-servers\")", body)
            self.assertIn("serverRequestOwners.owns(owner, bindingID: \"anisette-servers\")", body)
        button_start = view.index('Button("Use This Server")')
        button_end = view.index(".font(.caption)", button_start)
        selection = view[button_start:button_end]
        self.assertIn('serverRequestOwners.begin(bindingID: "anisette-servers")', selection)
        self.assertIn('await store.setStringAndWait("menuAnisetteURL", server.address)', selection)
        self.assertIn('serverRequestOwners.owns(owner, bindingID: "anisette-servers")', selection)
        self.assertIn("guard !remoteBusy else { return }", view)
        self.assertIn(".disabled(remoteBusy)", view)


if __name__ == "__main__":
    unittest.main()
