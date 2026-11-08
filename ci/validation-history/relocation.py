"""Exact, same-depth CI relocation. Never execute historical Git objects."""
from pathlib import Path
import hashlib
BASELINE = '6f20f702daad7aa8f870a9a3fe0d0b321dc41741'
# Fixed source/destination pairs, not caller-provided remappings.
RELOCATED = {'.ci/branch-cleanup/allowlist.json': 'ci/branch-cleanup/allowlist.json',
 '.ci/branch-cleanup/classify.py': 'ci/branch-cleanup/classify.py',
 '.ci/branch-cleanup/cleanup.py': 'ci/branch-cleanup/cleanup.py',
 '.ci/branch-cleanup/inventory.json': 'ci/branch-cleanup/inventory.json',
 '.ci/branch-cleanup/test_classify.py': 'ci/branch-cleanup/test_classify.py',
 '.ci/branch-cleanup/test_cleanup.py': 'ci/branch-cleanup/test_cleanup.py',
 '.ci/branch-forwardports/inputs.json': 'ci/branch-forwardports/inputs.json',
 '.ci/branch-forwardports/run.py': 'ci/branch-forwardports/run.py',
 '.ci/branch-semantic-review/README.md': 'ci/branch-semantic-review/README.md',
 '.ci/branch-semantic-review/inventory.json': 'ci/branch-semantic-review/inventory.json',
 '.ci/branch-semantic-review/inventory.py': 'ci/branch-semantic-review/inventory.py',
 '.ci/branch-semantic-review/source-receipt.json': 'ci/branch-semantic-review/source-receipt.json',
 '.ci/branch-semantic-review/test_inventory.py': 'ci/branch-semantic-review/test_inventory.py',
 '.ci/maintained-build/contract.json': 'ci/maintained-build/contract.json',
 '.ci/maintained-build/test_contract.py': 'ci/maintained-build/test_contract.py',
 '.ci/release-notes-v6.0.0-20261024/RELEASE_NOTES_v6.0.0-amended.md': 'ci/release-notes-v6.0.0-20261024/RELEASE_NOTES_v6.0.0-amended.md',
 '.ci/release-notes-v6.0.0-20261024/amend.py': 'ci/release-notes-v6.0.0-20261024/amend.py',
 '.ci/release-notes-v6.0.0-20261024/amendment-receipt.json': 'ci/release-notes-v6.0.0-20261024/amendment-receipt.json',
 '.ci/release-notes-v6.0.0-20261024/test_amend.py': 'ci/release-notes-v6.0.0-20261024/test_amend.py',
 '.ci/release-v6.0.0/RELEASE_NOTES_v6.0.0.md': 'ci/release-v6.0.0/RELEASE_NOTES_v6.0.0.md',
 '.ci/release-v6.0.0/SHA256SUMS': 'ci/release-v6.0.0/SHA256SUMS',
 '.ci/release-v6.0.0/promote.py': 'ci/release-v6.0.0/promote.py',
 '.ci/release-v6.0.0/release-provenance.json': 'ci/release-v6.0.0/release-provenance.json',
 '.ci/release-v6.0.0/reviewed-inputs.json': 'ci/release-v6.0.0/reviewed-inputs.json',
 '.ci/release-v6.0.0/test_promote.py': 'ci/release-v6.0.0/test_promote.py',
 '.github/workflows/amend-v6.0.0-notes.yml': '.github/workflow-archive/maintenance-v6.0.0/amend-v6.0.0-notes.yml',
 '.github/workflows/publish-v6.0.0.yml': '.github/workflow-archive/maintenance-v6.0.0/publish-v6.0.0.yml',
 '.github/workflows/reviewed-branch-classify.yml': '.github/workflow-archive/maintenance-v6.0.0/reviewed-branch-classify.yml',
 '.github/workflows/reviewed-branch-cleanup.yml': '.github/workflow-archive/maintenance-v6.0.0/reviewed-branch-cleanup.yml',
 '.github/workflows/reviewed-branch-forwardports.yml': '.github/workflow-archive/maintenance-v6.0.0/reviewed-branch-forwardports.yml',
 '.github/workflows/reviewed-branch-semantic-inventory.yml': '.github/workflow-archive/maintenance-v6.0.0/reviewed-branch-semantic-inventory.yml'}

