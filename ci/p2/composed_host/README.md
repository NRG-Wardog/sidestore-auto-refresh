# Composed P2 host gate

Builds exact e22de6df/4b973e2b with the verified P2 UI plus exact P0 initializer
support and renderer. SideStore59fa3 and AnisetteKit161435 sources are independently
verified before and after execution. Five maintained consumer tests must execute
with zero failures, errors or skips, including actual Swift parser/wire/host-module
runs and C++ formatter output. The unchanged genuine simulator build/resource
checks follow. Prior P0 native captures and P2 visual artifacts remain historical
and are not relabeled. This is a new composition proof, not release admission,
physical-device authentication, or IPA verification.

The exact pinned SideStore peer contains a committed dangling generated SideBackup.ipa link. Its path, blob and raw relative target are authenticated and its target must remain absent, with no linked ancestors or tracked build paths. It is recorded as unconsumed; no resource is followed or synthesized. All other link resolution remains strict. This peer is source input to the five initializer tests, not the app compiled by this gate.
