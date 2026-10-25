"""Extract actual finite auth-response code; fake data only, no network/target execution."""
import argparse, hashlib, json, re
from pathlib import Path
p=argparse.ArgumentParser()
for name in ('errors','auth','store','host','shell','output'): p.add_argument('--'+name,required=True)
a=p.parse_args()
inputs={k:Path(getattr(a,k)).read_text() for k in ('errors','auth','store','host','shell')}
def between(s,start,end):
    if s.count(start)!=1: raise ValueError('Nonunique extraction anchor: '+start)
    return s.split(start,1)[1].split(end,1)[0]
def block(s,anchor):
    if s.count(anchor)!=1: raise ValueError('Nonunique block anchor: '+anchor)
    start=s.index(anchor); brace=s.index('{',start); depth=1; pos=brace+1
    while depth:
        if s[pos]=='{': depth+=1
        elif s[pos]=='}': depth-=1
        pos+=1
    return s[start:pos]
context=block(inputs['errors'],'public struct AuthenticationResponseContext:')
errors=block(inputs['errors'],'public enum ServerError:')
parser=block(inputs['auth'],'private func parsePlistOrJSON(').replace('private func','func',1)
strict_parser=block(inputs['auth'],'private func parseILoaderPlist(').replace('private func','func',1)
selector_start='        let decodedResponse = ILoaderRemoteAuthentication.current == nil'
selector_end='        guard let responseDictionary = decodedResponse else {'
if inputs['auth'].count(selector_end) != 1: raise ValueError('Unique anchor missing: '+selector_end)
selector=selector_start+between(inputs['auth'],selector_start,selector_end)
if selector.count('parsePlistOrJSON(data, observation: observation)') != 1 or selector.count('parseILoaderPlist(data, observation: observation)') != 1:
    raise ValueError('Actual parser selection closure differs')
guard=block(inputs['auth'],'guard let responseDictionary = decodedResponse else {')
if 'rawPayload: "[redacted]"' not in guard: raise ValueError('Outer parse error is not redacted')
enums=[]
for name in ('AuthResponseMIME','AuthResponseShape'):
    x=block(inputs['store'],'enum '+name+':')
    if x!=block(inputs['host'],'enum '+name+':'): raise ValueError('Enum mirror differs')
    enums.append(x)
