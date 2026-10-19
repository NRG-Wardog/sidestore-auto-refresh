#!/usr/bin/env python3
"""Generate a pending candidate from fixed reviewed captures and exact Git objects.

No network, resolver, build, artifact code execution, source mutation, publication,
or activation. Output must be fresh. Capture identities are not caller-selectable.
"""
import argparse
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts'), str(ROOT/'migration')]
import p0_full_login_diagnostics as admission
import p2_composed_host as historical
from p0_maintained_sources import need, read, parse, sha, relative
from prepare_candidate_inventory import BASES, snapshot
dump = admission.dump


def generate(evidence_plan, source_roots):
    import login_capture_parser
    import maintained_sources as legacy
    need(set(source_roots) == admission.CHANGED, 'four exact changed source repositories required')
    prior_root, prior, _ = historical.approval(ROOT)
    transitions = {owner: snapshot(source_roots[owner], prior['owners'][owner]['commit'], admission.TUPLES[owner][0])
                   for owner in sorted(admission.CHANGED)}
    admission.verify_source_objects(source_roots, prior, transitions, legacy)
    source_files = admission.derive_source_metadata(prior_root, prior, transitions)
    inventory = parse(source_files['source-inventory.json'])
    for owner, source in source_roots.items():
        need(snapshot(source, BASES[owner], admission.TUPLES[owner][0]) == inventory[owner], 'complete historical-to-current inventory differs')
        # This generator reads exact Git objects; it does not claim recursive
        # acquisition or compile the current worktree. The unchanged normal CI
        # gate must verify every checkout and all five children before building.
        legacy.verify_origin(source, prior['owners'][owner]['repository'], admission.TUPLES[owner][0])
    evidence, capture_summary = login_capture_parser.replay(evidence_plan, source_roots)
    need(not (set(evidence) & (admission.SOURCE_NAMES | {'native-receipt.json', 'capture-summary.json', 'evidence-inventory.json'})), 'evidence path collision')
    for name in evidence: relative(name)
    evidence_map = {name: sha(raw) for name, raw in sorted(evidence.items())}
    need(sha(dump(evidence_map)) == admission.EVIDENCE_INVENTORY_SHA256, 'frozen parser inventory differs')
    need(sha(dump(capture_summary)) == admission.CAPTURE_SUMMARY_SHA256, 'frozen parser summary differs')
    files = {**source_files, **evidence, 'evidence-inventory.json': dump(evidence_map), 'capture-summary.json': dump(capture_summary)}
    active = parse(read(ROOT, 'migration/maintained-sources.json'))
    prior_pin_raw = read(ROOT, 'migration/maintained-sources.json') if active['source_basis'] == historical.BASIS else read(ROOT/admission.DIRECTORY, 'historical-p2/maintained-sources.json')
    historical.validate_pins(parse(prior_pin_raw), prior)
    for name in admission.inherited_names(prior):
        files['historical-p2/'+name] = prior_pin_raw if name == 'maintained-sources.json' else read(prior_root, name)
    # In-memory source/evidence reads only; receipt construction needs no output
    # directory and does not relabel the immutable original source handoff.
    handoff = parse(evidence['evidence/final-source-handoff.json'])
    need(sha(evidence['evidence/final-source-handoff.json']) == admission.HANDOFF_SHA256, 'handoff differs')
    receipt = {'schema_version': 1, 'source_basis': admission.BASIS, 'status': 'CANDIDATE_AWAITING_INDEPENDENT_REVIEW',
        'scope': admission.SCOPE, 'original_handoff_sha256': admission.HANDOFF_SHA256,
        'capture_summary_sha256': admission.CAPTURE_SUMMARY_SHA256,
        'captured_source_owners': handoff['captured_source_owners'], 'final_source_owners': handoff['final_source_owners'],
        'final_sidestore_equivalence': handoff['final_sidestore_equivalence'], 'scope_notes': handoff['capture_scope_notes'],
        'guest_execution': False, 'device_login_proven': False, 'physical_device_execution': False,
        'initializer47_containment_integrated': False, 'final_sidestore_commit_directly_compiled': False,
        'fresh_all_language_ui_execution': False, 'normal_release_completed': False}
    files['native-receipt.json'] = dump(receipt)
    review = {'schema_version': 1, 'source_basis': admission.BASIS, 'integration_parent': admission.INTEGRATION_PARENT,
        'inherited_p2_review_sha256': historical.REVIEW_SHA256, 'owners': admission.expected_owners(prior),
        'files': {name: sha(raw) for name, raw in sorted(files.items())},
        'native_validation': admission.native_validation(sha(files['native-receipt.json']))}
    summary = {'status': 'CANDIDATE_AWAITING_INDEPENDENT_REVIEW', 'source_basis': admission.BASIS,
        'candidate_review_sha256': sha(dump(review)), 'metadata_files': len(files),
        'source_transition_sha256': admission.TRANSITION_SHA256, 'capture_summary_sha256': admission.CAPTURE_SUMMARY_SHA256,
        'all_seven_local_source_gate_proven': False, 'full_graph_source_gate_required_in_normal_ci': True,
        'artifact_code_executed': False, 'guest_execution': False, 'device_login_proven': False,
        'activated': False, 'published': False}
    return files, review, summary


def write_candidate(output, files, review, summary):
    need(set(files) == set(review['files']) and all(isinstance(raw, bytes) and sha(raw) == review['files'][name] for name, raw in files.items()), 'candidate inventory/hash differs')
    need(summary['candidate_review_sha256'] == sha(dump(review)) and summary['activated'] is False, 'pending candidate summary differs')
    for name in files: relative(name)
    output = output.absolute()
    need(not output.exists() and not output.is_symlink() and output.parent.is_dir() and
         not any(parent.is_symlink() for parent in output.parents), 'fresh ordinary output directory required')
    with tempfile.TemporaryDirectory(prefix='login-candidate-', dir=output.parent) as temporary:
        staging = Path(temporary)/'candidate'; staging.mkdir()
        for name, raw in files.items():
            target = staging/'metadata'/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        (staging/'candidate-review.json').write_bytes(dump(review))
        (staging/'candidate-summary.json').write_bytes(dump(summary))
        need(not output.exists(), 'candidate output appeared during generation')
        staging.rename(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-plan', type=Path, required=True)
    parser.add_argument('--source', action='append', required=True, help='Owner=exact local Git repository')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        need(not args.output.exists(), 'output already exists')
        roots = {}
        for item in args.source:
            owner, path = item.split('=', 1)
            need(owner not in roots, 'duplicate source owner')
            roots[owner] = Path(path)
        files, review, summary = generate(args.evidence_plan, roots)
        write_candidate(args.output, files, review, summary)
        print(dump(summary).decode(), end='')
    except Exception as error:
        parser.exit(2, 'REJECTED: '+str(error)+'\n')


if __name__ == '__main__':
    main()
