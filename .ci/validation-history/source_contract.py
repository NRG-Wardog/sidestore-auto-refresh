"""Preserve frozen 6.0 history while admitting only exact reviewed successors.

The original manifest is never rewritten. Source objects are read as data;
no historical or artifact-supplied code is executed.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OVERRIDES = {'scripts/package_livecontainer_combined.py', 'tests/test_dylibify_source_build.py', '.github/workflows/maintained-packaging-contract.yml'}
DEPENDENCIES = {'scripts/experiment_local_symbols.py', 'scripts/experiment_sidebackup_stabs.py', 'scripts/local_symbol_packaging.py', 'scripts/macho_local_symbol_guard.py', 'scripts/openssl_local_symbol_policy.py', 'scripts/optimize_openssl_local_candidate.py', 'scripts/optimize_sidebackup_candidate.py', 'scripts/optimize_unsigned_widget.py', 'scripts/patch_refresh_callback_admission.py', 'scripts/widget_local_symbols.py', 'scripts/widget_signature_policy.py', 'docs/PACKAGING_PATCH_SURFACE.json', 'tests/test_ipa_size_report.py', 'tests/fixtures/upstream-packaging/2d092098-anchor-profile.json', 'tests/fixtures/upstream-packaging/manifest.json', 'tests/test_packaging_adapters.py', 'scripts/ipa_size_report.py', 'tests/fixtures/upstream-packaging/UPSTREAM-LICENSE', 'scripts/packaging_adapters.py', 'tests/fixtures/upstream-packaging/2d092098-build_github.sh.txt', 'scripts/rehearse_packaging_integration.py', 'tests/fixtures/upstream-packaging/433dd73-build_github.sh.txt'}
ADDED_WORKFLOWS = {'.github/workflows/cold-vpn-zopfli.yml', '.github/workflows/openssl-local-host-diagnostic.yml', '.github/workflows/custom-integration-contract.yml', '.github/workflows/zopfli-package.yml', '.github/workflows/issue53-lts-source.yml', '.github/workflows/issue56-zopfli.yml', '.github/workflows/cold-vpn-native.yml', '.github/workflows/issue56-native.yml', '.github/workflows/refresh-truth-preflight.yml', '.github/workflows/cold-vpn-preflight.yml', '.github/workflows/language-switch-ui.yml', '.github/workflows/language-switch-zopfli.yml', '.github/workflows/issue55-openssl-size.yml', '.github/workflows/issues-native.yml', '.github/workflows/issue53-pinned-crypto.yml', '.github/workflows/multitask-launch-preflight.yml', '.github/workflows/language-switch-native.yml', '.github/workflows/startup-upgrade-preflight.yml', '.github/workflows/recover-native-package.yml'}
HISTORICAL = '50b33610136197e3a0976f03f9660603ba6cf737'
QUALIFIED = 'fefb69346a4d3bb79ebbb4294b3b99bf81fcd045'
TREE = '2883e33a3d0328b905c90f543df08a0ac3d024cb'

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def pairs(items):
    value = {}
    for key, item in items:
        require(key not in value, 'Duplicate source contract key')
        value[key] = item
    return value

def read(path):
    require(path.is_file() and not path.is_symlink(), 'Missing or linked source: '+str(path))
    return path.read_bytes()

def git_read(root, ref, path):
    return subprocess.check_output(['git', 'show', ref+':'+path], cwd=root)

def verify_sources(root=ROOT, *, object_read=git_read, tree_read=None):
    root = Path(root)
    raw = read(root/'.ci/validation-history/manifest.json')
    require(raw == object_read(root,HISTORICAL,'.ci/validation-history/manifest.json'), 'Frozen historical manifest object differs')
    m = json.loads(raw, object_pairs_hook=pairs)
    c = json.loads(read(root/'.ci/validation-history/reviewed-successor.json'), object_pairs_hook=pairs)
    require(set(c) == {'schema','historical_manifest_sha256','historical_commit','qualified_commit','qualified_tree','protected_successors','added_workflows_sha256','dependency_sha256'}, 'Successor closure differs')
    require(c['schema'] == 'reviewed-main-source-successor-v1', 'Successor schema differs')
    require(c['historical_manifest_sha256'] == sha(raw), 'Frozen historical manifest differs')
    require((c['historical_commit'],c['qualified_commit'],c['qualified_tree']) == (HISTORICAL,QUALIFIED,TREE), 'Reviewed lineage differs')
    require(set(c['protected_successors']) == OVERRIDES, 'Protected successor scope differs')
    require(set(c['dependency_sha256']) == DEPENDENCIES, 'Packaging dependency closure differs')
    require(set(c['added_workflows_sha256']) == ADDED_WORKFLOWS, 'Added workflow scope differs')
    tree = tree_read(root,QUALIFIED) if tree_read else subprocess.check_output(['git','rev-parse',QUALIFIED+'^{tree}'],cwd=root,text=True).strip()
    require(tree == TREE, 'Qualified Git tree differs')
    # Historical assertions remain exact and inspect the original Git objects.
    for name, row in c['protected_successors'].items():
        require(set(row) == {'historical_sha256','reviewed_sha256','origin'}, 'Protected mapping closure differs')
        require(row['historical_sha256'] == m['protected_sha256'][name], 'Historical mapping changed')
        require(sha(object_read(root,HISTORICAL,name)) == row['historical_sha256'], 'Historical source object differs: '+name)
        origin = 'source_guard_repair' if name == '.github/workflows/maintained-packaging-contract.yml' else 'qualified_candidate'
        require(row['origin'] == origin, 'Source origin differs')
        if origin == 'qualified_candidate':
            require(sha(object_read(root,QUALIFIED,name)) == row['reviewed_sha256'], 'Qualified source object differs: '+name)
    for category in ('protected_sha256','archived_workflows_sha256'):
        for name, expected in m[category].items():
            if name in c['protected_successors']:
                expected = c['protected_successors'][name]['reviewed_sha256']
            require(sha(read(root/name)) == expected, 'Reviewed source differs: '+name)
    for category in ('added_workflows_sha256','dependency_sha256'):
        for name, expected in c[category].items():
            require(name not in m['protected_sha256'], 'Existing protected path reclassified')
            if category == 'added_workflows_sha256':
                require(name.startswith('.github/workflows/') and name.endswith('.yml') and len(Path(name).parts) == 3, 'Invalid added workflow path')
            require(sha(object_read(root,QUALIFIED,name)) == expected, 'Qualified successor object differs: '+name)
            require(sha(read(root/name)) == expected, 'Reviewed successor bytes differ: '+name)
    original = {Path(p).name for p in m['protected_sha256'] if p.startswith('.github/workflows/')}
    expected = original | {'validation-history-contract.yml'} | {Path(p).name for p in c['added_workflows_sha256']}
    require({p.name for p in (root/'.github/workflows').glob('*.yml')} == expected, 'Unreviewed workflow inventory')
    return m,c

if __name__ == '__main__':
    require(sys.argv[1:] in ([], ['--packaging']), 'Unknown source verification argument')
    verify_sources()
    print('PASS: frozen historical source and exact qualified successor/dependency/workflow closure verified; no artifact executed.')
