"""Verify patches against pinned runtime sources and preservation boundaries."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("guest_return", ROOT / "scripts/patch_guest_return.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GuestReturnTests(unittest.TestCase):
    def test_preservation_has_no_termination_or_new_session(self):
        code = module.METHODS
        self.assertIn("minimizeWindow", code)
        self.assertIn("self.lcActivateHost()", code)
        for forbidden in ("terminate]", "SIGKILL", "raise(", "launchToGuestApp", "NSExtension", "removeObjectForKey"):
            self.assertNotIn(forbidden, code)

    def test_control_is_event_driven_and_touch_transparent(self):
        self.assertIn("return hit == self ? nil : hit", module.CONTROL)
        self.assertIn("UIGestureRecognizerStateEnded", module.CONTROL)
        self.assertIn("isfinite(x)", module.CONTROL)
        self.assertIn("self.safeAreaInsets", module.CONTROL)
        self.assertIn("UIKeyboardWillChangeFrameNotification", module.CONTROL)
        for forbidden in ("NSTimer", "dispatch_after", "sleep("):
            self.assertNotIn(forbidden, module.CONTROL)

    def test_pinned_patch_and_idempotence(self):
        source = ROOT / ".audit/upstream/LiveContainer"
        if not source.exists():
            self.skipTest("Pinned LiveContainer source unavailable")
        paths = ("MultitaskSupport/AppSceneViewController.m", "MultitaskSupport/AppSceneViewController.h",
                 "MultitaskSupport/MultitaskAppWindow.swift", "MultitaskSupport/MultitaskDockView.swift",
                 "SideStoreSupport/SideStoreHooks.m", "LiveContainerSwiftUI/Models/LCAppModel.swift")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in paths:
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name, dest)
            module.patch(root)
            first = {name: (root / name).read_bytes() for name in paths}
            module.patch(root)
            self.assertEqual(first, {name: (root / name).read_bytes() for name in paths})
            window = (root / paths[2]).read_text()
            self.assertIn('returnOpenWindow(id: "Main")', window)
            self.assertIn("getpgid(a.value.pid)", window)
            self.assertIn("appDict[appInfo.dataUUID]?.pid = pid", window)


if __name__ == "__main__":
    unittest.main()
