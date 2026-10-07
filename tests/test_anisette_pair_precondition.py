"""Execute exact generated resolver/provider continuations without Apple or Keychain.

macOS CI supplies Swift and the pinned source. Linux still verifies extraction,
required evidence and the bounded patch contract; a skip is not a native pass.
"""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pair_keychain_fixture", ROOT / "tests/test_embedded_keychain.py")
existing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(existing)


def declaration(source, anchor):
    start = source.index(anchor)
    opening = source.index("{", start)
    depth = 0
    for end in range(opening, len(source)):
        if source[end] == "{": depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0: return source[start:end + 1]
    raise AssertionError(anchor)


def pinned_sources():
    root = os.environ.get("EMBEDDED_SIDESTORE_TEST_SOURCE")
    if not root:
        raise unittest.SkipTest("Pinned SideStore input required; supplied by combined CI")
    return [existing.read_pinned_source(root, p) for p in existing.module.ANISETTE_PATHS]


def program(*, baseline=False, old_fallback=False):
    config, oda, remote = pinned_sources()
    if not baseline:
        config = existing.module.patch_anisette_config(config)
        oda = existing.module.patch_anisette_provider(oda, on_device=True)
        remote = existing.module.patch_anisette_provider(remote, on_device=False)
    methods = [declaration(config, "    public func resolveDeviceIdentifier(")]
    if not baseline:
        methods += [declaration(config, "    func resolveAnisetteSnapshot("),
                    declaration(config, "    func commitAnisetteBlob(")]
    fixture = (ROOT / "tests/fixtures/anisette_pair_precondition_harness.swift").read_text()
    fixture = fixture.replace("__PRODUCTION_CONFIG_METHODS__", "\n".join(methods))
    fixture = fixture.replace("__PRODUCTION_ODA_METHOD__", declaration(oda, "    public func fetchAnisetteData("))
    fixture = fixture.replace("__PRODUCTION_REMOTE_PROVIDER__", declaration(remote, "enum AnisetteProvider {"))
    if not baseline:
        common = (ROOT / "scripts/templates/combined_failure.swift").read_text()
        support = (ROOT / "scripts/templates/anisette_legacy_recovery.swift").read_text()
        extra = "\n".join(declaration(common, signature) for signature in (
            "struct V3AnisetteAttemptContext {", "struct V3AnisetteAttemptError:",
            "struct V3AnisetteNativeEvidence {"))
        extra += (ROOT / "tests/fixtures/anisette_recovery_probe_double.swift").read_text()
        extra += declaration(support, "extension OnDeviceAnisetteManager {")
        fixture = fixture.replace("@main struct AnisettePairTests", extra + "\n@main struct AnisettePairTests", 1)
        fixture = fixture.replace("    static let shared = SyntheticAnisetteDataManager()",
            '    let libsDir = URL(fileURLWithPath: "/synthetic-libraries")\n    static let shared = SyntheticAnisetteDataManager()', 1)
    doubles = existing.DOUBLES.replace("\nfinal class Keychain {", "\nfinal class Keychain {\n    static var shared: Keychain!", 1)
    doubles = doubles.replace('        func getData(_ key: String) throws -> Data? {',
        '        func getData(_ key: String) throws -> Data? {\n'
        '            if PairTest.requireLockedReads && ["identifier", "adiPb"].contains(key) { precondition(PairTest.lockDepth == 1) }', 1)
    template = existing.TEMPLATE.read_text()
    if old_fallback:
        # The exact pre-33f6694 fallback condition, with all other shipped
        # adapter behavior unchanged. Only used to demonstrate upgrade input.
        template = template.replace("if data == nil && !ready && LCSharedKeychainMigration.supportsLegacyCertificateFallback(key) {",
                                    "if data == nil && !ready {", 1)
    return doubles + template + existing.module.KEYCHAIN_ACCESS_ADAPTER + fixture


