"""Structural regression check for the GENERATED host Settings list.

The physical defect: in Settings, immediately after the "Guest Runtime" section,
an empty-looking label/row rendered. Root cause: the hidden programmatic
JIT-Less diagnose NavigationLink was injected as a bare top-level child of the
Settings `Form`. Its label is `EmptyView()`, and a top-level NavigationLink in a
Form still occupies a row, so the list drew a blank, tappable-looking cell.

This test inspects the final generated host UI, not just the template: it
replays the real patchers against the pinned LiveContainer tree, then flattens
the generated Settings list and splices in the generated V3AccountSettings body.

Checks:
- No empty actionable row renders anywhere in the generated Settings list.
- The item following "Guest Runtime" is a Section or the end of the list.
- No Section renders an empty body.
- The check has teeth: a synthetic empty-label row is detected.
"""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TEMPLATE = SCRIPTS / "templates" / "v3_unified_shell.swift"
PINNED = ROOT / ".audit/upstream/LiveContainer"
PINNED_SIDESTORE = ROOT / ".audit/v103-sources/SideStore"
SETTINGS_RELATIVE = "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"
SHELL_RELATIVE = "LiveContainerSwiftUI/Views/V3UnifiedShell.swift"

ACTIONABLE = ("NavigationLink", "Button", "Link", "Toggle", "Picker", "TextField",
              "ColorPicker", "Stepper", "Menu", "V3SignInLink", "V3BoolSettingRow")

_CACHE = {}


def strip_comments_and_strings(text: str) -> str:
    out, index, length = [], 0, len(text)
    while index < length:
        char = text[index]
        if text.startswith("//", index):
            index = text.find("\n", index)
            if index < 0:
                break
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index)
            index = length if end < 0 else end + 2
            continue
        if char == '"':
            index += 1
            while index < length and text[index] != '"':
                index += 2 if text[index] == "\\" else 1
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def mask(text: str) -> str:
    """Blank string and comment interiors while preserving every offset.

    Brace depth must ignore braces inside literals, but section titles and label
    text must stay readable, so the original text is kept for extraction and
    this same-length mask is used only for structure.
    """
    out, index, length = list(text), 0, len(text)
    while index < length:
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = length if end < 0 else end
            for position in range(index, end):
                out[position] = " "
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index)
            end = length if end < 0 else end + 2
            for position in range(index, end):
                if out[position] != "\n":
                    out[position] = " "
            index = end
            continue
        if text[index] == '"':
            index += 1
            while index < length and text[index] != '"':
                if text[index] == "\\":
                    out[index] = " "
                    index += 1
                if index < length and text[index] != '"':
                    out[index] = "x" if text[index] != "\n" else "\n"
                index += 1
            index += 1
            continue
        index += 1
    return "".join(out)


def body_span(masked: str, anchor: str):
    """Offsets of the brace-balanced body that follows `anchor`."""
    start = masked.index(anchor) + len(anchor)
    depth, index = 1, start
    while depth and index < len(masked):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
        index += 1
    return start, index - 1


