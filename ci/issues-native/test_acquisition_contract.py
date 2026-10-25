"""Generate a no-network Swift metadata test from the exact acquired compiler inputs."""
from pathlib import Path
import argparse
import hashlib
import json
import re
parser = argparse.ArgumentParser()
parser.add_argument('--sign', required=True)
parser.add_argument('--store', required=True)
parser.add_argument('--host', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
sign = Path(args.sign).read_text()
ss = Path(args.store).read_text()
host = Path(args.host).read_text()
for token in ('public enum AnisetteError:', '\npublic final class AnisetteDataManager:'):
    if sign.count(token) != 1:
        raise ValueError('Nonunique SideSign extraction anchor')
for token in ('    else if let anisette = error as? SideSign.AnisetteError {',
              '    else if let anisette = error as? AnisetteKit.AnisetteError'):
    if ss.count(token) != 1:
        raise ValueError('Nonunique SideStore extraction anchor')
pattern = r'    enum AcquisitionFailure: String \{.*?\n    \}'
store_enums, host_enums = re.findall(pattern, ss, re.S), re.findall(pattern, host, re.S)
if len(store_enums) != 1 or store_enums != host_enums:
    raise ValueError('Producer/consumer acquisition subtype mismatch')
for text in (ss, host):
    if text.count('            case "typed_error":') != 1:
        raise ValueError('Nonunique typed-error validator anchor')
store_guard = ss.split('            case "typed_error":')[1].split('            default: return nil')[0]
host_guard = host.split('            case "typed_error":')[1].split('            default: return nil')[0]
if store_guard != host_guard:
    raise ValueError('Producer/consumer typed-error validator mismatch')
error=sign.split('public enum AnisetteError:')[1].split('\npublic final class AnisetteDataManager:')[0]
acq=re.search(r'    enum AcquisitionFailure: String \{.*?\n    \}',ss,re.S).group()
block=ss.split('    else if let anisette = error as? SideSign.AnisetteError {')[1].split('    else if let anisette = error as? AnisetteKit.AnisetteError')[0]
switch=block[block.index('        switch anisette'):].rsplit('    }',1)[0]
validator = ss.split('            case \"typed_error\":')[1].split('            default: return nil')[0].strip()
validator = validator.replace('else { return nil }', 'else { return false }')
source='import Foundation\n\nenum SideSign { public enum AnisetteError:'+error+'\n}\nstruct V3AccountOperationError {\n'+acq+'\n}\n'+'''func project(_ anisette: SideSign.AnisetteError) -> [String: String] {
    var httpStatus: Int?
    var acquisitionFailure: V3AccountOperationError.AcquisitionFailure?
'''+switch+'''    return ["typed_error": acquisitionFailure?.rawValue ?? "anisetteFailure",
            "http_status": httpStatus.map(String.init) ?? "unavailable", "server_code": "unknown"]
}
func validType(_ text: String) -> Bool {
    guard text.utf8.count <= 512 else { return false }
'''+validator+'''
    return true
}
let secret = "SECRET_PASSWORD https://private.example/token?credential=SECRET_TOKEN"
let cases: [(SideSign.AnisetteError, String, String)] = [
    (.serverListHTTPFailure(statusCode:403), "anisetteAcquisitionServerListHTTP", "403"),
    (.odaMetadataHTTPFailure(statusCode:503), "anisetteAcquisitionMetadataHTTP", "503"),
    (.archiveHTTPFailure(statusCode:404), "anisetteAcquisitionArchiveHTTP", "404"),
    (.archiveHTTPFailure(statusCode:-1), "anisetteAcquisitionArchiveHTTP", "unavailable"),
    (.serverListHTTPFailure(statusCode:600), "anisetteAcquisitionServerListHTTP", "unavailable"),
    (.sha256Mismatch(expected:secret, actual:secret), "anisetteAcquisitionChecksumMismatch", "unavailable"),
    (.missingODAEntry, "anisetteAcquisitionMissingMetadata", "unavailable"),
    (.invalidBase64Payload, "anisetteAcquisitionInvalidPayload", "unavailable"),
    (.decompressionFailed(secret), "anisetteAcquisitionExtractionFailed", "unavailable"),
    (.missingRequiredLibs([secret]), "anisetteAcquisitionMissingLibraries", "unavailable"),
    (.providerNotReady(secret), "anisetteAcquisitionProviderNotReady", "unavailable"),
    (.downloadFailed(secret), "anisetteAcquisitionDownloadFailed", "unavailable"),
    (.invalidAnisetteData, "anisetteFailure", "unavailable")
]
for (error, kind, status) in cases {
    let fields = project(error)
    precondition(fields["typed_error"] == kind)
    precondition(fields["http_status"] == status)
    let data = try JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
    let text = String(decoding: data, as: UTF8.self)
    precondition(!text.contains("SECRET") && !text.contains("private.example"))
    let decoded = try JSONSerialization.jsonObject(with: data) as? [String:String]
    precondition(decoded == fields)
}
let factories: [(Int) -> SideSign.AnisetteError] = [
    { .serverListHTTPFailure(statusCode: $0) },
    { .odaMetadataHTTPFailure(statusCode: $0) },
    { .archiveHTTPFailure(statusCode: $0) }
]
for factory in factories {
    for status in [99, 100, 599, 600] {
        let result = project(factory(status))
        precondition(result["http_status"] == ((100...599).contains(status) ? String(status) : "unavailable"))
        precondition(validType(result["typed_error"]!))
    }
}
for (_, kind, _) in cases { precondition(validType(kind)) }
precondition(!validType(secret))
precondition(!validType(String(repeating: "x", count: 513)))
precondition(!validType("anisetteAcquisitionUnexpected"))
precondition(V3AccountOperationError.AcquisitionFailure(rawValue: secret) == nil)
print("PASS: extracted mapping and typed allowlist, 13 fake errors, all HTTP boundary families, no-payload dictionary serialization (not full wire roundtrip)")
'''
output = Path(args.output)
output.write_text(source)
output.with_suffix('.sources.json').write_text(json.dumps({
    'scope': 'Extracted error enum and mapper only; no target library execution or authentication',
    'input_sha256': {name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                     for name, path in [('SideSign', args.sign), ('SideStore', args.store), ('LiveContainer', args.host)]},
    'fixture_sha256': hashlib.sha256(source.encode()).hexdigest()
}, indent=2) + '\n')
