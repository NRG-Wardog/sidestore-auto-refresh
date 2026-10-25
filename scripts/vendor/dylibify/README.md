# Reviewed source-built dylibify

Source: https://github.com/lgq2015/dylibify at commit
`5daf713df9fb07724510490c88aa8e00061be14a`, tree
`1c4f323e269ba3834b28b0a1b0d17c09f96b6926`.
Original main.m SHA-256:
`0c30a8bd4088aa891eb172c5d3a3e185cc9d00572b60c7b29f30e08c6245acd8`.
The MIT license is preserved alongside the source.

This is a reviewed source replacement for the unavailable LiveContainer/dylibify
1.0 executable, not a claim of byte equivalence to that release. It converts the
locally built SideStore Mach-O into a loadable framework. The packager compiles
it with Apple's clang and Foundation; it downloads no converter binary.

Local hardening bounds and zero-initializes LC_ID_DYLIB storage using the UTF-8
name length, writes a name in every architecture, checks allocation/file I/O,
and propagates conversion failure. Packaging additionally verifies thin arm64,
load-command bounds, the single dylib identity replacing PAGEZERO, zeroed
padding, unchanged remaining commands, and shifted chained-fixup segment table.
Native regression tests execute this source on macOS with a chained-fixup
fixture, missing input, and truncated input.

Review found no network access, credential collection, subprocess launching,
or persistence behavior. This scoped source review is not a malware-free
guarantee, and does not reverse-engineer inherited third-party binary libraries.
