from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "native-build.yml"
SCRIPTS = ROOT / "scripts"
REQUIRED_SCRIPTS = set()
LIVE_CONTAINER_SCRIPTS = {
    "ipa_size_report.py",
    "native_error_evidence.py", "headless_artifact_contract.py", "anisette_evidence.py",
    "patch_v3_service.py",
    "patch_embedded_keychain.py",
    "patch_refresh_result_bridge.py",
    "combined_build_evidence.py",
    "run_issue25_rendering.py", "rendering_localization.py",
    "run_p0_signin_rendering.py",
    "p2_localization_contract.py",
    "p2_permission_contract.py",
    "audit_ipa_signing.py",
    "patch_refresh_result_bridge.py",
    "patch_v3_service.py",
    "combined_build_evidence.py",
    "run_issue25_rendering.py",
    "package_livecontainer_combined.py", "packaging_adapters.py", "rehearse_packaging_integration.py",
    "audit_ipa_signing.py",
    "patch_refresh_result_bridge.py",
}
EMBEDDED_KEYCHAIN_SCRIPT = "patch_embedded_keychain.py"

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
            and not (parts[0].lower() == "tests" and parts[1].lower() == "ci")
            and suffix in _PUBLIC_SOURCE_SUFFIXES
        )
        return not is_public_source
    return False


