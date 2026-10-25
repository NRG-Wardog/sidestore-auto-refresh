# Issue #33: lifecycle diagnosis

Reviewed 2026-10-05 against LiveContainer `12377cf3b91d51739a33f14a302e5f522b238593` and the current builder patches.

## What is established

- The original pinned guest implementation listens for only one background notification per runtime mode. The builder already backports both observers. Issue #33's release comment says this correction shipped in v3.1.0, but device survival remains unverified.
- In the pinned iOS 18+ scene-hosting path, `AppSceneViewController` removes the extension's application-lifecycle observers and delegates events to its scene observer. This supports the need to handle both guest notification routes; source inspection cannot establish which route fired on a reporter's device.
- The LiveProcess Return path minimizes or requests host activation; it does not call `terminate`. The explicit Close action does call `terminate`. Direct-process Return intentionally restarts the host and is a separate mode.
- The overridden `Dead10ccFix._terminateWithStatus:` is a preparation callback, despite its inherited name. It invokes preparation and can arrange another callback after two seconds. It does not itself issue a process kill or exit. The previous `PROCESS_INTERRUPTED` marker there falsely represented a healthy callback as an interruption.

## Diagnostic-only correction

Preparation now emits `DEAD10CC_PREPARATION`. Actual extension request interruption/cancellation callbacks emit `PROCESS_INTERRUPTED`/`PROCESS_CANCELLED`, respectively, with the guest PID and coarse callback source. These are callback observations, not an OS termination reason. No paths, exception text, guest data or credentials are logged. Scanner logic, termination policy, lifecycle observers and ownership behavior are unchanged.

Prepared trees upgrade the old diagnostic idempotently. Source-contract tests check the emitted markers, reject changed callback anchors, and show that removing the added logs restores the original callback bodies exactly.

## Still unproven

Neither the issue body nor its two comments supplies a device `.ips` report, RunningBoard termination evidence, or same-PID trace. Audio pausing followed by loss of a guest is consistent with a suspension boundary, but does not prove a specific termination reason. A confirmed descriptor-iteration defect found separately remains unmodified and has not been linked to a device termination.

Minimum evidence for one reproducible occurrence:

1. Exact IPA/build, iOS version, guest app/version and runtime mode.
2. Approximate timestamp, actions and elapsed background interval, with PID before leaving and on return where available.
3. Matching device analytics `.ips` report or device-console termination event: process identity/PID, termination namespace/code and timestamp. Include the lifecycle lines around that interval. Redact unrelated personal data.

A same-PID return establishes survival for that trial; a changed PID alone establishes a relaunch, not its cause. Diagnose a matching OS termination code before claiming that a candidate fixes the reported failure. Run device trials without artificially extending background lifetime.

Sources: [issue #33](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/33), [release validation comment](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/33#issuecomment-5978974699), [pinned scene controller](https://github.com/NRG-Wardog/LiveContainer/blob/12377cf3b91d51739a33f14a302e5f522b238593/MultitaskSupport/AppSceneViewController.m), [pinned preparation callback](https://github.com/NRG-Wardog/LiveContainer/blob/12377cf3b91d51739a33f14a302e5f522b238593/LiveContainer/Tweaks/Dead10ccFix.m).