def child_spans(masked: str, start: int, end: int):
    """Direct child spans of a ViewBuilder body, by brace depth."""
    spans, depth, begin = [], 0, start
    for index in range(start, end):
        char = masked[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if depth == 0 and char == "\n":
            if masked[begin:index].strip():
                spans.append((begin, index))
            begin = index + 1
    if masked[begin:end].strip():
        spans.append((begin, end))
    return spans


MODIFIER_ONLY = re.compile(r"^\s*\.[A-Za-z]")


def rows(masked: str, start: int, end: int, text: str):
    """Group direct children into rendered rows: a view plus its modifiers."""
    grouped = []
    for begin, stop in child_spans(masked, start, end):
        fragment = text[begin:stop]
        if grouped and MODIFIER_ONLY.match(fragment) and not fragment.strip().startswith("}"):
            grouped[-1][1] = stop
            grouped[-1][2] += fragment
            continue
        grouped.append([begin, stop, fragment])
    return [(begin, stop, fragment) for begin, stop, fragment in grouped]


def generate() -> dict:
    """Replay the CI patchers once and cache the generated host sources."""
    if _CACHE:
        return _CACHE
    if not PINNED.exists() or not PINNED_SIDESTORE.exists():
        raise unittest.SkipTest("pinned upstream trees unavailable")
    sys.path.insert(0, str(SCRIPTS))
    try:
        import patch_livecontainer_autorefresh
        import patch_v3_unified_shell
        import patch_v3_service
    finally:
        sys.path.pop(0)
    workspace = Path(tempfile.mkdtemp(prefix="settings-layout-"))
    try:
        live = workspace / "LiveContainer"
        side = workspace / "EmbeddedSideStore"
        shutil.copytree(PINNED, live)
        shutil.copytree(PINNED_SIDESTORE, side)
        # The real patchers verify the pinned revision through git. The
        # disposable copies are not repositories, so the verified pin is supplied
        # directly while every other check in the patchers still runs for real.
        real_check_output = subprocess.check_output
        pins = iter(patch_v3_service.PINS)

        def checked(command, *args, **kwargs):
            if isinstance(command, (list, tuple)) and "rev-parse" in [str(part) for part in command]:
                return f"{next(pins)}\n"
            return real_check_output(command, *args, **kwargs)

        # Replay the CI patch order that shapes the Settings Form.
        patch_livecontainer_autorefresh.patch_host_delegate(live)
        patch_livecontainer_autorefresh.patch_settings(live)
        patch_v3_unified_shell.patch_host(live)
        with mock.patch.object(patch_v3_service.subprocess, "check_output", checked):
            patch_v3_service.patch(live, side)
        _CACHE.update({
            "settings": (live / SETTINGS_RELATIVE).read_text(encoding="utf-8"),
            "shell": (live / SHELL_RELATIVE).read_text(encoding="utf-8"),
        })
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return _CACHE


def tearDownModule():
    _CACHE.clear()


def settings_rows() -> list:
    """The rendered rows of the generated Settings Form, V3 sections spliced in."""
    generated = generate()
    settings, shell = generated["settings"], generated["shell"]
    masked_settings, masked_shell = mask(settings), mask(shell)
    start, end = body_span(masked_settings, "Form {")
    struct_start, struct_end = body_span(masked_shell, "struct V3AccountSettings")
    account_start, account_end = body_span(masked_shell[struct_start:struct_end], "var body: some View {")
    account = [fragment for _, _, fragment
               in rows(masked_shell, struct_start + account_start, struct_start + account_end, shell)]
    result = []
    for _, _, fragment in rows(masked_settings, start, end, settings):
        if fragment.strip().startswith("V3AccountSettings()"):
            result.extend(fragment for fragment in account
                          if section_name(fragment) is not None)
        else:
            result.append(fragment)
    return result



ROW_NEUTRALIZERS = (".listRowInsets(", ".listRowSeparator(.hidden)", ".frame(width: 0, height: 0)",
                    ".accessibilityHidden(true)")


def section_name(fragment: str):
    match = re.match(r'\s*Section\("([^"]+)"\)', fragment)
    return match.group(1) if match else None


def first_actionable(fragment: str):
    return next((name for name in ACTIONABLE if re.search(rf"\b{name}\b", fragment)), None)


def renders_an_empty_cell(fragment: str) -> bool:
    """A top-level actionable row with an EmptyView label and no neutralizer."""
    if fragment.strip().startswith("Section"):
        return False
    if first_actionable(fragment) is None:
        return False
    if "EmptyView()" not in fragment:
        return False
    return not any(neutralizer in fragment for neutralizer in ROW_NEUTRALIZERS)


class GeneratedSettingsLayoutTests(unittest.TestCase):
    # -- the defect itself ------------------------------------------------

    def test_no_empty_actionable_row_after_guest_runtime(self):
        children = settings_rows()
        names = [section_name(child) for child in children]
        self.assertIn("Guest Runtime", names,
                      f"Guest Runtime section missing from the generated list: {names}")
        for fragment in children[names.index("Guest Runtime") + 1:]:
            self.assertFalse(renders_an_empty_cell(fragment),
                             f"an empty actionable row renders after Guest Runtime: {fragment[:200]!r}")

    def test_the_item_after_guest_runtime_is_never_a_rendered_row(self):
        # The canonical JIT-Less route stays a top-level sibling, but it is
        # row-neutralized, so nothing renders between the two sections.
        children = settings_rows()
        names = [section_name(child) for child in children]
        index = names.index("Guest Runtime")
        following = children[index + 1:]
        for fragment in following:
            stripped = fragment.strip()
            # A conditional block is not itself a row; its Section children are.
            if stripped.startswith(("Section", "if ", "ForEach", "Group", "} else")):
                continue
            actionable = first_actionable(fragment)
            if actionable is None:
                continue
            self.assertTrue(any(neutralizer in fragment for neutralizer in ROW_NEUTRALIZERS),
                            f"a bare actionable {actionable} row follows Guest Runtime: {fragment[:200]!r}")
        self.assertTrue(any(fragment.strip().startswith("Section") for fragment in following),
                        "no section follows Guest Runtime in the generated list")

    def test_generated_account_sections_keep_their_real_actions(self):
        names = [section_name(child) for child in settings_rows()]
        for section in ("Setup", "Account and Signing", "Device", "Apps and Data",
                        "Services", "Diagnostics", "Guest Runtime"):
            self.assertIn(section, names, f"{section} is missing from the generated Settings list")

    def test_guest_runtime_keeps_its_real_visible_action(self):
        # Guest Runtime itself is not redesigned; it still offers Tweaks.
        self.assertIn("Guest Runtime", generate()["shell"])
        self.assertIn('Label("Tweaks", systemImage: "slider.vertical.3")',
                      TEMPLATE.read_text(encoding="utf-8"))

    # -- the class of defect ----------------------------------------------

    def test_no_actionable_row_anywhere_renders_an_empty_cell(self):
        for fragment in settings_rows():
            self.assertFalse(renders_an_empty_cell(fragment),
                             f"an actionable row renders as an empty cell: {fragment[:200]!r}")

    def test_the_hidden_jitless_route_is_row_neutralized(self):
        text = generate()["settings"]
        self.assertIn("V3_JITLESS_ROUTE_ROW_NEUTRALIZED_V1", text)
        start = text.index("NavigationLink(destination: LCJITLessDiagnoseView()")
        block = text[start:start + 600]
        for neutralizer in ROW_NEUTRALIZERS:
            self.assertIn(neutralizer, block,
                          f"the hidden route must neutralize its row footprint ({neutralizer})")

    def test_no_section_renders_an_empty_body(self):
        text = mask(generate()["settings"])
        self.assertIsNone(re.search(r"Section[^{]*\{\s*\}", text),
                          "a Section with an empty body renders as layout residue")

    # -- the check must have teeth ----------------------------------------

    def test_the_check_detects_a_synthetic_empty_row(self):
        bare = 'NavigationLink(destination: LCJITLessDiagnoseView(), isActive: $x) { EmptyView() }.hidden()'
        self.assertTrue(renders_an_empty_cell(bare),
                        "an unneutralized EmptyView-labelled row must be flagged")

    def test_a_row_neutralized_route_is_accepted(self):
        neutralized = ('NavigationLink(destination: LCJITLessDiagnoseView(), isActive: $x) { EmptyView() }\n'
                       '                    .frame(width: 0, height: 0)\n'
                       '                    .listRowInsets(EdgeInsets())\n'
                       '                    .listRowSeparator(.hidden)\n'
                       '                    .accessibilityHidden(true)\n'
                       '                    .hidden()')
        self.assertFalse(renders_an_empty_cell(neutralized))

    def test_a_populated_row_is_accepted(self):
        populated = 'NavigationLink { LCJITLessDiagnoseView() } label: { Label("Diagnose", systemImage: "x") }'
        self.assertFalse(renders_an_empty_cell(populated))

    def test_a_row_placed_after_guest_runtime_is_caught(self):
        children = ['Section("Guest Runtime") { Label("Tweaks", systemImage: "x") }',
                    'NavigationLink(destination: Text("x"), isActive: $y) { EmptyView() }.hidden()',
                    'Section("Guest Controls") { Text("x") }']
        names = [section_name(child) for child in children]
        self.assertTrue(renders_an_empty_cell(children[names.index("Guest Runtime") + 1]))


if __name__ == "__main__":
    unittest.main()