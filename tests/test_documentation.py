"""Current user-guide links and release identity, without network access."""
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
# Update with a published release, never from an unqualified candidate.
EXPECTED_RELEASE = "v6.0.1"
GUIDES = [ROOT / name for name in
          ('README.md', 'README.he.md', 'README.ru.md', 'README.zh-CN.md', 'README.zh-TW.md')]


class DocumentationTests(unittest.TestCase):
    def test_current_guides_select_one_matching_release_asset(self):
        english = GUIDES[0].read_text()
        releases = re.findall(r'releases/download/(v\d+\.\d+\.\d+)/LiveContainer-SideStore-(v\d+\.\d+\.\d+)\.ipa', english)
        self.assertTrue(releases, 'English guide must link the installable release asset')
        version = EXPECTED_RELEASE
        for path in GUIDES:
            text = path.read_text()
            assets = re.findall(r'releases/download/(v\d+\.\d+\.\d+)/LiveContainer-SideStore-(v\d+\.\d+\.\d+)\.ipa', text)
            self.assertTrue(assets, path.name)
            self.assertEqual(set(assets), {(version, version)}, path.name)
            self.assertIn('releases/tag/' + version, text, path.name)

    def test_docs_contains_only_general_architecture(self):
        self.assertEqual({p.relative_to(ROOT / "docs").as_posix() for p in (ROOT / "docs").rglob("*") if p.is_file()}, {"README.md"})

    def test_current_documentation_links_exist(self):
        paths = GUIDES + [ROOT / 'CONTRIBUTING.md', ROOT / 'ci/README.md', ROOT / 'docs/README.md']
        for path in paths:
            for target in re.findall(r'\]\(([^\s)]+)(?:\s+"[^"]*")?\)', path.read_text()):
                url = urlsplit(target.strip('<>'))
                if url.scheme or url.netloc or not url.path:
                    continue
                resolved = (path.parent / unquote(url.path)).resolve()
                self.assertTrue(resolved.is_relative_to(ROOT), (path.name, target))
                self.assertTrue(resolved.exists(), (path.name, target))

    def test_update_and_network_safety_remain_visible(self):
        text = GUIDES[0].read_text()
        for required in ('LiveProcess', 'same subnet', '/32', 'Save & Apply', 'Allow Intermediate Addresses'):
            self.assertIn(required, text)
        self.assertRegex(text.lower(), r'do not delete|don.t delete')
        self.assertIn('signing identity', text.lower())
        self.assertIn('identifiers', text.lower())


if __name__ == '__main__':
    unittest.main()