# Machine-readable contracts belong with their executable CI owners.
DOCUMENT_CONTRACT_RELOCATIONS = {'docs/CUSTOM_INTEGRATION_CONTRACTS.json': 'ci/issue52-integration/contracts.json',
 'docs/ERROR_CODES_V1.json': 'ci/diagnostics/error-codes-v1.json',
 'docs/ERROR_PRESENTATION_COVERAGE.json': 'ci/diagnostics/presentation-coverage.json',
 'docs/PACKAGING_PATCH_SURFACE.json': 'ci/packaging/patch-surface.json'}
RELOCATED.update(DOCUMENT_CONTRACT_RELOCATIONS)

# Only these literal path prefixes may change inside these exact files.
PATH_REWRITES = {'.ci/branch-forwardports/run.py': (('.ci/branch-forwardports', 'ci/branch-forwardports'),),
 '.ci/maintained-build/test_contract.py': (('.ci/maintained-build', 'ci/maintained-build'),),
 '.github/workflows/maintained-entry-contract.yml': (('.ci/maintained-build',
                                                      'ci/maintained-build'),),
 '.github/workflows/maintained-packaging-contract.yml': (('.ci/validation-history',
                                                          'ci/validation-history'),),
 '.github/workflows/validation-history-contract.yml': (('.ci/maintained-build',
                                                        'ci/maintained-build'),
                                                       ('.ci/validation-history',
                                                        'ci/validation-history'))}
