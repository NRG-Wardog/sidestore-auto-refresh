from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build-current.yml"
SCRIPTS = ROOT / "scripts"
REQUIRED_SCRIPTS = {
    "patch_jktcp_reliability.py",
    "patch_coredevice_idevice.py",
    "patch_sidestore_integration.py",
    "patch_background_automation.py",
    "patch_local_idevice_package.py",
    "adapt_sidestore_070_pairing.py",
    "adapt_sidestore_070_signing.py",
}
LIVE_CONTAINER_SCRIPT = "patch_livecontainer_autorefresh.py"
LIVE_CONTAINER_STARTUP_SCRIPT = "patch_embedded_sidestore_startup.py"
COMBINED_REFRESH_SCRIPT = "patch_combined_refresh_contract.py"
EMBEDDED_KEYCHAIN_SCRIPT = "patch_embedded_keychain.py"


class RepositoryTests(unittest.TestCase):
    def test_current_files_exist(self):
        self.assertTrue((ROOT / "README.md").is_file())
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / "CONTRIBUTING.md").is_file())
        self.assertTrue((ROOT / "SECURITY.md").is_file())
        self.assertTrue((ROOT / "docs" / "VERIFICATION.md").is_file())
        self.assertTrue(WORKFLOW.is_file())
        self.assertEqual(
            {path.name for path in SCRIPTS.glob("*.py")},
            REQUIRED_SCRIPTS | {LIVE_CONTAINER_SCRIPT, LIVE_CONTAINER_STARTUP_SCRIPT,
                                COMBINED_REFRESH_SCRIPT, EMBEDDED_KEYCHAIN_SCRIPT, 'audit_ipa_signing.py', 'patch_guest_return.py',
                                'package_livecontainer_combined.py', 'patch_combined_transport.py', 'patch_refresh_result_bridge.py'},
        )

    def test_patch_scripts_parse_and_are_idempotent(self):
        for name in REQUIRED_SCRIPTS | {LIVE_CONTAINER_SCRIPT, LIVE_CONTAINER_STARTUP_SCRIPT,
                                        COMBINED_REFRESH_SCRIPT, EMBEDDED_KEYCHAIN_SCRIPT, "patch_combined_transport.py", "patch_refresh_result_bridge.py"}:
            path = SCRIPTS / name
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        self.assertIn(
            "if MARKER in text",
            (SCRIPTS / "patch_background_automation.py").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "if MARKER in text",
            (SCRIPTS / "patch_coredevice_idevice.py").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "if MARKER in text",
            (SCRIPTS / "patch_sidestore_integration.py").read_text(encoding="utf-8"),
        )

    def test_workflow_references_current_scripts(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        references = set(re.findall(r"builder/scripts/([A-Za-z0-9_.-]+\.py)", workflow))
        excluded_from_current = {"adapt_sidestore_070_pairing.py", "adapt_sidestore_070_signing.py"}
        self.assertTrue({name for name in REQUIRED_SCRIPTS if name not in excluded_from_current}.issubset(references))
        self.assertNotRegex(workflow, r"builder/scripts/patch_v\d+")
        self.assertNotIn("build-v29-coredevice-self-refresh.yml", workflow)
        live_workflow = (ROOT / ".github/workflows/livecontainer-build.yml").read_text(encoding="utf-8")
        self.assertIn("builder/scripts/" + LIVE_CONTAINER_STARTUP_SCRIPT, live_workflow)
        self.assertIn("builder/scripts/" + COMBINED_REFRESH_SCRIPT, live_workflow)
        contract = (SCRIPTS / COMBINED_REFRESH_SCRIPT).read_text(encoding="utf-8")
        self.assertIn("from patch_embedded_keychain import patch as patch_shared_keychain", contract)
        self.assertIn("patch_shared_keychain(Path(sys.argv[1]))", contract)
        self.assertIn("[LC_KEYCHAIN] SHARED_GROUP_SELECTED", contract)

    def test_upstream_ipsec_anchor_preserves_original_punctuation(self):
        source = (SCRIPTS / "patch_sidestore_integration.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        anchors = [
            ast.literal_eval(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "old_ipsec_requirement"
                    for target in node.targets)
        ]
        self.assertEqual(len(anchors), 1)
        self.assertIn("interface found \u2014 LocalDevVPN", anchors[0])
        self.assertNotIn(chr(0x2014), source)

    def test_no_sensitive_paths_are_reachable(self):
        result = subprocess.run(
            ["git", "rev-list", "--objects", "--all"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertNotRegex(
            result.stdout,
            r"(?i)(pairingFile|rppairing|client\.p12|client_(cert|key)|LockdownDirectDiag|\.der$)",
        )

    def test_public_docs_do_not_expose_known_private_network_details(self):
        """Generic RFC1918 examples are allowed; known diagnostic addresses are not."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        verification = (ROOT / "docs" / "VERIFICATION.md").read_text(encoding="utf-8")
        public_docs = readme + "\n" + verification

        # These were diagnostic/local addresses and must never leak into public docs.
        for sensitive_ip in ("10.7.0.1", "10.7.0.2"):
            self.assertNotIn(sensitive_ip, public_docs)

        # Documentation may intentionally use RFC1918 examples such as
        # 10.0.0.x or 192.168.1.x to explain same-subnet LocalDevVPN routing.
        self.assertIn("same subnet", readme)
        self.assertIn("/32", readme)


if __name__ == "__main__":
    unittest.main()
