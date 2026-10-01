"""Shared source slices for the secure-handoff harnesses.

Several harnesses compile a part of v3_secret_handoff.swift rather than all of
it, because the module's SecItem calls need a real Keychain. Keeping the slice
arithmetic in one place stops an access-level change or a reordered declaration
from silently breaking a harness boundary: each of those broke a harness once
already, and each breakage only showed on the macOS runner.

Every block below is delimited by a declaration, never by a line prefix, so
`enum X` and `public enum X` are the same boundary.
"""
from __future__ import annotations

LOCK_DECLARATION = "enum V3AppGroupProcessLock {"
FAILURE_DECLARATION = "public enum V3SecretHandoffFailure"
POLICY_DECLARATION = "public enum V3SecretHandoffFailurePolicy"
ROLE_COMMENT = "/// Which side of the handoff is running."
ERROR_DECLARATION = "public enum V3SecretHandoffError"
ADMISSION_COMMENT = "/// Serializes the full shared-Keychain"
FAIL_EXTENSION = "extension V3SecretHandoffError {\n    /// Builds a typed handoff failure"
RECORD_DECLARATION = "enum V3SecretHandoffRecord {"
TOKEN_DECLARATION = "enum V3SecretHandoff {"
TOKEN_STORE_MEMBER = "    static func storeString("


def _block(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    return source[start:source.index(end_marker, start)]


def lock(source: str) -> str:
    """Just the process-shared lock type."""
    start = source.index(LOCK_DECLARATION)
    return source[start:source.index(FAILURE_DECLARATION, start)]


def typed_blocks(source: str) -> str:
    """The failure taxonomy, diagnostics, trace and role, without the policy.

    The classification policy builds CombinedFailures, so harnesses that do not
    compile the service leave it out. `with_policy` adds it back.
    """
    return "\n".join([
        _block(source, FAILURE_DECLARATION, POLICY_DECLARATION),
        _block(source, ROLE_COMMENT, ERROR_DECLARATION),
    ])


def error_blocks(source: str) -> str:
    """The error type, its user-facing text and the typed failure constructor."""
    return "\n".join([
        _block(source, ERROR_DECLARATION, ADMISSION_COMMENT),
        _block(source, FAIL_EXTENSION, RECORD_DECLARATION),
    ])


def policy_block(source: str) -> str:
    """The classification policy, which needs CombinedFailure."""
    return _block(source, POLICY_DECLARATION, ROLE_COMMENT)


def without_policy(source: str) -> str:
    """Everything a lock harness composes with: taxonomy plus error, no policy."""
    return "\n".join([typed_blocks(source), error_blocks(source)])


def token_validator(source: str) -> str:
    """The canonical-token gate, without the SecItem bodies that follow it.

    A malformed token must be rejected before it is ever used as an account, so a
    harness that models the transport needs this exact predicate rather than its
    own idea of what a token looks like.
    """
    start = source.index(TOKEN_DECLARATION)
    return source[start:source.index(TOKEN_STORE_MEMBER, start)]
