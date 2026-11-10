import Foundation
import Security
let certificate = try Data(contentsOf: URL(fileURLWithPath: "synthetic-cert.der"))
let attributes = try Data(contentsOf: URL(fileURLWithPath: "signed-attributes.der"))
let signature = try Data(contentsOf: URL(fileURLWithPath: "cms-signature.bin"))
guard let cert = SecCertificateCreateWithData(nil, certificate as CFData),
      let key = SecCertificateCopyKey(cert) else { fatalError("Synthetic certificate import failed") }
var error: Unmanaged<CFError>?
guard SecKeyVerifySignature(key, .rsaSignatureMessagePKCS1v15SHA256, attributes as CFData,
                            signature as CFData, &error) else { fatalError("CMS signed-attribute signature invalid") }
var altered = attributes; altered.append(0)
error = nil
guard !SecKeyVerifySignature(key, .rsaSignatureMessagePKCS1v15SHA256, altered as CFData,
                             signature as CFData, &error) else { fatalError("Changed signed attributes accepted") }
var trust: SecTrust?
guard SecTrustCreateWithCertificates(cert, SecPolicyCreateBasicX509(), &trust) == errSecSuccess,
      let trust else { fatalError("Synthetic trust evaluation setup failed") }
guard SecTrustSetNetworkFetchAllowed(trust, false) == errSecSuccess else { fatalError("Network trust fetching not disabled") }
var trustError: CFError?
guard !SecTrustEvaluateWithError(trust, &trustError) else { fatalError("Synthetic Apple chain unexpectedly trusted") }
print("PASS actual-ZSign-CMS-signature-and-tamper-untrusted-chain")
