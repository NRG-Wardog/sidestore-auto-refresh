"""Read-only replay of the exact independently reviewed full-login captures.

Only this fixed run pair is supported. These are preservation/replay receipts,
not fresh native execution or generic acceptance of caller-selected evidence.
No captured Python, shell, fixture or binary is imported or executed. The only
subprocesses are read-only Git operations through prepare_candidate_inventory.
The public replay(directory, roots) returns (relative bytes mapping, summary),
and never writes, fetches, publishes, activates or grants release readiness.
"""
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from prepare_candidate_inventory import git, snapshot

INPUT_FILES = {'evidence/final-source-handoff.json': 'final-source-handoff.json',
 'evidence/phase-a/independent-artifact-api.json': 'capture-results/39210961502/independent-artifact-api.json',
 'evidence/phase-a/independent-commit-api.json': 'capture-results/39210961502/independent-commit-api.json',
 'evidence/phase-a/independent-descendant-clearance.json': 'capture-results/39210961502/independent-descendant-clearance.json',
 'evidence/phase-a/independent-review.json': 'capture-results/39210961502/independent-review.json',
 'evidence/phase-a/independent-run-api.json': 'capture-results/39210961502/independent-run-api.json',
 'evidence/phase-b/independent-artifact-api.json': 'capture-results/39229204851/independent-artifact-api.json',
 'evidence/phase-b/independent-cargo.json': 'capture-results/39229204851/independent-cargo.json',
 'evidence/phase-b/independent-command-source-review.json': 'capture-results/39229204851/independent-command-source-review.json',
 'evidence/phase-b/independent-commit-api.json': 'capture-results/39229204851/independent-commit-api.json',
 'evidence/phase-b/independent-idevice-commit-api.json': 'capture-results/39229204851/independent-idevice-commit-api.json',
 'evidence/phase-b/independent-idevice-tree-api.json': 'capture-results/39229204851/independent-idevice-tree-api.json',
 'evidence/phase-b/independent-jktcp-commit-api.json': 'capture-results/39229204851/independent-jktcp-commit-api.json',
 'evidence/phase-b/independent-jktcp-tree-api.json': 'capture-results/39229204851/independent-jktcp-tree-api.json',
 'evidence/phase-b/independent-keychainaccess-commit-api.json': 'capture-results/39229204851/independent-keychainaccess-commit-api.json',
 'evidence/phase-b/independent-products.json': 'capture-results/39229204851/independent-products.json',
 'evidence/phase-b/independent-review.json': 'capture-results/39229204851/independent-review.json',
 'evidence/phase-b/independent-run-api.json': 'capture-results/39229204851/independent-run-api.json',
 'evidence/phase-b/independent-semanticversion-commit-api.json': 'capture-results/39229204851/independent-semanticversion-commit-api.json',
 'evidence/phase-b/independent-zipfoundation-commit-api.json': 'capture-results/39229204851/independent-zipfoundation-commit-api.json',
 'evidence/publication/independent-lock-tree-review.json': 'lock-child-proposal-39229204851/independent-lock-tree-review.json',
 'evidence/publication/independent-publication-clearance.json': 'lock-child-proposal-39229204851/independent-publication-clearance.json',
 'evidence/publication/independent-published-commit-api.json': 'lock-child-proposal-39229204851/independent-published-commit-api.json',
 'evidence/publication/independent-published-ref-api.json': 'lock-child-proposal-39229204851/independent-published-ref-api.json',
 'evidence/source/publication-trees.json': 'phase-b-retry5-candidate/ci/p0loginb/publication-trees.json',
 'evidence/source/source-seal.json': 'phase-b-retry5-candidate/ci/p0loginb/source-seal.json'}

ARCHIVE_OUTPUTS = {'evidence/phase-a/evidence-index.json': ('phase-a', 'evidence-index.json'),
 'evidence/phase-a/provenance/host-overlay.json': ('phase-a', 'provenance/host-overlay.json'),
 'evidence/phase-a/provenance/owned-command-groups.json': ('phase-a', 'provenance/owned-command-groups.json'),
 'evidence/phase-a/provenance/result.json': ('phase-a', 'provenance/result.json'),
 'evidence/phase-a/provenance/reviewed-inputs.json': ('phase-a', 'provenance/reviewed-inputs.json'),
 'evidence/phase-a/provenance/source-after.json': ('phase-a', 'provenance/source-after.json'),
 'evidence/phase-a/provenance/source-before.json': ('phase-a', 'provenance/source-before.json'),
 'evidence/phase-b/evidence-index.json': ('phase-b', 'evidence-index.json'),
 'evidence/phase-b/provenance/app-resolved-after-build.json': ('phase-b',
                                                               'provenance/app-resolved-after-build.json'),
 'evidence/phase-b/provenance/app-resolved-before-build.json': ('phase-b',
                                                                'provenance/app-resolved-before-build.json'),
 'evidence/phase-b/provenance/host-overlay.json': ('phase-b', 'provenance/host-overlay.json'),
 'evidence/phase-b/provenance/owned-command-groups.json': ('phase-b', 'provenance/owned-command-groups.json'),
 'evidence/phase-b/provenance/prior-phase-a.json': ('phase-b', 'provenance/prior-phase-a.json'),
 'evidence/phase-b/provenance/result.json': ('phase-b', 'provenance/result.json'),
 'evidence/phase-b/provenance/reviewed-inputs.json': ('phase-b', 'provenance/reviewed-inputs.json'),
 'evidence/phase-b/provenance/self-contained-tests.json': ('phase-b', 'provenance/self-contained-tests.json'),
 'evidence/phase-b/provenance/source-after.json': ('phase-b', 'provenance/source-after.json'),
 'evidence/phase-b/provenance/source-before.json': ('phase-b', 'provenance/source-before.json'),
 'evidence/phase-b/provenance/source-object-equivalence.json': ('phase-b',
                                                                'provenance/source-object-equivalence.json'),
 'resolver-locks/SideSign.Package.resolved.after': ('phase-a', 'provenance/SideSign.Package.resolved.after'),
 'resolver-locks/SideSign.Package.resolved.before': ('phase-a',
                                                     'provenance/SideSign.Package.resolved.before'),
 'resolver-locks/SideSign.Package.resolved.final-observed': ('phase-a',
                                                             'provenance/SideSign.Package.resolved.final-observed'),
 'resolver-locks/SideStore.Package.resolved.after': ('phase-b',
                                                     'provenance/SideStore.Package.resolved.after'),
 'resolver-locks/SideStore.Package.resolved.before': ('phase-b',
                                                      'provenance/SideStore.Package.resolved.before'),
 'resolver-locks/SideStore.Package.resolved.final-observed': ('phase-b',
                                                              'provenance/SideStore.Package.resolved.final-observed')}

