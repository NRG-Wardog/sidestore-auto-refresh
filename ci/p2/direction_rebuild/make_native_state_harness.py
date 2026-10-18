"""Generate a Swift harness from actual candidate state/load/write declarations.
Run generated file with: swiftc -parse-as-library -swift-version 5 state.swift -o state && ./state
No UIKit, guest code, authentication, or network. Service replies are held explicitly.
"""
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[1]
s=(Path(sys.argv[2]) if len(sys.argv)>2 else root/'LiveContainerSwiftUI/Views/V3UnifiedShell.swift').read_text()
def block(start):
 a=s.index(start); b=s.index('{',a); depth=1; p=b+1
 while depth:
  if s[p]=='{':depth+=1
  elif s[p]=='}':depth-=1
  p+=1
 return s[a:p]
generation=block('struct V3SettingsWriteGeneration {')
model=block('@MainActor final class V3BoolSettingState: ObservableObject {')
row=block('struct V3BoolSettingRow: View {')
methods=row[row.index('    private func save('):row.rfind('}')]
preamble=r'''
import Foundation
protocol ObservableObject: AnyObject {}
@propertyWrapper final class Published<Value> {
 var wrappedValue: Value
 init(wrappedValue: Value) { self.wrappedValue = wrappedValue }
}
enum TestError: Error { case expected }
@MainActor final class V3SideStoreStatusStore {
 var settings: [String: Bool] = [:]
 var errors = 0; var reloads = 0; var begins = 0; var finishes = 0
 var notice = ""
 func present(_ error: Error) { errors += 1 }
 func reload() { reloads += 1 }
 func beginDirectMutation() -> Int { begins += 1; return begins }
 func finishDirectMutation(ticket: Int, requestReload: Bool) { finishes += 1 }
}
@MainActor final class V3ServiceBridge {
 static let shared = V3ServiceBridge()
 struct Call { let operation: String; let payload: [String: Any]; let continuation: CheckedContinuation<[String:Any], Error> }
 var calls: [Int: Call] = [:]; var next = 0
 func request(operation: String, payload: [String:Any] = [:]) async throws -> [String: Any] {
  let id = next; next += 1
  return try await withCheckedThrowingContinuation { continuation in calls[id] = Call(operation: operation, payload: payload, continuation: continuation) }
 }
 func acknowledgeDirectRecoveryAfterSuccess(_ reply: [String:Any], operation: String) async -> Bool { true }
 func success(_ id: Int, _ reply: [String:Any]) { calls.removeValue(forKey:id)!.continuation.resume(returning:reply) }
 func failure(_ id: Int) { calls.removeValue(forKey:id)!.continuation.resume(throwing:TestError.expected) }
}
'''
driver='\n@MainActor struct V3BoolRowDriver {\n let state: V3BoolSettingState\n let status: V3SideStoreStatusStore\n let key: String\n func change(_ value: Bool) { state.value = value; save(value) }\n'+methods+'}\n'
tests=r'''
@main struct Harness {
 @MainActor static func waitFor(_ condition: () -> Bool) async {
  for _ in 0..<10000 { if condition() { return }; await Task.yield() }
  fatalError("Condition did not settle")
 }
 @MainActor static func main() async {
  let bridge = V3ServiceBridge.shared
  let key = "isBetaUpdatesEnabled"
  func reply(_ value: Bool) -> [String:Any] { ["bools":[key:value]] }
  // A direction-only row replacement shares owner; pending load reaches new row.
  let s=V3BoolSettingState(); let status=V3SideStoreStatusStore()
  let load=Task { await s.load(key:key,status:status) }
  await waitFor { bridge.calls[0] != nil }
  let replacement=V3BoolRowDriver(state:s,status:status,key:key)
  bridge.success(0,reply(true)); await load.value
  precondition(replacement.state.value && replacement.state.loaded && !s.loadingRequest)
  // Cancellation applies neither success nor error. Reappearance supersedes an
  // unwinding old load, whose defer must not release the new load's ownership.
  let old=Task { await s.load(key:key,status:status) }
  await waitFor { bridge.calls[1] != nil }; old.cancel()
  let newer=Task { await s.load(key:key,status:status) }
  await waitFor { bridge.calls[2] != nil }
  bridge.success(1,reply(false)); await old.value
  precondition(s.value && s.loadingRequest)
  bridge.success(2,reply(false)); await newer.value
  precondition(!s.value && !s.loadingRequest)
  let canceled=Task { await s.load(key:key,status:status) }
  await waitFor { bridge.calls[3] != nil }; canceled.cancel()
  bridge.failure(3); await canceled.value; precondition(status.errors == 0)
  // Even without cancellation, later load owns the result and error handling.
  let stale=Task { await s.load(key:key,status:status) }
  await waitFor { bridge.calls[4] != nil }
  let latest=Task { await s.load(key:key,status:status) }
  await waitFor { bridge.calls[5] != nil }
  bridge.success(5,reply(true)); await latest.value
  bridge.failure(4); await stale.value
  precondition(s.value && status.errors == 0 && !s.loadingRequest)
  // Read begun before a write cannot overwrite its optimistic state.
  let beforeWrite=Task { await s.load(key:key,status:status) }
  await waitFor { bridge.calls[6] != nil }
  replacement.change(false); await waitFor { bridge.calls[7] != nil }
  bridge.success(6,reply(true)); await beforeWrite.value; precondition(!s.value)
  bridge.success(7,[:]); await waitFor { status.finishes == 1 }
  precondition(s.confirmedValue == false && !s.writeGenerations.hasPendingWrites(for:key))
  // Two row instances across a direction change still share generation tracking.
  replacement.change(true); await waitFor { bridge.calls[8] != nil }
  let another=V3BoolRowDriver(state:s,status:status,key:key)
  another.change(false); await waitFor { bridge.calls[9] != nil }
  bridge.success(9,[:]); await waitFor { status.finishes == 2 }
  bridge.success(8,[:]); await waitFor { bridge.calls[10] != nil }
  precondition(bridge.calls[10]!.operation == "settingsGet")
  let reloadsBefore = status.reloads
  bridge.success(10,reply(false)); await waitFor { status.reloads > reloadsBefore }
  precondition(!another.state.value && status.begins == 3 && status.finishes == 3)
  // A separate preference owner cannot share pending generations or values.
  let independent=V3BoolSettingState()
  precondition(independent.writeGenerations.current(for:key) == 0 && !independent.loaded)
  precondition(bridge.calls.isEmpty)
  print("PASS: retained load/write ownership, cancellation, supersession, stale reads, out-of-order writes, independent rows")
 }
}
'''
Path(sys.argv[1]).write_text(preamble+generation+'\n'+model+driver+tests)
