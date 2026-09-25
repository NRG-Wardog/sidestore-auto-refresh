"""Coverage for catalog request failure classification and propagation.

The physical defect this file locks down: a source from issue #38 could be
added and appeared in Sources, but opening it failed with "SideStore could not
start or complete the requested action", which is the generic command-stage
message. That message is produced on both sides of the boundary:
- host: V3ServiceBridge minted stage=.command for unavailable, XPC, busy,
  timeout and invalid-response failures;
- service: the catalog Core Data read already had a typed failure, but a
  pre-query rejection returned an idless token.

Rules enforced here:
- A catalog request keeps its operation context on both sides of the boundary.
- Each boundary gets its own typed classification instead of a generic message.
- The source manifest is never blamed unless manifest parsing actually failed.
- Diagnostics are privacy-safe: no source identifier, URL, app names, bundle
  identifiers, credentials, pairing records, or filesystem paths.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "scripts/templates/v3_service_bridge.swift"
SERVICE = ROOT / "scripts/templates/v3_sidestore_service.swift"
SHELL = ROOT / "scripts/templates/v3_unified_shell.swift"
FAILURE = ROOT / "scripts/templates/combined_failure.swift"



def normalized(text: str) -> str:
    """Collapse whitespace so assertions do not depend on line wrapping."""
    return re.sub(r"\s+", " ", text)


def uncommented(text: str) -> str:
    """Strip // comments so prose about a rule is not read as the rule."""
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())

def bridge():
    return BRIDGE.read_text(encoding="utf-8")


def service():
    return SERVICE.read_text(encoding="utf-8")


def catalog_view() -> str:
    text = SHELL.read_text(encoding="utf-8")
    start = text.index("struct V3CatalogView")
    return text[start:start + 14000]


def request_function() -> str:
    text = bridge()
    start = text.index("public func request(operation: String")
    return text[start:text.index("public func disconnected()", start)]


class HostBridgePropagationTests(unittest.TestCase):
    """Item 15: the host side must retain the operation context."""

    def test_request_correlation_is_minted_before_connecting(self):
        body = request_function()
        # A failure before dispatch must still be attributable to the caller.
        self.assertLess(body.index("let id = UUID().uuidString"), body.index("try await connect()"))

    def test_pre_connect_failure_keeps_its_own_truthful_operation(self):
        body = request_function()
        start = body.index("do {\n            try await connect()")
        block = body[start:body.index("if mutation {", start)]
        self.assertIn("if error is CancellationError { throw CancellationError() }", block)
        self.assertIn("V3CatalogRequestContext.annotating(error, requestedOperation: operation, requestID: id)", block)
        # operation=connect is what proves no mutation ran; it is never rewritten.
        self.assertIn("guard combined.operation != requestedOperation else { return combined }", bridge())

    def test_catalog_boundaries_use_the_catalog_stage(self):
        text = bridge()
        self.assertIn("operation == \"catalog\" ? .catalog : .command", text)
        body = request_function()
        # Timeout, undecodable reply, and bad envelope all report catalog stage.
        flat = normalized(body)
        self.assertIn("stage: V3CatalogRequestContext.hostStage(for: operation), code: .timedOut", flat)
        self.assertIn("stage: V3CatalogRequestContext.hostStage(for: operation), code: .invalidResponse", flat)
        # Both invalid-response boundaries carry the catalog stage.
        self.assertEqual(flat.count("stage: V3CatalogRequestContext.hostStage(for: operation), code: .invalidResponse"), 2)

    def test_a_well_formed_reply_with_a_foreign_id_stays_stale_result(self):
        body = normalized(request_function())
        self.assertIn('guard decoded["id"] as? String == id else { throw CombinedFailure(operation: operation, stage: .command, code: .staleResult, id: id) }', body)
        # Malformed and mismatched replies are no longer conflated.
        self.assertNotIn('as? [String: Any], decoded["id"]', body)
        self.assertIn('as? [String: Any] else { throw CombinedFailure(operation: operation, stage: V3CatalogRequestContext.hostStage(for: operation), code: .invalidResponse', body)

    def test_missing_client_is_retryable_for_a_read(self):
        body = request_function()
        self.assertIn("code: .interrupted, id: id, retryable: V3WireContract.readOperations.contains(operation)", body)

    def test_plain_service_error_tokens_are_typed_not_vocabulary_losing(self):
        text = bridge()
        start = text.index("static func hostFailure(")
        block = text[start:text.index("\n    }", start)]
        for token, code in (("notReady", ".notReady"), ("busy", ".busy"),
                            ("responseTooLarge", ".invalidResponse"),
                            ("invalidRequest", ".invalidConfiguration"),
                            ("cancelled", ".cancelled")):
            self.assertIn(f'case "{token}":', block)
            self.assertIn(f"code: {code}", block)
        # notReady is service startup, not a generic command boundary.
        self.assertIn("stage: .serviceReadiness", block)
        # No bare numeric comparison.
        self.assertNotIn("errorCode ==", block)
        self.assertNotIn("== 29", block)

    def test_read_timeout_retirement_behaviour_is_preserved(self):
        body = request_function()
        self.assertIn("timeouts[id] = Task { @MainActor in", body)
        self.assertIn("RefreshHandler.shared.v3_stopService()", body)
        self.assertIn("onCancel: {", body)


