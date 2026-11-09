# Tests

Run commands from the repository root. File patterns still find domain tests:

```sh
python3 -B -m unittest discover -s tests -p 'test_settings.py' -v
python3 -B -m unittest discover -s tests/settings -t . -v
python3 -B -m unittest discover -s tests -v
```

## Layout and scope

- Root modules retain current CI, packaging, source-identity and build-evidence
  entry points. Some are immutable provenance inputs; do not rename them or
  update recorded hashes to make a cleanup pass.
- `authentication/`: account, anisette, keychain, certificate and prompt behavior.
- `refresh/`: scheduling, history, transport, prerequisite and result handling.
- `installation/`: IPA staging, sources, provisioning and operation recovery.
- `settings/`: persistence, effective defaults, cached settings and write races.
- `presentation/`: shell, sign-in, rendering and localization regressions.
- `host/`: startup, guest lifecycle, app groups and cross-feature host behavior.
- `support/` contains shared test helpers; `fixtures/` contains harness inputs.

Domain suites mostly exercise retained patch templates and historical source
graphs. They do not qualify the current upstream app or a release by themselves.
Native tests require their documented Swift, macOS/Xcode or exact source pins.
A prerequisite skip is not a pass. Current required CI gates remain unchanged.

For a required lane, the existing runner rejects zero tests and unexpected skips:

```sh
python3 -B scripts/run_required_tests.py --start-directory tests \
  --pattern 'test_integration_contracts.py' \
  --allowlist scripts/required_test_skip_allowlist.json
```

It also supports absolute paths from outside the checkout. Do not broaden the
empty skip allowlist to hide missing native prerequisites. The integration suite
checks that ordinary discovery includes every root and domain module, has no
import failures or duplicate IDs, and preserves focused file selection.

## CI ownership

`source-integrity-contract.yml` owns reviewed-source validation and current-entry
controls on Linux, portable packaging controls on Linux, and the bounded native
converter/packaging suite on macOS. The Linux and macOS packaging runs are
intentional cross-platform coverage; each suite runs once per platform.
`custom-integration-contract.yml` owns integration inventory and boundary tests,
not another copy of the packaging suite. Feature branches are checked on the
pull request, and main is checked after merging, not twice for every branch push.
The retired packaging/current-entry workflow wrappers no longer duplicate these
jobs. Full native builds and compiled-catalog qualification remain separate.

The Dead10cc lifecycle suite now reads the current LiveContainer owner commit
from `ci/issues-native/candidate-graph.json`. It rejects a different checkout or
a modified source blob, then executes its C descriptor and transition blocks.
Set `MAINTAINED_LIVE_CONTAINER_TEST_SOURCE` to that acquired checkout for local
runs. CI supplies the same pinned source already acquired for integration
inventory, and treats missing prerequisites as failures, not skips. Historical
patch-generator/idempotence assertions were retired with their unused generator;
notification, diagnostics, resource ownership and runtime controls remain.

Certificate observation tests read the same maintained LiveContainer pin as the
production build, never a generated patch or historical upstream fixture. The
macOS bounded-contract lane requires the real ZSign/OpenSSL 3 harness (valid,
wrong-password, malformed and multiple certificates plus validation callbacks)
without skips. The shared owner loader rejects mismatched pins and modified
source bytes. Dead10cc continues to execute production C blocks on Linux.

CoreDevice source guards now inspect the pinned maintained idevice owner after
CI source acquisition, including heartbeat log privacy and rejected dynamic-log
mutations. The historical transport generator is retired. These are source
contracts, not a Rust execution or device-connectivity claim; the existing
maintained native owner/ABI qualification remains unchanged.

Multitask Dock regressions read the maintained owner's Dock and Settings Swift
sources. The macOS lane executes the extracted production session/presentation
state helper once (the old suite ran the same harness twice) and syntax-checks
both complete owner files, with no skips. First presentation, session override,
per-session reset, tucked preference and separate hidden/guest settings remain
covered. This is not an on-device SwiftUI rendering result.

App Layout tests now inspect the maintained LiveContainer/SideStore owners. They
cover the current grid-size/label preferences, accessibility/action routing,
dynamic type and shared theme configuration. The old list/grid settings patch
for SideStore is not a current headless-product input. Its generator and replay
tests are retired. Grid/model templates remain because Issue25 rendering uses
them, and tests require byte equality with the maintained owner. macOS CI requires
all source checks and Swift syntax checks without skips; this is not visual QA.
