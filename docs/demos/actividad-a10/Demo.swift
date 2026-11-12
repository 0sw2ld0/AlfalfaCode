import AlfalfaKit
import AppKit
import SwiftUI

/// Sólo usa un hogar de ensayo y captura el contenido de su propia ventana.
@main
struct ActivityDemo {
    @MainActor
    static func main() throws {
        guard let path = ProcessInfo.processInfo.environment["ALFALFA_A10_DEMO_DIR"],
              !path.isEmpty, path.hasPrefix("/") else { throw CocoaError(.fileNoSuchFile) }
        let directory = URL(fileURLWithPath: path)
        let home = directory.appendingPathComponent("alfalfa")
        guard let store = BrowserActivityStore(home: home, sessionID: "ensayo-a10") else {
            throw CocoaError(.fileReadCorruptFile)
        }
        let history = try store.load()
        guard history.eventos.count == 3, history.eventos.filter(\.hasCapture).count == 1 else {
            throw CocoaError(.fileReadCorruptFile)
        }
        let without = BrowserActivityReport.render(history, store: store, includeCaptures: false)
        let with = BrowserActivityReport.render(history, store: store, includeCaptures: true)
        guard !String(decoding: without, as: UTF8.self).contains("data:image/png;base64,"),
              String(decoding: with, as: UTF8.self).contains("data:image/png;base64,") else {
            throw CocoaError(.fileWriteUnknown)
        }
        try without.write(to: directory.appendingPathComponent("informe-sin-capturas.html"), options: .atomic)
        try with.write(to: directory.appendingPathComponent("informe-con-captura.html"), options: .atomic)

        let application = NSApplication.shared
        application.setActivationPolicy(.regular)
        let view = NSHostingView(rootView: BrowserActivityView(store: store))
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 740, height: 620),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Demo A10 · Qué hizo el agente"
        window.contentView = view
        window.center()
        window.makeKeyAndOrderFront(nil)
        application.activate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
            defer { application.terminate(nil) }
            window.displayIfNeeded()
            view.layoutSubtreeIfNeeded()
            guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            guard let png = bitmap.representation(using: .png, properties: [:]) else { return }
            try? png.write(to: directory.appendingPathComponent("panel.png"), options: .atomic)
        }
        application.run()
    }
}