class ServiceSidePropagationTests(unittest.TestCase):
    """Item 15: the service must return typed, correlated failures."""

    def test_invalid_request_is_correlated_when_the_envelope_is_well_formed(self):
        text = service()
        start = text.index("private func receive(")
        block = text[start:text.index("completed = completed.filter", start)]
        # Both rejection paths ask the same correlated builder.
        self.assertEqual(block.count("encode(invalidRequestReply(for: data))"), 2)
        builder_start = text.index("private func invalidRequestReply(")
        builder = text[builder_start:text.index("private func encode(", builder_start)]
        self.assertIn('"error": "invalidRequest"', builder)
        self.assertIn("code: .invalidConfiguration, id: id", builder)
        # Only trusted envelope fields are echoed back, never the payload.
        self.assertIn('V3WireContract.operations.contains($0)', builder)
        self.assertIn("UUID(uuidString: $0) != nil", builder)
        for forbidden in ("payload", "target", "deadline"):
            self.assertNotIn(f'["{forbidden}"]', builder)

    def test_oversized_reply_is_a_typed_invalid_response(self):
        text = service()
        start = text.index("private func encode(")
        block = text[start:text.index("private func run(", start)]
        self.assertIn('"error": "responseTooLarge"', block)
        self.assertIn("code: .invalidResponse", block)
        # The failure never carries response content.
        self.assertNotIn("apps", block)
        self.assertNotIn("result", block)

    def test_encode_call_sites_preserve_the_operation(self):
        text = normalized(service())
        head, _, tail = text.partition("completed = completed.filter")
        calls = re.findall(r"encode\(", tail)
        forwarded = re.findall(r"operation: operation\)", tail)
        # Every reply emitted after the operation is bound forwards it, so an
        # oversized response is never misattributed to a generic command. The
        # only exception is the pre-validation rejection, which has no trusted
        # operation to forward and carries it inside the failure envelope.
        self.assertEqual(len(calls), len(forwarded) + 1,
                         f"{len(calls)} encode calls but {len(forwarded)} forward the operation")
        self.assertIn("encode(invalidRequestReply(for: data))", head)
        # The oversize fallback must not recurse.
        encode_start = text.index("private func encode(")
        encode = text[encode_start:text.index("private func run(", encode_start)]
        self.assertEqual(encode.count("encode("), 1,
                         "the oversize fallback must serialize directly, not recursively")

    def test_catalog_core_data_failure_is_unchanged(self):
        text = service()
        self.assertIn('} else if operation == "catalog" {', text)
        self.assertIn('operation: "catalog", stage: .catalog, code: .failed', text)
        self.assertIn("safeCause: .catalogUnavailable, sourceStep: .catalogRead", text)

    def test_catalog_query_records_privacy_safe_facts(self):
        text = service()
        # The catalog query is the last `case "catalog":` in the file.
        start = text.rindex("case \"catalog\":")
        block = text[start:start + 3000]
        self.assertIn("[V3_CATALOG] RESULT operation=catalog stage=catalogRead", block)
        for fact in ("source_found=", "source_identifier_match=", "catalog_row_count=", "has_more=", "cursor=", "request_id="):
            self.assertIn(fact, block)
        # Nothing identifying the source or its apps may be logged.
        for forbidden in ("target)", "sourceURL", "bundleIdentifier", "localizedDescription", "absoluteString"):
            self.assertNotIn(forbidden, block.split("debugLog(")[1].split("\n")[0],
                             "the catalog result line must stay privacy-safe")

    def test_source_add_persistence_is_untouched(self):
        text = service()
        self.assertIn('"sourceAddConfirmed"', text)
        # The verified-result response for an added source is preserved.
        self.assertIn("sourcePersistenceUnverified", text)
        self.assertNotIn("V3AuthSessionSnapshot", text.replace("V3_AUTH_SESSION_SNAPSHOT_V1", ""))


