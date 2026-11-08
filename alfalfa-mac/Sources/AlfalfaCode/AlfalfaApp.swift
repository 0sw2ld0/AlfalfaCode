import AppKit
import SwiftUI

final class AppDelegate: NSObject {
    var model: AppModel?

    /// Carga el icono empaquetado y conserva el dibujo como respaldo.
    @MainActor private static func icon() -> NSImage? {
        if let url = Bundle.module.url(forResource: "AppIcon", withExtension: "icns"),
           let image = NSImage(contentsOf: url) {
            return image
        }
        if let url = Bundle.module.url(forResource: "AppIcon", withExtension: "png"),
           let image = NSImage(contentsOf: url) {
            return image
        }
        return fallbackIcon()
    }

    @MainActor private static func fallbackIcon() -> NSImage? {
        let view = ZStack {
            RoundedRectangle(cornerRadius: 110).fill(Theme.sidebar)
            LeafShape().stroke(Theme.alfalfa, style: StrokeStyle(lineWidth: 34, lineCap: .round, lineJoin: .round))
                .frame(width: 560, height: 560)
        }
        .frame(width: 1024, height: 1024)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 1
        return renderer.nsImage
    }
}

// MARK: - NSApplicationDelegate

extension AppDelegate: NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        NSApp.applicationIconImage = Self.icon()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    @MainActor func applicationWillTerminate(_ notification: Notification) {
        model?.shutdown()
    }
}

@main
struct AlfalfaApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()

    init() { Self.carryOverPreferences() }

    /// La app se llamaba AlfalfaMac y sus preferencias (carpetas recientes, paneles
    /// plegados) viven en ese dominio: se copian una vez al de AlfalfaCode.
    static func carryOverPreferences(defaults: UserDefaults = .standard, old: String = "AlfalfaMac") {
        let marker = "preferencias.de.AlfalfaMac.copiadas"
        guard !defaults.bool(forKey: marker) else { return }
        if let previous = defaults.persistentDomain(forName: old) {
            for (key, value) in previous where defaults.object(forKey: key) == nil {
                defaults.set(value, forKey: key)
            }
        }
        defaults.set(true, forKey: marker)
    }

    var body: some Scene {
        WindowGroup("AlfalfaCode") {
            RootView()
                .environmentObject(model)
                .frame(minWidth: 1100, minHeight: 640)
                .preferredColorScheme(.dark)
                .task {
                    delegate.model = model
                    await model.reload()
                }
        }
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Buscar actualización…") { model.checkForUpdate() }
            }
        }
        .defaultSize(width: 1280, height: 800)

        // AlfalfaCode ▸ Ajustes… (⌘,): el sistema pone el título en español gracias al Info.plist.
        Settings {
            SettingsView().environmentObject(model)
        }
    }
}