FIXED_FILE_HASHES = {'evidence/final-source-handoff.json': '776b0813e84a6cd7202960e8ff92fb6e79b69167f4405250b01d8d678722ab08',
 'evidence/phase-a/evidence-index.json': '7d8c95b751b27341a116638d2012ced11a6414781f81c591aa7e1a45bedbc8df',
 'evidence/phase-a/independent-artifact-api.json': 'd53718478eadf9ccc88646a87b14163cf750087c899d71194817b5cad61b32c1',
 'evidence/phase-a/independent-commit-api.json': 'e1a349b0e01154eb1376a83c798297df2a0f8a8f955cc76ab5d043b4b5c9e01e',
 'evidence/phase-a/independent-descendant-clearance.json': 'b99b1f60ad549bf0ebe9d7a48e16e25e83e5d09f95fc76ec5b4d4b8debe62444',
 'evidence/phase-a/independent-review.json': '0ad45e272a4e0f74551c7869a4db3d24b28964ed10c16cc01c23d2508ac72e57',
 'evidence/phase-a/independent-run-api.json': '0a64ef7851ea886eb91c195c9179e89dc877f54aa08c4c5a95a9269e0cb6302e',
 'evidence/phase-a/provenance/host-overlay.json': 'fb800ce256f07a5c39b096641ed32aaec4b04a08cac39ce81fc2d5ea4240a820',
 'evidence/phase-a/provenance/owned-command-groups.json': '19dd92c8d0a9d03689828630bcd2d23b4a02bbe2605a6ff43632944b064abd54',
 'evidence/phase-a/provenance/result.json': '4741e876635086818b48deda7c1f83fdacec8924ee1f6cd30f749c472909823c',
 'evidence/phase-a/provenance/reviewed-inputs.json': '4b765084e34ee1a88d35af4553327f131983d7002c0c6b6ae54acf976a171cfa',
 'evidence/phase-a/provenance/source-after.json': 'f00097bd1bbcb5ffd96d9caf72b2fd134e21e7de2099f7c32d6e6d2126d0a16d',
 'evidence/phase-a/provenance/source-before.json': '3bcc50195889171b0cc66b2386e7542ced5f3eea0eed32ac637dfa6925649a31',
 'evidence/phase-b/evidence-index.json': 'fe9208a39c72864e8dfe213e8fafa005420dcc9fafa10d8e6d46c7bb973a0af3',
 'evidence/phase-b/independent-artifact-api.json': '7f06a28744571eb4a65cedbfe5695a21d706e5c9d2a229ee1e83b7b69736f6c6',
 'evidence/phase-b/independent-cargo.json': 'a50194418bfe937771c494b6f77452510749adff316347272fc1f4e9e1c3c734',
 'evidence/phase-b/independent-command-source-review.json': '62b857a452b0a96ef0c44a2d27ca3f5ff8e74d3d806db1ae8bbfd215eae7060c',
 'evidence/phase-b/independent-commit-api.json': '3497afb3f4cfd2220df5c8e22d85a04ee65d279170547e3ef5c305f584def5c5',
 'evidence/phase-b/independent-idevice-commit-api.json': '92c7e57d32da20eac7a49c4649c380349097c826c824394d5f1d877c7a76fdad',
 'evidence/phase-b/independent-idevice-tree-api.json': '0aa8d0cf13e5772051e61129b93b2721e017de88716a2839e193f03dc106d112',
 'evidence/phase-b/independent-jktcp-commit-api.json': 'ecb5c52d4c337f4af7d51d0c5005cc56bcb47d7d489506dfecab8c300f2e921d',
 'evidence/phase-b/independent-jktcp-tree-api.json': '6367bcf924c6f62b4455f56e726cceea98bbe0fc792b72cae6661aee5d62b5eb',
 'evidence/phase-b/independent-keychainaccess-commit-api.json': '24ee9aa34e1d284c4cb5eb89123e635defa73a2ebccebd38a01570e1425e430b',
 'evidence/phase-b/independent-products.json': '13f1139d3fc1327de2bd307bdfb52e5f29ba71a7358a007bf0c745b503e782f5',
 'evidence/phase-b/independent-review.json': '6e32d813acd065586a0841ca3c3f241b41c9f338085b55ae04ad65ee63e7c2cd',
 'evidence/phase-b/independent-run-api.json': 'ec49256d5238ad0c4ffa0cb5bfe4e5fbe9f7f9b4eab48e85e47642642545592f',
 'evidence/phase-b/independent-semanticversion-commit-api.json': '05312c6ef5505747fc5bd9525bd98893debb22efa01a7933c9e036914c7a36b2',
 'evidence/phase-b/independent-zipfoundation-commit-api.json': '042774e3e32e3743597e942c96234f12a761882d6f76d7e1e51c0fa745193653',
 'evidence/phase-b/provenance/app-resolved-after-build.json': 'd7def3653d03e375b04096317165371c8a36dfc5fc1d089eaa30fafdd5b59104',
 'evidence/phase-b/provenance/app-resolved-before-build.json': 'd7def3653d03e375b04096317165371c8a36dfc5fc1d089eaa30fafdd5b59104',
 'evidence/phase-b/provenance/host-overlay.json': '271d7112c674af4024a83979ff75d107f0d1726b7a2a310275868cccbbf235a3',
 'evidence/phase-b/provenance/owned-command-groups.json': '9006a5690b158364aafd03788c8f03ad9e7fc43309cf56e515d4ad934b359c2e',
 'evidence/phase-b/provenance/prior-phase-a.json': 'c2e3152f3587481c48d219d9e34e5286d735420b7736925dbe41aa9e482a5d78',
 'evidence/phase-b/provenance/result.json': '3c6d45cc123f2364e5b3250d2cf54d38cf9fb73e01d4a25356a85d9c4e9bff8e',
 'evidence/phase-b/provenance/reviewed-inputs.json': '70a315ebe5aa6ed0505346794a17ecf96e0ab93cc31ada2a348c539aa96403ba',
 'evidence/phase-b/provenance/self-contained-tests.json': '86d5a4fbc4b1c3c79fe2441a5c34224c94cf384e10d29e37afb74ae55dade180',
 'evidence/phase-b/provenance/source-after.json': '81a33cf95066cac9b4a8e15540e74229765a6b534680914433a84158265e24a6',
 'evidence/phase-b/provenance/source-before.json': '81a33cf95066cac9b4a8e15540e74229765a6b534680914433a84158265e24a6',
 'evidence/phase-b/provenance/source-object-equivalence.json': 'c56d5dd63c405dc3767580d6a258162f9b7676cf241dc198c9bb6fa62510290a',
 'evidence/publication/independent-lock-tree-review.json': '16dc41befe8de07afeafb1a1ca030e86ea667506b1208e069adba7d220dae16b',
 'evidence/publication/independent-publication-clearance.json': '82dbc29a45eb998cbc27f041adbac74f07866aa41a99987a59b67a4833ba92f1',
 'evidence/publication/independent-published-commit-api.json': 'db8ae38ba818156cbb367344dcdb4c72b5c168e242698908586e089740b2efbf',
 'evidence/publication/independent-published-ref-api.json': '89c953356d3a8803fb19573aac8151a54a9fdcd30b804bd68023591c2b821a5d',
 'evidence/source/publication-trees.json': '9c0da8c5be4eb6ebac50a006225e371ed9a1a3008e5e79dd4e30d71a906eba5a',
 'evidence/source/source-seal.json': '0caa11b1135abbf9c3905c601d100475b91f8d011237033ab47d1f96fad116e8',
 'resolver-locks/SideSign.Package.resolved.after': 'cdfe27d752edde2e836cd0c25166139223e233ba1c18aa6df69ba893d0a07c35',
 'resolver-locks/SideSign.Package.resolved.before': '560c36d844b162b942175385490bce7368015a75164c1f4ea6229cdf6339a961',
 'resolver-locks/SideSign.Package.resolved.final-observed': 'cdfe27d752edde2e836cd0c25166139223e233ba1c18aa6df69ba893d0a07c35',
 'resolver-locks/SideStore.Package.resolved.after': 'e97604f913903659539663f09176a20ee9ca5fe06188a41ff38f54257d076d08',
 'resolver-locks/SideStore.Package.resolved.before': 'b3dc185eaf06254e079170681b0ea528b95d8355513e296e60aa0e469c341764',
 'resolver-locks/SideStore.Package.resolved.final-observed': 'e97604f913903659539663f09176a20ee9ca5fe06188a41ff38f54257d076d08'}