BASELINE_SHA256 = {'.ci/branch-cleanup/allowlist.json': '3a14284da05826e1f3f64724e5a57ec8324cbc9998f072a680171582f5a95cdb',
 '.ci/branch-cleanup/classify.py': 'a2689be21aa30c4b2d1766631cd93d68269f1dc3dc69aca99159dbb63ebb277a',
 '.ci/branch-cleanup/cleanup.py': 'abe214580455a1ec31e2009222e258597eb2c476b262893fdb4e64f0f168112d',
 '.ci/branch-cleanup/inventory.json': '450891d9f2649e5244368f536489fc66df5d56316bed930bcfdb275e80da57b7',
 '.ci/branch-cleanup/test_classify.py': '5a2f92592e2a7c84b12bcce7e70c83a996fd88812a3ce808a4dc9460343614bf',
 '.ci/branch-cleanup/test_cleanup.py': '844a97604ef0d5dc4cd2ec8104319edcb343df77e338a30ef2d2f735fed0cda0',
 '.ci/branch-forwardports/inputs.json': 'e93c894e7581071ccadf03e790aa16560b98be82b939499858482c60e70e8245',
 '.ci/branch-forwardports/run.py': '98ee029b37b2b3143563758db411c17033e3a820e46946ab1b8e5628ddbbf316',
 '.ci/branch-semantic-review/README.md': '8ec019e73b7d4f1da4b2ff18d7f096dc6f7246b536f5e7444fd954ed27ac1a41',
 '.ci/branch-semantic-review/inventory.json': '1a9f1da6d5aefa53fc1af8c380ca9119de8833c9259e8a4235a8e2666503c6a4',
 '.ci/branch-semantic-review/inventory.py': 'f2090a4bffcaee1910c5ed26360a165be2cb6c4509676a6316fb472152d460f8',
 '.ci/branch-semantic-review/source-receipt.json': 'ac3e56abe8c17bb190c748e967050dc9c871484625f36c4274672dc2c13b7752',
 '.ci/branch-semantic-review/test_inventory.py': '2f0fee4248c55a7c51a4dd50c64eb0121f67506edce8106c90547f619fc41c33',
 '.ci/maintained-build/contract.json': '4d1f64ff03816b192ff9f6dd2edb55b1bd55c2ab6e1cd464e1aeac5dbdd39636',
 '.ci/maintained-build/test_contract.py': 'f328c2d1f980cb3671d0009fdfb14e5a52c055de8dddf9870a2aea29ee94b4b9',
 '.ci/release-notes-v6.0.0-20261024/RELEASE_NOTES_v6.0.0-amended.md': '5e58d105be58ff815e67ecfa9fcfdba40d8d495447436772cebfb2fa890ccfa9',
 '.ci/release-notes-v6.0.0-20261024/amend.py': 'de0fbf2dc117f78771bcb98b610e9935e935547f442e39587b6912b482a499a0',
 '.ci/release-notes-v6.0.0-20261024/amendment-receipt.json': 'd656166acda3f61fc795dd55f0f246e3c46963f7a4bf1b37ee1cfc126adfdfe6',
 '.ci/release-notes-v6.0.0-20261024/test_amend.py': '7c8d5a2de1c2a8d8ce557475de1e56c7f1fd3477c3c3ba212887442fe477abd7',
 '.ci/release-v6.0.0/RELEASE_NOTES_v6.0.0.md': '6800c82893f41fa6d54c727a351f551490f27d601d64e50be4c9247164091f69',
 '.ci/release-v6.0.0/SHA256SUMS': '10f9dada8ac061262796b91c552e76896974075987513ab940fee12735e1a5bb',
 '.ci/release-v6.0.0/promote.py': 'eb3f6d1a05e692ca403184d4112645ef589edbffa609a258dcc822531a4e1e7f',
 '.ci/release-v6.0.0/release-provenance.json': '0a3a7b99449baeba95fd40adfc0a3f70b782f651114d4a593d406816b01139aa',
 '.ci/release-v6.0.0/reviewed-inputs.json': '9d5b77f88f4fa3d45d7998a7b87a569eab021d8c78dff7a3fbbe733bf74ceb71',
 '.ci/release-v6.0.0/test_promote.py': 'f33426436fc956c9b1efad89320695fe46e6beb7a777651e33e86c7b4960cb00',
 '.github/workflows/amend-v6.0.0-notes.yml': '5de9af586ae151951bc4e39d345c89a1ac5b6ec367339f010f249b640dffe4d5',
 '.github/workflows/maintained-entry-contract.yml': 'e315b7d22899fbe405adfabc9a8b2c74154e6e014c212b81f78ceb5709b0660d',
 '.github/workflows/maintained-packaging-contract.yml': 'bb29bc138201636dd8aa0ee2e3bfabe23fb9a85c35a05bc42568004ea86f51cb',
 '.github/workflows/publish-v6.0.0.yml': '4257b0c3266c6a15fef5dc2b130e851cf89520b7ecae218c00d5fe2eb0bdff5e',
 '.github/workflows/reviewed-branch-classify.yml': '94dfaa31e62d6dd12e4cd54ac572e0349a6ac8a0d9acb35a30bacf49e580967d',
 '.github/workflows/reviewed-branch-cleanup.yml': 'ce75fd09359d306f5fff19d276b5c37a038c88465c517faa0820c2d13f2b1c9d',
 '.github/workflows/reviewed-branch-forwardports.yml': 'aad590861355b2352c4f5582f727c767cb102882827b751616e05df9cc9e83ad',
 '.github/workflows/reviewed-branch-semantic-inventory.yml': 'a36160b16f19e3c1591b1b4994e030db1418c2857ddd65650bb846e9b1c58efa',
 '.github/workflows/validation-history-contract.yml': 'c22774f05b7408f27e8c8fbbcac7de99be12e7330f66e38356e5e9838b918b5a'}


