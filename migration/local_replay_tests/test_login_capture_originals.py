"""Explicit local-only original-capture replay, excluded from ordinary CI discovery.

Run this file directly with --evidence-plan PATH and four --source Owner=PATH
arguments (AnisetteKit, SideSign, SideStore, LiveContainer). These locations are
required; missing evidence or source repositories fail rather than skip. This
suite reads the original captures and Git objects but never executes captured
code or native fixtures. The 27 portable tests remain in migration/tests.
"""
import argparse
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'migration'), str(ROOT)]
import login_capture_parser as parser


class ExternalOriginalEvidenceReplayTests(unittest.TestCase):
    """Explicit real-evidence replay; missing configuration or source data fails."""
    plan = None
    roots = None
    @classmethod
    def setUpClass(cls):
        if cls.plan is None or cls.roots is None:
            raise ValueError('Explicit --evidence-plan and four --source paths are required')
        if not (cls.plan / 'final-source-handoff.json').is_file():
            raise ValueError('Configured original evidence handoff is absent')
        if not all(path.is_dir() for path in cls.roots.values()):
            raise ValueError('A configured original source repository is absent')

    def test_actual_exact_original_replay_is_parser_only(self):
        files, summary = parser.replay(self.plan, self.roots)
        self.assertEqual(set(files), set(parser.FIXED_FILE_HASHES))
        self.assertEqual({n: parser.sha(raw) for n, raw in files.items()}, parser.FIXED_FILE_HASHES)
        self.assertEqual(summary, parser.EXPECTED_SUMMARY)
        self.assertEqual(summary['phase_b']['run_id'], 39229204851)
        self.assertEqual(summary['phase_a']['run_id'], 39210961502)
        self.assertTrue(summary['parser_only'])
        for flag in ('compiled_final_commit', 'release_eligible', 'device_login_proven',
                     'guest_execution', 'artifact_execution_performed', 'new_builder_admission_granted'):
            self.assertIs(summary[flag], False)
        self.assertEqual(summary['scope_notes'], parser.parse(files['evidence/phase-b/independent-review.json'])['scope_notes'])
        self.assertEqual(files['resolver-locks/SideSign.Package.resolved.after'],
                         files['resolver-locks/SideSign.Package.resolved.final-observed'])
        self.assertEqual(files['resolver-locks/SideStore.Package.resolved.after'],
                         files['resolver-locks/SideStore.Package.resolved.final-observed'])
        self.assertFalse(any(n.endswith(('.py', '.sh', '.zip', '.dylib', '.a', '.ipa')) for n in files))

    def test_actual_wrong_source_root_and_capture_host_are_rejected(self):
        bad = {'LiveContainer': self.roots['AnisetteKit']} | self.roots
        bad['LiveContainer'] = self.roots['AnisetteKit']
        # Exercise source identity without decompressing the archives again.
        handoff = parser.parse(parser.read_regular(self.plan / 'final-source-handoff.json'))
        with self.assertRaisesRegex(ValueError, 'source repository'):
            parser._source_replay(bad, handoff, {}, {}, {})


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--evidence-plan', required=True, type=Path)
    cli.add_argument('--source', action='append', required=True, metavar='OWNER=PATH')
    args = cli.parse_args(argv)
    roots = {}
    for value in args.source:
        name, separator, location = value.partition('=')
        if not separator or not location or name in roots:
            cli.error('Each source must be a unique OWNER=PATH')
        roots[name] = Path(location).absolute()
    if set(roots) != {'AnisetteKit', 'SideSign', 'SideStore', 'LiveContainer'}:
        cli.error('Exactly AnisetteKit, SideSign, SideStore and LiveContainer are required')
    ExternalOriginalEvidenceReplayTests.plan = args.evidence_plan.absolute()
    ExternalOriginalEvidenceReplayTests.roots = roots
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ExternalOriginalEvidenceReplayTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun == 2 and not result.skipped else 1


if __name__ == '__main__':
    raise SystemExit(main())
