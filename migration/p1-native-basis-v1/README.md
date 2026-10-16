# P1 presentation-only admission

Current state: native review pending. The P1 gate is disabled until a real
native host/UI run is independently reviewed and its exact receipt is sealed.
The active production pins remain P0 until that review finishes.

## Exact source transition

- Repository: NRG-Wardog/LiveContainer
- Parent: 00546cec351adad1fa1cad5590da4f06e612db8f
- P1 checkpoint: fbaafb939bfd3b7c08fdc8ff4b8d2aae43526ece
- P1 tree: 3d3efc14415db6d28ede216965f83fa5924e9e9c
- Only changed path: LiveContainerSwiftUI/Views/V3UnifiedShell.swift
- SHA-256: 3c40aab6667431d0ecb502d49a38416b9e76250ddf6e66dc1037e60d4594e0be

The reviewed change reserves navigation space for recovery feedback and scopes
copy feedback to its diagnostic payload. Authentication, service callbacks,
other owner source, dependency locks and child commits remain unchanged.
This checkpoint does not contain the P2 language changes.

The original P0 metadata and admission code remain byte-identical. Copies under
`inherited/` preserve the original facts and native receipts without relabeling
them as evidence for the changed P1 host. A separate P1 native receipt must bind
fresh host compilation, the actual P1 source, canonical cleanup checks, the
existing render matrix and screenshots, plus 5 targeted P1 behavior cases on
each of phone and tablet: feedback timeout, prompt replacement, dismiss/reopen,
and recovery navigation at default and largest text size.

A real run cannot be replaced with source checks, synthetic unit-test fixtures,
a caller-supplied registry digest or a receipt whose hashes were merely changed.
The new gate checks the exact Git parent/tree/delta, complete source inventory,
immutable review digest, fresh host run, preserved inherited proof and final
package evidence. Physical-device signing, login and refresh remain separate.

Run the local structural and adversarial suites with:

```
python3 -B -m unittest discover -s migration/tests -p test_p1_admission.py -v
python3 -B -m unittest discover -s migration/tests -p test_p0_admission.py -v
V3_P1_REQUIRED=1 MAINTAINED_LIVE_CONTAINER_TEST_SOURCE=/path/to/P1 \
  python3 -B -m unittest discover -s tests -p test_v3_signin_screen_cleanup.py -v
```

Synthetic positive fixtures are confined to temporary test directories and do
not alter the production review anchor or grant native/build readiness.