# Exact path-only edits for the four relocated machine contracts and their callers.
for _name, _edits in {'.github/workflows/custom-integration-contract.yml': (('docs/PACKAGING_PATCH_SURFACE.json',
                                                        'ci/packaging/patch-surface.json'),
                                                       ('docs/CUSTOM_INTEGRATION*',
                                                        'ci/issue52-integration/contracts.json')),
 '.github/workflows/maintained-packaging-contract.yml': (('docs/PACKAGING_PATCH_SURFACE.json',
                                                          'ci/packaging/patch-surface.json'),),
 'ci/issue52-integration/verify_inventory.py': (('docs/CUSTOM_INTEGRATION_CONTRACTS.json',
                                                 'ci/issue52-integration/contracts.json'),),
 'docs/CUSTOM_INTEGRATION_CONTRACTS.json': (('docs/CUSTOM_INTEGRATION_CONTRACTS.json',
                                             'ci/issue52-integration/contracts.json'),),
 'scripts/integration_contracts.py': (('docs/CUSTOM_INTEGRATION_CONTRACTS.json',
                                       'ci/issue52-integration/contracts.json'),),
 'tests/test_packaging_adapters.py': (('docs/PACKAGING_PATCH_SURFACE.json',
                                       'ci/packaging/patch-surface.json'),),
 'tests/test_v3_diagnostic_codes.py': (('docs/ERROR_CODES_V1.json',
                                        'ci/diagnostics/error-codes-v1.json'),
                                       ('docs/ERROR_PRESENTATION_COVERAGE.json',
                                        'ci/diagnostics/presentation-coverage.json'))}.items():
    PATH_REWRITES[_name] = PATH_REWRITES.get(_name, ()) + _edits
BASELINE_SHA256.update({'.github/workflows/custom-integration-contract.yml': '4ecfdb563c8fdc3aa37791dab9b3eff5d34ab9d9fa12a479d71f6a48898b8b05',
 '.github/workflows/maintained-packaging-contract.yml': 'bb29bc138201636dd8aa0ee2e3bfabe23fb9a85c35a05bc42568004ea86f51cb',
 'ci/issue52-integration/verify_inventory.py': '4b02f6d5a9e335c26b6bb1bbc9a9a165d2c79647b0331ad0abf174d4b92536a7',
 'docs/CUSTOM_INTEGRATION_CONTRACTS.json': '708b7083309e447c40e5d008ee84ec80e3146a91c06567c0b9212119247491ea',
 'docs/ERROR_CODES_V1.json': 'fbc6ee596461b35bd9afad8e1151f53f9590072fa3be4c9bfbd9676005dddec4',
 'docs/ERROR_PRESENTATION_COVERAGE.json': 'ef5c5ed2ace50055fd9517666e69126ae428e48ab589bb41332681d3140f40fb',
 'docs/PACKAGING_PATCH_SURFACE.json': '8a536f818ba517c54b97ecd3f61f4cfa5be718cdca549e29f0cdbd01f9bb95c8',
 'scripts/integration_contracts.py': '290c2b0443f10f0f5e2b6fe8572630eda4ad32130fdb8094c6652c360da1bcc6',
 'tests/test_packaging_adapters.py': '59432d38a84f6dddd234968241992b79299949c88a6542409121c23a5c55db34',
 'tests/test_v3_diagnostic_codes.py': '86a84105f2793651fd1cc48e2709f03daf6c9b423f99bd4780963d72586d2ffa'})

# Update only documentation pointers in retained application sources.
PATH_REWRITES.update({'.github/workflows/build-current.yml': (('docs/MAINTAINED_BUILD.md', 'ci/README.md'),),
 '.github/workflows/livecontainer-build.yml': (('docs/MAINTAINED_BUILD.md', 'ci/README.md'),),
 'scripts/templates/combined_failure.swift': (('docs/ERROR_CODES_V1.json',
                                               'ci/diagnostics/error-codes-v1.json'),)})
BASELINE_SHA256.update({'.github/workflows/build-current.yml': 'ad49b58782140ca91902606632592207f334d8692df0afa505671030448900d5',
 '.github/workflows/livecontainer-build.yml': '31ad8b71e32415865ac47d5a9edf44954c03a3726486b1ffa113174cec1f1d7f',
 'scripts/templates/combined_failure.swift': '91eb30bdefee35c0dc9b36ae7d64257a6b8c99dd7c7943997ad6a4c520a78287'})

# Also verify callers outside the frozen historical protected-source inventory.
DOCUMENT_CONTRACT_CALLERS = {
    'scripts/integration_contracts.py',
    'ci/issue52-integration/verify_inventory.py',
    'tests/test_packaging_adapters.py',
    'tests/test_v3_diagnostic_codes.py',
    '.github/workflows/maintained-packaging-contract.yml',
    '.github/workflows/custom-integration-contract.yml',
    '.github/workflows/build-current.yml',
    '.github/workflows/livecontainer-build.yml',
    'scripts/templates/combined_failure.swift',
}

