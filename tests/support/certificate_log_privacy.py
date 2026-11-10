"""Read-only lexical guards for serial-bearing backend diagnostic calls."""
import re

V3_CERTIFICATE_SERIAL_LOG_IDENTIFIERS = (
    "serialNumber", "serialHex", "serialNumDecimal", "installedAppSerial",
    "overrideCertSerial", "activeCertSerial", "targetSigningCertSerial",
    "portalCertificateSerials", "mainBundleCertSerial",
)


def contains_certificate_serial_log_identifier(call):
    folded = call.casefold()
    return any(identifier.casefold() in folded
               for identifier in V3_CERTIFICATE_SERIAL_LOG_IDENTIFIERS)


def swift_log_call_ranges(text):
    """Return complete debugLog/verboseLog calls, including interpolated strings."""
    pattern = re.compile(r"(?<![A-Za-z0-9_.])(?:self\.)?(?:debugLog|verboseLog)\s*\(")
    ranges = []
    for match in pattern.finditer(text):
        # The match includes the first string quote so Swift triple strings and
        # nested quoted values inside string interpolation are lexed correctly.
        opening = text.find("(", match.start(), match.end())
        if opening < 0:
            continue
        stack = [("code", 1, False)]  # mode, delimiter depth, triple-string flag
        index = opening + 1
        while index < len(text) and stack:
            mode, depth, triple = stack[-1]
            if mode == "string":
                if triple and text.startswith('"""', index):
                    stack.pop()
                    index += 3
                    continue
                if not triple and text[index] == '"':
                    stack.pop()
                    index += 1
                    continue
                if text[index] == "\\":
                    if index + 1 < len(text) and text[index + 1] == "(":
                        stack.append(("interpolation", 1, False))
                        index += 2
                    else:
                        index += min(2, len(text) - index)
                    continue
                index += 1
                continue

            # Code context: either the call argument or Swift string interpolation.
            if text.startswith("//", index):
                newline = text.find("\n", index + 2)
                index = len(text) if newline < 0 else newline + 1
                continue
            if text.startswith("/*", index):
                comment_depth = 1
                index += 2
                while index < len(text) and comment_depth:
                    if text.startswith("/*", index):
                        comment_depth += 1
                        index += 2
                    elif text.startswith("*/", index):
                        comment_depth -= 1
                        index += 2
                    else:
                        index += 1
                continue
            if text.startswith('"""', index):
                stack.append(("string", 0, True))
                index += 3
                continue
            if text[index] == '"':
                stack.append(("string", 0, False))
                index += 1
                continue
            if mode == "interpolation":
                if text[index] == "(":
                    stack[-1] = (mode, depth + 1, False)
                elif text[index] == ")":
                    if depth == 1:
                        stack.pop()
                    else:
                        stack[-1] = (mode, depth - 1, False)
                index += 1
                continue
            if text[index] == "(":
                stack[-1] = (mode, depth + 1, False)
            elif text[index] == ")":
                if depth == 1:
                    ranges.append((match.start(), index + 1, text[match.start():index + 1]))
                    break
                stack[-1] = (mode, depth - 1, False)
            index += 1
    return ranges
