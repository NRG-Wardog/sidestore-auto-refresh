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
