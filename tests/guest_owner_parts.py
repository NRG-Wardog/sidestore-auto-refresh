"""Extract current pinned guest implementations for retained runtime harnesses."""
from types import SimpleNamespace
from owner_source import owner_source

PATHS = (
    'MultitaskSupport/AppSceneViewController.m', 'MultitaskSupport/AppSceneViewController.h',
    'MultitaskSupport/MultitaskAppWindow.swift', 'MultitaskSupport/MultitaskDockView.swift',
    'SideStoreSupport/SideStoreHooks.m', 'LiveContainerSwiftUI/Models/LCAppModel.swift',
    'LiveContainerSwiftUI/Views/LCTabView.swift', 'LiveContainer/LCBootstrap.m',
    'LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift',
    'MultitaskSupport/DecoratedAppSceneViewController.m')


def block(source, anchor):
    if source.count(anchor) != 1: raise ValueError('ambiguous owner anchor: '+anchor)
    start = source.index(anchor); opening = source.index('{', start); depth = 1
    for end in range(opening+1, len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if depth == 0: return source[start:end+1]
    raise ValueError('unterminated owner block')


def implementations():
    texts = {p: owner_source(p) for p in PATHS}
    scene, bootstrap = texts[PATHS[0]], texts['LiveContainer/LCBootstrap.m']
    window = texts['MultitaskSupport/MultitaskAppWindow.swift']
    registry_start = window.index('    // LC_GUEST_RETURN_V2:')
    registry_end_anchor = block(window, '@objc class func openExistingAppWindow(')
    registry_end = window.index(registry_end_anchor)+len(registry_end_anchor)
    def control(text, name):
        start = text.index('static double LCReturnAxisCenter(')
        end = text.index('@end', text.index('@implementation '+name))+len('@end')
        return text[start:end]
    direct_start = bootstrap.index('// LC_DIRECT_RETURN_V1:')
    direct_end = bootstrap.index('@end', bootstrap.index('@implementation LCDirectReturnPresenter'))+len('@end')
    return SimpleNamespace(
        PATHS=PATHS, sources=texts,
        GEOMETRY=block(scene, 'static double LCReturnAxisCenter(')+'\n'+block(scene, 'static int LCReturnShouldHide('),
        CONTROL=control(scene, 'LCReturnControl'), DIRECT_CONTROL=control(bootstrap, 'LCDirectReturnControl'),
        DIRECT_RUNTIME=bootstrap[direct_start:direct_end],
        METHODS=block(scene, '- (void)viewDidLayoutSubviews {')+'\n'+block(scene, '- (void)lcReturnToHost'),
        CLEANUP=block(scene, '- (void)appTerminationCleanUp'),
        DOCK_RESUME=block(texts['MultitaskSupport/MultitaskDockView.swift'], 'func bringMultitaskViewToFront(uuid:'),
        WINDOW_MANAGER=window[registry_start:registry_end],
        SETTINGS_PROPERTIES='\n'.join(line for line in texts['LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift'].splitlines() if '@AppStorage' in line))


def verify_settings_controls(settings):
    def require(ok, reason):
        if not ok: raise ValueError(reason)
    section = block(settings, 'Section {\n                    Toggle("Show Return Button"')
    for binding in [
        'Toggle("Show Return Button", isOn: Binding(get: { !hideReturnControl }, set: { hideReturnControl = !$0 }))',
        'Toggle("Start Collapsed", isOn: $returnStartsCollapsed)',
        'Toggle("Use Custom Colors", isOn: $returnCustomColors)',
        'if returnCustomColors {',
        'ColorPicker("Icon Color", selection: returnColorBinding($returnTintRGB), supportsOpacity: false)',
        'ColorPicker("Button Background", selection: returnColorBinding($returnBackgroundRGB), supportsOpacity: false)',
        '.disabled(hideReturnControl)',
    ]:
        require(binding in section, 'missing or miswired guest setting: '+binding)
    helper = block(settings, 'private func returnColorBinding(')
    for token in ['let value = rgb.wrappedValue',
                  'Double((value >> 16) & 0xFF) / 255', 'Double((value >> 8) & 0xFF) / 255',
                  'Double(value & 0xFF) / 255, opacity: 1',
                  'guard UIColor(color).getRed(&red, green: &green, blue: &blue, alpha: &alpha) else { return }',
                  'Int((min(1, max(0, value)) * 255).rounded())',
                  'rgb.wrappedValue = (channel(red) << 16) | (channel(green) << 8) | channel(blue)']:
        require(token in helper, 'guest color conversion changed: '+token)


def verify_resume_ownership(model, tab):
    resume = 'if await bringExistingMultitaskWindowIfNeeded(dataUUID: currentDataFolder, urlScheme: urlStr) {\n                return\n            }'
    guard = '''if await MainActor.run(body: { MultitaskManager.isUsing(container: currentDataFolder) }) {
                throw "lc.container.inUse".loc + "\\nA retained guest still owns this container. Close its existing window before relaunching."
            }'''
    if resume not in model or guard not in model:
        raise ValueError('failed resume must reject a still-owned container')
    between = model[model.index(resume)+len(resume):model.index(guard)]
    if 'LC_RETURN_CONTAINER_GUARD' not in between or len(between) > 200:
        raise ValueError('container ownership check must immediately follow failed resume')
    capture = 'MultitaskWindowManager.mainSceneSession = sceneDelegate.window?.windowScene?.session'
    clear = '''if #available(iOS 16.1, *), MultitaskWindowManager.mainSceneSession?.persistentIdentifier == scene1.session.persistentIdentifier {
                        MultitaskWindowManager.mainSceneSession = nil
                    }'''
    if tab.count(capture) != 1 or tab.count(clear) != 1:
        raise ValueError('capture actual main session and clear only its disconnect')
