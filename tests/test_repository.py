from __future__ import annotations

import ast
import hashlib
import json
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
LIVE_CONTAINER_SCRIPTS = {
    "patch_native_error_presenters.py",
    "patch_cf_bundle_scan.py",
    "patch_lc_certificate_observation.py",
    "patch_livecontainer_autorefresh.py",
    "patch_embedded_sidestore_startup.py",
    "patch_v3_unified_shell.py",
    "patch_sidesign_2fa_state.py",
    "patch_v3_service.py",
    "patch_combined_refresh_contract.py",
    "patch_embedded_keychain.py",
    "patch_guest_return.py",
    "patch_multitask_dock.py",
    "patch_dead10cc_fix.py",
    "patch_combined_refresh_contract.py",
    "patch_combined_transport.py",
    "patch_combined_service_startup.py",
    "patch_refresh_result_bridge.py",
    "patch_app_layout.py",
    "patch_embedded_sidestore_startup.py",
    "patch_local_idevice_package.py",
    "patch_combined_service_startup.py",
    "combined_build_evidence.py",
    "run_issue25_rendering.py", "rendering_localization.py",
    "run_p0_signin_rendering.py",
    "run_p0_signin_preflight.py",
    "p2_localization_contract.py",
    "p2_permission_contract.py",
    "select_livecontainer_lane.py",
    "patch_app_layout.py",
    "audit_ipa_signing.py",
    "patch_guest_return.py",
    "patch_combined_transport.py",
    "patch_combined_service_startup.py",
    "patch_refresh_result_bridge.py",
    "patch_background_automation.py",
    "patch_combined_refresh_contract.py",
    "patch_embedded_sidestore_startup.py",
    "patch_local_idevice_package.py",
    "patch_v3_service.py",
    "patch_combined_service_startup.py",
    "combined_build_evidence.py",
    "run_issue25_rendering.py",
    "patch_app_layout.py",
    "package_livecontainer_combined.py",
    "audit_ipa_signing.py",
    "patch_guest_return.py",
    "patch_combined_transport.py",
    "patch_refresh_result_bridge.py",
    "patch_embedded_sidestore_startup.py",
    "patch_local_idevice_package.py",
}
LIVE_CONTAINER_SCRIPT = "patch_livecontainer_autorefresh.py"
LIVE_CONTAINER_STARTUP_SCRIPT = "patch_embedded_sidestore_startup.py"
COMBINED_REFRESH_SCRIPT = "patch_combined_refresh_contract.py"
EMBEDDED_KEYCHAIN_SCRIPT = "patch_embedded_keychain.py"
APP_LAYOUT_SCRIPT = "patch_app_layout.py"
V3_UNIFIED_SHELL_SCRIPT = "patch_v3_unified_shell.py"

_SENSITIVE_ARTIFACT_SUFFIXES = {".p12", ".pfx", ".der", ".pem", ".key"}
_SENSITIVE_NAMES = re.compile(
    r"(?i)(pairingfile|rppairing|client_(cert|key)|lockdowndirectdiag|"
    r"(?:^|[/_.-])pairing(?:[/_.-]|$))"
)
_PUBLIC_SOURCE_DIRS = {"scripts", "tests", "docs"}
_PUBLIC_SOURCE_SUFFIXES = {
    ".c", ".cpp", ".h", ".hpp", ".js", ".md", ".rst", ".py", ".rs", ".swift", ".ts", ".tsx"
}


def _is_sensitive_reachable_path(path: str) -> bool:
    """Classify Git path output without treating source identifiers as secrets."""
    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    parts = normalized.split("/")
    basename = parts[-1].lower()
    suffix = Path(basename).suffix

    # These formats are private signing/pairing material wherever they occur.
    if suffix in _SENSITIVE_ARTIFACT_SUFFIXES:
        return True
    # Source identifiers may mention pairing. Serialized data (including JSON
    # and plist) and private material never inherit the source exemption.
    if _SENSITIVE_NAMES.search(normalized):
        is_public_source = (
            len(parts) > 1
            and parts[0].lower() in _PUBLIC_SOURCE_DIRS
            and suffix in _PUBLIC_SOURCE_SUFFIXES
        )
        return not is_public_source
    return False