# Exact reviewed CI source objects, not a directory or extension exemption.
# New bytes at these paths require a fresh content review before adding a digest.
_REVIEWED_PAIRING_SOURCE_SHA256 = {
    "ci/issues-native/preflight_comprehensive_pairing.py": {
        # Original source and reviewed a1b61bdd source-inventory guard successor.
        # Both remain reachable; neither admits arbitrary bytes at this path.
        "6a77204a2516f0592f078ab6ff03e805fd15720569589ef38f357e9c10e03e27",
        "1e8c254fb86417ed67b3a452a348b82cac239fa5285a78e13d945d59135560a3",
    },
    "ci/issues-native/russian_pairing_projection.py": "3b4995b3f99742536c2f4a780f4d08a63f95cc4880b4e1119dee331520ade7e2",
    "ci/issues-native/test_russian_pairing_projection.py": "632bae9b89584615a14e2b61a6c3ddd33a9834f185153f96f5b961e2863f3d80",
    'ci/diagnostics/preflight_comprehensive_pairing.py': '493ca6ada48af900f4b76a93b96d9f95acf2deda2c0a9676319702c730fe4978',
    'ci/qualification/native/russian_pairing_projection.py': {
        # Historical source plus the exact reviewed prompt inverse adapter.
        'a843c2e79b2e70112ad1a81e69c8822df8341c54f4e6936633c2d943938bbebc',
        '18d46bc1cc8a9580c389d6c68e8d63aebb152a2fcadab82a0b3cfc45d2fdeff4',
    },
    'tests/ci/native/test_russian_pairing_projection.py': '2dcac1bad40ce081d4cdb8a2be557e5ae6d3dd42486ce889e173b7b030e815ac',
}
_PUBLIC_PAIRING_MANIFEST_PATH = "ci/qualification/native/russian-pairing-extension.json"
_PUBLIC_PAIRING_MANIFEST_PATHS = {_PUBLIC_PAIRING_MANIFEST_PATH, "ci/issues-native/russian-pairing-extension.json"}
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
        expected = _REVIEWED_PAIRING_SOURCE_SHA256[path]
        allowed = {expected} if isinstance(expected, str) else expected
        if hashlib.sha256(content).hexdigest() not in allowed:
            return False
        try:
            ast.parse(content.decode("utf-8"))
        except (UnicodeError, SyntaxError, ValueError):
            return False
        return True
    if path not in _PUBLIC_PAIRING_MANIFEST_PATHS:
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
    """Preserve the all-ref object scan; inspect bytes only for exact historical and current paths."""
    rejected = []
    reviewed_paths = set(_REVIEWED_PAIRING_SOURCE_SHA256) | _PUBLIC_PAIRING_MANIFEST_PATHS
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
        self.assertTrue((ROOT / "docs" / "README.md").is_file())
        self.assertTrue((ROOT / "SECURITY.md").is_file())
        self.assertTrue(WORKFLOW.is_file())
        self.assertEqual(
            {path.name for path in SCRIPTS.glob("*.py")},
                REQUIRED_SCRIPTS | LIVE_CONTAINER_SCRIPTS | {
                    'experiment_local_symbols.py', 'experiment_sidebackup_stabs.py', 'local_symbol_packaging.py', 'macho_local_symbol_guard.py', 'openssl_local_symbol_policy.py', 'optimize_openssl_local_candidate.py', 'optimize_sidebackup_candidate.py', 'optimize_unsigned_widget.py', 'patch_refresh_callback_admission.py', 'widget_local_symbols.py', 'widget_signature_policy.py',
                    "integration_contracts.py", "experiment_strip_stabs.py", "macho_stab_guard.py", "optimize_native_candidate.py",
                    "check_compiled_localization.py", "verify_candidate_ipa.py", "run_required_tests.py", "maintained_sources.py", "maintained_package_evidence.py", "verify_archive_symbols.py"},

        )

    def test_current_scripts_parse(self):
        for path in SCRIPTS.glob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_workflow_references_current_scripts(self):
        workflow = WORKFLOW.read_text()
        self.assertIn('permissions:\n  contents: read', workflow)
        self.assertIn('record.py receipt-current', workflow)
        self.assertIn('verify_candidate_graph.py', workflow)
        self.assertIn('package_candidate.py --same-run-qualified-size', workflow)
        self.assertNotIn('contents: write', workflow)
        # Product assembly now consumes maintained owners, not patch replay.
        # Binary keychain selection still has its read-only artifact gate.
        contract = (SCRIPTS / "headless_artifact_contract.py").read_text(encoding="utf-8")
        self.assertIn("[LC_KEYCHAIN] GROUP_SELECTED", contract)
        self.assertIn("def verify_keychain_selection_contract", contract)

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
                     _PUBLIC_PAIRING_MANIFEST_PATH + ".pem", "ci/qualification/native/pairingFile.json"):
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

    def test_relocated_pairing_sources_are_content_bound(self):
        for path in ("ci/diagnostics/preflight_comprehensive_pairing.py",
                     "ci/qualification/native/russian_pairing_projection.py",
                     "tests/ci/native/test_russian_pairing_projection.py"):
            with self.subTest(path=path):
                raw = (ROOT / path).read_bytes()
                self.assertTrue(_is_sensitive_reachable_path(path))
                self.assertTrue(_is_reviewed_public_pairing_blob(path, "blob", raw))
                self.assertFalse(_is_reviewed_public_pairing_blob(path, "blob", raw + b"\n# changed"))
                self.assertFalse(_is_reviewed_public_pairing_blob("private/" + path, "blob", raw))

    def test_pairing_preflight_admits_only_two_reviewed_git_source_objects(self):
        path = "ci/issues-native/preflight_comprehensive_pairing.py"
        refs = {
            "51aad480087357289b86fa07dbc19eb207bf04c0":
                "6a77204a2516f0592f078ab6ff03e805fd15720569589ef38f357e9c10e03e27",
            "fefb69346a4d3bb79ebbb4294b3b99bf81fcd045":
                "1e8c254fb86417ed67b3a452a348b82cac239fa5285a78e13d945d59135560a3",
        }
        self.assertEqual(_REVIEWED_PAIRING_SOURCE_SHA256[path], set(refs.values()))
        for ref, expected in refs.items():
            # Read immutable objects as data, never execute historical code.
            source = subprocess.check_output(["git", "show", ref + ":" + path], cwd=ROOT)
            self.assertEqual(hashlib.sha256(source).hexdigest(), expected)
            self.assertTrue(_is_reviewed_public_pairing_blob(path, "blob", source))
            self.assertFalse(_is_reviewed_public_pairing_blob(path, "blob", source + b"\n# unreviewed"))
            self.assertFalse(_is_reviewed_public_pairing_blob(path, "tree", source))
            self.assertFalse(_is_reviewed_public_pairing_blob("ci/other_pairing.py", "blob", source))

    def test_pairing_prompt_adapter_admits_only_two_reviewed_git_blobs(self):
        path = 'ci/qualification/native/russian_pairing_projection.py'
        blobs = {
            '0c656a9f90e59cb9ee20352288865afaef7f9365':
                'a843c2e79b2e70112ad1a81e69c8822df8341c54f4e6936633c2d943938bbebc',
            'b7bd46277bc05ea885a650fdf7757027e9d1f93f':
                '18d46bc1cc8a9580c389d6c68e8d63aebb152a2fcadab82a0b3cfc45d2fdeff4',
        }
        self.assertEqual(_REVIEWED_PAIRING_SOURCE_SHA256[path], set(blobs.values()))
        for blob, expected in blobs.items():
            source = subprocess.check_output(['git', '--no-replace-objects', 'cat-file', 'blob', blob], cwd=ROOT)
            self.assertEqual(hashlib.sha256(source).hexdigest(), expected)
            self.assertTrue(_is_reviewed_public_pairing_blob(path, 'blob', source))
            for changed in (source + b'\n# unreviewed', source + b'HostPrivateKey=SYNTHETIC_SECRET'):
                self.assertFalse(_is_reviewed_public_pairing_blob(path, 'blob', changed))
            self.assertFalse(_is_reviewed_public_pairing_blob(path, 'tree', source))
            self.assertFalse(_is_reviewed_public_pairing_blob('ci/other_pairing.py', 'blob', source))
            self.assertFalse(_is_reviewed_public_pairing_blob('private/' + path, 'blob', source))

    def test_public_docs_do_not_expose_known_private_network_details(self):
        """Generic RFC1918 examples are allowed; known diagnostic addresses are not."""
        public_paths = list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md")) + [ROOT / "ci/README.md"]
        public_docs = "\n".join(path.read_text(encoding="utf-8") for path in public_paths)

        # These were diagnostic/local addresses and must never leak into public docs.
        for sensitive_ip in ("10.7.0.1", "10.7.0.2"):
            self.assertNotIn(sensitive_ip, public_docs)

        # Documentation may intentionally use RFC1918 examples such as
        # 10.0.0.x or 192.168.1.x to explain same-subnet LocalDevVPN routing.
        self.assertTrue(public_paths)


if __name__ == "__main__":
    unittest.main()
