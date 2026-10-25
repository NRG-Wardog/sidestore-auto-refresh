#!/usr/bin/env python3
"""Parser-only P2 candidate generation; never grants admission or edits active pins.

Authentic API records must be independently retrieved. Artifact code/binaries are
never imported or executed. Independent review and isolated activation follow.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts'), str(ROOT/'migration/p2_capture_parser')]
import p0_cxx_allocation as historical
import p2_composed_host as admission
from p0_maintained_sources import need, read, parse, sha, relative


def dump(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n').encode()


def generate(args):
    import replay_p2_capture as replay
    prior_root, prior, _ = historical.approval(ROOT)
    source = ROOT/admission.DIRECTORY
    admission.validate_source_metadata(source, prior_root, prior)
    need(admission.git_delta(args.host, prior['owners']['LiveContainer']['commit'], admission.LC_COMMIT) ==
         parse(read(source, 'reviewed-source-delta.json')), 'actual exhaustive LiveContainer delta differs')
    need(admission.git_delta(args.host, admission.LC_PARENT, admission.LC_COMMIT) ==
         parse(read(source, 'composition-parent-delta.json')), 'actual composition-parent delta differs')
    need(replay.git(args.host, 'rev-list', '--parents', '-n1', admission.LC_COMMIT).decode().split() ==
         [admission.LC_COMMIT, admission.LC_PARENT], 'composed source is not the reviewed direct child')
    shared = {'runner': args.runner, 'host': args.host, 'peer': args.peer, 'producer': args.producer}
    native = replay.native(SimpleNamespace(zip=args.native_zip, run_json=args.native_run_json,
        artifact_json=args.native_artifact_json, **shared))
    ui = replay.parent_ui(SimpleNamespace(zip=args.ui_zip, run_json=args.ui_run_json,
        artifact_json=args.ui_artifact_json, **shared))
    capture = native['native-capture-candidate.json']
    need(capture['status'] == 'CAPTURED_FOR_INDEPENDENT_REVIEW', 'native parser attempted to grant acceptance')
    # Check the accepted schema in memory only. Retained output remains pending.
    normalized = copy.deepcopy(capture)
    normalized['status'] = 'INDEPENDENTLY_REVIEWED_COMPOSED_HOST_PASS'
    owners = admission.expected_owners(prior)
    admission.validate_capture(normalized, owners)
    admission.validate_ui_basis(ui['parent-ui-basis.json'])
    names = admission.SOURCE_NAMES | {'historical-p0/'+name for name in admission.inherited_names(prior)}
    files = {name: read(source, name) for name in names}
    files['native-capture.json'] = dump(capture)
    files['parent-ui-basis.json'] = dump(ui['parent-ui-basis.json'])
    review = {'schema_version': 1, 'source_basis': admission.BASIS, 'integration_parent': admission.INTEGRATION_PARENT,
        'inherited_p0_review_sha256': historical.REVIEW_SHA256, 'owners': owners,
        'files': {name: sha(raw) for name, raw in sorted(files.items())},
        'native_validation': {'source_basis': admission.BASIS, 'readiness_scope': 'candidate_for_gated_full_build_review',
            'review_sha256': None, 'inherited_p0_review_sha256': historical.REVIEW_SHA256,
            'fresh_composed_capture_sha256': sha(files['native-capture.json']),
            'parent_ui_basis_sha256': sha(files['parent-ui-basis.json']),
            'tested_owners': capture['tested_owners'], 'scope': admission.FRESH_SCOPE}}
    parser_files = ['replay_p2_capture.py', 'p2_archive.py', 'p2_sources.py', 'p2_commands.py', 'p2_product.py']
    summary = {'status': 'CANDIDATE_AWAITING_INDEPENDENT_REVIEW', 'source_basis': admission.BASIS,
        'candidate_review_sha256': sha(dump(review)), 'metadata_files': len(files),
        'parser_sha256': {name: sha((ROOT/'migration/p2_capture_parser'/name).read_bytes()) for name in parser_files},
        'native_replay': native['native-replay-summary.json'], 'parent_ui_replay': ui['parent-ui-replay-summary.json'],
        'full_graph_source_gate_proven_by_this_generator': False,
        'full_graph_source_gate_required_in_normal_ci': True,
        'guest_execution': False, 'artifact_binaries_executed': False, 'activated': False, 'published': False}
    return files, review, summary


def write_candidate(output, files, review, summary):
    need(isinstance(files, dict) and set(files) == set(review['files']) and
         all(isinstance(raw, bytes) and sha(raw) == review['files'][name] for name, raw in files.items()), 'candidate file inventory/hash differs')
    need(summary['candidate_review_sha256'] == sha(dump(review)) and summary['activated'] is False, 'candidate summary differs')
    for name in files: relative(name)
    output = output.absolute()
    need(not output.exists() and not output.is_symlink() and output.parent.is_dir() and
         not any(parent.is_symlink() for parent in output.parents), 'fresh unlinked candidate output required')
    with tempfile.TemporaryDirectory(prefix='p2-candidate-', dir=output.parent) as temporary:
        staging = Path(temporary)/'candidate'; staging.mkdir()
        for name, raw in files.items():
            target = staging/'metadata'/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        (staging/'candidate-review.json').write_bytes(dump(review))
        (staging/'candidate-summary.json').write_bytes(dump(summary))
        need(not output.exists(), 'candidate output appeared during generation')
        staging.rename(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('native-zip', 'native-run-json', 'native-artifact-json', 'ui-zip', 'ui-run-json', 'ui-artifact-json',
                 'runner', 'host', 'peer', 'producer', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        need(not args.output.exists(), 'candidate output already exists')
        files, review, summary = generate(args)
        write_candidate(args.output, files, review, summary)
        print(json.dumps({'status': summary['status'], 'candidate_review_sha256': summary['candidate_review_sha256'],
                          'metadata_files': len(files), 'activated': False}, sort_keys=True))
    except Exception as error:
        parser.exit(2, 'REJECTED: '+str(error)+'\n')


if __name__ == '__main__':
    main()
