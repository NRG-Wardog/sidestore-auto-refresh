"""Generate a standalone macOS Swift test from the complete production helper.
URLProtocol fixtures prevent all real network access; no APK/library code runs.
Archive.Reader is a test double, so native app CI remains the real archive API gate.
"""
import sys,pathlib,hashlib,json
source=pathlib.Path(sys.argv[1]).read_text()
if source.count('import Crypto\n') != 1: raise ValueError('Unexpected crypto import')
source=source.replace('import Crypto\n','import CryptoKit\n')
preamble='''
import Foundation
import CryptoKit
enum AnisetteError: Error {
 case providerNotReady(String), missingRequiredLibs([String]), sha256Mismatch(expected:String,actual:String)
 case downloadFailed(String), decompressionFailed(String), archiveHTTPFailure(statusCode:Int)
}
enum Archive {
 struct Entry { let filename:String; let isDirectory:Bool; let externalAttributes:UInt32; let uncompressedSize:Int64; let compressedSize:Int64; let compressionMethod:UInt16 }
 final class Reader {
  static func open(at:URL) throws -> Reader { throw AnisetteError.decompressionFailed("fixture does not parse archives") }
  func entries() throws -> [Entry] { [] }
  func goToFirstFile() throws {}
  func currentEntry() throws -> Entry { fatalError("unreachable") }
  func readCurrentFile() throws -> Data { fatalError("unreachable") }
  func goToNextFile() -> Bool { false }
 }
}
'''
tests=r'''
func check(_ condition: Bool, _ name: String) throws {
 if !condition { throw NSError(domain: "VendorContract", code: 1, userInfo:[NSLocalizedDescriptionKey:name]) }
}
func rejects(_ name:String, _ body:() throws -> Void) throws {
 do { try body() } catch { return }
 throw NSError(domain:"VendorContract",code:2,userInfo:[NSLocalizedDescriptionKey:name])
}
final class FixtureProtocol: URLProtocol {
 static var status=200
 static var length: String?="142139820"
 static var encoding: String?=nil
 static var bodyBytes=0
 static var responseURL=VerifiedAppleMusicLibraries.source
 static var calls=0
 static var afterFirstChunk: (() -> Void)?
 override class func canInit(with request:URLRequest)->Bool { true }
 override class func canonicalRequest(for request:URLRequest)->URLRequest { request }
 override func startLoading() {
  Self.calls += 1
  var headers:[String:String]=[:]
  if let v=Self.length { headers["Content-Length"]=v }
  if let v=Self.encoding { headers["Content-Encoding"]=v }
  let response=HTTPURLResponse(url:Self.responseURL,statusCode:Self.status,httpVersion:"HTTP/1.1",headerFields:headers)!
  client?.urlProtocol(self,didReceive:response,cacheStoragePolicy:.notAllowed)
  let chunk=Data(repeating:0,count:1_048_576)
  var remaining=Self.bodyBytes
  var first=true
  while remaining > 0 {
   let count=min(remaining,chunk.count)
   client?.urlProtocol(self,didLoad:count==chunk.count ? chunk : Data(chunk.prefix(count)))
   remaining-=count
   if first { first=false; Self.afterFirstChunk?() }
  }
  client?.urlProtocolDidFinishLoading(self)
 }
 override func stopLoading() {}
}
func checkDownloadRoot(_ root:URL) throws {
 let attributes=try FileManager.default.attributesOfItem(atPath:root.path)
 try check(attributes[.type] as? FileAttributeType == .typeDirectory,"fixture root is not a directory")
 try check(FileManager.default.isWritableFile(atPath:root.path),"fixture root is not writable")
}
enum ExpectedDownloadFailure {
 case http(Int), metadata, incomplete, byteCap, cancellation, staging
}
func checkDownloadFailure(_ error:Error, expected:ExpectedDownloadFailure) throws {
 switch expected {
 case .cancellation:
  try check(error is CancellationError,"expected cancellation, got \(error)")
 case .http(let status):
  guard case .archiveHTTPFailure(let actual)? = error as? AnisetteError else {
   throw NSError(domain:"UnexpectedFailure",code:1,userInfo:[NSLocalizedDescriptionKey:"Expected HTTP rejection, got \(error)"])
  }
  try check(actual==status,"wrong HTTP status")
 default:
  guard case .downloadFailed(let message)? = error as? AnisetteError else {
   throw NSError(domain:"UnexpectedFailure",code:2,userInfo:[NSLocalizedDescriptionKey:"Expected download failure, got \(error)"])
  }
  let wanted:String
  switch expected {
   case .metadata: wanted="Unexpected vendor response metadata."
   case .incomplete: wanted="Incomplete vendor archive."
   case .byteCap: wanted="Vendor archive exceeds byte limit."
   case .staging: wanted="Cannot stage vendor download."
   default: fatalError("handled above")
  }
  try check(message==wanted,"wrong download failure: \(message)")
 }
}
func downloadRejects(_ root:URL, _ name:String, expected:ExpectedDownloadFailure, preCancelled:Bool=false) async throws {
 print("CASE \(name)")
 try checkDownloadRoot(root)
 let config=URLSessionConfiguration.ephemeral; config.protocolClasses=[FixtureProtocol.self]
 let target=root.appendingPathComponent(UUID().uuidString)
 let downloader=VendorAPKDownload(destination:target,configuration:config)
 let before=FixtureProtocol.calls
 if preCancelled { downloader.cancel() }
 var captured:Error?
 do { try await downloader.download() } catch { captured=error }
 guard let captured else { throw NSError(domain:"UnexpectedSuccess",code:3,userInfo:[NSLocalizedDescriptionKey:name]) }
 try checkDownloadFailure(captured,expected:expected)
 try check(FixtureProtocol.calls-before == (preCancelled ? 0 : 1),"fixture request count for \(name)")
 print("PASS \(name)")
}
func main() async throws {
 let root=FileManager.default.temporaryDirectory.appendingPathComponent("vendor-contract-"+UUID().uuidString)
 try FileManager.default.createDirectory(at:root,withIntermediateDirectories:false)
 defer { try? FileManager.default.removeItem(at:root) }
 let abc=Data("abc".utf8)
 let spec=VerifiedAppleMusicLibraries.Library(name:"fixture",size:3,sha256:"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
 try VerifiedAppleMusicLibraries.validate(abc,library:spec)
 try rejects("wrong bytes") { try VerifiedAppleMusicLibraries.validate(Data("abd".utf8),library:spec) }
 try rejects("wrong size") { try VerifiedAppleMusicLibraries.validate(Data(),library:spec) }
 try rejects("missing cache") { try VerifiedAppleMusicLibraries.validateCache(root) }
 let link=root.appendingPathComponent("link")
 try FileManager.default.createSymbolicLink(at:link,withDestinationURL:root)
 try rejects("symlink cache") { try VerifiedAppleMusicLibraries.validateCache(link) }
 let malformed=root.appendingPathComponent("malformed.apk")
 try Data("PK".utf8).write(to:malformed)
 try rejects("small malformed APK") { try VerifiedAppleMusicLibraries.extract(archive:malformed,into:root) }
 for status in [206,302,403,500] {
  FixtureProtocol.status=status
  try await downloadRejects(root,"HTTP status \(status)",expected:.http(status))
 }
 FixtureProtocol.status=200
 for length in [nil,"142139819","142139821"] as [String?] {
  FixtureProtocol.length=length
  try await downloadRejects(root,"length",expected:.metadata)
 }
 FixtureProtocol.length="142139820"; FixtureProtocol.encoding="gzip"
 try await downloadRejects(root,"encoding",expected:.metadata)
 FixtureProtocol.encoding=nil; FixtureProtocol.responseURL=URL(string:"https://example.invalid/archive")!
 try await downloadRejects(root,"unexpected final URL",expected:.metadata)
 FixtureProtocol.responseURL=VerifiedAppleMusicLibraries.source
 try await downloadRejects(root,"truncated stream",expected:.incomplete)
 let before=FixtureProtocol.calls
 try await downloadRejects(root,"pre-start cancellation",expected:.cancellation,preCancelled:true)
 try check(FixtureProtocol.calls==before,"cancelled transfer started")
 let publishRoot=root.appendingPathComponent("publish")
 try FileManager.default.createDirectory(at:publishRoot,withIntermediateDirectories:false)
 func validateFixture(_ directory:URL) throws {
  let data=try Data(contentsOf:directory.appendingPathComponent("fixture"))
  try VerifiedAppleMusicLibraries.validate(data,library:spec)
 }
 func stageFixture(_ name:String) throws -> URL {
  let directory=publishRoot.appendingPathComponent(name)
  try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:false)
  try abc.write(to:directory.appendingPathComponent("fixture")); return directory
 }
 let destination=publishRoot.appendingPathComponent("active")
 let first=try stageFixture("first")
 _ = try VerifiedAppleMusicLibraries.publish(output:first,destination:destination,base:publishRoot,validateDirectory:validateFixture)
 try validateFixture(destination)
 let winner=try stageFixture("winner")
 _ = try VerifiedAppleMusicLibraries.publish(output:winner,destination:destination,base:publishRoot,validateDirectory:validateFixture)
 try check(FileManager.default.fileExists(atPath:winner.path),"valid winner replaced")
 try Data("bad".utf8).write(to:destination.appendingPathComponent("fixture"))
 _ = try VerifiedAppleMusicLibraries.publish(output:winner,destination:destination,base:publishRoot,validateDirectory:validateFixture)
 try validateFixture(destination)
 let bad=try stageFixture("bad")
 try Data("bad".utf8).write(to:bad.appendingPathComponent("fixture"))
 try rejects("invalid staged output") {
  _ = try VerifiedAppleMusicLibraries.publish(output:bad,destination:destination,base:publishRoot,validateDirectory:validateFixture)
 }
 try validateFixture(destination)
 try Data("bad".utf8).write(to:destination.appendingPathComponent("fixture"))
 let rollbackStage=try stageFixture("rollback-stage")
 let legacy=try stageFixture("legacy-must-remain")
 try rejects("activation failure") {
  _ = try VerifiedAppleMusicLibraries.publish(output:rollbackStage,destination:destination,base:publishRoot,
   validateDirectory: { directory in
    try validateFixture(directory)
    if directory==rollbackStage { try FileManager.default.removeItem(at:directory) }
   })
 }
 try check(try Data(contentsOf:destination.appendingPathComponent("fixture"))==Data("bad".utf8),"damaged old cache not restored")
 try validateFixture(legacy)
 let lockPath=publishRoot.appendingPathComponent(".vendor-install.lock")
 try FileManager.default.removeItem(at:lockPath)
 let sentinel=root.appendingPathComponent("lock-sentinel")
 try abc.write(to:sentinel)
 try FileManager.default.createSymbolicLink(at:lockPath,withDestinationURL:sentinel)
 let symlinkStage=try stageFixture("symlink-lock-stage")
 try rejects("symlink lock") {
  _ = try VerifiedAppleMusicLibraries.publish(output:symlinkStage,destination:destination,base:publishRoot,validateDirectory:validateFixture)
 }
 try check(try Data(contentsOf:sentinel)==abc,"symlink lock target modified")
 let config=URLSessionConfiguration.ephemeral; config.protocolClasses=[FixtureProtocol.self]
 let successTarget=root.appendingPathComponent("successful-stream")
 let success=VendorAPKDownload(destination:successTarget,configuration:config)
 FixtureProtocol.bodyBytes=Int(VerifiedAppleMusicLibraries.archiveBytes)
 print("CASE successful stream")
 try checkDownloadRoot(root)
 try await success.download()
 print("PASS successful stream")
 let written=try FileManager.default.attributesOfItem(atPath:successTarget.path)[.size] as? NSNumber
 try check(written?.int64Value==VerifiedAppleMusicLibraries.archiveBytes,"stream did not write exact size")
 try FileManager.default.removeItem(at:successTarget)
 FixtureProtocol.bodyBytes=Int(VerifiedAppleMusicLibraries.archiveBytes)+1
 try await downloadRejects(root,"actual byte cap",expected:.byteCap)
 FixtureProtocol.bodyBytes=1_048_576
 let cancelled=VendorAPKDownload(destination:root.appendingPathComponent("cancelled"),configuration:config)
 FixtureProtocol.afterFirstChunk={ cancelled.cancel() }
 print("CASE in-flight cancellation")
 try checkDownloadRoot(root)
 var cancellationFailure:Error?
 do { try await cancelled.download() } catch { cancellationFailure=error }
 guard let cancellationFailure else { throw NSError(domain:"UnexpectedSuccess",code:1) }
 try checkDownloadFailure(cancellationFailure,expected:.cancellation)
 print("PASS in-flight cancellation")
 FixtureProtocol.afterFirstChunk=nil
 let failedDestination=VendorAPKDownload(destination:root,configuration:config)
 print("CASE existing-directory staging rejection")
 let stagingCalls=FixtureProtocol.calls
 var stagingFailure:Error?
 do { try await failedDestination.download() } catch { stagingFailure=error }
 guard let stagingFailure else { throw NSError(domain:"UnexpectedSuccess",code:2) }
 try checkDownloadFailure(stagingFailure,expected:.staging)
 try check(FixtureProtocol.calls==stagingCalls,"staging failure started network")
 print("PASS existing-directory staging rejection")
 let session=URLSession(configuration:config)
 let request=URLRequest(url:VerifiedAppleMusicLibraries.source)
 let task=session.dataTask(with:request) // Never resumed; protocol prevents networking regardless.
 var redirected=true
 success.urlSession(session,task:task,
  willPerformHTTPRedirection:HTTPURLResponse(url:VerifiedAppleMusicLibraries.source,statusCode:302,httpVersion:nil,headerFields:nil)!,
  newRequest:URLRequest(url:URL(string:"https://example.invalid/")!)) { redirected = $0 != nil }
 try check(!redirected,"redirect accepted")
 session.invalidateAndCancel()
 print("PASS vendor acquisition contract: production helper, fake URL responses, no network or target execution")
}
Task {
 do { try await main(); exit(0) }
 catch { print("FAIL vendor acquisition contract: \(error)"); exit(1) }
}
dispatchMain()
'''
out=pathlib.Path(sys.argv[2]);out.write_text(preamble+'\n'+source+'\n'+tests)
print(json.dumps({'source_sha256':hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest(),'fixture_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'coverage':'production downloader via fake URLProtocol; production digest/cache rejection; archive reader double, no full archive integration'},indent=2))
