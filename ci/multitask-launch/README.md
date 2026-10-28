# Focused multitasking launch boundary investigation

This job is not an app build, private-extension integration test, or delivery gate.
It executes the candidate's actual extracted Objective-C launch boundary with
controlled extension outcomes and observes the unchanged virtual-window attachment
on a real installed iOS Simulator runtime matching the selected Xcode SDK family.

The app diagnostic baseline is `830e8277`; the exact tested LC revision is in
`source.json`. No submodules, credentials, pairing records, provisioning data,
guest applications, account data or downloaded simulator runtimes are used.

The UIKit fixture's four representative SwiftUI root observations are evidence,
not assertions that a real guest works. A deliberately obscured sibling is a
negative control. It uses actual VirtualWindowsHostView and the actual language
bridge, with declared AppScene/Decorated boundary stubs. The first-child attachment
line is projected with a nullable child only to record an absent child instead of
crashing. Source hashes bind these files to the unchanged delivered baseline.

Run portable controls:

```
python3 -B -m unittest discover -s ci/multitask-launch -p 'test_*.py' -v
```

Native execution requires macOS and the selected installed Xcode/simulator.
`run_completion.py` and `run_attachment.py` reject missing native capabilities,
failed or skipped execution, modified inputs, and stale output directories.
Only the newly created simulator may be shut down/deleted. No launch or assertion
retry is allowed. Existing bounded boot observation and SDK selection policy is
reused unchanged from `ci/language-switch`.

`verify.py` independently checks source/run identities, complete tracked input
inventory, native completion report, fixture result hashes, baseline extraction,
SDK/runtime selection, and owned simulator boot evidence. Existing native CI,
packaging, source graph and size/signature gates are not changed by this job.