# Exact reviewed CI source objects, not a directory or extension exemption.
# New bytes at these paths require a fresh content review before adding a digest.
_REVIEWED_PAIRING_SOURCE_SHA256 = {
    "ci/issues-native/preflight_comprehensive_pairing.py": "6a77204a2516f0592f078ab6ff03e805fd15720569589ef38f357e9c10e03e27",
    "ci/issues-native/russian_pairing_projection.py": "3b4995b3f99742536c2f4a780f4d08a63f95cc4880b4e1119dee331520ade7e2",
    "ci/issues-native/test_russian_pairing_projection.py": "632bae9b89584615a14e2b61a6c3ddd33a9834f185153f96f5b961e2863f3d80",
}
_PUBLIC_PAIRING_MANIFEST_PATH = "ci/issues-native/russian-pairing-extension.json"
_PUBLIC_PAIRING_MANIFEST_INPUTS = {
    "LiveContainer/.ci/comprehensive-diagnostics/generate_pairing_recovery_fixture.py",
    "LiveContainer/.ci/comprehensive-diagnostics/generate_wire_fixture.py",
    "LiveContainer/.ci/comprehensive-diagnostics/pairing_recovery_harness.swift",
    "LiveContainer/.ci/comprehensive-diagnostics/test_pairing_recovery_contract.py",
    "LiveContainer/LiveContainerSwiftUI/Utilities/V3Localization.swift",
    "LiveContainer/Resources/Localizable.xcstrings",
}
_PUBLIC_PAIRING_OBJECT_LIMIT = 8192


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate manifest key")
        value[key] = item
    return value


def _is_reviewed_public_pairing_blob(path: str, object_type: str, content: bytes) -> bool:
    """Permit only inspected source bytes or a closed hash-only public manifest.

    Never accepts real pairing records, signing material, arbitrary CI JSON,
    directories, private-path aliases, or a changed Python source object.
    """
    if object_type != "blob" or not content or len(content) > _PUBLIC_PAIRING_OBJECT_LIMIT:
        return False
    if path in _REVIEWED_PAIRING_SOURCE_SHA256:
        if hashlib.sha256(content).hexdigest() != _REVIEWED_PAIRING_SOURCE_SHA256[path]:
            return False
        try:
            ast.parse(content.decode("utf-8"))
        except (UnicodeError, SyntaxError, ValueError):
            return False
        return True
    if path != _PUBLIC_PAIRING_MANIFEST_PATH:
        return False
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeError, ValueError, RecursionError):
        return False
    if not isinstance(value, dict) or set(value) != {"schema", "candidate_inputs", "frozen_contract_sha256"}:
        return False
    if value["schema"] != "reviewed-russian-pairing-extension-v1":
        return False
    if value["frozen_contract_sha256"] != "858106577241aef930f9dc4fb976d74c66625bb86217b78e769f07f0f4fd7750":
        return False
    inputs = value["candidate_inputs"]
    return (isinstance(inputs, dict) and set(inputs) == _PUBLIC_PAIRING_MANIFEST_INPUTS and
            all(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
                for digest in inputs.values()))


def _reachable_sensitive_objects(rows):
    """Preserve the all-ref object scan; inspect bytes only for four exact paths."""
    rejected = []
    reviewed_paths = set(_REVIEWED_PAIRING_SOURCE_SHA256) | {_PUBLIC_PAIRING_MANIFEST_PATH}
    for object_id, path in rows:
        if not _is_sensitive_reachable_path(path):
            continue
        if path not in reviewed_paths or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id) is None:
            rejected.append(path)
            continue
        object_type = subprocess.check_output(["git", "cat-file", "-t", object_id], cwd=ROOT, text=True).strip()
        size = int(subprocess.check_output(["git", "cat-file", "-s", object_id], cwd=ROOT, text=True))
        if object_type != "blob" or not 0 < size <= _PUBLIC_PAIRING_OBJECT_LIMIT:
            rejected.append(path)
            continue
        content = subprocess.check_output(["git", "cat-file", "blob", object_id], cwd=ROOT)
        if not _is_reviewed_public_pairing_blob(path, object_type, content):
            rejected.append(path)
    return rejected


