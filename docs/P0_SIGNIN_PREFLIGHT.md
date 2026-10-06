# Focused P0 sign-in UI preflight

On macOS with Xcode and an installed iOS runtime containing an iPhone and iPad:

```sh
python3 scripts/run_p0_signin_preflight.py --output artifacts/p0-preflight
```

The output directory must be absent or empty. A completed earlier run cannot be
reused or overwritten. No upstream source checkout, Rust dependencies, IPA build,
or legacy layout rendering is needed. The oldest installed available iOS runtime
is selected using the existing release-rendering selector. Phone and tablet run
serially. The runner never creates, erases, or deletes simulators, and shuts down
only devices for which it initiated a boot attempt. Existing booted devices remain
booted. Failed boot/test/export stages still attempt diagnostics and owned cleanup.

## Exact production input

`patch_v3_unified_shell.generated_shell_source()` is shared by production
`patch_host()` and this runner. It retains the existing four-template byte
composition, rather than reconstructing a synthetic prepared host. The generated
source and generator/input SHA-256 hashes are retained. The actual builder checkout
HEAD and working-tree state are recorded; fixture build tags use that HEAD.
Uncommitted changes under `scripts/` or `tests/` fail closed: commit the intended
inputs before running. Input hashes are still retained when that check fails.
Changes to the builder commit or input hashes during execution also fail closed.

An optional comparison checks every extracted P0 source, including the full
production sign-in view, against an already prepared production shell:

```sh
python3 scripts/run_p0_signin_preflight.py --output artifacts/p0-preflight \
  --v3-source work/LiveContainer/LiveContainerSwiftUI/Views/V3UnifiedShell.swift
```

Any extracted declaration/member drift fails before building. This comparison
does not claim that unrelated parts of a prepared host or its dependencies match.

## Acceptance and evidence

The runner delegates to the existing `run_p0_signin_rendering.prepare`, `execute`,
and `verify_export` acceptance path. Both device classes must pass all four cases:
credentials and submitting, each at default and largest Dynamic Type. All eight
case reports and 24 named, valid screenshots are required; diagnostics cannot
substitute for missing acceptance artifacts.

Existing bounds remain unchanged: 300 seconds for build/default commands,
240 seconds per XCTest case, 1080 seconds per device test invocation, 600 seconds
for simulator boot readiness, 30 seconds for a terminal screenshot, and 15 seconds
for runner-container lookup. The existing durable capture runs before attachment
export and simulator cleanup, including when XCTest times out or produces no
finished result bundle.

Evidence includes:

- `p0-preflight-verification.json`: distinct fail-closed aggregate, including
  actual source identities, devices, cleanup results and failure phase
- `source-generation.json` and `production-source/V3UnifiedShell.swift`
- `command-diagnostics.jsonl`: bounded subprocess diagnostics
- `p0-signin/`: source/project snapshots, xcresults, exported attachments,
  per-device verification, terminal images and current-run durable diagnostics

The aggregate is written before work and updated as work progresses. A failed or
interrupted run stays failed; a hard termination can leave `status: running` with
`passed: false`. This is preflight evidence only: `releaseEligible` and
`fullSuiteValidated` are always false. It neither runs the 14 legacy reports nor
replaces the full release gate, package audit, physical-device checks, real account
operations or spoken VoiceOver validation.

## Portable source/lifecycle regression checks

```sh
python3 -m unittest discover -s tests -p test_p0_signin_preflight.py -v
```

These tests exercise the real extractor, preparation, export verifier and durable
capture with mocked macOS commands, including failure and interruption cleanup.
They are source/lifecycle tests, not simulator execution evidence. The executable
runner fails closed on Linux and preserves source provenance plus that limitation.
