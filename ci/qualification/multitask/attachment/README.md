# Qualified attachment helper with retained regression control

The exact candidate is `../source.json`. The historical counterfactual is the
unchanged `VirtualWindowsHostView.m` from delivered LC dcb98b6, SHA256
3b31573f5ef5cf7d8d7ceb0ae72869eca97f52609957997174a4d820863bd785.
Its class is renamed as a subclass so old/new implementations coexist; its old
initializer and hit testing remain intact. Import substitutions provide only
reviewed AppScene/Decorated boundary stubs. The old first-child selection is
projected with an optional child to record absence rather than trap.

The actual candidate VirtualWindowsHostView header/implementation, language bridge,
and LCGuestHostWindowSelection enum are compiled. The exact keyWindow property
body is executed with scene/session providers replaced by finite input types,
while real UIKit windows/controllers and the actual selector are retained.
This tests selection logic, not actual OS scene discovery or registration.

Mandatory evidence contains five historical cases, ten corrected helper cases,
and fourteen host ownership cases. The historical controls must reproduce
[true,true,false,false,false] marker hit testing. Corrected attachment must pass
with/without bridge, English/Hebrew, repeat attachment, opaque sibling, same-root
child replacement, host-bounds resize, visibility preservation/caller unhide,
marker-only root replacement, and rejection of invalid hosts. Explicit guest LTR
semantics and identity survive. Host-bounds resize is not an OS rotation test;
marker hide/unhide is not a full guest minimize/resume test; marker migration is
not permission to move an existing live guest controller to another owner.

Four synthetic screenshots show old/new bridge-on English/Hebrew windows. They
contain only fixture labels and are hashed in the receipt. No user data or real
extension is executed. `attachment_contract.py` is the shared finite acceptance
validator for focused CI, native CI and independent package audit.

The fixture uses four 100ms layout-settling yields per root only to let SwiftUI
install its native hierarchy, never as product readiness logic. Its representative
TabView is not the complete V3UnifiedShell. Physical device app launching remains
unproven by this fixture.