mapper=between(inputs['store'],'        case .invalidResponseFormat(_, let context):','        case .missingKey:')
validators=between(inputs['store'],'            case "auth_response_mime":','            case "provider_code":')
if validators!=between(inputs['host'],'            case "auth_response_mime":','            case "provider_code":'): raise ValueError('Validator mirror differs')
validators='case "auth_response_mime":'+validators
renderer=between(inputs['store'],'        let accountDetails =','        let nativeDetails =')
if renderer!=between(inputs['host'],'        let accountDetails =','        let nativeDetails ='): raise ValueError('Renderer mirror differs')
emission=between(inputs['store'],'        if kind == .sideSignInvalidResponse, let authResponseMIME {','        if kind == .anisetteKitADIError, let nativeEvidence {')
emission='if kind == .sideSignInvalidResponse, let authResponseMIME {'+emission
observer='''
enum AuthenticationDiagnosticTrace {
 enum Outcome { case parse }
 final class Request {
  func parser(plist: Any?, json: Any?, selected: String) {}
  func finish(_ outcome: Outcome) {}
 }
}
'''
source='import Foundation\nimport CoreFoundation\n'+observer+'enum ILoaderRemoteAuthentication { static let current: Int? = nil }\n'+context+'\n'+errors+'\n'+parser+'\n'+strict_parser+'''
func outer(_ data: Data, _ statusCode: Int, _ mime: String?) throws -> [String: any Sendable] {
    let observation: AuthenticationDiagnosticTrace.Request? = nil
    struct Response { let mimeType: String? }; let response = Response(mimeType: mime)
'''+selector+guard+'''
    return responseDictionary
}
struct V3AccountOperationError {
'''+ '\n'.join(enums)+'''
}
enum Kind: String { case sideSignInvalidResponse }
func project(_ error: ServerError, priorStatus: Int? = nil) -> [String:String] {
 var httpStatus = priorStatus
 var kind = Kind.sideSignInvalidResponse
 var authResponseShape: V3AccountOperationError.AuthResponseShape?
 var authResponseMIME: V3AccountOperationError.AuthResponseMIME?
 switch error {
 case .invalidResponseFormat(_, let context):
'''+mapper+'''
 default: preconditionFailure("Unexpected test case")
 }
 var signingContext = ["typed_error":kind.rawValue,"http_status":httpStatus.map(String.init) ?? "unavailable","server_code":"unknown"]
'''+emission+'''
 return signingContext
}
func validate(_ fields:[String:String]) -> [String:String]? {
 guard fields.count <= 20 else { return nil }
 for (key,text) in fields {
  guard text.utf8.count <= 512 else { return nil }
  switch key {
'''+validators+'''
  case "typed_error": guard text == "sideSignInvalidResponse" else { return nil }
  case "server_code": guard text == "unknown" else { return nil }
  default: return nil
  }
 }
 return fields
}
func render(_ fields:[String:String]) -> String {
 let fields = validate(fields) ?? [:]
 let step = "appleAuthentication"
 let accountDetails ='''+renderer+'''
 return accountDetails + authResponseDetails
}
'''+r'''
let secret = "SECRET_PASSWORD_TOKEN_PRIVATE_ID"
let dictionary = ["dummy":secret]
let plist = try PropertyListSerialization.data(fromPropertyList:dictionary,format:.binary,options:0)
let xml = try PropertyListSerialization.data(fromPropertyList:dictionary,format:.xml,options:0)
let json = try JSONSerialization.data(withJSONObject:dictionary)
for data in [plist,xml,json] { for status in [200,503] { let accepted = try outer(data,status,"application/json"); precondition(accepted["dummy"] as? String == secret) } }
let arrayPlist = try PropertyListSerialization.data(fromPropertyList:[secret],format:.binary,options:0)
let scalarPlist = try PropertyListSerialization.data(fromPropertyList:secret,format:.binary,options:0)
let jsonArray = try JSONSerialization.data(withJSONObject:[secret])
let cases:[(Data,AuthenticationResponseContext.Shape?)] = [
 (arrayPlist,.plistArray),(scalarPlist,.plistScalar),(jsonArray,.jsonArray),
 (Data("null".utf8),nil),(Data("true".utf8),nil),
 (Data("<html><body>SECRET_PASSWORD_TOKEN_PRIVATE_ID</body></html>".utf8),.unrecognized),
 (Data("<?xml version=\"1.0\"?><not-a-plist/>".utf8),.unrecognized),
 (Data([0xff,0x00,0xfe]),.unrecognized)]
for (data,shape) in cases {
 do { _ = try outer(data,503,"text/html"); preconditionFailure("Accepted wrong root") }
 catch let error as ServerError {
   guard case .invalidResponseFormat(let payload,let context) = error else { preconditionFailure("Changed error") }
   precondition(payload == "[redacted]" && context != nil)
   if let shape { precondition(context!.shape == shape) }
   precondition(!error.description.contains(secret) && !error.localizedDescription.contains(secret) && !String(describing:error).contains(secret) && !String(describing:error as NSError).contains(secret))
   precondition((error as NSError).code == 1)
   let fields = project(error)
   precondition(fields["http_status"] == "503" && fields["typed_error"] == "sideSignInvalidResponse")
   let wire = try JSONSerialization.data(withJSONObject:fields,options:[.sortedKeys])
   let decoded = try JSONSerialization.jsonObject(with:wire) as! [String:String]
   precondition(validate(decoded) == fields)
   let displayed = render(decoded)
   precondition(displayed.contains("http_status=503") && displayed.contains("auth_response_shape=") && displayed.contains("auth_response_mime=html"))
   precondition(!displayed.contains(secret) && !String(decoding:wire,as:UTF8.self).contains(secret))
 }
}
for status in [99,100,200,429,503,599,600] {
 let context = AuthenticationResponseContext(data:Data([0xff]),httpStatus:status)
 let fields = project(.invalidResponseFormat(rawPayload:"[redacted]",responseContext:context),priorStatus:418)
 precondition(fields["http_status"] == ((100...599).contains(status) ? String(status):"418"))
}
let legacy = project(.invalidResponseFormat(rawPayload:"legacy"),priorStatus:418)
precondition(legacy["http_status"] == "418" && legacy["auth_response_shape"] == nil && legacy["auth_response_mime"] == nil)
let mimeCases:[(String?,AuthenticationResponseContext.MIME)] = [(nil,.unavailable),("text/html",.html),("application/xhtml+xml",.html),("TEXT/HTML",.html),("application/json",.json),("application/x-plist",.xmlOrPlist),("application/octet-stream",.other),("text/html; charset=utf-8",.other),(secret,.other)]
for (mime,expected) in mimeCases { precondition(AuthenticationResponseContext(data:Data(),httpStatus:200,mimeType:mime).mime == expected) }
for key in ["auth_response_shape","auth_response_mime"] {
 for value in [secret,String(repeating:"x",count:513),"unexpected"] {
  let fields = ["typed_error":"sideSignInvalidResponse",key:value]
  precondition(validate(fields) == nil && !render(fields).contains(value))
 }
 precondition(validate(["typed_error":"anisetteFailure",key:key == "auth_response_shape" ? "unrecognized":"html"]) == nil)
}
for value in ["99","600","0200",secret] { precondition(validate(["http_status":value]) == nil) }
print("PASS: actual parser/context/error/mapper and mirrored validator-render fragments; fake payloads only; not full CombinedFailure wire integration")
'''
# Throwing expressions cannot occur inside precondition's nonthrowing autoclosure.
source=source.replace('precondition(try outer(data,200,"application/json")["dummy"] as? String == secret)','let accepted = try outer(data,200,"application/json"); precondition(accepted["dummy"] as? String == secret)')

Path(a.output).write_text(source)
Path(a.output).with_suffix('.sources.json').write_text(json.dumps({'scope':'Extracted production fragments, not full CombinedFailure wire integration; fake data only','input_sha256':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in inputs.items()}},indent=2)+'\n')