PHASES = {'phase-a': {'artifact_bytes': 557731,
             'artifact_id': 12167887288,
             'artifact_sha256': '1ba62e133de91b9cb3794338157126cbaf39fd60a7b23fb30753825234338c80',
             'commands': 67,
             'evidence_index_sha256': '7d8c95b751b27341a116638d2012ced11a6414781f81c591aa7e1a45bedbc8df',
             'host_commit': '3ec28a81d8ff8f0f736e775c676d733bc5b61be8',
             'host_tree': 'f1bb7314c08bd0ff3cd707258244255ddc8197dd',
             'indexed_files': 799,
             'input_sha256': '4b765084e34ee1a88d35af4553327f131983d7002c0c6b6ae54acf976a171cfa',
             'review_sha256': '0ad45e272a4e0f74551c7869a4db3d24b28964ed10c16cc01c23d2508ac72e57',
             'run_attempt': 1,
             'run_id': 39210961502},
 'phase-b': {'artifact_bytes': 47313261,
             'artifact_id': 12178055541,
             'artifact_sha256': 'b2c931dccffaa7232e8818e674267013e083af2ecb8ff47d256414b6bbc77c93',
             'commands': 70,
             'evidence_index_sha256': 'fe9208a39c72864e8dfe213e8fafa005420dcc9fafa10d8e6d46c7bb973a0af3',
             'host_commit': 'dd8fff072238af26bcee00ae17881f9f15d3ec2c',
             'host_tree': '5b7e6b28c14cd6c2aac54c32ed2ccc5ae6fff5cd',
             'indexed_files': 406,
             'input_sha256': '70a315ebe5aa6ed0505346794a17ecf96e0ab93cc31ada2a348c539aa96403ba',
             'review_sha256': '6e32d813acd065586a0841ca3c3f241b41c9f338085b55ae04ad65ee63e7c2cd',
             'run_attempt': 1,
             'run_id': 39229204851}}

