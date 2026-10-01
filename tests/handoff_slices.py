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
TOKEN_MEMBER = "    static func isValidToken("


def token_validator(source: str) -> str:
    """The canonical-token gate on its own.

    It lives inside an enum whose closing brace is hundreds of lines below it, so
    slicing the enclosing type would leave a brace open. A harness only needs the
    predicate, wrapped in a type of its own so it is valid at file scope.
    """
    start = source.index(TOKEN_MEMBER)
    member = source[start:source.index("\n    }\n", start) + len("\n    }\n")]
    return "enum V3TokenValidator {\n" + member + "}\n"
