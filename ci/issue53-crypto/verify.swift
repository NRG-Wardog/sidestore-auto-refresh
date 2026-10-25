import Foundation
import Security

let publicBytes = try Data(contentsOf: URL(fileURLWithPath: "public.der"))
let message = try Data(contentsOf: URL(fileURLWithPath: "message.bin"))
let signature = try Data(contentsOf: URL(fileURLWithPath: "signature.bin"))
var error: Unmanaged<CFError>?
let attributes: [String: Any] = [kSecAttrKeyType as String: kSecAttrKeyTypeRSA,
                                 kSecAttrKeyClass as String: kSecAttrKeyClassPublic,
                                 kSecAttrKeySizeInBits as String: 2048]
guard let key = SecKeyCreateWithData(publicBytes as CFData, attributes as CFDictionary, &error) else {
    fatalError("Synthetic public-key import failed")
}
guard SecKeyVerifySignature(key, .rsaSignatureMessagePKCS1v15SHA256, message as CFData,
                            signature as CFData, &error) else {
    fatalError("Independent synthetic signature verification failed")
}
error = nil
var changed = message
changed.append(0)
guard !SecKeyVerifySignature(key, .rsaSignatureMessagePKCS1v15SHA256, changed as CFData,
                             signature as CFData, &error) else {
    fatalError("Independent verifier accepted changed message")
}
print("PASS Apple-Security-signature-and-tamper")
