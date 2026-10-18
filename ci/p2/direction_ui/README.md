# Genuine candidate Settings observation gate

Pins the successfully compiled candidate from run39165480984. Passing this gate
requires fresh native observations and retained screenshots; source review alone
does not establish a UI result.

The external XCTest template reuses the existing exact startup presentation-only
acknowledgements and genuine Settings navigation. It observes English, Hebrew,
Simplified Chinese, Traditional Chinese, then English, without launch-language
arguments, preference injection or product patches.

A newly installed app must show Start Collapsed false. One real UI tap in the
initial English layout sets true. Each language then checks the translated switch
label and true value, captures its screenshot/AX, returns to the real language
picker and captures that screen. This proves only that persistent preference
witness, not transient text, focus, scrolling, navigation or async model identity.
The separately passing extracted state-method tests cover load/write races.

Completion requires native execution and independent pixel review. The remaining
integration/relaunch/error presentation witnesses remain separate acceptance work.
No physical-device authentication or distributable IPA follows from this test.

Run39166185990 retained the same SS-LAUNCH-C11-L15 presentation after entering
Settings and before any language/preference observation. The lane permits one
optional exact recurrence acknowledgement at that boundary only, binding the
completed startup fault and retaining separate pre/post receipts and pixels.
Every unknown alert and any later recurrence still blocks the run. The service
fault remains unresolved. Settings starts two settingsGet loads whose error
paths can present issues; the exact triggering request was not identified.