class CatalogFailureMessageTests(unittest.TestCase):
    """Item 15: each classified failure gets its own honest sentence."""

    def test_catalog_stage_names_every_boundary(self):
        text = FAILURE.read_text(encoding="utf-8")
        start = text.index("case .catalog:")
        block = text[start:start + 1600]
        for message in ("The SideStore service is not ready to load this source yet.",
                        "The connection to the SideStore service was interrupted while loading the source.",
                        "SideStore is still finishing another operation. Wait a moment, then reload the source.",
                        "SideStore returned an unreadable response while loading the source catalog.",
                        "SideStore could not read this source's saved catalog."):
            self.assertIn(message, text)

    def test_manifest_is_not_blamed_for_a_catalog_read_failure(self):
        text = FAILURE.read_text(encoding="utf-8")
        start = text.index("case .catalog:")
        block = text[start:start + 1600]
        block = uncommented(block)
        for forbidden in ("manifest", "source returned data", "invalid source"):
            self.assertNotIn(forbidden, block,
                             f"a catalog read failure must not claim a {forbidden} problem")

    def test_recovery_is_specific_to_the_boundary(self):
        text = FAILURE.read_text(encoding="utf-8")
        self.assertIn("Wait for SideStore to finish starting, then reload the source.", text)
        self.assertIn("Wait for the current SideStore operation to finish, then reload the source.", text)

    def test_generic_command_message_is_not_used_for_a_catalog_read(self):
        text = FAILURE.read_text(encoding="utf-8")
        generic = "SideStore could not start or complete the requested "
        # The generic sentence is reachable only from the command stage.
        command_stage = text[text.index("case .command:"):]
        self.assertIn(generic, command_stage)
        # A catalog request selects its wording from the operation, before any
        # stage is consulted, so the generic branch is unreachable for it.
        message = text[text.index("public var message: String {"):]
        self.assertIn('if operation == "catalog", let catalog = catalogFailureMessage { return catalog }',
                      message)
        self.assertLess(message.index('if operation == "catalog"'),
                        message.index("switch stage {"))


class CatalogViewValidationTests(unittest.TestCase):
    """Items 14 and 16: the view must not hide or invent a failure."""

    def test_cancellation_is_not_presented_as_a_catalog_failure(self):
        view = catalog_view()
        self.assertIn("catch is CancellationError {", view)
        cancel = view[view.index("catch is CancellationError {"):]
        cancel = cancel[:cancel.index("} catch {")]
        self.assertIn("error = nil", cancel)
        self.assertIn("failure = nil", cancel)

    def test_pages_are_validated_instead_of_coerced(self):
        view = catalog_view()
        self.assertNotIn('result["apps"] as? [[String: Any]] ?? []', view)
        self.assertNotIn('result["nextCursor"] as? Int ?? -1', view)
        self.assertIn('guard let rawApps = result["apps"] as? [[String: Any]] else', view)
        self.assertIn("CFGetTypeID(number) != CFBooleanGetTypeID()", view)
        self.assertIn("guard page.count == rawApps.count else", view)
        self.assertIn("guard next == -1 || next > cursor else", view)

    def test_invalid_page_reports_a_correlated_typed_failure(self):
        view = catalog_view()
        start = view.index("private func catalogResponseFailure(")
        block = normalized(view[start:start + 700])
        self.assertIn('operation: "catalog", stage: .catalog, code: .invalidResponse', block)
        self.assertIn("safeCause: .catalogUnavailable, sourceStep: .catalogRead", block)
        self.assertIn("annotatingCatalogPage(cursor: cursor)", block)

    def test_every_visible_catalog_failure_offers_diagnostics(self):
        view = catalog_view()
        self.assertIn('DisclosureGroup("Technical details")', view)
        self.assertIn('Button("Copy Diagnostics")', view)
        self.assertIn("V3OperationFailureDetails(combined)", view)

    def test_host_request_context_is_appended_to_diagnostics(self):
        text = FAILURE.read_text(encoding="utf-8")
        self.assertIn("public var requestContext: String?", text)
        self.assertIn("private var requestContextSuffix: String {", text)
        self.assertIn("request_operation=\\(requestedOperation) request_correlation=\\(requestID)", text)
        # Host-only: it must never appear on the wire envelope.
        wire = text[text.index("public var wire: [String: Any]"):]
        wire = wire[:wire.index("public var encodedString")]
        self.assertNotIn("requestContext", wire)
        decoder = text[text.index("public static func decode("):]
        decoder = decoder[:decoder.index("public static func preserving")]
        self.assertNotIn("requestContext", decoder)


class CatalogPrivacyTests(unittest.TestCase):
    """Item 16: diagnostics stay privacy-safe."""

    def test_request_context_never_records_the_source_identifier(self):
        text = bridge()
        start = text.index("static func annotating(")
        block = text[start:text.index("\n}", start)]
        self.assertNotIn("target", block)
        self.assertNotIn("source", block.lower().replace("requestedOperation", ""))

    def test_catalog_page_context_records_only_the_offset(self):
        text = FAILURE.read_text(encoding="utf-8")
        start = text.index("public mutating func annotatingCatalogPage(")
        block = text[start:text.index("}", text.index("source_step=catalogRead page_cursor", start))]
        self.assertIn("page_cursor=", block)
        for forbidden in ("identifier", "url", "bundleID", "apps"):
            self.assertNotIn(forbidden, block)

    def test_no_catalog_log_line_contains_content(self):
        for path, needle in ((SERVICE, "[V3_CATALOG]"),):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if needle in line:
                    self.assertNotIn("target)", line)
                    self.assertNotIn("absoluteString", line)
                    self.assertIsNone(re.search(r"(appName|bundleID|appleID|udid|pairing)\b", line))


if __name__ == "__main__":
    unittest.main()