EXPECTED_SUMMARY = {'artifact_execution_performed': False,
 'compiled_final_commit': False,
 'device_login_proven': False,
 'final_sidestore_equivalence': {'changes': {'AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved': {'after': {'git_object': '3867aab14eaa661b8c9c526be9c473cb645a609c',
                                                                                                                                        'mode': '100644',
                                                                                                                                        'sha256': 'e97604f913903659539663f09176a20ee9ca5fe06188a41ff38f54257d076d08',
                                                                                                                                        'type': 'blob'},
                                                                                                                              'before': {'git_object': 'f8e64c3570ddd856da53c4741bd8afdcdd255a91',
                                                                                                                                         'mode': '100644',
                                                                                                                                         'sha256': 'b3dc185eaf06254e079170681b0ea528b95d8355513e296e60aa0e469c341764',
                                                                                                                                         'type': 'blob'}}},
                                 'compiled_final_commit': False,
                                 'equivalence': 'all tracked inputs identical except exact observed app lock',
                                 'final_commit': 'd507f9e417f12c262b4d7e717c84d4a244c8e625',
                                 'final_tree': '829d5e86ae2365b2870d268e77735f11f16944fb',
                                 'lock_sha256': 'e97604f913903659539663f09176a20ee9ca5fe06188a41ff38f54257d076d08',
                                 'tested_commit': 'dc29ce0d79a299f32a340c460e1d61e8190e320c'},
 'final_source_owners': {'AnisetteKit': {'commit': 'e6e9d4aac698de2a5219aaef02c9a417da45f4e9',
                                         'repository': 'https://github.com/NRG-Wardog/AnisetteKit.git',
                                         'tree': '734664d759b9e99caa09089905e6a240b149548d'},
                         'LiveContainer': {'commit': '545438e1f0a89fdef375f73273c5c9fc2735cbce',
                                           'repository': 'https://github.com/NRG-Wardog/LiveContainer.git',
                                           'tree': 'e8014dd771c25c4a05dc76ab0aa304a233642a83'},
                         'SideSign': {'commit': 'c733332b0d64022cd603ab9ac5de054413729aab',
                                      'repository': 'https://github.com/NRG-Wardog/SideSign.git',
                                      'tree': '6559fa8c226ea7496e1fb1aa813c058bfee3e342'},
                         'SideStore': {'commit': 'd507f9e417f12c262b4d7e717c84d4a244c8e625',
                                       'repository': 'https://github.com/NRG-Wardog/SideStore.git',
                                       'tree': '829d5e86ae2365b2870d268e77735f11f16944fb'},
                         'idevice': {'commit': '803f9368e6b27a43d8a9f22dc0ec281c87883113',
                                     'repository': 'https://github.com/NRG-Wardog/idevice.git',
                                     'tree': 'f5ac11668bae97e577e7c4fc098b5484df1c9af1'},
                         'jktcp': {'commit': '32c0210f286b4fd16efe05be4f57167a9d94459b',
                                   'repository': 'https://github.com/NRG-Wardog/jktcp.git',
                                   'tree': 'e94eaefdcae5fe54a535186e36a0b2ebccc9bf13'},
                         'minimuxer': {'commit': 'efbcab05d7d636aa37c6bf6c7f364d122c5610f6',
                                       'repository': 'https://github.com/NRG-Wardog/minimuxer.git',
                                       'tree': '30aa947645c5befb0c3210700095c8b039893d83'}},
 'guest_execution': False,
 'initializer47_containment_integrated': False,
 'new_builder_admission_granted': False,
 'original_phase_b_review_sha256': '6e32d813acd065586a0841ca3c3f241b41c9f338085b55ae04ad65ee63e7c2cd',
 'original_publication_review_sha256': '82dbc29a45eb998cbc27f041adbac74f07866aa41a99987a59b67a4833ba92f1',
 'parser_only': True,
 'phase_a': {'artifact_bytes': 557731,
             'artifact_id': 12167887288,
             'artifact_sha256': '1ba62e133de91b9cb3794338157126cbaf39fd60a7b23fb30753825234338c80',
             'commands': 67,
             'evidence_index_sha256': '7d8c95b751b27341a116638d2012ced11a6414781f81c591aa7e1a45bedbc8df',
             'host_commit': '3ec28a81d8ff8f0f736e775c676d733bc5b61be8',
             'host_tree': 'f1bb7314c08bd0ff3cd707258244255ddc8197dd',
             'indexed_files': 799,
             'input_sha256': '4b765084e34ee1a88d35af4553327f131983d7002c0c6b6ae54acf976a171cfa',
             'review_sha256': '0ad45e272a4e0f74551c7869a4db3d24b28964ed10c16cc01c23d2508ac72e57',
             'run_attempt': 1,
             'run_id': 39210961502},
 'phase_a_scope_notes': ['Bound to this exact authenticated run/artifact only; not a generic admission '
                         'validator.',
                         'Temporary fixture source bytes are generated by the reviewed immutable test '
                         'drivers and are not independently retained in this artifact; command provenance '
                         'and source-driver identities are verified.',
                         'SideSign lock-only descendant may preserve these exact observed bytes after '
                         'independent Git tree comparison; Phase B execution requires separate review.'],
 'phase_b': {'artifact_bytes': 47313261,
             'artifact_id': 12178055541,
             'artifact_sha256': 'b2c931dccffaa7232e8818e674267013e083af2ecb8ff47d256414b6bbc77c93',
             'commands': 70,
             'evidence_index_sha256': 'fe9208a39c72864e8dfe213e8fafa005420dcc9fafa10d8e6d46c7bb973a0af3',
             'host_commit': 'dd8fff072238af26bcee00ae17881f9f15d3ec2c',
             'host_tree': '5b7e6b28c14cd6c2aac54c32ed2ccc5ae6fff5cd',
             'indexed_files': 406,
             'input_sha256': '70a315ebe5aa6ed0505346794a17ecf96e0ab93cc31ada2a348c539aa96403ba',
             'review_sha256': '6e32d813acd065586a0841ca3c3f241b41c9f338085b55ae04ad65ee63e7c2cd',
             'run_attempt': 1,
             'run_id': 39229204851},
 'production_ready': False,
 'release_eligible': False,
 'schema_version': 1,
 'scope_notes': ['This review binds only this exact authenticated capture and reviewed host/input tuple; it '
                 'is not a generic evidence validator.',
                 'Five owner source object graphs were replayed locally. idevice and jktcp complete '
                 'recursive tree metadata were independently authenticated and reconstructed; their actual '
                 'checkout-byte verification ran in the authenticated reviewed CI adapter.',
                 'All dependency source, registry, tool, transport and new product bytes were checked by the '
                 'reviewed CI code, but most raw bytes are not retained. Local replay verifies retained '
                 'records and complete cross-bindings, not a second build or independent re-hash of absent '
                 'bytes.',
                 'Retained official SideStore OpenSSL ZIP and generated plist_ffi header received '
                 'independent raw-byte checks. Preexisting signatures are identified, not cryptographically '
                 'authenticated.',
                 'The precise SideStore app-lock-only child may be published after separate full-tree '
                 'equivalence review and authenticated commit readback. The future child was not directly '
                 'compiled.',
                 'Fresh builder admission and normal release eligibility remain separate. This does not '
                 'prove actual-device initializer or login success.'],
 'source_children': {'LiveContainer/OpenSSL': {'commit': '623c84da314e85363236507ca38a4bde65df21c3',
                                               'declared_repository': 'https://github.com/krzyzanowskim/OpenSSL',
                                               'repository': 'https://github.com/krzyzanowskim/OpenSSL',
                                               'tree': '21bc09387e7f91a46eb8bd26587ce1d052277801'},
                     'LiveContainer/litehook': {'commit': '8025e0c8ebdf5cdd1d2a4f45025813234bf9dc55',
                                                'declared_repository': 'https://github.com/LiveContainer/litehook',
                                                'repository': 'https://github.com/LiveContainerMirror/litehook.git',
                                                'tree': '62bc06a17a1c12d805aec1ada25910bfa9f89781'},
                     'SideStore/Dependencies/SideSign': {'commit': 'c733332b0d64022cd603ab9ac5de054413729aab',
                                                         'declared_repository': 'https://github.com/NRG-Wardog/SideSign.git',
                                                         'repository': 'https://github.com/NRG-Wardog/SideSign.git',
                                                         'tree': '6559fa8c226ea7496e1fb1aa813c058bfee3e342'},
                     'SideStore/Dependencies/minimuxer': {'commit': 'efbcab05d7d636aa37c6bf6c7f364d122c5610f6',
                                                          'declared_repository': 'https://github.com/NRG-Wardog/minimuxer.git',
                                                          'repository': 'https://github.com/NRG-Wardog/minimuxer.git',
                                                          'tree': '30aa947645c5befb0c3210700095c8b039893d83'},
                     'idevice/cpp/plist_ffi': {'commit': '66e6e6362b58bc361725c3e1b0a34af498aa2b3a',
                                               'declared_repository': 'https://github.com/jkcoxson/plist_ffi.git',
                                               'repository': 'https://github.com/jkcoxson/plist_ffi.git',
                                               'tree': '1dc97bcad12d41a13417ae05fbcdc4408c6df553'}},
 'source_roots_replayed': ['AnisetteKit', 'LiveContainer', 'SideSign', 'SideStore'],
 'local_replay_scope': 'This parser replays four modified owner object graphs. The original independent review also replayed minimuxer; idevice/jktcp complete authenticated recursive tree metadata remain hash-bound original receipts, not locally retained raw checkout blobs.',
 'source_seal_sha256': '0caa11b1135abbf9c3905c601d100475b91f8d011237033ab47d1f96fad116e8',
 'status': 'EXACT_ORIGINAL_CAPTURE_AND_LOCK_BYTES_REPLAYED'}

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 48 * 1024 * 1024
MAX_EXPANDED_BYTES = 96 * 1024 * 1024
MAX_ENTRIES = 1500
LOCK = 'AltStore.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved'
REPOSITORY = 'NRG-Wardog/LiveContainer'
SOURCE_PARENT = '545438e1f0a89fdef375f73273c5c9fc2735cbce'
SIDESIGN_PARENT = '770b991b1dbc269ad19dd18b8b2e8252b665af9b'
SIDESTORE_PARENT = 'dc29ce0d79a299f32a340c460e1d61e8190e320c'
SIDESTORE_FINAL = 'd507f9e417f12c262b4d7e717c84d4a244c8e625'


