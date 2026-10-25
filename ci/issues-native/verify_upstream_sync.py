"""Additive compile-only gates. Never execute target-native or authentication code."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
SPEC = json.loads((HERE / 'upstream-sync-contract.json').read_text())


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull,
               GIT_NO_LAZY_FETCH='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0',
               GIT_ALLOW_PROTOCOL='https')
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null',
        '-c', 'advice.graftFileDeprecated=false', '--no-replace-objects', '-C', str(root),
        *args], text=True, env=env).strip()


def validate_ancestry(root, expected):
    require(git(root, 'rev-parse', 'HEAD') == expected['commit'], 'Native owner commit differs')
    require(git(root, 'rev-parse', '--is-shallow-repository') == 'false', 'Shallow native source history')
    require(git(root, 'rev-parse', 'HEAD^{tree}') == expected['tree'], 'Native owner tree differs')
    parents = git(root, 'show', '-s', '--format=%P', 'HEAD').split()
    if 'merge_anchor' not in expected:
        require(len(expected['required_commit_parents']) == 2 and
                parents == expected['required_commit_parents'], 'Both exact ordered native parents are required')
        for parent in parents:
            git(root, 'merge-base', '--is-ancestor', parent, 'HEAD')
        require(git(root, 'merge-base', *parents) == expected['merge_base'], 'Native merge base differs')
    else:
        # Admit only the reviewed direct child of an exact, independently proven
        # two-parent merge. This is not a general single-parent ancestry fallback.
        anchor = expected['merge_anchor']
        require(set(anchor) == {'commit', 'tree', 'required_commit_parents', 'merge_base'},
                'Native merge anchor schema differs')
        require(all(isinstance(anchor[key], str) and re.fullmatch('[0-9a-f]{40}', anchor[key])
                    for key in ('commit', 'tree', 'merge_base')), 'Native merge anchor identities differ')
        if 'followup_parent' in expected:
            # One exact additional successor only, never an arbitrary ancestor,
            # branch name, open-ended chain, or replacement merge anchor.
            prior = expected['followup_parent']
            require(prior == {
                'commit': '1d033920660c6f50b8f30c4c333f025867cd9e96',
                'tree': 'cb4b239ebf9f3720f8998dc1debd71b3e6debd27',
                'required_commit_parents': ['48419a2d24bd74efcee6dc80552ea89e85dafdee']
            } and anchor['commit'] == prior['required_commit_parents'][0],
                    'Exact reviewed native follow-up parent schema differs')
            require(expected['commit'] not in (prior['commit'], anchor['commit']) and
                    expected['required_commit_parents'] == [prior['commit']] and
                    parents == [prior['commit']], 'Exact second native follow-up parent is required')
            require(git(root, 'rev-parse', prior['commit'] + '^{tree}') == prior['tree'],
                    'Native follow-up parent tree differs')
            require(git(root, 'show', '-s', '--format=%P', prior['commit']).split() == [anchor['commit']],
                    'Native follow-up parent ancestry differs')
            git(root, 'merge-base', '--is-ancestor', prior['commit'], 'HEAD')
            git(root, 'merge-base', '--is-ancestor', anchor['commit'], prior['commit'])
        else:
            require(expected['commit'] != anchor['commit'] and
                    expected['required_commit_parents'] == [anchor['commit']] and
                    parents == [anchor['commit']], 'Exact direct native follow-up parent is required')
        require(git(root, 'rev-parse', anchor['commit'] + '^{tree}') == anchor['tree'],
                'Native merge anchor tree differs')
        anchor_parents = git(root, 'show', '-s', '--format=%P', anchor['commit']).split()
        require(len(anchor['required_commit_parents']) == 2 and
                len(set(anchor['required_commit_parents'])) == 2 and
                anchor_parents == anchor['required_commit_parents'],
                'Both exact ordered native merge-anchor parents are required')
        git(root, 'merge-base', '--is-ancestor', anchor['commit'], 'HEAD')
        for parent in anchor_parents:
            git(root, 'merge-base', '--is-ancestor', parent, anchor['commit'])
        require(expected['merge_base'] == anchor['merge_base'] and
                git(root, 'merge-base', *anchor_parents) == anchor['merge_base'],
                'Native merge anchor base differs')
    for relative, expected_sha in expected['files_sha256'].items():
        path = root / relative
        require(path.is_file() and not path.is_symlink(), 'Missing or linked native metadata: ' + relative)
        require(sha(path) == expected_sha, 'Native metadata hash differs: ' + relative)
    return parents


def source_parity(sources, output):
    report = {'status': 'STARTED_NOT_PASSED', 'target_native_executed': False, 'owners': {}}
    write(output, report)
    for owner, expected in SPEC['native_sources'].items():
        root = sources / owner
        parents = validate_ancestry(root, expected)
        # Invoke the exact unchanged committed verifier, not an inventory-only replacement.
        raw = subprocess.check_output([sys.executable, '-B', str(root / '.ci/source-parity.py')], text=True)
        observed = json.loads(raw)
        merge = expected.get('merge_anchor', expected)
        if owner == 'jktcp':
            # This owner intentionally incorporates reviewed upstream behavior;
            # do not mislabel it as the older generated-source parity baseline.
            exact = dict(expected['verifier_report'])
            require(set(exact) == {'owner', 'status', 'product_files_verified',
                    'metadata_files_verified', 'behavior_changes', 'native_execution'} and
                    exact['owner'] == 'jktcp' and exact['status'] == 'exact_reviewed_source_pass' and
                    exact['native_execution'] is False and exact['product_files_verified'] == 10 and
                    exact['metadata_files_verified'] == 4, 'Unexpected jktcp verifier contract')
            exact.update(fork_commit=expected['commit'], upstream_base=merge['required_commit_parents'][1],
                         merge_parents=parents)
            require(observed == exact and observed.get('native_execution') is False and
                    type(observed.get('product_files_verified')) is int and
                    type(observed.get('metadata_files_verified')) is int,
                    'Exact reviewed jktcp verifier report differs')
        else:
            require(observed['status'] == 'exact_frozen_source_pass', 'Frozen source verifier did not pass')
        require(observed['fork_commit'] == expected['commit'] and
                observed['upstream_base'] == merge['required_commit_parents'][1],
                'Frozen source verifier identity differs')
        report['owners'][owner] = {'parents': parents, 'expected_tree': expected['tree'],
            'metadata_sha256': expected['files_sha256'], 'parity_result': observed}
        if 'merge_anchor' in expected:
            report['owners'][owner]['merge_anchor'] = expected['merge_anchor']
        if 'followup_parent' in expected:
            report['owners'][owner]['followup_parent'] = expected['followup_parent']
        write(output, report)
    report['status'] = 'EXACT_COMMITTED_SOURCE_PARITY_PASS_NOT_RUNTIME_VALIDATION'
    write(output, report)
    print(json.dumps(report, indent=2, sort_keys=True))


def sidestore_tests(sources, output):
    folder = sources / 'SideStore/tests/upstream_sync'
    manifest = {p.relative_to(sources / 'SideStore').as_posix(): sha(p)
                for p in sorted(folder.glob('test_*.py'))}
    suite = unittest.defaultTestLoader.discover(str(folder), pattern='test_*.py')
    require(suite.countTestCases() == SPEC['sidestore_test_count'], 'SideStore test inventory differs')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'status': 'PASS' if result.wasSuccessful() and not result.skipped else 'FAIL',
        'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'skipped': len(result.skipped), 'test_sources_sha256': manifest,
        'target_native_executed': False, 'authentication_executed': False,
        'scope': 'source-owned fake-data/model and source tests only'}
    write(output, report)
    require(result.wasSuccessful() and not result.skipped and
            result.testsRun == SPEC['sidestore_test_count'], 'SideStore source-owned tests did not all pass')
    require(manifest == {p.relative_to(sources / 'SideStore').as_posix(): sha(p)
                        for p in sorted(folder.glob('test_*.py'))}, 'SideStore test sources changed')
    print(json.dumps(report, indent=2, sort_keys=True))


def minimuxer_tests(sources, output, swiftc):
    require(swiftc, 'A Swift compiler is required for the fake-only harness')
    root = sources / 'SideStore/Dependencies/minimuxer'
    expected = SPEC['minimuxer_fake_tests']
    manifest = {name: sha(root / name) for name in expected['source_inputs']}
    require(manifest == expected['source_inputs'], 'Minimuxer fake harness source inputs differ')
    report = {'status': 'STARTED_NOT_PASSED', 'source_inputs_sha256': manifest,
              'target_native_executed': False, 'authentication_executed': False,
              'scope': 'extracted production Swift with source-owned fake dependencies only'}
    write(output, report)
    command = [sys.executable, '-B', str(root / '.ci/verify-upstream-sync.py'), '--swiftc', swiftc]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # Preserve the genuine compiler/harness result even if validation fails.
    output.with_suffix('.log').write_text(result.stdout)
    print(result.stdout, end='')
    require(result.returncode == 0, 'Minimuxer fake-only compiler/harness failed')
    require(result.stdout.splitlines().count(expected['success_line']) == 1, 'Minimuxer exact fake-test totals not observed')
    # The unchanged source-owned runner prints its JSON report last; compiler
    # warnings can contain braces and must not be mistaken for that report.
    start = result.stdout.rfind('\n{')
    observed = json.loads(result.stdout[start + 1:]) if start >= 0 else {}
    require(observed == {'status': 'static_source_invariants_pass', 'native_or_device_execution': False,
                         'swift_smoke': 'source_owned_fake_tests_pass'}, 'Minimuxer fake-only report differs')
    require(manifest == {name: sha(root / name) for name in expected['source_inputs']},
            'Minimuxer fake harness source inputs changed')
    report.update(status='SOURCE_OWNED_FAKE_SWIFT_PASS_NOT_RUNTIME_VALIDATION',
                  pairing_cases=7, transport_cases=144, gateway_batch_switch='pass',
                  atomic_configuration='pass', source_verifier_report=observed,
                  command=command)
    write(output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['source-parity', 'sidestore-tests', 'minimuxer-tests'])
    parser.add_argument('--sources', type=Path, default=Path('sources'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--swiftc')
    args = parser.parse_args()
    sources = args.sources.resolve(strict=True)
    if args.phase == 'source-parity':
        source_parity(sources, args.output)
    elif args.phase == 'sidestore-tests':
        sidestore_tests(sources, args.output)
    else:
        minimuxer_tests(sources, args.output, args.swiftc)


if __name__ == '__main__':
    main()
