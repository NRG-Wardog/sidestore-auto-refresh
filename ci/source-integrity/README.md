# Current source integrity

`verify.py` checks the reviewed bytes of current packaging tools, their retained
support files, source-owner graph/receipt entry and every active workflow.
`manifest.json` is a review input, not a file regenerated during CI. Changes to
its bytes require updating the verifier's manifest digest in the same reviewed
change. Added or removed workflows must be explicitly reviewed too.

The check rejects missing or changed sources, duplicate keys, path traversal,
symlink files/directories and unreviewed workflows. It does not execute old Git
objects, reconstruct historical directory layouts, or require retired branch
maintenance tools. Those tools remain available in Git history, not this tree.

Run:

```sh
python3 -B ci/source-integrity/verify.py
python3 -B -m unittest discover -s ci/source-integrity -p 'test_*.py' -v
```

This is repository integrity, not artifact or device qualification. Native
builds still verify exact component pins, compiler inputs, same-run receipts,
CMS/signing policy, refresh/database/startup/multitask results and packaging
admission. The manual maintained build is documented in `ci/current-build/`.

CFBundle and embedded startup regressions read pinned owner sources. The two C
harnesses retain guard-page and failed-read/write checks; their unsafe baseline
loops are explicit negative test controls, not a source transformer. macOS also
compiles the real production adapter against the iPhoneOS SDK. These bounded
checks are not device qualification. Current SideStore uses its maintained
MaintenanceManager storage preflight, not the former callback-era startup code.
Native error presenter regressions read pinned LiveContainer sources. They check
finite IDs, display/copy routing, optional errors and separate crash details.
macOS executes the exact Swift helper plus a deliberately wrong-ID control and
parses the 12 maintained Swift files. These checks are not UIKit/device tests.
The source-evidence inventory remains shared by both packaging verifiers.