def need(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        need(key not in value, 'duplicate JSON key: ' + key)
        value[key] = item
    return value


def _nonfinite(value):
    raise ValueError('nonfinite JSON number: ' + value)


def parse(raw):
    """Strict, size/depth-bounded JSON, including exponent overflow rejection."""
    need(type(raw) is bytes and len(raw) <= MAX_JSON_BYTES, 'JSON size/type bound')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                           parse_constant=_nonfinite)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid or excessively nested JSON') from error
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        need(depth <= 64 and nodes <= 500000, 'JSON structure bound')
        if isinstance(item, dict):
            pending.extend((x, depth + 1) for x in item.values())
        elif isinstance(item, list):
            pending.extend((x, depth + 1) for x in item)
        elif isinstance(item, float):
            need(math.isfinite(item), 'nonfinite JSON number')
    return value


def relative(name):
    need(isinstance(name, str) and 0 < len(name) <= 512, 'invalid relative name')
    need(not any(ord(c) < 32 or ord(c) == 127 for c in name), 'control in path')
    need('\\' not in name and ':' not in name and not name.startswith('/'), 'unsafe path')
    need(all(part not in ('', '.', '..') for part in name.split('/')), 'unsafe path component')
    need(str(PurePosixPath(name)) == name, 'noncanonical path')
    return name


def _open_nofollow(path, directory=False):
    """Walk every component with directory FDs, including the final component."""
    path = Path(path)
    need('..' not in path.parts, 'parent traversal in input path')
    path = path.absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if directory or index < len(parts) - 1:
                flags |= os.O_DIRECTORY
            newfd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = newfd
        result = fd
        fd = None
        return result
    finally:
        if fd is not None:
            os.close(fd)


def _directory(path):
    fd = _open_nofollow(path, directory=True)
    os.close(fd)
    return Path(path).absolute()


def read_regular(path, limit=MAX_JSON_BYTES):
    fd = _open_nofollow(path)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode), 'input is not a regular file')
        need(0 <= info.st_size <= limit, 'input size bound')
        raw = stream.read(limit + 1)
        need(len(raw) == info.st_size and len(raw) <= limit, 'input size changed or exceeds bound')
    return raw


