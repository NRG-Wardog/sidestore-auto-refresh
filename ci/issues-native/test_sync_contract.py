"""Offline positive/negative checks for additive sync gates; no target execution."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v = load('sync_verifier', HERE / 'verify_upstream_sync.py')
r = load('sync_recorder', HERE / 'record.py')


class ExactAncestryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.ci').mkdir()
        for name in ('source-parity.py', 'source-parity.json'):
            (self.root / '.ci' / name).write_text('synthetic')
        self.expected = copy.deepcopy(v.SPEC['native_sources']['idevice'])
        # Keep the original direct two-parent rejection tests even when the
        # currently pinned idevice is a separately tested exact follow-on.
        if 'merge_anchor' in self.expected:
            self.expected.update(self.expected.pop('merge_anchor'))
        self.expected['files_sha256'] = {p: v.sha(self.root / p) for p in self.expected['files_sha256']}
        self.actual = {
            ('rev-parse', 'HEAD'): self.expected['commit'],
            ('rev-parse', '--is-shallow-repository'): 'false',
            ('rev-parse', 'HEAD^{tree}'): self.expected['tree'],
            ('show', '-s', '--format=%P', 'HEAD'): ' '.join(self.expected['required_commit_parents']),
            ('merge-base', *self.expected['required_commit_parents']): self.expected['merge_base'],
        }
        for parent in self.expected['required_commit_parents']:
            self.actual[('merge-base', '--is-ancestor', parent, 'HEAD')] = ''
        self.mock = patch.object(v, 'git', lambda root, *args: self.actual[args])
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def test_exact_two_parent_ancestry(self):
        self.assertEqual(v.validate_ancestry(self.root, self.expected), self.expected['required_commit_parents'])

    def test_wrong_owner(self):
        self.actual[('rev-parse', 'HEAD')] = '0' * 40
        with self.assertRaisesRegex(RuntimeError, 'commit differs'):
            v.validate_ancestry(self.root, self.expected)

    def test_shallow(self):
        self.actual[('rev-parse', '--is-shallow-repository')] = 'true'
        with self.assertRaisesRegex(RuntimeError, 'Shallow'):
            v.validate_ancestry(self.root, self.expected)

    def test_wrong_tree(self):
        self.actual[('rev-parse', 'HEAD^{tree}')] = '0' * 40
        with self.assertRaisesRegex(RuntimeError, 'tree differs'):
            v.validate_ancestry(self.root, self.expected)

    def test_one_parent(self):
        self.actual[('show', '-s', '--format=%P', 'HEAD')] = self.expected['required_commit_parents'][0]
        with self.assertRaisesRegex(RuntimeError, 'Both exact ordered'):
            v.validate_ancestry(self.root, self.expected)

    def test_reordered_parents(self):
        self.actual[('show', '-s', '--format=%P', 'HEAD')] = ' '.join(reversed(self.expected['required_commit_parents']))
        with self.assertRaisesRegex(RuntimeError, 'Both exact ordered'):
            v.validate_ancestry(self.root, self.expected)

    def test_wrong_merge_base(self):
        self.actual[('merge-base', *self.expected['required_commit_parents'])] = '0' * 40
        with self.assertRaisesRegex(RuntimeError, 'merge base differs'):
            v.validate_ancestry(self.root, self.expected)

    def test_changed_manifest(self):
        (self.root / '.ci/source-parity.json').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'hash differs'):
            v.validate_ancestry(self.root, self.expected)

    def test_changed_verifier(self):
        (self.root / '.ci/source-parity.py').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'hash differs'):
            v.validate_ancestry(self.root, self.expected)

    def test_linked_metadata(self):
        path = self.root / '.ci/source-parity.json'
        path.unlink()
        path.symlink_to(self.root / '.ci/source-parity.py')
        with self.assertRaisesRegex(RuntimeError, 'linked native metadata'):
            v.validate_ancestry(self.root, self.expected)


class FakeHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sources = Path(self.temp.name) / 'sources'
        self.root = self.sources / 'SideStore/Dependencies/minimuxer'
        self.root.mkdir(parents=True)
        self.output = Path(self.temp.name) / 'evidence/fake.json'
        self.expected = copy.deepcopy(v.SPEC['minimuxer_fake_tests'])
        for name in self.expected['source_inputs']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic fixture')
            self.expected['source_inputs'][name] = v.sha(path)
        spec = dict(v.SPEC, minimuxer_fake_tests=self.expected)
        self.spec_patch = patch.object(v, 'SPEC', spec)
        self.spec_patch.start()
        self.addCleanup(self.spec_patch.stop)
        self.report = {'status': 'static_source_invariants_pass', 'native_or_device_execution': False,
                       'swift_smoke': 'source_owned_fake_tests_pass'}
        self.stdout = self.expected['success_line'] + '\n' + json.dumps(self.report, indent=2) + '\n'

    def run_fake(self, stdout=None, code=0):
        result = subprocess.CompletedProcess([], code, self.stdout if stdout is None else stdout)
        with patch.object(v.subprocess, 'run', return_value=result) as run:
            v.minimuxer_tests(self.sources, self.output, '/verified/swiftc')
            command = run.call_args.args[0]
            self.assertEqual(command[-2:], ['--swiftc', '/verified/swiftc'])
            self.assertTrue(command[2].endswith('/.ci/verify-upstream-sync.py'))

    def test_exact_fake_report(self):
        self.run_fake()
        report = json.loads(self.output.read_text())
        self.assertEqual(report['transport_cases'], 144)
        self.assertFalse(report['target_native_executed'])

    def test_compiler_warning_braces_not_mistaken_for_report(self):
        self.run_fake('warning: synthetic source { braces }\n' + self.stdout)

    def test_compiler_failure_does_not_pass(self):
        with self.assertRaisesRegex(RuntimeError, 'compiler/harness failed'):
            self.run_fake(code=1)

    def test_missing_exact_totals_do_not_pass(self):
        with self.assertRaisesRegex(RuntimeError, 'totals not observed'):
            self.run_fake(self.stdout.replace('transport_cases=144', 'transport_cases=143'))

    def test_wrong_report_does_not_pass(self):
        with self.assertRaisesRegex(RuntimeError, 'report differs'):
            self.run_fake(self.stdout.replace('source_owned_fake_tests_pass', 'not_run'))

    def test_changed_source_stops_before_compiler(self):
        (self.root / '.ci/upstream-sync-smoke.swift').write_text('modified source')
        with patch.object(v.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'source inputs differ'):
                v.minimuxer_tests(self.sources, self.output, '/verified/swiftc')
            run.assert_not_called()


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.e = self.root / 'evidence'
        self.s = self.root / 'sources'
        self.e.mkdir()
        self.path = self.s / 'SideStore' / r.LOCKS['SideStore']
        self.path.parent.mkdir(parents=True)
        self.before = {'version': 3, 'originHash': 'a' * 64, 'pins': [
            {'identity': 'anisettekit', 'kind': 'remoteSourceControl',
             'location': 'https://github.com/NRG-Wardog/AnisetteKit',
             'state': {'revision': 'e6e9d4aac698de2a5219aaef02c9a417da45f4e9'}},
            {'identity': 'unrelated', 'kind': 'remoteSourceControl',
             'location': 'https://github.com/example/unrelated', 'state': {'revision': 'b' * 40}}]}
        self.after = copy.deepcopy(self.before)
        self.after['pins'][0]['state']['revision'] = r.OWNERS['AnisetteKit'][1]
        self.after['originHash'] = 'c' * 64
        self.patches = [patch.object(r, 'E', self.e), patch.object(r, 'S', self.s)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def seal(self):
        (self.e / 'SideStore.Package.resolved.before').write_text(json.dumps(self.before))
        self.path.write_text(json.dumps(self.after))
        r.seal_resolution()

    def test_exact_observed_transition(self):
        self.seal()
        report = json.loads((self.e / 'app-lock-transition.json').read_text())
        self.assertEqual(report['after_originHash'], 'c' * 64)
        self.assertEqual((self.e / 'SideStore.Package.resolved.after').read_bytes(), self.path.read_bytes())

    def test_other_pin_drift(self):
        self.after['pins'][1]['state']['revision'] = 'd' * 40
        with self.assertRaisesRegex(RuntimeError, 'beyond the exact'):
            self.seal()

    def test_wrong_ak_start(self):
        self.before['pins'][0]['state']['revision'] = 'd' * 40
        with self.assertRaisesRegex(RuntimeError, 'starting AK'):
            self.seal()

    def test_identity_drift(self):
        self.after['pins'][1]['identity'] = 'different'
        with self.assertRaisesRegex(RuntimeError, 'identity closure'):
            self.seal()

    def test_origin_drift(self):
        self.after['pins'][0]['location'] = 'https://github.com/other/AnisetteKit'
        with self.assertRaisesRegex(RuntimeError, 'beyond the exact'):
            self.seal()

    def test_duplicate_pin(self):
        self.after['pins'].append(self.after['pins'][0])
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            self.seal()

    def test_invalid_observed_origin_hash(self):
        self.after['originHash'] = 'fabricated'
        with self.assertRaisesRegex(RuntimeError, 'Invalid observed'):
            self.seal()

    def test_pending_pin_rejected_before_network(self):
        pins = dict(r.OWNERS)
        pins['SideStore'] = (pins['SideStore'][0], 'PENDING_REVIEWED_SIDESTORE_COMMIT')
        with patch.object(r, 'OWNERS', pins), patch.object(r.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'missing or invalid exact'):
                r.acquire()
            run.assert_not_called()


class ExactFollowupAncestryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.ci').mkdir()
        for name in ('source-parity.py', 'source-parity.json'):
            (self.root / '.ci' / name).write_text('synthetic')
        self.expected = copy.deepcopy(v.SPEC['native_sources']['SideStore/Dependencies/minimuxer'])
        # Continue testing the original direct-child shape independently.
        self.expected.pop('followup_parent', None)
        self.expected['required_commit_parents'] = [self.expected['merge_anchor']['commit']]
        self.expected['commit'] = 'a' * 40
        self.expected['tree'] = 'b' * 40
        self.expected['files_sha256'] = {p: v.sha(self.root / p) for p in self.expected['files_sha256']}
        self.anchor = self.expected['merge_anchor']
        self.actual = {
            ('rev-parse', 'HEAD'): self.expected['commit'],
            ('rev-parse', '--is-shallow-repository'): 'false',
            ('rev-parse', 'HEAD^{tree}'): self.expected['tree'],
            ('show', '-s', '--format=%P', 'HEAD'): self.anchor['commit'],
            ('rev-parse', self.anchor['commit'] + '^{tree}'): self.anchor['tree'],
            ('show', '-s', '--format=%P', self.anchor['commit']): ' '.join(self.anchor['required_commit_parents']),
            ('merge-base', '--is-ancestor', self.anchor['commit'], 'HEAD'): '',
            ('merge-base', *self.anchor['required_commit_parents']): self.anchor['merge_base'],
        }
        for parent in self.anchor['required_commit_parents']:
            self.actual[('merge-base', '--is-ancestor', parent, self.anchor['commit'])] = ''
        self.mock = patch.object(v, 'git', lambda root, *args: self.actual[args])
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def test_exact_child_and_original_two_parent_merge(self):
        self.assertEqual(v.validate_ancestry(self.root, self.expected), [self.anchor['commit']])
        # Both independently required ancestry proofs must fail closed.
        for failed in [
                ('merge-base', '--is-ancestor', self.anchor['commit'], 'HEAD'),
                ('merge-base', '--is-ancestor', self.anchor['required_commit_parents'][1], self.anchor['commit'])]:
            def git(root, *args):
                if args == failed:
                    raise subprocess.CalledProcessError(1, ['git', *args])
                return self.actual[args]
            with self.subTest(failed=failed), patch.object(v, 'git', git), self.assertRaises(subprocess.CalledProcessError):
                v.validate_ancestry(self.root, self.expected)

    def test_no_implicit_single_parent_fallback(self):
        self.expected.pop('merge_anchor')
        with self.assertRaisesRegex(RuntimeError, 'Both exact ordered'):
            v.validate_ancestry(self.root, self.expected)

    def test_wrong_direct_parent(self):
        self.actual[('show', '-s', '--format=%P', 'HEAD')] = 'c' * 40
        with self.assertRaisesRegex(RuntimeError, 'Exact direct'):
            v.validate_ancestry(self.root, self.expected)

    def test_extra_head_parent(self):
        self.actual[('show', '-s', '--format=%P', 'HEAD')] += ' ' + 'c' * 40
        with self.assertRaisesRegex(RuntimeError, 'Exact direct'):
            v.validate_ancestry(self.root, self.expected)

    def test_anchor_cannot_replace_current_commit(self):
        self.expected['commit'] = self.anchor['commit']
        self.actual[('rev-parse', 'HEAD')] = self.anchor['commit']
        with self.assertRaisesRegex(RuntimeError, 'Exact direct'):
            v.validate_ancestry(self.root, self.expected)

    def test_wrong_anchor_tree(self):
        self.actual[('rev-parse', self.anchor['commit'] + '^{tree}')] = 'c' * 40
        with self.assertRaisesRegex(RuntimeError, 'anchor tree'):
            v.validate_ancestry(self.root, self.expected)

    def test_missing_anchor_parent(self):
        self.actual[('show', '-s', '--format=%P', self.anchor['commit'])] = self.anchor['required_commit_parents'][0]
        with self.assertRaisesRegex(RuntimeError, 'Both exact ordered native merge-anchor'):
            v.validate_ancestry(self.root, self.expected)

    def test_reordered_anchor_parents(self):
        self.actual[('show', '-s', '--format=%P', self.anchor['commit'])] = ' '.join(reversed(self.anchor['required_commit_parents']))
        with self.assertRaisesRegex(RuntimeError, 'Both exact ordered native merge-anchor'):
            v.validate_ancestry(self.root, self.expected)

    def test_wrong_anchor_merge_base(self):
        self.actual[('merge-base', *self.anchor['required_commit_parents'])] = 'c' * 40
        with self.assertRaisesRegex(RuntimeError, 'anchor base'):
            v.validate_ancestry(self.root, self.expected)

    def test_inconsistent_top_level_merge_base(self):
        self.expected['merge_base'] = 'c' * 40
        with self.assertRaisesRegex(RuntimeError, 'anchor base'):
            v.validate_ancestry(self.root, self.expected)

    def test_anchor_schema_drift(self):
        self.anchor['allow_any_parent'] = True
        with self.assertRaisesRegex(RuntimeError, 'anchor schema'):
            v.validate_ancestry(self.root, self.expected)

    def test_non_exact_anchor_identity(self):
        self.anchor['tree'] = 'main'
        with self.assertRaisesRegex(RuntimeError, 'anchor identities'):
            v.validate_ancestry(self.root, self.expected)



    def test_source_parity_uses_original_upstream_parent(self):
        observed = {'status': 'exact_frozen_source_pass', 'fork_commit': self.expected['commit'],
                    'upstream_base': self.anchor['required_commit_parents'][1]}
        owner = 'SideStore/Dependencies/minimuxer'
        output = self.root / 'result.json'
        with patch.object(v, 'SPEC', {'native_sources': {owner: self.expected}}), \
             patch.object(v, 'validate_ancestry', return_value=[self.anchor['commit']]), \
             patch.object(v.subprocess, 'check_output', return_value=json.dumps(observed)):
            v.source_parity(self.root, output)
        self.assertEqual(json.loads(output.read_text())['owners'][owner]['merge_anchor'], self.anchor)

    def test_source_parity_rejects_anchor_as_upstream(self):
        observed = {'status': 'exact_frozen_source_pass', 'fork_commit': self.expected['commit'],
                    'upstream_base': self.anchor['commit']}
        with patch.object(v, 'SPEC', {'native_sources': {'mux': self.expected}}), \
             patch.object(v, 'validate_ancestry', return_value=[self.anchor['commit']]), \
             patch.object(v.subprocess, 'check_output', return_value=json.dumps(observed)):
            with self.assertRaisesRegex(RuntimeError, 'identity differs'):
                v.source_parity(self.root, self.root / 'result.json')


class ExactReviewedJktcpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'report.json'
        self.expected = copy.deepcopy(v.SPEC['native_sources']['jktcp'])
        self.expected['commit'] = 'd' * 40
        self.expected['tree'] = 'e' * 40
        self.report = dict(self.expected['verifier_report'], fork_commit=self.expected['commit'],
                           upstream_base=self.expected['required_commit_parents'][1],
                           merge_parents=self.expected['required_commit_parents'])

    def invoke(self, report, owner='jktcp', expected=None):
        expected = self.expected if expected is None else expected
        with patch.object(v, 'SPEC', {'native_sources': {owner: expected}}), \
             patch.object(v, 'validate_ancestry', return_value=expected['required_commit_parents']), \
             patch.object(v.subprocess, 'check_output', return_value=json.dumps(report)):
            v.source_parity(self.root, self.output)

    def test_exact_jktcp_report_admitted(self):
        self.invoke(self.report)
        self.assertEqual(json.loads(self.output.read_text())['owners']['jktcp']['parity_result'], self.report)

    def test_jktcp_report_drift_missing_extra_and_false_pass_types_rejected(self):
        changes = [('owner', 'idevice'), ('status', 'exact_frozen_source_pass'),
                   ('native_execution', True), ('native_execution', 0),
                   ('product_files_verified', 9), ('product_files_verified', 10.0),
                   ('metadata_files_verified', 3), ('behavior_changes', []),
                   ('fork_commit', 'f' * 40), ('upstream_base', 'f' * 40),
                   ('merge_parents', list(reversed(self.report['merge_parents']))),
                   ('unreviewed_extra_field', True)]
        for key, value in changes:
            report = dict(self.report, **{key: value})
            with self.subTest(key=key, value=value), self.assertRaisesRegex(RuntimeError, 'jktcp verifier report'):
                self.invoke(report)
        report = dict(self.report)
        del report['native_execution']
        with self.assertRaisesRegex(RuntimeError, 'jktcp verifier report'):
            self.invoke(report)

    def test_original_owners_do_not_accept_jktcp_status(self):
        expected = copy.deepcopy(v.SPEC['native_sources']['idevice'])
        report = {'status': 'exact_reviewed_source_pass', 'fork_commit': expected['commit']}
        with self.assertRaisesRegex(RuntimeError, 'Frozen source verifier did not pass'):
            self.invoke(report, owner='idevice', expected=expected)



class ExactSecondFollowupAncestryTests(unittest.TestCase):
    def setUp(self):
        ExactFollowupAncestryTests.setUp(self)
        self.prior = {
            'commit': '1d033920660c6f50b8f30c4c333f025867cd9e96',
            'tree': 'cb4b239ebf9f3720f8998dc1debd71b3e6debd27',
            'required_commit_parents': [self.anchor['commit']]}
        self.expected['followup_parent'] = self.prior
        self.expected['required_commit_parents'] = [self.prior['commit']]
        self.actual.update({
            ('show', '-s', '--format=%P', 'HEAD'): self.prior['commit'],
            ('rev-parse', self.prior['commit'] + '^{tree}'): self.prior['tree'],
            ('show', '-s', '--format=%P', self.prior['commit']): self.anchor['commit'],
            ('merge-base', '--is-ancestor', self.prior['commit'], 'HEAD'): '',
            ('merge-base', '--is-ancestor', self.anchor['commit'], self.prior['commit']): ''})

    def test_exact_second_successor_and_original_merge(self):
        self.assertEqual(v.validate_ancestry(self.root, self.expected), [self.prior['commit']])

    def test_no_open_ended_parent_shape(self):
        for field, value in [('commit', 'c' * 40), ('tree', 'c' * 40),
                             ('required_commit_parents', ['c' * 40]), ('allow_any_ancestor', True)]:
            expected = copy.deepcopy(self.expected)
            expected['followup_parent'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'parent schema'):
                v.validate_ancestry(self.root, expected)

    def test_missing_intermediate_is_not_direct_child(self):
        self.expected.pop('followup_parent')
        with self.assertRaisesRegex(RuntimeError, 'Exact direct'):
            v.validate_ancestry(self.root, self.expected)

    def test_wrong_or_extra_head_parent(self):
        for parents in (self.anchor['commit'], self.prior['commit'] + ' ' + 'c' * 40):
            self.actual[('show', '-s', '--format=%P', 'HEAD')] = parents
            with self.subTest(parents=parents), self.assertRaisesRegex(RuntimeError, 'Exact second'):
                v.validate_ancestry(self.root, self.expected)

    def test_parent_cannot_replace_current_commit(self):
        self.expected['commit'] = self.prior['commit']
        self.actual[('rev-parse', 'HEAD')] = self.prior['commit']
        with self.assertRaisesRegex(RuntimeError, 'Exact second'):
            v.validate_ancestry(self.root, self.expected)

    def test_intermediate_tree_drift_rejected(self):
        self.actual[('rev-parse', self.prior['commit'] + '^{tree}')] = 'c' * 40
        with self.assertRaisesRegex(RuntimeError, 'parent tree'):
            v.validate_ancestry(self.root, self.expected)

    def test_intermediate_parent_drift_rejected(self):
        self.actual[('show', '-s', '--format=%P', self.prior['commit'])] = 'c' * 40
        with self.assertRaisesRegex(RuntimeError, 'parent ancestry'):
            v.validate_ancestry(self.root, self.expected)

    def test_every_exact_ancestry_link_is_required(self):
        for failed in [('merge-base', '--is-ancestor', self.prior['commit'], 'HEAD'),
                       ('merge-base', '--is-ancestor', self.anchor['commit'], self.prior['commit']),
                       ('merge-base', '--is-ancestor', self.anchor['commit'], 'HEAD'),
                       ('merge-base', '--is-ancestor', self.anchor['required_commit_parents'][1], self.anchor['commit'])]:
            def git(root, *args):
                if args == failed:raise subprocess.CalledProcessError(1, ['git', *args])
                return self.actual[args]
            with self.subTest(failed=failed), patch.object(v, 'git', git), self.assertRaises(subprocess.CalledProcessError):
                v.validate_ancestry(self.root, self.expected)

    def test_original_merge_tree_and_order_still_required(self):
        for command, invalid, error in [
            (('rev-parse', self.anchor['commit'] + '^{tree}'), 'c' * 40, 'anchor tree'),
            (('show', '-s', '--format=%P', self.anchor['commit']), ' '.join(reversed(self.anchor['required_commit_parents'])), 'Both exact ordered'),
            (('merge-base', *self.anchor['required_commit_parents']), 'c' * 40, 'anchor base')]:
            old=self.actual[command];self.actual[command]=invalid
            with self.subTest(command=command), self.assertRaisesRegex(RuntimeError, error):
                v.validate_ancestry(self.root, self.expected)
            self.actual[command]=old

    def test_current_report_records_both_fixed_anchors(self):
        observed={'status':'exact_frozen_source_pass','fork_commit':self.expected['commit'],
                  'upstream_base':self.anchor['required_commit_parents'][1]}
        owner='SideStore/Dependencies/minimuxer';output=self.root/'result.json'
        with patch.object(v,'SPEC',{'native_sources':{owner:self.expected}}), \
             patch.object(v,'validate_ancestry',return_value=[self.prior['commit']]), \
             patch.object(v.subprocess,'check_output',return_value=json.dumps(observed)):
            v.source_parity(self.root,output)
        row=json.loads(output.read_text())['owners'][owner]
        self.assertEqual(row['followup_parent'],self.prior)
        self.assertEqual(row['merge_anchor'],self.anchor)




class VPNTransportEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.sources=self.root/'sources';self.evidence=self.root/'evidence'
        self.folder=self.evidence/'vpn-transport-recovery';self.folder.mkdir(parents=True)
        self.source=self.sources/'SideStore/tests/vpn_transport_recovery/test_contract.py'
        self.source.parent.mkdir(parents=True);self.source.write_text('synthetic source')
        self.expected={'source_inputs':{'SideStore/tests/vpn_transport_recovery/test_contract.py':r.sha(self.source)},
          'input_rows':{'test':{'path':'SideStore/tests/vpn_transport_recovery/test_contract.py','sha256':r.sha(self.source)}},
          'runtime_marker':'VPN_TRANSPORT_RECOVERY_PASS transport_cases=144 actual_scanner=pass baseline_failure_reproduced=pass finite_exclusions=pass'}
        (self.folder/'fixture.swift').write_text('synthetic fixture')
        self.expected['generated_sha256']=r.sha(self.folder/'fixture.swift')
        (self.folder/'inputs.json').write_text(json.dumps({'test':{'path':str(self.source),'sha256':r.sha(self.source)}}))
        (self.folder/'compiler.log').write_text('')
        (self.folder/'runtime.log').write_text(self.expected['runtime_marker']+'\n')
        (self.evidence/'vpn-transport-recovery.exit-code.txt').write_text('0\n')
        (self.evidence/'vpn-transport-recovery.log').write_text('Ran 4 tests in 0.1s\n\nOK\n'+self.expected['runtime_marker']+'\n')
        for name,value in [('E',self.evidence),('S',self.sources)]:
            item=patch.object(r,name,value);item.start();self.addCleanup(item.stop)
    def test_exact_evidence_passes(self):
        self.assertEqual(self.root, self.root.resolve())
        # Reproduce the macOS /var -> /private/var mismatch on every platform.
        # Only fake input construction changes; the real exact-root guard stays strict.
        alias=self.root/'temp-root-alias';alias.symlink_to(self.root, target_is_directory=True)
        aliased=alias/'sources/SideStore/tests/vpn_transport_recovery/test_contract.py'
        self.assertNotEqual(str(aliased), str(aliased.resolve()))
        self.assertEqual(aliased.resolve(), self.source)
        row={'test':{'path':str(aliased),'sha256':r.sha(self.source)}}
        (self.folder/'inputs.json').write_text(json.dumps(row))
        with self.assertRaisesRegex(RuntimeError,'exact input roles'):
            r.verify_vpn_transport_evidence(self.expected)
        row['test']['path']=str(aliased.resolve())
        (self.folder/'inputs.json').write_text(json.dumps(row))
        result=r.verify_vpn_transport_evidence(self.expected)
        self.assertEqual(result['transport_case_count'],144)
        self.assertFalse(result['target_native_executed'])
    def test_changed_input_stops_admission(self):
        self.source.write_text('changed')
        with self.assertRaisesRegex(RuntimeError,'source hashes'):r.verify_vpn_transport_evidence(self.expected)
    def test_receipt_role_root_drift_rejected(self):
        (self.folder/'inputs.json').write_text(json.dumps({'test':{'path':'/old/root/test.py','sha256':r.sha(self.source)}}))
        with self.assertRaisesRegex(RuntimeError,'exact input roles'):r.verify_vpn_transport_evidence(self.expected)
    def test_generated_source_drift_rejected(self):
        (self.folder/'fixture.swift').write_text('changed')
        with self.assertRaisesRegex(RuntimeError,'generated Swift'):r.verify_vpn_transport_evidence(self.expected)
    def test_missing_or_duplicate_actual_marker_rejected(self):
        for marker in ('',self.expected['runtime_marker']+'\n'+self.expected['runtime_marker']):
            (self.folder/'runtime.log').write_text(marker+'\n')
            with self.subTest(marker=marker),self.assertRaisesRegex(RuntimeError,'runtime marker'):r.verify_vpn_transport_evidence(self.expected)
    def test_wrong_totals_skips_and_failed_command_rejected(self):
        for log in ('Ran 3 tests in 0.1s\nOK\n','Ran 4 tests in 0.1s\nOK (skipped=1)\n'):
            (self.evidence/'vpn-transport-recovery.log').write_text(log+self.expected['runtime_marker']+'\n')
            with self.subTest(log=log),self.assertRaisesRegex(RuntimeError,'failed, skipped'):r.verify_vpn_transport_evidence(self.expected)
        (self.evidence/'vpn-transport-recovery.exit-code.txt').write_text('1\n')
        with self.assertRaisesRegex(RuntimeError,'command did not pass'):r.verify_vpn_transport_evidence(self.expected)
    def test_linked_evidence_rejected(self):
        p=self.folder/'compiler.log';p.unlink();p.symlink_to(self.folder/'runtime.log')
        with self.assertRaisesRegex(RuntimeError,'Missing/linked'):r.verify_vpn_transport_evidence(self.expected)

if __name__ == '__main__':
    unittest.main(verbosity=2)