# Exact CI job organization, separate from the path-only relocation edits.
# The immutable process-group tests retain their original Linux execution platform.
LINUX_SEMANTIC_JOB = """  historical-semantic:
    name: Frozen semantic contracts (Linux)
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          fetch-depth: 0
          persist-credentials: false
      - name: Run all provenance and immutable historical semantic tests
        run: python3 -B -m unittest discover -s ci/validation-history -p 'test_*.py' -v
      - name: Verify historical ancestry and production source boundaries
        run: python3 -B ci/validation-history/verify.py
"""

WORKFLOW_LAYOUT_EDITS = {
    '.github/workflows/maintained-packaging-contract.yml': ((
        'jobs:\n  packaging-contract:\n',
        'jobs:\n' + LINUX_SEMANTIC_JOB +
        '  packaging-contract:\n    needs: historical-semantic\n',
    ),),
    '.github/workflows/validation-history-contract.yml': ((
        'jobs:\n  bounded-contracts:\n',
        'jobs:\n' + LINUX_SEMANTIC_JOB +
        '  bounded-contracts:\n    needs: historical-semantic\n',
    ),),
}


# Exact entry-only change: all native and same-run packaging steps remain.
BASELINE_SHA256[".github/workflows/cold-vpn-native.yml"] = "cde76b1f24206541e4ef59edffafca5182d0a796c59e5c1e16a03b459d3a5ddb"
WORKFLOW_LAYOUT_EDITS[".github/workflows/cold-vpn-native.yml"] = (("name: Cold VPN stabilization native qualification\non:\n  workflow_dispatch:\n  push:\n    branches: ['validation/cold-vpn']\n    paths: ['ci/refresh-direct-native/**', 'ci/refresh-truth/**', 'ci/startup-upgrade/**', 'ci/cold-vpn/**', 'ci/multitask-launch/**', 'ci/language-switch/**', 'ci/issue56-startup/**', 'ci/issues-native/**', '.github/workflows/cold-vpn-native.yml', 'scripts/*strip*.py', 'scripts/macho_stab_guard.py', 'scripts/macho_local_symbol_guard.py', 'scripts/experiment_local_symbols.py', 'scripts/local_symbol_packaging.py', 'scripts/optimize_native_candidate.py', 'tests/test_*strip*.py', 'tests/test_macho_stab_guard.py', 'tests/test_macho_local_symbol_guard.py', 'tests/test_experiment_local_symbols.py', 'tests/test_local_symbol_packaging.py', 'tests/test_native_size_optimization.py']\npermissions:\n  contents: read\nconcurrency:\n  group: cold-vpn-native-${{ github.ref }}\n  cancel-in-progress: false\njobs:\n  compile:\n    if: github.repository == 'NRG-Wardog/sidestore-auto-refresh' && github.ref == 'refs/heads/validation/cold-vpn'\n    runs-on: macos-26\n    timeout-minutes: 100\n    env:\n      DEVELOPER_DIR: /Applications/Xcode_26.4.1.app/Contents/Developer\n      RUSTUP_TOOLCHAIN: '1.98.1'\n      GIT_TERMINAL_PROMPT: '0'\n      GIT_CONFIG_NOSYSTEM: '1'\n      GIT_CONFIG_GLOBAL: /dev/null\n      GIT_ALLOW_PROTOCOL: https\n      GIT_NO_REPLACE_OBJECTS: '1'\n      PYTHONDONTWRITEBYTECODE: '1'\n    defaults:\n      run:\n        shell: bash\n", "name: Build and package pinned maintained graph (6.0.1 basis)\non:\n  workflow_dispatch:\npermissions:\n  contents: read\nconcurrency:\n  group: maintained-native-package-${{ github.ref }}\n  cancel-in-progress: false\njobs:\n  compile:\n    if: github.repository == 'NRG-Wardog/sidestore-auto-refresh' && github.event_name == 'workflow_dispatch'\n    runs-on: macos-26\n    timeout-minutes: 100\n    env:\n      DEVELOPER_DIR: /Applications/Xcode_26.4.1.app/Contents/Developer\n      RUSTUP_TOOLCHAIN: '1.98.1'\n      GIT_TERMINAL_PROMPT: '0'\n      GIT_CONFIG_NOSYSTEM: '1'\n      GIT_CONFIG_GLOBAL: /dev/null\n      GIT_ALLOW_PROTOCOL: https\n      GIT_NO_REPLACE_OBJECTS: '1'\n      PYTHONDONTWRITEBYTECODE: '1'\n    defaults:\n      run:\n        shell: bash\n"), ('      - name: Check sync guards with source-owned synthetic fixtures only\n', '      - name: Identify the exact maintained source graph before building\n        run: |\n          set -euo pipefail\n          python3 -B harness/ci/current-build/source_selection.py --graph harness/ci/issues-native/candidate-graph.json --output evidence/current-source-selection.json --summary "$GITHUB_STEP_SUMMARY"\n      - name: Check sync guards with source-owned synthetic fixtures only\n'), ('python3 harness/ci/issues-native/record.py receipt\n', 'python3 harness/ci/issues-native/record.py receipt-current\n'))

