# Contributing

Start with the [architecture](docs/README.md) and [build instructions](ci/README.md). Application implementation
lives in the pinned upstream repositories; this repository owns build, packaging
and validation tooling.

## Changes and tests

- Explain the problem and the exact source/build affected.
- Run the focused tests for every changed tool, plus `git diff --check`.
- Use `python3 -B -m unittest discover -s tests -p 'test_NAME.py' -v` for a
  focused Python suite, including domain subdirectories. See the
  [test guide](tests/README.md) for domain commands and coverage scope.
  Native suites also need their documented macOS/Xcode,
  Swift or pinned-source prerequisites; a skipped test is not a pass.
- Use the matching GitHub Actions checks for the final commit. Repository-wide
  discovery also includes historical/platform-specific tests and is not a
  substitute for the current required workflow gates.
- Keep packaging/source identity checks and immutable evidence intact. Historical
  records refer to their recorded revisions, not necessarily the current layout.
- Distinguish CI evidence from real-device results. Never claim a release is
  qualified by a different commit's successful build.

## Reporting a problem

Include the release/build, device model, iOS version, action attempted and the
exact sanitized error. For refresh problems, include the final history result
and whether Wi-Fi and LocalDevVPN were connected. Do not reset pairing or account
state merely to collect diagnostics.

Never post credentials, pairing files, private keys, signing material, full
personal logs or unnecessary device/network identifiers. See [security reporting](SECURITY.md).

## License

Repository-authored code uses the [MIT License](LICENSE) unless stated otherwise.
Upstream components retain their own licenses and [third-party notices](THIRD_PARTY_NOTICES.md).