class RepositoryTests(unittest.TestCase):
    def test_current_files_exist(self):
        self.assertTrue((ROOT / "README.md").is_file())
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / "CONTRIBUTING.md").is_file())
        self.assertTrue((ROOT / "docs" / "VERIFICATION.md").is_file())
        self.assertTrue((ROOT / "SECURITY.md").is_file())
        self.assertTrue(WORKFLOW.is_file())
        self.assertEqual(
            {path.name for path in SCRIPTS.glob("*.py")},
                REQUIRED_SCRIPTS | LIVE_CONTAINER_SCRIPTS | {
                    "check_compiled_localization.py", "patch_sidesign_privacy.py", "patch_anisette_isolated_otp.py", "verify_candidate_ipa.py", "run_required_tests.py", "verify_pinned_sources.py", "maintained_sources.py", "p0_maintained_sources.py", "p1_maintained_sources.py", "p0_constructor_diagnostics.py", "p0_elf_metadata_bounds.py", "p0_cxx_allocation.py", "p2_composed_host.py", "p0_full_login_diagnostics.py", "maintained_package_evidence.py", "verify_archive_symbols.py", "verify_prepared_anisette.py", "patch_transport_lockfiles.py"},

        )

    def test_patch_scripts_parse_and_contain_guard_markers(self):
        """Syntax and guard-marker checks; actual repeat-application tests live beside patch fixtures.

        Examples include test_automation, test_app_layout, test_embedded_keychain,
        test_guest_return, test_sidesign_privacy, and test_v3_unified_shell.
        """
        for name in REQUIRED_SCRIPTS | {LIVE_CONTAINER_SCRIPT, LIVE_CONTAINER_STARTUP_SCRIPT,
                                        COMBINED_REFRESH_SCRIPT, EMBEDDED_KEYCHAIN_SCRIPT, "patch_combined_transport.py", "patch_refresh_result_bridge.py", APP_LAYOUT_SCRIPT, V3_UNIFIED_SHELL_SCRIPT,
                                        "patch_sidesign_2fa_state.py"}:
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
        # Historical patch recipes remain inspectable, but active entrypoints
        # must use the maintained immutable implementation, not those recipes.
        import json
        maintained = json.loads((ROOT / ".ci/maintained-build/contract.json").read_text())
        for name in ("livecontainer-build.yml", "build-current.yml"):
            workflow = (ROOT / ".github/workflows" / name).read_text()
            self.assertIn("uses: NRG-Wardog/LiveContainer/.github/workflows/native-app-compile.yml@"
                          + maintained["implementation_commit"], workflow)
            self.assertNotIn("builder/scripts/", workflow)
            self.assertNotIn("contents: write", workflow)
        archived = ROOT / "migration/historical/ssa-validation/p0-livecontainer-build.yml"
        self.assertIn("builder/scripts/maintained_sources.py", archived.read_text())
        contract = (SCRIPTS / COMBINED_REFRESH_SCRIPT).read_text(encoding="utf-8")
        self.assertIn("from patch_embedded_keychain import patch as patch_shared_keychain", contract)
        self.assertIn("patch_combined_cli(Path(sys.argv[1]))", contract)
        self.assertIn("patch_shared_keychain(staged)", contract)
        self.assertLess(contract.index("patch_shared_keychain(staged)"),
                        contract.index("_patch_verified(staged)", contract.index("patch_shared_keychain(staged)")))
        self.assertIn("[LC_KEYCHAIN] GROUP_SELECTED", contract)
        self.assertIn("verify_keychain_selection_contract", contract)

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
        # Retain the audit of every reachable ref, including deleted files in
        # history. A clone containing private investigation refs must fail.
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", "rev-list", "--objects", "--all"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        reachable_objects = [parts for line in result.stdout.splitlines()
                             if len(parts := line.split(maxsplit=1)) == 2]
        self.assertFalse(
            _reachable_sensitive_objects(reachable_objects),
            "reachable repository contains sensitive artifact paths",
        )

    def test_sensitive_path_classifier_preserves_security_boundary(self):
        self.assertFalse(_is_sensitive_reachable_path("scripts/adapt_sidestore_070_pairing.py"))
        self.assertFalse(_is_sensitive_reachable_path("tests/PairingFile.swift"))
        self.assertTrue(_is_sensitive_reachable_path("PairingFile"))
        self.assertTrue(_is_sensitive_reachable_path("private/client_cert.pem"))
        self.assertTrue(_is_sensitive_reachable_path("scripts/client.p12"))
        self.assertTrue(_is_sensitive_reachable_path("diagnostics/LockdownDirectDiag.log"))
        self.assertTrue(_is_sensitive_reachable_path("docs/export.der"))
        for path in ("scripts/pairingFile.json", "tests/rppairing.plist",
                     "data/device.pairing", "backups/pairingFile.plist.old",
                     "exports/client_key.backup", "LockdownDirectDiag/output.log",
                     "docs/CLIENT.P12", "scripts/key.pem", "data/phone.pfx"):
            with self.subTest(path=path):
                self.assertTrue(_is_sensitive_reachable_path(path))

    def test_public_pairing_manifest_accepts_only_closed_hash_inventory(self):
        manifest = {
            "schema": "reviewed-russian-pairing-extension-v1",
            "frozen_contract_sha256": "858106577241aef930f9dc4fb976d74c66625bb86217b78e769f07f0f4fd7750",
            "candidate_inputs": {path: "a" * 64 for path in _PUBLIC_PAIRING_MANIFEST_INPUTS},
        }
        encoded = json.dumps(manifest).encode()
        self.assertTrue(_is_reviewed_public_pairing_blob(_PUBLIC_PAIRING_MANIFEST_PATH, "blob", encoded))
        # Its name remains sensitive without the object-level content check.
        self.assertTrue(_is_sensitive_reachable_path(_PUBLIC_PAIRING_MANIFEST_PATH))
        for path in ("ci/other-pairing.json", "private/" + _PUBLIC_PAIRING_MANIFEST_PATH,
                     _PUBLIC_PAIRING_MANIFEST_PATH + ".pem", "ci/issues-native/pairingFile.json"):
            self.assertFalse(_is_reviewed_public_pairing_blob(path, "blob", encoded))
        for kind in ("tree", "commit", "tag"):
            self.assertFalse(_is_reviewed_public_pairing_blob(_PUBLIC_PAIRING_MANIFEST_PATH, kind, encoded))
        for field, payload in (("HostPrivateKey", "SYNTHETIC_SECRET"), ("HostCertificate", "SYNTHETIC_CERT"),
                               ("PairRecordData", "SYNTHETIC_RECORD"), ("EscrowBag", "SYNTHETIC_ESCROW")):
            altered = dict(manifest, **{field: payload})
            self.assertFalse(_is_reviewed_public_pairing_blob(_PUBLIC_PAIRING_MANIFEST_PATH, "blob", json.dumps(altered).encode()))
        for value in ([], None, "SYNTHETIC_PRIVATE_RECORD", {"HostID": "synthetic", "SystemBUID": "synthetic"}):
            self.assertFalse(_is_reviewed_public_pairing_blob(_PUBLIC_PAIRING_MANIFEST_PATH, "blob", json.dumps(value).encode()))
        for field in ("schema", "frozen_contract_sha256", "candidate_inputs"):
            altered = dict(manifest)
            altered.pop(field)
            self.assertFalse(_is_reviewed_public_pairing_blob(_PUBLIC_PAIRING_MANIFEST_PATH, "blob", json.dumps(altered).encode()))
        for value in ("SYNTHETIC_PRIVATE_KEY", "a" * 63, "A" * 64, 0, ["a" * 64], {"hash": "a" * 64}):
            altered = json.loads(encoded)
            altered["candidate_inputs"][next(iter(_PUBLIC_PAIRING_MANIFEST_INPUTS))] = value
            self.assertFalse(_is_reviewed_public_pairing_blob(_PUBLIC_PAIRING_MANIFEST_PATH, "blob", json.dumps(altered).encode()))
        altered = json.loads(encoded)
        altered["candidate_inputs"]["private/PairingFile"] = "a" * 64
        self.assertFalse(_is_reviewed_public_pairing_blob(_PUBLIC_PAIRING_MANIFEST_PATH, "blob", json.dumps(altered).encode()))
        duplicate = encoded[:-1] + b', "schema": "reviewed-russian-pairing-extension-v1"}'
        self.assertFalse(_is_reviewed_public_pairing_blob(_PUBLIC_PAIRING_MANIFEST_PATH, "blob", duplicate))
        for content in (b"", b"\xff", b"[" * 4096, b"x" * (_PUBLIC_PAIRING_OBJECT_LIMIT + 1)):
            self.assertFalse(_is_reviewed_public_pairing_blob(_PUBLIC_PAIRING_MANIFEST_PATH, "blob", content))

    def test_pairing_source_allowance_is_exact_and_never_a_directory_exemption(self):
        for path in _REVIEWED_PAIRING_SOURCE_SHA256:
            self.assertTrue(_is_sensitive_reachable_path(path))
            for content in (b"print('harmless but unreviewed')\n", b"{\"HostPrivateKey\": \"SYNTHETIC\"}",
                            b"-----BEGIN PRIVATE KEY-----\nSYNTHETIC\n-----END PRIVATE KEY-----"):
                self.assertFalse(_is_reviewed_public_pairing_blob(path, "blob", content))
        self.assertFalse(_is_reviewed_public_pairing_blob("ci/new_pairing.py", "blob", b"pass\n"))

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
