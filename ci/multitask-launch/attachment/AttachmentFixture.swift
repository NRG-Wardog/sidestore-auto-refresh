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
@main final class FixtureApp: UIResponder, UIApplicationDelegate {
 var window: UIWindow?
 var records:[[String:Any]]=[]
 func application(_ application:UIApplication,didFinishLaunchingWithOptions options:[UIApplication.LaunchOptionsKey:Any]?=nil)->Bool {
  return true
 }
 func application(_ application:UIApplication, configurationForConnecting session:UISceneSession, options:UIScene.ConnectionOptions)->UISceneConfiguration {
  let config=UISceneConfiguration(name:"AttachmentFixture",sessionRole:session.role);config.delegateClass=FixtureSceneDelegate.self;return config
 }
 func start(window:UIWindow) {
  self.window=window;window.makeKeyAndVisible()
  Task { @MainActor in
   for bridge in [false,true] { for language in [V3AppLanguage.english,.hebrew] {
    let host=UIHostingController(rootView:RepresentativeRoot(bridge:bridge,language:language))
    self.window!.rootViewController=host
    await self.settle(host)
    self.observe(host:host,label:"swiftui-\(bridge ? "bridge" : "no-bridge")-\(language.isRightToLeft ? "he" : "en")")
   }}
   let synthetic=UIViewController();synthetic.view.backgroundColor = .white
   self.window!.rootViewController=synthetic
   let first=UIView(frame:synthetic.view.bounds);first.autoresizingMask=[.flexibleWidth,.flexibleHeight]
   let cover=UIView(frame:synthetic.view.bounds);cover.autoresizingMask=[.flexibleWidth,.flexibleHeight];cover.backgroundColor = .blue
   synthetic.view.addSubview(first);synthetic.view.addSubview(cover)
   await self.settle(synthetic)
   self.observe(host:synthetic,label:"negative-opaque-sibling")
   let report:[String:Any]=["schema":"multitask-attachment-baseline-v1","status":"OBSERVED_NOT_PRODUCT_QUALIFICATION","physical_guest_execution":false,"records":self.records]
   let output=FileManager.default.urls(for:.documentDirectory,in:.userDomainMask)[0].appendingPathComponent("attachment-result.json")
   do {try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:output)} catch {NSLog("FIXTURE_WRITE_FAILED")}
  }
 }
 @MainActor func settle(_ host:UIViewController) async {
  host.view.setNeedsLayout();host.view.layoutIfNeeded()
  for _ in 0..<4 { await Task.yield();try? await Task.sleep(nanoseconds:100_000_000) }
  host.view.layoutIfNeeded()
 }
 @MainActor func observe(host:UIViewController,label:String) {
  let overlay=VirtualWindowsHostView()
  let marker=UIButton(type:.system);marker.frame=CGRect(x:100,y:180,width:120,height:80);marker.backgroundColor = .red;marker.setTitle("Guest marker",for:.normal)
  overlay.addSubview(marker)
  let child=host.view.subviews.first
  // Exact production target selection, variable names projected only.
  child?.addSubview(overlay)
  host.view.layoutIfNeeded()
  let point=marker.convert(CGPoint(x:60,y:40),to:window)
  let hit=window!.hitTest(point,with:nil)
  let visibleHit=hit === marker || hit?.isDescendant(of:marker)==true
  func rect(_ r:CGRect)->[Double] {[r.origin.x,r.origin.y,r.size.width,r.size.height].map(Double.init)}
  records.append(["case":label,"host_class":String(describing:type(of:host)),"first_child_class":child.map{String(describing:type(of:$0))} ?? "none","first_child_frame":child.map{rect($0.frame)} ?? [],"first_child_interactive":child?.isUserInteractionEnabled ?? false,"first_child_hidden":child?.isHidden ?? false,"overlay_attached_to_window":overlay.window === window,"overlay_frame":rect(overlay.frame),"marker_hit":visibleHit,"hit_class":hit.map{String(describing:type(of:$0))} ?? "none","host_child_count":host.view.subviews.count,"same_parent_as_production":overlay.superview === child])
  overlay.removeFromSuperview()
 }
}

final class FixtureSceneDelegate: UIResponder, UIWindowSceneDelegate {
 var window:UIWindow?
 func scene(_ scene:UIScene,willConnectTo session:UISceneSession,options:UIScene.ConnectionOptions) {
  guard let scene=scene as? UIWindowScene else{return};let window=UIWindow(windowScene:scene);self.window=window
  (UIApplication.shared.delegate as! FixtureApp).start(window:window)
 }
}