class Archive:
    """Never extracts files. Hashes and parses the very same immutable buffer."""
    def __init__(self, raw, expected_sha, expected_size, expected_index, expected_count):
        need(type(raw) is bytes and len(raw) <= MAX_ARCHIVE_BYTES, 'archive size/type bound')
        need(len(raw) == expected_size and sha(raw) == expected_sha, 'exact original archive bytes differ')
        self.original_bytes = raw
        self.files = {}
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                need(0 < len(entries) <= MAX_ENTRIES, 'archive entry count bound')
                need(sum(i.file_size for i in entries) <= MAX_EXPANDED_BYTES, 'archive expanded size bound')
                names = []
                for item in entries:
                    relative(item.filename)
                    need(item.orig_filename == item.filename, 'NUL/aliased archive path')
                    need(not item.is_dir() and stat.S_ISREG(item.external_attr >> 16),
                         'nonregular archive member')
                    need(not item.flag_bits & 1, 'encrypted archive member')
                    need(item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                         'unsupported archive compression')
                    need(0 <= item.file_size <= MAX_MEMBER_BYTES, 'archive member size bound')
                    names.append(item.filename)
                need(len(names) == len(set(names)) == len({n.casefold() for n in names}),
                     'duplicate or case-aliased archive paths')
                need('evidence-index.json' in names, 'missing archive index')
                need(archive.getinfo('evidence-index.json').file_size <= MAX_JSON_BYTES, 'index size bound')
                index_raw = archive.read('evidence-index.json')
                need(sha(index_raw) == expected_index, 'exact original index differs')
                self.index = parse(index_raw)
                need(type(self.index) is dict and len(self.index) == expected_count, 'index count/type differs')
                need(set(names) == set(self.index) | {'evidence-index.json'}, 'archive/index exact file set differs')
                self.files['evidence-index.json'] = index_raw
                for name, digest in self.index.items():
                    relative(name)
                    need(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest), 'invalid indexed digest')
                    data = archive.read(name)
                    need(len(data) == archive.getinfo(name).file_size and sha(data) == digest,
                         'indexed bytes differ: ' + name)
                    # Parse every retained JSON as data, never import archive modules.
                    if name.endswith('.json'):
                        parse(data)
                    self.files[name] = data
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
            raise ValueError('invalid or unsupported archive') from error

    def raw(self, name):
        need(name in self.files, 'missing evidence member: ' + name)
        return self.files[name]

    def json(self, name):
        return parse(self.raw(name))


def _capture_status(phase, result):
    need(phase in ('phase-a', 'phase-b'), 'unknown capture phase')
    expected = 'CAPTURED_FOR_REVIEW' if phase == 'phase-a' else 'CAPTURED_FOR_INDEPENDENT_REVIEW'
    need(result.get('status') == expected and result.get('primary_error') is None, 'capture status/primary error')
    if phase == 'phase-b':
        need(result.get('app_typechecked') is True and result.get('distribution_ipa_built') is False,
             'Phase B app typecheck/distribution scope')


def _retained_prior_bytes(phase_a, phase_b, files):
    need(phase_b.raw('prior-phase-a/artifact.zip') == phase_a.original_bytes,
         'retained nested Phase A archive differs from original Phase A buffer')
    need(phase_b.raw('prior-phase-a/independent-review.json') ==
         files['evidence/phase-a/independent-review.json'], 'retained nested original Phase A review bytes differ')


def _authenticated_phase(phase, archive, files):
    prefix = 'evidence/' + phase + '/'
    review = parse(files[prefix + 'independent-review.json'])
    expected = PHASES[phase]
    for key in ('run_id', 'run_attempt', 'artifact_id', 'artifact_sha256',
                'host_commit', 'host_tree', 'input_sha256', 'evidence_index_sha256', 'indexed_files'):
        need(type(review[key]) is type(expected[key]) and review[key] == expected[key], 'review identity: ' + key)
    for field in ('authenticated_receipt_sha256', 'component_review_sha256'):
        for name, digest in review.get(field, {}).items():
            need(sha(files[prefix + relative(name)]) == digest, 'original review receipt binding: ' + name)
    run = parse(files[prefix + 'independent-run-api.json'])
    artifact = parse(files[prefix + 'independent-artifact-api.json'])
    commit = parse(files[prefix + 'independent-commit-api.json'])
    need(run['id'] == expected['run_id'] and type(run['id']) is int and
         run['run_attempt'] == expected['run_attempt'] and type(run['run_attempt']) is int,
         'authenticated run identity')
    need(run['repository']['full_name'] == REPOSITORY and run['head_sha'] == expected['host_commit'] and
         run['head_branch'] == 'validation/p0-login-trace-' + phase and
         run['path'] == '.github/workflows/p0-login-trace-' + phase + '.yml' and
         run['event'] == 'push' and run['status'] == 'completed' and run['conclusion'] == 'success' and
         run['html_url'] == 'https://github.com/' + REPOSITORY + '/actions/runs/' + str(expected['run_id']),
         'authenticated run tuple')
    need(artifact['id'] == expected['artifact_id'] and artifact['size_in_bytes'] == expected['artifact_bytes'] and
         artifact['digest'] == 'sha256:' + expected['artifact_sha256'] and artifact['expired'] is False and
         artifact['workflow_run']['id'] == expected['run_id'] and
         artifact['workflow_run']['head_sha'] == expected['host_commit'], 'authenticated artifact tuple')
    need(commit['sha'] == expected['host_commit'] and commit['tree']['sha'] == expected['host_tree'] and
         [p['sha'] for p in commit['parents']] == [SOURCE_PARENT], 'authenticated host sole source parent')
    inputs = archive.json('provenance/reviewed-inputs.json')
    result = archive.json('provenance/result.json')
    _capture_status(phase, result)
    need(sha(archive.raw('provenance/reviewed-inputs.json')) == expected['input_sha256'], 'reviewed input bytes')
    need(result['approved_inputs_sha256'] == expected['input_sha256'], 'result/input binding')
    need(result['run'] == {k: expected[k] for k in ('run_id', 'run_attempt', 'host_commit', 'host_tree')} |
         {'repository': REPOSITORY}, 'result run identity')
    for obj in (inputs, result):
        need(obj['phase'] == phase and obj['source_basis'] == 'p0-login-trace-' + phase + '-v1' and
             obj['source_seal_sha256'] == EXPECTED_SUMMARY['source_seal_sha256'], 'phase/source seal identity')
    need(result['last_stage'] == 'captured' and result['writers_quiescent'] is True and
         result['guest_execution'] is False and result['device_validation'] is False and
         result['real_ADI_executed'] is False and result['production_ready'] is False, 'capture scope boundary')
    need(inputs['publication_sha256'] == sha(files['evidence/source/publication-trees.json']), 'publication source binding')
    overlay = archive.json('provenance/host-overlay.json')
    need(overlay['commit'] == expected['host_commit'] and overlay['tree'] == expected['host_tree'], 'host overlay binding')
    groups = archive.json('provenance/owned-command-groups.json')
    need(type(groups) is list and len(groups) == expected['commands'], 'command group count')
    labels = [g['label'] for g in groups]
    need(len(labels) == len(set(labels)), 'duplicate command group')
    for group in groups:
        name = relative(group['label'])
        settled = archive.json('logs/' + name + '.settlement.json')
        need(archive.raw('logs/' + name + '.exit-code.txt') == b'0\n' and
             type(settled['returncode']) is int and settled['returncode'] == 0 and
             type(settled['terminationReturncode']) is int and settled['terminationReturncode'] == 0,
             'command did not succeed: ' + name)
        owned = settled['ownedProcessGroup']
        need({k: v for k, v in group.items() if k != 'label'} == owned, 'command settlement binding')
        need(all(owned[k] is True for k in ('leaderReaped', 'pipesJoined', 'quiescent', 'reapedBeforeSignals')) and
             owned['errors'] == [] and owned['signals'] == [], 'command not settled: ' + name)
        archive.json('logs/' + name + '.command.json')
    return review, inputs, result


