# Combined startup crash: matched-binary investigation

The reported trap is the nil bookmark force unwrap in the shipped v3 startup path. This is supported by the matching binary, not inferred from a function name.

- Builder: af50c2dbe55005ee57e56d2f53975f91413e989f
- CI: https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34756153474
- Original IPA SHA-256: 65ffa81c4bae88b3e090c4b7f71f0ac1d2f2b1fcf072e51b617c97008b0cad2f
- Framework: Payload/LiveContainer.app/Frameworks/SideStoreSupport.framework/SideStoreSupport
- Mach-O UUID: 07E95F24-0DF4-3F9F-B1B4-3AF4881C1CBD (exact reporter match).
- Reported PC 0x102e2bea4 minus image base 0x102e20000 = 0xbea4.
- The async v3_startRefresh resume symbol starts at 0xa270; the difference is 7220 bytes, matching the reported stack offset.

ARM64 disassembly of that exact framework:

```text
0xac04  bl   _getenv
0xac08  cbz  x0, 0xbea0
...
0xac74  bl   0x5c08             ; _bookmarkForURL
0xac7c  bl   objc_retainAutoreleasedReturnValue
0xac80  mov  x20, x0
0xac88  bl   objc_release
0xac8c  cbz  x20, 0xbea4
...
0xbea0  brk  #1                ; different trap: missing getenv result
0xbea4  brk  #1                ; reported trap: nil bookmark result
0xbea8  brk  #1                ; separate bundle unwrap
```

The matching source was reconstructed deterministically from the builder's patches and pinned source revisions. It contains bookmarkForURL(sideStoreHomeURL)! in the corresponding operation. The Objective-C helper passed error:0 and discarded the reason for failure.

No original matching dSYM was retained by that workflow. This is binary/symbol-table/disassembly evidence, not a DWARF source-line symbolication. Do not describe reconstructed source line numbers as symbolicated line numbers. The diagnostic regression builds and executes the same nil-bookmark force-unwrap mechanism; it does not reproduce the reporter's filesystem on a device.

The underlying bookmark NSError remains unknown. First-run storage was previously prepared only by the later legacy bootstrap path, making an absent SideStore directory a concrete mechanism; it is not claimed as the confirmed filesystem condition on the reporter's phone. Authentication, pairing and VPN failures do not explain this instruction.

## Corrected responsibilities

CombinedServiceConnection is an injectable, MainActor-isolated startup state machine. RefreshHandler supplies platform adapters for authoritative LC_HOME_PATH resolution, existing-container directory preparation, NSError-preserving bookmark creation, extension discovery/launch, XPC acceptance and service readiness. It neither creates an alternate container nor resets existing data. Legacy and service startup share LCPrepareContainerDirectories.

V3ServiceBridge connects through ensureServiceConnected. There is no __v3_connect sentinel or disguised empty refresh request. Existing AppIntent entry points remain explicit refresh adapters. Connection readiness, command completion and installation verification are distinct. Extension and XPC callbacks carry launch identity; concurrent callers share one launch, and continuations settle once. Failed startup requires controlled retry rather than timer-driven relaunch.

CombinedFailure carries a versioned, bounded allowlist of operation, stage, stable code, correlation UUID, sanitized underlying domain/code and optional retryability. Recovery text is generated locally. Arbitrary NSError userInfo and native descriptions are not serialized. Refresh result metadata is sanitized separately so saved errors cannot bypass the command error envelope.

Both combined product lines include the Issue 24 propagation fix. CoreDevice, heartbeat, RSD/lockdownd and live UniqueDeviceID errors are not converted into a pairing error or hidden by a pairing-file UDID fallback. Standalone source/build configuration is not changed.

## Acceptance limits

Repository tests, injected native-boundary tests, parsing, source builds and package markers are not physical-device proof. Login/2FA, cold launch, extension registration, in-place upgrade and real refresh/expiration advancement remain device acceptance tests. The upstream GSA fix is integrated; HTTP 429 resolution and universal login success are not claimed. Each candidate's immutable commit, run, hash, UUIDs and matching dSYMs belong to that candidate only.

