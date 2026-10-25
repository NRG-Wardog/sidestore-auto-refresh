# IPA staging: source fix and acceptance boundary

The host now admits IPA staging asynchronously and dispatches only an accepted
result belonging to the same install attempt. The synchronous security scope,
file coordination and copy run in `Task.detached`; a plain MainActor `Task` is
used only to receive results and update the UI.

Cancellation clears UI ownership immediately. It does not interrupt a native
copy or delete its open destination. The worker cleans its unclaimed result
after the copy returns. Late success/failure cannot replace a newer attempt or
start an install. Picker dismissal can happen before or after staging finishes.
A “Preparing IPA…” row provides an accessible Cancel action while staging runs.

The copy is private under a UUID `.partial` name until validated and renamed to
its canonical IPA token. Completed IPA orphan pruning cannot remove an in-flight
copy that inherited an old source timestamp. Normal failure/cancellation removes
partials. Per-token process-shared flock leases protect active copies; aged
partials and lease-only crash remnants are reclaimed only after nonblocking lock
acquisition and descriptor/path inode verification. A writer paused before lock
acquisition cannot resume on an unlinked lease. Existing service-lease checks
still govern all dispatched tokens.

## Automated checks

`python -m unittest discover -s tests -p 'test_v3_async_ipa_staging.py' -v`

- Source guards reject the audited synchronous MainActor path
- Extracted production host methods cover both dismissal orders, cancellation,
  same-file replacement, late failure/success, reload gates and store teardown
- A macOS harness runs the production detached worker and synchronous staging
  with real `NSFileCoordinator`, a 16 MiB file and controllable I/O. It covers
  a MainActor heartbeat while copying is blocked, cancellation without deleting
  an open file, orphan pruning during copy, same-file retry, disk-full/partial
  cleanup, coordinator failure, source removal and final file permissions
- A separate executable lease harness verifies active-copy exclusion across
  processes, aged partial cleanup after process exit, recent-file retention,
  preserved tokens, symlink rejection and stale-descriptor rejection
- Existing launch and pinned portal harnesses retain D08/D10 coverage; fast
  patch guards ensure missing launch UUIDs do not produce synthetic Cocoa 3587
  and quota 9120 mapping remains narrow and idempotent

The Linux implementation workspace has no Swift compiler or Apple SDK. Swift,
coordinator and UI harness results must be established in macOS/native CI before
claiming native verification. Full upstream patch replay requires pinned source
trees; template-level shell replay remains covered by the ordinary Python suite.

## Native UI/device acceptance (pending)

Use an expendable test installation and the exact candidate build, without
changing or deleting the user's primary installation or account data.

1. Pick a large IPA from local Files and from a deliberately slow file provider
2. While “Preparing IPA…” is visible, scroll/navigate and tap
   `V3_IPA_STAGING_CANCEL`; verify immediate UI response and no operation cover
3. Select the same file again before the earlier provider finishes. Complete the
   newer attempt, then allow the old callback to return. It must neither replace
   the newer presentation nor delete its staged token
4. Repeat with the picker dismissed before and after copy completion, a denied
   provider read, a provider disconnect, low storage and process termination
5. Verify that one successful attempt produces one backend install request;
   retry after a terminal backend result must retain the service lease policy
6. On device, verify security-scoped provider access lasts through coordination
   and that late access failures produce a safe preparation error without paths

Record UI responsiveness, files/leases, exact build SHA and observed results.
A source guard or MainActor heartbeat is not a device/UI acceptance result.
D08 diagnostics do not prove extension activation is fixed; D10 does not free
App ID capacity or explain unrelated authentication/provisioning failures.
