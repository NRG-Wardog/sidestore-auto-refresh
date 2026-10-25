# Genuine candidate compilation gate

Pins LiveContainer f46a3e14c0752714144aa076355663c79a54f1a9 and its exact tree.
Only the two reviewed Settings changes differ from eb10cedb. This builds the
real LiveContainer Release target for a newly created, unbooted iPhone17Pro
iOS26.5 simulator destination, as used by the prior successful host build.
The generic destination attempted unsupported x86_64 assembly in run39164895608.
No product source or assembly changes are made. There is no product build-setting
or architecture override. Declared dependency commits and trees remain pinned.

The existing provenance, package inventory and localization contracts are reused.
The exact native build command has a 1500-second ceiling; other commands240s,
shared experiment32minutes, separate finalization6minutes. Owned-child cleanup
and stable archive gates are retained. No simulator launch or device authentication
is attempted. Passing means full-product compilation/resource packaging only.
The resulting simulator application is not an IPA and is not user delivery.
