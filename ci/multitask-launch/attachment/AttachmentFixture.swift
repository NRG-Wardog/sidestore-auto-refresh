import SwiftUI
import UIKit

enum V3AppLanguage { case english, hebrew; var isRightToLeft: Bool { self == .hebrew } }
struct RepresentativeRoot: View {
 let bridge: Bool
 let language: V3AppLanguage
 var body: some View {
  TabView {
   NavigationStack { List { Text("Representative app row"); Text("Second row") }.navigationTitle("Apps") }.tabItem { Label("Apps",systemImage:"square.stack") }
   Text("Settings").tabItem { Label("Settings",systemImage:"gear") }
  }
  .environment(\.locale,Locale(identifier:language.isRightToLeft ? "he" : "en"))
  .environment(\.layoutDirection,language.isRightToLeft ? .rightToLeft : .leftToRight)
  .background { if bridge { V3HostLanguageUIKitBridge(language:language) } }
 }
}
struct FixtureFailure: Error { let reason: String }
@main final class FixtureApp: UIResponder, UIApplicationDelegate {
 var window: UIWindow?
 var records:[[String:Any]]=[]
 var corrected:[[String:Any]]=[]
 var resolver:[[String:Any]]=[]
 var screenshots:[String]=[]
 func application(_ application:UIApplication,didFinishLaunchingWithOptions options:[UIApplication.LaunchOptionsKey:Any]?=nil)->Bool { true }
 func application(_ application:UIApplication, configurationForConnecting session:UISceneSession, options:UIScene.ConnectionOptions)->UISceneConfiguration {
  let config=UISceneConfiguration(name:"AttachmentFixture",sessionRole:session.role);config.delegateClass=FixtureSceneDelegate.self;return config
 }
 func start(window:UIWindow) {
  self.window=window;window.makeKeyAndVisible()
  Task { @MainActor in
   do {
    for bridge in [false,true] { for language in [V3AppLanguage.english,.hebrew] {
     let host=UIHostingController(rootView:RepresentativeRoot(bridge:bridge,language:language))
     window.rootViewController=host;await self.settle(host)
     let label="swiftui-\(bridge ? "bridge" : "no-bridge")-\(language.isRightToLeft ? "he" : "en")"
     self.observeBaseline(host:host,label:label)
     try self.qualify(host:host,label:label)
    }}
    let synthetic=UIViewController();synthetic.view.backgroundColor = .white;window.rootViewController=synthetic
    let first=UIView(frame:synthetic.view.bounds);first.autoresizingMask=[.flexibleWidth,.flexibleHeight]
    let cover=UIView(frame:synthetic.view.bounds);cover.autoresizingMask=[.flexibleWidth,.flexibleHeight];cover.backgroundColor = .blue
    synthetic.view.addSubview(first);synthetic.view.addSubview(cover);await self.settle(synthetic)
    self.observeBaseline(host:synthetic,label:"negative-opaque-sibling")
    try self.qualify(host:synthetic,label:"corrected-opaque-sibling")
    try await self.rootReplacement()
    try self.qualifyWindowResolver()
    self.finish(status:"PASS_ACTUAL_ATTACHMENT_HELPER_NOT_GUEST_LAUNCH",failure:nil)
   } catch { self.finish(status:"FAILED",failure:(error as? FixtureFailure)?.reason ?? "Unexpected fixture error") }
  }
 }
 func finish(status:String,failure:String?) {
  var report:[String:Any]=["schema":"multitask-attachment-qualified-v2","status":status,"physical_guest_execution":false,"records":records,"corrected_records":corrected,"resolver_records":resolver,"screenshots":screenshots,"scene_provider_scope":"projected_inputs_real_uiwindows"]
  if let failure { report["failure"]=failure }
  let output=FileManager.default.urls(for:.documentDirectory,in:.userDomainMask)[0].appendingPathComponent("attachment-result.json")
  do {try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:output)} catch {NSLog("FIXTURE_WRITE_FAILED")}
 }
 @MainActor func require(_ value:Bool,_ reason:String) throws { if !value { throw FixtureFailure(reason:reason) } }
 @MainActor func settle(_ host:UIViewController) async {
  host.view.setNeedsLayout();host.view.layoutIfNeeded()
  for _ in 0..<4 { await Task.yield();try? await Task.sleep(nanoseconds:100_000_000) }
  host.view.layoutIfNeeded()
 }
 @MainActor func marker()->UIButton {
  let marker=UIButton(type:.system);marker.frame=CGRect(x:100,y:180,width:120,height:80);marker.backgroundColor = .red;marker.setTitle("Guest marker",for:.normal);return marker
 }
 @MainActor func hit(_ marker:UIButton)->Bool {
  let point=marker.convert(CGPoint(x:60,y:40),to:window);let hit=window!.hitTest(point,with:nil)
  return hit === marker || hit?.isDescendant(of:marker)==true
 }
 @MainActor func refreshBridges(_ controller:UIViewController)->Int {
  var count=0
  if let bridge=controller as? V3HostLanguageUIKitBridge.Controller {bridge.refreshHostChrome();count+=1}
  for child in controller.children {count+=refreshBridges(child)}
  return count
 }
 @MainActor func observeBaseline(host:UIViewController,label:String) {
  let overlay=BaselineVirtualWindowsHostView();let marker=marker();overlay.addSubview(marker)
  let child=host.view.subviews.first
  // Historical target selection projected only; old init/hitTest are compiled unchanged.
  child?.addSubview(overlay);host.view.layoutIfNeeded()
  let point=marker.convert(CGPoint(x:60,y:40),to:window);let hit=window!.hitTest(point,with:nil)
  func rect(_ r:CGRect)->[Double] {[r.origin.x,r.origin.y,r.size.width,r.size.height].map(Double.init)}
  records.append(["case":label,"host_class":String(describing:type(of:host)),"first_child_class":child.map{String(describing:type(of:$0))} ?? "none","first_child_frame":child.map{rect($0.frame)} ?? [],"first_child_interactive":child?.isUserInteractionEnabled ?? false,"first_child_hidden":child?.isHidden ?? false,"overlay_attached_to_window":overlay.window === window,"overlay_frame":rect(overlay.frame),"marker_hit":self.hit(marker),"hit_class":hit.map{String(describing:type(of:$0))} ?? "none","host_child_count":host.view.subviews.count,"same_parent_as_production":overlay.superview === child])
  if label.hasPrefix("swiftui-bridge-") {capture(name:"baseline-"+label)}
  overlay.removeFromSuperview()
 }
 @MainActor func qualify(host:UIViewController,label:String) throws {
  let overlay=VirtualWindowsHostView();let marker=marker();overlay.addSubview(marker)
  let bar=UINavigationBar(frame:CGRect(x:0,y:0,width:200,height:44));bar.semanticContentAttribute = .forceLeftToRight;overlay.addSubview(bar)
  try require(overlay.attach(toHostView:host.view),"Valid host attachment rejected")
  let firstHit=hit(marker);try require(firstHit,"Corrected marker not hittable")
  try require(overlay.superview === host.view && overlay.window === window,"Corrected host ownership differs")
  try require(overlay.frame==host.view.bounds,"Overlay frame differs from host bounds")
  let childCount=overlay.subviews.count
  try require(overlay.attach(toHostView:host.view),"Repeated attachment rejected")
  let bridgeCount=refreshBridges(host)
  if label.hasPrefix("swiftui-bridge-") {try require(bridgeCount>0,"Actual host bridge controller missing")}
  try require(overlay.subviews.count==childCount && marker.superview === overlay,"Repeated attachment replaced guest")
  try require(host.view.subviews.filter{$0 === overlay}.count==1,"Duplicate overlay attachment")
  try require(hit(marker),"Repeated attachment lost hit testing")
  try require(bar.semanticContentAttribute == .forceLeftToRight,"Guest explicit LTR changed")
  corrected.append(["case":label,"marker_hit":firstHit,"repeated_marker_hit":hit(marker),"direct_host_parent":true,"same_guest_identity":true,"single_attachment":true,"bounds_match":true,"guest_explicit_ltr_preserved":true,"bridge_refresh_count":bridgeCount])
  if label.hasPrefix("swiftui-bridge-") {capture(name:"corrected-"+label)}
  overlay.removeFromSuperview()
 }
 @MainActor func rootReplacement() async throws {
  let first=UIHostingController(rootView:RepresentativeRoot(bridge:true,language:.english));window!.rootViewController=first;await settle(first)
  let overlay=VirtualWindowsHostView();let marker=marker();overlay.addSubview(marker)
  let bar=UINavigationBar(frame:CGRect(x:0,y:0,width:200,height:44));bar.semanticContentAttribute = .forceLeftToRight;overlay.addSubview(bar)
  try require(overlay.attach(toHostView:first.view) && hit(marker),"Initial root replacement attachment failed")
  let opaque=UIView(frame:first.view.bounds);opaque.backgroundColor = .blue;opaque.autoresizingMask=[.flexibleWidth,.flexibleHeight];first.view.addSubview(opaque)
  try require(!hit(marker),"Child replacement negative occlusion control failed")
  try require(overlay.attach(toHostView:first.view) && hit(marker),"Same-controller child replacement not restored")
  corrected.append(["case":"same-controller-child-replacement","marker_hit":true,"same_guest_identity":marker.superview === overlay,"direct_host_parent":overlay.superview === first.view])
  let originalBounds=first.view.bounds;first.view.bounds=CGRect(x:0,y:0,width:originalBounds.width-20,height:originalBounds.height-20)
  try require(overlay.attach(toHostView:first.view) && overlay.frame==first.view.bounds,"Host resize bounds not propagated")
  try require(hit(marker),"Host resize lost hit testing")
  corrected.append(["case":"host-bounds-resize","marker_hit":true,"same_guest_identity":marker.superview === overlay,"bounds_match":true])
  first.view.bounds=originalBounds
  overlay.isHidden=true
  try require(overlay.attach(toHostView:first.view) && overlay.isHidden,"Attachment changed minimized visibility")
  overlay.isHidden=false
  try require(hit(marker) && marker.superview === overlay,"Caller resume lost marker identity")
  corrected.append(["case":"hidden-then-resume","marker_hit":true,"same_guest_identity":true,"hidden_state_preserved_by_helper":true,"caller_unhid_marker_only":true])
  let replacement=UIHostingController(rootView:RepresentativeRoot(bridge:true,language:.hebrew));window!.rootViewController=replacement;await settle(replacement)
  try require(overlay.attach(toHostView:replacement.view),"Replacement root attachment failed")
  let bridgeCount=refreshBridges(replacement);try require(bridgeCount>0,"Replacement bridge controller missing")
  try require(overlay.superview === replacement.view && !first.view.subviews.contains(overlay),"Overlay stayed on replaced root")
  try require(marker.superview === overlay && hit(marker),"Replacement lost same guest")
  try require(bar.semanticContentAttribute == .forceLeftToRight,"Replacement changed guest LTR")
  corrected.append(["case":"root-replacement","scope":"helper_marker_only_not_live_controller_migration","marker_hit":true,"same_guest_identity":true,"old_parent_released":true,"guest_explicit_ltr_preserved":true,"bounds_match":overlay.frame==replacement.view.bounds])
  let detached=UIView(frame:CGRect(x:0,y:0,width:320,height:640))
  try require(!overlay.attach(toHostView:detached),"Detached host was accepted")
  try require(overlay.superview === replacement.view && hit(marker),"Rejected detached host stole guest")
  let zero=UIView(frame:.zero);replacement.view.addSubview(zero)
  try require(!overlay.attach(toHostView:zero),"Empty host was accepted")
  try require(overlay.superview === replacement.view && hit(marker),"Rejected empty host stole guest")
  corrected.append(["case":"invalid-hosts","detached_rejected":true,"empty_rejected":true,"same_guest_identity":true,"original_parent_preserved":true,"marker_hit":true])
  overlay.removeFromSuperview()
 }
 @MainActor func capture(name:String) {
  guard let window else{return}
  window.layoutIfNeeded()
  let image=UIGraphicsImageRenderer(bounds:window.bounds).image {context in window.layer.render(in:context.cgContext)}
  let filename=name+".png"
  if let data=image.pngData() {do {try data.write(to:FileManager.default.urls(for:.documentDirectory,in:.userDomainMask)[0].appendingPathComponent(filename));screenshots.append(filename)} catch {}}
 }
 @MainActor func qualifyWindowResolver() throws {
  guard let scene=window?.windowScene else {throw FixtureFailure(reason:"Fixture scene missing")}
  var created:[UIWindow]=[]
  func hostWindow(_ root:UIViewController,bridge:Bool,attachBridge:Bool=true)->UIWindow {
   let w=UIWindow(windowScene:scene);w.frame=window!.bounds;w.rootViewController=root;w.isHidden=false;created.append(w)
   if bridge {
    let controller=V3HostLanguageUIKitBridge.Controller(language:.english);root.addChild(controller)
    if attachBridge {controller.view.frame=CGRect(x:0,y:0,width:10,height:10);root.view.addSubview(controller.view)}
    controller.didMove(toParent:root)
   }
   root.view.layoutIfNeeded();return w
  }
  defer {for window in created {window.isHidden=true}}
  let main=hostWindow(UIViewController(),bridge:true)
  let other=hostWindow(UIViewController(),bridge:true)
  let guest=hostWindow(AppSceneViewController(),bridge:true)
  let noBridge=hostWindow(UIViewController(),bridge:false)
  let detached=hostWindow(UIViewController(),bridge:true,attachBridge:false)
  let nestedGuest=DecoratedAppSceneViewController();main.rootViewController!.addChild(nestedGuest);main.rootViewController!.view.addSubview(nestedGuest.view);nestedGuest.didMove(toParent:main.rootViewController!)
  func check(_ label:String,_ actual:UIWindow?,_ expected:UIWindow?) throws {
   try require(actual === expected,"Host resolver failed: \(label)")
   resolver.append(["case":label,"selected_expected":true,"expected":expected == nil ? "none" : "main"])
  }
  try check("typed-host-marker",LCGuestHostWindowSelection.select(from:[noBridge,main]),main)
  try check("native-guest-excluded",LCGuestHostWindowSelection.select(from:[guest]),nil)
  try check("host-with-virtual-guest",LCGuestHostWindowSelection.select(from:[guest,main]),main)
  try check("ambiguous-hosts",LCGuestHostWindowSelection.select(from:[main,other]),nil)
  try check("detached-marker",LCGuestHostWindowSelection.select(from:[detached]),nil)
  main.isHidden=true;try check("hidden-host",LCGuestHostWindowSelection.select(from:[main]),nil);main.isHidden=false
  main.windowLevel = .alert;try check("non-normal-window",LCGuestHostWindowSelection.select(from:[main]),nil);main.windowLevel = .normal
  let mainScene=FixtureWindowScene("main",.foregroundActive,[main]);let otherScene=FixtureWindowScene("other",.foregroundActive,[other]);let guestScene=FixtureWindowScene("guest",.foregroundActive,[guest])
  try check("registered-main-session",projectedProductionHostWindow(inputScenes:[otherScene,guestScene,mainScene],registeredSession:FixtureSession(persistentIdentifier:"main")),main)
  try check("missing-registration-marker-fallback",projectedProductionHostWindow(inputScenes:[guestScene,mainScene],registeredSession:nil),main)
  try check("stale-registration-no-fallback",projectedProductionHostWindow(inputScenes:[mainScene],registeredSession:FixtureSession(persistentIdentifier:"stale")),nil)
  try check("background-main-rejected",projectedProductionHostWindow(inputScenes:[FixtureWindowScene("main",.background,[main])],registeredSession:FixtureSession(persistentIdentifier:"main")),nil)
  try check("foreground-inactive-main",projectedProductionHostWindow(inputScenes:[FixtureWindowScene("main",.foregroundInactive,[main])],registeredSession:FixtureSession(persistentIdentifier:"main")),main)
  try check("ambiguous-fallback-rejected",projectedProductionHostWindow(inputScenes:[mainScene,otherScene],registeredSession:nil),nil)
  try check("missing-window-rejected",projectedProductionHostWindow(inputScenes:[FixtureWindowScene("main",.foregroundActive,[])],registeredSession:FixtureSession(persistentIdentifier:"main")),nil)
 }
}
final class FixtureSceneDelegate: UIResponder, UIWindowSceneDelegate {
 var window:UIWindow?
 func scene(_ scene:UIScene,willConnectTo session:UISceneSession,options:UIScene.ConnectionOptions) {
  guard let scene=scene as? UIWindowScene else{return};let window=UIWindow(windowScene:scene);self.window=window
  (UIApplication.shared.delegate as! FixtureApp).start(window:window)
 }
}