def _commit(root, commit, tree, parents=None):
    raw = git(root, 'cat-file', 'commit', commit)
    need(hashlib.sha1(b'commit ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == commit,
         'raw commit object identity')
    header = raw.split(b'\n\n', 1)[0].splitlines()
    need([r[5:].decode() for r in header if r.startswith(b'tree ')] == [tree], 'raw commit tree')
    if parents is not None:
        need([r[7:].decode() for r in header if r.startswith(b'parent ')] == parents, 'raw commit sole parent')
    return raw


def _tree(root, commit):
    raw = git(root, 'ls-tree', '-r', '-z', commit)
    need(len(raw) <= 8 * 1024 * 1024, 'Git tree size bound')
    rows = raw.split(b'\0')
    need(len(rows) <= 10000, 'Git tree entry bound')
    result = {}
    for row in rows:
        if not row:
            continue
        meta, path = row.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        path = relative(path.decode('utf-8'))
        need(path not in result, 'duplicate Git tree path')
        need(mode in ('100644', '100755', '120000', '160000') and
             re.fullmatch('[0-9a-f]{40}', oid), 'invalid Git tree entry')
        result[path] = {'mode': mode, 'type': kind, 'git_object': oid}
    return result


def _lock_semantics(before, after):
    old, new = parse(before), parse(after)
    need(set(old) == set(new) == {'version', 'pins', 'originHash'} and
         type(old['version']) is int and old['version'] == new['version'] == 3,
         'resolver lock schema')
    for lock in (old, new):
        need(type(lock['originHash']) is str and re.fullmatch('[0-9a-f]{64}', lock['originHash']), 'lock originHash')
        identities = [p['identity'] for p in lock['pins']]
        need(len(identities) == len(set(identities)) and identities.count('anisettekit') == 1, 'duplicate/missing package pin')
    need(old['originHash'] != new['originHash'], 'genuine resolver originHash must change')
    old_pin = next(p for p in old['pins'] if p['identity'] == 'anisettekit')
    new_pin = next(p for p in new['pins'] if p['identity'] == 'anisettekit')
    need(old_pin['state'] == {'revision': '161435bcc0344e1a9853669455c1ca8d9dda2bf1'} and
         new_pin['state'] == {'revision': EXPECTED_SUMMARY['final_source_owners']['AnisetteKit']['commit']} and
         new_pin['location'] == 'https://github.com/NRG-Wardog/AnisetteKit.git', 'AnisetteKit resolver revision')
    expected = copy.deepcopy(old)
    expected['originHash'] = new['originHash']
    next(p for p in expected['pins'] if p['identity'] == 'anisettekit')['state'] = new_pin['state']
    need(expected == new, 'unrelated resolver lock semantic delta')


def _lock_child(root, parent, final, path, before, after, terminal, count):
    need(after == terminal == git(root, 'show', final + ':' + path), 'captured after/terminal/final lock bytes differ')
    need(before == git(root, 'show', parent + ':' + path), 'captured before/parent lock bytes differ')
    _lock_semantics(before, after)
    old, new = _tree(root, parent), _tree(root, final)
    need(len(old) == len(new) == count and old.keys() == new.keys(), 'complete lock-child tree inventory')
    need([p for p in old if old[p] != new[p]] == [path], 'lock child has unrelated tracked changes')
    blob = hashlib.sha1(b'blob ' + str(len(after)).encode() + b'\0' + after).hexdigest()
    need(new[path] == {'mode': '100644', 'type': 'blob', 'git_object': blob} and
         old[path]['mode'] == '100644' and old[path]['type'] == 'blob', 'exact lock blob/mode')


def _source_replay(roots, handoff, archives, phase_inputs, publication):
    need(type(roots) is dict and set(roots) == {'AnisetteKit', 'SideSign', 'SideStore', 'LiveContainer'},
         'exact four source roots required')
    roots = {name: _directory(path) for name, path in roots.items()}
    for name, root in roots.items():
        _directory(root / '.git')
        # Git status may consult clean filters. Reject any local filter driver
        # before invoking it; the shared Git wrapper already disables hooks,
        # fsmonitor, global/system configuration and lazy fetching.
        config = git(root, 'config', '--list', '--null')
        need(not any(row.split(b'\n', 1)[0].startswith(b'filter.') for row in config.split(b'\0')),
             'source repository config contains a filter driver')
        owner = handoff['final_source_owners'][name]
        need(git(root, 'remote', 'get-url', 'origin').decode().strip() == owner['repository'], 'source repository: ' + name)
        need(git(root, 'rev-parse', 'HEAD').decode().strip() == owner['commit'], 'source HEAD: ' + name)
        need(git(root, 'status', '--porcelain', '--untracked-files=all', '--ignore-submodules=all') == b'', 'dirty source root: ' + name)
        _commit(root, owner['commit'], owner['tree'])
        for phase in ('phase-a', 'phase-b'):
            inputs = phase_inputs[phase]
            item = (inputs['owners'] if phase == 'phase-a' else inputs['graph']['owners'])[name]
            replayed = snapshot(root, item['base'], item['commit'])
            need(replayed['tree'] == item['tree'] and replayed['changes'] == item['changes'], 'complete source graph replay: ' + name)
    final_ss = handoff['final_source_owners']['SideStore']
    raw_commit = _commit(roots['SideStore'], SIDESTORE_FINAL, final_ss['tree'], [SIDESTORE_PARENT])
    need(sha(raw_commit) == publication['raw_commit_sha256'], 'published raw commit receipt binding')
    _commit(roots['SideSign'], handoff['final_source_owners']['SideSign']['commit'],
            handoff['final_source_owners']['SideSign']['tree'], [SIDESIGN_PARENT])
    for phase, name, parent, path, count in (
            ('phase-a', 'SideSign', SIDESIGN_PARENT, 'Package.resolved', 72),
            ('phase-b', 'SideStore', SIDESTORE_PARENT, LOCK, 658)):
        archive = archives[phase]
        captured = [archive.raw('provenance/' + name + '.Package.resolved.' + suffix)
                    for suffix in ('before', 'after', 'final-observed')]
        _lock_child(roots[name], parent, handoff['final_source_owners'][name]['commit'], path, *captured, count)
    equivalence = snapshot(roots['SideStore'], SIDESTORE_PARENT, SIDESTORE_FINAL)
    need(equivalence['changes'] == publication['equivalence']['changes'], 'full publication equivalence receipt differs')
    return roots


def replay(plan_path, source_roots):
    """Replay the immutable original evidence at a location; return bytes, summary.

    Location selection never selects identities. Every accepted original output
    is hash-anchored in FIXED_FILE_HASHES. Archive validation uses one byte buffer;
    no source or artifact code is loaded, and no source checkout is changed.
    """
    plan = _directory(plan_path)
    files = {}
    for output, source in INPUT_FILES.items():
        raw = read_regular(plan / source)
        need(sha(raw) == FIXED_FILE_HASHES[output], 'fixed original evidence differs: ' + output)
        parse(raw)
        files[output] = raw
    handoff = parse(files['evidence/final-source-handoff.json'])
    publication = parse(files['evidence/publication/independent-publication-clearance.json'])
    for name, digest in publication['authenticated_receipt_sha256'].items():
        need(sha(files['evidence/publication/' + relative(name)]) == digest, 'publication API receipt binding')
    need(sha(files['evidence/publication/independent-lock-tree-review.json']) == publication['local_tree_review_sha256'],
         'publication local review binding')
    need(handoff['final_source_owners'] == EXPECTED_SUMMARY['final_source_owners'] and
         handoff['source_children'] == EXPECTED_SUMMARY['source_children'], 'final source graph')
    api = parse(files['evidence/publication/independent-published-commit-api.json'])
    ref = parse(files['evidence/publication/independent-published-ref-api.json'])
    need(api['sha'] == publication['commit'] == SIDESTORE_FINAL and api['tree']['sha'] == publication['tree'] and
         [p['sha'] for p in api['parents']] == [SIDESTORE_PARENT] and
         ref['ref'] == 'refs/heads/' + publication['branch'] and ref['object']['sha'] == SIDESTORE_FINAL and
         ref['object']['type'] == 'commit', 'authenticated final source publication')
    archives, reviews, inputs, results = {}, {}, {}, {}
    for phase, expected in PHASES.items():
        raw = read_regular(plan / 'capture-results' / str(expected['run_id']) / 'artifact.zip', MAX_ARCHIVE_BYTES)
        archive = Archive(raw, expected['artifact_sha256'], expected['artifact_bytes'],
                          expected['evidence_index_sha256'], expected['indexed_files'])
        archives[phase] = archive
        reviews[phase], inputs[phase], results[phase] = _authenticated_phase(phase, archive, files)
    for output, (phase, member) in ARCHIVE_OUTPUTS.items():
        raw = archives[phase].raw(member)
        need(sha(raw) == FIXED_FILE_HASHES[output], 'retained original member differs: ' + output)
        files[output] = raw
    a, b = archives['phase-a'], archives['phase-b']
    _retained_prior_bytes(a, b, files)
    prior = b.json('provenance/prior-phase-a.json')
    need(prior['independent_review'] == reviews['phase-a'] and
         prior['independent_review_sha256'] == PHASES['phase-a']['review_sha256'] and
         prior['input_sha256'] == PHASES['phase-a']['input_sha256'] and
         prior['api_receipt_sha256']['archive'] == PHASES['phase-a']['artifact_sha256'], 'actual retained Phase A binding')
    prior_expected = inputs['phase-b']['prior_phase_a']
    need(prior_expected['independent_review']['receipt_sha256'] == PHASES['phase-a']['review_sha256'] and
         prior_expected['expected']['artifact_sha256'] == PHASES['phase-a']['artifact_sha256'] and
         prior_expected['expected']['lock_sha256'] == sha(a.raw('provenance/SideSign.Package.resolved.after')),
         'Phase B input/prior raw Phase A lock binding')
    graph = inputs['phase-b']['graph']
    need(results['phase-b']['graph'] == graph and graph['children'] == handoff['source_children'], 'captured graph differs')
    need({name: {k: item[k] for k in ('commit', 'tree', 'repository')} for name, item in graph['owners'].items()} ==
         handoff['captured_source_owners'], 'captured owner identities differ')
    need(b.raw('provenance/source-before.json') == b.raw('provenance/source-after.json') and
         b.raw('provenance/app-resolved-before-build.json') == b.raw('provenance/app-resolved-after-build.json'),
         'Phase B source/resolved graph changed during build')
    for output, status in (
            ('independent-command-source-review.json', 'INDEPENDENT_COMMAND_SOURCE_PRIOR_REVIEW_PASSED'),
            ('independent-cargo.json', 'PASS_WITH_EVIDENCE_BOUNDARIES'),
            ('independent-products.json', 'PASS_PRODUCT_EVIDENCE_BINDINGS_WITH_EXPLICIT_TRUST_LIMITS')):
        component = parse(files['evidence/phase-b/' + output])
        need(component['status'] == status, 'original component review status')
        need(not component.get('gaps', []) and not component.get('mismatches', []), 'original component unresolved findings')
    _source_replay(source_roots, handoff, archives, inputs, publication)
    need(set(files) == set(FIXED_FILE_HASHES) and all(sha(raw) == FIXED_FILE_HASHES[path] for path, raw in files.items()),
         'final retained exact file inventory')
    return files, copy.deepcopy(EXPECTED_SUMMARY)
