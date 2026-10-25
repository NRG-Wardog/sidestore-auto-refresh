import UIKit

// Standalone synthetic test host. No LiveContainer backend or guest is launched.
@main
final class FixtureAppDelegate: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication, configurationForConnecting session: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let configuration = UISceneConfiguration(name: "P2AlertScene", sessionRole: session.role)
        configuration.delegateClass = FixtureSceneDelegate.self
        return configuration
    }
}

final class FixtureSceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options: UIScene.ConnectionOptions) {
        guard let scene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: scene)
        window.rootViewController = UIViewController()
        window.rootViewController?.view.backgroundColor = .systemBackground
        self.window = window
        window.makeKeyAndVisible()
    }
}