class AnisettePairPreconditionTests(unittest.TestCase):
    def test_generated_provider_contract_and_idempotence(self):
        config, oda, remote = pinned_sources()
        generated = [existing.module.patch_anisette_config(config),
                     existing.module.patch_anisette_provider(oda, on_device=True),
                     existing.module.patch_anisette_provider(remote, on_device=False)]
        self.assertEqual(generated[0], existing.module.patch_anisette_config(generated[0]))
        for source, on_device in zip(generated[1:], (True, False)):
            self.assertEqual(source, existing.module.patch_anisette_provider(source, on_device=on_device))
            self.assertIn("let anisetteSnapshot = try await AnisetteConfigManager.shared.resolveAnisetteSnapshot()", source)
            self.assertNotIn("AnisetteConfigManager.shared.anisetteAdiBlob", source)
            self.assertNotIn("AnisetteConfigManager.shared.resolveDeviceIdentifier()", source)
            self.assertIn("try await AnisetteConfigManager.shared.commitAnisetteBlob(freshBlob, snapshot: anisetteSnapshot)", source)
        for baseline, old_fallback in ((False, False), (True, False), (True, True)):
            self.assertNotIn("__PRODUCTION_", program(baseline=baseline, old_fallback=old_fallback))

    def test_guard_and_migration_do_not_repair_or_mix(self):
        source = existing.TEMPLATE.read_text()
        policy = declaration(source, "struct LCAnisetteStoredPair:")
        self.assertIn("if blob != nil && parsed == nil", policy)
        self.assertIn("if identifier != nil && parsed == nil", policy)
        for forbidden in ("UUID()", ".set(", ".remove(", "debugLog", "UserDefaults"):
            self.assertNotIn(forbidden, policy)
        resolver = declaration(source, "    static func resolveAnisetteSnapshot(")
        self.assertLess(resolver.index("stored.validated()"), resolver.index("UUID()"))
        self.assertIn("withSharedTransaction", resolver)
        commit = declaration(source, "    static func commitAnisetteBlob(")
        self.assertLess(commit.index("current == snapshot.stored"), commit.index('writeOne("adiPb"'))
        self.assertLess(commit.index("blob == existing"), commit.index('writeOne("adiPb"'))
        migration = source.split("    static func prepare(group:", 1)[1].split("// LC_SHARED_MIGRATION_POLICY_END", 1)[0]
        self.assertLess(migration.index("LCAnisetteStoredPair.validateMigration"), migration.index("for key in source.keys.sorted()"))
        for path in existing.module.ANISETTE_PATHS:
            self.assertIn(path, (ROOT / "scripts/combined_build_evidence.py").read_text())
            self.assertIn(path, (ROOT / "scripts/verify_candidate_ipa.py").read_text())
        self.assertIn("if error is LCAnisettePairError { return error }", existing.module.KEYCHAIN_ACCESS_ADAPTER)
        service = (ROOT / "scripts/patch_v3_service.py").read_text()
        self.assertIn('v3ClassifyAuthError(error)?.rawValue == \\"anisetteIdentityStateInvalid\\" { throw error }', service)

    def test_exact_production_continuations(self):
        sources = {"guarded": program(), "baseline": program(baseline=True),
                   "legacy": program(baseline=True, old_fallback=True)}
        compiler = shutil.which("swiftc")
        if not compiler:
            self.skipTest("Swift unavailable; mandatory generated pair continuation harness runs in macOS CI")
        guarded = ("empty", "valid_id_only", "complete_pair", "orphaned_blob", "invalid_id_only", "invalid_id_blob",
                   "invalid_blob", "base64_identifier", "legacy_pair", "interrupted_migration", "old_fallback_upgrade",
                   "selected_orphan_legacy_id", "legacy_orphan_selected_id", "legacy_orphan_empty", "legacy_orphan_bridge", "state_changed",
                   "paired_read_epoch", "keychain_read_failure", "repeated_pair", "cached_blob_matches", "cached_blob_differs")
        cases = {"guarded": guarded + tuple(s + "_remote" for s in guarded),
                 "baseline": ("unguarded_orphan", "unguarded_orphan_remote"),
                 "legacy": ("old_fallback_creates_orphan", "old_fallback_creates_orphan_remote")}
        with tempfile.TemporaryDirectory(prefix="anisette-pair-") as temporary:
            for variant, source in sources.items():
                path = Path(temporary) / (variant + ".swift")
                binary = Path(temporary) / variant
                path.write_text(source)
                built = subprocess.run([compiler, "-swift-version", "5", "-parse-as-library", str(path), "-o", str(binary)],
                                       capture_output=True, text=True, timeout=120)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                for scenario in cases[variant]:
                    with self.subTest(variant=variant, scenario=scenario):
                        result = subprocess.run([str(binary), scenario], capture_output=True, text=True, timeout=15)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn("ANISETTE_PAIR_PASS " + scenario, result.stdout)


if __name__ == "__main__":
    unittest.main()