# Newly reviewed read-only workflows, outside the frozen historical manifests.
REVIEWED_ADDITIONAL_WORKFLOWS = {
    ".github/workflows/current-build-contract.yml": "c901c05efba7a82e6b21db8143521a172ae61062813a48703fd4602f65004ad5",
    '.github/workflows/documentation-contract.yml': '396364eca5bb7bccd73fc24b2b7a16d89ce1d3d7bf0b45abae7d365be99c1e1a',
}

ARCHIVED_WORKFLOWS = {'amend-v6.0.0-notes.yml',
 'publish-v6.0.0.yml',
 'reviewed-branch-classify.yml',
 'reviewed-branch-cleanup.yml',
 'reviewed-branch-forwardports.yml',
 'reviewed-branch-semantic-inventory.yml'}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def read(path):
    require(path.is_file() and not path.is_symlink(), 'Missing or linked relocated source: '+str(path))
    require(not any(p.is_symlink() for p in path.parents), 'Linked relocation directory')
    return path.read_bytes()

def verify_current(root, name, expected, object_read):
    """Compare exact baseline objects, then apply only enumerated reviewed edits."""
    destination = RELOCATED.get(name, name)
    if name not in BASELINE_SHA256:
        require(hashlib.sha256(read(root/destination)).hexdigest() == expected,
                'Reviewed source bytes differ: '+name)
        return
    baseline = object_read(root, BASELINE, name)
    digest = hashlib.sha256(baseline).hexdigest()
    require(digest == BASELINE_SHA256[name] == expected, 'Relocation baseline differs: '+name)
    current = baseline
    for prefix, replacement in PATH_REWRITES.get(name, ()):
        require(prefix.encode() in current, 'Relocation prefix missing')
        current = current.replace(prefix.encode(), replacement.encode())
    for original, replacement in WORKFLOW_LAYOUT_EDITS.get(name, ()):
        require(current.count(original.encode()) == 1, 'Workflow layout anchor differs')
        current = current.replace(original.encode(), replacement.encode())
    require(read(root/destination) == current, 'Relocated source bytes differ: '+name)
    if destination != name:
        require(not (root/name).exists() and not (root/name).is_symlink(), 'Obsolete relocation source exists: '+name)

def verify_auxiliary(root, object_read):
    # This workflow was outside the original manifest. Bind it explicitly now.
    name = '.github/workflows/validation-history-contract.yml'
    verify_current(root, name, BASELINE_SHA256[name], object_read)
    for name in set(DOCUMENT_CONTRACT_RELOCATIONS) | DOCUMENT_CONTRACT_CALLERS:
        verify_current(root, name, BASELINE_SHA256[name], object_read)
    for name, expected in REVIEWED_ADDITIONAL_WORKFLOWS.items():
        require(hashlib.sha256(read(root/name)).hexdigest() == expected,
                'Additional reviewed workflow bytes differ: '+name)
    require(not (root/'.ci').exists() and not (root/'.ci').is_symlink(), 'Obsolete .ci directory exists')
