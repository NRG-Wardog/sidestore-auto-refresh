"""Run the frozen setup checker with one exact historical renderer comparison.

Only that comparison consumes the proven prompt inverse. The source-owned
fixture generator still reads, hashes and emits the actual current renderer.
The separate native prompt suite qualifies new behavior; this is not equivalence.
"""
import sys
from pathlib import Path
ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'ci/native_paths.py').is_file())
sys.path.insert(0, str(ROOT))
import argparse
import json
from ci.qualification.native import functional_recovery_projection as functional
from ci.qualification.native import portal_identity_projection as portal
from ci.qualification.native import portal_owner_successor as git_proof

TEST = 'LiveContainer/.ci/comprehensive-diagnostics/test_setup_localization_contract.py'
GENERATOR = 'LiveContainer/.ci/comprehensive-diagnostics/generate_setup_localization_fixture.py'
PINS = {
    TEST: '1222f013816035a0ec590c6557cb7b07e6a37983d0cb77dd5ecf5ca1f0e1b655',
    GENERATOR: '154c18a26e69a8be402ff61a563f57c28991f48d1fe690a02252406a70874e36',
}
KEY = 'reviewed_extension_prompt_renderer_inverse'
CLAIM = 'unchanged_after_exact_prompt_inverse_and_russian_fallback_normalization'
ORIGINAL = """    renderer = normalize_russian_renderer(s['localization'][s['localization'].index('enum V3Localization {'):])
    baseline_renderer = old['localization'][old['localization'].index('enum V3Localization {'):]
    assert renderer == baseline_renderer
    assert normalize_russian_renderer(s['localization'].replace(
        'guard depth < 8 else { return nil }', 'guard depth < 9 else { return nil }', 1)
        [s['localization'].index('enum V3Localization {'):]) != baseline_renderer
"""
REVIEWED = """    historical_localization = _restore_prompt_renderer(s['localization'])
    renderer = normalize_russian_renderer(historical_localization[historical_localization.index('enum V3Localization {'):])
    baseline_renderer = old['localization'][old['localization'].index('enum V3Localization {'):]
    assert renderer == baseline_renderer
    assert normalize_russian_renderer(historical_localization.replace(
        'guard depth < 8 else { return nil }', 'guard depth < 9 else { return nil }', 1)
        [historical_localization.index('enum V3Localization {'):]) != baseline_renderer
"""
require, sha = functional.require, functional.sha


def descriptor():
    file = functional.specifications()['files'][functional.LOCALIZATION]
    return {'schema': 'exact-setup-prompt-renderer-comparison-v1',
            'manifest_sha256': functional.MANIFEST_SHA256,
            'current_sha256': file['after_sha256'], 'historical_sha256': file['before_sha256'],
            'fixture_source': 'actual_current_bytes', 'native_execution': False}


def current(contract):
    return contract.get('input_sha256', {}).get(functional.LOCALIZATION) == descriptor()['current_sha256']


def command(contract, original):
    """Only the exact current prompt input selects the separately bound caller."""
    if not current(contract):
        return original
    require(len(original) == 3 and original[1] == '-B'
            and Path(original[2]).as_posix().endswith('/' + TEST), 'Setup localization command differs')
    return [original[0], '-B', Path(__file__).resolve(), '--sources', Path(original[2]).parents[3]]


def project_test(raw):
    require(sha(raw) == PINS[TEST], 'Unreviewed setup localization test bytes')
    text = raw.decode()
    require(text.count(ORIGINAL) == 1 and REVIEWED not in text, 'Setup renderer comparison boundary differs')
    projected = text.replace(ORIGINAL, REVIEWED, 1)
    require(projected.replace(REVIEWED, ORIGINAL, 1) == text, 'Setup comparison inverse differs')
    return projected


def restore_renderer(text):
    data = text.encode()
    spec = functional.specifications()['files'][functional.LOCALIZATION]
    require(sha(data) == spec['after_sha256'], 'Setup prompt renderer is not current reviewed bytes')
    return functional.restore_bytes(functional.LOCALIZATION, data).decode()


def verify_static(value, contract):
    if current(contract):
        require(value.get(KEY) == descriptor() and value.get('strict_prose_and_original_copy') == CLAIM,
                'Missing/changed exact setup prompt inverse evidence')
    else:
        require(KEY not in value, 'Unexpected setup prompt inverse evidence')


def run(sources, candidate, baseline, output):
    sources = Path(sources).resolve(strict=True)
    candidate = Path(candidate).resolve(strict=True)
    baseline = Path(baseline).resolve(strict=True)
    output = Path(output)
    require(candidate == sources / 'LiveContainer', 'Setup prompt candidate root differs')
    require(not output.exists() and not output.is_symlink() and not output.resolve().is_relative_to(sources),
            'Setup prompt output must be fresh and outside sources')
    functional.validate_sources(sources)
    root = git_proof.source_root(sources, 'LiveContainer')
    commit = functional.OWNER_COMMITS['LiveContainer'][1]
    inputs = {}
    for path, digest in PINS.items():
        data = portal.read_source(sources, path)
        require(sha(data) == digest and data == git_proof.git(root, 'cat-file', 'blob', commit + ':' + path.split('/', 1)[1]),
                'Setup localization caller working/Git bytes differ: ' + path)
        inputs[path] = data
    projected = project_test(inputs[TEST])
    scope = {'__name__': '_reviewed_setup_prompt_checker', '__file__': str(sources / TEST),
             '_restore_prompt_renderer': restore_renderer}
    exec(compile(projected, str(sources / TEST), 'exec'), scope)
    scope['check'](candidate, baseline, output)
    # The original checker already verifies the actual fixture prefix, output
    # hashes and full case closure. Keep those current hashes, not predecessor hashes.
    static_path = output / 'static-contract.json'
    value = json.loads(static_path.read_text())
    require(value.get('strict_prose_and_original_copy') == 'unchanged_except_exact_russian_fallback_additions'
            and value.get('source_sha256', {}).get('localization') == descriptor()['current_sha256'],
            'Setup checker did not retain actual renderer provenance')
    value['strict_prose_and_original_copy'] = CLAIM
    value[KEY] = descriptor()
    static_path.write_text(json.dumps(value, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--candidate-root', type=Path, required=True)
    parser.add_argument('--baseline-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.sources, args.candidate_root, args.baseline_root, args.output)
